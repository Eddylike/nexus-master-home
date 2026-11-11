"""NEXUS Studio: Fehlerfaelle, Berechtigungen, Neustart, Original-Schutz (ohne LLM)."""
import json
import os

import pytest
from PIL import Image

from nexus.studio import api
from nexus.studio.core import Studio, StudioError, hamming


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr("nexus.studio.core.ALLOWED_SOURCE_ROOTS", (str(tmp_path)[:3].upper(),))
    src = tmp_path / "src"; src.mkdir()
    Image.new("RGB", (320, 240), (200, 120, 40)).save(src / "a.jpg")
    im = Image.new("RGB", (320, 240), (20, 120, 200))
    for x in range(0, 320, 20):
        for y in range(240):
            im.putpixel((x, y), (255, 255, 255))
    im.save(src / "b.jpg")
    im.save(src / "b_kopie.png")
    return Studio(tmp_path / "studio"), src


def test_import_hash_readonly_and_restart(lib):
    st, src = lib
    r = st.import_files([str(src / "a.jpg"), str(src / "b.jpg"), str(src / "b.jpg"), str(src / "nope.jpg")])
    assert len(r["imported"]) == 2 and len(r["errors"]) == 1
    iid = r["imported"][0]
    cp = st.base / "originals" / st.items[iid]["copy"]
    assert not os.access(cp, os.W_OK)
    v = st.edit(iid, "warm")
    assert (st.base / "versions" / v["file"]).is_file()
    assert st.verify()["all_ok"] is True
    st2 = Studio(st.base)  # Neustart: Index wird von Platte geladen
    assert set(st2.items) == set(st.items) and st2.items[iid]["versions"][0]["op"] == "warm"


def test_duplicates_and_search(lib):
    st, src = lib
    st.import_files([str(src / "a.jpg"), str(src / "b.jpg"), str(src / "b_kopie.png")])
    d = st.duplicates()
    assert any(p["type"] == "dublette" for p in d["pairs"])
    k = next(iter(st.items)); st.items[k].update(category="Industrie", keywords=["Maschine"])
    assert [i["id"] for i in st.search("maschine")] == [k]
    assert st.search(category="Natur & Landschaft") == []
    assert hamming("ffff", "0000") == 16


def test_errors_blocked_and_rect(lib):
    st, src = lib
    iid = st.import_files([str(src / "a.jpg")])["imported"][0]
    for op, code in (("outpaint", "BLOCKED:outpaint"), ("rm -rf", "UNKNOWN_OP"), ("remove_object", "RECT_REQUIRED")):
        with pytest.raises(StudioError) as e:
            st.edit(iid, op)
        assert e.value.code.startswith(code)
    assert st.edit(iid, "remove_object", rect=[10, 10, 40, 40])["n"] == 1
    with pytest.raises(StudioError):
        st.get("unbekannt")


def test_permissions_source_and_approval(lib, tmp_path):
    st, src = lib
    st.import_files([str(src / "a.jpg")])
    inside = st.base / "originals" / next(iter(st.items.values()))["copy"]
    assert st.import_files([str(inside)])["errors"][0]["error"] == "SOURCE_INSIDE_LIBRARY"
    st.items[next(iter(st.items))]["category"] = "Industrie"
    p = st.propose_folders()
    with pytest.raises(StudioError):
        st.apply_folders(p["nonce"], confirm=False)
    with pytest.raises(StudioError):
        st.apply_folders("falsch", confirm=True)
    assert st.apply_folders(p["nonce"], confirm=True)["copied"] == 1
    with pytest.raises(StudioError):  # Freigabe nur einmal gueltig
        st.apply_folders(p["nonce"], confirm=True)


def test_api_loopback_only(lib):
    st, _ = lib
    assert api.handle("GET", "/api/studio/status", {}, None, "192.168.1.50", st)[0] == 403
    code, body = api.handle("GET", "/api/studio/status", {}, None, "127.0.0.1", st)
    assert code == 200 and body["stats"]["total"] == 0
    assert api.handle("POST", "/api/studio/edit", {}, {"id": "x", "op": "auto"}, "127.0.0.1", st)[0] == 404
    assert api.handle("POST", "/api/studio/organize/apply", {}, {"nonce": "x"}, "127.0.0.1", st)[0] == 403


def test_corrupt_index_recovers(tmp_path):
    base = tmp_path / "s"; base.mkdir()
    (base / "index.json").write_text("{kaputt", encoding="utf-8")
    st = Studio(base)
    assert st.items == {} and any(p.name.startswith("index.corrupt_") for p in base.iterdir())