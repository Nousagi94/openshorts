"""Background music: a royalty-free library folder and the ducked mix.

Trending TikTok songs are copyrighted; burning one into a clip gets it muted
or claimed, and TikTok's own trending sounds can only be attached in the
app. So the renderer only ever uses tracks the creator is allowed to publish:

* ``assets/music/`` (or ``FUNNY_MUSIC_DIR``): drop ``.mp3``/``.wav``/``.m4a``
  files from YouTube Audio Library, Pixabay Music, Uppbeat, or your own. An
  optional ``manifest.json`` tags them: ``[{"file": "x.mp3", "mood": "funny",
  "source": "...", "license": "..."}]``. Untagged files count as "funny".
* No files at all: the synthesised loop from ``synth.music_loop``.

To ride a trend, post the clip with the music low (or ``--no-music``) and add
the trending sound inside TikTok/Reels: the app licenses it there.
"""
from __future__ import annotations

import json
import os
import random

AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")
MOODS = ("funny", "chill", "dramatic", "hype")


def library_dir(repo_root):
    return os.environ.get("FUNNY_MUSIC_DIR") or os.path.join(repo_root, "assets", "music")


def load_library(folder):
    """``[{"path", "mood", "source", "license"}]`` for every track in ``folder``."""
    if not os.path.isdir(folder):
        return []
    tags = {}
    manifest = os.path.join(folder, "manifest.json")
    if os.path.exists(manifest):
        try:
            with open(manifest, encoding="utf-8") as f:
                for row in json.load(f):
                    tags[row.get("file")] = row
        except (ValueError, OSError):
            tags = {}
    tracks = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(AUDIO_EXT):
            continue
        row = tags.get(name, {})
        tracks.append({"path": os.path.join(folder, name),
                       "mood": (row.get("mood") or "funny").lower(),
                       "source": row.get("source", ""),
                       "license": row.get("license", "")})
    return tracks


def pick(tracks, mood="funny", seed=None):
    """A track of ``mood``, else any track, else None."""
    if not tracks:
        return None
    rng = random.Random(seed)
    same = [t for t in tracks if t["mood"] == mood]
    return rng.choice(same or tracks)
