"""Comedy edit plan and the FFmpeg graph that renders it.

At every punchline the clip gets, in this order:
  whoosh (just before) -> punch-in zoom with a short camera shake -> white
  flash -> sound effect -> meme text (drawn by ``captions`` as ASS)
and the zoom eases back out about a second later, the way meme edits do.

On the ``stream`` layout (facecam on top, game/screen below, the Indonesian
stream-clip look) the punch-in is a face takeover instead: for a moment the
facecam fills the whole frame, so the reaction is the punchline.

One ffmpeg pass does the whole clip: cut, 9:16 reframe, zoom/shake, flash,
captions, then the audio mix (voice + effects + ducked music). Everything is
plain FFmpeg filters, so it runs on any build with libass.
"""
from __future__ import annotations

import functools
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import List

OUT_W, OUT_H, FPS = 1080, 1920, 30
ZOOM = 1.32          # punch-in amount
RISE_S, HOLD_S, FALL_S = 0.10, 1.0, 0.35
SHAKE_S, SHAKE_PX = 0.35, 18
FLASH_S = 0.08
SFX_ROTATION = ("boom", "boing", "ding", "pop")
MUSIC_DB = -20.0     # music under speech, before ducking
STREAM_TOP_H = 760   # facecam band height on the stream layout (captions sit on its seam)
LOUDNESS_LUFS = -14  # what TikTok/Reels/Shorts normalise to anyway
JUDUL_Y = 520        # "judul" layout: the video's top edge under the title
JUDUL_MAX_H = 1000
DROP_MUFFLE_S, DROP_GAP_S = 2.5, 0.35

# Watermark corners: (x, y) of the anchor point and the overlay expression for a logo.
WATERMARK_POS = {
    "kiri-atas": ((40, 70), "40:70"),
    "kanan-atas": ((OUT_W - 40, 70), "W-w-40:70"),
    "kanan-tengah": ((OUT_W - 40, OUT_H // 2 - 220), "W-w-40:H/2-h-200"),
    "kiri-bawah": ((40, 1540), "40:1540-h"),
    "kanan-bawah": ((OUT_W - 40, 1540), "W-w-40:1540-h"),
}
LOGO_W = 200


@dataclass
class EditPlan:
    duration: float
    punchlines: List[float]                      # clip seconds
    sfx: List[tuple] = field(default_factory=list)  # (time, name)
    memes: List[tuple] = field(default_factory=list)  # (time, text)
    zoom: bool = True
    shake: bool = True
    flash: bool = True
    takeover: bool = False   # stream layout: facecam fills the frame at the punchline
    shouts: List[tuple] = field(default_factory=list)  # (start, end) clip seconds: shake
    party: bool = False      # disco hue strobe + negative flash after each punchline
    drop: bool = False       # muffle, then a beat of silence, before the first punchline


SHOUT_ZOOM = 1.10    # a shake needs room to move in
SHOUT_PX = 22
SHOUT_MAX_S = 1.5
PARTY_S = 1.6


def clip_shouts(shouts, clip_start, clip_end, punches=()):
    """Shout spans inside the clip, in clip time, away from punch-ins (those shake already)."""
    out = []
    for s, e in shouts or []:
        s, e = max(s, clip_start) - clip_start, min(e, clip_end) - clip_start
        e = min(e, s + SHOUT_MAX_S)
        if e - s < 0.2 or any(p - 0.3 <= s <= p + RISE_S + HOLD_S for p in punches):
            continue
        out.append((round(s, 2), round(e, 2)))
    return out[:12]


def plan(moment, clip_start, clip_end, effects=True, meme=True, layout="blur",
         shouts=(), party=False, drop=False):
    """Turn a moment's absolute punchline times into a clip-time edit plan.

    ``shouts``: absolute (start, end) spans to shake. ``party`` and ``drop``
    are the "Party" and "Meme cerita" looks (see ``video_fx_filters`` and
    ``build_command``). A long clip gets one punchline every ~15 s at most.
    """
    duration = clip_end - clip_start
    punches = []
    for p in sorted(moment.get("punchlines") or []):
        t = float(p) - clip_start
        # Effects closer than 2 s to each other read as noise; keep the first.
        if 0.3 <= t <= duration - 0.5 and (not punches or t - punches[-1] >= 2.0):
            punches.append(round(t, 2))
    punches = punches[:max(4, int(duration // 15))]
    if not effects:
        return EditPlan(duration, [], zoom=False, shake=False, flash=False)
    shake_spans = clip_shouts(shouts, clip_start, clip_end, punches)
    sfx = []
    for i, t in enumerate(punches):
        sfx.append((max(0.0, t - 0.4), "whoosh"))
        sfx.append((t, SFX_ROTATION[i % len(SFX_ROTATION)]))
    memes = []
    if meme and moment.get("meme_text") and punches:
        memes = [(punches[0] + 0.15, moment["meme_text"])]
    drop = drop and bool(punches) and punches[0] >= 2.0
    if layout == "stream":
        return EditPlan(duration, punches, sfx, memes, zoom=False, shake=False,
                        flash=False, takeover=True, shouts=shake_spans, party=party, drop=drop)
    return EditPlan(duration, punches, sfx, memes, shouts=shake_spans, party=party, drop=drop)


def takeover_enable(plan_: EditPlan):
    """``enable=`` expression: the stretches where the facecam fills the frame."""
    if not (plan_.takeover and plan_.punchlines):
        return None
    hold = RISE_S + HOLD_S + 0.4
    return "+".join(f"between(t,{p:.3f},{p + hold:.3f})" for p in plan_.punchlines)


def _envelope(p):
    """0..1 zoom envelope around punchline ``p`` as an ffmpeg expression of T."""
    a, b, c = p + RISE_S, p + RISE_S + HOLD_S, p + RISE_S + HOLD_S + FALL_S
    return (f"if(between(T,{p:.3f},{a:.3f}),(T-{p:.3f})/{RISE_S},"
            f"if(between(T,{a:.3f},{b:.3f}),1,"
            f"if(between(T,{b:.3f},{c:.3f}),1-(T-{b:.3f})/{FALL_S},0)))")


def zoom_filter(plan_: EditPlan):
    """``zoompan`` doing punch-ins (+ shake) at every punchline and a shake
    on every shout, or None when there is neither."""
    punches = plan_.punchlines if plan_.zoom else []
    if not (punches or plan_.shouts):
        return None
    T = f"(on/{FPS})"
    parts = []
    if punches:
        env = "+".join(_envelope(p) for p in punches).replace("T", T)
        parts.append(f"{ZOOM - 1:.3f}*min(1,{env})")
    if plan_.shouts:
        inside = "+".join(f"between({T},{a:.3f},{b:.3f})" for a, b in plan_.shouts)
        parts.append(f"{SHOUT_ZOOM - 1:.3f}*min(1,{inside})")
    z = f"1+max({','.join(parts)})" if len(parts) == 2 else f"1+{parts[0]}"
    shakes = []
    if plan_.shake:
        shakes += [f"if(between({T},{p:.3f},{p + SHAKE_S:.3f}),{SHAKE_PX}*sin({T}*95),0)"
                   for p in punches]
    shakes += [f"if(between({T},{a:.3f},{b:.3f}),{SHOUT_PX}*sin({T}*83),0)"
               for a, b in plan_.shouts]
    shake = "+".join(shakes) or "0"
    x = f"max(0,min(iw-iw/zoom,iw/2-iw/zoom/2+({shake})))"
    y = f"max(0,min(ih-ih/zoom,ih/2-ih/zoom/2+({shake.replace('sin', 'cos')})))"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={OUT_W}x{OUT_H}:fps={FPS}"


def party_filters(plan_: EditPlan):
    """Disco lights after each punchline: hue jumps every 0.1 s, one negative frame."""
    if not (plan_.party and plan_.punchlines):
        return []
    on = "+".join(f"between(t,{p:.3f},{p + PARTY_S:.3f})" for p in plan_.punchlines)
    neg = "+".join(f"between(t,{p + 0.5:.3f},{p + 0.57:.3f})" for p in plan_.punchlines)
    return [f"hue=H='2*PI*0.27*floor(t*10)':s=1.7:enable='{on}'",
            f"negate=enable='{neg}'"]


def flash_filters(plan_: EditPlan):
    if not plan_.flash:
        return []
    return [f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.55:t=fill:"
            f"enable='between(t,{p:.3f},{p + FLASH_S:.3f})'" for p in plan_.punchlines]


def parse_box(text, src_w, src_h):
    """``"x,y,w,h"`` in pixels, or as fractions of the frame (all <= 1)."""
    vals = [float(v) for v in str(text).split(",")]
    if len(vals) != 4:
        raise ValueError("facecam harus 4 angka: x,y,w,h")
    if all(v <= 1.0 for v in vals):
        vals = [vals[0] * src_w, vals[1] * src_h, vals[2] * src_w, vals[3] * src_h]
    x, y, w, h = (int(round(v)) for v in vals)
    x, y = max(0, min(x, src_w - 2)), max(0, min(y, src_h - 2))
    w, h = max(2, min(w, src_w - x)), max(2, min(h, src_h - y))
    return x, y, w - w % 2, h - h % 2


def _cover(label_in, w, h, label_out, crop=None):
    pre = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
    return (f"{label_in}{pre}scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1{label_out}")


def reframe_filters(src_w, src_h, mode="blur", facecam=None, takeover=None):
    """Filters from the source frame to a 1080x1920 frame, as graph text.

    ``blur``: the whole frame centred over a blurred, zoomed copy of itself
    (podcasts, streams, two-shots: nobody gets cropped out of the joke).
    ``crop``: centre crop to fill (single speaker, face roughly centred).
    ``stream``: ``facecam`` (x, y, w, h) on top, the rest of the frame below.
    ``takeover``: enable expression for the stretches where the facecam fills
    the whole frame (stream only).
    A source that is already vertical is only scaled.
    """
    if mode == "judul":
        # Meme-story look: white page, title on top (drawn by the captions), video below it.
        return (f"color=white:s={OUT_W}x{OUT_H}:r={FPS}[page];"
                f"[0:v]scale={OUT_W}:{JUDUL_MAX_H}:force_original_aspect_ratio=decrease,"
                f"setsar=1[fg];[page][fg]overlay=(W-w)/2:{JUDUL_Y}:shortest=1,fps={FPS}[base]")
    if mode == "stream" and facecam:
        top_h = STREAM_TOP_H
        graph = (f"[0:v]split={3 if takeover else 2}[camsrc][scrsrc]{'[fullsrc]' if takeover else ''};"
                 + _cover("[camsrc]", OUT_W, top_h, "[cam]", facecam) + ";"
                 + _cover("[scrsrc]", OUT_W, OUT_H - top_h, "[scr]") + ";"
                 f"[cam][scr]vstack=inputs=2,fps={FPS}")
        if takeover:
            graph += ("[stacked];" + _cover("[fullsrc]", OUT_W, OUT_H, "[full]", facecam) + ";"
                      f"[stacked][full]overlay=0:0:enable='{takeover}'")
        return graph + "[base]"
    if src_h > src_w * 1.4 or mode == "crop":
        return _cover("[0:v]", OUT_W, OUT_H, f",fps={FPS}[base]")
    return (f"[0:v]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={OUT_W // 4}:{OUT_H // 4}:force_original_aspect_ratio=increase,"
            f"crop={OUT_W // 4}:{OUT_H // 4},boxblur=10:2,scale={OUT_W}:{OUT_H},eq=brightness=-0.08[bg];"
            f"[fgsrc]scale={OUT_W}:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS}[base]")


# Hardware H.264 encoders worth trying per platform, with settings that keep
# a 1080x1920 meme edit clean (~8 Mb/s). libx264 is the fallback everywhere.
HW_ENCODERS = {
    "darwin": [("h264_videotoolbox", ["-b:v", "8M", "-maxrate", "12M", "-allow_sw", "1"])],
    "win32": [("h264_amf", ["-quality", "quality", "-rc", "vbr_peak", "-b:v", "8M", "-maxrate", "12M"]),
              ("h264_nvenc", ["-preset", "p5", "-cq", "21", "-b:v", "0"]),
              ("h264_qsv", ["-global_quality", "22"])],
}


@functools.lru_cache(maxsize=1)
def hardware_encoder():
    """``(name, args)`` of the first hardware encoder that really encodes here, or None.

    Listed in ``ffmpeg -encoders`` is not enough (AMF is compiled into every
    Windows build, GPU or not), so each candidate encodes one tiny frame.
    ``FUNNY_VIDEO_ENCODER=libx264`` forces software.
    """
    forced = os.environ.get("FUNNY_VIDEO_ENCODER", "").strip()
    candidates = HW_ENCODERS.get(sys.platform, [])
    if forced:
        candidates = [c for c in candidates if c[0] == forced]
    for name, args in candidates:
        probe = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
                 "-frames:v", "1", "-c:v", name, *args, "-f", "null", "-"]
        try:
            if subprocess.run(probe, capture_output=True, timeout=20).returncode == 0:
                return name, args
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def video_encoder_args(crf=20, preset="veryfast"):
    hw = hardware_encoder()
    if hw:
        return ["-c:v", hw[0], *hw[1]]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]


def _escape_path(path):
    return path.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def build_command(src, start, end, out, plan_: EditPlan, src_size, ass_path=None,
                  fonts_dir=None, sfx_paths=None, music_path=None, reframe="blur",
                  facecam=None, music_db=MUSIC_DB, crf=20, preset="veryfast",
                  logo_path=None, logo_pos="kanan-atas"):
    """The full ffmpeg argv for one funny clip."""
    duration = end - start
    sfx_paths = sfx_paths or {}
    args = ["ffmpeg", "-y", "-nostdin", "-v", "error",
            "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", src]
    graph = [reframe_filters(*src_size, mode=reframe, facecam=facecam,
                             takeover=takeover_enable(plan_))]
    chain = []
    zf = zoom_filter(plan_)
    if zf:
        chain.append(zf)
    chain += flash_filters(plan_)
    chain += party_filters(plan_)
    if ass_path:
        sub = f"ass='{_escape_path(ass_path)}'"
        if fonts_dir:
            sub += f":fontsdir='{_escape_path(fonts_dir)}'"
        chain.append(sub)
    idx = 1
    if logo_path:
        args += ["-loop", "1", "-i", logo_path]
        pos = WATERMARK_POS.get(logo_pos, WATERMARK_POS["kanan-atas"])[1]
        graph.append("[base]" + ",".join(chain) + "[pre]")
        graph.append(f"[{idx}:v]scale={LOGO_W}:-1,format=rgba,colorchannelmixer=aa=0.85[logo]")
        graph.append(f"[pre][logo]overlay={pos}:shortest=1,format=yuv420p[v]")
        idx += 1
    else:
        chain.append("format=yuv420p")
        graph.append("[base]" + ",".join(chain) + "[v]")

    # Audio: voice, then one input per effect, then the music bed.
    voice = "[0:a]aresample=44100,aformat=channel_layouts=stereo"
    music_from = 0.0
    if plan_.drop:
        # Meme-story drop: muffled build-up, a beat of silence, then the punchline hits.
        p = plan_.punchlines[0]
        a, b = max(0.0, p - DROP_MUFFLE_S), p - DROP_GAP_S
        voice += (f",lowpass=f=650:enable='between(t,{a:.3f},{b:.3f})'"
                  f",volume=0:enable='between(t,{b:.3f},{p:.3f})'")
        music_from = p
    graph.append(voice + ",asplit=2[voice][key]")
    mix_inputs = ["[voice]"]
    for i, (t, name) in enumerate(plan_.sfx):
        path = sfx_paths.get(name)
        if not path:
            continue
        args += ["-i", path]
        ms = int(t * 1000)
        vol = 0.5 if name == "whoosh" else 0.9
        graph.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
                     f"volume={vol},adelay={ms}|{ms}[sfx{i}]")
        mix_inputs.append(f"[sfx{i}]")
        idx += 1
    if music_path:
        args += ["-stream_loop", "-1", "-i", music_path]
        delay = ""
        if music_from:
            ms = int(music_from * 1000)
            delay = f"adelay={ms}|{ms},"
            music_db += 4  # the drop: music comes in loud with the punchline
        graph.append(
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
            f"atrim=0:{max(0.5, duration - music_from):.3f},volume={music_db}dB,"
            f"afade=t=in:d={0.05 if music_from else 0.5},"
            f"afade=t=out:st={max(0.0, duration - music_from - 1.0):.3f}:d=1,{delay}anull[bed];"
            f"[bed][key]sidechaincompress=threshold=0.04:ratio=6:attack=20:release=400[music]")
        mix_inputs.append("[music]")
        idx += 1
    else:
        graph.append("[key]anullsink")
    graph.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:"
                 f"normalize=0,loudnorm=I={LOUDNESS_LUFS}:TP=-1.5:LRA=11[a]")

    args += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
             "-t", f"{duration:.3f}",
             *video_encoder_args(crf, preset),
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out]
    return args
