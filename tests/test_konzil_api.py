"""Konzil-API: State, Pause/Resume, Intervention (Einwand) ohne echten Runner-Start."""
import json
import pytest
from nexus.games.konzil import api as A, engine as E


class FakeH:
    client_address = ("127.0.0.1", 0)

    def __init__(self):
        self.out = None

    def _json(self, code, payload):
        self.out = (code, payload)

    def _agent_guard(self):
        return (False, "denied")


@pytest.fixture
def paths(tmp_path, monkeypatch):
    for n in ("STATE", "PAUSE", "STOP", "INBOX", "PIDF"):
        monkeypatch.setattr(A, n, tmp_path / getattr(A, n).name)
    monkeypatch.setattr(A, "RUNNER", tmp_path / "fehlt.py")
    return tmp_path


def test_state_missing_and_present(paths):
    h = FakeH(); assert A.handle(h, "GET", "/api/konzil/state", None)
    assert h.out[1]["exists"] is False
    st = E.new_state("S1"); st["case"] = 1; E.save(st, A.STATE)
    A.handle(h, "GET", "/api/konzil/state", None)
    assert h.out[1]["case_title"] == E.CASES[0][0] and "Hermes" in h.out[1]["values"]


def test_pause_resume_intervene(paths):
    h = FakeH()
    A.handle(h, "POST", "/api/konzil/pause", {}); assert A.PAUSE.exists()
    A.handle(h, "POST", "/api/konzil/resume", {}); assert not A.PAUSE.exists() and h.out[1]["runner_started"] is False
    A.handle(h, "POST", "/api/konzil/intervene", {"kind": "einwand", "text": "Warum zählt Linas Stimme nicht?"})
    rec = json.loads(A.INBOX.read_text(encoding="utf-8").splitlines()[0])
    assert rec["kind"] == "einwand"
    A.handle(h, "POST", "/api/konzil/intervene", {"text": ""}); assert h.out[0] == 400
    assert A.handle(h, "GET", "/api/other", None) is False


def test_remote_post_needs_guard(paths):
    h = FakeH(); h.client_address = ("10.0.0.5", 0)
    A.handle(h, "POST", "/api/konzil/pause", {}); assert h.out[0] == 403 and not A.PAUSE.exists()


def test_watchdog_does_not_start_finished_or_stopped(paths):
    st = E.new_state("S"); st["status"] = "finished"; E.save(st, A.STATE)
    assert A.ensure_running() is False
    st["status"] = "running"; E.save(st, A.STATE); A.STOP.write_text("x")
    assert A.ensure_running() is False
    assert A.pid_alive(0) is False
