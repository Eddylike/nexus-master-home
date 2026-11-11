"""OMEGA Agent tests: echte Pipeline, HITL-Pause/Resume, Code-Sandbox.

Der Websuche-/Slack-Teil nutzt einen Fake-Invoker, damit der Test offline und
deterministisch bleibt. Der Code-Teil nutzt die echte Registry + lokale Sandbox.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _omega_home(tmp_path, monkeypatch):
    home = tmp_path / "omega"
    monkeypatch.setenv("OMEGA_HOME", str(home))
    monkeypatch.setenv("OMEGA_DB", str(home / "omega.db"))
    monkeypatch.setenv("OMEGA_HITL", "confirm")
    monkeypatch.setenv("OMEGA_DISABLE_LLM", "1")
    monkeypatch.setenv("OMEGA_DISABLE_DOCKER", "1")
    monkeypatch.delenv("OMEGA_ALLOW_LOCAL_EXEC", raising=False)
    from nexus.omega import events, rag

    events._READY.clear()
    rag._INIT.clear()
    yield


class FakeInvoker:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.pending_params: dict | None = None

    def __call__(self, name, params=None, *, run_id=None, approval_id=None, ctx=None):
        params = dict(params or {})
        self.calls.append({"name": name, "params": params, "approval_id": approval_id})
        if name == "web_search":
            return {
                "success": True, "status": "OK", "source": "fake-web", "count": 2,
                "results": [
                    {"title": "AI Update Eins", "url": "https://example.com/ai-1",
                     "snippet": "Ein wichtiger AI-Meilenstein wurde heute veroeffentlicht."},
                    {"title": "AI Update Zwei", "url": "https://example.com/ai-2",
                     "snippet": "Ein zweiter Bericht beschreibt neue Modelle und Risiken."},
                ],
            }
        if name == "fetch_page":
            return {
                "success": True, "status": "OK", "url": params.get("url"),
                "title": "Seite", "suspicious": False, "chars": 120,
                "text": "Die Seite berichtet ueber AI-Modelle, Risiken und neue Forschung.",
            }
        if name == "slack_post":
            if approval_id is None:
                self.pending_params = params
                return {"success": False, "status": "APPROVAL_REQUIRED",
                        "approval_id": "APR-FAKE", "error": "Freigabe erforderlich"}
            if approval_id == "APR-FAKE" and params == self.pending_params:
                return {"success": True, "status": "OK", "transport": "fake",
                        "channel": "#test", "ts": "123.456"}
            return {"success": False, "status": "APPROVAL_INVALID",
                    "error": "Parameter oder Freigabe ungueltig"}
        raise AssertionError("unexpected tool: " + name)


def test_research_and_slack_pauses_then_resumes():
    from nexus.omega.agent import OmegaAgent

    fake = FakeInvoker()
    agent = OmegaAgent(invoke=fake)
    goal = ("Recherchiere die wichtigsten AI-News und poste eine Zusammenfassung "
            "in Slack.")
    first = agent.run(goal)
    assert first["status"] == "awaiting_approval"
    assert first["approval_id"] == "APR-FAKE"
    assert [t["step"] for t in first["trace"]][:3] == ["research", "compose", "slack"]
    assert fake.pending_params is not None

    slack_first = [c for c in fake.calls if c["name"] == "slack_post"][0]
    assert slack_first["approval_id"] is None
    assert "Web-Update" in slack_first["params"]["text"]
    assert "example.com/ai-1" in slack_first["params"]["text"]

    resumed = agent.resume(first["run_id"], approval_id="APR-FAKE")
    assert resumed["success"] is True
    assert resumed["status"] == "succeeded"
    assert "Slack-Post" in resumed["final"]
    assert "#test" in resumed["final"]
    slack_calls = [c for c in fake.calls if c["name"] == "slack_post"]
    assert slack_calls[-1]["approval_id"] == "APR-FAKE"
    assert slack_calls[-1]["params"] == fake.pending_params
    assert resumed["sources"]


def test_code_pipeline_uses_injected_llm_and_real_sandbox(monkeypatch):
    from nexus.omega.agent import OmegaAgent

    monkeypatch.setenv("OMEGA_ALLOW_LOCAL_EXEC", "1")

    def llm(prompt: str) -> str:
        assert "Python" in prompt
        return "```python\nprint('omega-ok')\n```"

    result = OmegaAgent(llm=llm).run("Schreibe ein Python-Skript und fuehre es aus.")
    assert result["status"] == "succeeded", result
    assert "omega-ok" in result["final"]
    calls = {c["tool"]: c for c in result["tool_calls"]}
    assert calls.get("run_python", {}).get("status") == "OK"


def test_code_pipeline_without_llm_fails_honestly():
    from nexus.omega.agent import OmegaAgent

    result = OmegaAgent().run("Schreibe ein Python-Skript und fuehre es aus.")
    assert result["status"] == "failed"
    assert "LLM" in (result.get("error") or result.get("final") or "")
