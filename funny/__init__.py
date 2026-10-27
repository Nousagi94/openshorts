"""Funny-moment clipping for OpenShorts (Indonesian creators first).

The regular pipeline asks the model for "viral" moments. This package adds a
pipeline tuned for comedy: it listens for laughter, asks the model for the
punchline rather than the topic, and edits the clip like a meme page would
(punch-in zooms, sound effects, meme captions, background music).

Modules:
  laughter  audio laughter/hype detector (numpy only, no model download)
  moments   candidate windows from laughter + transcript, LLM ranking, fallback
  captions  TikTok-style word-by-word ASS captions with slang emphasis
  effects   zoom/shake/flash/SFX/meme-text plan and the FFmpeg graph for it
  music     royalty-free background music library and ducked mix
  pipeline  ties the stages together; ``funny_clip.py`` is its CLI

Everything here is importable without the heavy video stack (torch, cv2,
mediapipe), so it is unit-testable in CI like ``clip_selection``.
"""
