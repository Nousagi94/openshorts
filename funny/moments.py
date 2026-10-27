"""Pick the funny moments: laughter shortlist, LLM ranking, heuristic fallback.

Flow:
1. ``transcript_cues`` finds laughs the ASR wrote down ("wkwk", "hahaha",
   "(tertawa)", "[Laughter]") and merges them with the audio events from
   ``laughter``.
2. ``build_prompt`` gives the model the timestamped transcript with ``[TAWA]``
   markers where people laughed, and asks for set-up + punchline + reaction
   clips, Indonesian titles and hashtags, the punchline timestamps (where the
   editor zooms and drops a sound effect) and a short meme caption.
3. Without a model (no ``GEMINI_API_KEY`` and no ``LLM_BASE_URL``) or when the
   call fails, ``heuristic_moments`` cuts around the strongest laughs: the
   joke comes *before* the laugh, so a window ends a few seconds after the
   laugh peak and starts ~20 s earlier, snapped to sentence boundaries.

Both paths return the same ``FunnyMoment`` dicts, so the renderer never
branches on where a moment came from.
"""
from __future__ import annotations

import json
import os
import re
from typing import List

from pydantic import BaseModel, Field

MIN_CLIP_S = 12.0
MAX_CLIP_S = 60.0
PRE_ROLL_S = 20.0     # set-up before the laugh peak
POST_ROLL_S = 4.0     # the reaction is part of the joke
DEFAULT_MAX_CLIPS = 5

LAUGH_TOKENS = re.compile(
    r"(w+k+w*k+[wk]*|h(?:a|e|i){1,}h(?:a|e|i)+[ah]*|ngakak|kwkw+|"
    r"\(?\[?\s*(?:tertawa|ketawa|tawa|laughter|laughs|laughing|tawa penonton)\s*\]?\)?)",
    re.IGNORECASE)


class FunnyMomentModel(BaseModel):
    start: float = Field(description="clip start, seconds")
    end: float = Field(description="clip end, seconds")
    punchlines: List[float] = Field(default_factory=list,
                                    description="seconds where the joke lands")
    funny_score: int = Field(ge=0, le=100)
    title: str
    caption: str
    hashtags: List[str] = Field(default_factory=list)
    meme_text: str = ""
    context_text: str = ""
    reason: str = ""


class FunnyResponse(BaseModel):
    moments: List[FunnyMomentModel]


def transcript_cues(transcript) -> List[float]:
    """Times where the ASR transcribed laughter."""
    times = []
    for seg in (transcript or {}).get("segments", []):
        words = seg.get("words") or []
        hit_words = [w for w in words if LAUGH_TOKENS.search(w.get("word", ""))]
        if hit_words:
            times.extend(float(w["start"]) for w in hit_words)
        elif LAUGH_TOKENS.search(seg.get("text", "")):
            times.append(float(seg["start"]))
    return sorted(times)


def merge_laughs(audio_events, cue_times, gap: float = 6.0):
    """One list of ``{"t", "score"}`` laugh marks from audio and transcript."""
    marks = [{"t": float(e["peak"]), "score": float(e["score"])} for e in audio_events]
    for t in cue_times:
        near = [m for m in marks if abs(m["t"] - t) <= gap]
        if near:
            for m in near:  # the ASR agreeing with the audio is strong evidence
                m["score"] = min(1.0, m["score"] + 0.25)
        else:
            marks.append({"t": float(t), "score": 0.6})
    return sorted(marks, key=lambda m: m["t"])


def _sentence_bounds(transcript):
    return [(float(s["start"]), float(s["end"])) for s in (transcript or {}).get("segments", [])]


def snap_window(start, end, transcript, duration):
    """Widen [start, end] to whole sentences and clamp to the clip length band."""
    bounds = _sentence_bounds(transcript)
    for i, (a, b) in enumerate(bounds):
        if a <= start < b:
            # Inside the last half second of a sentence: start on the next one
            # rather than dragging in a sentence that is already over.
            start = bounds[i + 1][0] if b - start < 0.6 and i + 1 < len(bounds) else a
        if a < end <= b:
            end = b
    start = max(0.0, start)
    end = min(float(duration), end)
    if end - start < MIN_CLIP_S:
        pad = (MIN_CLIP_S - (end - start)) / 2
        start, end = max(0.0, start - pad), min(float(duration), end + pad)
    if end - start > MAX_CLIP_S:
        start = end - MAX_CLIP_S
    return round(start, 2), round(end, 2)


def _overlaps(a, b, slack=2.0):
    return a["start"] < b["end"] - slack and b["start"] < a["end"] - slack


def dedupe(moments, max_clips):
    """Best first, no two clips sharing the same joke."""
    out = []
    for m in sorted(moments, key=lambda m: -m["funny_score"]):
        if any(_overlaps(m, o) for o in out):
            continue
        out.append(m)
        if len(out) >= max_clips:
            break
    return sorted(out, key=lambda m: m["start"])


def _text_between(transcript, start, end, limit=140):
    words = []
    for seg in (transcript or {}).get("segments", []):
        if seg["end"] < start or seg["start"] > end:
            continue
        words.append(seg.get("text", "").strip())
    text = " ".join(words)
    return text[:limit].rsplit(" ", 1)[0] if len(text) > limit else text


def punch_before(transcript, t, fallback_lead=1.0):
    """Where the joke lands for a laugh at ``t``: the last words said before it.

    The laugh peak is seconds after the punchline. The last sentence that
    starts before the laugh carries the joke; its final ~0.8 s is where the
    zoom and the boom belong.
    """
    before = [s for s in (transcript or {}).get("segments", []) if s["start"] < t - 0.3]
    if not before:
        return max(0.0, t - fallback_lead)
    seg = before[-1]
    words = [w for w in seg.get("words") or [] if w["start"] < t]
    if words:
        return max(float(seg["start"]), float(words[-1]["start"]) - 0.1)
    return max(float(seg["start"]), min(float(seg["end"]), t) - 0.8)


def next_sentence_start(transcript, t):
    starts = [float(s["start"]) for s in (transcript or {}).get("segments", [])
              if float(s["start"]) > t]
    return min(starts) if starts else None


def heuristic_moments(laughs, transcript, duration, max_clips=DEFAULT_MAX_CLIPS):
    """Clips around the strongest laughs, for when no model is available."""
    moments = []
    for m in sorted(laughs, key=lambda m: -m["score"]):
        punch = punch_before(transcript, m["t"])
        end = m["t"] + POST_ROLL_S
        nxt = next_sentence_start(transcript, m["t"] - 0.5)
        if nxt is not None and nxt < end:
            end = max(m["t"] + 1.0, nxt)  # stop before the next topic starts
        start, end = snap_window(punch - PRE_ROLL_S, end, transcript, duration)
        moments.append({
            "start": start, "end": end, "punchlines": [round(punch, 2)],
            "funny_score": int(round(m["score"] * 100)),
            "title": "Momen paling ngakak 🤣",
            "caption": _text_between(transcript, start, end) or "Gak kuat liat ini 😭",
            "hashtags": ["#lucu", "#ngakak", "#fyp", "#komedi", "#wkwkwk"],
            "meme_text": "WKWKWK", "context_text": "",
            "reason": "laughter peak", "source": "heuristic",
        })
    return dedupe(moments, max_clips)


def format_transcript(transcript, laughs, max_chars=60000):
    """``[mm:ss.s] text`` lines with ``[TAWA x.xx]`` markers interleaved."""
    marks = list(laughs)
    lines = []
    for seg in (transcript or {}).get("segments", []):
        while marks and marks[0]["t"] <= seg["start"]:
            m = marks.pop(0)
            lines.append(f"[{m['t']:.1f}] [TAWA {m['score']:.2f}]")
        lines.append(f"[{seg['start']:.1f}-{seg['end']:.1f}] {seg.get('text', '').strip()}")
    for m in marks:
        lines.append(f"[{m['t']:.1f}] [TAWA {m['score']:.2f}]")
    text = "\n".join(lines)
    return text[:max_chars]


def build_prompt(transcript, laughs, duration, max_clips=DEFAULT_MAX_CLIPS):
    return f"""Kamu adalah editor konten komedi untuk TikTok, Reels, dan Shorts Indonesia.
Dari transkrip video berdurasi {duration:.0f} detik di bawah, pilih maksimal {max_clips} momen PALING LUCU
yang bisa berdiri sendiri sebagai klip pendek.

Penanda [TAWA skor] artinya di detik itu terdengar tawa (skor 0-1, makin tinggi makin yakin).
Tawa biasanya terjadi SETELAH punchline, jadi klip harus memuat set-up, punchline, dan reaksinya.

Aturan:
- Durasi tiap klip {MIN_CLIP_S:.0f}-{MAX_CLIP_S:.0f} detik. Mulai di awal kalimat, jangan potong di tengah kata.
- Utamakan: punchline, roasting, salah ucap, reaksi kaget, plesetan, situasi absurd, celetukan spontan.
- Jangan pilih bagian yang hanya lucu kalau tahu konteks panjang sebelumnya.
- "punchlines": detik (absolut, dari transkrip) saat lelucon mendarat. Di situ editor akan zoom dan kasih efek suara.
- "funny_score" 0-100: seberapa besar kemungkinan orang Indonesia ketawa dan share.
- "title": judul clickbait bahasa Indonesia gaul, maksimal 60 karakter, boleh 1 emoji.
- "caption": caption posting 1-2 kalimat santai bahasa Indonesia.
- "hashtags": 5-8 hashtag, campur umum (#fyp #lucu #ngakak) dan spesifik isi klip.
- "meme_text": teks meme pendek KAPITAL maksimal 3 kata yang muncul saat punchline (contoh: "WKWKWK", "GAK KUAT", "PLOT TWIST").
- "context_text": satu baris konteks untuk penonton yang belum tahu situasinya, maksimal 45 karakter,
  gaya caption klip streamer (contoh: "Saya: Mas Rian nge-whatsapp", "Chat: minta di-roasting"). Kosongkan kalau tidak perlu.
- "reason": satu kalimat kenapa lucu.
- Kalau tidak ada yang lucu, kembalikan daftar kosong. Jangan mengarang.

Balas HANYA JSON: {{"moments": [{{"start": 0, "end": 0, "punchlines": [0], "funny_score": 0, "title": "", "caption": "", "hashtags": [], "meme_text": "", "context_text": "", "reason": ""}}]}}

TRANSKRIP:
{format_transcript(transcript, laughs)}
"""


def _call_model(prompt):
    """``(parsed, provider)`` or ``(None, reason)`` when no model is usable."""
    try:
        import llm_backend
    except Exception:  # pragma: no cover - repo root not on sys.path
        llm_backend = None
    if llm_backend is not None and llm_backend.active():
        parsed, _ = llm_backend.generate_json(prompt, FunnyResponse)
        return parsed, "llm:" + llm_backend.model_name()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None, "no GEMINI_API_KEY / LLM_BASE_URL"
    from google import genai
    from google.genai import types as genai_types
    model = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
    client = genai.Client(api_key=key)
    resp = client.models.generate_content(
        model=model, contents=prompt,
        config=genai_types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=FunnyResponse))
    parsed = getattr(resp, "parsed", None)
    parsed = parsed.model_dump() if parsed is not None else json.loads(resp.text)
    return parsed, "gemini:" + model


def clean_model_moments(raw, transcript, duration, max_clips):
    """Validate, snap and clamp what the model returned."""
    out = []
    for m in (raw or {}).get("moments", []):
        try:
            m = FunnyMomentModel.model_validate(m).model_dump()
        except Exception:
            continue
        if m["end"] <= m["start"] or m["start"] >= duration:
            continue
        m["start"], m["end"] = snap_window(m["start"], m["end"], transcript, duration)
        m["punchlines"] = [round(float(p), 2) for p in m["punchlines"]
                           if m["start"] <= float(p) <= m["end"]] or \
                          [round(m["start"] + (m["end"] - m["start"]) * 0.7, 2)]
        m["hashtags"] = [h if h.startswith("#") else "#" + h
                         for h in (t.strip().replace(" ", "") for t in m["hashtags"]) if h][:8]
        m["meme_text"] = m["meme_text"].strip().upper()[:24]
        m["context_text"] = m["context_text"].strip()[:60]
        m["source"] = "model"
        out.append(m)
    return dedupe(out, max_clips)


def find_moments(transcript, audio_events, duration, max_clips=DEFAULT_MAX_CLIPS,
                 use_model=True, log=print):
    """The funny moments of a video, best effort, never raising on the model."""
    laughs = merge_laughs([e if isinstance(e, dict) else e.to_dict() for e in audio_events],
                          transcript_cues(transcript))
    if use_model and (transcript or {}).get("segments"):
        try:
            raw, provider = _call_model(build_prompt(transcript, laughs, duration, max_clips))
            if raw is not None:
                moments = clean_model_moments(raw, transcript, duration, max_clips)
                log(f"🤖 {provider}: {len(moments)} momen lucu")
                if moments:
                    return moments, laughs
            else:
                log(f"ℹ️ Model dilewati ({provider}), pakai deteksi tawa saja")
        except Exception as e:
            log(f"⚠️ Model gagal ({type(e).__name__}: {str(e)[:160]}), pakai deteksi tawa saja")
    return heuristic_moments(laughs, transcript, duration, max_clips), laughs
