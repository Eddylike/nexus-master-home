"""HTTP dispatch helpers for Agent Bridge routes (used by chat.server.Handler)."""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from nexus.bridge import PROTOCOL, PROTOCOL_VERSION
from nexus.bridge.auth import ensure_bridge_token, mask_token, verify_bearer
from nexus.bridge.errors import BridgeError, UNAUTHORIZED, INVALID_REQUEST
from nexus.bridge.executors import bind_default_executors
from nexus.bridge.models import MessageRequest, TaskCreateRequest
from nexus.bridge.permissions import PermissionLevel, process_level, TASK_PERMISSIONS
from nexus.bridge.tasks import REGISTERED_TASKS, TaskManager
from nexus.bridge.outcomes import evaluate as _loaded_outcome_evaluator
from nexus.common.config import Config
from nexus.common.paths import ROOT

_MANAGER: TaskManager | None = None
_TOKEN_BOOT: dict[str, Any] = {}

PROTOCOL_VERSION_MASTER = "1.1.0"
OUTCOME_CONTRACT = "nexus.executor-outcome.v1"


def boot() -> dict[str, Any]:
    """Load/generate token + bind executors. Safe to call multiple times."""
    global _MANAGER, _TOKEN_BOOT
    tok, path, generated = ensure_bridge_token()
    if _MANAGER is None:
        candidate = TaskManager()
        bind_default_executors(candidate)
        _MANAGER = candidate
    _TOKEN_BOOT = {
        "token_present": bool(tok),
        "token_mask": mask_token(tok),
        "token_path": str(path) if path else None,
        "generated": generated,
    }
    print(
        f"Agent-Bridge Auth: Bearer aktiv (Token {mask_token(tok)}"
        + (f", file={path}" if path else "")
        + ("; generated" if generated else "")
        + f"; process_level={process_level().value})",
        flush=True,
    )
    return _TOKEN_BOOT


def manager() -> TaskManager:
    if _MANAGER is None:
        boot()
    assert _MANAGER is not None
    return _MANAGER


def _norm_path(path: str) -> str:
    if path.startswith("/api/v1/"):
        return "/api/" + path[len("/api/v1/"):]
    if path == "/api/v1":
        return "/api"
    return path


def is_bridge_get(path: str) -> bool:
    p = _norm_path(path)
    if p in (
        "/health",
        "/api/bridge/health",
        "/api/capabilities",
        "/api/bridge/status",
        "/api/bridge/capabilities",
        "/api/bridge/approvals",
        "/api/bridge/sessions",
        "/api/bridge/skills",
        "/api/skills",
    ):
        return True
    if p.startswith("/api/bridge/approvals/"):
        return True
    if p.startswith("/api/task/"):
        return True
    if path in ("/api/v1/tasks", "/api/v1/status"):
        return True
    if p == "/api/bridge/tasks":
        return True
    return False


def is_bridge_post(path: str) -> bool:
    p = _norm_path(path)
    if p in ("/api/task", "/api/message", "/api/bridge/message", "/api/bridge/approvals", "/api/bridge/sessions"):
        return True
    if p.startswith("/api/task/") and p.endswith("/cancel"):
        return True
    if p.startswith("/api/bridge/approvals/") and (
        p.endswith("/approve") or p.endswith("/deny")
    ):
        return True
    return False


def require_auth(handler) -> bool:
    hdr = handler.headers.get("Authorization") or handler.headers.get("authorization") or ""
    if verify_bearer(hdr):
        return True
    handler._json(401, {
        "success": False,
        "error": UNAUTHORIZED,
        "message": "Bearer NEXUS_BRIDGE_TOKEN required",
    })
    return False


# --- Local-only maintenance / drain control (never on the gateway allowlist) ---
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


def _is_loopback(handler) -> bool:
    try:
        addr = (handler.client_address or [""])[0]
    except Exception:
        return False
    return str(addr).strip().lower() in _LOOPBACK


def is_maintenance_path(path: str) -> bool:
    p = _norm_path(path)
    return p == "/api/bridge/maintenance" or p.startswith("/api/bridge/maintenance/")


def _reject_maintenance_remote(handler) -> None:
    handler._json(403, {
        "success": False,
        "error": "LOCAL_ONLY",
        "message": "maintenance control is loopback-only and not reachable via Funnel/gateway",
    })


def handle_maintenance_get(handler, path: str, qs: dict) -> None:
    if not require_auth(handler):
        return
    if not _is_loopback(handler):
        return _reject_maintenance_remote(handler)
    from nexus.maintenance import maintenance
    return handler._json(200, {"success": True, "maintenance": maintenance().snapshot()})


def handle_maintenance(handler, path: str, body: dict) -> None:
    if not require_auth(handler):
        return
    if not _is_loopback(handler):
        return _reject_maintenance_remote(handler)
    from nexus.maintenance import maintenance
    p = _norm_path(path)
    ctl = maintenance()

    if p == "/api/bridge/maintenance/drain":
        actor = str(body.get("actor") or "local_maintenance")
        reason = str(body.get("reason") or "")
        timeout = body.get("timeout_seconds")
        try:
            timeout = float(timeout) if timeout is not None else None
        except Exception:
            timeout = None
        ctl.begin_drain(reason=reason, actor=actor, timeout=timeout)
        return handler._json(200, {"success": True, "maintenance": ctl.snapshot()})

    if p == "/api/bridge/maintenance/cancel":
        actor = str(body.get("actor") or "local_maintenance")
        return handler._json(200, {"success": True, "maintenance": ctl.cancel_drain(actor=actor)})

    if p == "/api/bridge/maintenance/shutdown":
        actor = str(body.get("actor") or "local_maintenance")
        res = ctl.request_shutdown(actor=actor, confirm=bool(body.get("confirm")))
        if res.get("ok"):
            return handler._json(200, {"success": True, "shutdown": res})
        code = 409 if res.get("error") in ("work_in_flight", "drain_not_active") else 400
        if res.get("error") == "confirmation_required":
            code = 400
        return handler._json(code, {"success": False, "shutdown": res})

    return handler._json(404, {"success": False, "error": "not_found", "path": path})



def handle_get(handler, path: str, qs: dict) -> None:
    if not require_auth(handler):
        return
    raw_path = path
    p = _norm_path(path)

    if p in ("/health", "/api/bridge/health", "/api/bridge/status") or raw_path == "/api/v1/status":
        return handler._json(200, health_payload())

    if p in ("/api/bridge/skills", "/api/skills"):
        return handler._json(200, skills_payload())

    if p in ("/api/capabilities", "/api/bridge/capabilities"):
        return handler._json(200, capabilities_payload())

    if raw_path == "/api/v1/tasks" or p == "/api/bridge/tasks":
        lim = 50
        try:
            lim = int((qs.get("limit") or ["50"])[0])
        except Exception:
            lim = 50
        items = [t.to_dict() for t in manager().list(limit=lim)]
        return handler._json(200, {"success": True, "tasks": items, "count": len(items)})

    if p == "/api/bridge/approvals":
        from nexus.bridge.approvals import approvals
        lim = 50
        try:
            lim = int((qs.get("limit") or ["50"])[0])
        except Exception:
            lim = 50
        status = (qs.get("status") or [None])[0]
        items = [a.to_dict() for a in approvals().list(limit=lim, status=status)]
        return handler._json(200, {"success": True, "approvals": items, "count": len(items)})

    if p.startswith("/api/bridge/approvals/"):
        from nexus.bridge.approvals import approvals
        aid = p[len("/api/bridge/approvals/"):].strip("/")
        try:
            rec = approvals().get(aid)
        except BridgeError as e:
            return handler._json(e.http_status, e.to_dict())
        return handler._json(200, {"success": True, "approval": rec.to_dict()})

    if p == "/api/bridge/sessions":
        from nexus.bridge.sessions import sessions
        return handler._json(200, {"success": True, "sessions": sessions().list()})

    if p.startswith("/api/task/"):
        rest = p[len("/api/task/"):]
        parts = [x for x in rest.split("/") if x]
        if not parts:
            return handler._json(400, BridgeError(INVALID_REQUEST, "task id required").to_dict())
        task_id = parts[0]
        want_log = len(parts) > 1 and parts[1] == "log"
        try:
            rec = manager().get(task_id)
        except BridgeError as e:
            return handler._json(e.http_status, e.to_dict())
        if want_log:
            return handler._json(200, {"success": True, "task_id": task_id, "log": rec.log})
        return handler._json(200, {"success": True, "task": rec.to_dict(include_log=False)})

    handler._json(404, {"success": False, "error": "not_found", "path": path})


def handle_post(handler, path: str, body: dict) -> None:
    if not require_auth(handler):
        return
    p = _norm_path(path)

    if p == "/api/bridge/sessions":
        from nexus.bridge.sessions import sessions
        rec = sessions().create(
            client=(str(body.get("client")) if body.get("client") is not None else None),
            source=(str(body.get("source")) if body.get("source") is not None else None),
        )
        return handler._json(200, {"success": True, "session": rec})

    if p == "/api/bridge/approvals":
        from nexus.bridge.approvals import approvals
        task = (body.get("task") or "").strip()
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        reason = str(body.get("reason") or "")
        if not task:
            return handler._json(400, BridgeError(INVALID_REQUEST, "task required").to_dict())
        if task not in TASK_PERMISSIONS or TASK_PERMISSIONS[task] != PermissionLevel.PRIVILEGED:
            return handler._json(400, BridgeError(INVALID_REQUEST, "task must be PRIVILEGED").to_dict())
        rec = approvals().request(task, params, reason=reason)
        return handler._json(200, {"success": True, "approval": rec.to_dict(), "ui": "/approvals"})

    if p.startswith("/api/bridge/approvals/") and (p.endswith("/approve") or p.endswith("/deny")):
        from nexus.bridge.approvals import approvals
        approve = p.endswith("/approve")
        mid = p[len("/api/bridge/approvals/"):]
        aid = mid[: -len("/approve")] if approve else mid[: -len("/deny")]
        aid = aid.strip("/")
        try:
            rec = approvals().decide(aid, approve=approve, decided_by=str(body.get("by") or "local_ui"))
        except BridgeError as e:
            return handler._json(e.http_status, e.to_dict())
        return handler._json(200, {"success": True, "approval": rec.to_dict()})

    if p == "/api/task":
        req = TaskCreateRequest.from_body(body or {})
        try:
            rec = manager().create_and_run(req)
        except BridgeError as e:
            return handler._json(e.http_status, e.to_dict())
        if rec.status == "awaiting_approval":
            return handler._json(202, {
                "success": False,
                "awaiting_approval": True,
                "task": rec.to_dict(include_log=False),
                "approval_id": rec.approval_id,
                "ui": "/approvals",
            })
        code = 200 if rec.status == "succeeded" else 400
        return handler._json(code, {
            "success": rec.status == "succeeded",
            "task": rec.to_dict(include_log=False),
        })

    if p.startswith("/api/task/") and p.endswith("/cancel"):
        rest = p[len("/api/task/"): - len("/cancel")]
        task_id = rest.strip("/")
        if not task_id:
            return handler._json(400, BridgeError(INVALID_REQUEST, "task id required").to_dict())
        try:
            rec = manager().cancel(task_id)
        except BridgeError as e:
            return handler._json(e.http_status, e.to_dict())
        return handler._json(200, {"success": True, "task": rec.to_dict()})

    if p in ("/api/message", "/api/bridge/message"):
        msg = MessageRequest.from_body(body or {})
        if not msg.message:
            return handler._json(400, BridgeError(INVALID_REQUEST, "message required").to_dict())
        if msg.session_id:
            try:
                from nexus.bridge.sessions import sessions
                sessions().touch(msg.session_id, message=True)
            except Exception:
                pass
        lower = msg.message.lower().strip()
        # Handshake tokens
        if any(k in lower for k in (
            "nexus_bridge_ok", "nexus_agent_bridge_v1", "nexus_master_ok",
            "handshake", "ping bridge",
        )):
            reply = (
                "NEXUS_BRIDGE_OK NEXUS_AGENT_BRIDGE_V1 NEXUS_MASTER_OK ready "
                f"process_level={process_level().value} tasks={len(REGISTERED_TASKS)}"
            )
        else:
            reply = (
                f"Agent Bridge Master received message ({len(msg.message)} chars). "
                f"process_level={process_level().value}. "
                f"Registered tasks: {len(REGISTERED_TASKS)}. "
                f"Use POST /api/task. PRIVILEGED needs local /approvals. "
                f"Chat: POST /api/assistant (separate Bearer)."
            )
        return handler._json(200, {
            "success": True,
            "reply": reply,
            "source": "agent_bridge",
            "protocol": PROTOCOL,
            "version": PROTOCOL_VERSION_MASTER,
            "process_level": process_level().value,
            "handshake": "NEXUS_BRIDGE_OK" in reply,
            "client_source": msg.source,
            "session_id": msg.session_id,
        })

    handler._json(404, {"success": False, "error": "not_found", "path": path})


def health_payload() -> dict[str, Any]:
    boot_info = _TOKEN_BOOT or boot()
    return {
        "success": True,
        "ok": True,
        "outcome_contract": OUTCOME_CONTRACT,
        "protocol": PROTOCOL,
        "version": PROTOCOL_VERSION_MASTER,
        "marker": "NEXUS_AGENT_BRIDGE_V1",
        "master_marker": "NEXUS_MASTER_SYSTEM",
        "server": "NexusAI/1.0",
        "port": Config.PORT,
        "ssl_port": Config.SSL_PORT,
        "root": str(ROOT.resolve()),
        "auth": {
            "required": True,
            "scheme": "Bearer",
            "env": "NEXUS_BRIDGE_TOKEN",
            "token_mask": boot_info.get("token_mask"),
            "separate_from_assistant": True,
            "assistant_env": "NEXUS_ASSISTANT_TOKEN",
        },
        "permissions": {
            "process_level": process_level().value,
            "levels": [p.value for p in PermissionLevel],
            "safe_write": True,
            "privileged_requires_approval": True,
        },
        "tasks_registered": list(REGISTERED_TASKS),
        "funnel_note": "Approvals UI is local-only (/approvals); not on Funnel allowlist",
        "maintenance": _maintenance_snapshot(),
    }


def _maintenance_snapshot() -> dict[str, Any]:
    try:
        from nexus.maintenance import maintenance
        return maintenance().snapshot()
    except Exception as e:
        return {"state": "UNKNOWN", "error": type(e).__name__}


def capabilities_payload() -> dict[str, Any]:
    return {
        "success": True,
        "protocol": PROTOCOL,
        "version": PROTOCOL_VERSION_MASTER,
        "schema": "nexus.bridge.capabilities.v1",
        "permissions": [p.value for p in PermissionLevel],
        "process_level": process_level().value,
        "tasks": [
            {"name": n, "permission": TASK_PERMISSIONS[n].value}
            for n in REGISTERED_TASKS
        ],
        "executors": {
            "file": {"atomic_write": True, "root": str(ROOT.resolve())},
            "git": {"argv_only": True},
            "python": {"project_scripts_only": True},
            "powershell": {"registry_only": True},
            "remote_shell": False,
        },
        "approvals": {
            "required_for": "PRIVILEGED",
            "ui": "/approvals",
            "funnel_exposed": False,
        },
        "sessions": True,
        "model_routing": "local-first",
        "cost_rule": "LOCAL->FREE->NOT",
        "openai_required": False,
        "endpoints": {
            "POST /api/task": "Create+run registered task",
            "GET /api/task/{id}": "Task status",
            "POST /api/task/{id}/cancel": "Cancel task",
            "GET /api/task/{id}/log": "Task log",
            "GET /api/capabilities": "Capability list",
            "POST /api/message": "Handshake / lightweight bridge message",
            "GET /api/bridge/health": "Bridge health",
            "GET/POST /api/bridge/approvals": "Local approval store",
            "POST /api/bridge/approvals/{id}/approve|deny": "Decide approval",
            "GET /api/v1/status": "Alias health",
            "GET /api/v1/tasks": "List bridge tasks",
        },
        "orchestrator": "NEXUS Master Home",
        "agents": _agents_capabilities_section(),
        "skills_endpoint": "/api/bridge/skills",
        "notes": [
            "GET /api/status remains Nexus ENGINE.status (unchanged, no bridge auth).",
            "GET /api/tasks remains master project tasks (unchanged).",
            "POST /api/assistant uses NEXUS_ASSISTANT_TOKEN (separate).",
            "POST /api/chat and voice/media/HTTPS stay without bridge auth.",
            "Funnel allowlist: bridge paths only; Chat/Voice/Media stay local.",
            "No generic remote shell; no Cloudflare.",
        ],
    }


def _agents_capabilities_section() -> dict:
    """Crew capability allowlist inventory (Phase 4). Safe; no secrets."""
    try:
        from nexus.crew.capabilities import inventory
        return inventory()
    except Exception as e:
        return {"ok": False, "error": "agents_inventory_unavailable", "detail": str(e)[:200]}


def skills_payload() -> dict:
    """Minimal skills discovery (Phase 3 registry) via Bridge — Core-adjacent for agents."""
    try:
        from nexus.skills import list_skills
        payload = list_skills()
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.setdefault("orchestrator", "NEXUS Master Home")
            payload.setdefault("endpoint", "/api/bridge/skills")
            payload.setdefault("schema", "nexus.skills.discovery.v1")
            return payload
        return {"success": False, "ok": False, "error": "invalid_skills_payload"}
    except Exception as e:
        return {
            "success": False,
            "ok": False,
            "error": "skills_unavailable",
            "message": str(e)[:200],
            "orchestrator": "NEXUS Master Home",
        }

