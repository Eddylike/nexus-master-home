# -*- coding: utf-8 -*-
"""PB-05/PB-06/PB-ABN: Bibliotheks-Mindestumfang und Platzhalter."""
from __future__ import annotations
import json
from pathlib import Path
from collections import Counter

from nexus.promptbib import loader as L

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "workspace" / "promptbib" / "skills"
REQUIRED_CATS = ["schreiben", "code", "analyse", "kreativ", "business", "lernen", "creator"]


def test_readme_exists():
    assert (ROOT / "workspace" / "promptbib" / "README.md").is_file()


def test_library_min_counts_and_placeholders():
    entries = L.load_all(SKILLS)
    # Solange Stubs offen: NotImplementedError -> Suite skippt via conftest.
    # Nach PB-05: >=21 Eintraege, je Kategorie >=3, {{...}} in prompt-Feldern.
    assert len(entries) >= 21
    counts = Counter(e.get("kategorie") for e in entries)
    for cat in REQUIRED_CATS:
        assert counts.get(cat, 0) >= 3, f"{cat} hat nur {counts.get(cat, 0)}"
    for e in entries:
        if e.get("typ") in ("prompt", "metaprompt"):
            assert "{{" in (e.get("prompt") or "") and "}}" in (e.get("prompt") or "")
        for key in ("id", "typ", "kategorie", "tags", "titel", "beschreibung", "version"):
            assert key in e and e[key] not in (None, "", [])
