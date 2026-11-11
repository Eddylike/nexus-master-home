import threading
import time

import pytest

from nexus.games.haus5t import api, engine, state as state_mod, voice


class FakeSynth:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
        self.lock = threading.Lock()

    def __call__(self, text, path):
        with self.lock:
            self.calls.append(str(text))
        if self.fail:
            return {"success": False, "error": "edge_tts_missing"}
        return {"success": True, "path": str(path), "voice": "fake"}


class FakePlayer:
    def __init__(self, delay=0.005):
        self.delay = delay
        self.played = []
        self.active = 0
        self.max_active = 0
        self.pause_calls = 0
        self.resume_calls = 0
        self.stop_calls = 0
        self.lock = threading.Lock()

    def play(self, path):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self.lock:
            self.active -= 1
            self.played.append(str(path))
        return True

    def pause(self):
        self.pause_calls += 1
        return True

    def resume(self):
        self.resume_calls += 1
        return True

    def stop(self):
        self.stop_calls += 1
        return True


def _queue(tmp_path, synth=None, player=None):
    return voice.SpeechQueue(
        state_path=tmp_path / "voice.json",
        synth=synth or FakeSynth(),
        player=player or FakePlayer(),
        text_dir=tmp_path / "tts",
    )


def _fake_ask(agent, prompt):
    scores = {"NEXUS": 8, "Hermes": 6, "NEXUS GPT": 7, "Aether": 9,
              "Mira": 5, "Bruno": 4, "Lina": 10}
    if "BEWERTUNG:" in prompt:
        return f"Spielwert: {scores[agent]}/10 - begruendet.", True, "test"
    return f"{agent} antwortet auf Deutsch.", True, "test"


def _run_session(st):
    steps = 0
    while st.get("status") != "finished" and steps < 200:
        engine.step(st, _fake_ask)
        steps += 1
    assert st["status"] == "finished"


def _expected_announcements(st):
    return [voice.build_announcement(entry) for entry in st["contributions"]]


def test_announcement_contains_name_door_and_exact_answer():
    entry = {
        "round": 2,
        "speaker": "Mira",
        "text": "Ich brauche Ruhe und einen klaren Platz.",
        "score": 5,
    }
    text = voice.build_announcement(entry)
    assert "Tür 1 von 5: Leiblichkeit" in text
    assert "Mira" in text
    assert "5 von 10" in text
    assert entry["text"] in text


def test_full_game_reads_every_stored_answer_once_in_order(tmp_path):
    synth, player = FakeSynth(), FakePlayer()
    q = _queue(tmp_path, synth, player)
    st = state_mod.new_state("SESSION-VOICE")
    q.reset(st["session_id"])

    steps = 0
    while st.get("status") != "finished" and steps < 200:
        engine.step(st, _fake_ask)
        q.announce_new(st)
        steps += 1
    assert st["status"] == "finished"
    assert q.wait_idle(20.0)

    expected = _expected_announcements(st)
    assert len(expected) == len(st["contributions"])
    assert synth.calls == expected
    for entry, announcement in zip(st["contributions"], expected):
        assert entry["text"] in announcement
        assert entry["speaker"] in announcement
    door_announcements = [a for a in expected if "Tür " in a]
    assert door_announcements and any("Tür 1 von 5" in a for a in door_announcements)
    assert any("Abstimmungsergebnis" in a for a in expected)
    assert any("Einzelbewertung" in a for a in expected)
    assert player.max_active == 1
    assert len(player.played) == len(expected)

    # Kein doppeltes Vorlesen bei erneutem Sync.
    q.announce_new(st)
    assert q.wait_idle(5.0)
    assert len(synth.calls) == len(expected)

    # Neustart/Fortsetzung: derselbe persistente Stand liest nichts erneut.
    synth2, player2 = FakeSynth(), FakePlayer()
    q2 = _queue(tmp_path, synth2, player2)
    assert q2.announce_new(st) == 0
    assert q2.wait_idle(5.0)
    assert synth2.calls == []


def test_summary_and_start_are_read_by_nexus(tmp_path):
    synth = FakeSynth()
    q = _queue(tmp_path, synth)
    st = state_mod.new_state("S-SUMMARY")
    q.reset(st["session_id"])
    q.announce_text("start:" + st["session_id"], voice.START_TEXT, kind="start")
    st["status"] = "finished"
    q.announce_text("summary:" + st["session_id"],
                    voice.SUMMARY_PREFIX + engine.summary(st)["text"], kind="summary")
    assert q.wait_idle(5.0)
    assert synth.calls[0] == voice.START_TEXT
    assert synth.calls[1].startswith(voice.SUMMARY_PREFIX)
    # Erneutes Ankuendigen der Zusammenfassung ist dedupliziert.
    q.announce_text("summary:" + st["session_id"],
                    voice.SUMMARY_PREFIX + engine.summary(st)["text"], kind="summary")
    assert q.wait_idle(2.0)
    assert len(synth.calls) == 2


def test_pause_resume_and_disable(tmp_path):
    synth, player = FakeSynth(), FakePlayer()
    q = _queue(tmp_path, synth, player)
    q.reset("S-PAUSE")

    q.pause()
    q.announce_text("a", "Erster Text.")
    time.sleep(0.15)
    assert synth.calls == []
    q.resume()
    assert q.wait_idle(5.0)
    assert synth.calls == ["Erster Text."]

    q.disable()
    q.announce_text("b", "Zweiter Text.")
    time.sleep(0.15)
    assert synth.calls == ["Erster Text."]

    q.enable()
    q.announce_text("b", "Zweiter Text.")
    assert q.wait_idle(2.0)
    assert synth.calls == ["Erster Text."]

    q.announce_text("c", "Dritter Text.")
    assert q.wait_idle(5.0)
    assert synth.calls == ["Erster Text.", "Dritter Text."]


def test_pause_touches_current_playback(tmp_path):
    started = threading.Event()
    release = threading.Event()

    class BlockingPlayer(FakePlayer):
        def play(self, path):
            started.set()
            release.wait(5.0)
            return super().play(path)

    player = BlockingPlayer()
    q = _queue(tmp_path, FakeSynth(), player)
    q.reset("S-CURRENT")
    q.announce_text("current", "Ein laufender Text.")
    assert started.wait(2.0)
    result = q.pause()
    assert result["current_paused"] is True and player.pause_calls == 1
    q.resume()
    assert player.resume_calls == 1
    release.set()
    assert q.wait_idle(5.0)


def test_missing_tts_does_not_block_or_repeat(tmp_path):
    synth = FakeSynth(fail=True)
    q = _queue(tmp_path, synth)
    st = state_mod.new_state("S-FAIL")
    q.reset(st["session_id"])
    state_mod.add_contribution(st, "Mira", "Antwort eins.", True, "test")
    q.announce_new(st)
    assert q.wait_idle(5.0)
    assert len(synth.calls) == 1
    assert q.status()["last_error"] == "edge_tts_missing"
    assert q.status()["spoken"] == 0

    q.announce_new(st)
    assert q.wait_idle(2.0)
    assert len(synth.calls) == 1


def test_existing_answers_are_not_read_retroactively(tmp_path):
    st = state_mod.new_state("S-EXISTING")
    state_mod.add_contribution(st, "Mira", "Alte Antwort.", True, "test")
    synth = FakeSynth()
    q = _queue(tmp_path, synth)
    assert q.announce_new(st) == 0
    assert q.wait_idle(2.0)
    assert synth.calls == []

    state_mod.add_contribution(st, "Bruno", "Neue Antwort.", True, "test")
    q.announce_new(st)
    assert q.wait_idle(5.0)
    assert len(synth.calls) == 1
    assert "Neue Antwort." in synth.calls[0]
    assert "Alte Antwort." not in synth.calls[0]


def test_api_full_session_with_voice_and_controls(tmp_path, monkeypatch):
    synth = FakeSynth()
    q = _queue(tmp_path, synth)
    monkeypatch.setattr(voice, "get_queue", lambda: q)
    monkeypatch.setattr(api, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(api, "PAUSE_PATH", tmp_path / "games" / "haus5t.pause")
    monkeypatch.setattr(api, "STOP_PATH", tmp_path / "games" / "haus5t.stop")
    monkeypatch.setattr(api, "GAMES_DIR", tmp_path / "games")
    monkeypatch.setattr(api.agents, "make_ask", lambda: _fake_ask)

    code, started = api.handle("POST", "/api/games/haus5t/start",
                               {"new": True, "session_id": "API-SESSION"})
    assert code == 200 and started["voice"]["enabled"] is True
    assert started["voice"]["queued"] >= 1

    for _ in range(20):
        code, payload = api.handle("POST", "/api/games/haus5t/step", {"count": 20})
        assert code == 200
        if payload["status"] == "finished":
            break
    assert q.wait_idle(30.0)

    code, payload = api.handle("GET", "/api/games/haus5t/state", {})
    assert code == 200 and payload["status"] == "finished"
    stored = state_mod.load_state(api.STATE_PATH)["contributions"]
    assert synth.calls[0] == voice.START_TEXT
    assert synth.calls[-1].startswith(voice.SUMMARY_PREFIX)
    contribution_announcements = synth.calls[1:-1]
    assert len(contribution_announcements) == len(stored)
    for entry, announcement in zip(stored, contribution_announcements):
        assert entry["text"] in announcement
        assert entry["speaker"] in announcement

    # API-Steuerung: pausieren/fortsetzen/abschalten.
    code, off = api.handle("POST", "/api/games/haus5t/voice", {"action": "off"})
    assert code == 200 and off["enabled"] is False
    code, on = api.handle("POST", "/api/games/haus5t/voice", {"action": "on"})
    assert code == 200 and on["enabled"] is True
    code, paused = api.handle("POST", "/api/games/haus5t/voice", {"action": "pause"})
    assert code == 200 and paused["paused"] is True
    code, resumed = api.handle("POST", "/api/games/haus5t/voice", {"action": "resume"})
    assert code == 200 and resumed["paused"] is False

