"""Video in, captioned funny clips out.

    transcript -> laughter -> moments -> per clip: plan, captions, one ffmpeg pass

Writes into ``out_dir``:
  funny_01.mp4, funny_02.mp4, ...   9:16 clips, best first by start time
  funny_01.txt, ...                 title + caption + hashtags, ready to paste
  funny_clips.json                  everything above plus scores and timings
  transcript.json                   reused by ``--transcript`` on a re-run
  render.json                       source, size and options, for re-rendering
  funny_01.edit.json, ...           hand-corrected subtitles (see save_edits)
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time

from . import captions, effects, laughter, moments as moments_mod, music, synth

# The stages a run goes through, in order, with the label the browser shows.
STAGES = (("download", "Download / ambil video"), ("transkripsi", "Ubah suara jadi teks"),
          ("tawa", "Cari suara tawa"), ("ai", "AI memilih momen lucu"),
          ("render", "Edit dan render klip"))

# Rough transcription speed (seconds of audio per second of work), only used
# to give the progress bar an estimate: mlx on an M-series GPU vs CPU whisper.
ASR_SPEED = {"mlx": 6.0, "cpu": 2.0}


def _no_progress(stage, fraction=None, detail=None, eta=None):
    pass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS_DIR = os.path.join(REPO_ROOT, "fonts")
SFX_DIR = os.path.join(REPO_ROOT, "assets", "sfx")


def probe(path):
    """``(width, height, duration)`` of the first video stream."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    st = (data.get("streams") or [{}])[0]
    return int(st.get("width", 0)), int(st.get("height", 0)), float(data["format"]["duration"])


def parse_time(value):
    """``"90"``, ``"1:30"``, ``"1:02:03"`` or ``"1:30.5"`` -> seconds; None passes through."""
    if value is None or value == "":
        return None
    secs = 0.0
    for part in str(value).strip().split(":"):
        secs = secs * 60 + float(part)
    return secs


def download(url, out_dir, start=None, end=None, log=print, progress=_no_progress):
    """Fetch a URL with yt-dlp (best <=1080p mp4); returns the file path.

    With ``start``/``end`` only that stretch is downloaded, so picking 15
    minutes out of a 3-hour stream does not fetch the whole VOD.
    """
    import yt_dlp
    opts = {"format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
            "outtmpl": os.path.join(out_dir, "source.%(ext)s"),
            "merge_output_format": "mp4", "noplaylist": True, "quiet": True,
            "noprogress": True}

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            got = d.get("downloaded_bytes") or 0
            mb = f"{got / 1e6:.0f} MB" + (f" dari {total / 1e6:.0f} MB" if total else "")
            progress("download", got / total if total else None, mb)
    opts["progress_hooks"] = [hook]
    if start is not None or end is not None:
        from yt_dlp.utils import download_range_func
        opts["download_ranges"] = download_range_func(None, [(start or 0, end or float("inf"))])
        opts["force_keyframes_at_cuts"] = True
    log(f"⬇️  Download {url}" + (f" ({start or 0:.0f}s-{end or 0:.0f}s)" if end else ""))
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info).rsplit(".", 1)[0] + ".mp4"


def cut_range(src, out_dir, start=None, end=None, log=print):
    """Copy ``[start, end]`` of a local file to ``range.mp4`` (stream copy, fast)."""
    out = os.path.join(out_dir, "range.mp4")
    cmd = ["ffmpeg", "-y", "-nostdin", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if end:
        cmd += ["-to", f"{end:.3f}"] if not start else ["-t", f"{end - start:.3f}"]
    cmd += ["-i", src, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
            "-avoid_negative_ts", "make_zero", out]
    log(f"✂️  Ambil rentang {start or 0:.0f}s-{end or 0:.0f}s")
    subprocess.run(cmd, check=True)
    return out


def apple_silicon():
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def asr_eta(duration):
    """Seconds the transcription will roughly take, for the progress bar."""
    backend = os.environ.get("TRANSCRIBE_BACKEND") or ("mlx" if apple_silicon() else "cpu")
    return 15 + duration / ASR_SPEED["mlx" if backend == "mlx" else "cpu"]


class _ProgressTee:
    """Pass a stream through while reading transcription progress out of it.

    mlx-whisper draws a tqdm bar on stderr ("37%|####  | ..."), faster-whisper
    prints "Transcribing… 25%" on stdout, and transcribe_backends reports a
    backend fallback as an "[ASR]" line; the first two become real progress,
    the last goes into the job log.
    """

    _PCT = re.compile(r"(\d{1,3})%(?:\||\s*\()")

    def __init__(self, stream, progress, log):
        self.stream, self.progress, self.log = stream, progress, log
        self.last = -1

    def write(self, text):
        self.stream.write(text)
        for m in self._PCT.finditer(text):
            pct = int(m.group(1))
            if pct != self.last and pct < 100:
                self.last = pct
                self.progress("transkripsi", pct / 100.0)
        if "[ASR]" in text:
            self.log(text.strip())
        return len(text)

    def flush(self):
        self.stream.flush()

    def isatty(self):
        return False

    def __getattr__(self, name):
        return getattr(self.stream, name)


def transcribe(path, language="id", log=print, progress=_no_progress):
    # On an M-series Mac whisper runs on the GPU through MLX unless the
    # user chose a backend; transcribe_backends falls back to CPU by itself.
    if apple_silicon() and not os.environ.get("TRANSCRIBE_BACKEND"):
        os.environ["TRANSCRIBE_BACKEND"] = "mlx"
    import transcribe_backends
    log(f"🎙️ Transkripsi ({language or 'auto'})…")
    out, err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = _ProgressTee(out, progress, log), _ProgressTee(err, progress, log)
    try:
        return transcribe_backends.transcribe_media(path, language=language)
    finally:
        sys.stdout, sys.stderr = out, err
        transcribe_backends.release_models()


def sfx_paths(cache_dir):
    """name -> wav/mp3, preferring the creator's own files in assets/sfx."""
    paths = {}
    for name in synth.SFX_NAMES:
        for ext in (".wav", ".mp3"):
            own = os.path.join(SFX_DIR, name + ext)
            if os.path.exists(own):
                paths[name] = own
                break
        else:
            paths[name] = synth.ensure(name, cache_dir)
    return paths


def choose_music(cache_dir, mood="funny", seed=None):
    track = music.pick(music.load_library(music.library_dir(REPO_ROOT)), mood, seed)
    if track:
        return track
    return {"path": synth.ensure("loop", cache_dir), "mood": "funny",
            "source": "generated (funny/synth.py)", "license": "royalty-free, generated"}


def resolve_facecam(options, source, size, log=print):
    """The facecam box for the stream layout, or None to fall back to blur."""
    if options.get("layout") != "stream":
        return None
    spec = options.get("facecam") or "auto"
    if spec != "auto":
        return effects.parse_box(spec, *size[:2])
    try:
        import camera_inset  # needs the full OpenShorts stack (cv2, YOLO)
        box = camera_inset.detect(source)
    except Exception as e:
        log(f"⚠️ Deteksi facecam otomatis tidak tersedia ({type(e).__name__}); "
            f"isi --facecam x,y,w,h. Pakai layout blur.")
        return None
    if not box:
        log("ℹ️ Facecam tidak ketemu, pakai layout blur.")
    return box


def render_clip(src, src_size, moment, transcript, out_path, cache_dir, options, log=print,
                words=None, seed=None):
    """Render one clip. ``words`` (clip time) overrides the transcript's subtitles;
    ``seed`` picks the music track (default: the output path)."""
    start, end = moment["start"], moment["end"]
    facecam = options.get("facecam_box")
    layout = "stream" if facecam else options.get("layout", "blur")
    if layout == "stream" and not facecam:
        layout = "blur"
    style = options.get("style", captions.DEFAULT_STYLE)
    plan = effects.plan(moment, start, end, effects=options.get("effects", True),
                        meme=options.get("meme", style == "meme"), layout=layout)
    context = moment.get("context_text") if options.get("context", True) else None
    ass_path = out_path.rsplit(".", 1)[0] + ".ass"
    tr = transcript if options.get("captions", True) else None
    if not options.get("captions", True):
        words = None
    if not captions.write_ass(tr, start, end, ass_path, memes=plan.memes, style=style,
                              context=context, words=words):
        ass_path = None
    track = None
    if options.get("music", True):
        track = choose_music(cache_dir, options.get("mood", "funny"), seed=seed or out_path)
    cmd = effects.build_command(
        src, start, end, out_path, plan, src_size[:2], ass_path=ass_path,
        fonts_dir=FONTS_DIR, sfx_paths=sfx_paths(cache_dir) if plan.sfx else None,
        music_path=track["path"] if track else None, reframe=layout, facecam=facecam,
        music_db=options.get("music_db", effects.MUSIC_DB))
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg gagal: {proc.stderr[-800:]}")
    if ass_path and not options.get("keep_ass"):
        os.remove(ass_path)
    log(f"   🎬 {os.path.basename(out_path)} ({end - start:.0f}s, "
        f"{len(plan.punchlines)} punchline, {time.time() - t0:.0f}s render)")
    music_info = None
    if track:
        music_info = {"file": os.path.basename(track["path"]), "source": track["source"],
                      "license": track["license"]}
    return {"punchlines_clip_time": plan.punchlines, "sfx": plan.sfx, "memes": plan.memes,
            "layout": layout, "music": music_info}


def post_text(m):
    return f"{m['title']}\n\n{m['caption']}\n\n{' '.join(m['hashtags'])}\n"


def run(source, out_dir, transcript=None, language="id", max_clips=5, use_model=True,
        options=None, start=None, end=None, log=print, progress=_no_progress):
    """The whole pipeline. ``source`` is a file path or a URL.

    ``start``/``end`` (seconds) limit the analysis to that stretch; clip times
    in the output are then relative to the stretch, and ``range_offset`` in
    funny_clips.json says where it began in the original.

    ``progress(stage, fraction, detail, eta)`` is called as each of
    ``STAGES`` starts and advances; ``fraction`` is None when unknown and
    ``eta`` is a rough total in seconds for stages without real progress.
    """
    options = options or {}
    os.makedirs(out_dir, exist_ok=True)
    cache_dir = os.path.join(out_dir, ".cache")
    if start is not None and end is not None and end <= start:
        raise ValueError("--sampai harus setelah --dari")
    progress("download", None, "menyiapkan video")
    if source.startswith(("http://", "https://")):
        source = download(source, out_dir, start, end, log, progress)
    elif start or end:
        source = cut_range(source, out_dir, start, end, log)
    w, h, duration = probe(source)
    progress("download", 1.0, f"video {duration / 60:.0f} menit siap")
    log(f"📼 {os.path.basename(source)}: {w}x{h}, {duration:.0f}s")
    options["facecam_box"] = resolve_facecam(options, source, (w, h), log)
    with open(os.path.join(out_dir, "render.json"), "w", encoding="utf-8") as f:
        json.dump({"source": os.path.abspath(source), "size": [w, h], "options": options}, f,
                  ensure_ascii=False, indent=2)

    if transcript is None:
        progress("transkripsi", None, f"video {duration / 60:.0f} menit", asr_eta(duration))
        transcript = transcribe(source, language, log, progress)
    # Always next to the clips: the subtitle editor reads the words from it.
    with open(os.path.join(out_dir, "transcript.json"), "w", encoding="utf-8") as f:
        json.dump(transcript, f, ensure_ascii=False)

    words = sum(len(s.get("words") or []) for s in transcript.get("segments", []))
    progress("transkripsi", 1.0, f"{words} kata")
    progress("tawa", None, "menganalisis audio")
    log("😂 Deteksi tawa…")
    curve, events = laughter.detect(source)
    log(f"   {len(events)} lonjakan tawa/hype di audio")
    progress("tawa", 1.0, f"{len(events)} momen tawa terdengar")
    progress("ai", None, "membaca transkrip", 30 + words / 40)

    found, laughs = moments_mod.find_moments(
        transcript, events, duration, max_clips, use_model=use_model, log=log,
        progress=lambda f: progress("ai", min(f, 0.99)))
    progress("ai", 1.0, f"{len(found)} momen dipilih")
    if not found:
        log("🤷 Tidak ada momen lucu yang terdeteksi.")
    results = []
    for i, m in enumerate(found, 1):
        progress("render", (i - 1) / len(found), f"klip {i} dari {len(found)}")
        out_path = os.path.join(out_dir, f"funny_{i:02d}.mp4")
        render = render_clip(source, (w, h), m, transcript, out_path, cache_dir, options, log)
        with open(out_path[:-4] + ".txt", "w", encoding="utf-8") as f:
            f.write(post_text(m))
        results.append(dict(m, file=os.path.basename(out_path), **render))

    progress("render", 1.0, f"{len(results)} klip jadi")
    summary = {"source": os.path.basename(source), "duration": duration,
               "range_offset": start or 0.0,
               "language": transcript.get("language"), "laughs": laughs,
               "clips": results}
    with open(os.path.join(out_dir, "funny_clips.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


# --- correcting subtitles after the run ----------------------------------------

def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def render_state(job_dir, summary):
    """Source, size and options of a finished run.

    Runs from before render.json existed get a best guess: the source by name
    next to the clips (or in the web uploads folder) and default options with
    each clip's own layout; a facecam layout without its box falls back to blur.
    """
    state = read_json(os.path.join(job_dir, "render.json"))
    if state and os.path.exists(state["source"]):
        return state
    name = os.path.basename(summary.get("source") or "")
    for cand in (os.path.join(job_dir, name),
                 os.path.join(os.path.dirname(job_dir), "uploads", name)):
        if name and os.path.exists(cand):
            w, h, _ = probe(cand)
            options = (state or {}).get("options") or {}
            return {"source": cand, "size": [w, h], "options": options}
    raise FileNotFoundError("video sumber klip ini sudah tidak ada, buat ulang klipnya")


def _clip(job_dir, index):
    summary = read_json(os.path.join(job_dir, "funny_clips.json"))
    if not summary:
        raise FileNotFoundError("hasil proses tidak ditemukan")
    name = f"funny_{index:02d}.mp4"
    for clip in summary["clips"]:
        if clip["file"] == name:
            return summary, clip
    raise FileNotFoundError(f"{name} tidak ada")


def _clip_words(job_dir, index, clip):
    edit = read_json(os.path.join(job_dir, f"funny_{index:02d}.edit.json"))
    if edit:
        return edit["words"]
    transcript = read_json(os.path.join(job_dir, "transcript.json"), {})
    return captions.clip_words(transcript, clip["start"], clip["end"])


def caption_lines(job_dir, index):
    """What the subtitle editor shows for one clip: its on-screen lines,
    the context line and the post text."""
    _, clip = _clip(job_dir, index)
    lines = captions.edit_blocks(_clip_words(job_dir, index, clip))
    txt = os.path.join(job_dir, f"funny_{index:02d}.txt")
    post = open(txt, encoding="utf-8").read() if os.path.exists(txt) else post_text(clip)
    return {"lines": [{"start": round(l["start"], 2), "end": round(l["end"], 2),
                       "text": l["text"]} for l in lines],
            "context": clip.get("context_text") or "", "post": post}


def _replace(src, dst, tries=20):
    """os.replace that waits out a player still reading ``dst`` (Windows locks it)."""
    for i in range(tries):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.5)


def save_edits(job_dir, index, texts, context=None, post=None, log=print):
    """Apply the editor's corrections to clip ``index`` (1-based).

    ``texts`` has one entry per line of :func:`caption_lines`. The clip is
    re-rendered only when a line or the context changed; ``post`` just
    rewrites the .txt. Returns True when the video was re-rendered.
    """
    summary, clip = _clip(job_dir, index)
    stem = os.path.join(job_dir, f"funny_{index:02d}")
    if post is not None:
        with open(stem + ".txt", "w", encoding="utf-8") as f:
            f.write(post.strip() + "\n")
    lines = captions.edit_blocks(_clip_words(job_dir, index, clip))
    if len(texts) != len(lines):
        raise ValueError("jumlah baris subtitle berubah, muat ulang editornya")
    texts = [" ".join(str(t).split()) for t in texts]
    context = clip.get("context_text") or "" if context is None else " ".join(context.split())
    if texts == [l["text"] for l in lines] and context == (clip.get("context_text") or ""):
        return False
    words = captions.apply_edits([dict(l, text=t) for l, t in zip(lines, texts)])
    state = render_state(job_dir, summary)
    moment = dict(clip, context_text=context)
    tmp = stem + ".new.mp4"
    render_clip(state["source"], state["size"], moment, None, tmp,
                os.path.join(job_dir, ".cache"), dict(state["options"]), log,
                words=words, seed=stem + ".mp4")
    _replace(tmp, stem + ".mp4")
    _write_json(stem + ".edit.json", {"words": words})
    clip["context_text"] = context
    _write_json(os.path.join(job_dir, "funny_clips.json"), summary)
    return True
