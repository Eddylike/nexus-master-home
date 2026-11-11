from nexus.livetalk.pipeline import run_turn

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
