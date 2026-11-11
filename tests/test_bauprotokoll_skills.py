"""Phase 3 Abnahme: 8 Start-Skills, Versionierung, Auswahl (mind. 8/10)."""
from __future__ import annotations

import json

from nexus.bauprotokoll.skills import STARTER_SKILLS, SkillLibrary


def test_starters_created_with_contract(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    created = lib.ensure_starters()
    assert sorted(created) == sorted(STARTER_SKILLS)
    for name in STARTER_SKILLS:
        folder = tmp_path / "skills" / name
        assert (folder / "skill.json").is_file()
        assert (folder / "prompt.md").read_text(encoding="utf-8").strip()
        contract = json.loads((folder / "tests" / "contract.json").read_text(encoding="utf-8"))
        assert contract["erwartete_ausgabe_felder"]


def test_selection_matches_10_examples_at_least_8(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    lib.ensure_starters()
    cases = [
        ("Bitte pruefe diesen Diff auf Risiken", "code_review"),
        ("Der Test stuerzt mit einem Traceback ab, repariere den Bug", "bugfix"),
        ("Baue eine Single-File HTML Landingpage mit CSS", "html_single_file"),
        ("Schreibe ein Python-Modul fuer die Steuerung", "python_module"),
        ("Recherchiere Quellen zum Thema Solarstrom", "recherche"),
        ("Fasse diesen langen Text zusammen", "zusammenfassen"),
        ("Schreibe Pytest-Tests fuer die Funktion", "test_schreiben"),
        ("Erstelle eine README-Dokumentation", "doku_schreiben"),
        ("Ich brauche eine Webseite als einzelne Datei mit JavaScript", "html_single_file"),
        ("Pruefe die Code-Qualitaet im Diff", "code_review"),
    ]
    ok = 0
    misses = []
    for task, expected in cases:
        got = lib.select(task, top_k=1)[0]["name"]
        if got == expected:
            ok += 1
        else:
            misses.append((task, expected, got))
    assert ok >= 8, f"nur {ok}/10 korrekt: {misses}"


def test_versioning_keeps_old_version(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    meta = {
        "name": "demo", "version": "1.0.0", "beschreibung": "alt",
        "benoetigte_tools": [], "modell": "router",
        "eingabe": {"typ": "text"}, "ausgabe": {"typ": "text"},
    }
    lib.save(meta, "prompt v1")
    meta["version"] = "1.1.0"
    meta["beschreibung"] = "neu"
    lib.save(meta, "prompt v2")
    assert (tmp_path / "skills" / "demo" / "versions" / "1.0.0" / "prompt.md").read_text(
        encoding="utf-8") == "prompt v1"
    assert lib.get("demo")["beschreibung"] == "neu"


def test_same_version_change_is_rejected(tmp_path):
    lib = SkillLibrary(tmp_path / "skills")
    meta = {
        "name": "demo", "version": "1.0.0", "beschreibung": "a",
        "benoetigte_tools": [], "modell": "router",
        "eingabe": {"typ": "text"}, "ausgabe": {"typ": "text"},
    }
    lib.save(meta, "p1")
    meta["beschreibung"] = "b"
    try:
        lib.save(meta, "p2")
    except ValueError:
        pass
    else:
        raise AssertionError("Aenderung ohne Versionserhoehung muss abgelehnt werden")
