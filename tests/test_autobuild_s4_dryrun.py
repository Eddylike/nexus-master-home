"""Auftrag 20261117 Schritt 4a: Dry-Run ist Standard im Selbstbau (nexus/autobuild/loop.py).

Dry-Run plant nur, schreibt nichts ins Repo/Backlog und loggt den Plan nach cycles.jsonl (mode=dry_run).
Echt-Schreiben nur mit explizitem Flag (Autobuild(write=True) oder NEXUS_AUTOBUILD_WRITE=1).
"""
import json
import shutil
import threading
import time
import uuid
from pathlib import Path

import pytest

from nexus.autobuild import guard, loop

GREEN = {"ok": True, "reason": "VALIDATOR_GRUEN: test"}


@pytest.fixture()
def sb(monkeypatch):
    root = Path(guard.ROOT) / "workspace" / "autobuild" / f"_test_s4_{uuid.uuid4().hex[:8]}"
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


def _planner(rel):
    return lambda item: {"mode": "patch", "result": {"begruendung": "b",
                                                      "edits": [{"file": rel, "find": "X = 1", "replace": "X = 2"}]}}


def test_default_is_dry_run(sb):
    ab = loop.Autobuild()
    assert ab.write is False
    assert ab.status()["mode"] == "dry_run"
    assert loop.write_enabled_default() is False


def test_write_needs_explicit_flag(sb, monkeypatch):
    monkeypatch.setenv(loop.WRITE_ENV, "0")
    assert loop.Autobuild().write is False
    monkeypatch.setenv(loop.WRITE_ENV, "yes")
    assert loop.Autobuild().write is False
    monkeypatch.setenv(loop.WRITE_ENV, "1")
    assert loop.Autobuild().write is True
    assert loop.Autobuild(write=False).write is False
    monkeypatch.delenv(loop.WRITE_ENV)
    assert loop.Autobuild(write=True).write is True


def test_dry_run_plans_but_writes_nothing(sb):
    root, target, rel = sb
    backlog_before = (root / "backlog.json").read_bytes()
    calls = []
    ab = loop.Autobuild(planner=_planner(rel),
                        tests_runner=lambda a, t: calls.append(("tests", a)) or {"ok": True, "summary": "x"},
                        hermes=lambda c, t: calls.append(("hermes", c)) or {"ok": True, "path": "p"},
                        busy=lambda: None, gate=lambda: GREEN)
    r = ab.cycle()
    assert r["result"] == "DRY_RUN" and r["mode"] == "dry_run" and r["success"] is True
    assert target.read_text(encoding="utf-8") == "X = 1\n"          # Repo-Datei unveraendert
    assert (root / "backlog.json").read_bytes() == backlog_before    # Backlog unveraendert
    assert calls == []                                               # keine Tests, kein Hermes-Bericht
    assert not (root / "progress.md").exists() and not (root / "backups").exists()
    last = _cycles(root)[-1]
    assert last["mode"] == "dry_run" and last["result"] == "DRY_RUN" and last["item"] == "T1"
    assert last["plan"]["would_change"] == [rel]
    assert last["plan"]["edits"] == [{"file": rel, "kind": "patch"}]
    assert last["gate"]["ok"] is True


def test_dry_run_logs_plan_error_without_writing(sb):
    root, target, rel = sb
    def boom(item):
        raise RuntimeError("kein Modell")
    ab = loop.Autobuild(planner=boom, hermes=lambda c, t: pytest.fail("Hermes im Dry-Run"),
                        busy=lambda: None, gate=lambda: GREEN)
    r = ab.cycle()
    assert r["result"] == "DRY_RUN" and "PLAN_FAILED" in r["plan"]["plan_error"]
    assert target.read_text(encoding="utf-8") == "X = 1\n"
    assert json.loads((root / "backlog.json").read_text(encoding="utf-8"))[0].get("attempts") is None
    assert _cycles(root)[-1]["mode"] == "dry_run"


def test_write_flag_really_writes(sb):
    root, target, rel = sb
    ab = loop.Autobuild(planner=_planner(rel), tests_runner=lambda a, t: {"ok": True, "summary": "1 passed"},
                        hermes=lambda c, t: {"ok": True, "path": "p"}, busy=lambda: None,
                        write=True, gate=lambda: GREEN)
    r = ab.cycle()
    assert r["result"] == "UEBERNOMMEN" and r["mode"] == "write"
    assert target.read_text(encoding="utf-8") == "X = 2\n"
    assert _cycles(root)[-1]["mode"] == "write"


def test_loop_dry_run_does_not_spin(sb, monkeypatch):
    root, target, rel = sb
    monkeypatch.setattr(loop, "FIRST_DELAY_SEC", 0)
    monkeypatch.setattr(loop, "INTERVAL_SEC", 3600)
    planned = []
    ab = loop.Autobuild(planner=lambda i: planned.append(i["id"]) or _planner(rel)(i),
                        busy=lambda: None, gate=lambda: GREEN)
    th = threading.Thread(target=ab._loop, daemon=True)
    th.start()
    try:
        deadline = time.time() + 20
        while time.time() < deadline and not _cycles(root):
            time.sleep(0.1)
        time.sleep(1.0)
        assert len(_cycles(root)) == 1 and planned == ["T1"]  # 1 Plan, dann Intervall-Wartezeit
        assert target.read_text(encoding="utf-8") == "X = 1\n"
    finally:
        ab._stop.set()
        th.join(10)
    assert not th.is_alive()
