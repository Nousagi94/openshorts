"""Tests for the funny-moment pipeline (funny/): detection, picking, captions, edit graph."""
import shutil
import subprocess

import numpy as np
import pytest

from funny import captions, effects, laughter, moments, pipeline, synth


def _seg(start, end, text):
    words, t = [], start
    parts = text.split()
    step = (end - start) / len(parts)
    for p in parts:
        words.append({"word": " " + p, "start": round(t, 2), "end": round(t + step * 0.9, 2)})
        t += step
    return {"start": start, "end": end, "text": " " + text, "words": words}


TRANSCRIPT = {"language": "id", "segments": [
    _seg(0.0, 5.0, "Halo guys balik lagi di podcast"),
    _seg(5.5, 12.0, "Jadi kemarin gue beli nasi goreng pedes"),
    _seg(12.5, 18.0, "Abangnya masukin cabe sepuluh biji"),
    _seg(18.5, 21.0, "Anjir gue nangis wkwkwk"),
    _seg(25.0, 30.0, "Oke lanjut bahas kerjaan"),
    _seg(30.5, 40.0, "Kantor lagi sibuk banget minggu ini"),
]}


# --- laughter ---------------------------------------------------------------

def _babble(seconds, rng):
    """Speech-like signal: voiced syllables with small gaps."""
    sr = laughter.SAMPLE_RATE
    t = np.arange(int(sr * seconds)) / sr
    f0 = 140 + 20 * np.sin(2 * np.pi * 0.3 * t)
    voiced = sum(np.sin(2 * np.pi * k * np.cumsum(f0) / sr) / k for k in range(1, 6))
    syll = 0.55 + 0.45 * np.clip(np.sin(2 * np.pi * 3.0 * t + rng.random()), 0, 1)
    return 0.3 * voiced * syll


def _laugh(seconds, rng):
    sr = laughter.SAMPLE_RATE
    t = np.arange(int(sr * seconds)) / sr
    voiced = sum(np.sin(2 * np.pi * k * 260 * t) / k for k in range(1, 8))
    bursts = np.clip(np.sin(2 * np.pi * 5.2 * t), 0, 1) ** 1.5
    return (0.6 * voiced + 0.5 * rng.standard_normal(len(t))) * bursts


def test_laugh_after_talk_is_the_peak():
    rng = np.random.default_rng(0)
    audio = np.concatenate([_babble(20, rng), _laugh(3, rng), _babble(15, rng)])
    curve = laughter.score_curve(audio.astype(np.float32))
    events = laughter.peaks(curve)
    assert events, "the laugh should be detected"
    best = max(events, key=lambda e: e.score)
    assert 19.0 <= best.peak <= 24.0
    assert curve[5:15].max() < best.score


def test_silence_has_no_events():
    curve = laughter.score_curve(np.zeros(laughter.SAMPLE_RATE * 10, dtype=np.float32))
    assert laughter.peaks(curve) == []


# --- moments ----------------------------------------------------------------

def test_transcript_cues_find_written_laughter():
    tr = {"segments": [_seg(0, 2, "biasa aja"), _seg(3, 5, "wkwkwk parah"),
                       _seg(6, 8, "(tertawa)"), _seg(9, 11, "Hahaha iya")]}
    assert moments.transcript_cues(tr) == [3.0, 6.0, 9.0]


def test_cue_near_audio_laugh_boosts_it():
    marks = moments.merge_laughs([{"peak": 22.0, "score": 0.5}], [21.0, 35.0])
    assert marks[0] == {"t": 22.0, "score": 0.75}
    assert marks[1]["t"] == 35.0


def test_heuristic_clip_holds_setup_punch_and_stops_before_next_topic():
    found = moments.heuristic_moments([{"t": 22.5, "score": 0.9}], TRANSCRIPT, 40.0)
    assert len(found) == 1
    m = found[0]
    assert m["start"] <= 5.5            # the set-up is in
    assert m["end"] <= 25.5             # "Oke lanjut" is not
    assert 18.5 <= m["punchlines"][0] <= 21.0
    assert moments.MIN_CLIP_S <= m["end"] - m["start"] <= moments.MAX_CLIP_S


def test_dedupe_keeps_best_of_overlapping():
    a = {"start": 0, "end": 30, "funny_score": 50}
    b = {"start": 5, "end": 35, "funny_score": 90}
    c = {"start": 40, "end": 60, "funny_score": 10}
    assert moments.dedupe([a, b, c], 5) == [b, c]


def test_prompt_marks_laughter_in_transcript():
    prompt = moments.build_prompt(TRANSCRIPT, [{"t": 22.0, "score": 0.8}], 40.0)
    assert "[TAWA 0.80]" in prompt
    assert prompt.index("Anjir gue nangis") < prompt.index("[TAWA 0.80]")


def test_model_output_is_cleaned():
    raw = {"moments": [
        {"start": 6.0, "end": 20.5, "punchlines": [20.0, 99.0], "funny_score": 88,
         "title": "Cabe sepuluh 🤣", "caption": "x", "hashtags": ["lucu", "#fyp"],
         "meme_text": "gak kuat"},
        {"start": 30.0, "end": 20.0, "punchlines": [], "funny_score": 50,
         "title": "bad", "caption": "", "hashtags": []},
        {"start": "nope"},
    ]}
    out = moments.clean_model_moments(raw, TRANSCRIPT, 40.0, 5)
    assert len(out) == 1
    m = out[0]
    assert m["start"] == 5.5 and m["end"] == 21.0  # snapped to sentences
    assert m["punchlines"] == [20.0]
    assert m["hashtags"] == ["#lucu", "#fyp"]
    assert m["meme_text"] == "GAK KUAT"


def test_find_moments_falls_back_when_model_fails(monkeypatch):
    def boom(prompt):
        raise RuntimeError("503 UNAVAILABLE")
    monkeypatch.setattr(moments, "_call_model", boom)
    found, laughs = moments.find_moments(TRANSCRIPT, [{"peak": 22.5, "score": 0.9}], 40.0,
                                         log=lambda *_: None)
    assert found and found[0]["source"] == "heuristic"


def test_find_moments_uses_model(monkeypatch):
    monkeypatch.setattr(moments, "_call_model", lambda p: ({"moments": [
        {"start": 5.5, "end": 21.0, "punchlines": [19.0], "funny_score": 90,
         "title": "t", "caption": "c", "hashtags": [], "meme_text": "WKWK"}]}, "fake"))
    found, _ = moments.find_moments(TRANSCRIPT, [], 40.0, log=lambda *_: None)
    assert found[0]["source"] == "model" and found[0]["funny_score"] == 90


# --- captions ---------------------------------------------------------------

def test_emphasis_words():
    assert captions.is_emphasis("Anjir,")
    assert captions.is_emphasis("wkwkwk")
    assert captions.is_emphasis("Hahaha")
    assert not captions.is_emphasis("nasi")


def test_emphasis_word_gets_its_own_block():
    words = captions.clip_words(TRANSCRIPT, 18.5, 21.0)
    texts = [[w["word"] for w in b] for b in captions.blocks(words, max_words=3)]
    assert ["Anjir"] in texts and ["wkwkwk"] in texts


def test_streamer_style_is_one_blue_word_at_a_time():
    words = captions.clip_words(TRANSCRIPT, 12.5, 21.0)
    doc = captions.build_ass(words, style="streamer", context="Saya: beli nasgor")
    events = [l for l in doc.splitlines() if l.startswith("Dialogue: 0,")]
    assert len(events) == len(words)
    assert "Style: Funny,Bangers" in doc and captions.BLUE in doc
    assert "Saya:\\Nbeli nasgor" in doc


def test_censor():
    assert captions.censor("goblok") == "gobl*k"
    assert captions.censor("Anjing!") == "Anj*ng!"
    assert captions.censor("nasi") == "nasi"
    assert captions._display("goblok,") == "GOBL*K"


def test_ass_document():
    words = captions.clip_words(TRANSCRIPT, 12.5, 21.0)
    doc = captions.build_ass(words, memes=[(3.0, "gak kuat")], style="meme")
    assert "PlayResX: 1080" in doc and "Style: Funny,Anton" in doc
    assert captions.RED in doc and captions.YELLOW in doc
    assert "GAK KUAT" in doc
    assert words[0]["start"] == 0.0  # re-timed to the clip


def test_trailing_period_dropped_question_kept():
    assert captions._display("goreng.") == "GORENG"
    assert captions._display("nggak?") == "NGGAK?"


# --- effects ----------------------------------------------------------------

def test_plan_keeps_punchlines_inside_and_apart():
    m = {"punchlines": [10.2, 10.5, 15.0, 100.0], "meme_text": "WKWK"}
    plan = effects.plan(m, 10.0, 30.0)
    assert plan.punchlines == [0.5, 5.0]
    assert ("whoosh" in [n for _, n in plan.sfx]) and plan.memes[0][1] == "WKWK"
    assert effects.plan(m, 10.0, 30.0, effects=False).sfx == []


def test_build_command_wires_every_input():
    plan = effects.plan({"punchlines": [5.0], "meme_text": "X"}, 0.0, 20.0)
    cmd = effects.build_command("in.mp4", 0.0, 20.0, "out.mp4", plan, (1920, 1080),
                                ass_path="/tmp/a.ass", sfx_paths={"whoosh": "w.wav", "boom": "b.wav"},
                                music_path="m.mp3")
    assert cmd.count("-i") == 4
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "zoompan" in graph and "sidechaincompress" in graph and "amix=inputs=4" in graph


def test_stream_layout_takes_over_at_punchline():
    plan = effects.plan({"punchlines": [5.0]}, 0.0, 20.0, layout="stream")
    assert plan.takeover and not plan.zoom
    graph = effects.reframe_filters(1920, 1080, "stream", facecam=(1400, 600, 480, 440),
                                    takeover=effects.takeover_enable(plan))
    assert "vstack" in graph and "crop=480:440:1400:600" in graph
    assert "enable='between(t,5.000," in graph


def test_parse_box():
    assert effects.parse_box("0.75,0.5,0.25,0.5", 1920, 1080) == (1440, 540, 480, 540)
    assert effects.parse_box("1400,600,480,440", 1920, 1080) == (1400, 600, 480, 440)
    with pytest.raises(ValueError):
        effects.parse_box("1,2,3", 1920, 1080)


def test_parse_time():
    assert pipeline.parse_time("90") == 90
    assert pipeline.parse_time("1:30") == 90
    assert pipeline.parse_time("1:02:03") == 3723
    assert pipeline.parse_time(None) is None


def test_synth_sounds(tmp_path):
    for name in synth.GENERATORS:
        path = synth.ensure(name, str(tmp_path))
        assert (tmp_path / f"{name}.wav").stat().st_size > 1000, path


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_render_end_to_end(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=220", "-t", "8", "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    tr = {"segments": [_seg(0.5, 4.0, "Anjir lucu banget sumpah")]}
    m = {"start": 0.0, "end": 6.0, "punchlines": [3.0], "meme_text": "WKWK"}
    out = tmp_path / "out.mp4"
    for opts in ({}, {"style": "meme", "facecam_box": (400, 200, 200, 140)}):
        pipeline.render_clip(str(src), (640, 360, 8.0), m, tr, str(out), str(tmp_path / "c"),
                             opts, log=lambda *_: None)
        w, h, dur = pipeline.probe(str(out))
        assert (w, h) == (1080, 1920) and 5.5 < dur < 6.5


def test_snap_skips_a_sentence_that_is_ending():
    assert moments.snap_window(4.8, 21.0, TRANSCRIPT, 40.0)[0] == 5.5
    assert moments.snap_window(3.0, 21.0, TRANSCRIPT, 40.0)[0] == 0.0


# --- web app ----------------------------------------------------------------

def test_facecam_spec_corners():
    from funny import webapp
    assert webapp.facecam_spec("kanan-bawah", "sedang") == "0.750,0.600,0.250,0.400"
    assert webapp.facecam_spec("kiri-atas", "kecil") == "0.000,0.000,0.200,0.300"


def test_options_from_form():
    from funny import webapp
    opts, kw = webapp.options_from_form({"layout": "stream", "corner": "kiri-bawah",
                                         "style": "meme", "max_clips": 50, "dari": "1:00",
                                         "sampai": "", "music": False})
    assert opts["facecam"].startswith("0.000,0.600") and opts["style"] == "meme"
    assert opts["music"] is False and kw["max_clips"] == 10
    assert kw["start"] == 60 and kw["end"] is None


def test_transcription_progress_is_read_from_tqdm_and_whisper_lines(monkeypatch):
    import io
    import sys
    import types
    seen, logged = [], []

    def fake_media(path, language=None):
        sys.stderr.write("\r 37%|####      | 4412/11941 [01:02<02:00]")
        print("🎙️ Transcribing… 75% (12s)")
        print("⚠️ [ASR] mlx-whisper failed (ImportError) — falling back to whisper")
        return {"segments": []}
    fake = types.SimpleNamespace(transcribe_media=fake_media, release_models=lambda: None)
    monkeypatch.setitem(sys.modules, "transcribe_backends", fake)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    pipeline.transcribe("x.mp4", log=logged.append,
                        progress=lambda stage, f=None, *a: seen.append((stage, f)))
    assert ("transkripsi", 0.37) in seen and ("transkripsi", 0.75) in seen
    assert any("[ASR]" in line for line in logged)


def test_long_transcript_goes_to_the_model_in_pieces(monkeypatch):
    monkeypatch.setenv("FUNNY_CHUNK_CHARS", "120")
    pieces = moments.transcript_chunks(TRANSCRIPT, 120)
    assert len(pieces) > 1
    assert pieces[0]["segments"][-1] is pieces[1]["segments"][0]  # shared boundary
    calls = []

    def fake(prompt):
        calls.append(prompt)
        return {"moments": [{"start": 5.5, "end": 21.0, "punchlines": [19.0],
                             "funny_score": 60 + len(calls), "title": "t", "caption": "c",
                             "hashtags": [], "meme_text": ""}]}, "fake"
    monkeypatch.setattr(moments, "_call_model", fake)
    seen = []
    found, _ = moments.find_moments(TRANSCRIPT, [], 40.0, log=lambda *_: None,
                                    progress=seen.append)
    assert len(calls) == len(pieces) and seen[-1] == 1.0
    assert len(found) == 1  # the same joke from two pieces collapses to one


def test_software_encoder_where_no_hardware_one_is_listed(monkeypatch):
    monkeypatch.setattr(effects.sys, "platform", "linux")
    effects.hardware_encoder.cache_clear()
    try:
        assert effects.video_encoder_args(20, "veryfast")[:2] == ["-c:v", "libx264"]
    finally:
        effects.hardware_encoder.cache_clear()


# --- subtitle editor ----------------------------------------------------------

def test_edited_lines_keep_timing_or_share_the_line():
    words = captions.clip_words(TRANSCRIPT, 12.5, 40.0)
    lines = captions.edit_blocks(words)
    assert " ".join(l["text"] for l in lines).split() == [w["word"] for w in words]
    assert all(len(l["words"]) <= 8 for l in lines) and len(lines) >= 3
    first, second = lines[0], lines[1]
    fixed = captions.apply_edits([dict(first, text=first["text"].replace("Abangnya", "Abangnye")),
                                  dict(second, text="gue nangis beneran sumpah wkwk"),
                                  dict(lines[2], text="  ")])
    n = len(first["words"])
    assert fixed[0]["word"] == "Abangnye" and fixed[0]["start"] == first["words"][0]["start"]
    assert [w["end"] for w in fixed[:n]] == [w["end"] for w in first["words"]]
    rest = fixed[n:]
    assert [w["word"] for w in rest] == ["gue", "nangis", "beneran", "sumpah", "wkwk"]
    assert rest[0]["start"] == pytest.approx(second["start"])
    assert all(a["end"] == pytest.approx(b["start"]) for a, b in zip(rest, rest[1:]))
    assert rest[-1]["end"] >= second["end"] - 1e-6


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_save_edits_rerenders_with_the_corrected_words(tmp_path):
    import json
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=220", "-t", "8", "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src)], check=True)
    tr = {"segments": [_seg(0.5, 4.0, "Anjir lucu banget sumpah")]}
    clip = {"start": 0.0, "end": 6.0, "punchlines": [3.0], "file": "funny_01.mp4",
            "title": "t", "caption": "c", "hashtags": ["#a"], "context_text": "Saya: tes",
            "funny_score": 8}
    (tmp_path / "transcript.json").write_text(json.dumps(tr))
    (tmp_path / "funny_clips.json").write_text(json.dumps({"source": "src.mp4", "clips": [clip]}))
    (tmp_path / "render.json").write_text(json.dumps(
        {"source": str(src), "size": [640, 360], "options": {"keep_ass": True}}))
    (tmp_path / "funny_01.mp4").write_bytes(b"old")

    d = pipeline.caption_lines(str(tmp_path), 1)
    assert d["context"] == "Saya: tes" and d["lines"]
    texts = [l["text"] for l in d["lines"]]
    assert not pipeline.save_edits(str(tmp_path), 1, texts, post="judul baru")
    assert (tmp_path / "funny_01.txt").read_text() == "judul baru\n"

    texts[0] = texts[0].replace("Anjir", "Anjay")
    assert pipeline.save_edits(str(tmp_path), 1, texts, "Saya: diedit", log=lambda *_: None)
    w, h, dur = pipeline.probe(str(tmp_path / "funny_01.mp4"))
    assert (w, h) == (1080, 1920) and not (tmp_path / "funny_01.new.mp4").exists()
    assert "ANJAY" in (tmp_path / "funny_01.new.ass").read_text()
    again = pipeline.caption_lines(str(tmp_path), 1)
    assert again["lines"][0]["text"].startswith("Anjay") and again["context"] == "Saya: diedit"
    with pytest.raises(ValueError):
        pipeline.save_edits(str(tmp_path), 1, texts[:-1] if len(texts) > 1 else texts + ["x"])
