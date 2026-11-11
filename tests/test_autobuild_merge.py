"""20261104_merge: save_backlog merged statt ueberschreibt, atomar, ohne BOM."""
import json

from nexus.autobuild import loop


def _setup(tmp_path, monkeypatch, data):
    f = tmp_path / "backlog.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(loop, "BACKLOG", f)
    monkeypatch.setattr(loop, "check_write", lambda p: p)
    return f


def test_foreign_new_entries_survive(tmp_path, monkeypatch):
    f = _setup(tmp_path, monkeypatch, [{"id": "A", "status": "offen"}, {"id": "B", "status": "offen"}])
    items = loop.load_backlog()
    # fremder Prozess fuegt LT-01 hinzu und aendert B
    disk = json.loads(f.read_text(encoding="utf-8"))
    disk.append({"id": "LT-01", "status": "offen"}); disk[1]["status"] = "erledigt"
    f.write_text(json.dumps(disk), encoding="utf-8")
    items[0]["status"] = "läuft"
    loop.save_backlog(items)
    raw = f.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    got = {i["id"]: i["status"] for i in json.loads(raw.decode("utf-8"))}
    assert got == {"A": "läuft", "B": "erledigt", "LT-01": "offen"}
    assert not (tmp_path / "backlog.json.tmp").exists()


def test_new_own_entry_appended(tmp_path, monkeypatch):
    f = _setup(tmp_path, monkeypatch, [{"id": "A"}])
    items = loop.load_backlog()
    items.append({"id": "SB-AUTO-1"})
    loop.save_backlog(items)
    assert [i["id"] for i in json.loads(f.read_text(encoding="utf-8"))] == ["A", "SB-AUTO-1"]


def test_bom_file_readable(tmp_path, monkeypatch):
    f = tmp_path / "backlog.json"
    f.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"id": "X"}]).encode())
    monkeypatch.setattr(loop, "BACKLOG", f)
    assert loop.load_backlog() == [{"id": "X"}]