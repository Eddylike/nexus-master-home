from nexus.games.haus5t import agents


def _fake_success(message, system=None):
    _fake_success.calls.append((message, system))
    return {"success": True, "reply": "Eigene Antwort", "error": None}


_fake_success.calls = []


def test_figures_are_separate_persistent_instances(tmp_path, monkeypatch):
    import nexus.integrations.gpt_local_agent as gpt

    _fake_success.calls = []
    monkeypatch.setattr(gpt, "ask", _fake_success)
    monkeypatch.setattr(agents, "MEMORY_DIR", tmp_path)

    for name in ("Mira", "Bruno", "Lina"):
        text, ok, source = agents.ask_agent(name, "prompt-" + name, timeout=5)
        assert ok and text == "Eigene Antwort"
        assert source.startswith("ollama-persistent/")

    assert len(_fake_success.calls) == 3
    systems = [system or "" for _, system in _fake_success.calls]
    assert "Mira" in systems[0] and "Bruno" in systems[1] and "Lina" in systems[2]
    assert "Bruno" not in systems[0] and "Mira" not in systems[1]

    for name in ("Mira", "Bruno", "Lina"):
        path = agents.figure_memory_path(name)
        assert path.is_file()
        assert "Eigene Antwort" in path.read_text(encoding="utf-8")

    _fake_success.calls = []
    agents.ask_agent("Mira", "zweiter Zug", timeout=5)
    mira_system = _fake_success.calls[0][1] or ""
    assert "Eigene Antwort" in mira_system
    assert "Bruno" not in mira_system


def test_figure_failure_is_honest_and_not_stored(tmp_path, monkeypatch):
    import nexus.integrations.gpt_local_agent as gpt

    monkeypatch.setattr(agents, "MEMORY_DIR", tmp_path)
    monkeypatch.setattr(gpt, "ask", lambda *a, **k: {"success": False, "error": "OLLAMA_DOWN"})

    text, ok, source = agents.ask_agent("Mira", "x", timeout=5)
    assert ok is False and text == ""
    assert "OLLAMA_DOWN" in source
    assert not agents.figure_memory_path("Mira").exists()
