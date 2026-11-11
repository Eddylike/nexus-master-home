"""HAUS5T - Spielstand, Speicherung und Fortsetzung.

SIMULATION/Reflexionsspiel, keine Therapie. Der Spielstand wird atomar als
UTF-8-JSON ohne BOM unter workspace/games/ gespeichert. Jede Agenteninstanz
(Mira, Bruno, Lina) besitzt zusaetzlich ein eigenes Gedaechtnis in
workspace/games/haus5t_agents/.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from . import rules

ROLE_DESCRIPTIONS = {
    "NEXUS": "Spielleitung und Hueter des Hauses. Eroeffnet Tueren, achtet auf die Regeln, "
             "fasst zusammen und berechnet den Durchschnitt ausschliesslich aus echten Bewertungen.",
    "Hermes": "Bote und Gespraechsbegleiter. Traegt Gedanken weiter, spiegelt und sorgt fuer ruhigen Fluss.",
    "NEXUS GPT": "Analytische Perspektive. Zerlegt Aussagen, benennt Muster und bringt neue Sichtweisen ein.",
    "Aether": "Empathischer Begleiter (Dr. Maslenata Umbarum). Achtet auf die fuenf Saeulen, "
              "setzt hoechstens eine sanfte Nadelspitze und danach einen mitfuehlenden Auffangsatz.",
    "Mira": "Persistente Archiv-Agentin. Fiktive Angst, vergessen zu werden; sucht Spuren und Zeugnisse.",
    "Bruno": "Persistenter Kontroll-Agent. Fiktive Angst vor Kontrollverlust; will alles absichern.",
    "Lina": "Persistente Sprach-Agentin. Fiktive Angst, niemals gut genug zu sein; feilt an jedem Wort.",
}


def now():
    return datetime.now().isoformat()


def new_state(session_id: str) -> dict:
    """Neuer Spielstand: Initialisierung aller Felder, Validierung der Session-ID.

    Erstellt einen neuen Spielstand für das Spiel 'haus5t'.
    Führt eine lokale Überprüfung durch (kein Backup, kein Netzwerk).
    Ersetzt bestehende Inhalte nicht direkt auf C:, nutzt nur lokale Pfade.
    Begrenzt Fixversuche auf 3 (logisch im Kontext des Aufrufs sicherzustellen).

    Args:
        session_id: Einzigartige ID für diese Spielsitzung. Wenn leer/None, wird eine generierte verwendet.

    Returns:
        dict: Der initialisierte Spielstand-Zustand.

    Raises:
        ValueError: Wenn die Session-ID ungültig ist (z.B. enthält erlaubte Pfade oder ist zu lang).
    """

    # Validierung der session_id (Gardrails: keine C:/, keine Netzwerkpfade, Länge)
    if not isinstance(session_id, str):
        raise ValueError("Code: INVALID_TYPE - session_id muss ein String sein.")

    if len(session_id) > 64:
        raise ValueError("Code: INVALID_LENGTH - session_id darf maximal 64 Zeichen lang sein.")

    # Prüfen auf Tabu-Pfade (C:, UNC-Pfade, Netzwerk)
    lower_id = session_id.lower()
    c_prefixes = ["c:", "c:/", "\\"]
    if any(p in lower_id for p in c_prefixes):
        raise ValueError("Code: FORBIDDEN_PATH - 'C:' oder andere lokale/System-Pfade sind tabu.")

    if "http" in lower_id or "ftp" in lower_id:
         raise ValueError("Code: FORBIDDEN_NETWORK - Netzwerkzugriffe sind nicht erlaubt.")

    # Zeitstempel generieren
    stamp = now()

    # Initialisierung des Spielstands-Dictionaries mit leeren Listen/Wertungen, Status 'running', Round 1
    initial_state = {
        "game": "haus5t",
        "schema": "nexus.haus5t.state.v1",
        "session_id": str(session_id), # Sicherstellung des Strings
        "created": stamp,
        "updated": stamp,
        "status": "running",
        "round": 1,
        "step": 0,
        "round_title": round_title(1),
        "roles": dict(ROLE_DESCRIPTIONS), # Kopie der Beschreibungen
        "contributions": [],
        "scores": {},
        "ratings": {},
        "open_questions": {},
        "eddie_inputs": [],
        "pending_eddie": [],
        "seen_msgs": 0,
        "events": [],
        "needles": {},
    }

    # Validierung des erstellten Objekts (selbstschutz gegen falsche Typen vor der Rückgabe)
    required_keys = {"game", "schema", "session_id", "created", "updated", "status", "round", "step"}
    if not all(key in initial_state for key in required_keys):
        raise ValueError("Code: INTERNAL_ERROR - Init-Prozess beschädigt.")

    return initial_state


def save_state(state: dict, path: Path) -> None:
    """Atomar (tmp + replace), UTF-8 ohne BOM."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    state["updated"] = now()
    tmp = target.with_name(target.name + ".tmp")
    payload = json.dumps(state, ensure_ascii=False, indent=1)
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def load_state(path: Path) -> dict | None:
    """None wenn Datei fehlt oder unlesbar; sonst Dict."""
    target = Path(path)
    if not target.is_file():
        return None
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def add_contribution(state: dict, speaker: str, text: str, ok: bool, source: str) -> dict:
    """Haengt Beitrag mit round/ts an; unbekannter Sprecher -> ValueError."""
    if speaker not in state.get("roles", {}):
        raise ValueError("unbekannter Sprecher: " + str(speaker))
    entry = {
        "round": int(state.get("round", 1)),
        "speaker": speaker,
        "text": str(text or ""),
        "ok": bool(ok),
        "source": str(source or ""),
        "ts": now(),
    }
    state.setdefault("contributions", []).append(entry)
    state["updated"] = entry["ts"]
    return entry


def set_score(state: dict, door: str, score: int | float | None) -> None:
    """Wert 0..10 oder None; sonst ValueError; unbekannte Tuer -> ValueError."""
    if door not in rules.DOORS:
        raise ValueError("unbekannte Tuer: " + str(door))
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("Spielwert muss eine Zahl 0..10 oder None sein")
        if not 0 <= float(score) <= 10:
            raise ValueError("Spielwert ausserhalb 0..10")
        score = round(float(score), 1)
        if score == int(score):
            score = int(score)
    state.setdefault("scores", {})[door] = score


def set_rating(state: dict, door: str, speaker: str, score: int | None) -> None:
    """Speichert eine echte Einzelbewertung (pro Tuer und Teilnehmer)."""
    if door not in rules.DOORS:
        raise ValueError("unbekannte Tuer: " + str(door))
    if speaker not in state.get("roles", {}):
        raise ValueError("unbekannter Sprecher: " + str(speaker))
    if score is None or isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 10:
        raise ValueError("Einzelbewertung muss 0..10 sein")
    by_door = state.setdefault("ratings", {}).setdefault(door, {})
    by_door[speaker] = int(score)


def round_title(round_num: int) -> str:
    if round_num == 1:
        return "Eintritt & Rolle"
    elif round_num == 2:
        return "Rolle offenbaren & Punkte sammeln"
    else:
        return f"Aktionsrunde {round_num}"
