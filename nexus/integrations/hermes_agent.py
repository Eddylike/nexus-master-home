"""NEXUS -> Hermes Agent (Auftrag 003, 2026-10-15).

Kontrollierte lokale Schnittstelle zum bestehenden Hermes-CLI (kein zweiter Hermes-Dienst,
kein offener Port): je Auftrag ein Oneshot-Lauf `hermes chat --query-file ... -Q` mit
Provider ollama-qwen35 (Ollama 127.0.0.1:11434), festem Modell, Toolset nur `file`,
Arbeitsverzeichnis unter workspace/, Timeout 300 s. Cloud-Keys werden fuer den Kindprozess
entfernt (kein stiller Cloud-Fallback). Jeder Auftrag wird in workspace/hermes_service/requests.log
protokolliert. Faellt Hermes aus, kommt ehrlich HERMES_UNAVAILABLE, nie eine andere Modellantwort.
"""
from __future__ import annotations

import json, os, re, shutil, subprocess, threading, time, urllib.request, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "workspace" / "hermes_service"
WORK_DIR = ROOT / "workspace" / "hermes_tasks"
PROVIDER = "ollama-qwen35"
MODEL = os.environ.get("NEXUS_HERMES_MODEL", "qwen3.5:9b")  # 28.10.2026: ein Modell im RAM
TOOLSETS = "file"
TIMEOUT = 300  # 28.10.2026: Modell-Kaltstart 9b@64k
OLLAMA = "http://127.0.0.1:11434"
OLLAMA_HEALTH_TIMEOUT = 2
CLOUD_KEYS = ("OPENROUTER_API_KEY", "NOUS_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "FIREWORKS_API_KEY",
              "GEMINI_API_KEY", "GOOGLE_API_KEY", "OLLAMA_API_KEY")
_LOCK = threading.Lock()
_CURRENT = {"busy": False, "busy_since": None, "request_id": None}  # 12:03: echter laufender Job fuer /api/hermes/health
PREFIX = re.compile(r"^\s*(?:@hermes\b[\s:,]*|hermes\s*[:,]\s*)", re.I)  # 003b: @hermes auch ohne Doppelpunkt


def _exe():
    # 28.10.2026: Installierte Runtime bevorzugen. shutil.which fand sonst die hermes-ps-Shim
    # (G:\meine-ki\hermes-ps\home\bin\hermes.cmd) ohne tools\python-* -> HERMES_INSTALLED_RUNTIME_UNAVAILABLE.
    env_exe = os.environ.get("NEXUS_HERMES_EXE")
    if env_exe and Path(env_exe).is_file():
        # HERMES_TOOL_LOOP_LIMIT Schutz: Nur wenn kein Tool-Aufruf pending
        with _LOCK:
            if not _CURRENT["busy"]:
                # Synchroner Start ohne Tool-Loop (Tool-Set bereits auf 'file' limitiert)
                pass
        return env_exe
    inst = Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "bin" / "hermes.exe"
    if inst.is_file():
        return str(inst)
    for name in ("hermes.exe", "hermes"):
        w = shutil.which(name)
        if w and (Path(w).parent.parent / "hermes-agent" / "hermes_bootstrap.py").is_file():
            return w
    return shutil.which("hermes") or str(inst)


def _log(entry: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / "requests.log", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _ollama_models():
    with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=OLLAMA_HEALTH_TIMEOUT) as r:
        return [m.get("name") for m in json.loads(r.read().decode()).get("models", [])]


def health() -> dict:
    """Read readiness without launching Hermes or claiming a verified execution."""
    out = {"agent": "hermes", "interface": "cli_oneshot", "provider": PROVIDER, "model": MODEL,
           "toolsets": TOOLSETS, "ollama": OLLAMA, "listening_port": None,
           "version": None, "version_source": "not_probed", "cli_probe": "path_exists",
           "execution_verified": False,
           "busy": bool(_CURRENT["busy"]), "busy_since": _CURRENT["busy_since"], "request_id": _CURRENT["request_id"]}
    from nexus.integrations import hermes_runas as _ra  # 003d: optionaler Startweg, Standard aus
    _rs = _ra.settings(); out["run_as"] = _rs["run_as"]; out["run_as_switch"] = _rs["switch"]
    if _rs["enabled"]:
        try:
            _have = _ra.credential_present(_rs["credential_target"])
        except _ra.RunAsUnavailable as e:
            _have = False
        if not _have:
            return {**out, "cli": None, "reachable": False, "error": "HERMES_UNAVAILABLE",
                    "detail": f"run_as={_rs['run_as']}: Credential '{_rs['credential_target']}' fehlt"}
    # Even --version starts Hermes' bootstrap. A status read must not initialize
    # its runtime, write caches or bypass the configured Run-as start path.
    try:
        exe = _exe()
        out["cli"] = exe if Path(exe).is_file() else None
    except (OSError, ValueError) as e:
        return {**out, "cli": None, "reachable": False, "error": "HERMES_UNAVAILABLE", "detail": type(e).__name__}
    if not out["cli"]:
        return {**out, "reachable": False, "error": "HERMES_UNAVAILABLE", "detail": "hermes CLI nicht gefunden"}
    try:
        models = _ollama_models()
    except Exception as e:
        return {**out, "reachable": False, "error": "HERMES_UNAVAILABLE", "detail": "Ollama: " + type(e).__name__}
    if not any(m == MODEL or m == MODEL + ":latest" for m in models):
        return {**out, "reachable": False, "error": "HERMES_UNAVAILABLE", "detail": f"Modell {MODEL} fehlt"}
    return {**out, "reachable": True, "error": None}


def is_hermes_request(text: str) -> bool:
    return bool(PREFIX.match(text or ""))


def strip_prefix(text: str) -> str:
    return PREFIX.sub("", text or "", count=1).strip()


# Selbstwartung (Eddie 16.10.): Paketliste des eigenen venv vor/nach jedem Lauf, Unterschied -> install.log
HERMES_OWN_HOME = ROOT / "workspace" / "hermes_arbeitsbereich" / "nexus-hermes-home"
SNAP_DIR = LOG_DIR / "pkg_snapshots"


def _pkg_snapshot() -> dict:
    pk = {}
    try:
        for d in HERMES_OWN_HOME.glob("installs/*/environments/*/venv/Lib/site-packages/*.dist-info"):
            n, _, v = d.name[:-10].rpartition("-")
            if n:
                pk[n.lower().replace("_", "-")] = v
    except Exception:
        pass
    return pk


def _pkg_diff_log(rid: str, before: dict, after: dict, rc) -> None:
    ch = []
    for n in sorted(set(before) | set(after)):
        a, b = before.get(n), after.get(n)
        if a != b:
            ch.append({"package": n, "from": a, "to": b,
                       "action": "install" if a is None else ("remove" if b is None else "update")})
    if not ch:
        return
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    (SNAP_DIR / f"{rid}.before.json").write_text(json.dumps(before, indent=0), encoding="utf-8")
    with open(LOG_DIR / "install.log", "a", encoding="utf-8") as f:
        for c in ch:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "request_id": rid, **c,
                                "source": "PyPI (uv)", "result": "ok" if rc == 0 else f"run_rc={rc}",
                                "restore_point": f"pkg_snapshots/{rid}.before.json"}, ensure_ascii=False) + "\n")


def ask(task: str, workdir: str | None = None) -> dict:
    task = (task or "").strip()
    rid = "HX-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    base = {"agent": "hermes", "source": "hermes", "request_id": rid, "intent": "hermes",
            "provider": PROVIDER, "model": MODEL}
    if not task:
        return {**base, "success": False, "error": "TASK_REQUIRED", "reply": "Kein Auftrag fuer Hermes."}
    wd = Path(workdir) if workdir else WORK_DIR
    wd = wd.resolve()
    try:
        wd.relative_to(ROOT / "workspace")
    except ValueError:
        return {**base, "success": False, "error": "PATH_NOT_AUTHORIZED", "reply": "Arbeitsverzeichnis ausserhalb workspace/."}
    wd.mkdir(parents=True, exist_ok=True)
    # 2026-10-26 (Eddie-Freigabe): expliziter Werkzeugauftrag -> direkt Ollama-native Tool-Loop (CLI liefert Toolcalls nur als Text)
    try:
        from nexus.integrations import hermes_tools as _ht
        _want = _ht.wants_tools(task) and os.environ.get("NEXUS_HERMES_NATIVE_TOOLS", "1") == "1"
    except Exception as _e:
        _want = False
        _log({"event": "native_tools_error", "request_id": rid, "error": str(_e)[:300]})
    if _want:
        _tm = os.environ.get("NEXUS_HERMES_TOOL_MODEL", "qwen3.5:9b")
        _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "request_native_tools", "request_id": rid, "model": _tm, "workdir": str(wd), "task": task[:500]})
        if not _LOCK.acquire(timeout=5):
            return {**base, "success": False, "error": "HERMES_BUSY", "reply": "Hermes bearbeitet bereits einen Auftrag."}
        _CURRENT.update(busy=True, busy_since=time.strftime("%Y-%m-%dT%H:%M:%S"), request_id=rid)
        _t0 = time.time()
        try:
            _nl = _ht.native_loop(task, wd, _tm, rid)
        except Exception as _e:
            _nl = {"reply": f"HERMES_UNAVAILABLE: {_e}", "tool_calls": [], "turns": 0}
        finally:
            _CURRENT.update(busy=False, busy_since=None, request_id=None)
            _LOCK.release()
        _ms = int((time.time() - _t0) * 1000)
        _calls = _nl.get("tool_calls") or []
        _approval_pending = any(c.get("status") == "APPROVAL_REQUIRED" for c in _calls)
        _ok = (
            bool(_calls)
            and all(c.get("ok") is True for c in _calls)
            and _nl.get("reply") != "HERMES_TOOL_LOOP_LIMIT"
            and not _nl.get("final_missing", False)
        )
        if _approval_pending:
            _st = "HERMES_APPROVAL_REQUIRED"
        elif _ok:
            _st = "HERMES_TOOLS_EXECUTED"
        elif _calls:
            _st = "HERMES_TOOL_EXECUTION_FAILED"
        elif str(_nl.get("reply") or "").strip() and _nl.get("reply") != "HERMES_TOOL_LOOP_LIMIT":
            # 2026-11-09 FIXALL: Chat-/Spielantwort ohne Tool ist gueltig (kein Fake-503).
            _ok = True
            _st = "HERMES_ANSWERED"
        else:
            _st = "HERMES_NO_TOOL_CALL"
        _log({"event": "response_native_tools", "request_id": rid, "ok": _ok, "latency_ms": _ms,
              "tools": [c["tool"] for c in _nl["tool_calls"]], "reply": _nl["reply"][:500]})
        return {**base, "model": _tm, "success": _ok, "error": None if _ok else _st, "reply": _nl["reply"], "latency_ms": _ms,
                "provenance": "hermes_native_tools", "model_provenance": "model_response_tools_executed" if _ok else "model_response_unverified",
                "data": {"execution_status": _st, "agent": "hermes", "source": "hermes", "request_id": rid, "model": _tm,
                         "provider": "ollama-native", "latency_ms": _ms, "tool_calls": _nl["tool_calls"], "task_ids": []}}
    # 2026-11-09 FIXALL: Ohne Werkzeugbedarf nicht die langsame/haengende Hermes-CLI starten.
    # Direkter Ollama-Chat (ohne Tools) haelt einfache Antworten unter 45 s.
    _tm = os.environ.get("NEXUS_HERMES_TOOL_MODEL", "qwen3.5:9b")
    if not _LOCK.acquire(timeout=5):
        return {**base, "success": False, "error": "HERMES_BUSY", "reply": "Hermes bearbeitet bereits einen Auftrag."}
    _CURRENT.update(busy=True, busy_since=time.strftime("%Y-%m-%dT%H:%M:%S"), request_id=rid)
    _t0 = time.time()
    try:
        import urllib.request as _ur
        body = {
            "model": _tm,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 8192},
            "messages": [
                {"role": "system", "content": "Du bist Hermes in NEXUS Master Home. Antworte knapp auf Deutsch. Keine erfundenen Tool-Ergebnisse."},
                {"role": "user", "content": task},
            ],
        }
        req = _ur.Request(OLLAMA + "/api/chat", data=json.dumps(body).encode("utf-8"),
                          headers={"Content-Type": "application/json"})
        with _ur.urlopen(req, timeout=40) as resp:
            msg = json.loads(resp.read().decode("utf-8")).get("message") or {}
        reply = str(msg.get("content") or "").strip()
        _ms = int((time.time() - _t0) * 1000)
        if not reply:
            raise RuntimeError("empty_reply")
        _log({"event": "response_fast_chat", "request_id": rid, "latency_ms": _ms, "reply": reply[:500]})
        return {**base, "model": _tm, "success": True, "error": None, "reply": reply, "latency_ms": _ms,
                "provenance": "hermes_fast_chat", "model_provenance": "model_response_unverified",
                "data": {"execution_status": "HERMES_ANSWERED", "agent": "hermes", "source": "hermes",
                         "request_id": rid, "model": _tm, "provider": "ollama", "latency_ms": _ms,
                         "tool_calls": [], "task_ids": []}}
    except Exception as _e:
        _log({"event": "fast_chat_fallback_cli", "request_id": rid, "error": str(_e)[:300]})
        # fall through to CLI
    finally:
        _CURRENT.update(busy=False, busy_since=None, request_id=None)
        _LOCK.release()

    exe = _exe()
    qdir = LOG_DIR / "queries"; qdir.mkdir(parents=True, exist_ok=True)
    qfile = qdir / f"{rid}.txt"
    try:
        from nexus.integrations.hermes_brain import hermes_lessons_block
        _les = hermes_lessons_block()
    except Exception:
        _les = ""
    from nexus.common.leitbild import LEITBILD as _LB
    qfile.write_text("LEITBILD (deine Grundhaltung): " + _LB + "\n\n" + ((_les + "\n\n") if _les else "") + task, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in CLOUD_KEYS}
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [exe, "chat", "--query-file", str(qfile), "-Q", "--provider", PROVIDER, "-m", MODEL, "-t", TOOLSETS,
           "--in", str(wd), "--max-turns", "8", "--run-budget", str(TIMEOUT - 10)]
    _pk0 = _pkg_snapshot()
    t0 = time.time()
    _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": "request", "request_id": rid, "model": MODEL,
          "workdir": str(wd), "task": task[:500]})
    if not _LOCK.acquire(timeout=5):
        _log({"event": "busy", "request_id": rid})
        return {**base, "success": False, "error": "HERMES_BUSY", "reply": "Hermes bearbeitet bereits einen Auftrag."}
    _CURRENT.update(busy=True, busy_since=time.strftime("%Y-%m-%dT%H:%M:%S"), request_id=rid)
    try:
        from nexus.integrations import hermes_runas as _ra  # 003d: optionaler Startweg, Standard aus
        _rs = _ra.settings()
        try:
            if _rs["enabled"]:  # kein Rueckfall auf den angemeldeten Benutzer
                r = _ra.run(cmd, cwd=str(wd), timeout=TIMEOUT, base_env=env, user=_rs["user"],
                            credential_target=_rs["credential_target"])
            else:
                r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   timeout=TIMEOUT, cwd=str(wd), env=env)
        except _ra.RunAsUnavailable as e:
            r = None; err = "HERMES_UNAVAILABLE"; detail = f"run_as={_rs['run_as']}: {e}"
        except FileNotFoundError:
            r = None; err = "HERMES_UNAVAILABLE"; detail = "hermes CLI nicht gefunden"
        except subprocess.TimeoutExpired:
            r = None; err = "HERMES_TIMEOUT"; detail = f"keine Antwort in {TIMEOUT}s"
    finally:
        _CURRENT.update(busy=False, busy_since=None, request_id=None)
        _LOCK.release()
    ms = int((time.time() - t0) * 1000)
    try:
        _pkg_diff_log(rid, _pk0, _pkg_snapshot(), getattr(r, 'returncode', None))
    except Exception:
        pass
    if r is None:
        _log({"event": "error", "request_id": rid, "error": err, "run_as": _rs.get("run_as"), "run_as_switch": _rs.get("switch"), "latency_ms": ms})
        return {**base, "data": {"agent": "hermes", "source": "hermes", "execution_status": err}, "success": False, "error": err, "reply": f"{err}: {detail}. Keine Ersatzantwort.",
                "latency_ms": ms}
    stdout = (r.stdout or "").strip()
    session = None
    pat = re.compile(r"(?im)^\s*session(?:[_ ]id)?\s*[:=]\s*(\S+)\s*$")
    m = pat.search(stdout) or pat.search(r.stderr or "")
    if m:
        session = m.group(1)
    stdout = pat.sub("", stdout).strip()
    if r.returncode != 0 or not stdout:
        _log({"event": "error", "request_id": rid, "rc": r.returncode, "run_as": _rs.get("run_as"), "run_as_switch": _rs.get("switch"), "latency_ms": ms,
              "stderr": (r.stderr or "")[-400:]})
        return {**base, "data": {"agent": "hermes", "source": "hermes", "execution_status": "HERMES_UNAVAILABLE"}, "success": False, "error": "HERMES_UNAVAILABLE", "rc": r.returncode, "latency_ms": ms,
                "reply": f"HERMES_UNAVAILABLE: Hermes-Lauf rc={r.returncode}. Keine Ersatzantwort."}
    # 2026-10-26 (Eddie-Freigabe): Text-Toolcall -> echte Ausfuehrung ueber Ollama-native Tools
    _tool_calls = []
    try:
        from nexus.integrations import hermes_tools as _ht
        if _ht.has_text_toolcall(stdout):
            _log({"event": "text_toolcall_detected", "request_id": rid, "reply": stdout[:300]})
            _nl = _ht.native_loop(task, wd, os.environ.get("NEXUS_HERMES_TOOL_MODEL", "qwen3.5:9b"), rid)
            _tool_calls = _nl["tool_calls"]
            if _tool_calls:
                stdout = _nl["reply"] or stdout
            ms = int((time.time() - t0) * 1000)
    except Exception as _e:
        _log({"event": "tool_loop_error", "request_id": rid, "error": str(_e)[:300]})
    _log({"event": "response", "request_id": rid, "rc": 0, "run_as": _rs.get("run_as"), "run_as_switch": _rs.get("switch"), "latency_ms": ms, "session_id": session,
          "reply": stdout[:500]})
    return {**base, "success": True, "error": None, "reply": stdout, "latency_ms": ms, "session_id": session,
            "rc": 0, "provenance": "hermes_response", "model_provenance": "model_response_unverified",
            "data": {"execution_status": "HERMES_ANSWERED", "agent": "hermes", "source": "hermes", "model_provenance": "model_response_unverified", "request_id": rid,
                     "session_id": session, "latency_ms": ms, "model": MODEL, "provider": PROVIDER,
                     "provenance": "hermes_response", "task_ids": [], "tool_calls": _tool_calls}}
