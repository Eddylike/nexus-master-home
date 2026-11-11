"""Sprechzimmer: API /api/sprechzimmer (SZ-05). Reiner Dispatcher fuer nexus/chat/server.py (http.server)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from nexus.sprechzimmer import engine, participants as participants_mod, session_manager

PREFIX = "/api/sprechzimmer"


def _parts(path: str) -> list[str]:
    rest = path[len(PREFIX):] if path.startswith(PREFIX) else path
    return [p for p in rest.strip("/").split("/") if p]


def handle(method: str, path: str, body: dict | None, base_dir: Path,
           ask: Callable[[str, dict], str] | None = None) -> tuple[int, dict]:
    parts = _parts(path)
    body = body if isinstance(body, dict) else {}
    method = (method or "GET").upper()
    try:
        if method == "GET":
            if not parts or parts == ["sessions"]:
                return 200, {"sessions": session_manager.list_sessions(base_dir)}
            if len(parts) == 2 and parts[0] == "sessions":
                return 200, session_manager.load_session(parts[1], base_dir)
            return 404, {"success": False, "error": "not_found"}

        if method == "POST" and (not parts or parts == ["sessions"]):
            names = participants_mod.validate_participants(list(body.get("participants") or []))
            if not names:
                return 400, {"success": False, "error": "participants_required"}
            session = session_manager.create_session(names, base_dir, str(body.get("title") or ""))
            return 201, session

        if len(parts) >= 2 and parts[0] == "sessions":
            session_id = parts[1]
            action = parts[2] if len(parts) > 2 else ""
            if method == "DELETE" and not action:
                if session_manager.delete_session(session_id, base_dir):
                    return 200, {"deleted": session_id}
                return 404, {"success": False, "error": "not_found"}
            if method != "POST":
                return 404, {"success": False, "error": "not_found"}

            session = session_manager.load_session(session_id, base_dir)
            if action == "say":
                message = engine.user_say(session, str(body.get("text") or ""))
                session_manager.save_session(session, base_dir)
                return 200, message
            if action == "invite":
                session["participants"] = participants_mod.invite(
                    list(session.get("participants") or []), str(body.get("agent") or ""),
                    str(body.get("by") or ""))
                session_manager.save_session(session, base_dir)
                return 200, session
            if action == "autonomous" and len(parts) > 3 and parts[3] == "start":
                if ask is None:
                    return 503, {"success": False, "error": "ask_unavailable"}
                rounds = body.get("max_rounds", 6)
                timeout = body.get("timeout", 90)
                try:
                    rounds = max(1, min(int(rounds), 12))
                    timeout = max(0.1, min(float(timeout), 600.0))
                except (TypeError, ValueError):
                    return 400, {"success": False, "error": "invalid_rounds_or_timeout"}
                produced = engine.run_autonomous(session, ask, rounds, timeout)
                session_manager.save_session(session, base_dir)
                return 200, {"messages": produced, "session": session}
            if action == "autonomous" and len(parts) > 3 and parts[3] == "pause":
                engine.pause(session)
                session_manager.save_session(session, base_dir)
                return 200, {"paused": True}
            if action == "resume":
                return 200, session_manager.resume_session(session_id, base_dir)
            return 404, {"success": False, "error": "not_found"}

        return 404, {"success": False, "error": "not_found"}
    except PermissionError as exc:
        return 403, {"success": False, "error": str(exc)}
    except ValueError as exc:
        return 400, {"success": False, "error": str(exc)}
    except FileNotFoundError:
        return 404, {"success": False, "error": "not_found"}
