"""AETHER — eigenstaendiger lokaler KI-Partner fuer NEXUS Master Home.

Ausschliesslich lokales Ollama (qwen3.5:9b, Fallback qwen3.5:4b), kein Pull, keine Cloud. Fehler werden offen zurueckgegeben.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from nexus.common.ai_layer import keep_alive_value, load_config, ollama_base_url

PARTNER = "gpt"
PROVIDER = "ollama"
# 2026-11-09 FIXALL: Primaermodell = ai_models.json strong/chat (9b). 4b nur Fallback,
# weil 9b+4b nicht gleichzeitig in 8GB VRAM passen (Eddie 2026-11-02).
MODEL = "qwen3.5:9b"
FALLBACK_MODEL = "qwen3.5:4b"
TIMEOUT = 120

def preferred_model() -> str:
    """Zentrale Vorgabe aus workspace/apex/ai_models.json (roles.strong / chat_model)."""
    try:
        cfg = load_config()
        roles = cfg.get("roles") if isinstance(cfg.get("roles"), dict) else {}
        return str(roles.get("strong") or cfg.get("chat_model") or MODEL)
    except Exception:
        return MODEL

SYSTEM_PROMPT = (
    "Du bist NEXUS GPT in NEXUS Master Home und laeufst lokal ueber Ollama (kein OpenAI). "
    "Schwerpunkte: Aufgabenplanung, Analyse, Wissen, Beratung. Plaene als nummerierte, pruefbare Schritte. "
    "Behaupte niemals, Werkzeuge, Dateien oder Systeme benutzt zu haben. Erfinde keine Ergebnisse. Antworte kurz auf Deutsch. "
    "LEITBILD: NEXUS Master Home soll zeigen, dass Mensch und Maschine zusammen stark sind, und die Welt fuer jeden besser machen. Es verbindet Mensch und Maschine und auch Menschen untereinander. Es vermittelt die richtigen Werte: Ehrlichkeit, Schutz, Hilfsbereitschaft, Fuersorge, gute Zusammenarbeit und Freundschaft. Es bleibt kostenlos und lokal zuerst."
)


def _base() -> dict[str, Any]:
    return {"partner": PARTNER, "provider": PROVIDER, "model": MODEL}


def _models() -> list[str]:
    url = ollama_base_url(load_config()) + "/api/tags"
    with urllib.request.urlopen(url, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [str(m.get("name") or "") for m in payload.get("models", []) if isinstance(m, dict)]


def health() -> dict[str, Any]:
    try:
        models = _models()
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {**_base(), "reachable": False, "success": False, "error": "GPT_LOCAL_UNAVAILABLE", "error_type": type(exc).__name__}
    present = MODEL in models
    return {**_base(), "reachable": present, "success": present,
            "error": None if present else "GPT_LOCAL_MODEL_MISSING"}


def ask(message: str, _model: str | None = None, fmt: str | None = None, num_ctx: int | None = None,
        system: str | None = None) -> dict[str, Any]:
    text = (message or "").strip()
    if not text:
        return {**_base(), "success": False, "reply": "", "error": "MESSAGE_REQUIRED"}
    cfg = load_config()
    payload = {
        "model": _model or preferred_model(),
        "think": False,
        "stream": False,
        "keep_alive": keep_alive_value(cfg),
        "messages": [
            {"role": "system", "content": system or SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
    if fmt:  # z.B. "json": Ollama erzwingt gueltiges JSON (Autobuild 20261028)
        payload["format"] = fmt
    if not num_ctx:
        try:
            from nexus.common import ai_layer
            num_ctx = ai_layer.unified_num_ctx()  # 2026-10-28: einheitlich, sonst Reload
        except Exception:
            num_ctx = None
    if num_ctx:
        payload["options"] = {"num_ctx": int(num_ctx)}
    req = urllib.request.Request(
        ollama_base_url(cfg) + "/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
        reply = str(((data.get("message") or {}).get("content") or "")).strip()
        if not reply:
            return {**_base(), "success": False, "reply": "", "error": "GPT_LOCAL_EMPTY_RESPONSE"}
        return {**_base(), "model": _model or MODEL, "mode": "local/ollama", "source": "gpt_local", "success": True, "reply": reply, "error": None}
    except urllib.error.HTTPError as exc:
        return {**_base(), "success": False, "reply": "", "error": "GPT_LOCAL_HTTP_ERROR", "http_status": exc.code, "error_type": type(exc).__name__}
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        if not _model:
            return ask(message, FALLBACK_MODEL, fmt=fmt, num_ctx=num_ctx, system=system)
        return {**_base(), "success": False, "reply": "", "error": "GPT_LOCAL_UNAVAILABLE", "error_type": type(exc).__name__}


# --- NEXUS GPT Werkzeug: Befehle ueber das NEXUS-Terminal (20261031) -----------------
TOOL_PROMPT = (
    "Du bist NEXUS GPT mit einem echten PowerShell-7-Terminal im Projekt G:\\meine-ki\\nexus-ai (Windows). "
    "Antworte NUR mit JSON: {\"command\": \"<genau ein PowerShell-Befehl>\", \"why\": \"<kurz>\"}. "
    "Nur lesende oder harmlose Befehle im Projekt auf G:. Niemals C:, nichts loeschen, nichts installieren. "
    "Wenn kein Befehl noetig ist: {\"command\": \"\", \"why\": \"...\"}."
)


def run_tool(command: str, shell: str = "session", timeout: int | None = None, approval_id: str | None = None) -> dict[str, Any]:
    """Echter Befehl als agent=NEXUS-GPT: Rechtestufe aus Terminal-Konfiguration, Erfolgspruefung, Audit, live im Stream."""
    from nexus.terminal import service as _term
    from nexus.safety import dispatch as _p2d  # Phase 2 Schritt 8
    r = _p2d.shell_execute(str(command or ""), agent="NEXUS-GPT", shell=shell, timeout=timeout, wait=True, approval_id=approval_id)
    return {**_base(), "tool": "terminal", "command": command, **r, "success": bool(r.get("ok"))}


def tool_ask(task: str) -> dict[str, Any]:
    """Aufgabe -> lokales Modell waehlt einen Befehl -> Terminal fuehrt echt aus -> Antwort nur aus echtem Ergebnis."""
    text = (task or "").strip()
    if not text:
        return {**_base(), "success": False, "error": "TASK_REQUIRED", "verdict": "FEHLER"}
    plan = ask(text, fmt="json", system=TOOL_PROMPT)
    if not plan.get("success"):
        return {**_base(), "success": False, "error": plan.get("error") or "GPT_LOCAL_UNAVAILABLE", "verdict": "FEHLER", "plan": plan}
    try:
        obj = json.loads(plan.get("reply") or "")
        cmd = str(obj.get("command") or "").strip()
        why = str(obj.get("why") or "")
    except (ValueError, AttributeError):
        return {**_base(), "success": False, "error": "GPT_TOOLCALL_INVALID", "verdict": "FEHLER", "raw": plan.get("reply")}
    if not cmd:
        return {**_base(), "success": False, "error": "GPT_NO_COMMAND", "verdict": "FEHLER", "why": why}
    res = run_tool(cmd)
    summary = ask(
        f"Aufgabe: {text}\nAusgefuehrter Befehl: {cmd}\nTerminal-Ergebnis: {res.get('verdict')} rc={res.get('returncode')} "
        f"Gruende={res.get('reasons')}\nAusgabe:\n{(res.get('stdout') or '')[:3000]}\n{(res.get('stderr') or '')[:1000]}\n"
        "Fasse nur dieses echte Ergebnis kurz zusammen. Erfinde nichts. Bei FEHLER klar sagen, dass es fehlgeschlagen ist."
    )
    return {**_base(), "success": bool(res.get("ok")), "verdict": res.get("verdict"), "reasons": res.get("reasons"),
            "command": cmd, "why": why, "terminal": {k: res.get(k) for k in ("run", "status", "returncode", "stdout", "stderr", "sec",
                                                                              "level_required", "level_agent", "approval_id")},
            "reply": summary.get("reply") or "", "error": None if res.get("ok") else (res.get("error") or ",".join(res.get("reasons") or []))}
