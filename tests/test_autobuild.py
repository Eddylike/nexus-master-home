import json
from pathlib import Path

import pytest

from nexus.autobuild import guard, loop


def test_c_drive_blocked():
    with pytest.raises(guard.CDriveBlocked):
        guard.check_write("C:\\Windows\\x.txt")
    with pytest.raises(guard.CDriveBlocked):
        guard.check_write("c:/Users/x/AppData/Local/Temp/y.txt")
    assert guard.check_write(Path(guard.ROOT) / "workspace" / "x.txt")


@pytest.fixture()
def sandbox(monkeypatch):
    root = Path(guard.ROOT) / "workspace" / "autobuild" / "_test"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "mod.py"
    target.write_text("X = 1\n", encoding="utf-8")
    monkeypatch.setattr(loop, "DIR", root)
    monkeypatch.setattr(loop, "BACKLOG", root / "backlog.json")
    monkeypatch.setattr(loop, "CYCLES", root / "cycles.jsonl")
    monkeypatch.setattr(loop, "PROGRESS", root / "progress.md")
    monkeypatch.setattr(loop, "STOP_FILE", root / "STOP")
    rel = str(target.relative_to(guard.ROOT)).replace("\\", "/")
    (root / "backlog.json").write_text(json.dumps([{"id": "T1", "title": "t", "status": "offen", "file": rel}]), encoding="utf-8")
    yield root, target, rel
    import shutil
    shutil.rmtree(root, ignore_errors=True)


def _ab(edits, ok=True):
    return loop.Autobuild(tests_runner=lambda a, t: {"ok": ok, "summary": "x"},
                          planner=lambda i: {"mode": "patch", "result": {"edits": edits}},
                          hermes=lambda c, t: {"ok": True, "path": "p"}, busy=lambda: None)


def test_apply_and_rollback(sandbox):
    root, target, rel = sandbox
    r = _ab([{"file": rel, "find": "X = 1", "replace": "X = 2"}], ok=False).cycle()
    assert r["result"] == "ROLLBACK" and target.read_text() == "X = 1\n"
    r = _ab([{"file": rel, "find": "X = 1", "replace": "X = 2"}], ok=True).cycle()
    assert r["result"] == "UEBERNOMMEN" and target.read_text() == "X = 2\n"


def test_bad_patch_and_c_drive(sandbox):
    root, target, rel = sandbox
    assert _ab([{"file": rel, "find": "nope", "replace": "y"}]).cycle()["result"] == "ROLLBACK"
    json.loads((root / "backlog.json").read_text())[0]
    (root / "backlog.json").write_text(json.dumps([{"id": "T2", "title": "t", "status": "offen", "file": rel}]), encoding="utf-8")
    r = _ab([{"file": "C:/Windows/evil.py", "find": "a", "replace": "b"}]).cycle()
    assert "C_DRIVE_BLOCKED" in r["reason"]


def test_paused_when_busy(sandbox):
    ab = loop.Autobuild(busy=lambda: "Gaming aktiv (x)")
    assert ab.cycle()["result"] == "PAUSIERT"


# ---- Selbstheilung / Hermes-Bericht (20261028) ----
def test_record_issue_dedup_and_recurring():
    from nexus.autobuild.loop import record_issue
    items = []
    a = record_issue(items, "Hermes-Bericht", "TOOL_OUTSIDE_AUTHORIZED_CONTRACT", "log1")
    assert a["id"] == "SB-AUTO-01" and a["status"] == "beobachtet"
    b = record_issue(items, "Hermes-Bericht", "BLOCKED TOOL_OUTSIDE_AUTHORIZED_CONTRACT", "log2")
    assert b is a and len(items) == 1 and a["count"] == 2 and a["status"] == "offen" and a["logs"] == ["log1", "log2"]
    c = record_issue(items, "Hermes-Bericht", "MAINTENANCE_ACTIVE", "x")
    assert c["id"] == "SB-AUTO-02"


def test_hermes_report_waits_on_maintenance(monkeypatch):
    from nexus.autobuild import loop
    monkeypatch.setattr(loop, "_maintenance_active", lambda: True)
    monkeypatch.setattr(loop.time, "sleep", lambda s: None)
    h = loop._hermes_report("T1", "x", wait_sec=-1)
    assert h["deferred"] and h["error"] == "DEFERRED_MAINTENANCE_ACTIVE"


def test_hermes_report_uses_contract_path(monkeypatch):
    from nexus.autobuild import loop
    from nexus.integrations import hermes_brain
    seen = []
    def fake(self, reqs):
        seen.append(reqs)
        return {"status": "PASS", "task_ids": ["t1", "t2"], "verification": [{"sha256": "ab"}]}
    monkeypatch.setattr(loop, "_maintenance_active", lambda: False)
    monkeypatch.setattr(hermes_brain.HermesBrain, "apply_requirements", fake)
    h = loop._hermes_report("T2", "inhalt")
    assert h["ok"] and seen == [[{"path": "workspace/projects/hermes/autobuild_T2.md", "content": "inhalt"}]]


def test_hermes_report_no_retry_on_mismatch(monkeypatch):
    from nexus.autobuild import loop
    from nexus.integrations import hermes_brain
    calls = []
    monkeypatch.setattr(loop, "_maintenance_active", lambda: False)
    monkeypatch.setattr(hermes_brain.HermesBrain, "apply_requirements",
                        lambda self, r: calls.append(1) or {"status": "BLOCKED", "error": "CONTENT_MISMATCH_NO_OVERWRITE"})
    h = loop._hermes_report("T3", "x")
    assert not h["ok"] and len(calls) == 1

def test_record_issue_uses_specific_code():
    from nexus.autobuild.loop import record_issue
    items = []
    assert record_issue(items, "Engine PLAN_FAILED", "PLAN_FAILED:NO_JSON", "x")["auto_key"] == "Engine PLAN_FAILED:NO_JSON"


def test_attempt_cap_blocks_item_and_selects_next():
    items = [
        {"id": "SWARM-05", "status": "fehlgeschlagen", "attempts": 3, "prio": 10},
        {"id": "SWARM-06", "status": "offen", "attempts": 0, "prio": 1},
    ]
    assert loop.pick_next_item(items)["id"] == "SWARM-06"
    assert items[0]["status"] == "blocked"


def test_apply_patch_is_idempotent_and_crlf_safe():
    text = "before\r\nanchor\r\nafter\r\n"
    assert loop.apply_patch(text, "anchor\n", "replacement\n") == "before\nreplacement\nafter\n"
    assert loop.apply_patch("replacement\n", "anchor\n", "replacement\n") == "already_applied"
    with pytest.raises(loop.AnchorMissing, match="PATCH_ANCHOR_MISSING"):
        loop.apply_patch("before\n", "anchor\n", "replacement\n")


def test_display_unavailable_skips_autobuild_item(sandbox):
    from nexus.platform.display import DisplayUnavailable

    _, _, rel = sandbox
    build = loop.Autobuild(
        tests_runner=lambda args, timeout: (_ for _ in ()).throw(DisplayUnavailable("headless")),
        planner=lambda item: {"mode": "patch", "result": {"edits": [{"file": rel, "find": "X = 1", "replace": "X = 2"}]}},
        hermes=lambda cycle, text: {"ok": True, "path": "p"},
        busy=lambda: None,
    )
    assert build.cycle()["result"] == "SKIPPED"


def test_locate_source_finds_error_code():
    from nexus.autobuild.loop import locate_source
    f, anchor = locate_source({"auto_key": "Engine PLAN_FAILED:NO_JSON", "logs": ["x SB03: PLAN_FAILED:NO_JSON"]})
    assert f == "nexus/autobuild/loop.py" and "NO_JSON" in anchor


def test_gpt_ask_passes_json_format(monkeypatch):
    from nexus.integrations import gpt_local_agent as g
    sent = {}
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"message": {"content": "{}"}}'
    def fake(req, timeout=None):
        sent.update(json.loads(req.data.decode("utf-8"))); return R()
    import json
    monkeypatch.setattr(g.urllib.request, "urlopen", fake)
    assert g.ask("hi", fmt="json", num_ctx=4096)["success"]
    assert sent["format"] == "json" and sent["options"]["num_ctx"] == 4096


def test_correction_round_not_counted(sandbox):
    root, target, rel = sandbox
    plans = iter([{"mode": "patch", "result": {"edits": []}},
                  {"mode": "patch", "result": {"edits": [{"file": rel, "find": "X = 1", "replace": "X = 3"}]}}])
    seen = []
    def planner(i):
        seen.append(i.get("_korrektur"))
        return next(plans)
    ab = loop.Autobuild(tests_runner=lambda a, t: {"ok": True, "summary": "x"}, planner=planner,
                        hermes=lambda c, t: {"ok": True, "path": "p"}, busy=lambda: None)
    r = ab.cycle()
    assert r["result"] == "UEBERNOMMEN" and "NO_EDITS" in r["korrektur"] and target.read_text() == "X = 3\n"
    assert seen[0] is None and "NO_EDITS" in seen[1][1] and "_korrektur" not in json.loads((root / "backlog.json").read_text())[0]
    assert not json.loads((root / "backlog.json").read_text())[0].get("attempts")


def test_ast_excerpt_full_function_and_imports():
    src = "import os\n\ndef a(x):\n    y = 1\n    raise ValueError('NO_JSON')\n\ndef b():\n    return a(2)\n"
    ex = loop._ast_excerpt(src, 4)
    assert "import os" in ex and "def a(x):" in ex and "raise ValueError('NO_JSON')" in ex and "def b():" in ex


def test_json_from_keeps_nested_and_rejects_garbage():
    # Schutz bestehenden Verhaltens: verschachteltes JSON vollstaendig, Muell bleibt Fehler (kein Scheinerfolg)
    r = loop._json_from('Antwort: {"edits": [{"file": "a.py", "find": "x", "replace": "y"}], "test": {"path": "t", "content": "c"}}')
    assert r["edits"][0]["replace"] == "y" and r["test"]["content"] == "c"
    with pytest.raises(ValueError):
        loop._json_from("kein json hier")


def test_compute_edits_strips_excerpt_headers(sandbox):
    root, target, rel = sandbox
    out = loop._compute_edits([{"file": rel, "find": "# --- Betroffen: X ---\nX = 1", "replace": "# --- Betroffen: X ---\nX = 5"}])
    assert list(out.values())[0] == "X = 5\n"


def test_pick_next_item_respects_depends_on():
    items = [
        {"id": "A", "status": "offen", "attempts": 0, "prio": 10},
        {"id": "B", "status": "offen", "attempts": 0, "prio": 99, "depends_on": ["A"]},
        {"id": "C", "status": "erledigt", "attempts": 0, "prio": 1},
        {"id": "D", "status": "offen", "attempts": 0, "prio": 50, "depends_on": ["C"]},
    ]
    # B has highest prio but blocked by unmet deps -> A next (prio 10) over D? D has prio 50 and deps ok
    assert loop.pick_next_item(items)["id"] == "D"
    items[0]["status"] = "erledigt"
    assert loop.pick_next_item(items)["id"] == "B"


def test_ensure_target_scaffold_creates_missing_md(tmp_path, monkeypatch):
    from nexus.autobuild import guard
    monkeypatch.setattr(loop, "ROOT", tmp_path)
    monkeypatch.setattr(guard, "ROOT", tmp_path)
    # check_write must allow tmp_path
    def _cw(p):
        return Path(p)
    monkeypatch.setattr(loop, "check_write", _cw)
    item = {"id": "SZ-06", "file": "workspace/reports/X.md", "title": "Sec"}
    assert loop.ensure_target_scaffold(item) == "scaffold:workspace/reports/X.md"
    p = tmp_path / "workspace" / "reports" / "X.md"
    assert p.exists() and "NOT_TESTED" in p.read_text(encoding="utf-8")
    assert loop.ensure_target_scaffold(item) is None  # already exists
