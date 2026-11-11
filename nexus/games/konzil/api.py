"""KONZIL API + Watchdog (2026-11-04). Routen: GET /api/konzil/state, POST /api/konzil/pause|resume|intervene.
Watchdog: setzt ein laufendes Konzil nach Core-Neustart/Absturz automatisch fort (ohne Admin, nur G:)."""
from __future__ import annotations
import ctypes, json, os, subprocess, sys, threading, time
from pathlib import Path
from nexus.common.paths import ROOT
from . import engine as E

GAMES = Path(ROOT) / "workspace" / "games"
STATE = GAMES / "konzil_state.json"
PAUSE = GAMES / "konzil.pause"
STOP = GAMES / "konzil.stop"
INBOX = GAMES / "konzil_inbox.jsonl"
PIDF = GAMES / "konzil.pid"
RUNNER = GAMES / "konzil_run.py"
LOGDIR = Path(ROOT) / "workspace" / "logs" / "games"


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def runner_pid() -> int:
    try:
        return int(PIDF.read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def public_state() -> dict:
    st = E.load(STATE)
    if not st:
        return {"success": True, "exists": False}
    pid = runner_pid()
    alive = pid_alive(pid)
    status = st.get("status")
    # 2026-11-09 FIXALL: running ohne Runner / mit Stop-Datei -> stopped melden und speichern.
    # konzil.stop wird NICHT entfernt; Spiel wird nicht gestartet.
    if status == "running" and (STOP.exists() or not alive):
        status = "stopped"
        try:
            st["status"] = "stopped"
            st["updated"] = E.now()
            E.save(st, STATE)
        except Exception:
            pass
    case = st.get("case") or 0
    return {"success": True, "exists": True, "status": status, "paused": PAUSE.exists(), "case": case,
            "case_title": E.CASES[case - 1][0] if 1 <= case <= 6 else ("Start" if case == 0 else "Finale"),
            "phase": st.get("phase"), "session_id": st.get("session_id"), "runner_pid": pid, "runner_alive": alive,
            "values": st.get("values"), "stats": E.STATS, "agents": E.AGENTS, "violations": st.get("violations"),
            "sanctions": st.get("sanctions"), "no_vote": st.get("no_vote"), "excluded": st.get("excluded"),
            "stability": st.get("stability"), "average": E.average(st), "profile": E.profile(E.average(st)),
            "results": st.get("results"), "events": st.get("events"), "branches": st.get("branches"), "flags": st.get("flags"),
            "tech_failures": st.get("tech_failures"), "repairs": st.get("repairs"), "objections": st.get("objections"),
            "changes": (st.get("changes") or [])[-60:], "protocol": (st.get("protocol") or [])[-80:],
            "contributions": (st.get("contributions") or [])[-30:], "updated": st.get("updated")}


def handle(h, method: str, path: str, body: dict | None) -> bool:
    if not path.startswith("/api/konzil/"):
        return False
    route = path[len("/api/konzil/"):]
    if method == "GET" and route == "state":
        h._json(200, public_state()); return True
    if method != "POST":
        h._json(405, {"success": False, "error": "method"}); return True
    if h.client_address[0] not in ("127.0.0.1", "::1"):
        ok, why = h._agent_guard()
        if not ok:
            h._json(403, {"success": False, "error": why}); return True
    b = body if isinstance(body, dict) else {}
    if route == "pause":
        PAUSE.write_text("pause", encoding="utf-8"); h._json(200, {"success": True, "paused": True}); return True
    if route == "resume":
        if PAUSE.exists():
            PAUSE.unlink()
        started = ensure_running()
        h._json(200, {"success": True, "paused": False, "runner_started": started}); return True
    if route == "intervene":
        text = str(b.get("text") or "").strip()[:1500]
        if not text:
            h._json(400, {"success": False, "error": "text fehlt"}); return True
        kind = "einwand" if b.get("kind") == "einwand" else "eingriff"
        rec = {"ts": E.now(), "kind": kind, "text": text, "target": str(b.get("target") or "")[:80]}
        with INBOX.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        h._json(200, {"success": True, "queued": rec}); return True
    h._json(404, {"success": False, "error": "unbekannte Konzil-Route"}); return True


def ensure_running() -> bool:
    """Startet den Runner mit --resume, wenn ein Spiel laeuft/pausiert ist und kein Runner lebt."""
    st = E.load(STATE)
    if not st or st.get("status") in ("finished", "aborted") or STOP.exists() or not RUNNER.exists():
        return False
    if pid_alive(runner_pid()):
        return False
    LOGDIR.mkdir(parents=True, exist_ok=True)
    out = (LOGDIR / "konzil.log").open("a", encoding="utf-8")
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    py = sys.executable.replace("pythonw.exe", "python.exe")
    p = subprocess.Popen([py, "-B", "-u", str(RUNNER), "--resume"], cwd=str(ROOT), stdout=out, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    PIDF.write_text(str(p.pid), encoding="utf-8")
    return True


_started = False


def start_watchdog(interval: float = 60.0) -> bool:
    global _started
    if _started:
        return False
    _started = True

    def loop():
        time.sleep(20)
        while True:
            try:
                if ensure_running():
                    print("Konzil-Watchdog: Spiel automatisch fortgesetzt", flush=True)
            except Exception as e:  # nie den Core stoeren
                print(f"Konzil-Watchdog Fehler: {type(e).__name__}", flush=True)
            time.sleep(interval)
    threading.Thread(target=loop, name="konzil-watchdog", daemon=True).start()
    return True
