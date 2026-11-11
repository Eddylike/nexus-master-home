"""Code-Tools: Lint, Sandbox-Ausfuehrung, Schreiben in den OMEGA-Sandboxbereich."""
from __future__ import annotations

from pathlib import Path

from nexus.omega import config, hooks, sandbox


def _files_root() -> Path:
    root = config.sandbox_dir() / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_path(rel: str) -> Path | None:
    raw = str(rel or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or ":" in raw or ".." in raw.split("/"):
        return None
    target = (_files_root() / raw).resolve()
    try:
        target.relative_to(_files_root())
    except ValueError:
        return None
    return target


def lint_python(params: dict, ctx: dict) -> dict:
    result = hooks.lint_python(str(params.get("code") or ""))
    return {"success": bool(result["ok"]), "status": "OK" if result["ok"] else "LINT_FAILED",
            **result}


def run_python(params: dict, ctx: dict) -> dict:
    res = sandbox.run_python(str(params.get("code") or ""),
                             int(params.get("timeout") or 20),
                             str(params.get("stdin") or ""))
    return res


def write_code(params: dict, ctx: dict) -> dict:
    path = _safe_path(str(params.get("path") or ""))
    if path is None:
        return {"success": False, "status": "PATH_DENIED",
                "error": "Nur relative Pfade im OMEGA-Sandboxverzeichnis erlaubt"}
    code = str(params.get("code") or "")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return {"success": True, "status": "OK", "path": str(path.relative_to(_files_root())),
            "abs_path": str(path), "bytes": len(code.encode("utf-8")),
            "lint": hooks.lint_python(code)}


HANDLERS = {"lint_python": lint_python, "run_python": run_python, "write_code": write_code}
