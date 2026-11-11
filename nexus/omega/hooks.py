"""OMEGA Layer 3: Pre-/Post-Execution-Hooks.

Pre:  Rechte, Freigabe (HITL), Rate-Limit. Ohne Freigabe wird nicht ausgefuehrt.
Post: Lint generierten Codes, Audit in SQLite. Fehler werden sichtbar gemacht.
"""
from __future__ import annotations

import ast
import sys
from typing import Any

from nexus.omega import config, events, ratelimit

_RISK_REASONS = {
    "slack_post": "Nachricht an ein externes System senden",
    "slack_read": "Externe Slack-Daten lesen",
    "notion_create_page": "In eine externe Notion-Datenbank schreiben",
    "trigger_webhook": "Externen Webhook ausloesen",
    "files_delete": "Datei loeschen",
    "git_repo": "Git-Repository veraendern",
    "github": "GitHub-Zustand veraendern",
    "http_request": "HTTP-Anfrage mit Wirkung senden",
    "promote_tool": "Neues Tool dauerhaft in die Registry uebernehmen",
}

_LINT_WARNINGS = {
    "eval": "eval() vermeiden",
    "exec": "exec() vermeiden",
    "os.system": "os.system() vermeiden",
    "subprocess": "subprocess nur in der Sandbox",
    "socket": "Netzwerkzugriff ist in der Sandbox deaktiviert",
    "urllib": "Netzwerkzugriff ist in der Sandbox deaktiviert",
    "requests": "Netzwerkzugriff ist in der Sandbox deaktiviert",
}


def _guard():
    from nexus.terminal import guard

    return guard


def _approval_reason(tool: str, params: dict) -> str:
    base = _RISK_REASONS.get(tool) or f"Wirkung nach aussen: {tool}"
    detail = ""
    if tool == "slack_post":
        detail = " channel=" + str(params.get("channel") or "(default)")
    elif tool in ("trigger_webhook", "http_request"):
        detail = " " + str(params.get("url") or "")[:160]
    elif tool == "files_delete":
        detail = " " + str(params.get("path") or "")
    elif tool == "git_repo":
        detail = " " + " ".join(str(x) for x in (params.get("args") or [])[:4])
    return (base + detail)[:500]


def _request_approval(tool: str, params: dict, run_id: str | None) -> str:
    guard = _guard()
    reason = _approval_reason(tool, params)
    aid = guard.request_action_approval(
        agent=config.AGENT_NAME,
        action=tool,
        params=params,
        reason=reason,
        description=f"OMEGA will ausfuehren: {tool}",
    )
    events.record_approval(aid, tool, params, reason, status="pending")
    events.log_hook(run_id, "pre", tool, "APPROVAL_REQUESTED", {"approval_id": aid, "reason": reason})
    return aid


def _interactive_approve(tool: str, params: dict, run_id: str | None) -> tuple[bool, str | None]:
    """Nur fuer den interaktiven CLI-Modus. Ohne TTY -> Pause."""
    if not sys.stdin or not sys.stdin.isatty():
        return False, None
    aid = _request_approval(tool, params, run_id)
    print(f"\n[OMEGA] Freigabe erforderlich: {tool}")
    print(f"[OMEGA] Grund: {_approval_reason(tool, params)}")
    print(f"[OMEGA] approval_id: {aid}")
    try:
        answer = input("[OMEGA] Ausfuehren? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer not in ("y", "yes", "j", "ja"):
        events.set_approval_status(aid, "denied", "OMEGA-CLI")
        events.log_hook(run_id, "pre", tool, "DENIED", {"approval_id": aid})
        return False, aid
    _guard().decide(aid, True, by="OMEGA-CLI")
    events.set_approval_status(aid, "approved", "OMEGA-CLI")
    return True, aid


def pre_execute(tool: str, params: dict, run_id: str | None = None, *,
                approval_id: str | None = None, requires_approval: bool = False,
                rate: dict | None = None) -> dict:
    """Entscheidet vor der Ausfuehrung. Rueckgabe: {allow, status, approval_id, ...}."""
    events.log_hook(run_id, "pre", tool, "CHECK", {"approval_required": bool(requires_approval)})
    if requires_approval:
        mode = config.hitl_mode()
        if mode == "allow":
            events.log_hook(run_id, "pre", tool, "ALLOW_OVERRIDE", {"mode": mode})
        elif approval_id:
            ok = _guard().consume_action_approval(approval_id, config.AGENT_NAME, tool, params)
            events.set_approval_status(approval_id, "used" if ok else "invalid")
            if not ok:
                events.log_hook(run_id, "pre", tool, "DENIED",
                                {"reason": "approval_id ungueltig, abgelaufen oder param-fremd",
                                 "approval_id": approval_id})
                return {"allow": False, "status": "APPROVAL_INVALID",
                        "error": "approval_id ungueltig, abgelaufen oder an andere Parameter gebunden",
                        "approval_id": approval_id}
            events.log_hook(run_id, "pre", tool, "APPROVED", {"approval_id": approval_id})
        elif mode == "interactive":
            ok, aid = _interactive_approve(tool, params, run_id)
            if not ok:
                return {"allow": False, "status": "APPROVAL_REQUIRED" if aid else "DENIED",
                        "error": "Freigabe nicht erteilt", "approval_id": aid}
            approval_id = aid
        else:
            aid = _request_approval(tool, params, run_id)
            return {"allow": False, "status": "APPROVAL_REQUIRED",
                    "error": "Menschliche Freigabe erforderlich (HITL)",
                    "approval_id": aid,
                    "reason": _approval_reason(tool, params)}

    if rate:
        key = f"{config.AGENT_NAME}:{tool}"
        if not ratelimit.allow(key, int(rate.get("limit", 60)), float(rate.get("window_s", 60))):
            events.log_hook(run_id, "pre", tool, "RATE_LIMITED", rate)
            return {"allow": False, "status": "RATE_LIMITED",
                    "error": f"Rate-Limit fuer {tool} erreicht ({rate})"}
    events.log_hook(run_id, "pre", tool, "ALLOW", {"approval_id": approval_id})
    return {"allow": True, "status": "OK", "approval_id": approval_id}


def lint_python(code: str) -> dict:
    text = str(code or "")
    errors: list[str] = []
    warnings: list[str] = []
    if len(text) > 200_000:
        errors.append("Code zu gross (>200k Zeichen)")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return {"ok": False, "errors": [f"SyntaxError line {exc.lineno}: {exc.msg}"],
                "warnings": [], "lines": text.count("\n") + 1}
    for node in ast.walk(tree):
        name = ""
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                parts = []
                cur: ast.AST = node.func
                while isinstance(cur, ast.Attribute):
                    parts.append(cur.attr)
                    cur = cur.value
                if isinstance(cur, ast.Name):
                    parts.append(cur.id)
                name = ".".join(reversed(parts))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = getattr(node, "module", None) or ""
            for alias in node.names:
                full = (mod + "." + alias.name).strip(".") if mod else alias.name
                root = full.split(".")[0]
                if root in _LINT_WARNINGS:
                    warnings.append(_LINT_WARNINGS[root])
        for needle, message in _LINT_WARNINGS.items():
            if name == needle or name.startswith(needle + "."):
                warnings.append(message)
    return {"ok": not errors, "errors": errors, "warnings": sorted(set(warnings)),
            "lines": text.count("\n") + 1}


def post_execute(tool: str, params: dict, result: Any, run_id: str | None = None,
                 *, duration_ms: int | None = None, approval_id: str | None = None,
                 lint_params: list[str] | None = None) -> dict:
    payload = result if isinstance(result, dict) else {"result": result}
    lint: dict | None = None
    for key in (lint_params or []):
        if isinstance(params.get(key), str) and params[key].strip():
            lint = lint_python(params[key])
            payload["lint"] = lint
            if not lint["ok"]:
                payload["success"] = False
                if not payload.get("error"):
                    payload["error"] = "LINT_FAILED: " + "; ".join(lint["errors"])[:500]
                payload.setdefault("status", "LINT_FAILED")
            break
    status = str(payload.get("status") or ("OK" if payload.get("success") else "ERROR"))
    events.log_call(run_id, tool, params, status=status, approval_id=approval_id,
                    duration_ms=duration_ms, result=payload,
                    error=None if payload.get("success") else str(payload.get("error") or ""))
    events.log_hook(run_id, "post", tool, status,
                    {"duration_ms": duration_ms, "lint_ok": None if not lint else lint.get("ok")})
    return payload
