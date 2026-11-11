"""Sprechzimmer-Service: verbindet die vier Kernagenten mit ihren echten lokalen Laufzeiten."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from nexus.sprechzimmer import memory

NEXUS_PROMPT = (
    "Du bist NEXUS, die koordinierende Intelligenz in NEXUS Master Home. Deutsch, klar, ehrlich. "
    "Du moderierst das Sprechzimmer, hoerst den anderen Agenten zu, fasst zusammen und triffst Entscheidungen. "
    "Behaupte keine Aktionen, die nicht in den Nachrichten belegt sind."
)
TRANSCRIPT_LIMIT = 14


def _transcript(session: dict) -> str:
    lines = []
    for message in (session.get("messages") or [])[-TRANSCRIPT_LIMIT:]:
        lines.append(f"{message.get('speaker')}: {message.get('text')}")
    return "\n".join(lines) or "(noch keine Beitraege)"


def _prompt(agent: str, session: dict, own_memory: list[dict]) -> str:
    mem = "\n".join(f"- {m.get('text')}" for m in own_memory[-6:]) or "- keine"
    return (
        f"Sprechzimmer, Teilnehmer: {', '.join(session.get('participants') or [])}. "
        f"Du bist {agent}. Antworte in hoechstens 6 Saetzen auf Deutsch.\n"
        f"Dein eigenes Gedaechtnis:\n{mem}\n\nBisheriges Gespraech:\n{_transcript(session)}\n\n"
        f"Dein Beitrag als {agent}:"
    )


def ask_agent(agent: str, session: dict, base_dir: Path) -> str:
    own_memory: list[dict] = []
    try:
        own_memory = memory.read_memory(agent, agent, base_dir)
    except Exception:
        own_memory = []
    prompt = _prompt(agent, session, own_memory)
    if agent == "Hermes":
        from nexus.integrations.hermes_agent import ask as hermes_ask
        result = hermes_ask(prompt)
    elif agent == "Aether":
        from nexus.integrations.aether_agent import ask as aether_ask
        result = aether_ask(prompt)
    elif agent == "NEXUS GPT":
        from nexus.integrations.gpt_local_agent import ask as gpt_ask
        result = gpt_ask(prompt)
    else:
        from nexus.integrations.gpt_local_agent import ask as gpt_ask
        result = gpt_ask(prompt, system=NEXUS_PROMPT)
    text = str(result.get("reply") or "").strip() if isinstance(result, dict) else str(result or "").strip()
    error = result.get("error") if isinstance(result, dict) else None
    if not text:
        text = f"[{agent} nicht erreichbar: {error or 'leere Antwort'}]"
    try:
        memory.append_memory(agent, {"text": text[:2000],
                                     "ts": datetime.now().astimezone().isoformat(timespec="seconds")}, base_dir)
    except Exception:
        pass
    return text


def make_ask(base_dir: Path, emit: Callable[[str, str, str], None] | None = None) -> Callable[[str, dict], str]:
    def ask(agent: str, session: dict) -> str:
        if emit:
            try:
                emit(agent, "beitrag", f"Sprechzimmer: {agent} antwortet")
            except Exception:
                pass
        return ask_agent(agent, session, base_dir)

    return ask
