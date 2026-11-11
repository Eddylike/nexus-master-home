"""PB-02: Bibliothek laden und nach Kategorie/Tag filtern."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Iterable


def skills_root() -> Path:
    """workspace/promptbib/skills."""
    # Prefer repo-relative path from this package: nexus/ -> repo root
    repo = Path(__file__).resolve().parents[2]
    return repo / "workspace" / "promptbib" / "skills"


def load_all(root: Path | None = None) -> list[dict[str, Any]]:
    """Laedt alle *.json unter skills/<kategorie>/ (ohne meta/).
    Ungueltige Dateien werden uebersprungen (kein Crash)."""
    base = Path(root) if root is not None else skills_root()
    if not base.exists():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*.json")):
        # skip meta and anything under meta/
        parts = path.relative_to(base).parts
        if not parts or parts[0] == "meta":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


def filter_entries(
    entries: Iterable[dict[str, Any]],
    *,
    kategorie: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    """Filtert nach kategorie und/oder tag (tag case-insensitive in tags[])."""
    tag_l = tag.lower() if isinstance(tag, str) else None
    result: list[dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if kategorie is not None and e.get("kategorie") != kategorie:
            continue
        if tag_l is not None:
            tags = e.get("tags") or []
            if not isinstance(tags, list):
                continue
            if not any(isinstance(t, str) and t.lower() == tag_l for t in tags):
                continue
        result.append(e)
    return result
