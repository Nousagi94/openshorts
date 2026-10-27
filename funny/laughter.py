"""Laughter and hype detection from the audio track, numpy only.

No model download and no GPU: the detector works on three cheap signals that
separate laughter from talk well enough to *shortlist* moments, which is all
the LLM pass needs from it.

* Loudness jump: the frame's energy against the median of the last ~8 s. A
  punchline is followed by the room (or the host) getting louder.
* Burst rhythm: "ha-ha-ha" / "wkwkwk" is a train of voiced bursts at 4-8 per
  second with deeper dips between them than ordinary syllables. Measured as the
  share of the energy envelope's modulation spectrum in that band, weighted by
  modulation depth.
* Noisiness: audience laughter and wheezing are broadband, so the spectral
  flatness of a laugh sits above that of voiced speech.

Transcript cues are folded in by ``moments`` (whisper writes "Hahaha",
"(tertawa)", "wkwk"), so a laugh the audio misses still counts when the ASR
heard it.

Output is a per-second score curve in 0..1 plus peak events. Thresholds are
relative to the file itself, so a quiet podcast and a loud stream both yield
peaks; a file with no laughter just yields low scores, and ``moments`` ignores
peaks under ``MIN_PEAK_SCORE``.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

SAMPLE_RATE = 16000
FRAME = 400          # 25 ms
HOP = 160            # 10 ms -> 100 frames per second
FPS = SAMPLE_RATE // HOP
WINDOW_S = 1.0       # analysis window for rhythm, in seconds
BASELINE_S = 8.0     # loudness baseline span
MIN_PEAK_SCORE = 0.45
PEAK_GAP_S = 6.0     # two laughs closer than this are one event


@dataclass
class LaughEvent:
    start: float
    end: float
    peak: float      # time of the strongest second
    score: float     # 0..1

    def to_dict(self):
        return {"start": round(self.start, 2), "end": round(self.end, 2),
                "peak": round(self.peak, 2), "score": round(self.score, 3)}


def load_audio(media_path: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode any media file to mono float32 with ffmpeg."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-i", media_path, "-vn",
           "-ac", "1", "-ar", str(sample_rate), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.float32).copy()


def _frames(audio: np.ndarray) -> np.ndarray:
    """Overlapping frames as a strided view: no copy, whatever the length."""
    if len(audio) < FRAME:
        audio = np.pad(audio, (0, FRAME - len(audio)))
    n = 1 + (len(audio) - FRAME) // HOP
    return np.lib.stride_tricks.as_strided(
        audio, shape=(n, FRAME), strides=(audio.strides[0] * HOP, audio.strides[0]),
        writeable=False)


def frame_features(audio: np.ndarray, block_frames: int = 6000):
    """Per-frame (rms_db, flatness) at 100 frames per second.

    Computed in blocks of ``block_frames`` (60 s): a 3-hour stream framed in
    one go is ~1.2M x 400 samples, several GB once windowed and FFT'd.
    """
    frames_all = _frames(audio)
    window = np.hanning(FRAME).astype(np.float32)
    lo, hi = int(300 * FRAME / SAMPLE_RATE), int(4000 * FRAME / SAMPLE_RATE)
    rms_db = np.empty(len(frames_all), dtype=np.float32)
    flatness = np.empty(len(frames_all), dtype=np.float32)
    for a in range(0, len(frames_all), block_frames):
        frames = frames_all[a:a + block_frames] * window[None, :]
        rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
        rms_db[a:a + len(frames)] = 20 * np.log10(rms + 1e-9)
        # 300 Hz - 4 kHz: where laughter and speech differ, without hum or hiss.
        band = np.abs(np.fft.rfft(frames, axis=1))[:, lo:hi] + 1e-9
        flatness[a:a + len(frames)] = (np.exp(np.mean(np.log(band), axis=1))
                                       / np.mean(band, axis=1))
    return rms_db, flatness


def _rolling_median(x: np.ndarray, span: int) -> np.ndarray:
    """Median of the preceding ``span`` samples (strided, so it stays fast)."""
    step = max(1, span // 16)
    out = np.empty_like(x)
    for i in range(0, len(x), step):
        lo = max(0, i - span)
        seg = x[lo:i] if i > lo else x[i:i + 1]
        out[i:i + step] = np.median(seg[::step] if len(seg) > 64 else seg)
    return out


def _rhythm_score(env_db: np.ndarray) -> float:
    """Burst-train strength of one window of the dB envelope, 0..1."""
    env = env_db - env_db.mean()
    if np.std(env) < 1e-6:
        return 0.0
    spec = np.abs(np.fft.rfft(env * np.hanning(len(env)))) ** 2
    freqs = np.fft.rfftfreq(len(env), d=1.0 / FPS)
    band = (freqs >= 3.5) & (freqs <= 8.5)
    total = spec[(freqs >= 0.5) & (freqs <= 20)].sum() + 1e-9
    share = spec[band].sum() / total
    # Laugh bursts dip 15-25 dB between "ha"s; syllables dip far less.
    depth = np.clip((np.percentile(env_db, 90) - np.percentile(env_db, 15)) / 25.0, 0, 1)
    return float(np.clip(share * 1.6, 0, 1) * depth)


def score_curve(audio: np.ndarray) -> np.ndarray:
    """Per-second laughter/hype score in 0..1."""
    rms_db, flat = frame_features(audio)
    n_sec = int(np.ceil(len(rms_db) / FPS))
    if n_sec == 0:
        return np.zeros(0)
    baseline = _rolling_median(rms_db, int(BASELINE_S * FPS))
    gate = np.percentile(rms_db, 20) + 6  # below this is silence/room tone
    flat_ref = np.median(flat[rms_db > gate]) if np.any(rms_db > gate) else np.median(flat)

    scores = np.zeros(n_sec)
    win = int(WINDOW_S * FPS)
    for s in range(n_sec):
        a, b = s * FPS, min(len(rms_db), s * FPS + win)
        if b - a < FPS // 2:
            continue
        seg = rms_db[a:b]
        voiced = seg > gate
        if voiced.mean() < 0.25:
            continue
        jump = np.clip((np.percentile(seg, 85) - np.median(baseline[a:b])) / 12.0, 0, 1)
        rhythm = _rhythm_score(seg)
        noisy = np.clip((np.median(flat[a:b][voiced]) - flat_ref) / (flat_ref + 1e-6), 0, 1)
        scores[s] = 0.4 * jump + 0.4 * rhythm + 0.2 * noisy
    # A laugh lasts more than one second; smooth so a lone spike does not win.
    kernel = np.array([0.25, 0.5, 0.25])
    return np.clip(np.convolve(scores, kernel, mode="same"), 0, 1)


def peaks(curve: np.ndarray, min_score: float = MIN_PEAK_SCORE,
          gap: float = PEAK_GAP_S) -> List[LaughEvent]:
    """Group seconds above the file's own threshold into events."""
    if len(curve) == 0:
        return []
    thresh = max(min_score * 0.8, float(np.percentile(curve, 90)))
    hot = np.where(curve >= thresh)[0]
    events: List[LaughEvent] = []
    for s in hot:
        if events and s - events[-1].end <= gap:
            ev = events[-1]
            ev.end = float(s + 1)
            if curve[s] > ev.score:
                ev.score, ev.peak = float(curve[s]), float(s) + 0.5
        else:
            events.append(LaughEvent(float(s), float(s + 1), float(s) + 0.5, float(curve[s])))
    return [e for e in events if e.score >= min_score]


def detect(media_path: str, audio: Optional[np.ndarray] = None):
    """``(curve, events)`` for a media file."""
    if audio is None:
        audio = load_audio(media_path)
    curve = score_curve(audio)
    return curve, peaks(curve)
