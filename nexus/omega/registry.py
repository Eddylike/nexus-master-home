"""OMEGA Layer 2+5: dynamische Skill-/Plugin-Registry.

Das Manifest (JSON) beschreibt alle Tools inklusive Schema, Rechten und
Rate-Limits. Python-Module werden erst beim ersten Aufruf importiert
(on-demand), damit das Kontextfenster und der Start schlank bleiben.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import time
from pathlib import Path
from typing import Any

from nexus.omega import config, events, hooks

TOOLS_DIR = Path(__file__).resolve().parent / "tools"
MANIFEST = TOOLS_DIR / "manifest.json"

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def _read_manifest(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    tools = data.get("tools") if isinstance(data, dict) else data
    return [t for t in (tools or []) if isinstance(t, dict) and t.get("name") and t.get("module")]


def _user_manifest_path() -> Path:
    return config.plugins_dir() / "manifest.json"


def manifest() -> list[dict]:
    """Kern-Tools plus freigegebene User-Plugins (ohne Module zu importieren)."""
    core = _read_manifest(MANIFEST)
    names = {t["name"] for t in core}
    for extra in _read_manifest(_user_manifest_path()):
        if extra["name"] not in names:
            core.append(extra)
    return core


def _index() -> dict[str, dict]:
    return {t["name"]: t for t in manifest()}


def get(name: str) -> dict | None:
    return _index().get(str(name or "").strip())


def list_tools(tags: list[str] | None = None, include_schema: bool = False) -> list[dict]:
    out: list[dict] = []
    wanted = {t.lower() for t in (tags or [])}
    for spec in manifest():
        tool_tags = {str(t).lower() for t in spec.get("tags") or []}
        if wanted and not (wanted & tool_tags):
            continue
        item = {
            "name": spec["name"],
            "description": spec.get("description", ""),
            "permission": spec.get("permission", "read"),
            "destructive": bool(spec.get("destructive")),
            "dynamic_guard": bool(spec.get("dynamic_guard")),
            "rate": spec.get("rate"),
            "tags": spec.get("tags") or [],
            "module": spec.get("module"),
        }
        if include_schema:
            item["parameters"] = spec.get("parameters") or {"type": "object", "properties": {}}
        out.append(item)
    return out


def describe_for_llm() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec.get("description", ""),
                "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
            },
        }
        for spec in manifest()
    ]


def _load_module(spec: dict):
    plugin = spec.get("plugin_path")
    if plugin:
        path = Path(str(plugin)).expanduser().resolve()
        module_name = "omega_user_plugin_" + spec["name"]
        spec_obj = importlib.util.spec_from_file_location(module_name, path)
        if spec_obj and spec_obj.loader:
            module = importlib.util.module_from_spec(spec_obj)
            spec_obj.loader.exec_module(module)
            return module
        raise ImportError(f"plugin_not_loadable: {path}")
    return importlib.import_module(f"nexus.omega.tools.{spec['module']}")


def _handler_for(module, name: str):
    handlers = getattr(module, "HANDLERS", None)
    if isinstance(handlers, dict) and callable(handlers.get(name)):
        return handlers[name]
    fn = getattr(module, name, None)
    if callable(fn):
        return fn
    fn = getattr(module, "handler", None)
    if callable(fn):
        return fn
    raise AttributeError(f"handler_not_found: {name}")


def validate_params(spec: dict, params: dict) -> list[str]:
    schema = spec.get("parameters") or {}
    if not isinstance(params, dict):
        return ["params_must_be_object"]
    errors: list[str] = []
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in params:
            errors.append(f"missing_required:{key}")
    for key, value in params.items():
        prop = props.get(key)
        if not prop:
            continue
        expected = prop.get("type")
        py_type = _TYPE_MAP.get(str(expected))
        if py_type and not isinstance(value, py_type):
            errors.append(f"wrong_type:{key}:{expected}")
        if expected == "string" and isinstance(value, str):
            if "maxLength" in prop and len(value) > int(prop["maxLength"]):
                errors.append(f"too_long:{key}")
        if expected == "integer" and isinstance(value, int):
            if "minimum" in prop and value < int(prop["minimum"]):
                errors.append(f"below_minimum:{key}")
            if "maximum" in prop and value > int(prop["maximum"]):
                errors.append(f"above_maximum:{key}")
        if prop.get("enum") is not None and value not in prop["enum"]:
            errors.append(f"not_in_enum:{key}")
    return errors


def requires_approval(spec: dict, params: dict) -> bool:
    """Fail-closed approval decision without importing plugin code pre-hook.

    Dynamic plugins are never imported just to ask whether they are destructive:
    module import itself can execute arbitrary top-level code. Known built-ins
    receive a conservative argument-only classification; unknown dynamic tools
    require approval.
    """
    if spec.get("destructive") or spec.get("approval_required"):
        return True
    if not spec.get("dynamic_guard"):
        return False
    name = str(spec.get("name") or "")
    if name == "git_repo":
        args = [str(x).lower() for x in (params.get("args") or [])]
        if not args:
            return True
        command = args[0]
        destructive = {"push", "clean", "filter-branch", "gc", "prune", "rm", "restore"}
        if command in destructive:
            return True
        if command == "reset":
            return any(x in args for x in ("--hard", "--keep", "--merge"))
        if command in {"branch", "tag"} and any(x in args for x in ("-d", "-D", "--delete")):
            return True
        return any(x in args for x in ("--force", "-f", "--delete", "--prune"))
    if name == "http_request":
        return str(params.get("method") or "GET").upper() not in ("GET", "HEAD")
    # GitHub CLI and third-party dynamic guards fail closed; never import them pre-HITL.
    return True


def default_ctx(run_id: str | None = None) -> dict:
    return {
        "run_id": run_id,
        "agent": config.AGENT_NAME,
        "home": config.home(),
        "sandbox": config.sandbox_dir(),
    }


def invoke(name: str, params: dict | None = None, *, run_id: str | None = None,
           approval_id: str | None = None, ctx: dict | None = None) -> dict:
    spec = get(name)
    if not spec:
        return {"success": False, "status": "UNKNOWN_TOOL", "tool": name,
                "error": f"Unbekanntes Tool: {name}", "available": sorted(_index())}
    params = dict(params or {})
    invalid = validate_params(spec, params)
    if invalid:
        result = {"success": False, "status": "INVALID_PARAMS", "tool": name,
                  "error": "Ungueltige Parameter: " + ", ".join(invalid), "errors": invalid}
        events.log_call(run_id, name, params, status="INVALID_PARAMS", error=result["error"])
        return result

    needs = requires_approval(spec, params)
    pre = hooks.pre_execute(name, params, run_id, approval_id=approval_id,
                            requires_approval=needs, rate=spec.get("rate"))
    if not pre.get("allow"):
        result = {"success": False, "status": pre.get("status", "DENIED"), "tool": name,
                  "error": pre.get("error", "blocked"), "approval_id": pre.get("approval_id")}
        events.log_call(run_id, name, params, status=result["status"],
                        approval_id=pre.get("approval_id"), error=result["error"],
                        result=result)
        return result

    started = time.time()
    context = dict(ctx or default_ctx(run_id))
    context.setdefault("run_id", run_id)
    context.setdefault("tool", name)
    try:
        module = _load_module(spec)
        handler = _handler_for(module, name)
        raw = handler(params, context)
        result = raw if isinstance(raw, dict) else {"success": True, "result": raw}
        if "success" not in result:
            result["success"] = result.get("status") in ("OK", "SUCCESS", "PENDING")
        result.setdefault("status", "OK" if result.get("success") else "ERROR")
        result["tool"] = name
    except Exception as exc:
        result = {"success": False, "status": "ERROR", "tool": name,
                  "error": f"{type(exc).__name__}: {exc}"[:1_000]}
    duration_ms = int((time.time() - started) * 1000)
    return hooks.post_execute(name, params, result, run_id, duration_ms=duration_ms,
                              approval_id=pre.get("approval_id"),
                              lint_params=spec.get("lint_params"))
