import pytest
from nexus.livetalk.sentences import split_stream
from nexus.livetalk.bargein import BargeInController
from nexus.livetalk.wakeword import WakeGate
from nexus.livetalk.pipeline import run_turn

def test_basic():
    toks = ["Hallo Eddie", ", wie geht", " es dir? ", "Mir geht es gut.", " Rest"]
    assert list(split_stream(toks)) == ["Hallo Eddie, wie geht es dir?", "Mir geht es gut.", "Rest"]

def test_abbrev_and_numbers():
    out = list(split_stream(["Das ist z. B. Version 3.5 von Qwen. Fertig ist es jetzt!"]))
    assert out == ["Das ist z. B. Version 3.5 von Qwen.", "Fertig ist es jetzt!"]

def test_short_merged_and_no_empty():
    out = list(split_stream(["Ja. ", "Das klappt sehr gut.", "  "]))
    assert out == ["Ja. Das klappt sehr gut."]

def test_stream_split_number():
    assert list(split_stream(["Version 3.", "5 ist da. Und weiter geht es"])) == ["Version 3.5 ist da.", "Und weiter geht es"]

def test_barge_in_while_speaking():
    b = BargeInController(min_speech_ms=90, frame_ms=30); b.set_state("speaking")
    assert [b.on_vad(True) for _ in range(3)] == [False, False, True]
    assert b.state == "listening"

def test_noise_resets():
    b = BargeInController(90, 30); b.set_state("speaking")
    for v in (True, True, False, True, True): assert b.on_vad(v) is False

def test_no_barge_when_idle_or_listening():
    b = BargeInController(30, 30)
    assert b.on_vad(True) is False
    b.set_state("listening"); assert b.on_vad(True) is False

def test_bad_state():
    with pytest.raises(ValueError): BargeInController().set_state("x")

W = lambda *ws, c=0.9: [{"word": w, "conf": c} for w in ws]

def test_positive_and_cooldown():
    g = WakeGate(0.5, cooldown_s=3)
    assert g.check(W("Hey", "Nexus", "wie"), True, 10.0)
    assert not g.check(W("hey", "nexus"), True, 11.0)
    assert g.check(W("hallo", "nexus"), True, 14.0)

def test_vad_gate_and_negatives():
    g = WakeGate(0.5)
    assert not g.check(W("hey", "nexus"), False, 1.0)
    assert not g.check(W("hey", "lexus"), True, 1.0)
    assert not g.check(W("nexus"), True, 1.0)
    assert WakeGate(0.5, allow_bare=True).check(W("nexus"), True, 1.0)

def test_sensitivity():
    assert not WakeGate(0.2).check(W("hey", "nexus", c=0.6), True, 1.0)
    assert WakeGate(0.6).check(W("hey", "nexus", c=0.6), True, 1.0)
    with pytest.raises(ValueError): WakeGate(1.5)

def _run(stt_text, tokens, cancel_after=None):
    sent, hist = [], [{"role": "assistant", "content": "Hi"}]
    n = {"i": 0}
    def cancelled():
        n["i"] += 1
        return cancel_after is not None and n["i"] > cancel_after
    t = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9] + [1.0] * 50)
    r = run_turn(b"pcm", hist, lambda pcm: stt_text, lambda m: iter(tokens),
                 lambda s: b"WAV:" + s.encode(), sent.append, cancelled, clock=lambda: next(t))
    return r, sent, hist

def test_full_turn():
    r, sent, hist = _run("Wie spaet ist es", ["Es ist ", "zwoelf Uhr jetzt.", " Noch was?"])
    assert r["ok"] and r["user"] == "Wie spaet ist es" and not r["interrupted"]
    chunks = [m for m in sent if m["type"] == "tts_chunk"]
    assert [c["text"] for c in chunks] == ["Es ist zwoelf Uhr jetzt.", "Noch was?"]
    assert chunks[0]["audio"] == b"WAV:Es ist zwoelf Uhr jetzt."
    assert sent[0] == {"type": "transcript", "text": "Wie spaet ist es"}
    assert isinstance(r["first_audio_ms"], int) and r["first_audio_ms"] >= 0
    assert hist[-2]["role"] == "user" and hist[-1]["role"] == "assistant"

def test_empty_stt():
    r, sent, hist = _run("  ", ["x"])
    assert r == {"ok": False, "reason": "empty"} and len(hist) == 1

def test_barge_in():
    r, sent, _ = _run("Erzaehl", ["Satz eins ist da. ", "Satz zwei ist da. ", "Drei."], cancel_after=1)
    assert r["interrupted"] and any(m["type"] == "barge_in" for m in sent)
    assert "Drei" not in r["reply"]
