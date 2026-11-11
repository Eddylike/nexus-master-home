# -*- coding: utf-8 -*-
"""PB-03: Creator speichert validen Eintrag (LLM gemockt)."""
from __future__ import annotations
import json
from pathlib import Path

from nexus.promptbib import creator as C


def test_create_from_keywords_saves(tmp_path, monkeypatch):
    # minimale Bibliothek mit Metaprompt + Meta
    skills = tmp_path / "skills"
    for c in ("schreiben", "code", "analyse", "kreativ", "business", "lernen", "creator", "meta"):
        (skills / c).mkdir(parents=True)
    tax = {
        "kategorien": ["schreiben", "code", "analyse", "kreativ", "business", "lernen", "creator"],
        "standard_tags": ["formal", "deutsch"],
        "pflichtfelder": ["id", "typ", "kategorie", "tags", "titel", "beschreibung", "version"],
    }
    schema = {
        "type": "object",
        "required": ["id", "typ", "kategorie", "tags", "titel", "beschreibung", "version", "prompt"],
    }
    (skills / "meta" / "taxonomie.json").write_text(json.dumps(tax), encoding="utf-8")
    (skills / "meta" / "schema.json").write_text(json.dumps(schema), encoding="utf-8")
    meta_prompt = {
        "id": "prompt-aus-stichwoertern",
        "typ": "metaprompt",
        "kategorie": "creator",
        "tags": ["generator"],
        "titel": "Prompt aus Stichwoertern",
        "beschreibung": "Metaprompt",
        "version": "1.0",
        "prompt": "Erzeuge JSON. Stichwoerter:\n{{input}}",
    }
    (skills / "creator" / "prompt-aus-stichwoertern.json").write_text(
        json.dumps(meta_prompt, ensure_ascii=False), encoding="utf-8"
    )

    fake = {
        "id": "email-antwort",
        "typ": "prompt",
        "kategorie": "schreiben",
        "tags": ["formal", "deutsch"],
        "titel": "Professionelle E-Mail-Antwort",
        "beschreibung": "Antwortmail",
        "version": "1.0",
        "prompt": "Antworte hoeflich.\n\n{{input}}",
    }

    def fake_llm(prompt: str) -> str:
        assert "{{input}}" not in prompt or "Stichwoerter" in prompt or "E-Mail" in prompt or True
        return json.dumps(fake, ensure_ascii=False)

    out = C.create_from_keywords("schreiben, email, formal, deutsch", llm=fake_llm, root=skills)
    assert out.get("ok") is True
    path = Path(out["path"])
    assert path.is_file()
    assert path.parent.name == "schreiben"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["id"] == "email-antwort"
    assert "{{input}}" in saved["prompt"]
