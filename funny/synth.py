"""Sound effects and a fallback music loop, synthesised with numpy.

Generated here, so they are ours and royalty-free by construction: a clip
never gets muted or claimed for a sound effect. Drop real files into
``assets/sfx/<name>.wav`` (or .mp3) to override any of them.

* boom   bass hit with a falling pitch (the "vine boom" role)
* boing  springy pitch wobble
* pop    short bubble pop
* whoosh filtered noise swell, for the zoom-in
* ding   bright bell
* loop   8-bar bouncy pizzicato loop for when ``assets/music`` is empty
"""
from __future__ import annotations

import os
import wave

import numpy as np

SR = 44100
SFX_NAMES = ("boom", "boing", "pop", "whoosh", "ding")


def _t(seconds):
    return np.arange(int(SR * seconds)) / SR


def _env(t, attack=0.005, decay=0.3):
    return np.minimum(1.0, t / max(attack, 1e-4)) * np.exp(-t / decay)


def boom():
    t = _t(1.2)
    freq = 40 + 90 * np.exp(-t * 9)
    phase = 2 * np.pi * np.cumsum(freq) / SR
    x = np.tanh(3.0 * np.sin(phase)) * _env(t, 0.003, 0.35)
    click = np.random.default_rng(1).standard_normal(len(t)) * np.exp(-t * 120) * 0.3
    return x + click


def boing():
    t = _t(0.7)
    freq = 220 + 260 * np.exp(-t * 4) * (1 + 0.35 * np.sin(2 * np.pi * 14 * t))
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return np.sin(phase) * _env(t, 0.004, 0.25)


def pop():
    t = _t(0.15)
    freq = 900 * np.exp(-t * 25) + 300
    return np.sin(2 * np.pi * np.cumsum(freq) / SR) * _env(t, 0.001, 0.03)


def whoosh():
    t = _t(0.45)
    noise = np.random.default_rng(2).standard_normal(len(t))
    # Rising one-pole low-pass sweep over white noise.
    out = np.zeros_like(noise)
    acc = 0.0
    cut = 0.02 + 0.3 * (t / t[-1]) ** 2
    for i, n in enumerate(noise):
        acc += cut[i] * (n - acc)
        out[i] = acc
    shape = np.sin(np.pi * t / t[-1]) ** 2
    return out * shape * 2.0


def ding():
    t = _t(1.0)
    x = sum(a * np.sin(2 * np.pi * f * t) for f, a in ((1568, 1.0), (3136, 0.4), (4704, 0.2)))
    return x * _env(t, 0.002, 0.3)


def music_loop(bars=8, bpm=118):
    """A cheerful C-major pizzicato + bass loop, seamless when repeated."""
    beat = 60.0 / bpm
    total = bars * 4 * beat
    n = int(SR * total)
    out = np.zeros(n)
    rng = np.random.default_rng(7)
    scale = [261.63, 293.66, 329.63, 392.0, 440.0, 523.25]
    chords = [(130.81, 0), (174.61, 3), (196.0, 4), (130.81, 0)]  # C F G C
    for step in range(bars * 8):          # eighth notes
        start = int(step * beat / 2 * SR)
        bar = step // 8
        root, _ = chords[bar % len(chords)]
        if step % 2 == 0 or rng.random() < 0.45:
            f = scale[rng.integers(len(scale))] * (2 if rng.random() < 0.2 else 1)
            t = _t(0.25)
            note = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t)) * _env(t, 0.002, 0.07)
            end = min(n, start + len(note))
            out[start:end] += 0.35 * note[:end - start]
        if step % 4 == 0:                  # bass on beats 1 and 3
            t = _t(beat * 1.5)
            b = np.sin(2 * np.pi * root * t) * _env(t, 0.005, 0.25)
            end = min(n, start + len(b))
            out[start:end] += 0.5 * b[:end - start]
        if step % 2 == 1:                  # soft hat on the off-beat
            t = _t(0.05)
            h = rng.standard_normal(len(t)) * np.exp(-t * 90) * 0.06
            end = min(n, start + len(h))
            out[start:end] += h[:end - start]
    return out


GENERATORS = {"boom": boom, "boing": boing, "pop": pop, "whoosh": whoosh,
              "ding": ding, "loop": music_loop}


def write_wav(samples, path):
    peak = np.max(np.abs(samples)) or 1.0
    pcm = (samples / peak * 0.89 * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def ensure(name, cache_dir):
    """Path to a generated ``name`` wav, created once per cache dir."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{name}.wav")
    if not os.path.exists(path):
        write_wav(GENERATORS[name](), path + ".tmp")
        os.replace(path + ".tmp", path)
    return path
