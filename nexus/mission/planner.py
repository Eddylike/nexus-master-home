"""Zielanalyse und Aufgabenplanung fuer NEXUS-Missionen.

Das lokale Modell liefert ausschliesslich JSON. Bei Ausfall oder ungueltigem
Plan greift ein deterministischer, ehrlicher Fallback-Plan. Es werden nur die
vier vorhandenen Kernagenten und bekannte Aktionen zugelassen.
"""
from __future__ import annotations

import json
import re
from typing import Any

AGENTS = ("NEXUS", "NEXUS GPT", "Hermes", "Aether")
ACTIONS = ("analyze", "research", "execute_technical", "strategy", "verify", "synthesize", "answer")
MAX_STEPS = 8

PLANNER_SYSTEM = (
    "Du bist der NEXUS-Missionsplaner. Zerlege das Ziel in konkrete Teilaufgaben fuer genau diese Agenten: "
    "NEXUS (Koordination, Zusammenfassung), NEXUS GPT (Wissen, Analyse, Text), "
    "Hermes (nur echte technische Ausfuehrung: PC, Dateien, Terminal), "
    "Aether (Strategie, Risiko, Qualitaetskontrolle). "
    "Erlaubte Aktionen: " + ", ".join(ACTIONS) + ". "
    "Antworte NUR als JSON: {\"analysis\":\"...\",\"steps\":[{\"title\":\"...\",\"agent\":\"NEXUS|NEXUS GPT|Hermes|Aether\","
    "\"action\":\"...\",\"instruction\":\"...\",\"depends_on\":[],\"critical\":false}]}. "
    "Maximal 8 Schritte. Unabhaengige Schritte bekommen keine depends_on (Parallelitaet). "
    "Der letzte Schritt ist action=synthesize mit agent=NEXUS und haengt von den vorherigen ab. "
    "critical=true nur fuer externe oder irreversible Aktionen (senden, buchen, loeschen, kaufen, veroeffentlichen)."
)

DESTRUCTIVE_RX = re.compile(
    r"(?i)(senden|versenden|abschicken|buchen|bestellen|kaufen|bezahlen|l(?:ö|oe|o)sch|entfernen|"
    r"veroeffentlichen|veröffentlichen|whatsapp|e-?mail\s+send|push\s+auf\s+main|deploy)"
)


def detect_critical(text: str) -> bool:
    return bool(DESTRUCTIVE_RX.search(str(text or "")))


def _fallback(goal: str) -> dict[str, Any]:
    low = goal.lower()
    if any(w in low for w in ("recherche", "markt", "analyse", "vergleich", "studie", "information")):
        steps = [
            {"title": "Ziel und benoetigte Informationen klaeren", "agent": "NEXUS", "action": "analyze",
             "instruction": "Analysiere das Ziel, benenne Teilfragen und benoetigte Quellen.", "depends_on": []},
            {"title": "Fachliche Analyse", "agent": "NEXUS GPT", "action": "research",
             "instruction": "Erstelle eine strukturierte fachliche Analyse mit klaren Annahmen und offenen Punkten.",
             "depends_on": [0]},
            {"title": "Strategische Bewertung", "agent": "Aether", "action": "strategy",
             "instruction": "Bewerte Risiken, Chancen und Qualitaet der Analyse.", "depends_on": [0]},
            {"title": "Qualitaetskontrolle", "agent": "Aether", "action": "verify",
             "instruction": "Pruefe, ob die Analyse das Ziel beantwortet und keine Behauptungen ohne Beleg enthaelt.",
             "depends_on": [1, 2]},
        ]
    elif any(w in low for w in ("code", "fehler", "bug", "system", "repar", "terminal", "pc", "datei", "programm")):
        steps = [
            {"title": "Technische Aufgabe eingrenzen", "agent": "NEXUS", "action": "analyze",
             "instruction": "Beschreibe das technische Ziel, moegliche Ursachen und noetige Pruefschritte.",
             "depends_on": []},
            {"title": "Technische Ausfuehrung", "agent": "Hermes", "action": "execute_technical",
             "instruction": "Fuehre die technisch notwendigen Schritte aus und melde echte Ergebnisse.",
             "depends_on": [0], "critical": detect_critical(goal)},
            {"title": "Ergebnis pruefen", "agent": "Aether", "action": "verify",
             "instruction": "Pruefe das technische Ergebnis auf Vollstaendigkeit und Nebenwirkungen.",
             "depends_on": [1]},
        ]
    else:
        steps = [
            {"title": "Ziel analysieren", "agent": "NEXUS", "action": "analyze",
             "instruction": "Zerlege das Ziel, benenne Prioritaeten und offene Fragen.", "depends_on": []},
            {"title": "Wissen und Vorschlaege", "agent": "NEXUS GPT", "action": "answer",
             "instruction": "Erarbeite konkrete, pruefbare Vorschlaege zum Ziel.", "depends_on": []},
            {"title": "Strategische Qualitaetskontrolle", "agent": "Aether", "action": "strategy",
             "instruction": "Bewerte die Vorschlaege, Risiken und naechsten Schritte.", "depends_on": [0, 1]},
        ]
    steps.append({"title": "Gesamtergebnis erstellen", "agent": "NEXUS", "action": "synthesize",
                  "instruction": "Fasse alle Ergebnisse zu einer klaren Antwort zusammen. Nenne offene Punkte ehrlich.",
                  "depends_on": list(range(len(steps)))})
    for index, item in enumerate(steps):
        item["id"] = index
        item.setdefault("critical", False)
        item.setdefault("depends_on", [])
        item["status"] = "pending"
    return {"analysis": f"Deterministischer Plan fuer: {goal}", "steps": steps, "planner": "fallback", "error": None}


def _normalize(raw: Any, goal: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("PLAN_NOT_AN_OBJECT")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError("PLAN_EMPTY")
    steps: list[dict[str, Any]] = []
    for i, item in enumerate(steps_raw[:MAX_STEPS]):
        if not isinstance(item, dict):
            continue
        agent = str(item.get("agent") or "NEXUS").strip()
        low = agent.lower()
        if "gpt" in low:
            agent = "NEXUS GPT"
        elif "hermes" in low:
            agent = "Hermes"
        elif "aether" in low:
            agent = "Aether"
        else:
            agent = "NEXUS"
        action = str(item.get("action") or "analyze").strip().lower()
        if action not in ACTIONS:
            action = "analyze"
        deps = [int(d) for d in (item.get("depends_on") or [])
                if isinstance(d, (int, float, str)) and str(d).isdigit() and 0 <= int(d) < i]
        steps.append({
            "id": i,
            "title": str(item.get("title") or action)[:160],
            "agent": agent,
            "action": action,
            "instruction": str(item.get("instruction") or item.get("task") or goal)[:2000],
            "depends_on": sorted(set(deps)),
            "critical": bool(item.get("critical")) or detect_critical(str(item.get("instruction") or "")),
            "status": "pending",
        })
    if not steps:
        raise ValueError("PLAN_EMPTY")
    if not any(s["action"] == "synthesize" for s in steps):
        steps.append({"id": len(steps), "title": "Gesamtergebnis erstellen", "agent": "NEXUS",
                      "action": "synthesize", "instruction": "Fasse alle Ergebnisse zusammen.",
                      "depends_on": list(range(len(steps))), "critical": False, "status": "pending"})
    return {"analysis": str(raw.get("analysis") or "")[:4000], "steps": steps,
            "planner": "llm", "error": None}


def plan_mission(goal: str, context: str = "") -> dict[str, Any]:
    text = str(goal or "").strip()
    if not text:
        raise ValueError("GOAL_REQUIRED")
    try:
        from nexus.integrations.gpt_local_agent import ask
        prompt = f"Ziel: {text}"
        if context:
            prompt += f"\nBekannter Kontext:\n{context[:3000]}"
        result = ask(prompt, fmt="json", system=PLANNER_SYSTEM)
        if not result.get("success"):
            raise RuntimeError(str(result.get("error") or "PLANNER_UNAVAILABLE"))
        reply = str(result.get("reply") or "")
        match = re.search(r"\{.*\}", reply, re.S)
        return _normalize(json.loads(match.group(0) if match else reply), text)
    except Exception as exc:
        plan = _fallback(text)
        plan["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return plan
