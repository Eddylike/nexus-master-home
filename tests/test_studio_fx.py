"""ST02-ST04: Filter-Galerie, Farbanpassung + Histogramm, Social-/Zitat-Generator (lokal, ohne LLM)."""
import numpy as np
import pytest
from PIL import Image

from nexus.studio import api, fx
from nexus.studio.core import Studio, sha256_file


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr("nexus.studio.core.ALLOWED_SOURCE_ROOTS", (str(tmp_path)[:3].upper(),))
    src = tmp_path / "src"; src.mkdir()
    a = np.zeros((240, 320, 3), np.uint8); a[..., 0] = np.linspace(0, 255, 320)[None, :]; a[..., 1] = 120; a[..., 2] = np.linspace(255, 0, 240)[:, None]
    a[::8, :, :] = 255; a[:, ::8, :] = 0  # Kanten fuer Schaerfen/Weichzeichnen
    Image.fromarray(a).save(src / "g.png")
    st = Studio(tmp_path / "studio")
    iid = st.import_files([str(src / "g.png")])["imported"][0]
    return st, iid


def test_st02_all_filters_new_versions_original_untouched(lib):
    st, iid = lib
    orig = st.base / "originals" / st.items[iid]["copy"]; h = sha256_file(orig)
    base = np.asarray(fx._load(st, iid), np.int16)
    for name in fx.FILTERS:
        out = fx.apply_filter(fx._load(st, iid), name)
        assert out.size == (320, 240)
        assert np.abs(np.asarray(out, np.int16) - base).mean() > 0.5, name
    v = fx.studio_filter(st, iid, "vintage")["version"]
    assert (st.base / "versions" / v["file"]).is_file() and v["op"] == "filter_vintage"
    assert fx.studio_filter(st, iid, "bw", preview=True)["preview"].startswith("data:image/jpeg;base64,")
    assert sha256_file(orig) == h and len(st.items[iid]["versions"]) == 1
    with pytest.raises(ValueError):
        fx.apply_filter(fx._load(st, iid), "nope")


def test_st03_adjust_identity_change_and_histogram(lib):
    st, iid = lib
    img = fx._load(st, iid)
    assert np.array_equal(np.asarray(fx.adjust(img, {})), np.asarray(img))
    out = fx.adjust(img, {"exposure": 30, "temperature": 40, "saturation": -50, "shadows": 20})
    assert np.abs(np.asarray(out, np.int16) - np.asarray(img, np.int16)).mean() > 3
    h = fx.histogram(out)
    assert set(h) == {"r", "g", "b"} and all(len(v) == 256 and sum(v) == 320 * 240 for v in h.values())
    r = fx.studio_adjust(st, iid, {"contrast": 25})
    assert r["version"]["params"] == {"contrast": 25.0}
    p = fx.studio_adjust(st, iid, {"tint": 10}, preview=True)
    assert "histogram" in p and len(st.items[iid]["versions"]) == 1


def test_st04_social_formats_and_api(lib):
    st, iid = lib
    for k, size in fx.FORMATS.items():
        assert fx.social("Test Zitat", k).size == size
    code, r = api.handle("POST", "/api/studio/social", {}, {"text": "Gib niemals auf.", "format": "insta_post", "bg_id": iid,
                                                             "author": "NEXUS", "watermark": "@nexus"}, "127.0.0.1", studio=st)
    assert code == 200 and r["size"] == [1080, 1080]
    with Image.open(st.base / "social" / r["file"]) as im:
        assert im.size == (1080, 1080)
    code, r = api.handle("POST", "/api/studio/filter", {}, {"id": iid, "name": "hdr"}, "127.0.0.1", studio=st)
    assert code == 200 and r["version"]["op"] == "filter_hdr"
    assert api.handle("POST", "/api/studio/filter", {}, {"id": iid, "name": "hdr"}, "10.0.0.5", studio=st)[0] == 403
    code, r = api.handle("GET", "/api/studio/fx", {}, None, "127.0.0.1", studio=st)
    assert code == 200 and "vignette" in r["filters"]
