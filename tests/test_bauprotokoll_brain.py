"""Phase 4 Abnahme: Model-Manager (ein grosses Modell) und Master Brain (max. 7 Schritte)."""
from __future__ import annotations

import pytest

from nexus.bauprotokoll.brain import MasterBrain
from nexus.bauprotokoll.models import ModelManager, ModelUnavailable
from nexus.bauprotokoll.skills import SkillLibrary
from nexus.bauprotokoll.states import TaskState


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


BASE = {
    "models": {
        "router": "router:3b", "generalist": "gen:9b",
        "coder": "coder:7b", "embeddings": "embed:1b",
    },
    "ollama": {"url": "http://127.0.0.1:1", "keep_alive": "5m", "timeout_s": 5},
    "budgets": {"max_steps": 7},
}


class FakeOllama:
    def __init__(self, installed, loaded):
        self.installed = list(installed)
        self.loaded_names = list(loaded)
        self.calls = []

    def tags(self):
        return list(self.installed)

    def loaded(self):
        return [{"name": n} for n in self.loaded_names]

    def unload(self, name):
        self.calls.append(("unload", name))
        if name in self.loaded_names:
            self.loaded_names.remove(name)
        return True

    def load(self, name, keep_alive="5m"):
        self.calls.append(("load", name, keep_alive))
        if name not in self.loaded_names:
            self.loaded_names.append(name)
        return True

    def generate(self, *a, **kw):
        return "ok"


def manager(installed=None, loaded=None):
    client = FakeOllama(installed or list(BASE["models"].values()), loaded or [])
    return ModelManager(config=Cfg(BASE), client=client), client


def test_ensure_unloads_other_big_model():
    mm, client = manager(loaded=["gen:9b", "embed:1b"])
    name = mm.ensure("coder")
    assert name == "coder:7b"
    assert ("unload", "gen:9b") in client.calls
    assert ("unload", "embed:1b") not in client.calls
    assert ("load", "coder:7b", "5m") in client.calls
    assert "coder:7b" in client.loaded_names


def test_embeddings_model_is_never_unloaded():
    mm, client = manager(loaded=["embed:1b"])
    mm.ensure("generalist")
    assert "embed:1b" in client.loaded_names


def test_missing_model_raises():
    mm, _ = manager(installed=["gen:9b"])
    with pytest.raises(ModelUnavailable):
        mm.ensure("coder")


def test_classify_routes():
    mm, _ = manager()
    assert mm.classify("Implementiere eine Python-Funktion") == "coder"
    assert mm.classify("Recherchiere Quellen zum Markt") == "generalist"
    assert mm.classify("ja") == "router"


class FakeMemory:
    def __init__(self):
        self.context_calls = []
        self.reports = []
        self.states = []

    def context_for(self, task):
        self.context_calls.append(task)
        return {"decisions": [{"x": 1}], "results": [], "lessons": [], "project_state": []}

    def record_validation(self, report):
        self.reports.append(report)
        return {"status": report["status"]}

    def set_project_state(self, *a, **kw):
        self.states.append((a, kw))


class FakePipeline:
    def __init__(self, status):
        self.status = status
        self.plan = None

    def run(self, plan):
        self.plan = plan
        return {"task_id": plan["task_id"], "status": self.status, "steps": []}


def brain(tmp_path, memory=None):
    lib = SkillLibrary(tmp_path / "skills")
    lib.ensure_starters()
    return MasterBrain(config=Cfg(BASE), memory=memory, skills=lib)


def test_plan_has_max_7_steps_and_memory(tmp_path):
    mem = FakeMemory()
    b = brain(tmp_path, mem)
    task = "A und B und C und D und E und F und G und H und I und J"
    plan = b.plan(task)
    assert len(plan["steps"]) <= 7
    assert mem.context_calls == [task]
    assert plan["goal"]["ziel"] == task


def test_plan_assigns_bugfix_to_coder(tmp_path):
    plan = brain(tmp_path).plan("Repariere den Fehler in der Python-Funktion")
    step = plan["steps"][0]
    assert step["skill"] == "bugfix"
    assert step["agent"] == "coder"


def test_run_pass_sets_done_and_writes_memory(tmp_path):
    mem = FakeMemory()
    b = brain(tmp_path, mem)
    out = b.run("Repariere den Fehler", pipeline=FakePipeline("PASS"))
    assert out["state"] == TaskState.DONE.value
    assert mem.reports and mem.reports[0]["status"] == "PASS"


def test_run_fail_returns_in_progress(tmp_path):
    mem = FakeMemory()
    b = brain(tmp_path, mem)
    out = b.run("Repariere den Fehler", pipeline=FakePipeline("FAIL"))
    assert out["state"] == TaskState.IN_PROGRESS.value
    assert mem.reports[0]["status"] == "FAIL"


def test_plan_only_never_marks_done(tmp_path):
    out = brain(tmp_path).run("Schreibe eine Doku")
    assert out["state"] == TaskState.PLANNED.value


def test_finish_rejects_invalid_report(tmp_path):
    b = brain(tmp_path)
    with pytest.raises(ValueError):
        b.finish({"task_id": "T", "status": "MAYBE"})
