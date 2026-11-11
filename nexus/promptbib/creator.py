"""PB-03: Creator - Stichwoerter -> Bibliothekseintrag via Metaprompt + Validierung + Speichern."""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from nexus.promptbib import loader as _loader
from nexus.promptbib import schema as _schema

METAPROMPT_ID = "prompt-aus-stichwoertern"


def taxonomie_path() -> Path:
    """workspace/promptbib/skills/meta/taxonomie.json."""
    return _loader.skills_root() / "meta" / "taxonomie.json"


def load_taxonomie(path: Path | None = None) -> dict[str, Any]:
    """Laedt erlaubte Kategorien, Standard-Tags, Pflichtfelder."""
    p = Path(path) if path is not None else taxonomie_path()
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    data.setdefault("kategorien", list(_schema.DEFAULT_KATEGORIEN))
    data.setdefault("standard_tags", [])
    data.setdefault("pflichtfelder", list(_schema.DEFAULT_REQUIRED))
    return data


def _split_keywords(keywords: str) -> list[str]:
    return [k.strip().lower() for k in re.split(r"[,;\n]+", keywords or "") if k.strip()]


def _default_llm(prompt: str) -> str:
    """Lokales Ollama ueber den NEXUS-AI-Layer (qwen3.5:9b laut ai_models.json). Kein Pull."""
    from nexus.common import ai_layer
    from nexus.common.ollama_client import OllamaClient

    client = OllamaClient()
    try:
        ai_layer.apply_to_client(client)
    except Exception:
        pass
    out = client.chat([{"role": "user", "content": prompt}], temperature=0.2, format="json")
    if not out.get("success"):
        raise RuntimeError(str(out.get("error") or out.get("reply") or "llm_failed"))
    return str(out.get("reply") or out.get("content") or "")


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM-Antwort ist kein JSON-Objekt")
    return data


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pb_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def create_from_keywords(
    keywords: str,
    *,
    llm: Callable[[str], str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """1) Stichwoerter gegen Taxonomie vorpruefen (falls Kategorie genannt).
    2) Metaprompt prompt-aus-stichwoertern laden und {{input}} ersetzen.
    3) llm(prompt) aufrufen (Default: lokales Ollama qwen3.5:9b ueber NEXUS-AI-Layer).
    4) JSON parsen, gegen schema validieren.
    5) Speichern unter skills/<kategorie>/<id>.json (atomar, UTF-8 ohne BOM).
    6) Rueckgabe {"ok": True, "entry": {...}, "path": str} oder
       {"ok": False, "error": "...", "errors": [...]}."""
    base = Path(root) if root is not None else _loader.skills_root()
    kws = _split_keywords(keywords)
    if not kws:
        return {"ok": False, "error": "KEINE_STICHWOERTER", "errors": ["Keine Stichwoerter angegeben"]}
    try:
        tax = load_taxonomie(base / "meta" / "taxonomie.json")
    except (OSError, ValueError) as e:
        return {"ok": False, "error": "TAXONOMIE_FEHLT", "errors": [str(e)]}
    try:
        schema = _schema.load_schema(base / "meta" / "schema.json")
    except (OSError, ValueError) as e:
        return {"ok": False, "error": "SCHEMA_FEHLT", "errors": [str(e)]}
    kategorien = list(tax.get("kategorien") or [])
    schema = dict(schema, kategorien=kategorien) if kategorien else schema
    genannt = [k for k in kws if k in kategorien]
    if len(genannt) > 1:
        return {"ok": False, "error": "MEHRERE_KATEGORIEN", "errors": [f"Mehrere Kategorien genannt: {genannt}"]}
    kategorie = genannt[0] if genannt else None
    meta = next((e for e in _loader.load_all(base) if e.get("id") == METAPROMPT_ID), None)
    if not meta or not isinstance(meta.get("prompt"), str):
        return {"ok": False, "error": "METAPROMPT_FEHLT", "errors": [f"{METAPROMPT_ID} nicht gefunden"]}
    eingabe = (
        f"Stichwoerter: {', '.join(kws)}\n"
        f"Erlaubte Kategorien: {', '.join(kategorien)}\n"
        f"Vorgegebene Kategorie: {kategorie or 'frei waehlen'}\n"
        f"Standard-Tags: {', '.join(tax.get('standard_tags') or [])}\n"
        f"Pflichtfelder: {', '.join(tax.get('pflichtfelder') or [])}"
    )
    prompt = meta["prompt"].replace("{{input}}", eingabe)
    try:
        raw = (llm or _default_llm)(prompt)
    except Exception as e:
        return {"ok": False, "error": "LLM_FEHLER", "errors": [f"{type(e).__name__}: {e}"]}
    try:
        entry = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as e:
        return {"ok": False, "error": "KEIN_JSON", "errors": [str(e)], "raw": (raw or "")[:500]}
    if kategorie and entry.get("kategorie") != kategorie:
        return {"ok": False, "error": "KATEGORIE_ABWEICHEND",
                "errors": [f"LLM lieferte '{entry.get('kategorie')}', verlangt '{kategorie}'"], "entry": entry}
    errs = _schema.validate_entry(entry, schema)
    if errs:
        return {"ok": False, "error": "SCHEMA_UNGUELTIG", "errors": errs, "entry": entry}
    target = base / entry["kategorie"] / f"{entry['id']}.json"
    if target.exists():
        return {"ok": False, "error": "ID_EXISTIERT", "errors": [f"{target} existiert bereits"], "entry": entry}
    _atomic_write(target, entry)
    return {"ok": True, "entry": entry, "path": str(target),
            "feedback": f"Eintrag '{entry['id']}' in Kategorie '{entry['kategorie']}' gespeichert."}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="NEXUS Prompt-Creator: Stichwoerter -> Bibliothekseintrag")
    ap.add_argument("stichwoerter", help='z.B. "schreiben, email, formal, deutsch"')
    a = ap.parse_args(argv)
    out = create_from_keywords(a.stichwoerter)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
