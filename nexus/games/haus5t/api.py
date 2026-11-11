"""HAUS5T - API fuer "Das Haus der fuenf Tueren".

Routen (Loopback/Agent-Guard setzt der HTTP-Server):
  GET  /api/games/haus5t/state
  POST /api/games/haus5t/start
  POST /api/games/haus5t/step
  POST /api/games/haus5t/control   {"action": "pause"|"resume"|"abort"}
  POST /api/games/haus5t/input     {"text": "..."}

Die API ist zustandslos aufrufbar; der Spielstand liegt in
workspace/games/haus5t_state.json. Jeder step fuehrt genau einen echten
Agentenaufruf aus. Timeouts werden ehrlich als ok=False gespeichert.
"""
from __future__ import annotations

from pathlib import Path

from nexus.common.paths import ROOT

from . import agents, engine, safety, state as state_mod, voice as voice_mod

PREFIX = "/api/games/haus5t/"
GAMES_DIR = Path(ROOT) / "workspace" / "games"
STATE_PATH = GAMES_DIR / "haus5t_state.json"
PAUSE_PATH = GAMES_DIR / "haus5t.pause"
STOP_PATH = GAMES_DIR / "haus5t.stop"
CONTROL_ACTIONS = {"pause", "resume", "abort", "stop"}
VOICE_ACTIONS = {"on", "enable", "off", "disable", "pause", "resume", "status"}
PUBLIC_CONTRIBUTIONS = 40
PUBLIC_EVENTS = 40


def _route(path: str) -> str:
    value = str(path or "")
    if value.startswith(PREFIX):
        return value[len(PREFIX):].strip("/")
    return ""


def _voice() -> voice_mod.SpeechQueue:
    return voice_mod.get_queue()


def _announce_summary_if_finished(state: dict) -> None:
    if state.get("status") != "finished":
        return
    text = voice_mod.SUMMARY_PREFIX + engine.summary(state)["text"]
    key = "summary:" + str(state.get("session_id") or "")
    _voice().announce_text(key, text, kind="summary")


def public_state(state: dict) -> dict:
    summary = engine.summary(state)
    return {
        "success": True,
        "exists": True,
        "game": state.get("game"),
        "session_id": state.get("session_id"),
        "status": state.get("status"),
        "round": state.get("round"),
        "step": state.get("step"),
        "round_title": state.get("round_title"),
        "roles": state.get("roles"),
        "scores": summary["scores"],
        "ratings": state.get("ratings") or {},
        "rounds_done": summary["rounds_done"],
        "open_questions": summary["open_questions"],
        "summary": summary["text"],
        "contributions": (state.get("contributions") or [])[-PUBLIC_CONTRIBUTIONS:],
        "events": (state.get("events") or [])[-PUBLIC_EVENTS:],
        "voice": _voice().status(),
        "updated": state.get("updated"),
    }


def _load() -> dict | None:
    return state_mod.load_state(STATE_PATH)


def _save(state: dict) -> None:
    state_mod.save_state(state, STATE_PATH)


def handle(method: str, path: str, body: dict) -> tuple[int, dict]:
    """Dispatch fuer /api/games/haus5t/{start,state,step,control,input}.
    TODO: Implementieren Sie die fehlenden Imports und Logik fur 'agent', 'engine', 'safety' aus der Basis-Datei,
um wenn Notwendig.
"""
    route = _route(path)
    if route not in {"state", "start", "step", "control", "input", "voice"}:
        return 404, {"success": False, "error": "unknown_route", "path": str(path)}
    data = body if isinstance(body, dict) else {}
    method = str(method or "GET").upper()

    if route == "voice":
        vq = _voice()
        if method == "GET":
            return 200, {"success": True, **vq.status()}
        if method != "POST":
            return 405, {"success": False, "error": "method_not_allowed"}
        action = str(data.get("action") or "status").strip().lower()
        if action not in VOICE_ACTIONS:
            return 400, {"success": False, "error": "INVALID_ACTION",
                         "allowed": sorted(VOICE_ACTIONS)}
        if action in ("on", "enable"):
            result = vq.enable()
        elif action in ("off", "disable"):
            result = vq.disable()
        elif action == "pause":
            result = vq.pause()
        elif action == "resume":
            result = vq.resume()
        else:
            result = {"success": True}
        return 200, {"success": True, **result, **vq.status()}

    if route == "state":
        if method != "GET":
            return 405, {"success": False, "error": "method_not_allowed"}
        state = _load()
        if state is None:
            return 200, {"success": True, "exists": False, "state": None}
        return 200, public_state(state)

    if method != "POST":
        return 405, {"success": False, "error": "method_not_allowed"}

    if route == "start":
        force = bool(data.get("new") or data.get("force"))
        state = _load()
        if state is not None and not force and not data.get("resume"):
            return 200, public_state(state)
        if state is None and data.get("resume"):
            return 404, {"success": False, "error": "STATE_NOT_FOUND"}
        if force or state is None:
            for control in (PAUSE_PATH, STOP_PATH):
                try:
                    control.unlink()
                except FileNotFoundError:
                    pass
            session_id = str(data.get("session_id") or "").strip()
            if not session_id:
                from datetime import datetime
                session_id = "HAUS5T-" + datetime.now().strftime("%Y%m%d%H%M%S")
            state = state_mod.new_state(session_id)
            state["posted"] = 0
            _save(state)
            vq = _voice()
            vq.reset(session_id)
            vq.announce_text("start:" + session_id, voice_mod.START_TEXT, kind="start")
        return 200, public_state(state)

    if route == "step":
        state = _load()
        if state is None:
            return 404, {"success": False, "error": "STATE_NOT_FOUND"}
        count = data.get("count", 1)
        try:
            count = max(1, min(20, int(count)))
        except (TypeError, ValueError):
            count = 1
        ask = agents.make_ask()
        last = None
        for _ in range(count):
            if state.get("status") in ("paused", "aborted", "finished"):
                break
            before = len(state.get("contributions") or [])
            engine.step(state, ask)
            _save(state)
            if len(state.get("contributions") or []) != before:
                _voice().announce_new(state)
            last = (state.get("contributions") or [None])[-1]
        _announce_summary_if_finished(state)
        payload = public_state(state)
        payload["last"] = last
        return 200, payload

    if route == "control":
        action = str(data.get("action") or "").strip().lower()
        # Sicherheitscheck: Nur erlaubte Aktionen
        if action not in CONTROL_ACTIONS:
            return 400, {"success": False, "error": "INVALID_ACTION", "allowed": sorted(CONTROL_ACTIONS)}
        state = _load()
        if state is None:
            return 404, {"success": False, "error": "STATE_NOT_FOUND"}
        engine.control(state, action)
        vq = _voice()
        if action == "pause":
            GAMES_DIR.mkdir(parents=True, exist_ok=True)
            PAUSE_PATH.write_text("pause", encoding="utf-8")
            vq.pause()
        elif action == "resume":
            try:
                PAUSE_PATH.unlink()
            except FileNotFoundError:
                pass
            vq.resume()
        else:
            GAMES_DIR.mkdir(parents=True, exist_ok=True)
            STOP_PATH.write_text("stop", encoding="utf-8")
            vq.pause()
        _save(state)
        return 200, public_state(state)

    if route == "input":
        text = str(data.get("text") or "").strip()
        if not text:
            return 400, {"success": False, "error": "TEXT_REQUIRED"}
        state = _load()
        if state is None:
            return 404, {"success": False, "error": "STATE_NOT_FOUND"}
        level = safety.distress_level(text)
        action = safety.safety_action(level)
        state.setdefault("eddie_inputs", []).append(text[:2000])
        if not action.get("continue"):
            state["status"] = "paused"
            state.setdefault("events", []).append({
                "kind": "safety_stop",
                "level": level,
                "ts": state_mod.now(),
            })
            _save(state)
            if action.get("message"):
                _voice().announce_text(
                    "safety:" + str(state.get("session_id") or ""),
                    "Spielleitung NEXUS: " + str(action["message"]),
                    kind="safety",
                )
            payload = public_state(state)
            payload.update({"safety": action, "message": action.get("message")})
            return 200, payload
        state.setdefault("pending_eddie", []).append(text[:2000])
        if action.get("offer_pause"):
            state.setdefault("events", []).append({
                "kind": "safety_elevated",
                "level": level,
                "ts": state_mod.now(),
            })
        _save(state)
        payload = public_state(state)
        payload["safety"] = action
        return 200, payload

    return 404, {"success": False, "error": "unknown_route", "path": str(path)}
