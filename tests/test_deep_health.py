from nexus.health import deep as deep_mod


def fake_agents(reachable=True, functional="not_probed"):
    from nexus.mission import agents
    data = {}
    for name in agents.CORE_AGENTS:
        data[name] = {"reachable": reachable, "functional": functional, "detail": {}}
    return data


def test_without_probe_never_green(tmp_path, monkeypatch):
    from nexus.mission import agents
    monkeypatch.setattr(deep_mod, "WORKSPACE", tmp_path)
    monkeypatch.setattr(agents, "health", lambda: fake_agents())
    result = deep_mod.deep_health(in_process=True)
    assert result["status"] == "degraded"
    assert result["functional_verified"] is False
    assert result["features"]["workspace"]["ok"] is True
    assert any("Erreichbarkeit" in n for n in result["notes"])


def test_real_probe_can_be_healthy(tmp_path, monkeypatch):
    from nexus.mission import agents
    import nexus.integrations.gpt_local_agent as gpt
    import nexus.integrations.aether_agent as aether
    monkeypatch.setattr(deep_mod, "WORKSPACE", tmp_path)
    monkeypatch.setattr(agents, "health", lambda: fake_agents())
    import nexus.integrations.hermes_agent as hermes
    monkeypatch.setattr(gpt, "ask", lambda *a, **k: {"success": True, "reply": "OK", "model": "qwen3.5:9b"})
    monkeypatch.setattr(aether, "ask", lambda *a, **k: {"success": True, "reply": "OK", "model": "qwen3.5:9b"})
    monkeypatch.setattr(hermes, "ask", lambda *a, **k: {"success": True, "reply": "OK", "agent": "hermes"})
    result = deep_mod.deep_health(probe=True, probe_hermes=True, in_process=True)
    assert result["status"] == "healthy"
    assert result["functional_verified"] is True
    assert result["agents"]["Hermes"]["functional"] == "ok"


def test_probe_without_hermes_is_only_degraded(tmp_path, monkeypatch):
    from nexus.mission import agents
    import nexus.integrations.gpt_local_agent as gpt
    import nexus.integrations.aether_agent as aether
    monkeypatch.setattr(deep_mod, "WORKSPACE", tmp_path)
    monkeypatch.setattr(agents, "health", lambda: fake_agents())
    monkeypatch.setattr(gpt, "ask", lambda *a, **k: {"success": True, "reply": "OK"})
    monkeypatch.setattr(aether, "ask", lambda *a, **k: {"success": True, "reply": "OK"})
    result = deep_mod.deep_health(probe=True, in_process=True)
    assert result["status"] == "degraded"
    assert result["functional_verified"] is True


def test_failed_probe_is_degraded(tmp_path, monkeypatch):
    from nexus.mission import agents
    import nexus.integrations.gpt_local_agent as gpt
    import nexus.integrations.aether_agent as aether
    monkeypatch.setattr(deep_mod, "WORKSPACE", tmp_path)
    monkeypatch.setattr(agents, "health", lambda: fake_agents())
    import nexus.integrations.hermes_agent as hermes
    monkeypatch.setattr(gpt, "ask", lambda *a, **k: {"success": False, "reply": "", "error": "MODEL_DOWN"})
    monkeypatch.setattr(aether, "ask", lambda *a, **k: {"success": True, "reply": "OK"})
    monkeypatch.setattr(hermes, "ask", lambda *a, **k: {"success": True, "reply": "OK", "agent": "hermes"})
    result = deep_mod.deep_health(probe=True, probe_hermes=True, in_process=True)
    assert result["status"] == "degraded"
    assert result["functional_verified"] is False
    assert result["agents"]["NEXUS GPT"]["functional"] == "failed"


def test_unreachable_agents_are_critical(tmp_path, monkeypatch):
    from nexus.mission import agents
    monkeypatch.setattr(deep_mod, "WORKSPACE", tmp_path)
    monkeypatch.setattr(agents, "health", lambda: fake_agents(reachable=False))
    result = deep_mod.deep_health(in_process=True)
    assert result["status"] == "critical"
