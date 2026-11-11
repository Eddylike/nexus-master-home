# -*- coding: utf-8 -*-
"""PB-02: Loader + Filter."""
from __future__ import annotations
import json
from pathlib import Path

from nexus.promptbib import loader as L


def _write_entry(dirpath: Path, entry: dict) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"{entry['id']}.json").write_text(
        json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_load_and_filter(tmp_path):
    root = tmp_path / "skills"
    e1 = {
        "id": "text-zusammenfassen",
        "typ": "prompt",
        "kategorie": "schreiben",
        "tags": ["kurz", "deutsch"],
        "titel": "Zusammenfassen",
        "beschreibung": "Bullets",
        "version": "1.0",
        "prompt": "Fasse zusammen: {{input}}",
    }
    e2 = {
        "id": "sql-generator",
        "typ": "prompt",
        "kategorie": "code",
        "tags": ["code", "sql"],
        "titel": "SQL",
        "beschreibung": "SQL aus Text",
        "version": "1.0",
        "prompt": "Erzeuge SQL fuer: {{input}}",
    }
    _write_entry(root / "schreiben", e1)
    _write_entry(root / "code", e2)
    (root / "meta").mkdir(parents=True, exist_ok=True)
    (root / "meta" / "ignore.json").write_text("{}", encoding="utf-8")

    all_e = L.load_all(root)
    assert {e["id"] for e in all_e} == {"text-zusammenfassen", "sql-generator"}
    only_write = L.filter_entries(all_e, kategorie="schreiben")
    assert [e["id"] for e in only_write] == ["text-zusammenfassen"]
    only_sql = L.filter_entries(all_e, tag="sql")
    assert [e["id"] for e in only_sql] == ["sql-generator"]


def test_skills_root_name():
    p = L.skills_root()
    assert p.name == "skills"
