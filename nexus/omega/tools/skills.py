"""Self-Improvement: Tool-Kandidat schreiben, sandbox-testen, kontrolliert promoten.

Promotion ist freigabepflichtig (Manifest: destructive). Ohne bestandenen
Sandbox-Test wird nichts registriert. Built-in-Tools koennen nie ueberschrieben
werden.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from nexus.omega import config, hooks, sandbox

_NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


def _candidates() -> Path:
    d = config.plugins_dir() / "candidates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _defines_handler(code: str) -> bool:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ("handler", "HANDLERS"):
            return True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "HANDLERS":
                    return True
    return False


def propose_tool(params: dict, ctx: dict) -> dict:
    name = str(params.get("name") or "").strip().lower()
    code = str(params.get("code") or "")
    description = str(params.get("description") or "")
    test_code = str(params.get("test_code") or "")
    if not _NAME.match(name):
        return {"success": False, "status": "INVALID_PARAMS",
                "error": "name muss a-z0-9_ sein (3-41 Zeichen)"}
    lint = hooks.lint_python(code)
    if not lint["ok"]:
        return {"success": False, "status": "LINT_FAILED", "error": "; ".join(lint["errors"]),
                "lint": lint}
    if not _defines_handler(code):
        return {"success": False, "status": "INVALID_TOOL",
                "error": "Tool muss eine Funktion handler(params, ctx) oder HANDLERS definieren"}
    from nexus.omega import registry

    if name in {t["name"] for t in registry._read_manifest(registry.MANIFEST)}:
        return {"success": False, "status": "NAME_RESERVED",
                "error": "Built-in-Toolnamen duerfen nicht ueberschrieben werden"}
    target = _candidates() / f"{name}.py"
    target.write_text(code, encoding="utf-8")
    test_result = None
    if test_code.strip():
        combined = code + "\n\n# --- OMEGA candidate tests ---\n" + test_code
        test_result = sandbox.run_python(combined, timeout=20)
    tested = bool(test_result and test_result.get("success"))
    meta = {"name": name, "description": description, "code_file": str(target),
            "created": datetime.now().isoformat(timespec="seconds"),
            "tested": tested, "test": test_result, "lint": lint}
    ( _candidates() / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
    status = "OK" if tested else "PENDING_TEST"
    return {"success": True, "status": status, "name": name, "candidate": str(target),
            "tested": tested, "test": test_result,
            "note": None if tested else ("Kandidat gespeichert, aber nicht sandbox-getestet. "
                                         "Promotion bleibt gesperrt.")}


def promote_tool(params: dict, ctx: dict) -> dict:
    name = str(params.get("name") or "").strip().lower()
    if not _NAME.match(name):
        return {"success": False, "status": "INVALID_PARAMS", "error": "invalid_name"}
    cand_meta = _candidates() / f"{name}.json"
    cand_code = _candidates() / f"{name}.py"
    if not cand_meta.is_file() or not cand_code.is_file():
        return {"success": False, "status": "NOT_FOUND", "error": "Kein Kandidat mit diesem Namen"}
    try:
        meta = json.loads(cand_meta.read_text(encoding="utf-8"))
    except ValueError:
        return {"success": False, "status": "INVALID_CANDIDATE", "error": "Metadaten unlesbar"}
    if not meta.get("tested"):
        return {"success": False, "status": "NOT_TESTED",
                "error": "Kandidat hat keinen bestandenen Sandbox-Test; Promotion verweigert"}
    from nexus.omega import registry

    if name in {t["name"] for t in registry._read_manifest(registry.MANIFEST)}:
        return {"success": False, "status": "NAME_RESERVED", "error": "Built-in-Name"}
    dest = config.plugins_dir() / f"{name}.py"
    shutil.copy2(cand_code, dest)
    tools = registry._read_manifest(registry._user_manifest_path())
    tools = [t for t in tools if t.get("name") != name]
    tools.append({
        "name": name,
        "module": name,
        "plugin_path": str(dest),
        "description": str(params.get("description") or meta.get("description") or
                           f"OMEGA User-Tool {name}"),
        "parameters": params.get("parameters") if isinstance(params.get("parameters"), dict)
        else {"type": "object", "properties": {}, "required": []},
        "permission": "write",
        "destructive": False,
        "approval_required": True,
        "rate": {"limit": 20, "window_s": 60},
        "tags": ["user-plugin"],
    })
    path = registry._user_manifest_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "tools": tools}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)
    return {"success": True, "status": "OK", "name": name, "plugin": str(dest),
            "manifest": str(path),
            "note": "User-Tools sind bei jedem Aufruf freigabepflichtig."}


HANDLERS = {"propose_tool": propose_tool, "promote_tool": promote_tool}
