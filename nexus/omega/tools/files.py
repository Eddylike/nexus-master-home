"""Datei-Tools, strikt begrenzt auf workspace/omega/sandbox/scratch."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from nexus.omega import config
from nexus.omega.sanitize import sanitize_untrusted

_MAX_READ = 200_000
_MAX_WRITE = 200_000


def _root() -> Path:
    root = config.sandbox_dir() / "scratch"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_path(rel: str) -> Path | None:
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ":" in raw:
        return None
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    target = (_root() / Path(*parts)).resolve()
    try:
        target.relative_to(_root())
    except ValueError:
        return None
    return target


def files_list(params: dict, ctx: dict) -> dict:
    base = _safe_path(str(params.get("path") or ".")) or _root()
    if not base.exists():
        return {"success": False, "status": "NOT_FOUND", "error": "Pfad existiert nicht"}
    if not base.is_dir():
        return {"success": False, "status": "NOT_A_DIRECTORY", "error": "Pfad ist kein Verzeichnis"}
    items = []
    for entry in sorted(base.iterdir(), key=lambda p: p.name.lower())[:500]:
        try:
            stat = entry.stat()
            items.append({"name": entry.name, "dir": entry.is_dir(), "bytes": stat.st_size,
                          "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                          .isoformat(timespec="seconds")})
        except OSError:
            continue
    return {"success": True, "status": "OK", "path": str(base.relative_to(_root())) or ".",
            "items": items, "count": len(items)}


def files_read(params: dict, ctx: dict) -> dict:
    path = _safe_path(str(params.get("path") or ""))
    if path is None:
        return {"success": False, "status": "PATH_DENIED",
                "error": "Nur relative Pfade im Scratch-Verzeichnis erlaubt"}
    if not path.is_file():
        return {"success": False, "status": "NOT_FOUND", "error": "Datei existiert nicht"}
    raw = path.read_bytes()[:_MAX_READ]
    clean = sanitize_untrusted(raw.decode("utf-8", "replace"))
    return {"success": True, "status": "OK", "path": str(path.relative_to(_root())),
            "content": clean["text"], "suspicious": clean["suspicious"],
            "findings": clean["findings"], "truncated": clean["truncated"],
            "untrusted": True}


def files_write(params: dict, ctx: dict) -> dict:
    path = _safe_path(str(params.get("path") or ""))
    if path is None:
        return {"success": False, "status": "PATH_DENIED",
                "error": "Nur relative Pfade im Scratch-Verzeichnis erlaubt"}
    content = str(params.get("content") or "")
    if len(content.encode("utf-8")) > _MAX_WRITE:
        return {"success": False, "status": "TOO_LARGE", "error": "Inhalt > 200 KB"}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"success": True, "status": "OK", "path": str(path.relative_to(_root())),
            "bytes": len(content.encode("utf-8"))}


def files_delete(params: dict, ctx: dict) -> dict:
    path = _safe_path(str(params.get("path") or ""))
    if path is None:
        return {"success": False, "status": "PATH_DENIED",
                "error": "Nur relative Pfade im Scratch-Verzeichnis erlaubt"}
    if not path.is_file():
        return {"success": False, "status": "NOT_FOUND", "error": "Datei existiert nicht"}
    size = path.stat().st_size
    path.unlink()
    return {"success": True, "status": "OK", "deleted": str(path.relative_to(_root())),
            "bytes": size}


HANDLERS = {
    "files_list": files_list,
    "files_read": files_read,
    "files_write": files_write,
    "files_delete": files_delete,
}
