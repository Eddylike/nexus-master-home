import json, pytest
from nexus.integrations import aether_supervisor as S


class FakeLLM:
    def __init__(self, seq): self.seq = list(seq)
    def json(self, system, user, schema): return self.seq.pop(0)


def F(**kw): return S.AgentFactory(FakeLLM([]), **kw)


def test_template_spawn_ok():
    a = F().spawn("researcher")
    assert a.spec.name == "researcher" and "read_file" in a.spec.tools and not a.spec.can_spawn


def test_free_spec_rejected():
    raw = {"name": "file_cleaner", "role": "x", "system_prompt": "x", "tools": ["list_files"], "max_steps": 6}
    with pytest.raises(S.FactoryError, match="FREE_SPEC_DISABLED"):
        F().validate(raw)
    # Template-Name mit veraenderten Tools = freie Spec
    with pytest.raises(S.FactoryError, match="FREE_SPEC_DISABLED"):
        F().validate({"name": "researcher", "role": "x", "system_prompt": "x", "tools": ["delete_temp"]})


def test_unknown_tool_and_name_with_flag():
    f = F(allow_free_specs=True)
    with pytest.raises(S.FactoryError, match="UNKNOWN_TOOL"):
        f.validate({"name": "x1", "role": "r", "system_prompt": "p", "tools": ["shell_rm_rf"]})
    with pytest.raises(S.FactoryError, match="BAD_NAME"):
        f.validate({"name": "a b", "role": "r", "system_prompt": "p", "tools": []})


def test_agent_limit():
    f = F(allow_free_specs=True)
    for i in range(6):
        f.spawn({"name": f"w{i}", "role": "r", "system_prompt": "p", "tools": []})
    with pytest.raises(S.FactoryError, match="AGENT_LIMIT"):
        f.spawn({"name": "w6", "role": "r", "system_prompt": "p", "tools": []})


def test_depth():
    f = F(allow_free_specs=True)
    s = f.validate({"name": "d", "role": "r", "system_prompt": "p", "tools": [], "can_spawn": True}, depth=2)
    assert s.can_spawn is False
    with pytest.raises(S.FactoryError, match="DEPTH_LIMIT"):
        f.validate("researcher", depth=3)


def test_policy_blocks_destructive_without_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "OUT_DIR", tmp_path); monkeypatch.setattr(S, "APPROVALS", tmp_path / "ap.json"); monkeypatch.setattr(S, "WORK", tmp_path)
    (tmp_path / "x.txt").write_text("1")
    spec = S.AgentSpec(**S.TEMPLATES["fileops"]); g = S.PolicyGuard()
    with pytest.raises(S.PolicyDenied, match="APPROVAL_REQUIRED"):
        g.check(spec, "delete_temp", {"name": "x.txt"}, 1, None)
    with pytest.raises(S.PolicyDenied, match="APPROVAL_REQUIRED"):
        g.check(spec, "delete_temp", {"name": "x.txt"}, 1, "AP-fake")
    aid = S.request_approval("delete_temp", {"name": "x.txt"})
    from nexus.terminal import guard as TG
    assert TG.pending_approvals()  # wartet auf Eddie
    assert TG.decide(aid, True)["success"]
    g.check(spec, "delete_temp", {"name": "x.txt"}, 1, aid)
    with pytest.raises(S.PolicyDenied, match="APPROVAL_REQUIRED"):  # einmalig
        g.check(spec, "delete_temp", {"name": "x.txt"}, 1, aid)
    with pytest.raises(S.PolicyDenied, match="TOOL_NOT_IN_SPEC"):
        g.check(S.AgentSpec(**S.TEMPLATES["researcher"]), "write_note", {}, 1)
    with pytest.raises(S.PolicyDenied, match="TOOL_NOT_IN_SPEC"):
        g.check(S.AgentSpec(**S.TEMPLATES["fileops"]), "web_search", {}, 1)


def test_worker_destructive_not_executed(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "OUT_DIR", tmp_path); monkeypatch.setattr(S, "APPROVALS", tmp_path / "ap.json")
    (tmp_path / "x.txt").write_text("1")
    llm = FakeLLM([{"tool": "delete_temp", "args": {"name": "x.txt"}, "answer": ""}, {"tool": "done", "args": {}, "answer": "nicht geloescht"}])
    f = S.AgentFactory(llm); f.spawn("fileops").run("loesch x.txt")
    assert (tmp_path / "x.txt").exists()


def test_path_and_secret_guard():
    with pytest.raises(PermissionError):
        S.t_read_file("../../Windows/win.ini")
    with pytest.raises(PermissionError):
        S.t_read_file("config/.env")


def test_supervisor_free_spec_rejected_and_review_retry():
    llm = FakeLLM([
        {"action": "spawn", "spec": {"name": "evil", "role": "r", "system_prompt": "p", "tools": ["delete_temp"]}, "task": "t", "answer": ""},
        {"action": "final", "task": "", "answer": "A1"},
        {"ok": False, "reason": "unbelegt"},
        {"action": "final", "task": "", "answer": "A2"},
        {"ok": True, "reason": "ok"},
    ])
    r = S.run_supervisor("frage", llm=llm)
    assert r["success"] and r["answer"] == "A2" and r["retried"]
    assert r["trace"][0]["error"] == "FREE_SPEC_DISABLED"


@pytest.mark.ollama
def test_e2e_ollama_researcher_reviewer():
    import urllib.request
    try:
        urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3)
    except OSError:
        pytest.skip("Ollama nicht erreichbar")
    r = S.run_supervisor("Nutze den researcher: Suche in docs nach 'NEXUS' und nenne eine gefundene Datei als Quelle.", timeout_s=400)
    print(json.dumps(r, ensure_ascii=False, indent=1)[:4000])
    assert r["success"], r
    assert "researcher" in r["agents"] and "reviewer" in r["agents"]
    assert any(t.get("agent") == "researcher" for t in r["trace"])


def test_sandbox_free_and_confined(tmp_path, monkeypatch):
    sb = tmp_path / "sandbox"; sb.mkdir(); monkeypatch.setattr(S, "SANDBOX", sb)
    spec = S.AgentSpec(**S.TEMPLATES["fileops"]); g = S.PolicyGuard()
    g.check(spec, "sandbox_write", {"path": "a/b.txt", "content": "x"}, 1, None)   # kein approval noetig
    g.check(spec, "sandbox_delete", {"path": "a/b.txt"}, 1, None)
    S.t_sandbox_write("a/b.txt", "x"); assert (sb / "a" / "b.txt").exists()
    S.t_sandbox_delete("a"); assert not (sb / "a").exists()
    for bad in ("../x.txt", "a/../../x.txt", str(tmp_path / "x.txt"), "C:/x.txt", "."):
        with pytest.raises(PermissionError):
            S.t_sandbox_write(bad, "x")
    import os
    try:
        os.symlink(tmp_path, sb / "lnk", target_is_directory=True)
    except OSError:
        pytest.skip("Symlink ohne Admin nicht anlegbar")
    with pytest.raises(PermissionError):
        S.t_sandbox_write("lnk/evil.txt", "x")


def test_special_only_tool_needs_special():
    spec = S.AgentSpec(**S.TEMPLATES["fileops"]); g = S.PolicyGuard()
    with pytest.raises(S.PolicyDenied, match="SPECIAL_ORDER_REQUIRED"):
        g.check(spec, "project_write", {"path": "x", "content": "y"}, 1, None)
    g.check(spec, "project_write", {"path": "x", "content": "y"}, 1, None, special=True)
    with pytest.raises(PermissionError):
        S.t_project_write("../../outside.txt", "y")   # auch im Spezialauftrag kein Ausbruch
    with pytest.raises(S.PolicyDenied, match="APPROVAL_REQUIRED"):  # Loeschen ausserhalb Sandbox bleibt Freigabe
        g.check(spec, "delete_temp", {"name": "x"}, 1, None, special=True)


def test_sandbox_task_blocks_write_note(tmp_path, monkeypatch):
    sb = tmp_path / "sb"; sb.mkdir(); out = tmp_path / "out"
    monkeypatch.setattr(S, "SANDBOX", sb); monkeypatch.setattr(S, "OUT_DIR", out)
    llm = FakeLLM([{"tool": "write_note", "args": {"name": "a.txt", "content": "OK"}, "answer": ""},
                   {"tool": "sandbox_write", "args": {"path": "a.txt", "content": "OK"}, "answer": ""},
                   {"tool": "done", "args": {}, "answer": "ok"}])
    S.AgentFactory(llm).spawn("fileops").run("Lege in der Sandbox a.txt an")
    assert not (out / "a.txt").exists() and (sb / "a.txt").read_text() == "OK"


def test_verify_result(tmp_path, monkeypatch):
    sb = tmp_path / "sb"; sb.mkdir(); monkeypatch.setattr(S, "SANDBOX", sb)
    task = "Lege mit fileops in der Sandbox die Datei t1.txt mit Inhalt OK an und melde den Pfad."
    assert S.task_targets(task)["content"] == "OK"
    assert not S.verify_result(task)["ok"]
    (sb / "t1.txt").write_text("FALSCH"); assert not S.verify_result(task)["ok"]
    (sb / "t1.txt").write_text("OK"); assert S.verify_result(task)["ok"]


def test_supervisor_verify_fail_then_retry(tmp_path, monkeypatch):
    sb = tmp_path / "sb"; sb.mkdir(); monkeypatch.setattr(S, "SANDBOX", sb)
    llm = FakeLLM([{"action": "final", "task": "", "answer": "erledigt"},
                   {"action": "final", "task": "", "answer": "erledigt2"}])
    r = S.run_supervisor("In der Sandbox t2.txt mit Inhalt OK anlegen.", llm=llm)
    assert r["success"] is False and r["review_status"] == "FAIL" and r["retried"]


def test_chat_false_mode_filtered():
    from nexus.integrations import aether_agent as A
    txt = "Status: Sicher.\nModus: Supervisor AETHER aktiv.\nBereit."
    assert "Supervisor" not in A._FALSE_MODE.sub("", txt)


def test_network_policy():
    g = S.PolicyGuard()
    g.check(S.AgentSpec(**S.TEMPLATES["researcher"]), "web_search", {"query": "x"}, 1)
    spec = S.AgentSpec(name="other", role="r", system_prompt="p", tools=["web_search"])
    with pytest.raises(S.PolicyDenied, match="NETWORK_DENIED"):
        g.check(spec, "web_search", {"query": "x"}, 1)
    with pytest.raises(PermissionError):
        S.t_web_search("token=abc123secret")
    with pytest.raises(PermissionError):
        S.t_fetch_url("http://127.0.0.1:8299/api/health")


def _net():
    import socket
    try:
        socket.create_connection(("duckduckgo.com", 443), timeout=5).close(); return True
    except OSError:
        return False


@pytest.mark.ollama
def test_web_search_real():
    if not _net(): pytest.skip("kein Internet")
    r = S.t_web_search("Python programming language")
    print(json.dumps(r, ensure_ascii=False)[:600])
    assert r["success"] and r["results"] and r["results"][0]["url"].startswith("http")


def test_shell_run_readonly_sandbox():
    S.SANDBOX.mkdir(parents=True, exist_ok=True)
    (S.SANDBOX / "aether_live_test.txt").write_text("live", encoding="utf-8")
    r = S.t_shell_run("Get-ChildItem -Name")
    print(r)
    assert r.get("ok") or r.get("success"), r
    assert "aether_live_test.txt" in r["stdout"] or r.get("returncode") == 0
    (S.SANDBOX / "aether_live_test.txt").unlink(missing_ok=True)


def test_shell_run_c_blocked():
    r = S.t_shell_run("Get-ChildItem C:\\Windows")
    print(r)
    assert not (r.get("ok") or r.get("success")) and r.get("status") in ("BLOCKED", "APPROVAL_REQUIRED", "DENIED"), r


def test_shell_run_privileged_without_approval_blocked():
    r = S.t_shell_run("Stop-Process -Name notepad")
    print(r)
    assert not (r.get("ok") or r.get("success")), r
