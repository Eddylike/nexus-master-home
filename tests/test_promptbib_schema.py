# -*- coding: utf-8 -*-
"""PB-01/PB-04: Schema-Validierung."""
from __future__ import annotations
import json
from pathlib import Path

from nexus.promptbib import schema as S

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "workspace" / "promptbib" / "skills" / "meta"


def test_meta_files_exist():
    assert (META / "taxonomie.json").is_file()
    assert (META / "schema.json").is_file()
    tax = json.loads((META / "taxonomie.json").read_text(encoding="utf-8"))
    assert "schreiben" in tax["kategorien"]
    assert "pflichtfelder" in tax


def test_validate_ok_prompt():
    entry = {
        "id": "email-antwort",
        "typ": "prompt",
        "kategorie": "schreiben",
        "tags": ["formal", "deutsch"],
        "titel": "Professionelle E-Mail-Antwort",
        "beschreibung": "Hoefliche Antwort.",
        "version": "1.0",
        "prompt": "Antworte professionell.\n\nE-Mail:\n{{input}}",
    }
    errs = S.validate_entry(entry)
    assert errs == []


def test_validate_rejects_missing_and_bad_id():
    bad = {
        "id": "Bad ID",
        "typ": "prompt",
        "kategorie": "schreiben",
        "tags": ["x"],
        "titel": "t",
        "beschreibung": "b",
        "version": "1.0",
        "prompt": "ohne platzhalter",
    }
    errs = S.validate_entry(bad)
    assert errs, "muss Fehler melden"


def test_schema_path_points_to_meta():
    p = S.schema_path()
    assert p.name == "schema.json"
    assert "promptbib" in str(p).replace("\\", "/")
