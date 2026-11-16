"""Echte Fähigkeitsproben für den Statusbericht.

Jede Probe führt die Fähigkeit wirklich aus (Bridge-Auftrag, Werkzeugaufruf,
Netzwerkabfrage) und liefert `reachable`/`tested` nur aus dem realen Ergebnis.
Ergebnisse werden kurz gecacht, damit eine Statusabfrage im Chat nicht bei jedem
Aufruf neue Bridge-Belege erzeugt.

Interaktive Fähigkeiten (Browser-Fenster öffnen, Mikrofon/STT mit echtem Audio,
Agenten-Lauf mit lokalem Modell) werden nicht bei jeder Statusabfrage ausgeführt;
dafür zählen persistierte Belege unter `workspace/hermes-core/evidence/` mit
Dateiname `capability-<key>-*.json`, Status `PASS` und Alter <= EVIDENCE_MAX_AGE_DAYS.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
CACHE_REL = Path("workspace/state/capability_probe.json")
EVIDENCE_REL = Path("workspace/hermes-core/evidence")
PROBE_DIR = "workspace/projects/hermes/code/capprobe"
CACHE_TTL_S = 600
EVIDENCE_MAX_AGE_DAYS = 30
PROBE_CONTENT = "NEXUS capability probe\nverified=true\n"
PROBE_PY = "def add(a, b):\n    return a + b\n"


def _now() -> float:
    return time.time()


def _probe(name: str, fn: Callable[[], dict]) -> dict:
    started = _now()
    try:
        out = fn()
    except Exception as exc:  # Probe darf den Bericht nie abschießen
        out = {"reachable": False, "tested": False, "error": type(exc).__name__ + ": " + str(exc)[:200]}
    out.setdefault("reachable", None)
    out.setdefault("tested", None)
    out["id"] = name
    out["duration_ms"] = int((_now() - started) * 1000)
    return out


# ---------------------------------------------------------------- Live-Proben

def _probe_files() -> dict:
    from nexus.integrations.hermes_client import invoke
    path = "workspace/projects/hermes/capprobe.txt"  # flacher Artefaktbereich (ARTIFACT_PATTERN)
    create = invoke({"operation": "submit", "task": "create_file", "params": {"path": path, "content": PROBE_CONTENT}})
    reachable = create.get("status") in ("succeeded", "failed", "blocked")
    ids = [create.get("task_id")]
    exists = create.get("status") == "failed" and (ROOT / path).is_file()
    if create.get("status") != "succeeded" and not exists:
        return {"reachable": reachable, "tested": False, "task_ids": [i for i in ids if i],
                "error": create.get("error_code") or create.get("error")}
    read = invoke({"operation": "submit", "task": "read_file", "params": {"path": path}})
    ids.append(read.get("task_id"))
    result: dict = read.get("result") if isinstance(read.get("result"), dict) else {}
    exact = read.get("status") == "succeeded" and result.get("content") == PROBE_CONTENT
    return {"reachable": True, "tested": bool(exact), "task_ids": [i for i in ids if i], "exact_match": bool(exact),
            "created": create.get("status") == "succeeded"}


def _probe_code() -> dict:
    from nexus.integrations.hermes_client import invoke
    path = f"{PROBE_DIR}/probe.py"
    create = invoke({"operation": "submit", "task": "create_file", "params": {"path": path, "content": PROBE_PY}})
    ids = [create.get("task_id")] if create.get("status") == "succeeded" else []
    check = invoke({"operation": "submit", "task": "hermes_check_code", "params": {"path": path}})
    ids.append(check.get("task_id"))
    result: dict = check.get("result") if isinstance(check.get("result"), dict) else {}
    ok = check.get("status") == "succeeded" and result.get("syntax") == "ok" and "add" in (result.get("defs") or [])
    return {"reachable": check.get("status") in ("succeeded", "failed"), "tested": bool(ok),
            "task_ids": [i for i in ids if i], "error": None if ok else check.get("error_code")}


def _probe_pc() -> dict:
    from nexus.computer import desktop
    results = {"pointer_position": desktop.pointer_position(),
               "screen_size": desktop.screen_size(),
               "clipboard_get": desktop.clipboard("get")}
    ok = {k: bool(v.get("success") and v.get("verified")) for k, v in results.items()}
    return {"reachable": any(r.get("error") != "input_unavailable" for r in results.values()),
            "tested": all(ok.values()), "actions": ok,
            "error": None if all(ok.values()) else {k: v.get("error") for k, v in results.items() if not ok[k]}}


def _probe_web() -> dict:
    from nexus.knowledge.research import Research
    out = Research().search("NEXUS AI assistant")
    hits = out.get("results") or []
    return {"reachable": bool(out.get("success")) or bool(hits), "tested": bool(out.get("success") and hits),
            "results": len(hits), "error": out.get("error")}


def _probe_improve() -> dict:
    from nexus.memory.self_improve import SelfImproveLoop
    out = SelfImproveLoop().run(trigger="capability_probe", apply_safe=False)
    props = out.get("proposals") or []
    return {"reachable": bool(out.get("success")), "tested": bool(out.get("success") and props),
            "proposals": len(props), "applied": 0}


def _probe_agents() -> dict:
    from nexus.crew.runner import CrewService
    out = CrewService().list_agents()
    agents = out.get("agents") or []
    return {"reachable": bool(out.get("success") and agents), "tested": None, "agents": len(agents)}


def _probe_voice() -> dict:
    from nexus.voice import stt
    status = stt.status() if hasattr(stt, "status") else {}
    available = bool(status.get("available"))
    return {"reachable": available, "tested": None, "mode": status.get("mode"), "model": status.get("model")}


def _probe_browser() -> dict:
    from nexus.computer.os_actions import OSActions
    return {"reachable": callable(getattr(OSActions, "open_url", None)), "tested": None}


LIVE_PROBES: dict[str, Callable[[], dict]] = {
    "files": _probe_files, "code": _probe_code, "pc": _probe_pc, "web": _probe_web,
    "improve": _probe_improve, "agents": _probe_agents, "voice": _probe_voice, "browser": _probe_browser,
}
# Fähigkeiten, deren `tested` nur aus persistierten Belegen kommt
EVIDENCE_KEYS = ("browser", "voice", "agents")


# --------------------------------------------------------- persistierte Belege

def persisted_evidence(key: str, root: Path = ROOT) -> dict | None:
    """Jüngster PASS-Beleg `capability-<key>-*.json`, sonst None."""
    folder = root / EVIDENCE_REL
    if not folder.is_dir():
        return None
    best = None
    for p in sorted(folder.glob(f"capability-{key}-*.json"), key=lambda q: q.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        age_days = (_now() - p.stat().st_mtime) / 86400
        if data.get("status") == "PASS" and age_days <= EVIDENCE_MAX_AGE_DAYS:
            best = {"file": p.name, "age_days": round(age_days, 1), "task_ids": data.get("task_ids") or [],
                    "summary": data.get("summary")}
            break
    return best


# ------------------------------------------------------------------- Einstieg

def run_probes(root: Path = ROOT, *, force: bool = False, keys: tuple[str, ...] | None = None) -> dict:
    cache = root / CACHE_REL
    if not force and cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if _now() - float(data.get("at", 0)) < CACHE_TTL_S and (keys is None or set(keys) <= set(data.get("probes", {}))):
                data["cached"] = True
                return data
        except Exception:
            pass
    selected = keys or tuple(LIVE_PROBES)
    probes = {k: _probe(k, LIVE_PROBES[k]) for k in selected if k in LIVE_PROBES}
    for key in EVIDENCE_KEYS:
        if key in probes:
            ev = persisted_evidence(key, root)
            probes[key]["tested"] = bool(ev) if ev or probes[key].get("tested") is None else probes[key]["tested"]
            probes[key]["evidence"] = ev
    data = {"at": _now(), "at_iso": time.strftime("%Y-%m-%dT%H:%M:%S"), "probes": probes, "cached": False}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    return data


def write_evidence(key: str, *, status: str, summary: str, task_ids: list | None = None,
                   details: dict | None = None, root: Path = ROOT) -> Path:
    """Persistiert einen E2E-Beleg, den `persisted_evidence` später findet."""
    folder = root / EVIDENCE_REL
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = folder / f"capability-{key}-{stamp}.json"
    target.write_text(json.dumps({"capability": key, "status": status, "summary": summary,
                                  "task_ids": task_ids or [], "details": details or {},
                                  "at": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    return target
