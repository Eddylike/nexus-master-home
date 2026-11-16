"""Autonomous Dashboard for NEXUS Master Home - Phase 8.

Zeigt Systemgesundheit, Agenten-Status, Aufgabenfortschritt,
Fehlerhistorie und Selbstbaustand in Echtzeit an.
Integriert in den bestehenden Live-Event-Bus und Health-Check.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from nexus.common.paths import ROOT, WORKSPACE
from nexus.common.live_events import emit, tail
from nexus.health.deep import deep_health
from nexus.autobuild.loop import load_backlog, CYCLES, PROGRESS
from nexus.integrations.gpt_local_agent import ask as gpt_ask
from nexus.integrations.hermes_agent import health as hermes_health
from nexus.integrations.aether_supervisor import run_supervisor, swarm_enabled
from nexus.common.ai_layer import status as ai_status


# ---------------------------------------------------------------------------
# Helper: sichere Pfade (kein C:, relative zu workspace)
# ---------------------------------------------------------------------------

_DASH_DIR = ROOT / "workspace" / "dashboard"
_DASH_DIR.mkdir(parents=True, exist_ok=True)
DASHBOARD_FILE = _DASH_DIR / "state.json"
ERRORS_LOG = _DASH_DIR / "errors.jsonl"
BACKUPS_LOG = _DASH_DIR / "backups.jsonl"
TESTS_LOG = _DASH_DIR / "tests.jsonl"


# ---------------------------------------------------------------------------
# Dashboard-Zustands-Erfassung
# ---------------------------------------------------------------------------

def _safe_eval(text: str, default: Any = None) -> Any:
    """Evaluere text sicher – nur für interne Metriken, keine Exec."""
    try:
        return json.loads(text)
    except Exception:
        return default


def get_system_health() -> dict:
    """Liefere aktuelle Systemgesundheit aus deep_health."""
    try:
        h = deep_health(probe=False)
        return {
            "status": h.get("status", "unknown"),
            "server": h.get("server", {}),
            "port_open": h.get("port_open", False),
            "agents": h.get("agents", {}),
            "functional_agents": h.get("functional_agents", []),
            "reachable_agents": h.get("reachable_agents", []),
            "notes": h.get("notes", []),
            "checked": h.get("checked"),
            "features": h.get("features", {}),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def get_agent_status() -> dict:
    """Liefere Status aller vier Agenten."""
    agents = {}

    # NEXUS (Server health)
    try:
        from nexus.chat.server import get_status
        agents["NEXUS"] = {"reachable": True, "functional": True, "detail": "Chat-Server laeuft"}
    except Exception as e:
        agents["NEXUS"] = {"reachable": False, "functional": False, "detail": f"{type(e).__name__}"}

    # Hermes
    try:
        h = hermes_health()
        agents["Hermes"] = {
            "reachable": h.get("reachable", False),
            "functional": h.get("functional") == "ok",
            "detail": h.get("error") or "OK",
        }
    except Exception as e:
        agents["Hermes"] = {"reachable": False, "functional": False, "detail": f"{type(e).__name__}"}

    # NEXUS GPT / Ollama
    try:
        a = ai_status()
        agents["NEXUS-GPT"] = {
            "reachable": a.get("reachable", False),
            "functional": a.get("chat_model_preferred") is not None,
            "detail": a.get("chat_model_preferred") or "Kein Modell",
        }
    except Exception as e:
        agents["NEXUS-GPT"] = {"reachable": False, "functional": False, "detail": f"{type(e).__name__}"}

    # Aether
    try:
        a = swarm_enabled()
        agents["AETHER"] = {
            "reachable": True,
            "functional": bool(a),
            "detail": "Supervisor laeuft" if a else "Deaktiviert",
        }
    except Exception as e:
        agents["AETHER"] = {"reachable": False, "functional": False, "detail": f"{type(e).__name__}"}

    return {"agents": agents, "checked": _now()}


def get_task_progress() -> dict:
    """Liefere Fortschritt der autonomen Aufgaben."""
    try:
        progress_text = PROGRESS.read_text(encoding="utf-8") if PROGRESS.is_file() else ""
        return {"progress": progress_text[:3000], "checked": _now()}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def get_recent_errors(limit: int = 50) -> list[dict]:
    """Liefere recente Fehler aus dem Error-Log."""
    try:
        lines = ERRORS_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        entries = []
        for line in lines[-limit:]:
            try:
                entry = json.loads(line)
                entries.append(entry)
            except Exception:
                pass
        return entries
    except Exception:
        return []


def get_backup_history(limit: int = 50) -> list[dict]:
    """Liefere Backup-Historie."""
    try:
        lines = BACKUPS_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        entries = []
        for line in lines[-limit:]:
            try:
                entry = json.loads(line)
                entries.append(entry)
            except Exception:
                pass
        return entries
    except Exception:
        return []


def get_test_results(limit: int = 50) -> list[dict]:
    """Liefere Testergebnisse."""
    try:
        lines = TESTS_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        entries = []
        for line in lines[-limit:]:
            try:
                entry = json.loads(line)
                entries.append(entry)
            except Exception:
                pass
        return entries
    except Exception:
        return []


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Dashboard-Datenerfassung speichern
# ---------------------------------------------------------------------------

def save_current_state() -> dict:
    """Speichere aktuellen Systemzustand als JSON und emitte Event."""
    system = get_system_health()
    agents = get_agent_status()
    tasks = get_task_progress()
    errors = get_recent_errors(limit=10)
    backups = get_backup_history(limit=10)
    tests = get_test_results(limit=10)

    state = {
        "ts": _now(),
        "system": system,
        "agents": agents,
        "tasks": tasks,
        "errors": errors[-20:],  # letze 20
        "backups": backups[-20:],
        "tests": tests[-20:],
    }

    # Als JSON-Datei speichern
    try:
        DASH_DIR_PARENT = DASH_DIR.parent
        DASH_DIR_PARENT.mkdir(parents=True, exist_ok=True)
        with open(DASHBOARD_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    # Event emittieren
    try:
        emit(
            "Dashboard",
            "status",
            f"Dashboard-Zustand aktualisiert: {state['system'].get('status', 'unknown')}",
            ok=state["system"].get("status") in ("healthy", "degraded"),
            data={"status": state["system"].get("status")},
        )
    except Exception:
        pass

    return state


# ---------------------------------------------------------------------------
# Fehler protokollieren
# ---------------------------------------------------------------------------

def log_error(source: str, error: str, excerpt: str | None = None) -> str:
    """Protokoliere einen Fehler in das Fehler-Log und emitte Event."""
    entry = {
        "ts": _now(),
        "source": str(source)[:80],
        "error": str(error)[:500],
        "excerpt": str(excerpt)[:500] if excerpt else "",
    }
    try:
        ERRORS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ERRORS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

    try:
        emit(
            "Dashboard",
            "error",
            f"{source}: {str(error)[:200]}",
            ok=False,
            data={"source": source, "error": str(error)[:200]},
        )
    except Exception:
        pass

    return entry["ts"]


def log_backup(operation: str, path: str, sha256: str | None = None) -> str:
    """Protokoliere ein Backup."""
    entry = {
        "ts": _now(),
        "operation": str(operation)[:80],
        "path": str(path)[:200],
        "sha256": sha256 or "",
    }
    try:
        BACKUPS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with BACKUPS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return entry["ts"]


def log_test_result(test_name: str, passed: bool, detail: str | None = None) -> str:
    """Protokoliere ein Testergebnis."""
    entry = {
        "ts": _now(),
        "test": str(test_name)[:80],
        "passed": bool(passed),
        "detail": str(detail)[:500] if detail else "",
    }
    try:
        TESTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with TESTS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return entry["ts"]


# ---------------------------------------------------------------------------
# Dashboard API-Endpoint (fuer /live)
# ---------------------------------------------------------------------------

def get_dashboard_api() -> dict:
    """Liefere kompletten Dashboard-Zustand als API-Response."""
    state = save_current_state()
    return {
        "status": state["system"].get("status", "unknown"),
        "agents": {k: {kk: vv for kk, vv in v.items() if kk != "detail"} for k, v in state["agents"].get("agents", {}).items()},
        "task_progress": state["tasks"].get("progress", "")[:500],
        "recent_errors": state["errors"][-10:],
        "recent_backups": state["backups"][-10:],
        "recent_tests": state["tests"][-10:],
        "timestamp": state["ts"],
    }


# ---------------------------------------------------------------------------
# Initialisierungs-Funktion: beim Server-Start registrieren
# ---------------------------------------------------------------------------

def register_dashboard_hooks() -> None:
    """Registriere Periodische Gesundheits-Checks alle 60 Sekunden."""
    _lock = threading.Lock()

    def _periodic_check():
        while True:
            try:
                time.sleep(60)
                with _lock:
                    save_current_state()
            except Exception:
                # Fehler leise protokollieren, aber nicht crashen
                pass

    # Daemon-Thread starten
    thread = threading.Thread(target=_periodic_check, name="dashboard-monitor", daemon=True)
    thread.start()


# Beim Import registrieren (nur, wenn nicht im Test)
import sys as _sys
if "pytest" not in _sys.modules:
    register_dashboard_hooks()