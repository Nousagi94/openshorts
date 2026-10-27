"""TikTok-style word-by-word captions for funny clips, written as ASS.

Two looks, picked with ``style``:

* ``streamer`` (default): the Indonesian stream-clip look. One word at a time,
  comic font (Bangers) in blue with a thick white outline, sitting on the seam
  of a split layout. Swear words are censored with ``*`` (GOBL*K) so the clip
  survives TikTok's filters while still reading as the word.
* ``meme``: 1-3 words in heavy caps (Anton) with a black outline, the spoken
  word yellow with a quick pop and the joke words ("WKWK", "ANJIR", "NGAKAK")
  red, bigger and tilted, at ~70% of the height.

Both can add a context line ("Saya: Mas Rian nge-whatsapp") bottom-left for
the first seconds, and meme text that pops in at the punchline.

Rendered in a 1080x1920 PlayRes so sizes are real pixels on a 9:16 export.
"""
from __future__ import annotations

import re
from typing import List

PLAY_W, PLAY_H = 1080, 1920
MEME_SIZE = 150
MEME_Y = 360
MEME_S = 1.8
CONTEXT_S = 5.0

WHITE = "&H00FFFFFF"
BLACK = "&H00000000"
YELLOW = "&H0000E5FF"   # ASS is &HAABBGGRR -> #FFE500
RED = "&H003C3CFF"      # #FF3C3C
BLUE = "&H00FF7B2F"     # #2F7BFF

STYLES = {
    "streamer": {"font": "Bangers", "size": 92, "emph_size": 104, "pos_y": 790,
                 "max_words": 1, "max_chars": 14, "max_s": 0.9, "outline": 7, "shadow": 0,
                 "color": BLUE, "active": BLUE, "emph": BLUE, "outline_color": WHITE,
                 "spacing": 3, "pop": True, "tilt": False},
    "meme": {"font": "Anton", "size": 96, "emph_size": 118, "pos_y": 1340,
             "max_words": 3, "max_chars": 16, "max_s": 1.3, "outline": 7, "shadow": 4,
             "color": WHITE, "active": YELLOW, "emph": RED, "outline_color": BLACK,
             "spacing": 2, "pop": True, "tilt": True},
}
DEFAULT_STYLE = "streamer"

# Words that are the joke in Indonesian chat/streams. Matched on the cleaned,
# lowercased word; "wkwk"-style laughs match by pattern.
EMPHASIS_WORDS = {
    "anjir", "anjay", "anjrit", "buset", "busett", "astaga", "astagfirullah",
    "ngakak", "kocak", "gila", "gokil", "parah", "sumpah", "lho", "loh", "hah",
    "waduh", "wadidaw", "bjir", "bjirr", "njir", "cuy", "anjim", "kampret",
    "receh", "goblok", "gblk", "bego", "mampus", "aduh", "dih", "idih",
    "anying", "jir", "woy", "woi", "ampun", "yaampun", "omg", "wow", "lah",
}
# Censored on screen: first and last letters stay, one inner vowel becomes *.
CENSOR_WORDS = {
    "goblok", "goblog", "anjing", "bangsat", "babi", "kontol", "memek", "tai",
    "bajingan", "brengsek", "tolol", "kampret", "asu", "jancok", "jancuk", "ngentot",
}
_LAUGH = re.compile(r"^(w+k+[wk]*|k+w+[kw]*|(h[aeiu]){2,}h?|ha+|he+)$")
_CLEAN = re.compile(r"[^\w]+", re.UNICODE)


def _key(word):
    return _CLEAN.sub("", word.lower())


def is_emphasis(word: str) -> bool:
    w = _key(word)
    return bool(w) and (w in EMPHASIS_WORDS or w in CENSOR_WORDS or bool(_LAUGH.match(w)))


def censor(word: str) -> str:
    """``goblok`` -> ``gobl*k``: still readable, not a match for word filters."""
    if _key(word) not in CENSOR_WORDS:
        return word
    letters = [i for i, c in enumerate(word) if c.isalpha()]
    inner = [i for i in letters[1:-1] if word[i].lower() in "aiueo"] or letters[1:-1]
    if not inner:
        return word
    i = inner[-1]
    return word[:i] + "*" + word[i + 1:]


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _display(word):
    """Caption text for a word: caps, censored, no trailing . or , (keep ? and !)."""
    up = censor(word).upper()
    return _escape(up.rstrip(".,;:") or up)


def clip_words(transcript, clip_start: float, clip_end: float):
    """Words inside the clip, re-timed to clip time."""
    words = []
    for seg in (transcript or {}).get("segments", []):
        for w in seg.get("words") or []:
            s, e = float(w["start"]), float(w["end"])
            if e <= clip_start or s >= clip_end:
                continue
            text = w.get("word", "").strip()
            if not text:
                continue
            words.append({"word": text, "start": max(0.0, s - clip_start),
                          "end": min(clip_end, e) - clip_start})
    return words


def blocks(words, max_words=3, max_chars=16, max_s=1.3):
    """Group words into on-screen blocks. An emphasis word gets its own block."""
    out: List[list] = []
    cur: list = []
    for w in words:
        emph = is_emphasis(w["word"])
        text_len = sum(len(x["word"]) + 1 for x in cur) + len(w["word"])
        if cur and (emph or is_emphasis(cur[-1]["word"]) or len(cur) >= max_words
                    or text_len > max_chars or w["end"] - cur[0]["start"] > max_s
                    or w["start"] - cur[-1]["end"] > 0.6
                    or cur[-1]["word"].endswith((".", "?", "!"))):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def _word_markup(word, active, st):
    text = _display(word["word"])
    if is_emphasis(word["word"]):
        tilt = "\\frz-4" if active and st["tilt"] else ""
        pop = ("\\t(0,90,\\fscx125\\fscy125)\\t(90,180,\\fscx100\\fscy100)"
               if active and st["pop"] else "")
        return f"{{\\c{st['emph']}\\fs{st['emph_size']}{tilt}{pop}}}{text}{{\\r}}"
    if active:
        pop = "\\fscx112\\fscy112\\t(0,120,\\fscx100\\fscy100)" if st["pop"] else ""
        return f"{{\\c{st['active']}{pop}}}{text}{{\\r}}"
    return f"{{\\c{st['color']}}}{text}"


def build_ass(words, memes=(), style=DEFAULT_STYLE, context=None) -> str:
    """Full ASS document for already re-timed ``words``.

    ``memes``: ``(time, text)`` pairs in clip time; each pops in at the top of
    the frame for ``MEME_S`` seconds, tilted, like a meme-page overlay.
    ``context``: one line shown bottom-left for the first ``CONTEXT_S`` seconds.
    """
    st = STYLES.get(style, STYLES[DEFAULT_STYLE])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_W}
PlayResY: {PLAY_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Funny,{st['font']},{st['size']},{st['color']},{st['color']},{st['outline_color']},&H96000000,0,0,0,0,100,100,{st['spacing']},0,1,{st['outline']},{st['shadow']},5,60,60,0,1
Style: Meme,Anton,{MEME_SIZE},{YELLOW},{YELLOW},{BLACK},&H96000000,0,0,0,0,100,100,3,0,1,9,6,5,60,60,0,1
Style: Context,Montserrat ExtraBold,46,{WHITE},{WHITE},{BLACK},&H96000000,0,0,0,0,100,100,0,0,1,4,2,1,40,40,60,1
"""
    header += "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    lines = []
    for blk in blocks(words, st["max_words"], st["max_chars"], st["max_s"]):
        for i, w in enumerate(blk):
            start = w["start"] if i else blk[0]["start"]
            end = blk[i + 1]["start"] if i + 1 < len(blk) else max(w["end"], start + 0.25)
            if end <= start:
                continue
            body = " ".join(_word_markup(x, x is w, st) for x in blk)
            pos = f"{{\\pos({PLAY_W // 2},{st['pos_y']})}}"
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Funny,,0,0,0,,{pos}{body}")
    for t, text in memes:
        if not text:
            continue
        pop = ("{\\fscx40\\fscy40\\frz-8\\t(0,140,\\fscx118\\fscy118)"
               "\\t(140,260,\\fscx100\\fscy100)}")
        lines.append(f"Dialogue: 1,{_ass_time(t)},{_ass_time(t + MEME_S)},Meme,,0,0,0,,"
                     f"{{\\pos({PLAY_W // 2},{MEME_Y})}}{pop}{_escape(text.upper())}")
    if context:
        text = _escape(context).replace(": ", ":\\N", 1)
        lines.append(f"Dialogue: 2,{_ass_time(0)},{_ass_time(CONTEXT_S)},Context,,0,0,0,,"
                     f"{{\\fad(150,300)}}{text}")
    return header + "\n".join(lines) + "\n"


def write_ass(transcript, clip_start, clip_end, path, memes=(), style=DEFAULT_STYLE,
              context=None, words=None) -> bool:
    """Write the clip's captions (and overlays); False when there is nothing to show.

    ``words`` (clip time) replaces the transcript's words, e.g. after the
    creator corrected the subtitles by hand.
    """
    if words is None:
        words = clip_words(transcript, clip_start, clip_end)
    if not words and not memes and not context:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_ass(words, memes, style, context))
    return True


def edit_blocks(words, max_words=8):
    """``words`` as editable lines (start, end, text, words): a phrase each,
    split at a sentence end, a pause or ``max_words``. Bigger than the
    on-screen blocks so a long clip is a short list to read through."""
    lines, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > 0.6
                    or cur[-1]["word"].endswith((".", "?", "!"))):
            lines.append(cur)
            cur = []
        cur.append(w)
    if cur:
        lines.append(cur)
    return [{"start": b[0]["start"], "end": b[-1]["end"],
             "text": " ".join(w["word"] for w in b), "words": b} for b in lines]


def apply_edits(lines):
    """Words back from edited lines (``start``, ``end``, ``text``).

    A line keeping its word count keeps each word's timing; otherwise its
    words share the line's time in proportion to their length. An emptied
    line drops its words.
    """
    out = []
    for line in lines:
        texts = str(line.get("text") or "").split()
        start, end = float(line["start"]), float(line["end"])
        old = line.get("words") or []
        if len(old) == len(texts):
            out += [{"word": t, "start": w["start"], "end": w["end"]} for t, w in zip(texts, old)]
            continue
        end = max(end, start + 0.25 * len(texts))
        total = sum(len(t) + 1 for t in texts) or 1
        t0 = start
        for t in texts:
            t1 = t0 + (end - start) * (len(t) + 1) / total
            out.append({"word": t, "start": round(t0, 3), "end": round(t1, 3)})
            t0 = t1
    return out
