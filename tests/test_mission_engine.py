import threading
import time

import pytest

from nexus.mission.engine import MissionEngine
from nexus.mission.store import MissionStore


def plan(steps):
    return lambda goal, context="": {"analysis": "test", "steps": steps, "planner": "test", "error": None}


def step(title, agent="NEXUS", action="analyze", deps=None, critical=False, instruction="tun"):
    return {"title": title, "agent": agent, "action": action, "instruction": instruction,
            "depends_on": deps or [], "critical": critical}


def ok_runner(calls=None, barrier=None):
    def run(agent, step, mission, store):
        if calls is not None:
            calls.append((step["id"], "start", time.time()))
        if barrier is not None and step["id"] in (0, 1):
            barrier.wait(timeout=3)
        if calls is not None:
            calls.append((step["id"], "end", time.time()))
        calls_out = [{"tool": "test"}] if step.get("action") == "execute_technical" else []
        return {"success": True, "output": f"{agent} ergebnis {step['title']}",
                "evidence": "test", "agent": agent, "tool_calls": calls_out}
    return run


def test_complete_mission_and_final_answer(tmp_path):
    engine = MissionEngine(store=MissionStore(tmp_path / "m"),
                           planner=plan([step("a"), step("b", agent="NEXUS GPT", deps=[0]),
                                         step("final", action="synthesize", deps=[0, 1])]),
                           runner=ok_runner())
    mission = engine.submit("Ziel erreichen", background=False)
    assert mission["status"] == "completed"
    assert "final" in mission["final"].lower()
    assert mission["verification"]["passed"] is True
    assert len(mission["approvals"]) == 0


def test_independent_steps_run_parallel(tmp_path):
    barrier = threading.Barrier(2)
    engine = MissionEngine(store=MissionStore(tmp_path / "m"), max_parallel=2,
                           planner=plan([step("a"), step("b"), step("final", action="synthesize")]),
                           runner=ok_runner(barrier=barrier))
    started = time.time()
    mission = engine.submit("parallel", background=False)
    assert mission["status"] == "completed"
    assert time.time() - started < 3


def test_failure_triggers_exactly_one_repair_round(tmp_path):
    attempts = {"n": 0}

    def run(agent, s, mission, store):
        if s["id"] == 0:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return {"success": False, "output": "", "error": "kaputt", "agent": agent}
        return {"success": True, "output": "repariert " + s["title"], "evidence": "test", "agent": agent}

    engine = MissionEngine(store=MissionStore(tmp_path / "m"),
                           planner=plan([step("technik"), step("final", action="synthesize", deps=[0])]),
                           runner=run)
    mission = engine.submit("reparieren", background=False)
    assert attempts["n"] == 2
    assert mission["status"] == "completed"
    assert mission["correction_round"] == 1
    assert "repariert" in mission["final"]


def test_critical_step_requires_approval_and_resumes(tmp_path):
    calls = []
    engine = MissionEngine(store=MissionStore(tmp_path / "m"),
                           planner=plan([step("senden", agent="Hermes", action="execute_technical",
                                              critical=True),
                                         step("final", action="synthesize", deps=[0])]),
                           runner=ok_runner(calls))
    mission = engine.submit("Nachricht senden", background=False)
    assert mission["status"] == "awaiting_approval"
    assert calls == []
    assert mission["steps"][0]["status"] == "awaiting_approval"

    done = engine.approve(mission["id"], 0, True, by="Eddie", background=False)
    assert done["status"] == "completed"
    assert any(c[0] == 0 for c in calls)
    assert done["approvals"]["0"]["approved"] is True


def test_rejected_critical_step_is_not_executed(tmp_path):
    calls = []
    engine = MissionEngine(store=MissionStore(tmp_path / "m"),
                           planner=plan([step("loeschen", agent="Hermes", action="execute_technical",
                                              critical=True)]),
                           runner=ok_runner(calls))
    mission = engine.submit("Datei loeschen", background=False)
    done = engine.approve(mission["id"], 0, False, background=False)
    assert done["steps"][0]["status"] == "rejected"
    assert calls == []
    assert done["status"] == "failed"
    assert "Nicht abgeschlossen" in done["final"]


def test_shared_memory_provenance_and_access(tmp_path):
    store = MissionStore(tmp_path / "m")
    store.remember("MS-1", "Hermes", "Passwort ist geheim", evidence="datei")
    store.remember("MS-1", "NEXUS GPT", "Oeffentliche Analyse")
    assert len(store.recall("MS-1", "Passwort", requester="Hermes")) == 1
    assert store.recall("MS-1", "Passwort", requester="NEXUS GPT") == []
    assert len(store.recall("MS-1", "Passwort", requester="NEXUS")) == 1
    assert store.recall("MS-1", "Analyse", requester="Hermes")[0]["agent"] == "NEXUS GPT"


def test_planner_fallback_is_valid_and_marks_destructive(tmp_path, monkeypatch):
    import nexus.integrations.gpt_local_agent as gpt

    def boom(*a, **k):
        raise RuntimeError("kein Modell")

    monkeypatch.setattr(gpt, "ask", boom)
    from nexus.mission.planner import plan_mission
    result = plan_mission("Loesche alte Dateien und repariere das System")
    assert result["planner"] == "fallback"
    assert result["error"]
    assert any(s["action"] == "synthesize" for s in result["steps"])
    assert any(s["critical"] for s in result["steps"])
    ids = [s["id"] for s in result["steps"]]
    assert ids == list(range(len(ids)))


def test_step_timeout_does_not_block_mission(tmp_path):
    def slow(agent, s, mission, store):
        time.sleep(1.0)
        return {"success": True, "output": "zu spaet", "agent": agent}

    engine = MissionEngine(store=MissionStore(tmp_path / "m"), step_timeout=0.2,
                           planner=plan([step("langsam")]), runner=slow)
    started = time.time()
    mission = engine.submit("timeout", background=False)
    elapsed = time.time() - started
    assert elapsed < 0.9
    assert mission["steps"][0]["status"] == "failed"
    assert "STEP_TIMEOUT" in mission["steps"][0]["result"]["error"]
