"""Phase 6 Abnahme: Backlog-Auswahl, Stufen, Rollback, STOP."""
from __future__ import annotations

import json

from nexus.bauprotokoll.selfbuild import SelfBuild


class Cfg:
    def __init__(self, data):
        self.data = data

    def get(self, dotted, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def cfg(**over):
    data = {
        "forbidden_paths": ["config.json", "validator/*"],
        "selfbuild": {
            "stop_file": "STOP", "observe_runs": 3, "successes_per_stage": 20,
            "stage_dirs": [["docs/*", "tests/*"], ["skills/*"], ["nexus/*"], ["nexus/*"]],
            "interval_s": 0,
        },
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k].update(v)
        else:
            data[k] = v
    return Cfg(data)


def write_backlog(root, items):
    (root / "backlog.json").write_text(json.dumps(items), encoding="utf-8")


def fake_git(calls):
    def runner(args):
        calls.append(args)
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "main"
        if args[:2] == ["rev-parse", "--verify"]:
            return 0, "abc123"
        return 0, ""
    return runner


def make(root, tmp_path, validator=None, implement=None, calls=None, **cfg_over):
    calls = calls if calls is not None else []
    return SelfBuild(
        root=root, config=cfg(**cfg_over), validator=validator,
        git_runner=fake_git(calls), implement=implement,
        state_path=tmp_path / "selfbuild.json",
    ), calls


def test_stop_file_halts_immediately(tmp_path):
    (tmp_path / "STOP").write_text("", encoding="utf-8")
    write_backlog(tmp_path, [{"id": "A", "risiko": "niedrig", "allowed_paths": ["docs/*"]}])
    sb, _ = make(tmp_path, tmp_path)
    assert sb.run_once(dry_run=False)["status"] == "STOPPED"


def test_pick_lowest_risk_then_priority(tmp_path):
    write_backlog(tmp_path, [
        {"id": "HIGH", "risiko": "hoch", "prioritaet": 9},
        {"id": "LOW1", "risiko": "niedrig", "prioritaet": 1},
        {"id": "LOW2", "risiko": "niedrig", "prioritaet": 5},
    ])
    sb, _ = make(tmp_path, tmp_path)
    assert sb.pick_backlog()["id"] == "LOW2"


def test_dry_run_does_not_touch_git(tmp_path):
    write_backlog(tmp_path, [{"id": "A", "risiko": "niedrig", "allowed_paths": ["docs/*"]}])
    sb, calls = make(tmp_path, tmp_path)
    out = sb.run_once(dry_run=True)
    assert out["status"] == "DRY_RUN"
    assert out["branch"].startswith("selfbuild/A-")
    assert all(c[0] == "rev-parse" for c in calls)  # nur lesend, keine Mutation


def test_stage_gate_rejects_outside_paths(tmp_path):
    write_backlog(tmp_path, [{"id": "A", "risiko": "niedrig", "allowed_paths": ["nexus/*"]}])
    sb, _ = make(tmp_path, tmp_path)
    out = sb.run_once(dry_run=True)
    assert out["status"] == "REJECTED"


def test_fail_triggers_rollback_and_requeue(tmp_path):
    write_backlog(tmp_path, [{"id": "A", "risiko": "niedrig", "allowed_paths": ["docs/*"]}])

    def validator(task, files):
        return {"task_id": task["task_id"], "status": "FAIL",
                "steps": [{"step": "tests", "result": "FAIL", "detail": "rot"}]}

    sb, calls = make(tmp_path, tmp_path, validator=validator,
                     implement=lambda item: {"docs/a.md": "x"})
    out = sb.run_once(dry_run=False)
    assert out["status"] == "FAIL" and out["rolled_back"] is True
    assert any(c[:2] == ["reset", "--hard"] for c in calls)
    state = sb.load_state()
    assert state["items"]["A"]["status"] == "backlog"
    assert state["successes_since_incident"] == 0


def test_pass_moves_to_staging_and_tracks_state(tmp_path):
    write_backlog(tmp_path, [{"id": "A", "risiko": "niedrig", "allowed_paths": ["docs/*"]}])
    validator = lambda task, files: {"task_id": task["task_id"], "status": "PASS",
                                     "steps": [{"step": "tests", "result": "PASS", "detail": "ok"}]}
    sb, calls = make(tmp_path, tmp_path, validator=validator,
                     implement=lambda item: {"docs/a.md": "x"})
    out = sb.run_once(dry_run=False)
    assert out["status"] == "PASS"
    assert any(c[:2] == ["merge", "--no-ff"] for c in calls)
    state = sb.load_state()
    assert state["items"]["A"]["status"] == "staging"
    assert state["staged_successes"] == 1


def test_promotion_to_main_after_observation(tmp_path):
    write_backlog(tmp_path, [{"id": "A", "risiko": "niedrig", "allowed_paths": ["docs/*"]}])
    validator = lambda task, files: {"task_id": task["task_id"], "status": "PASS", "steps": []}
    sb, calls = make(tmp_path, tmp_path, validator=validator,
                     implement=lambda item: {"docs/a.md": "x"},
                     selfbuild={"observe_runs": 1})
    out = sb.run_once(dry_run=False)
    assert out["promoted_main"] is True
    assert any(c[:1] == ["checkout"] and "main" in c for c in calls)
    assert sb.load_state()["items"]["A"]["status"] == "done"


def test_backlog_with_bom_is_read(tmp_path):
    (tmp_path / "backlog.json").write_text(
        json.dumps([{"id": "B", "risiko": "niedrig", "allowed_paths": ["docs/*"]}]),
        encoding="utf-8-sig",
    )
    sb, _ = make(tmp_path, tmp_path)
    assert [i["id"] for i in sb.load_backlog()] == ["B"]
    assert sb.run_once(dry_run=True)["status"] == "DRY_RUN"
