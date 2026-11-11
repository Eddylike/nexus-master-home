"""MH01 Health-Watchdog (2026-10-28): alle 10 Min Health/Kacheln/Chat-Probe -> workspace/audit/health_watch.jsonl.
Bei Ausfall von Core, Hermes, Downloader oder Ollama: SB-AUTO-Item + Neustart via restart_nexus_all.ps1
(Cooldown 30 Min, max. 6 Neustarts/24h). Nur G:, BelowNormal, Einzelinstanz per Lockdatei."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "workspace" / "audit"
LOG = AUDIT / "health_watch.jsonl"
STATE = AUDIT / "health_watch_state.json"
LOCK = AUDIT / "health_watch.lock"
BASE = "http://127.0.0.1:8299"
OLLAMA = "http://127.0.0.1:11434/api/tags"
INTERVAL = 600
COOLDOWN = 1800
MAX_RESTARTS_24H = 6
RESTART_PS1 = ROOT / "restart_nexus_all.ps1"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _get(url: str, timeout: float = 20.0):
    t0 = time.time()
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return data, int((time.time() - t0) * 1000)


def probe(get=_get) -> dict:
    rec: dict = {"ts": _now(), "checks": {}, "tiles": {}, "failed": []}
    c = rec["checks"]
    try:
        h, ms = get(BASE + "/api/health")
        c["core"] = {"ok": bool(h.get("ok")), "score": h.get("score"), "ms": ms, "python": (h.get("python") or {}).get("executable"), "python_version": (h.get("python") or {}).get("version")}
    except Exception as e:
        c["core"] = {"ok": False, "error": type(e).__name__}
    try:
        o, ms = get(OLLAMA, 5)
        c["ollama"] = {"ok": "models" in o, "ms": ms}
    except Exception as e:
        c["ollama"] = {"ok": False, "error": type(e).__name__}
    if c["core"]["ok"]:
        try:  # Deep Health: Erreichbarkeit != Funktionsfaehigkeit; degraded loest keinen Neustart aus
            dh, _ = get(BASE + "/api/health/deep", 5)
            c["deep"] = {"ok": dh.get("status") in ("healthy", "degraded"),
                         "status": dh.get("status"), "functional_verified": bool(dh.get("functional_verified")),
                         "reachable_agents": dh.get("reachable_agents") or []}
        except Exception as e:
            c["deep"] = {"ok": False, "error": type(e).__name__}
        try:
            hh, _ = get(BASE + "/api/hermes/health")
            c["hermes"] = {"ok": bool(hh.get("cli_probe") == "path_exists" or hh.get("execution_verified")), "busy": hh.get("busy")}
        except Exception as e:
            c["hermes"] = {"ok": False, "error": type(e).__name__}
        try:
            d, _ = get(BASE + "/api/downloads")
            c["downloader"] = {"ok": bool(d.get("success")), "jobs": len(d.get("jobs") or [])}
        except Exception as e:
            c["downloader"] = {"ok": False, "error": type(e).__name__}
        try:
            s, ms = get(BASE + "/api/nexus-os/status?e2e=chat", 120)
            rec["tiles"] = {k: v.get("status") for k, v in (s.get("components") or {}).items() if isinstance(v, dict)}
            rec["tiles"].update({"orbital." + k: v.get("status") for k, v in (s.get("orbital") or {}).items() if isinstance(v, dict)})
            ch = (s.get("orbital") or {}).get("CHAT") or {}
            c["chat_probe"] = {"ok": ch.get("status") == "ONLINE", "status": ch.get("status"), "reason": ch.get("reason"), "ms": ms}
        except Exception as e:
            c["chat_probe"] = {"ok": False, "error": type(e).__name__}
    else:
        for k in ("hermes", "downloader", "chat_probe"):
            c[k] = {"ok": False, "error": "CORE_DOWN"}
    rec["failed"] = [k for k in ("core", "hermes", "downloader", "ollama") if not c.get(k, {}).get("ok")]
    rec["ok"] = not rec["failed"]
    return rec


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"restarts": [], "started": _now()}


def should_restart(state: dict, now: float) -> bool:
    rs = [t for t in state.get("restarts", []) if now - t < 86400]
    state["restarts"] = rs
    return (not rs or now - rs[-1] >= COOLDOWN) and len(rs) < MAX_RESTARTS_24H


def record_sb_auto(failed: list[str], rec: dict) -> list[str]:
    from nexus.autobuild import loop
    items = loop.load_backlog()
    if not items:  # niemals leeres/kaputtes Backlog ueberschreiben
        return []
    ids = []
    for f in failed:
        it = loop.record_issue(items, "health_watch", f"{f.upper()}_DOWN", json.dumps(rec["checks"].get(f), ensure_ascii=False))
        if it:
            ids.append(it["id"])
    loop.save_backlog(items)
    return ids


def do_restart() -> int:
    flags = 0x00000008 | 0x00000200 | 0x00004000  # DETACHED | NEW_GROUP | BELOW_NORMAL
    p = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(RESTART_PS1)],
                         creationflags=flags if os.name == "nt" else 0, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    return p.pid


def cycle(get=_get, restart=do_restart, sb=record_sb_auto, now=None) -> dict:
    rec = probe(get)
    if rec["failed"]:
        rec["sb_auto"] = sb(rec["failed"], rec)
        st = _load_state()
        t = now if now is not None else time.time()
        if should_restart(st, t):
            rec["action"] = {"restart_pid": restart()}
            st["restarts"].append(t)
        else:
            rec["action"] = {"restart": "SKIPPED_COOLDOWN_OR_LIMIT"}
        STATE.write_text(json.dumps(st), encoding="utf-8")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def _single_instance():
    import msvcrt
    fh = open(LOCK, "a+")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        sys.exit(0)
    return fh


def main() -> None:
    AUDIT.mkdir(parents=True, exist_ok=True)
    _lk = _single_instance()
    st = _load_state(); st.setdefault("started", _now()); st["watch_python"] = sys.executable; st["watch_python_version"] = sys.version.split()[0]; STATE.write_text(json.dumps(st), encoding="utf-8")
    time.sleep(90)  # Core-Start abwarten
    while True:
        try:
            r = cycle()
            if r.get("action", {}).get("restart_pid"):
                time.sleep(180)
        except Exception as e:
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": _now(), "watchdog_error": type(e).__name__, "msg": str(e)[:200]}) + "\n")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
