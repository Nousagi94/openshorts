#!/usr/bin/env python3
"""Auto-clip the funny moments of a video into captioned 9:16 meme edits.

    python funny_clip.py -i podcast.mp4 -o output/funny
    python funny_clip.py -u https://youtu.be/... --max-clips 3
    python funny_clip.py -u https://youtu.be/... --dari 10:00 --sampai 25:30
    python funny_clip.py -i stream.mp4 --transcript output/funny/transcript.json --no-model

Uses GEMINI_API_KEY (or LLM_BASE_URL for a local model) to rank the moments;
without either it cuts around the strongest laughs. See
docs/architecture/funny-clips.md.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from funny import effects, pipeline  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description="Auto clipping momen lucu (OpenShorts Funny).")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("-i", "--input", help="video file")
    src.add_argument("-u", "--url", help="video URL (YouTube, etc.)")
    p.add_argument("--dari", "--from", dest="start", help="mulai dari (detik, mm:ss, atau hh:mm:ss)")
    p.add_argument("--sampai", "--to", dest="end", help="sampai (detik, mm:ss, atau hh:mm:ss)")
    p.add_argument("-o", "--output", default="output/funny", help="output folder")
    p.add_argument("--transcript", help="reuse a transcript.json from a previous run")
    p.add_argument("--language", default="id",
                   help="speech language for whisper (default id; 'auto' to detect)")
    p.add_argument("--max-clips", type=int, default=5)
    p.add_argument("--no-model", action="store_true", help="laughter only, no LLM call")
    p.add_argument("--durasi", "--length", dest="length", choices=("pendek", "sedang", "panjang"),
                   help="clip length: pendek 12-30 s, sedang 30-60 s, panjang 1-2 min "
                   "(default 12-60 s)")
    p.add_argument("--layout", "--reframe", dest="layout", choices=("blur", "crop", "stream"),
                   default="blur", help="blur: full frame over blurred fill; crop: centre crop; "
                   "stream: facecam on top, screen/game below")
    p.add_argument("--facecam", default="auto",
                   help="stream layout: facecam box x,y,w,h (pixels or 0-1 fractions), or auto")
    p.add_argument("--style", "--gaya", dest="style", choices=tuple(pipeline.LOOKS),
                   default="streamer",
                   help="edit look: streamer (1 word, blue), meme (yellow/red caps), warna "
                   "(1 word, rotating colours), frasa (2-3 word italic), party (disco lights), "
                   "cerita (title page + drop), bersih (plain)")
    p.add_argument("--utuh", "--whole", dest="whole", action="store_true",
                   help="no picking: the whole video (or --dari/--sampai) as one edited clip")
    p.add_argument("--watermark", help="your channel name, shown in a corner")
    p.add_argument("--logo", help="logo image (PNG) shown in a corner")
    p.add_argument("--watermark-pos", default="kanan-atas",
                   choices=("kiri-atas", "kanan-atas", "kanan-tengah", "kiri-bawah", "kanan-bawah"))
    p.add_argument("--censor", action=argparse.BooleanOptionalAction, default=None,
                   help="star out swear words (default: per look)")
    p.add_argument("--kata", type=int, choices=(1, 2, 3, 4),
                   help="words per caption (default: per look)")
    p.add_argument("--no-shake", action="store_true", help="no shake when someone shouts")
    p.add_argument("--no-context", action="store_true", help="no context line at the start")
    p.add_argument("--no-captions", action="store_true")
    p.add_argument("--no-effects", action="store_true", help="no zoom/shake/flash/SFX")
    p.add_argument("--meme", action=argparse.BooleanOptionalAction, default=None,
                   help="meme text at the punchline (default: on for --style meme)")
    p.add_argument("--no-music", action="store_true")
    p.add_argument("--mood", default="funny", help="music mood tag (assets/music/manifest.json)")
    p.add_argument("--music-db", type=float, default=effects.MUSIC_DB,
                   help=f"music level under speech (default {effects.MUSIC_DB})")
    p.add_argument("--keep-ass", action="store_true", help="keep the .ass caption files")
    a = p.parse_args(argv)

    transcript = None
    if a.transcript:
        with open(a.transcript, encoding="utf-8") as f:
            transcript = json.load(f)
    options = {"captions": not a.no_captions, "effects": not a.no_effects,
               "music": not a.no_music, "mood": a.mood, "music_db": a.music_db,
               "layout": a.layout, "facecam": a.facecam, "look": a.style,
               "style": pipeline.LOOKS[a.style]["style"], "shake": not a.no_shake,
               "context": not a.no_context, "keep_ass": a.keep_ass,
               "watermark_pos": a.watermark_pos}
    if a.meme is not None:
        options["meme"] = a.meme
    if a.censor is not None:
        options["censor"] = a.censor
    if a.kata:
        options["max_words"] = a.kata
    if a.watermark:
        options["watermark_text"] = a.watermark
    if a.logo:
        options["watermark_logo"] = os.path.abspath(a.logo)
    summary = pipeline.run(a.input or a.url, a.output, transcript=transcript,
                           language=None if a.language == "auto" else a.language,
                           max_clips=a.max_clips, use_model=not a.no_model,
                           options=options, start=pipeline.parse_time(a.start),
                           end=pipeline.parse_time(a.end), whole=a.whole,
                           length=a.length)
    print(f"\n✅ {len(summary['clips'])} klip di {a.output}")
    for c in summary["clips"]:
        print(f"   {c['file']}  [{c['funny_score']}]  {c['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
