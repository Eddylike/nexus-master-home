"""HAUS5T - Runner: startet das Spiel mit echten Agenten und schreibt in den Chat.

Aufruf:
  python -m nexus.games.haus5t.runner --new
  python -m nexus.games.haus5t.runner --resume
  python -m nexus.games.haus5t.runner --new --no-chat --max-steps 3

Steuerung waehrend des Laufs:
  workspace/games/haus5t.pause  -> pausieren (Datei entfernen = fortsetzen)
  workspace/games/haus5t.stop   -> abbrechen
Die API-Routen /api/games/haus5t/control erzeugen dieselben Steuerdateien.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from nexus.common.paths import ROOT

from . import agents, api, engine, state as state_mod, voice as voice_mod, voice

GAMES = Path(ROOT) / "workspace" / "games"
STATE = GAMES / "haus5t_state.json"
PAUSE = GAMES / "haus5t.pause"
STOP = GAMES / "haus5t.stop"
CORE = "http://127.0.0.1:8299"
PARTNER = {"NEXUS": "nexus", "Hermes": "hermes", "NEXUS GPT": "gpt", "Aether": "aether",
           "Mira": "nexus", "Bruno": "nexus", "Lina": "nexus"}


def _http(method: str, url: str, body: dict | None = None, timeout: float = 20.0) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8", errors="replace") or "{}")
        except Exception:
            return {"success": False, "error": f"HTTP_{exc.code}"}
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return {"success": False, "error": "SERVER_UNAVAILABLE"}


def _new_chat_session(title: str) -> str:
    result = _http("POST", CORE + "/api/sessions/new", {"title": title})
    session = result.get("session") or {}
    return str(session.get("id") or "")


def _post_chat(state: dict, speaker: str, text: str, meta: dict | None = None) -> bool:
    session_id = str(state.get("session_id") or "")
    if not session_id:
        return False
    result = _http("POST", CORE + "/api/sessions/append_turn", {
        "session_id": session_id,
        "speaker": speaker,
        "partner": PARTNER.get(speaker, "nexus"),
        "content": f"**{speaker}:** {text}",
        "meta": meta or {},
    })
    return result.get("success") is True


def _post_pending(state: dict) -> int:
    contributions = state.get("contributions") or []
    index = int(state.get("posted", 0) or 0)
    posted = 0
    for entry in contributions[index:]:
        if _post_chat(state, entry.get("speaker") or "NEXUS", entry.get("text") or "",
                      {"round": entry.get("round"), "source": entry.get("source"),
                       "ok": entry.get("ok")}):
            posted += 1
    state["posted"] = index + len(contributions[index:])
    return posted


def _check_control(state: dict, speech: voice_mod.SpeechQueue | None = None) -> bool:
    """True = weiterlaufen. Verarbeitet STOP/PAUSE-Dateien ehrlich."""
    if STOP.exists():
        state["status"] = "aborted"
        state_mod.save_state(state, STATE)
        if speech is not None:
            speech.pause()
        return False
    if PAUSE.exists():
        if state.get("status") == "running":
            state["status"] = "paused"
            state_mod.save_state(state, STATE)
            if speech is not None:
                speech.pause()
            _post_chat(state, "NEXUS", "(Spielsteuerung) Pause. Alle halten inne. "
                                       "Pause-Datei entfernen oder /resume senden.")
        return False
    if state.get("status") == "paused":
        state["status"] = "running"
        state_mod.save_state(state, STATE)
        if speech is not None:
            speech.resume()
        _post_chat(state, "NEXUS", "(Spielsteuerung) Wir setzen fort.")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Das Haus der fuenf Tueren starten/fortsetzen")
    parser.add_argument("--new", action="store_true", help="neue Spielsession erzwingen")
    parser.add_argument("--resume", action="store_true", help="gespeicherten Stand fortsetzen")
    parser.add_argument("--session-id", default="", help="bestehende Chat-Session verwenden")
    parser.add_argument("--no-chat", action="store_true", help="nicht in den NEXUS-Chat schreiben")
    parser.add_argument("--max-steps", type=int, default=0, help="0 = bis Ende/Statuswechsel")
    parser.add_argument("--step-delay", type=float, default=0.5, help="Pause zwischen Zuegen")
    parser.add_argument("--no-voice", "--no-speak", dest="no_voice", action="store_true",
                        help="Antworten nicht automatisch vorlesen")
    parser.add_argument("--voice-wait", type=float, default=voice_mod.WAIT_TIMEOUT,
                        help="max. Sekunden auf die Sprachausgabe warten (0 = nicht warten)")
    args = parser.parse_args(argv)

    GAMES.mkdir(parents=True, exist_ok=True)
    speech = None if args.no_voice else voice_mod.get_queue()
    state = state_mod.load_state(STATE)
    if args.new or (state is None and not args.resume):
        for control in (PAUSE, STOP):
            try:
                control.unlink()
            except FileNotFoundError:
                pass
        session_id = str(args.session_id or "").strip()
        if not session_id and not args.no_chat:
            session_id = _new_chat_session("Das Haus der fuenf Tueren")
        state = state_mod.new_state(session_id)
        state["posted"] = 0
        state_mod.save_state(state, STATE)
        if speech is not None:
            speech.reset(session_id)
            speech.announce_text("start:" + session_id, voice_mod.START_TEXT, kind="start")
        if not args.no_chat:
            _post_chat(state, "NEXUS",
                       "(Spielstart) Das Haus der fuenf Tueren - ein Rollenspiel und eine SIMULATION, "
                       "keine Therapie. Mira, Bruno und Lina sind eigenstaendige persistente Agenten. "
                       "Eddie, du kannst jederzeit eingreifen.")
    elif state is None:
        print("Kein Spielstand vorhanden. Nutze --new.")
        return 2
    elif args.session_id:
        state["session_id"] = str(args.session_id)
        state_mod.save_state(state, STATE)

    ask = agents.make_ask()
    steps = 0
    while state.get("status") not in ("finished", "aborted"):
        if not _check_control(state, speech):
            break
        if args.max_steps and steps >= args.max_steps:
            break
        before = len(state.get("contributions") or [])
        engine.step(state, ask)
        state_mod.save_state(state, STATE)
        new_entries = (state.get("contributions") or [])[before:]
        if speech is not None and new_entries:
            speech.announce_new(state)
        if not args.no_chat:
            _post_pending(state)
            state_mod.save_state(state, STATE)
        for entry in new_entries:
            print(f"[{entry.get('ts')}] R{entry.get('round')} {entry.get('speaker')} "
                  f"({entry.get('source')}, ok={entry.get('ok')})", flush=True)
            print(f"    {str(entry.get('text') or '')[:500]}", flush=True)
        steps += 1
        if args.step_delay:
            time.sleep(max(0.0, args.step_delay))

    summary = engine.summary(state)
    total = len(state.get("contributions") or [])
    print(f"STATUS={state.get('status')} RUNDEN={summary['rounds_done']}/7 BEITRAEGE={total}", flush=True)
    if state.get("status") == "finished":
        print(summary["text"], flush=True)
        if speech is not None:
            session_id = str(state.get("session_id") or "")
            speech.announce_text("summary:" + session_id,
                                 voice_mod.SUMMARY_PREFIX + summary["text"], kind="summary")
        if not args.no_chat:
            _post_chat(state, "NEXUS", "(Spielende) " + summary["text"])
    if speech is not None and args.voice_wait > 0 and state.get("status") == "finished":
        if not speech.wait_idle(args.voice_wait):
            print("SPRACHAUSGABE=offen (TTS nicht verfuegbar oder pausiert)", flush=True)
        else:
            print("SPRACHAUSGABE=fertig", flush=True)
    return 0 if state.get("status") == "finished" else (1 if state.get("status") == "aborted" else 0)


if __name__ == "__main__":
    raise SystemExit(main())
