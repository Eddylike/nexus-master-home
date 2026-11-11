import pytest

from nexus.mission import engine as engine_mod
from nexus.mission.api import handle
from nexus.mission.engine import MissionEngine
from nexus.mission.store import MissionStore


def mk_engine(tmp_path, critical=False):
    plan = lambda goal, context="": {"analysis": "t", "planner": "test", "error": None, "steps": [
        {"title": "a", "agent": "NEXUS", "action": "analyze", "instruction": "a", "depends_on": [],
         "critical": critical},
        {"title": "final", "agent": "NEXUS", "action": "synthesize", "instruction": "f", "depends_on": [0],
         "critical": False},
    ]}

    def runner(agent, step, mission, store):
        return {"success": True, "output": "ok " + step["title"], "evidence": "test", "agent": agent}

    return MissionEngine(store=MissionStore(tmp_path / "m"), planner=plan, runner=runner)


@pytest.fixture
def api(tmp_path, monkeypatch):
    eng = mk_engine(tmp_path)
    monkeypatch.setattr(engine_mod, "_ENGINE", eng)
    from nexus.mission import agents
    monkeypatch.setattr(agents, "health", lambda: {n: {"reachable": True} for n in agents.CORE_AGENTS})
    return eng


def test_run_wait_and_status(api):
    code, payload = handle("POST", "/api/mission/run", {"goal": "test", "wait": True})
    assert code == 200
    mission = payload["mission"]
    assert mission["status"] == "completed"
    code, status = handle("GET", "/api/mission/status")
    assert code == 200 and status["success"]
    assert any(m["id"] == mission["id"] for m in status["missions"])
    code, detail = handle("GET", "/api/mission/runs/" + mission["id"])
    assert code == 200 and detail["mission"]["final"]


def test_rejects_non_loopback_writes(api):
    code, payload = handle("POST", "/api/mission/run", {"goal": "x"}, remote="10.0.0.5")
    assert code == 403
    code, _ = handle("POST", "/api/mission/run", {"goal": "x"}, headers={"X-Forwarded-For": "1.2.3.4"})
    assert code == 403


def test_requires_goal_and_unknown_mission(api):
    assert handle("POST", "/api/mission/run", {})[0] == 400
    assert handle("GET", "/api/mission/runs/MS-nope")[0] == 404


def test_approval_flow_via_api(tmp_path, monkeypatch):
    eng = mk_engine(tmp_path, critical=True)
    monkeypatch.setattr(engine_mod, "_ENGINE", eng)
    from nexus.mission import agents
    monkeypatch.setattr(agents, "health", lambda: {n: {"reachable": True} for n in agents.CORE_AGENTS})
    code, payload = handle("POST", "/api/mission/run", {"goal": "senden", "wait": True})
    assert code == 200
    mission = payload["mission"]
    assert mission["status"] == "awaiting_approval"
    code, out = handle("POST", "/api/mission/approve", {"mission_id": mission["id"], "step_id": 0,
                                                        "approved": True, "wait": True})
    assert code == 200
    assert out["mission"]["status"] == "completed"


def test_memory_access_via_api(api):
    api.store.remember("MS-1", "Hermes", "Passwort geheim")
    code, out = handle("GET", "/api/mission/memory", None,
                       {"q": ["Passwort"], "requester": ["NEXUS GPT"]})
    assert code == 200 and out["entries"] == []
    code, out = handle("GET", "/api/mission/memory", None,
                       {"q": ["Passwort"], "requester": ["NEXUS"]})
    assert code == 200 and len(out["entries"]) == 1
    assert handle("GET", "/api/mission/memory", None, {"requester": ["Fremd"]})[0] == 400
