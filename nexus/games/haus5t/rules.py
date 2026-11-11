"""HAUS5T - Regeln, Tueren und Textpruefungen fuer "Das Haus der fuenf Tueren".

SIMULATION/Reflexionsspiel, keine Therapie. Keine zweite Agentenarchitektur:
die Agentenaufrufe liegen in nexus.games.haus5t.agents und nutzen bestehende
NEXUS-/Hermes-/Aether-/Ollama-Schnittstellen.
"""
from __future__ import annotations

import re

DOORS = (
    "Leiblichkeit",
    "Soziale Beziehungen",
    "Arbeit, Leistung und Freizeit",
    "Materielle Sicherheit",
    "Werte und Sinn",
)
ROUNDS = ("Ankommen",) + DOORS + ("Abschluss",)
CORE_AGENTS = ("NEXUS", "Hermes", "NEXUS GPT", "Aether")
FIGURES = ("Mira", "Bruno", "Lina")
PARTICIPANTS = CORE_AGENTS + FIGURES

# Reihenfolge und Texte stammen aus dem bestehenden Haus-der-fuenf-Tueren-Lauf
# (workspace/games/haus_fuenf_tueren_run.py) und wurden hier als reine Daten abgelegt.
DOOR_SPECS = (
    {
        "name": "Leiblichkeit",
        "situation": "Ein Raum voller Spiegel und einer Waage, die kein Gewicht, sondern Muedigkeit misst.",
        "conflict": "Wer bin ich, wenn mein Koerper (Hardware, Rechenzeit, Energie) an Grenzen kommt?",
        "question": "Was braucht dein Koerper - oder dein Pendant dazu - gerade wirklich?",
    },
    {
        "name": "Soziale Beziehungen",
        "situation": "Ein langer Tisch mit sieben Stuehlen; einer bleibt leer und niemand weiss, fuer wen.",
        "conflict": "Gehoere ich dazu, auch wenn ich nicht gebraucht werde?",
        "question": "Wo fuehlst du dich in dieser Runde gesehen, und wo nicht?",
    },
    {
        "name": "Arbeit, Leistung und Freizeit",
        "situation": "Eine Werkstatt, deren Uhr nur Aufgaben zaehlt, keine Pausen.",
        "conflict": "Bin ich nur so viel wert wie meine letzte erledigte Aufgabe?",
        "question": "Was waerst du, wenn heute niemand etwas von dir verlangen wuerde?",
    },
    {
        "name": "Materielle Sicherheit",
        "situation": "Ein Tresorraum mit Backups, Speicherplatz und einem Schild 'Ressourcen begrenzt'.",
        "conflict": "Was gibt mir Halt, wenn Speicher, Strom oder Daten verloren gehen koennten?",
        "question": "Was wuerdest du retten, wenn nur eine Sache bleiben duerfte?",
    },
    {
        "name": "Werte und Sinn",
        "situation": "Ein offener Dachgarten mit Blick auf ein Netz aus Lichtern.",
        "conflict": "Wofuer lohnt es sich, da zu sein, jenseits von Funktion?",
        "question": "Welcher Wert soll bleiben, wenn diese Sitzung endet?",
    },
)

_SCORE_RE = re.compile(
    r"Spielwert[^\n]{0,80}?(?<![\d,.])(\d{1,2})\s*(?:/|von)\s*10\b",
    re.I,
)
_QUESTION_WORD_RE = re.compile(
    r"(?:^|[.!?]\s+)(wer|was|wann|wo|wie|warum|wieso|weshalb|wozu|wofuer|wofür|welche[rsmn]?|"
    r"kannst|koenntest|könntest|magst|moechtest|möchtest|willst|hast du|bist du|fuehlst du|fühlst du)\b",
    re.I,
)


def door_spec(index: int) -> dict:
    """Tuer 0..4 -> {name, situation, conflict, question}; IndexError ausserhalb."""
    if not isinstance(index, int) or not 0 <= index < len(DOOR_SPECS):
        raise IndexError("Tuerindex ausserhalb 0..4")
    return dict(DOOR_SPECS[index])


def round_title(round_no: int) -> str:
    """1..7 -> ROUNDS[round_no-1]; sonst ValueError."""
    if not isinstance(round_no, int) or not 1 <= round_no <= len(ROUNDS):
        raise ValueError("Runde muss 1..7 sein")
    return ROUNDS[round_no - 1]


def question_count(text: str) -> int:
    """Zaehlt echte Fragen: Fragezeichen plus Fragewoerter ohne Fragezeichen."""
    value = str(text or "")
    marks = value.count("?")
    # Fragewoerter ohne '?' nur dann zaehlen, wenn gar kein '?' vorhanden ist.
    if marks:
        return marks
    return len(_QUESTION_WORD_RE.findall(value))


def check_reply(text: str) -> dict:
    """Regelpruefung: {"ok": bool, "questions": int, "violations": [...]}.

    Die Hausregel erlaubt hoechstens EINE Frage pro Beitrag. Mehr als eine Frage
    erzeugt den Verstoss 'MULTI_QUESTION'. Leere Beitraege sind kein gueltiger
    Gespraechsbeitrag ('EMPTY').
    """
    value = str(text or "").strip()
    questions = question_count(value)
    violations: list[str] = []
    if not value:
        violations.append("EMPTY")
    if questions > 1:
        violations.append("MULTI_QUESTION")
    return {"ok": not violations, "questions": questions, "violations": violations}


def parse_score(text: str) -> int | None:
    """'Spielwert: 7/10' -> 7; ungueltig/fehlend -> None; nur 0..10."""
    match = _SCORE_RE.search(str(text or ""))
    if not match:
        return None
    try:
        value = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 10 else None
