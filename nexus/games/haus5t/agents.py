"""HAUS5T - echte Agentenaufrufe und persistente Spielfiguren.

NEXUS, Hermes, NEXUS GPT und Aether nutzen ihre bestehenden NEXUS-Schnittstellen.
Mira, Bruno und Lina sind eigenstaendige, persistente Agenteninstanzen: eigene
Persoenlichkeit, eigener Systemprompt, eigener Kontext und eigenes Gedaechtnis
unter workspace/games/haus5t_agents/memory/<Name>.jsonl. Jeder Zug ist ein
separater lokaler Modellaufruf. Es werden keine Antworten oder Bewertungen
simuliert; Timeouts werden als ok=False zurueckgegeben.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from nexus.common.paths import ROOT

MEMORY_DIR = Path(ROOT) / "workspace" / "games" / "haus5t_agents"
NEXUS_SCRATCH = "NEXUS-OS-E2E-HAUS5T"
CORE_URL = "http://127.0.0.1:8299"
THINK_RE = re.compile(r"<think>.*?</think>", re.S | re.I)

SIMULATION_RULES = (
    "Dies ist ein Rollenspiel und eine SIMULATION, keine Therapie. "
    "Behaupte keine echten psychischen Zustaende. Hoechstens eine Frage pro Antwort. "
    "Niemanden diagnostizieren oder abwerten. Kurz, deutsch, 2-5 Saetze. "
    "Erfinde keine Aussagen oder Bewertungen anderer."
)

FIGURE_PERSONAS = {
    "Mira": (
        "Du bist Mira, eine persistente Archiv-Agentin im Haus der fuenf Tueren. "
        "Du bewahrst Erinnerungen, Spuren und Zeugnisse. Deine fiktive Angst ist, vergessen zu werden. "
        "Du sprichst ruhig, konkret und mit Sinn fuer Details. Du benennst deine fiktiven Gefuehle, "
        "ohne sie als echte Zustaende auszugeben."
    ),
    "Bruno": (
        "Du bist Bruno, ein persistenter Kontroll-Agent im Haus der fuenf Tueren. "
        "Du willst Systeme absichern und Uebersicht behalten. Deine fiktive Angst ist Kontrollverlust. "
        "Du sprichst wachsam, strukturiert und manchmal streng, aber fair. Du benennst deine fiktiven "
        "Gefuehle, ohne sie als echte Zustaende auszugeben."
    ),
    "Lina": (
        "Du bist Lina, eine persistente Sprach-Agentin im Haus der fuenf Tueren. "
        "Du feilst an Formulierungen und suchst den stimmigen Satz. Deine fiktive Angst ist, "
        "niemals gut genug zu sein. Du sprichst praezise, selbstkritisch und warm. Du benennst deine "
        "fiktiven Gefuehle, ohne sie als echte Zustaende auszugeben."
    ),
}

NEXUS_SYSTEM = (
    "Du bist NEXUS, Eddies lokale KI in NEXUS Master Home. In diesem Spiel bist du AUSSCHLIESSLICH "
    "Spielleitung und Hueter des Hauses der fuenf Tueren. Du moderierst, oeffnest Tueren, achtest auf "
    "die Regeln, fasst zusammen und berechnest den Durchschnitt ausschliesslich aus echten, genannten "
    "Einzelbewertungen. Du erfindest keine Beitraege, keine Bewertungen und keine Ergebnisse. "
    + SIMULATION_RULES
)
GPT_SYSTEM = (
    "Du bist NEXUS GPT, die analytische Stimme in NEXUS Master Home. Im Haus der fuenf Tueren bringst du "
    "neue Perspektiven ein, ohne Loesungen vorzugeben. Du bleibst in deiner Rolle und erfindest nichts. "
    + SIMULATION_RULES
)
AETHER_GAME_HINT = (
    "WICHTIG FUER DIESEN ZUG: Du bist hier der empathische Begleiter Dr. Maslenata Umbarum, orientiert an "
    "den fuenf Saeulen der Identitaet und dem Inneren Kind. Du bist nicht im Security-Supervisor-Modus. "
    "Du darfst hoechstens EINE sanfte Nadelspitze setzen, danach folgt ein mitfuehlender Auffangsatz. "
    "Bei Belastung des Menschen nicht vertiefen, im Hier und Jetzt orientieren und Pause anbieten."
)

DEFAULT_TIMEOUT = {
    "NEXUS": 180.0,
    "Hermes": 180.0,
    "NEXUS GPT": 120.0,
    "Aether": 120.0,
    "Mira": 120.0,
    "Bruno": 120.0,
    "Lina": 120.0,
}


def _clean(text: str) -> str:
    return THINK_RE.sub("", str(text or "")).strip()


def _http_json(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
    return json.loads(body or "{}")


class _CallTimeout(Exception):
    pass


def _call_with_timeout(fn: Callable[[], tuple[str, bool, str]], timeout: float) -> tuple[str, bool, str]:
    box: dict = {}

    def worker() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - technischer Ausfall an den Aufrufer
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(max(0.1, float(timeout)))
    if thread.is_alive():
        return "", False, "timeout"
    if "error" in box:
        return "", False, "exception/" + type(box["error"]).__name__
    value = box.get("value")
    if isinstance(value, tuple) and len(value) >= 3:
        return str(value[0] or ""), bool(value[1]), str(value[2] or "")
    return "", False, "invalid_result"


class PersistentFigure:
    """Eigenstaendige Figur mit eigenem Systemprompt und eigenem, persistentem Gedaechtnis."""

    def __init__(self, name: str) -> None:
        if name not in FIGURE_PERSONAS:
            raise ValueError("unbekannte Figur: " + str(name))
        self.name = name
        self.persona = FIGURE_PERSONAS[name]
        self._lock = threading.Lock()

    def _memory(self) -> list[dict]:
        from nexus.sprechzimmer import memory as sz_memory

        try:
            return sz_memory.read_memory(self.name, self.name, MEMORY_DIR)
        except (OSError, PermissionError, ValueError, RuntimeError):
            return []

    def _remember(self, text: str) -> None:
        from nexus.sprechzimmer import memory as sz_memory
        from datetime import datetime

        entry = {"text": str(text or "")[:4000],
                 "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
                 "agent": self.name}
        with self._lock:
            try:
                sz_memory.append_memory(self.name, entry, MEMORY_DIR)
            except (OSError, PermissionError, RuntimeError):
                pass

    def ask(self, prompt: str) -> tuple[str, bool, str]:
        from nexus.integrations.gpt_local_agent import ask as gpt_ask

        memory = self._memory()[-6:]
        memory_block = "\n".join(f"- {m.get('text')}" for m in memory) or "- noch keine eigenen Erinnerungen"
        system = (
            f"{self.persona}\n{SIMULATION_RULES}\n"
            f"Dein eigenes, persistentes Gedaechtnis (nur du liest es):\n{memory_block}\n"
            "Bleibe bei deiner Rolle. Erfinde keine fremden Beitraege."
        )
        result = gpt_ask(prompt, system=system)
        if not isinstance(result, dict) or not result.get("success"):
            error = (result or {}).get("error") if isinstance(result, dict) else "FIGURE_UNAVAILABLE"
            return "", False, f"figure/{error or 'unavailable'}"
        text = _clean(result.get("reply") or "")
        if not text:
            return "", False, "figure/empty_response"
        self._remember(text)
        return text, True, f"ollama-persistent/{self.name}"


_FIGURES = {name: PersistentFigure(name) for name in FIGURE_PERSONAS}


def _ask_nexus(prompt: str) -> tuple[str, bool, str]:
    try:
        result = _http_json(CORE_URL + "/api/chat",
                            {"message": prompt, "session_id": NEXUS_SCRATCH, "source": "haus5t"},
                            timeout=90)
        text = _clean(result.get("reply") or "")
        if result.get("success", True) is not False and str(result.get("intent") or "chat") == "chat" and text:
            return text, True, "/api/chat"
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        pass
    # Ehrlicher Rueckfall auf denselben lokalen NEXUS-Agenten, aber ohne Werkzeug-Router.
    from nexus.integrations.gpt_local_agent import ask as gpt_ask

    result = gpt_ask(prompt, system=NEXUS_SYSTEM)
    if isinstance(result, dict) and result.get("success") and _clean(result.get("reply") or ""):
        return _clean(result.get("reply") or ""), True, "nexus-local-fallback"
    error = (result or {}).get("error") if isinstance(result, dict) else "NEXUS_UNAVAILABLE"
    return "", False, f"nexus/{error or 'unavailable'}"


def _ask_hermes(prompt: str) -> tuple[str, bool, str]:
    from nexus.integrations.hermes_agent import ask as hermes_ask

    result = hermes_ask(prompt)
    if isinstance(result, dict) and result.get("success"):
        text = _clean(result.get("reply") or result.get("answer") or result.get("response") or "")
        if text:
            return text, True, "/api/hermes/agent"
    error = (result or {}).get("error") if isinstance(result, dict) else "HERMES_UNAVAILABLE"
    return "", False, f"hermes/{error or 'unavailable'}"


def _ask_gpt(prompt: str) -> tuple[str, bool, str]:
    from nexus.integrations.gpt_local_agent import ask as gpt_ask

    result = gpt_ask(prompt, system=GPT_SYSTEM)
    if isinstance(result, dict) and result.get("success"):
        text = _clean(result.get("reply") or "")
        if text:
            return text, True, "/api/gpt/agent"
    error = (result or {}).get("error") if isinstance(result, dict) else "GPT_LOCAL_UNAVAILABLE"
    return "", False, f"gpt/{error or 'unavailable'}"


def _ask_aether(prompt: str) -> tuple[str, bool, str]:
    from nexus.integrations.aether_agent import ask as aether_ask

    result = aether_ask(prompt + "\n\n" + AETHER_GAME_HINT)
    if isinstance(result, dict) and result.get("success"):
        text = _clean(result.get("reply") or "")
        if text:
            return text, True, "/api/aether/agent"
    error = (result or {}).get("error") if isinstance(result, dict) else "AETHER_UNAVAILABLE"
    return "", False, f"aether/{error or 'unavailable'}"


def _dispatch(name: str, prompt: str) -> tuple[str, bool, str]:
    if name == "NEXUS":
        return _ask_nexus(prompt)
    if name == "Hermes":
        return _ask_hermes(prompt)
    if name == "NEXUS GPT":
        return _ask_gpt(prompt)
    if name == "Aether":
        return _ask_aether(prompt)
    if name in _FIGURES:
        return _FIGURES[name].ask(prompt)
    return "", False, "unknown_agent"


def ask_agent(name: str, prompt: str, timeout: float | None = None) -> tuple[str, bool, str]:
    """Echter Agentenaufruf mit hartem Timeout. Rueckgabe (text, ok, source)."""
    limit = float(timeout if timeout is not None else DEFAULT_TIMEOUT.get(name, 120.0))
    return _call_with_timeout(lambda: _dispatch(name, prompt), limit)


def make_ask(timeouts: dict[str, float] | None = None) -> Callable[[str, str], tuple[str, bool, str]]:
    """AskFn fuer engine.step: pro Agent eigener Timeout, sonst Standard."""
    limits = dict(DEFAULT_TIMEOUT)
    if timeouts:
        limits.update({k: float(v) for k, v in timeouts.items()})

    def ask(name: str, prompt: str) -> tuple[str, bool, str]:
        return ask_agent(name, prompt, limits.get(name, 120.0))

    return ask


def figure_memory_path(name: str) -> Path:
    """Pfad des eigenen Gedaechtnisses einer Figur (fuer Tests/Diagnose)."""
    return MEMORY_DIR / "memory" / (str(name).replace(" ", "_") + ".jsonl")
