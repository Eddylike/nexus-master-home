"""SZ-04: Sprecherwechsel, Max-Runden, Pause/Stop, Nutzer-Eingriff, Timeout-Teilantwort. Ollama gemockt."""
import time


def _e():
    from nexus.sprechzimmer import engine
    return engine


def _ses(parts=("NEXUS", "Hermes", "Aether")):
    return {"id": "t1", "participants": list(parts), "messages": [], "status": "aktiv"}


def fake_ask(agent, session):
    return f"{agent} sagt etwas"


def test_round_robin():
    e = _e()
    s = _ses()
    assert e.next_speaker(s) == "NEXUS"
    s["messages"].append({"speaker": "NEXUS", "text": "a"})
    assert e.next_speaker(s) == "Hermes"
    s["messages"].append({"speaker": "Eddie", "text": "frage"})
    assert e.next_speaker(s) == "Hermes"
    s["messages"].append({"speaker": "Aether", "text": "b"})
    assert e.next_speaker(s) == "NEXUS"


def test_moderator_pick():
    e = _e()
    s = _ses()
    assert e.next_speaker(s, moderator_pick="Aether") == "Aether"
    assert e.next_speaker(s, moderator_pick="Fremder") == "NEXUS"


def test_max_rounds_limit():
    e = _e()
    s = _ses()
    out = e.run_autonomous(s, fake_ask, max_rounds=4, timeout=5)
    assert len(out) == 4
    assert [m["speaker"] for m in out] == ["NEXUS", "Hermes", "Aether", "NEXUS"]
    assert len(s["messages"]) == 4


def test_pause_stops():
    e = _e()
    s = _ses()

    def ask(agent, session):
        e.pause(session)
        return "ok"
    out = e.run_autonomous(s, ask, max_rounds=6, timeout=5)
    assert len(out) == 1 and s["paused"] is True


def test_stop_flag():
    e = _e()
    out = e.run_autonomous(_ses(), fake_ask, max_rounds=6, timeout=5, stop_flag=lambda: True)
    assert out == []


def test_user_interrupt():
    e = _e()
    s = _ses()

    def ask(agent, session):
        if len(session["messages"]) == 0:
            e.user_say(session, "Stopp, ich habe eine Frage")
        return "antwort"
    out = e.run_autonomous(s, ask, max_rounds=6, timeout=5)
    assert len(out) == 1
    assert any(m["speaker"] == "Eddie" for m in s["messages"])
    assert s["interrupt"] is True


def test_timeout_partial_answer():
    e = _e()
    s = _ses(("Hermes",))

    def slow(agent, session):
        time.sleep(3)
        return "zu spaet"
    t0 = time.monotonic()
    out = e.run_autonomous(s, slow, max_rounds=1, timeout=0.5)
    assert time.monotonic() - t0 < 2.5
    assert len(out) == 1 and "[timeout]" in out[0]["text"]