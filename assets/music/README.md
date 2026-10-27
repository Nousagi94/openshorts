# Background music for funny clips

Drop `.mp3` / `.wav` / `.m4a` files here (or point `FUNNY_MUSIC_DIR` at another
folder). Use only music you may publish: YouTube Audio Library, Pixabay Music,
Uppbeat (free tier), or your own. Trending TikTok songs are copyrighted, so a
clip with one burned in gets muted or claimed; add the trending sound inside
the TikTok/Reels app instead, where it is licensed.

Optional `manifest.json` to tag moods (`funny`, `chill`, `dramatic`, `hype`):

```json
[{"file": "kocak-ukulele.mp3", "mood": "funny", "source": "Pixabay", "license": "Pixabay Content License"}]
```

Untagged files count as `funny`. Pick a mood with `--mood`. With this folder
empty, clips get a generated royalty-free loop (`funny/synth.py`).

Audio files here are git-ignored except this README and the manifest.
