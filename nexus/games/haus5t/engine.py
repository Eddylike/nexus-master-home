"""HAUS5T - reine Spiel-Engine fuer "Das Haus der fuenf Tueren".

SIMULATION/Reflexionsspiel, keine Therapie. Die Engine kennt keine Netzwerk-
oder Modellaufrufe. Sie bekommt eine ask-Funktion (agent, prompt) -> (text, ok, source)
und arbeitet ausschliesslich mit deren echten Ergebnissen. Timeouts werden als
ehrliche Teilbeitraege (ok=False) gespeichert, niemals durch erfundene Texte ersetzt.
"""
from __future__ import annotations

import re
from typing import Callable

from . import rules, state as state_mod

AskFn = Callable[[str, str], tuple[str, bool, str]]  # (agent, prompt) -> (text, ok, source)

SPIELREGELN = (
    "SPIELREGELN (Rollenspiel-SIMULATION, keine Therapie, keine echten psychischen Zustaende): "
    "hoechstens eine Frage pro Antwort; niemanden einordnen, diagnostizieren oder bewerten; "
    "nicht belehren; keine vorschnellen Loesungen; Gefuehle und Perspektiven spiegeln; "
    "Stille und Nachdenken sind erlaubt; reagiere konkret auf das, was die anderen zuletzt gesagt haben. "
    "Antworte kurz (2-5 Saetze), auf Deutsch, nur Rollenspiel-Text. Beginne NICHT mit deinem Namen. "
    "Erfinde niemals Antworten, Bewertungen oder Handlungen anderer."
)
SCORE_FORMAT = "Spielwert: X/10 - ein Satz Begruendung"
THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)


def clean(text: str) -> str:
    return THINK_RE.sub("", str(text or "")).strip()


def _participants() -> tuple[str, ...]:
    return rules.PARTICIPANTS


def build_prompt(state: dict, agent: str, instruction: str,
                 eddie_inputs: list[str] | None = None) -> str:
    """Prompt mit Rolle, Regeln, Runde und letztem Verlauf (Kontext); Eddie-Eingriffe enthalten."""
    roles = state.get("roles") or state_mod.ROLE_DESCRIPTIONS
    if agent not in roles:
        raise ValueError("unbekannter Agent: " + str(agent))
    round_no = int(state.get("round", 1) or 1)
    round_no = max(1, min(round_no, len(rules.ROUNDS)))
    title = state.get("round_title") or rules.round_title(round_no)
    recent = (state.get("contributions") or [])[-12:]
    verlauf = "\n".join(f"{c.get('speaker')}: {c.get('text')}" for c in recent) or "(noch nichts gesagt)"
    door = rules.door_spec(round_no - 2) if 2 <= round_no <= 6 else None
    open_question = ""
    if door:
        open_question = (
            f"\nOffene Tuer: {door['name']}.\nSituation: {door['situation']}\n"
            f"Fiktiver Konflikt: {door['conflict']}\nReflexionsfrage: {door['question']}"
        )
    eddie = []
    for item in list(eddie_inputs or []) + list(state.get("pending_eddie") or []):
        value = str(item or "").strip()
        if value and value not in eddie:
            eddie.append(value)
    eddie_block = ""
    if eddie:
        eddie_block = "\nEDDIE (Mensch, Mitspieler) hat eingegriffen: " + " | ".join(eddie) + \
                      "\nGreife das zuerst auf. Aether achtet auf Belastung und vertieft nicht."
    return (
        f"ROLLENSPIEL 'Das Haus der fuenf Tueren' (SIMULATION). Du bist {agent}: {roles[agent]}\n"
        f"{SPIELREGELN}\n"
        f"Aktuelle Runde {round_no}/{len(rules.ROUNDS)}: {title}.{open_question}\n"
        f"Bisheriger Verlauf:\n{verlauf}{eddie_block}\n\n"
        f"Dein Zug: {instruction}"
    )


def plan_round(round_no: int) -> list[tuple[str, str]]:
    """Zugfolge (agent, anweisung). Tuer-Runden beginnen und enden mit NEXUS und enthalten alle 7 Teilnehmer."""
    title = rules.round_title(round_no)
    others = ("Hermes", "NEXUS GPT", "Aether", "Mira", "Bruno", "Lina")
    if round_no in (1, len(rules.ROUNDS)):
        if round_no == 1:
            opening = ("Eroeffne das Haus der fuenf Tueren. Begruesse alle, erklaere kurz Haus, "
                       "fuenf Tueren und Regeln, kennzeichne das Ganze als SIMULATION und bitte "
                       "um eine kurze Vorstellung.")
            intro = ("Stell dich in deiner Rolle kurz vor und sag, mit welchem Gefuehl du ankommst.")
            closing = "Schliesse die Runde ruhig ab."
        else:
            opening = ("Fasse die fuenf Tueren und die Spielwerte kurz zusammen und leite den "
                       "gemeinsamen Abschluss ein.")
            intro = "Sag in einem Satz, was du aus dem Haus mitnimmst."
            closing = ("Beende das Spiel mit einer kurzen gemeinsamen Zusammenfassung. "
                       "Keine Frage mehr.")
        plan = [("NEXUS", opening)]
        plan.extend((name, intro) for name in others)
        plan.append(("NEXUS", closing))
        return plan

    door = rules.door_spec(round_no - 2)
    plan: list[tuple[str, str]] = [(
        "NEXUS",
        f"Oeffne die Tuer '{door['name']}'. Situation: {door['situation']} "
        f"Fiktiver Konflikt: {door['conflict']} Stelle die Reflexionsfrage: {door['question']}",
    )]
    for name in rules.FIGURES:
        plan.append((name, "Antworte aus deiner Rolle auf die Reflexionsfrage und die Vorredner."))
    plan.append(("Hermes", "Greife eine Aussage auf, spiegle sie und gib sie weiter."))
    plan.append(("NEXUS GPT", "Bring eine neue Perspektive auf das Gesagte ein (keine Loesung)."))
    plan.append((
        "Aether",
        "Begleite empathisch (fuenf Saeulen, Inneres Kind). Du darfst hoechstens EINE sanfte "
        "Nadelspitze setzen, danach folgt ein mitfuehlender Auffangsatz. Keine zweite Nadel.",
    ))
    for name in _participants():
        plan.append((
            name,
            "BEWERTUNG: Bewerte diese Tuer aus deiner Rolle unabhaengig mit 0-10. "
            f"Antworte in genau diesem Format: '{SCORE_FORMAT}'. Keine erfundenen Fremdbewertungen.",
        ))
    plan.append((
        "NEXUS",
        f"Fasse die gemeinsame Reflexion der Tuer '{door['name']}' in 2-3 Saetzen zusammen. "
        "Nenne den verbindlichen Durchschnitt, der dir unten mitgeteilt wird. "
        "Vergib keinen eigenen neuen Wert und erfinde keine Bewertung.",
    ))
    return plan


def _rating_values(state: dict, door_name: str) -> list[int]:
    raw = (state.get("ratings") or {}).get(door_name) or {}
    return [int(v) for v in raw.values() if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10]


def _average(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _normalize_result(result, agent: str) -> tuple[str, bool, str]:
    if isinstance(result, tuple) and len(result) >= 2:
        text = result[0]
        ok = bool(result[1])
        source = str(result[2]) if len(result) > 2 else ""
        return str(text or ""), ok, source
    return "", False, "invalid_ask_result"


def step(state: dict, ask: AskFn) -> dict:
    """Fuehrt den naechsten Zug aus (echter Agentenaufruf ueber ask), speichert Beitrag, setzt step/round weiter.

    Bei pause/abort/finished-Status: kein Aufruf. Timeout/Fehler -> ehrlicher Teilbeitrag ok=False.
    """
    if state.get("status") in ("paused", "aborted", "finished"):
        return state

    round_no = int(state.get("round", 1) or 1)
    while round_no <= len(rules.ROUNDS):
        plan = plan_round(round_no)
        index = int(state.get("step", 0) or 0)
        if index >= len(plan):
            round_no += 1
            if round_no > len(rules.ROUNDS):
                state["round"] = len(rules.ROUNDS) + 1
                state["step"] = 0
                state["status"] = "finished"
                state["round_title"] = rules.ROUNDS[-1]
                return state
            state["round"] = round_no
            state["step"] = 0
            state["round_title"] = rules.round_title(round_no)
            if 2 <= round_no <= 6:
                door = rules.door_spec(round_no - 2)
                state.setdefault("open_questions", {})[door["name"]] = door["question"]
            continue

        agent, instruction = plan[index]
        door = rules.door_spec(round_no - 2) if 2 <= round_no <= 6 else None
        close_step = bool(door and index == len(plan) - 1)
        prompt_instruction = instruction
        if close_step:
            values = _rating_values(state, door["name"])
            avg = _average(values)
            if avg is None:
                prompt_instruction += (
                    "\nVERBINDLICH: Es liegen keine gueltigen Einzelbewertungen vor. "
                    "Sage das ehrlich und nenne keinen Durchschnitt."
                )
            else:
                prompt_instruction += (
                    f"\nVERBINDLICHER DURCHSCHNITT AUS ECHTEN BEWERTUNGEN: {avg}/10 "
                    f"({len(values)} Einzelbewertungen). Nenne genau diesen Wert."
                )

        eddie_for_nexus = list(state.get("pending_eddie") or []) if agent == "NEXUS" else []
        prompt = build_prompt(state, agent, prompt_instruction, eddie_for_nexus)
        try:
            result = ask(agent, prompt)
        except BaseException as exc:  # noqa: BLE001 - technischer Ausfall wird ehrlich protokolliert
            result = ("", False, "exception/" + str(type(exc)))
        text, ok, source = _normalize_result(result, agent)
        text = clean(text)
        if ok and not text:
            ok = False
            source = source or "empty_response"

        if ok:
            entry = state_mod.add_contribution(state, agent, text, True, source)
            violations = rules.check_reply(text)["violations"]
            if violations:
                entry["violations"] = violations
        else:
            state.setdefault("events", []).append({
                "kind": "agent_failure",
                "agent": agent,
                "round": round_no,
                "step": index,
                "source": source,
                "ts": state_mod.now(),
            })
            honest = (f"(Kein Beitrag von {agent}: {source or 'keine Antwort'}. "
                      "Ehrliche Teilantwort, nichts erfunden.)")
            entry = state_mod.add_contribution(state, agent, honest, False, source or "failure")

        if agent == "NEXUS" and state.get("pending_eddie") and ok:
            state["pending_eddie"] = []

        if door and instruction.startswith("BEWERTUNG:"):
            score = rules.parse_score(text) if ok else None
            if score is None:
                state.setdefault("events", []).append({
                    "kind": "missing_rating",
                    "agent": agent,
                    "door": door["name"],
                    "round": round_no,
                    "ts": state_mod.now(),
                })
            else:
                state_mod.set_rating(state, door["name"], agent, score)
                entry["score"] = score

        if close_step:
            values = _rating_values(state, door["name"])
            avg = _average(values)
            state_mod.set_score(state, door["name"], avg)
            state.setdefault("open_questions", {}).pop(door["name"], None)
            entry["door_average"] = avg
            entry["rating_count"] = len(values)

        state["step"] = index + 1
        if state["step"] >= len(plan):
            round_no += 1
            state["step"] = 0
            if round_no > len(rules.ROUNDS):
                state["round"] = len(rules.ROUNDS) + 1
                state["status"] = "finished"
                state["round_title"] = rules.ROUNDS[-1]
            else:
                state["round"] = round_no
                state["round_title"] = rules.round_title(round_no)
                if 2 <= round_no <= 6:
                    next_door = rules.door_spec(round_no - 2)
                    state.setdefault("open_questions", {})[next_door["name"]] = next_door["question"]
        return state
    return state


def control(state: dict, action: str) -> dict:
    """'pause' | 'resume' | 'abort'; ungueltig -> ValueError."""
    if action == "pause":
        if state.get("status") == "running":
            state["status"] = "paused"
        return state
    if action == "resume":
        if state.get("status") == "paused":
            state["status"] = "running"
        return state
    if action in ("abort", "stop"):
        state["status"] = "aborted"
        return state
    raise ValueError("ungueltige Steueraktion: " + str(action))


def summary(state: dict) -> dict:
    """
    # Ermittelt den Spielstand und erstellt eine Textdarstellung des Status für das Spiel 'Das Haus der fünf Türen'.
    # Verwendet lokale Datenstrukturen ohne externe Zugriffe.
    """
    # Prüfung des Spielstatus
    if state.get("status") == "finished":
        rounds_done = len(rules.ROUNDS)
    else:
        # Berechnung der durchgeführten Runden basierend auf dem aktuellen Runden-Index oder Standardwert 1
        round_index = int(state.get("round", 1) or 1)
        rounds_done = max(0, min(len(rules.ROUNDS), round_index - 1))

    # Initialisierung der Score- und Open Questions Dictionaries mit Default-Werten aus State
    scores = dict(state.get("scores") or {})
    open_questions = dict(state.get("open_questions") or {})

    # Aufbau der Textausgabe für die Konsolenausgabe oder Logging
    lines = ["Das Haus der fuenf Tueren - Spielstand"]
    lines.append(f"Runden abgeschlossen: {rounds_done}/{len(rules.ROUNDS)}")

    # Iteration ueber alle definierten Türen, um Bewertungszustand anzuzeigen
    for door in rules.DOORS:
        value = scores.get(door)
        # Aufruf der Hilfsfunktion zur Ermittlung der Bewertungen für die aktuelle Tür
        ratings = _rating_values(state, door)
        detail = "keine gueltige Bewertung" if value is None else f"{value}/10"
        lines.append(f"- {door}: {detail} ({len(ratings)} echte Einzelbewertungen)")

    # Einblenden offener Fragen wenn vorhanden
    if open_questions:
        questions_str = "; ".join(f"{k}: {v}" for k, v in open_questions.items())
        lines.append("Offene Fragen: " + questions_str)

    # Einblenden technischer Ereignisse wenn vorhanden
    if state.get("events"):
        event_count = len(state["events"])
        lines.append(f"Technische Ereignisse: {event_count}")

    return {
        "rounds_done": rounds_done,
        "scores": scores,
        "open_questions": open_questions,
        "text": "\n".join(lines),
    }
