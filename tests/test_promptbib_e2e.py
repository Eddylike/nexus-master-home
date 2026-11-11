# -*- coding: utf-8 -*-
"""PB-ABN (20261109): Creator End-to-End gegen eine Kopie der echten Bibliothek + Fehlerpfade (LLM gemockt)."""
from __future__ import annotations
import json
import shutil
from pathlib import Path

import pytest

from nexus.promptbib import creator as C, loader as L, schema as S

REAL = Path(__file__).resolve().parents[1] / "workspace" / "promptbib" / "skills"


@pytest.fixture()
def lib(tmp_path):
    dst = tmp_path / "skills"
    shutil.copytree(REAL, dst)
    return dst


def _entry(**kw):
    e = {"id": "code-tests-schreiben", "typ": "prompt", "kategorie": "code", "tags": ["code", "test"],
         "titel": "Tests schreiben", "beschreibung": "Erzeugt pytest-Tests.", "version": "1.0",
         "prompt": "Rolle: Tester.\nAufgabe: Schreibe pytest-Tests.\n\nCode:\n{{input}}"}
    e.update(kw)
    return e


def test_real_library_entries_all_valid():
    for e in L.load_all(REAL):
        assert S.validate_entry(e) == [], e.get("id")


def test_e2e_real_metaprompt_and_taxonomie(lib):
    seen = {}

    def llm(prompt):
        seen["p"] = prompt
        return "```json\n" + json.dumps(_entry()) + "\n```"
    out = C.create_from_keywords("code, python, test", llm=llm, root=lib)
    assert out["ok"] is True, out
    assert "{{input}}" not in seen["p"] and "Stichwoerter: code, python, test" in seen["p"]
    assert "Vorgegebene Kategorie: code" in seen["p"]
    p = Path(out["path"])
    assert p.parent == lib / "code" and p.read_bytes()[:3] != b"\xef\xbb\xbf"
    assert any(e["id"] == "code-tests-schreiben" for e in L.filter_entries(L.load_all(lib), kategorie="code"))


def test_rejects_invalid_and_does_not_save(lib):
    out = C.create_from_keywords("code, test", llm=lambda p: json.dumps(_entry(id="Bad ID", prompt="ohne")), root=lib)
    assert out["ok"] is False and out["error"] == "SCHEMA_UNGUELTIG" and out["errors"]
    assert not list((lib / "code").glob("bad*"))


def test_rejects_non_json(lib):
    out = C.create_from_keywords("code", llm=lambda p: "keine Ahnung", root=lib)
    assert out["ok"] is False and out["error"] == "KEIN_JSON"


def test_category_mismatch_and_existing_id(lib):
    out = C.create_from_keywords("lernen, quiz", llm=lambda p: json.dumps(_entry()), root=lib)
    assert out["error"] == "KATEGORIE_ABWEICHEND"
    out = C.create_from_keywords("code", llm=lambda p: json.dumps(_entry(id="sql-generator")), root=lib)
    assert out["error"] == "ID_EXISTIERT"


def test_empty_keywords_and_llm_error(lib):
    assert C.create_from_keywords("  ", llm=lambda p: "{}", root=lib)["error"] == "KEINE_STICHWOERTER"

    def boom(p):
        raise RuntimeError("ollama_down")
    out = C.create_from_keywords("code", llm=boom, root=lib)
    assert out["error"] == "LLM_FEHLER" and "ollama_down" in out["errors"][0]


def test_taxonomie_path_and_load():
    assert C.taxonomie_path().name == "taxonomie.json"
    tax = C.load_taxonomie()
    assert "creator" in tax["kategorien"] and tax["pflichtfelder"]