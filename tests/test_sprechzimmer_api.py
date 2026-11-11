"""SZ-05: API-Dispatcher /api/sprechzimmer (server.py nutzt http.server, daher handle() direkt)."""

P = "/api/sprechzimmer/sessions"


def _h():
    from nexus.sprechzimmer.api import handle
    return handle


def ask(agent, session):
    return f"{agent}: ok"


def _new(tmp_path, parts=("NEXUS", "Hermes")):
    st, d = _h()("POST", P, {"participants": list(parts), "title": "T"}, tmp_path, ask)
    assert st == 201
    return d


def test_create_list_get_delete(tmp_path):
    h = _h()
    d = _new(tmp_path)
    st, lst = h("GET", P, None, tmp_path, ask)
    assert st == 200 and d["id"] in {s["id"] for s in lst["sessions"]}
    st, g = h("GET", f"{P}/{d['id']}", None, tmp_path, ask)
    assert st == 200 and g["participants"] == ["NEXUS", "Hermes"]
    st, _ = h("DELETE", f"{P}/{d['id']}", None, tmp_path, ask)
    assert st == 200
    assert h("GET", f"{P}/{d['id']}", None, tmp_path, ask)[0] == 404


def test_create_unknown_agent(tmp_path):
    st, _ = _h()("POST", P, {"participants": ["NEXUS", "Fremder"]}, tmp_path, ask)
    assert st == 400


def test_invite_rules(tmp_path):
    h = _h()
    d = _new(tmp_path)
    assert h("POST", f"{P}/{d['id']}/invite", {"agent": "Aether", "by": "Hermes"}, tmp_path, ask)[0] == 403
    assert h("POST", f"{P}/{d['id']}/invite", {"agent": "Fremder", "by": "NEXUS"}, tmp_path, ask)[0] == 400
    st, s = h("POST", f"{P}/{d['id']}/invite", {"agent": "Aether", "by": "NEXUS"}, tmp_path, ask)
    assert st == 200 and "Aether" in s["participants"]


def test_say_autonomous_pause_resume(tmp_path):
    h = _h()
    d = _new(tmp_path)
    st, m = h("POST", f"{P}/{d['id']}/say", {"text": "Wie geht es euch?"}, tmp_path, ask)
    assert st == 200 and m["speaker"] == "Eddie"
    st, r = h("POST", f"{P}/{d['id']}/autonomous/start", {"max_rounds": 3}, tmp_path, ask)
    assert st == 200 and len(r["messages"]) == 3
    assert h("POST", f"{P}/{d['id']}/autonomous/pause", {}, tmp_path, ask) == (200, {"paused": True})
    st, s = h("POST", f"{P}/{d['id']}/resume", {}, tmp_path, ask)
    assert st == 200 and s["status"] == "aktiv" and len(s["messages"]) == 4


def test_unknown_route(tmp_path):
    assert _h()("GET", "/api/sprechzimmer/nix", None, tmp_path, ask)[0] == 404