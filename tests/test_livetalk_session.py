import json, threading, time, uuid
import pytest
from nexus.livetalk import engines, protocol
from nexus.livetalk.server import Session, audio_frame

SPEECH = (b"\x10\x27" * 512)   # Amplitude 10000 -> Fake-VAD: Sprache
SILENCE = (b"\x00\x00" * 512)


class FakeVAD:
    def process(self, w):
        return 0.95 if abs(float(w[0])) > 0.1 else 0.01


def mk(reply_tokens=("Hallo Eddie, schön dich zu hören. ", "Wie geht es dir heute?"), delay=0.0, authed=True,
       stt_text="Hallo NEXUS"):
    sent, bins, seen = [], [], {}

    def reply(agent, mode, text, history, call_id, meta):
        seen.setdefault("history", []).append(list(history))
        meta["model"] = "fake"
        for t in reply_tokens:
            if delay:
                time.sleep(delay)
            yield t

    cfg = dict(engines.DEFAULTS)
    s = Session("ct" + uuid.uuid4().hex[:10], lambda raw: sent.append(json.loads(raw)), bins.append, authed=authed, cfg=cfg,
                vad=FakeVAD(), stt=lambda pcm: {"text": stt_text, "stt_ms": 5},
                synth=lambda text: iter([(22050, b"\x01\x00" * 2205)]), reply=reply,
                route=lambda t: ("NEXUS", "talk"), warm=False)
    return s, sent, bins, seen


def feed(s, chunk, n):
    for i in range(n):
        s.on_audio(protocol.pack_audio(i, chunk))


def wait_for(cond, t=5.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def types(sent):
    return [m["type"] for m in sent]


def test_full_turn_and_context():
    s, sent, bins, seen = mk()
    s.on_message(json.dumps({"type": "start"}))
    assert "ready" in types(sent)
    feed(s, SPEECH, 20); feed(s, SILENCE, 25)
    assert wait_for(lambda: "tts_end" in types(sent))
    t = types(sent)
    assert t.index("transcript") < t.index("agent") < t.index("tts_chunk") < t.index("tts_end") < t.index("metrics")
    assert bins and bins[0][:1] == b"A"
    met = [m for m in sent if m["type"] == "metrics"][0]
    assert met["first_audio_ms"] is not None and met["sentences"] == 2
    assert met.get("stt_spec") is True  # Spracherkennung lief schon in der Sprechpause
    assert s.history[0] == {"role": "user", "content": "Hallo NEXUS"}
    assert s.history[1]["agent"] == "NEXUS"
    # zweiter Zug bekommt den Kontext des ersten
    s.on_message(json.dumps({"type": "playback", "state": "ended", "turn": 1}))
    s.on_tick()
    assert s.ctrl.state == "listening"
    feed(s, SPEECH, 20); feed(s, SILENCE, 25)
    assert wait_for(lambda: types(sent).count("tts_end") == 2)
    assert len(seen["history"][1]) == 2


def test_barge_in_stops_turn():
    s, sent, bins, seen = mk(reply_tokens=["Satz Nummer %d ist da. " % i for i in range(40)], delay=0.05)
    s.on_message(json.dumps({"type": "start"}))
    feed(s, SPEECH, 20); feed(s, SILENCE, 25)
    assert wait_for(lambda: s.ctrl.state == "speaking")
    feed(s, SPEECH, 15)   # Eddie redet rein (>= 300 ms)
    assert "barge_in" in types(sent)
    assert wait_for(lambda: "tts_end" in types(sent))
    end = [m for m in sent if m["type"] == "tts_end"][0]
    assert end["interrupted"] is True
    assert s.ctrl.state == "listening"
    met = [m for m in sent if m["type"] == "metrics"][0]
    assert met["sentences"] < 40


def test_short_noise_no_turn():
    s, sent, bins, seen = mk()
    s.on_message(json.dumps({"type": "start"}))
    feed(s, SPEECH, 3); feed(s, SILENCE, 30)  # ~96 ms -> unter min_speech_ms
    time.sleep(0.2)
    assert "transcript" not in types(sent)


def test_auth_required():
    s, sent, bins, seen = mk(authed=False)
    with pytest.raises(PermissionError):
        s.on_message(json.dumps({"type": "start", "token": "falsch"}))
    assert sent[-1]["error"] == "AUTH_REQUIRED"
    feed(s, SPEECH, 30)
    assert "transcript" not in types(sent)


def test_text_turn_and_stop():
    s, sent, bins, seen = mk()
    s.on_message(json.dumps({"type": "start"}))
    s.on_message(json.dumps({"type": "text", "text": "Nur Text"}))
    assert wait_for(lambda: "tts_end" in types(sent))
    s.on_message(json.dumps({"type": "stop"}))
    assert s.ctrl.state == "idle" and not s.active


def test_audio_frame_header():
    f = audio_frame(3, 7, 22050, b"\x01\x02")
    assert f[:1] == b"A" and int.from_bytes(f[1:5], "big") == 3 and int.from_bytes(f[9:13], "big") == 22050


def test_first_clause_split():
    from nexus.livetalk.server import first_clause_split
    out = list(first_clause_split(iter(["Ein Lieblingswort ist einfach ein Wort, das einem besonders gut gefällt.",
                                        "Zweiter Satz, der bleibt ganz und gar so."]), 40))
    assert out == ["Ein Lieblingswort ist einfach ein Wort,", "das einem besonders gut gefällt.",
                   "Zweiter Satz, der bleibt ganz und gar so."]
    assert list(first_clause_split(iter(["Kurz, gut."]), 40)) == ["Kurz, gut."]


def test_barge_in_after_generation_finished():
    """Barge-in auch dann, wenn die Antwort schon fertig erzeugt ist und nur noch abgespielt wird."""
    s, sent, bins, seen = mk()
    s.on_message(json.dumps({"type": "start"}))
    feed(s, SPEECH, 20); feed(s, SILENCE, 25)
    assert wait_for(lambda: "tts_end" in types(sent))
    assert s.ctrl.state == "speaking"  # Handy spielt noch ab (kein playback ended)
    feed(s, SPEECH, 15)
    assert "barge_in" in types(sent)
    assert s.ctrl.state == "listening"


def test_no_barge_during_stt():
    """Sprache waehrend der Erkennung (Echo) startet keinen neuen Zug."""
    import json, time
    released = threading.Event()
    def slow_stt(pcm):
        released.wait(0.8)
        return {"text": "Hallo NEXUS", "stt_ms": 400, "confident": True}
    sent, bins = [], []
    cfg = dict(engines.DEFAULTS)
    sess = Session("ct" + uuid.uuid4().hex[:10], lambda raw: sent.append(json.loads(raw)), bins.append,
                   authed=True, cfg=cfg, vad=FakeVAD(), stt=slow_stt,
                   synth=lambda text: iter([(22050, b"\x01\x00" * 2205)]),
                   reply=lambda *a: iter(["ok "]),
                   route=lambda t: ("NEXUS", "talk"), warm=False)
    sess.on_message(json.dumps({"type": "start"}))
    feed(sess, SPEECH, 20); feed(sess, SILENCE, 25)
    assert wait_for(lambda: sess.ctrl.state == "thinking" or sess._turn_busy, t=2.0)
    # Echo waehrend STT
    feed(sess, SPEECH, 20)
    time.sleep(0.15)
    assert "barge_in" not in types(sent)
    released.set()
    assert wait_for(lambda: "tts_end" in types(sent), t=3.0)
    met = [m for m in sent if m["type"] == "metrics"]
    assert met and met[0].get("interrupted") != "during_stt"
