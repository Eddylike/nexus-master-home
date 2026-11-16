"""2026-11-16: gleiche Web-/Browser-Rechte fuer NEXUS, Hermes, NEXUS GPT, AETHER (offline)."""
import pytest
from nexus.integrations import partner_tools as pt


@pytest.fixture(autouse=True)
def _tmp_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(pt, 'AUDIT', tmp_path / 'audit.jsonl')  # echtes Audit-Log nicht verschmutzen


def test_rights_equal_for_all_agents():
    r = pt.rights()
    assert set(r["agents"]) == {"NEXUS", "Hermes", "NEXUS-GPT", "AETHER"}
    tools = {tuple(v["web_tools"]) for v in r["agents"].values()}
    assert len(tools) == 1 and set(next(iter(tools))) == {"web_search", "fetch_url", "open_url", "close_opened"}
    assert r["equal"] is True


def test_limits_same_for_all(monkeypatch):
    for a in ("nexus", "hermes", "gpt", "aether"):
        assert pt.run(a, "open_url", {"url": "file:///C:/Windows"})["error"] == "URL_DENIED"
        assert pt.run(a, "open_url", {"url": "https://x.de/?token=abc"})["error"] == "URL_DENIED"
        assert pt.run(a, "web_search", {"query": "mein password ist"})["error"] == "SECRET_IN_QUERY"
        assert pt.run(a, "shell", {})["error"] == "TOOL_NOT_SHARED"
    assert pt.run("fremder", "web_search", {"query": "x"})["error"] == "AGENT_UNKNOWN"


def test_open_url_uses_same_path(monkeypatch):
    opened = []

    class FakeOS:
        def open_url(self, u):
            opened.append(u)
            return {"success": True, "method": "fake"}

    import nexus.computer.os_actions as oa
    monkeypatch.setattr(oa, "get_os_actions", lambda: FakeOS())
    for a in ("nexus", "hermes", "gpt", "aether"):
        assert pt.run(a, "open_url", {"url": "example.com"})["success"] is True
    assert opened == ["https://example.com"] * 4


def test_agents_expose_shared_tools():
    from nexus.integrations import hermes_tools as ht, aether_supervisor as S
    names = {t["function"]["name"] for t in ht.TOOLS}
    assert set(pt.SHARED_TOOLS) <= names
    assert ht.wants_tools("suche im internet nach dem wetter")
    assert {"web_search", "fetch_url", "open_url"} <= set(S.TEMPLATES["researcher"]["tools"])
    assert "open_url" in S.TOOLS


def test_gpt_tool_summary_does_not_route_to_web(monkeypatch):
    from nexus.integrations import gpt_local_agent as g
    seen = []
    monkeypatch.setattr(g, "_web", lambda text: seen.append(text) or None)
    monkeypatch.setattr(g, "_ask_base", lambda *a, **k: {"success": True, "reply": '{"command": "", "why": "x"}'})
    g.tool_ask("zeige url liste")
    assert seen == ["zeige url liste"]  # nur einmal (Einstieg), nicht fuer interne ask-Aufrufe