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


def plan(moment, clip_start, clip_end, effects=True, meme=True, layout="blur"):
    """Turn a moment's absolute punchline times into a clip-time edit plan."""
    duration = clip_end - clip_start
    punches = []
    for p in sorted(moment.get("punchlines") or []):
        t = float(p) - clip_start
        # Effects closer than 2 s to each other read as noise; keep the first.
        if 0.3 <= t <= duration - 0.5 and (not punches or t - punches[-1] >= 2.0):
            punches.append(round(t, 2))
    punches = punches[:4]
    if not effects:
        return EditPlan(duration, [], zoom=False, shake=False, flash=False)
    sfx = []
    for i, t in enumerate(punches):
        sfx.append((max(0.0, t - 0.4), "whoosh"))
        sfx.append((t, SFX_ROTATION[i % len(SFX_ROTATION)]))
    memes = []
    if meme and moment.get("meme_text") and punches:
        memes = [(punches[0] + 0.15, moment["meme_text"])]
    if layout == "stream":
        return EditPlan(duration, punches, sfx, memes, zoom=False, shake=False,
                        flash=False, takeover=True)
    return EditPlan(duration, punches, sfx, memes)


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
    """``zoompan`` doing punch-ins (+ shake) at every punchline, or None."""
    if not (plan_.zoom and plan_.punchlines):
        return None
    T = f"(on/{FPS})"
    env = "+".join(_envelope(p) for p in plan_.punchlines).replace("T", T)
    z = f"1+{ZOOM - 1:.3f}*min(1,{env})"
    shake = "0"
    if plan_.shake:
        shake = "+".join(
            f"if(between({T},{p:.3f},{p + SHAKE_S:.3f}),{SHAKE_PX}*sin({T}*95),0)"
            for p in plan_.punchlines)
    x = f"max(0,min(iw-iw/zoom,iw/2-iw/zoom/2+({shake})))"
    y = f"max(0,min(ih-ih/zoom,ih/2-ih/zoom/2+({shake.replace('sin', 'cos')})))"
    return f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={OUT_W}x{OUT_H}:fps={FPS}"


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
                  facecam=None, music_db=MUSIC_DB, crf=20, preset="veryfast"):
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
    if ass_path:
        sub = f"ass='{_escape_path(ass_path)}'"
        if fonts_dir:
            sub += f":fontsdir='{_escape_path(fonts_dir)}'"
        chain.append(sub)
    chain.append("format=yuv420p")
    graph.append("[base]" + ",".join(chain) + "[v]")

    # Audio: voice, then one input per effect, then the music bed.
    voice = "[0:a]aresample=44100,aformat=channel_layouts=stereo,asplit=2[voice][key]"
    graph.append(voice)
    mix_inputs = ["[voice]"]
    idx = 1
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
        graph.append(
            f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
            f"atrim=0:{duration:.3f},volume={music_db}dB,"
            f"afade=t=in:d=0.5,afade=t=out:st={max(0.0, duration - 1.0):.3f}:d=1[bed];"
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
