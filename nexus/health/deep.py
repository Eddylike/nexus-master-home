"""Deep Health: trennt Erreichbarkeit von echter Funktionsfaehigkeit.

Regel: Ein antwortender Port allein ist kein "gruen". Ohne echte Probe lautet
der Status hoechstens "degraded" und functional_verified bleibt false.
"""
from __future__ import annotations

import socket
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from nexus.common.paths import WORKSPACE

BASE = "http://127.0.0.1:8299"
PROBE_TASK = "Antworte ausschliesslich mit dem Wort OK. Fuehre keine Werkzeuge aus."


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _server(timeout: float) -> dict:
    started = time.time()
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=timeout) as response:
            body = response.read(4096)
            return {"available": True, "http_status": response.status, "latency_ms": int((time.time() - started) * 1000),
                    "payload_ok": bool(body)}
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"[:300],
                "latency_ms": int((time.time() - started) * 1000)}


def _port(timeout: float) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 8299), timeout=timeout):
            return True
    except OSError:
        return False


def _writable(path: Path) -> dict:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".health-", delete=True):
            pass
        return {"ok": True, "path": str(path)}
    except Exception as exc:
        return {"ok": False, "path": str(path), "error": f"{type(exc).__name__}: {exc}"[:200]}


def _imports() -> dict:
    names = ["nexus.chat.server", "nexus.swarm.core", "nexus.mission.engine",
             "nexus.sprechzimmer.api", "nexus.integrations.aether_agent",
             "nexus.integrations.hermes_agent", "nexus.integrations.gpt_local_agent",
             "nexus.memory.store", "nexus.health_watch"]
    broken = {}
    for name in names:
        try:
            __import__(name)
        except Exception as exc:
            broken[name] = f"{type(exc).__name__}: {exc}"[:200]
    return {"ok": not broken, "broken": broken, "checked": len(names)}


def _probe_agent(name: str, timeout_hint: str = "model") -> dict:
    try:
        if name == "Hermes":
            from nexus.integrations.hermes_agent import ask
        elif name == "Aether":
            from nexus.integrations.aether_agent import ask
        else:
            from nexus.integrations.gpt_local_agent import ask
        started = time.time()
        result = ask(PROBE_TASK)
        reply = str(result.get("reply") or "").strip()
        ok = bool(result.get("success")) and "ok" in reply.lower()
        return {"functional": "ok" if ok else "failed", "success": ok,
                "latency_ms": int((time.time() - started) * 1000),
                "error": None if ok else str(result.get("error") or "PROBE_NOT_OK")[:200],
                "note": timeout_hint}
    except Exception as exc:
        return {"functional": "failed", "success": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


def deep_health(probe: bool = False, probe_hermes: bool = False, timeout: float = 2.0,
                in_process: bool = False) -> dict:
    from nexus.mission import agents as agent_runtime

    server = {"available": True, "in_process": True} if in_process else _server(timeout)
    port = True if in_process else _port(timeout)
    agents = agent_runtime.health()
    for name in list(agents):
        entry = agents.get(name) or {}
        entry["functional"] = "not_probed"
        if probe:
            if name == "Hermes" and not probe_hermes:
                entry["functional_note"] = "Hermes-Probe braucht echte CLI-Ausfuehrung; nur mit probe_hermes"
            else:
                entry.update(_probe_agent(name))
        agents[name] = entry

    features = {
        "workspace": _writable(WORKSPACE),
        "sprechzimmer": _writable(WORKSPACE / "sprechzimmer"),
        "missions": _writable(WORKSPACE / "missions"),
        "memory": _writable(WORKSPACE / "memory"),
        "imports": _imports(),
        "static_index": {"ok": (WORKSPACE.parent / "nexus" / "chat" / "static" / "index.html").is_file()
                         or (Path(__file__).resolve().parents[1] / "chat" / "static" / "index.html").is_file()},
    }

    reachable = [n for n, a in agents.items() if a.get("reachable")]
    functional = [n for n, a in agents.items() if a.get("functional") == "ok"]
    functional_failed = [n for n, a in agents.items() if a.get("functional") == "failed"]
    critical = (not server.get("available")) or (not features["workspace"].get("ok")) \
        or (not features["imports"].get("ok")) or (not reachable)

    all_reachable = len(reachable) == len(agents) and bool(agents)
    hermes_ok = (agents.get("Hermes") or {}).get("functional") == "ok"
    if critical:
        status = "critical"
    elif probe and not functional_failed and all_reachable and hermes_ok:
        status = "healthy"
    else:
        status = "degraded"

    notes = []
    if not probe:
        notes.append("Keine echte Funktionsprobe gelaufen: Erreichbarkeit ist nicht Funktionsfaehigkeit.")
    if probe and "Hermes" in functional_failed:
        notes.append("Hermes-Funktionsprobe fehlgeschlagen.")
    if probe and not probe_hermes:
        notes.append("Hermes wurde nicht funktional geprueft: ohne Hermes-Probe bleibt der Status degraded.")
    if not reachable:
        notes.append("Kein Kernagent erreichbar (Ollama/Modell pruefen, niemals automatisch pullen).")

    return {
        "success": True, "status": status, "functional_verified": bool(probe and not functional_failed),
        "checked": _now(), "server": server, "port_open": port, "agents": agents,
        "features": features, "reachable_agents": reachable, "functional_agents": functional,
        "notes": notes,
    }
