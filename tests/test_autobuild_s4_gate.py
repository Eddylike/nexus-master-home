"""Auftrag 20261117 Schritt 4b: Validator-Gate im Selbstbau.

Der Loop laeuft nur, wenn der Validator gruen ist; sonst pausiert er und schreibt einen
Logeintrag (paused, Grund) nach cycles.jsonl - im Dry-Run wie im Schreibmodus.
"""
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

import pytest

from nexus.autobuild import guard, loop

RED = {"ok": False, "reason": "VALIDATOR_ROT: 1 failed"}


@pytest.fixture()
def sb(monkeypatch):
    root = Path(guard.ROOT) / "workspace" / "autobuild" / f"_test_s4g_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True)
    target = root / "mod.py"
    target.write_text("X = 1\n", encoding="utf-8")
    for name, val in (("DIR", root), ("BACKLOG", root / "backlog.json"), ("CYCLES", root / "cycles.jsonl"),
                      ("PROGRESS", root / "progress.md"), ("STOP_FILE", root / "STOP"),
                      ("PENDING", root / "hermes_pending.json")):
        monkeypatch.setattr(loop, name, val)
    monkeypatch.setattr(loop, "_emit", lambda *a, **k: None)
    monkeypatch.delenv(loop.WRITE_ENV, raising=False)
    rel = target.relative_to(guard.ROOT).as_posix()
    (root / "backlog.json").write_text(json.dumps([{"id": "T1", "title": "t", "status": "offen", "file": rel}]),
                                       encoding="utf-8")
    yield root, target, rel
    shutil.rmtree(root, ignore_errors=True)


def _cycles(root):
    fp = root / "cycles.jsonl"
    return [json.loads(l) for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()] if fp.exists() else []


def _never(*a, **k):
    pytest.fail("darf bei rotem Validator nicht aufgerufen werden")


@pytest.mark.parametrize("write", [False, True])
def test_red_validator_pauses_and_logs(sb, write):
    root, target, rel = sb
    ab = loop.Autobuild(planner=_never, tests_runner=_never, hermes=_never, busy=lambda: None,
                        write=write, gate=lambda: RED)
    r = ab.cycle()
    assert r["result"] == "PAUSIERT" and r["paused"] is True and r["gate_failed"] is True
    assert "VALIDATOR_ROT" in r["reason"]
    assert target.read_text(encoding="utf-8") == "X = 1\n"
    last = _cycles(root)[-1]
    assert last["paused"] is True and last["result"] == "PAUSIERT" and "VALIDATOR_ROT" in last["reason"]
    assert last["mode"] == ("write" if write else "dry_run")


def test_gate_exception_pauses(sb):
    root, target, rel = sb
    def boom():
        raise OSError("validator weg")
    r = loop.Autobuild(planner=_never, busy=lambda: None, gate=boom).cycle()
    assert r["result"] == "PAUSIERT" and "VALIDATOR_GATE_FEHLER" in r["reason"]
    assert _cycles(root)[-1]["paused"] is True


def test_green_gate_is_cached(sb):
    root, target, rel = sb
    n = []
    ab = loop.Autobuild(planner=lambda i: {"mode": "analysis", "result": {"x": 1}}, busy=lambda: None,
                        gate=lambda: n.append(1) or {"ok": True, "reason": "VALIDATOR_GRUEN"})
    assert ab.cycle()["result"] == "DRY_RUN"
    assert ab.cycle()["result"] == "DRY_RUN"
    assert len(n) == 1 and ab.status()["gate"]["ok"] is True


def test_red_gate_is_not_cached(sb):
    root, target, rel = sb
    n = []
    ab = loop.Autobuild(planner=_never, busy=lambda: None, gate=lambda: n.append(1) or RED)
    ab.cycle()
    ab.cycle()
    assert len(n) == 2 and ab.status()["gate"] is None


def test_validator_gate_function(monkeypatch, tmp_path):
    seen = []
    red = loop.validator_gate(runner=lambda a, t: seen.append(a) or {"ok": False, "summary": "1 failed", "detail": "E x"})
    assert red["ok"] is False and red["reason"].startswith("VALIDATOR_ROT")
    assert seen == [loop.VALIDATOR_TESTS]
    green = loop.validator_gate(runner=lambda a, t: {"ok": True, "summary": "60 passed"})
    assert green["ok"] is True and "60 passed" in green["reason"]
    monkeypatch.setattr(loop, "ROOT", tmp_path)  # kein Validator vorhanden
    missing = loop.validator_gate(runner=_never)
    assert missing["ok"] is False and missing["reason"].startswith("VALIDATOR_FEHLT")


def test_loop_pauses_on_red_validator(sb, monkeypatch):
    root, target, rel = sb
    monkeypatch.setattr(loop, "FIRST_DELAY_SEC", 0)
    monkeypatch.setattr(loop, "INTERVAL_SEC", 3600)
    n = []
    ab = loop.Autobuild(planner=_never, busy=lambda: None, gate=lambda: n.append(1) or RED)
    th = threading.Thread(target=ab._loop, daemon=True)
    th.start()
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not _cycles(root):
            time.sleep(0.1)
        time.sleep(1.0)
        rows = _cycles(root)
        assert len(rows) == 1 and rows[0]["paused"] is True and len(n) == 1  # kein Dauerfeuer
    finally:
        ab._stop.set()
        th.join(10)
    assert not th.is_alive()
