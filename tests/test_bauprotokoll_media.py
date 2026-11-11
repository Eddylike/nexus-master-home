"""Phase 7 Abnahme: Medienindex (Duplikate/Suche/Export) und Studio (3 Vorlagen)."""
from __future__ import annotations

import json

from nexus.bauprotokoll.media import MediaIndex
from nexus.bauprotokoll.studio import Studio


def test_media_index_dedup_search_export(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "sonnenuntergang.jpg").write_bytes(b"\xff\xd8fakejpg")
    (media / "kopie.jpg").write_bytes(b"\xff\xd8fakejpg")
    (media / "notizen.txt").write_text("hallo", encoding="utf-8")

    idx = MediaIndex(db_path=tmp_path / "media.db", root=tmp_path)
    result = idx.index_dir(media)
    assert result["indexed"] == 3
    assert result["duplicates"] == 1
    dups = idx.duplicates()
    assert len(dups) == 1 and dups[0]["count"] == 2
    assert idx.stats()["total"] == 3
    assert idx.search("sonnenuntergang")[0]["path"].endswith("sonnenuntergang.jpg")

    out = tmp_path / "stock.json"
    entries = idx.export_stock(out)
    assert out.is_file() and len(entries) == 3
    assert entries[0]["titel"] and "keywords" in entries[0]


def test_media_tags_merge(tmp_path):
    f = tmp_path / "a.png"
    f.write_bytes(b"png")
    idx = MediaIndex(db_path=tmp_path / "m.db", root=tmp_path)
    idx.index_file(f, tags="natur")
    idx.add_tags("a.png", "gruen, natur")
    item = idx.all()[0]
    assert item["tag_list"] == ["natur", "gruen"]


def test_studio_creates_three_templates_and_exports(tmp_path):
    st = Studio(templates_dir=tmp_path / "templates", output_dir=tmp_path / "out",
                config=type("C", (), {"root": tmp_path})())
    created = st.ensure_templates()
    assert sorted(created) == ["portfolio", "social_post", "thumbnail"]
    assert len(st.list_templates()) == 3

    paths = st.batch_export([
        {"template": "social_post", "titel": "Post", "untertitel": "A"},
        {"template": "thumbnail", "titel": "Thumb", "untertitel": "B"},
        {"template": "portfolio", "titel": "Port", "untertitel": "C"},
    ])
    assert len(paths) == 3
    for p in paths:
        text = open(p, encoding="utf-8").read()
        assert text.startswith("<!doctype html>")
        assert "#38bdf8" in text
        assert "{{" not in text


def test_studio_escapes_title(tmp_path):
    st = Studio(templates_dir=tmp_path / "t", output_dir=tmp_path / "o",
                config=type("C", (), {"root": tmp_path})())
    path = st.render("social_post", "<script>alert(1)</script>")
    text = path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text
