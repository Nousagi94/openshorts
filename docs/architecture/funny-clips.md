# Funny clips (`funny/`, `funny_clip.py`)

A second pipeline next to the viral-moment one, tuned for comedy and for
Indonesian creators. It runs fully local (Mac or Linux): ffmpeg, whisper
(mlx-whisper on Apple Silicon, faster-whisper elsewhere) and any
OpenAI-compatible model server (Ollama) for the picker. Gemini is optional.

```
input (file | URL | either + --dari/--sampai)
  -> transcript (transcribe_backends, language pinned to "id" by default)
  -> laughter.detect      per-second laugh/hype score from the audio, numpy only
  -> moments.find_moments laughs + transcript cues -> LLM ranking -> clips
  -> per clip: effects.plan + captions.write_ass + ONE ffmpeg pass
  -> funny_NN.mp4 + funny_NN.txt (title/caption/hashtags) + funny_clips.json
```

## Picking moments

- `laughter.score_curve` mixes loudness jump over an 8 s baseline, 4-8 Hz
  burst rhythm with deep dips ("ha-ha-ha"), and spectral flatness. Thresholds
  are relative to the file. It shortlists; it does not decide.
- `moments.transcript_cues` adds laughs the ASR wrote down (`wkwk`,
  `hahaha`, `(tertawa)`). A cue next to an audio laugh boosts it.
- The prompt is Indonesian and interleaves `[TAWA score]` markers into the
  timestamped transcript. Laughter comes AFTER the punchline, and the prompt
  says so. The response is validated with `FunnyResponse` (pydantic), then
  snapped to sentence boundaries and clamped to 12-60 s.
- No model, or the call fails: `heuristic_moments` cuts around the strongest
  laughs (set-up before, stop before the next sentence after the laugh). The
  model is never allowed to fail the job.

## Rendering

One ffmpeg command per clip (`effects.build_command`): cut, 9:16 reframe,
punch effects, ASS captions, then voice + SFX + ducked music
(`sidechaincompress`) normalised to -14 LUFS.

- Layouts: `blur` (frame over its own blurred fill, default), `crop`, and
  `stream` (facecam band on top, rest of the frame below; at each punchline
  the facecam takes the whole frame for ~1.5 s). `--facecam auto` uses
  `camera_inset.detect`, which needs the full stack (cv2/YOLO); on a light
  install pass the box (`x,y,w,h`, pixels or 0-1 fractions).
- Punch effects (non-stream): zoompan punch-in with shake, white flash,
  whoosh + rotating SFX. Effects closer than 2 s collapse to the first.
- Captions (`captions.STYLES`): `streamer` (one word, Bangers, blue on white
  outline, on the stream seam; swear words censored `GOBL*K`) and `meme`
  (1-3 words, Anton, active word yellow, joke words red and tilted). Both can
  show the model's `context_text` bottom-left for 5 s and meme text at the
  punchline. Fonts load from `fonts/` via `fontsdir`, so no system install
  is needed.
- Music: only tracks the creator may publish (`assets/music/`, see its
  README). Never ship a trending copyrighted song burned into the clip.
  Empty library -> generated loop (`funny/synth.py`); SFX are generated too.

## Browser UI (`funny/webapp.py`)

`python -m funny.webapp` (or double-click `Auto Klip Lucu.command`) serves
`funny/web/index.html` on 127.0.0.1:8765 with the standard library only. One
job runs at a time; uploads stream as a raw body (`X-Filename` header), jobs
write to `output/web/<job id>/`, and `/files/<job>/funny_NN.mp4` serves the
clips with Range support (Safari will not play a video without it). Facecam
for the stream layout is a corner + size pick (`webapp.facecam_spec`).

Subtitle editor: each clip card has "Edit subtitle". `pipeline.caption_lines`
gives phrase-sized lines (`captions.edit_blocks`), the context line and the
post text; `pipeline.save_edits` turns edited lines back into timed words
(`captions.apply_edits`: same word count keeps each word's timing, otherwise
the line's time is shared by length), re-renders into `funny_NN.new.mp4` and
swaps it in (retrying while Windows still has the old file open), and keeps
the words in `funny_NN.edit.json`. A run writes `render.json` (source, size,
options) and always `transcript.json` so this works after a restart; finished
jobs on disk are listed under "Hasil sebelumnya" (`webapp.load_finished_jobs`).

## Mac

`scripts/setup_mac.sh` installs ffmpeg/python/ollama with Homebrew, the deps
from `requirements-funny.txt` into `.venv`, pulls `qwen2.5:7b` (`qwen2.5:3b`
under 12 GB RAM) and writes the local `.env`; it installs Homebrew itself when
missing. `Install.command` wraps it for a Finder double-click. `./klip ...`
runs the CLI.
`TRANSCRIBE_BACKEND=mlx` falls back to CPU faster-whisper on any failure.

## Windows

`Install.bat` runs `scripts/setup_windows.ps1` (winget: Gyan.FFmpeg,
Python.Python.3.12, Ollama.Ollama; then `.venv`, model pull, `.env` with CPU
faster-whisper). `Auto Klip Lucu.bat` reloads PATH from the registry (Explorer
may still hold the pre-install one), sets `PYTHONUTF8=1` for the emoji logs
and starts `funny.webapp`; `Update.bat` mirrors `Update.command` with
robocopy. The `.bat` files are stored CRLF and marked `-text` in
`.gitattributes` so zips keep them runnable. `webapp.keep_awake` uses
SetThreadExecutionState there. The `.ps1` stays ASCII: Windows PowerShell 5.1
reads BOM-less files as ANSI.

## GPU use

- The moment picker runs in Ollama, which uses NVIDIA, recent AMD Radeon
  (ROCm on Windows, e.g. RX 7900) and Apple GPUs by itself. The Windows setup
  pulls `qwen2.5:14b` when the card has 12 GB+ VRAM (read from the display
  adapter registry keys).
- Encoding: `effects.hardware_encoder` probes VideoToolbox (Mac), AMF, NVENC
  and QSV (Windows) by encoding one frame, and falls back to libx264;
  `FUNNY_VIDEO_ENCODER=libx264` forces software.
- Transcription on Windows is CPU faster-whisper (CTranslate2 has no AMD
  backend). A whisper.cpp Vulkan backend would put it on a Radeon; not done.

## Tests

`tests/test_funny.py` covers detection on synthetic audio, moment cleaning and
fallback, caption styles, the filter graph, and one real ffmpeg render (skipped
without ffmpeg). Nothing there needs a model, a GPU or the network.
