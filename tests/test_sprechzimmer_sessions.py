"""SZ-03: Sitzung anlegen, speichern, laden, fortsetzen."""
import json

import pytest


def _s():
    from nexus.sprechzimmer import session_manager
    return session_manager


def test_create_persists(tmp_path):
    s = _s()
    ses = s.create_session(["NEXUS", "Hermes"], tmp_path, title="Erste")
    assert ses["id"] and ses["participants"] == ["NEXUS", "Hermes"]
    assert ses["messages"] == [] and ses["status"] == "aktiv"
    assert (tmp_path / "sessions" / f"{ses['id']}.json").exists()


def test_save_and_load_roundtrip(tmp_path):
    s = _s()
    ses = s.create_session(["NEXUS", "Aether"], tmp_path)
    msg = s.add_message(ses, "Aether", "Mir geht es gut")
    assert msg["speaker"] == "Aether" and msg["text"] == "Mir geht es gut"
    p = s.save_session(ses, tmp_path)
    assert json.loads(p.read_text(encoding="utf-8"))["messages"][0]["text"] == "Mir geht es gut"
    back = s.load_session(ses["id"], tmp_path)
    assert back["messages"][0]["speaker"] == "Aether"


def test_load_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        _s().load_session("gibtsnicht", tmp_path)


def test_list_sessions(tmp_path):
    s = _s()
    a = s.create_session(["NEXUS"], tmp_path, title="A")
    b = s.create_session(["Hermes"], tmp_path, title="B")
    ids = {x["id"] for x in s.list_sessions(tmp_path)}
    assert {a["id"], b["id"]} <= ids


def test_resume(tmp_path):
    s = _s()
    ses = s.create_session(["NEXUS", "Hermes"], tmp_path)
    s.add_message(ses, "Hermes", "Hallo")
    ses["status"] = "pausiert"; ses["paused"] = True; ses["interrupt"] = True
    s.save_session(ses, tmp_path)
    r = s.resume_session(ses["id"], tmp_path)
    assert r["status"] == "aktiv" and not r.get("paused") and not r.get("interrupt")
    assert r["messages"][0]["text"] == "Hallo"
    assert s.load_session(ses["id"], tmp_path)["status"] == "aktiv"