"""Sprechzimmer: Gespraechs-Engine (SZ-04).

ask(agent, session) -> str ist der Modellaufruf (Ollama qwen3.5:9b, in Tests gemockt).
"""
from __future__ import annotations

import threading
from typing import Callable

USER = "Eddie"
_STOPS: dict[str, threading.Event] = {}
_STOPS_LOCK = threading.Lock()


def _stop_event(session: dict) -> threading.Event | None:
    with _STOPS_LOCK:
        return _STOPS.get(str(session.get("id") or ""))


def next_speaker(session: dict, moderator_pick: str | None = None) -> str:
    participants = list(session.get("participants") or [])
    if not participants:
        return ""
    if moderator_pick and moderator_pick in participants:
        return moderator_pick
    last = None
    for message in reversed(session.get("messages") or []):
        speaker = message.get("speaker")
        if speaker in participants:
            last = speaker
            break
    if last is None:
        return participants[0]
    index = participants.index(last)
    return participants[(index + 1) % len(participants)]


def user_say(session: dict, text: str) -> dict:
    message = {"speaker": USER, "text": str(text)}
    session.setdefault("messages", []).append(message)
    session["interrupt"] = True
    return message


def pause(session: dict) -> None:
    session["paused"] = True
    event = _stop_event(session)
    if event is not None:
        event.set()


def _ask_with_timeout(ask: Callable[[str, dict], str], agent: str, session: dict,
                      timeout: float) -> tuple[str | None, bool]:
    box: dict = {}

    def _worker() -> None:
        try:
            box["text"] = ask(agent, session)
        except BaseException as exc:  # noqa: BLE001 - an den Aufrufer melden
            box["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None, True
    if "error" in box:
        return f"[fehler] {type(box['error']).__name__}: {box['error']}", False
    return str(box.get("text") or ""), False


def run_autonomous(session: dict, ask: Callable[[str, dict], str], max_rounds: int = 6,
                   timeout: float = 90.0, stop_flag: Callable[[], bool] | None = None) -> list[dict]:
    """
    Fuehrt einen autonomen Gespraechszyklus mit NEXUS-Moderator durch.

    Regeln:
    - Maximal max_rounds Austausche (außer bei Timeout/Interrupt).
    - Fallback auf Round-Robin wenn next_speaker() keinen Sprecher liefert.
    - Respektiert Pause-Flag, Interrupt-Status und globale Stop-Funktion.
    - Timeout pro Zug mit Teilantwort oder Timeout-Nachricht.
    - Nur lokale Ablaeufe, kein Zugriff auf C: oder Netzwerke.
    """
    session["interrupt"] = False
    sid = str(session.get("id") or "")

    # Event fuer diesen spezifischen Session-Stopp (falls SID vorhanden)
    event = threading.Event()
    if sid and hasattr(_STOPS, '__getitem__') and 'locks' in globals():
        try:
            with _STOPS_LOCK:
                _STOPS[sid] = event
        except NameError:
            pass

    produced: list[dict] = []

    try:
        # Wir koennen die Schleife nicht einfach ueberspringen, wir muessen versuchen.
        rounds_executed = 0

        while rounds_executed < max(1, int(max_rounds)):
            # Pruefung auf Pause oder Interrupt
            if session.get("paused") or session.get("interrupt"):
                break

            # Globale Stop-Flag checken
            if stop_flag and stop_flag():
                break

            # Sprecher bestimmen
            speaker = next_speaker(session)

            # Fallback einbauen wenn next_speaker None zurueckgibt (oder Session leer ist)
            if not speaker:
                fallback_candidate = _fallback_round_robin(session)
                if fallback_candidate:
                    speaker = fallback_candidate
                else:
                    # Keine Sprecher mehr verfuegbar
                    break

            # Aufruf der ask-Funktion mit Timeout
            text, timed_out = _ask_with_timeout(ask, speaker, session, float(timeout))

            if timed_out:
                text = f"[timeout] {speaker} hat nicht innerhalb von {float(timeout):g}s geantwortet."

            message = {"speaker": speaker, "text": str(text or "")}
            session.setdefault("messages", []).append(message)
            produced.append(message)

            rounds_executed += 1

    finally:
        # Sauberung der Event-Zuordnung falls vorhanden
        if sid and hasattr(_STOPS, '__getitem__') and 'locks' in globals():
            try:
                with _STOPS_LOCK:
                    if _STOPS.get(sid) is event:
                        _STOPS.pop(sid, None)
            except NameError:
                pass

    return produced


def _fallback_round_robin(session: dict) -> str | None:
    """Bestimmt naechsten Sprecher im Fallback-Modus via Round-Robin."""
    speakers = session.get("speakers", [])
    if not speakers:
        return None

    last_idx = session.get("_last_speaker_index", -1)
    idx = (last_idx + 1) % len(speakers)
    session["_last_speaker_index"] = idx
    return speakers[idx]
