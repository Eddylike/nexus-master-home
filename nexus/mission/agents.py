"""Ausfuehrung von Missionsschritten durch die vier Kernagenten.

Kein Agent darf Erfolg behaupten, den seine Laufzeit nicht belegt: Der
Rueckgabewert enthaelt immer success/output/evidence/error aus der echten
Antwort des jeweiligen Adapters.
"""
from __future__ import annotations

import time
from typing import Any

CORE_AGENTS = ("NEXUS", "NEXUS GPT", "Hermes", "Aether")

NEXUS_SYSTEM = (
    "Du bist NEXUS, die koordinierende Intelligenz in NEXUS Master Home. Deutsch, knapp, ehrlich. "
    "Du planst, moderierst und fasst zusammen. Behaupte nie eine Ausfuehrung, die nicht in den "
    "Ergebnissen belegt ist. Nenne offene Punkte und Unsicherheiten."
)
AETHER_SYSTEM = (
    "Du bist Aether (Dr. Maslenata Umbarum), Strategie- und Qualitaetsagent in NEXUS Master Home. "
    "Deutsch, kritisch, ehrlich. Pruefe Risiken, Nebenwirkungen und Belege. Keine Erfindungen."
)
GPT_SYSTEM = (
    "Du bist NEXUS GPT, Wissens- und Analyseagent in NEXUS Master Home. Deutsch, strukturiert, ehrlich. "
    "Trenne Fakten von Annahmen und erfinde keine Quellen."
)


def _context(step: dict, mission: dict, store, agent: str, limit: int = 5000) -> str:
    parts = []
    for other in mission.get("steps") or []:
        if other.get("id") == step.get("id"):
            continue
        result = other.get("result") or {}
        if result.get("success") and result.get("output"):
            parts.append(f"[{other.get('agent')} / {other.get('title')}]\n{result['output']}")
    try:
        mem = store.recall(mission.get("id"), step.get("instruction", ""), requester=agent, limit=8)
        for entry in mem:
            parts.append(f"[Gedaechtnis {entry.get('agent')}]\n{entry.get('text')}")
    except Exception:
        pass
    text = "\n\n".join(parts)
    return text[-limit:]


def _task(step: dict, mission: dict, context: str) -> str:
    return (
        f"Gesamtziel: {mission.get('goal')}\n"
        f"Teilaufgabe ({step.get('action')}): {step.get('title')}\n"
        f"Auftrag: {step.get('instruction')}\n\n"
        f"Bisherige Ergebnisse:\n{context or '(keine)'}"
    )


def _from_llm(agent: str, result: dict, started: float) -> dict:
    reply = str(result.get("reply") or "").strip()
    ok = bool(result.get("success")) and bool(reply)
    return {
        "success": ok,
        "output": reply,
        "evidence": f"model={result.get('model') or 'qwen3.5:9b'}; provenance={result.get('provenance') or 'model_response'}",
        "error": None if ok else str(result.get("error") or "EMPTY_RESPONSE"),
        "agent": agent,
        "latency_ms": int((time.time() - started) * 1000),
    }


def run_agent(agent: str, step: dict, mission: dict, store) -> dict[str, Any]:
    started = time.time()
    context = _context(step, mission, store, agent)
    task = _task(step, mission, context)
    action = step.get("action")

    if agent == "Hermes":
        from nexus.integrations.hermes_agent import ask as hermes_ask
        result = hermes_ask(task)
        reply = str(result.get("reply") or "").strip()
        calls = ((result.get("data") or {}).get("tool_calls") or [])
        evidence = "hermes: " + (", ".join(str(c.get("tool")) for c in calls) if calls else
                                  str(((result.get("data") or {}).get("execution_status") or "keine Werkzeuge")))
        executed = action != "execute_technical" or bool(calls)
        ok = bool(result.get("success")) and bool(reply) and executed
        error = str(result.get("error") or "HERMES_FAILED")
        if bool(result.get("success")) and not executed:
            error = "NO_TOOL_EXECUTION"
        return {"success": ok, "output": reply, "evidence": evidence,
                "error": None if ok else error,
                "agent": agent, "latency_ms": int((time.time() - started) * 1000),
                "tool_calls": calls, "execution_status": (result.get("data") or {}).get("execution_status")}

    if agent == "Aether":
        from nexus.integrations.aether_agent import ask as aether_ask
        result = aether_ask(task)
    elif agent == "NEXUS GPT":
        from nexus.integrations.gpt_local_agent import ask as gpt_ask
        result = gpt_ask(task, system=GPT_SYSTEM)
    else:
        from nexus.integrations.gpt_local_agent import ask as gpt_ask
        if action == "synthesize":
            system = NEXUS_SYSTEM + " Erstelle jetzt die abschliessende Gesamtantwort fuer Eddie."
        else:
            system = NEXUS_SYSTEM
        result = gpt_ask(task, system=system)
    return _from_llm(agent, result, started)


def _reachable(payload: dict) -> bool:
    if "reachable" in payload:
        return bool(payload.get("reachable"))
    return bool(payload.get("ok") or payload.get("success"))


def health() -> dict[str, Any]:
    agents: dict[str, Any] = {}
    try:
        from nexus.integrations.gpt_local_agent import health as gpt_health
        base = gpt_health()
        agents["NEXUS"] = {"reachable": _reachable(base), "detail": base}
        agents["NEXUS GPT"] = {"reachable": _reachable(base), "detail": base}
    except Exception as exc:
        agents["NEXUS"] = {"reachable": False, "error": str(exc)[:200]}
        agents["NEXUS GPT"] = {"reachable": False, "error": str(exc)[:200]}
    try:
        from nexus.integrations.aether_agent import health as aether_health
        a = aether_health()
        agents["Aether"] = {"reachable": _reachable(a), "detail": a}
    except Exception as exc:
        agents["Aether"] = {"reachable": False, "error": str(exc)[:200]}
    try:
        from nexus.integrations.hermes_agent import health as hermes_health
        h = hermes_health()
        agents["Hermes"] = {"reachable": _reachable(h), "detail": h}
    except Exception as exc:
        agents["Hermes"] = {"reachable": False, "error": str(exc)[:200]}
    return agents
