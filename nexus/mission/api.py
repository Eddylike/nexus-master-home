"""HTTP-API fuer Missionen: /api/mission/*. Schreibende Routen nur Loopback."""
from __future__ import annotations

from typing import Any

from nexus.mission import agents as agent_runtime
from nexus.mission.engine import get_engine

PREFIX = "/api/mission"


def _loopback(remote: str, headers) -> bool:
    if str(remote) not in ("127.0.0.1", "::1"):
        return False
    try:
        return not (headers or {}).get("X-Forwarded-For")
    except Exception:
        return True


def _first(qs: dict, key: str, default: str = "") -> str:
    value = (qs or {}).get(key)
    if isinstance(value, list):
        return str(value[0]) if value else default
    return str(value if value is not None else default)


def handle(method: str, path: str, body: dict | None = None, qs: dict | None = None,
           remote: str = "127.0.0.1", headers: Any = None) -> tuple[int, dict]:
    method = (method or "GET").upper()
    body = body if isinstance(body, dict) else {}
    engine = get_engine()
    try:
        if method == "GET":
            if path in (PREFIX, PREFIX + "/", PREFIX + "/status"):
                return 200, engine.status()
            if path == PREFIX + "/agents":
                return 200, {"success": True, "agents": agent_runtime.health()}
            if path == PREFIX + "/runs":
                return 200, {"success": True, "missions": engine.store.list(30)}
            if path.startswith(PREFIX + "/runs/"):
                mission = engine.get(path.rsplit("/", 1)[-1])
                if not mission:
                    return 404, {"success": False, "error": "MISSION_NOT_FOUND"}
                return 200, {"success": True, "mission": mission}
            if path == PREFIX + "/memory":
                requester = _first(qs, "requester", "NEXUS")
                if requester not in agent_runtime.CORE_AGENTS:
                    return 400, {"success": False, "error": "UNKNOWN_REQUESTER"}
                entries = engine.store.recall(_first(qs, "mission") or None, _first(qs, "q"),
                                              requester=requester,
                                              limit=int(_first(qs, "limit", "20") or 20))
                return 200, {"success": True, "requester": requester, "entries": entries,
                             "access": "sensitive nur NEXUS/Aether oder Urheber"}
            return 404, {"success": False, "error": "not_found"}

        if method == "POST":
            if not _loopback(remote, headers):
                return 403, {"success": False, "error": "LOOPBACK_ONLY"}
            if path == PREFIX + "/run":
                goal = str(body.get("goal") or "").strip()
                if not goal:
                    return 400, {"success": False, "error": "goal required"}
                wait = body.get("wait") is True
                mission = engine.submit(goal, str(body.get("context") or ""),
                                        background=not wait, meta={"origin": str(body.get("origin") or "api")})
                return (200 if wait else 202), {"success": True, "mission": mission,
                                               "mission_id": mission.get("id"), "status": mission.get("status")}
            if path == PREFIX + "/approve":
                mission_id = str(body.get("mission_id") or "").strip()
                if not mission_id:
                    return 400, {"success": False, "error": "mission_id required"}
                wait = body.get("wait") is True
                mission = engine.approve(mission_id, body.get("step_id"),
                                         body.get("approved", True) is not False,
                                         by=str(body.get("by") or "Eddie"), background=not wait)
                return (200 if wait else 202), {"success": True, "mission": mission}
            return 404, {"success": False, "error": "not_found"}

        return 404, {"success": False, "error": "not_found"}
    except KeyError:
        return 404, {"success": False, "error": "MISSION_NOT_FOUND"}
    except ValueError as exc:
        return 400, {"success": False, "error": str(exc)}
    except Exception as exc:
        return 500, {"success": False, "error": f"{type(exc).__name__}: {exc}"[:500]}
