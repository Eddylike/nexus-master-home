"""Phase 5 Abnahme: Rollenrechte, Whitelist, Pipeline-Limits."""
from __future__ import annotations

import sys

import pytest

from nexus.bauprotokoll.agents import (Coder, Hermes, PolicyError, Reviewer,
                                      TeamPipeline, Tester)


def hermes(tmp_path):
    return Hermes(
        ["python", "pytest", "git", "ruff"], ["pytest", "unittest", "py_compile"],
        cwd=tmp_path,
    )


def test_hermes_rejects_shell_string():
    with pytest.raises(PolicyError):
        Hermes(["python"], ["pytest"]).check("python -m pytest")


def test_hermes_rejects_non_whitelisted_binary():
    with pytest.raises(PolicyError):
        Hermes(["python"], []).check(["cmd", "/c", "dir"])


def test_hermes_rejects_python_dash_c():
    with pytest.raises(PolicyError):
        Hermes(["python"], ["pytest"]).check([sys.executable, "-c", "print(1)"])


def test_hermes_rejects_module_outside_whitelist():
    with pytest.raises(PolicyError):
        Hermes(["python"], ["pytest"]).check([sys.executable, "-m", "os", "listdir"])


def test_hermes_rejects_metacharacters():
    with pytest.raises(PolicyError):
        Hermes(["git"], []).check(["git", "log", "&&", "rm"])


def test_hermes_allows_whitelisted_module():
    exe = Hermes(["python"], ["pytest"]).check([sys.executable, "-m", "pytest", "-q"])
    assert exe == "python"


def test_coder_writes_only_allowed_paths(tmp_path):
    c = Coder(tmp_path)
    c.write("skills/demo/skill.json", "{}", ["skills/*"])
    assert (tmp_path / "skills" / "demo" / "skill.json").is_file()
    with pytest.raises(PolicyError):
        c.write("tests/test_x.py", "x", ["skills/*"])


def test_coder_rejects_forbidden_and_escape(tmp_path):
    c = Coder(tmp_path)
    with pytest.raises(PolicyError):
        c.write("config.json", "{}", ["*"])
    with pytest.raises(PolicyError):
        c.write("validator/validator.py", "x", ["*"])
    with pytest.raises(PolicyError):
        c.write("../outside.txt", "x", ["*"])


def test_reviewer_flags_secrets_and_todos():
    review = Reviewer().review("x", {"a.py": "TODO fix\npassword = 'supersecret123'\n"})
    assert any("TODO" in b for b in review["befund"])
    assert any("Secret" in r for r in review["risiken"])
    assert review["empfehlung"] == "blockieren"


class FakeMemory:
    def __init__(self):
        self.reports = []

    def record_validation(self, report):
        self.reports.append(report)


def make_pipeline(tmp_path, memory, results):
    calls = {"n": 0}

    def validator(plan, files):
        calls["n"] += 1
        status = results[min(calls["n"] - 1, len(results) - 1)]
        return {"task_id": plan["task_id"], "status": status,
                "steps": [{"step": "tests", "result": status, "detail": "fake"}]}

    h = hermes(tmp_path)
    coder = Coder(tmp_path)
    pipeline = TeamPipeline(coder, Tester(h, coder), Reviewer(), validator,
                            memory=memory, max_attempts=3)
    return pipeline, calls


def plan():
    return {"task_id": "T-1", "goal": {"ziel": "x"}, "allowed_paths": ["skills/*"]}


def test_pipeline_pass_on_second_attempt(tmp_path):
    mem = FakeMemory()
    pipeline, calls = make_pipeline(tmp_path, mem, ["FAIL", "PASS"])
    out = pipeline.run(plan(), implement=lambda p, a: {"skills/x.txt": "ok"})
    assert out["status"] == "PASS"
    assert len(out["attempts"]) == 2
    assert calls["n"] == 2
    assert mem.reports and mem.reports[-1]["status"] == "FAIL"


def test_pipeline_aborts_after_three_attempts(tmp_path):
    mem = FakeMemory()
    pipeline, calls = make_pipeline(tmp_path, mem, ["FAIL"])
    out = pipeline.run(plan(), implement=lambda p, a: {"skills/x.txt": "ok"})
    assert out["status"] == "FAIL"
    assert out["abgebrochen"] is True
    assert len(out["attempts"]) == 3
    assert "Eddy" in out["eskalation"]


def test_pipeline_rejects_write_outside_allowed(tmp_path):
    mem = FakeMemory()
    pipeline, calls = make_pipeline(tmp_path, mem, ["PASS"])
    out = pipeline.run(plan(), implement=lambda p, a: {"tests/test_x.py": "x"})
    # Implementierung liefert immer denselben unerlaubten Pfad -> Coder blockt, Validator nie PASS
    assert out["status"] == "FAIL"
    assert all(a["report"]["steps"][0]["step"] == "coder" for a in out["attempts"])
    assert calls["n"] == 0


def test_extract_files_from_fence():
    from nexus.bauprotokoll.agents import extract_files
    text = 'Hier:\n```json\n{"files": {"a/b.py": "print(1)"}}\n```'
    assert extract_files(text) == {"a/b.py": "print(1)"}


def test_extract_files_rejects_missing_files():
    from nexus.bauprotokoll.agents import PolicyError, extract_files
    try:
        extract_files('{"nope": 1}')
    except PolicyError:
        pass
    else:
        raise AssertionError("fehlendes files-Feld muss abgelehnt werden")


def test_run_task_dry_run_plans_only(tmp_path):
    from nexus.bauprotokoll.brain import MasterBrain
    from nexus.bauprotokoll.memory import Memory
    from nexus.bauprotokoll.run import run_task
    from nexus.bauprotokoll.skills import SkillLibrary

    class C:
        data = {"models": {"router": "r", "generalist": "g", "coder": "c", "embeddings": "e"},
                "ollama": {"url": "http://127.0.0.1:1"}, "budgets": {"max_steps": 7}}

        def get(self, dotted, default=None):
            node = self.data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    mem = Memory(db_path=tmp_path / "m.db", use_ollama=False)
    lib = SkillLibrary(tmp_path / "skills")
    lib.ensure_starters()
    brain = MasterBrain(config=C(), memory=mem, skills=lib)
    out = run_task("Repariere den Fehler", ["skills/*"], execute=False,
                   brain_pipeline=(brain, None, None, mem))
    assert out["state"] == "PLANNED"
    assert out["plan"]["allowed_paths"] == ["skills/*"]
    assert out["plan"]["base_ref"] == "HEAD"
