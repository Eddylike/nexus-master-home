"""PB-01/PB-04: JSON-Schema-Validierung fuer Bibliothekseintraege (ohne externe Pakete)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*\}\}")
TYPEN = ("prompt", "skill", "metaprompt")
DEFAULT_REQUIRED = ["id", "typ", "kategorie", "tags", "titel", "beschreibung", "version"]
DEFAULT_KATEGORIEN = ["schreiben", "code", "analyse", "kreativ", "business", "lernen", "creator"]


def schema_path() -> Path:
    """Pfad zu workspace/promptbib/skills/meta/schema.json."""
    repo = Path(__file__).resolve().parents[2]
    return repo / "workspace" / "promptbib" / "skills" / "meta" / "schema.json"


def load_schema(path: Path | None = None) -> dict[str, Any]:
    """Laedt das JSON-Schema. Fehlt die Datei -> FileNotFoundError."""
    p = Path(path) if path is not None else schema_path()
    if not p.is_file():
        raise FileNotFoundError(f"Schema nicht gefunden unter {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Schema {p} ist kein gueltiges JSON: {e}") from e


def _required(schema: dict[str, Any]) -> list[str]:
    req = schema.get("required") or schema.get("pflichtfelder")
    return list(req) if req else list(DEFAULT_REQUIRED)


def _kategorien(schema: dict[str, Any]) -> list[str]:
    if schema.get("kategorien"):
        return list(schema["kategorien"])
    enum = ((schema.get("properties") or {}).get("kategorie") or {}).get("enum")
    return list(enum) if enum else list(DEFAULT_KATEGORIEN)


def validate_entry(entry: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    """Prueft Pflichtfelder, Typen, kebab-case id, erlaubte Kategorie.
    Rueckgabe: Liste der Fehlerstrings (leer = gueltig).
    Skills brauchen pipeline (list[str]); Prompts/Metaprompts brauchen prompt (str)
    mit mindestens einem {{...}}-Platzhalter (mind. {{input}} empfohlen)."""
    if not isinstance(entry, dict):
        return ["Eintrag ist kein Objekt"]
    if schema is None:
        try:
            schema = load_schema()
        except (FileNotFoundError, ValueError):
            schema = {}
    errors: list[str] = []
    for field in _required(schema):
        val = entry.get(field)
        if val is None or val == "" or val == []:
            errors.append(f"Pflichtfeld fehlt oder leer: {field}")
    for field in ("id", "typ", "kategorie", "titel", "beschreibung", "version"):
        if field in entry and not isinstance(entry[field], str):
            errors.append(f"Feld '{field}' muss String sein")
    if isinstance(entry.get("id"), str) and entry["id"] and not ID_RE.match(entry["id"]):
        errors.append(f"id '{entry['id']}' ist nicht kebab-case")
    tags = entry.get("tags")
    if tags is not None and (not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags)):
        errors.append("tags muss Liste nicht-leerer Strings sein")
    typ = entry.get("typ")
    if isinstance(typ, str) and typ and typ not in TYPEN:
        errors.append(f"typ '{typ}' nicht erlaubt ({', '.join(TYPEN)})")
    kat = entry.get("kategorie")
    if isinstance(kat, str) and kat and kat not in _kategorien(schema):
        errors.append(f"kategorie '{kat}' nicht erlaubt")
    if typ == "skill":
        pl = entry.get("pipeline")
        if not isinstance(pl, list) or not pl or not all(isinstance(s, str) and s for s in pl):
            errors.append("skill braucht pipeline (Liste von Strings)")
    elif typ in ("prompt", "metaprompt"):
        pr = entry.get("prompt")
        if not isinstance(pr, str) or not pr.strip():
            errors.append(f"{typ} braucht prompt (String)")
        elif not PLACEHOLDER_RE.search(pr):
            errors.append("prompt braucht mindestens einen {{platzhalter}} (empfohlen {{input}})")
    if "ziel_format" in entry and not isinstance(entry["ziel_format"], dict):
        errors.append("ziel_format muss Objekt sein")
    return errors
