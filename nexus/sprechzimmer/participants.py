"""Sprechzimmer: Teilnehmerverwaltung (SZ-02). Moderator und Einladerecht: NEXUS."""
from __future__ import annotations

KNOWN_AGENTS = ("NEXUS", "NEXUS GPT", "Hermes", "Aether")
MODERATOR = "NEXUS"


def invite(participants: list[str], agent: str, by: str) -> list[str]:
    """Neue Teilnehmerliste mit agent. by != MODERATOR -> PermissionError; agent nicht in KNOWN_AGENTS -> ValueError.
    Doppelte Einladung aendert nichts. Die uebergebene Liste wird nicht veraendert."""
    if by != MODERATOR:
        raise PermissionError("Zugriff verweigert für " + by + ". Nur " + MODERATOR + " darf Teilnehmer einladen.")

    if agent not in KNOWN_AGENTS:
        raise ValueError("Unbekannter Agent '" + agent + "'. Erlaubte: " + str(KNOWN_AGENTS))

    result = list(participants)
    if agent not in result:
        result.append(agent)

    return result


def validate_participants(participants: list[str]) -> list[str]:
    """Prueft alle Namen gegen KNOWN_AGENTS (unbekannt -> ValueError), entfernt Duplikate, Reihenfolge bleibt."""
    for name in participants:
        if name not in KNOWN_AGENTS:
            raise ValueError("Unbekannter Teilnehmer '" + name + "'. Erlaubte: " + str(KNOWN_AGENTS))

    seen = set()
    result = []
    for name in participants:
        if name not in seen:
            seen.add(name)
            result.append(name)

    return result
