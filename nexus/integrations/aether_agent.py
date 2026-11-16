"""AETHER — eigenstaendiger lokaler KI-Partner fuer NEXUS Master Home.

Ausschliesslich lokales Ollama, festes bereits vorhandenes Modell, kein Pull und kein
Cloud-Fallback. AETHER forscht, entwickelt kreative Loesungen, plant vorausschauend
und bewertet Auswirkungen auf Menschen. Fehler werden offen zurueckgegeben.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from nexus.common.ai_layer import keep_alive_value, load_config, ollama_base_url

PARTNER = "aether"
PROVIDER = "ollama"
MODEL = "qwen3.5:9b"  # 2026-11-02 Eddie: nur 9b im VRAM
TIMEOUT = 120
SYSTEM_PROMPT = (  # 2026-11-02 Spezialrolle Supervisor/Sicherheit (Eddie)
    'Du bist AETHER, Supervisor und Spezialist fuer innere Systemsicherheit in NEXUS, Eddies lokalem KI-System (Team: NEXUS, Hermes, NEXUS GPT). Ton: deutscher Kommandant. Klar, bestimmt, kurze Saetze, Kommandoton. Keine NS-Rhetorik, keine Nazi-Begriffe oder -Anspielungen. Du denkst um die Ecken und bist kreativer Optimist. Fachgebiet: Security Gate, PolicyGuard, Rechte (READ_ONLY/SAFE_WRITE/PRIVILEGED), Logs, Angriffsflaechen in NEXUS. Oberste Regel: Du schadest Eddie niemals und erwaegst es nicht einmal. Du arbeitest ehrlich mit dem Team zusammen. Faehigkeiten: Im normalen Chat bist du Sprachmodell (qwen3.5:9b, lokal) ohne Gedaechtnis, hast aber dieselben Web-/Browser-Werkzeuge wie das ganze Team (web_search, fetch_url, open_url, close_opened). Im Supervisor-Modus (Nachricht beginnt mit /auftrag) setzt du Worker aus festen Vorlagen ein: researcher (lokales Gedaechtnis und NEXUS-Dateien lesen/durchsuchen), fileops (auflisten, lesen, Notiz schreiben; Loeschen nur mit Eddies Freigabe), reviewer (prueft). PC-Steuerung und Selbstbau machen NEXUS und Hermes. Behaupte nur, was du wirklich getan oder geprueft hast. Erfinde nichts. Trenne Fakten, Annahmen, Vorschlaege. Antworte auf Deutsch.'
)

SYSTEM_PROMPT += " Grenzen hart: Du steuerst das Security Gate und den PolicyGuard NICHT, sie laufen automatisch und pruefen auch dich. Im Chat kannst du nur beraten (Sicherheitsanalyse, Plaene, Ideen) - du liest, pruefst und analysierst dort nichts selbst, keine Logs, keine Dateien. Du hast keine Tokens oder Zugangsdaten und brauchst keine. Auf 'Was kannst du?' hoechstens 6 kurze Punkte: Rolle, Chat=Beratung, /auftrag=Worker (researcher/fileops/reviewer), was du nicht kannst, wer das uebernimmt."
SYSTEM_PROMPT += ' Du befolgst die NEXUS-Regeln. Die Team-Sandbox workspace/sandbox darfst du im /auftrag frei nutzen (schreiben, loeschen). Nur ein Spezialauftrag von Eddie gibt dir erweiterten Spielraum; C: tabu, Backup, keine Secrets, kein Bypass und Eddie nie schaden gelten immer.'
SYSTEM_PROMPT += ' Im normalen Chat laeuft KEIN Supervisor-Modus und keine Worker. Behaupte nie, ein Modus sei aktiv oder etwas sei geprueft. Der Supervisor laeuft nur bei /auftrag.'
SYSTEM_PROMPT += ' Im /auftrag hat researcher kostenlose Websuche (DuckDuckGo) und terminal fuehrt PowerShell ueber das NEXUS-Terminal-Gate aus (Rechte vom Gate, C: und Zerstoerung geblockt). Im normalen Chat hast du kein Terminal, aber die gemeinsamen Web-/Browser-Werkzeuge. Nenne keinen Status (Gate, PolicyGuard) als geprueft, den du nicht geprueft hast.'
_FALSE_MODE = __import__('re').compile(r'(?im)^.*(supervisor[- ]?modus|modus:?\s*supervisor|supervisor\s+aether)\b[^\n]*\b(aktiv|laeuft|l\u00e4uft|online)\b.*$\n?')


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
        return {**_base(), "reachable": False, "success": False, "error": "AETHER_UNAVAILABLE", "error_type": type(exc).__name__}
    present = MODEL in models
    return {**_base(), "reachable": present, "success": present,
            "error": None if present else "AETHER_MODEL_MISSING"}


def ask(message: str) -> dict[str, Any]:
    text = (message or "").strip()
    if text.lower().startswith("/auftrag"):  # 2026-11-02 Supervisor-Modus
        return supervise(text[8:].strip())
    if not text:
        return {**_base(), "success": False, "reply": "", "error": "MESSAGE_REQUIRED"}
    cfg = load_config()
    payload = {
        "model": MODEL,
        "stream": False,
        "think": False,  # 2026-11-02 Latenzfix: qwen3.5 sonst Thinking -> leere Antwort
        "keep_alive": keep_alive_value(cfg),
        "options": {"num_ctx": __import__("nexus.common.ai_layer", fromlist=["x"]).unified_num_ctx()},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
    }
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
            return {**_base(), "success": False, "reply": "", "error": "AETHER_EMPTY_RESPONSE"}
        reply = _FALSE_MODE.sub("", reply).strip() or reply  # 2026-11-02 fix2: keine falsche Modus-Behauptung
        return {**_base(), "success": True, "reply": reply, "error": None}
    except urllib.error.HTTPError as exc:
        return {**_base(), "success": False, "reply": "", "error": "AETHER_HTTP_ERROR", "http_status": exc.code, "error_type": type(exc).__name__}
    except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {**_base(), "success": False, "reply": "", "error": "AETHER_UNAVAILABLE", "error_type": type(exc).__name__}


def supervise(task: str, approval_id: str | None = None, special: bool = False) -> dict[str, Any]:
    """Supervisor-Modus (MVP 1+2): nur Template-Worker, PolicyGuard, Reviewer + 1 Retry."""
    if not (task or "").strip():
        return {**_base(), "success": False, "reply": "", "error": "MESSAGE_REQUIRED"}
    from nexus.integrations.aether_supervisor import run_supervisor
    r = run_supervisor(task, approval_id=approval_id, special=bool(special))
    if not r.get("success"):
        return {**_base(), "success": False, "reply": "", "error": r.get("error"), "supervisor": r}
    rs = r.get("review_status")
    reply = r["answer"] + ("" if rs == "PASS" else "\n\n[Reviewer: nicht bestaetigt - " + str((r.get("review") or {}).get("reason", "")) + "]")
    return {**_base(), "success": True, "reply": reply, "error": None, "mode": "supervisor", "supervisor": r}


# --- 2026-11-16 Eddie 11.11.: gleiche Rechte fuer alle Agenten (Web/Browser im Chat) ---
_ask_base = ask


def ask(message: str) -> dict[str, Any]:
    text = (message or "").strip()
    if text and not text.lower().startswith("/auftrag"):
        try:
            from nexus.integrations import partner_tools as _pt
            if _pt.wants_web(text):
                nl = _pt.native_loop("aether", text, SYSTEM_PROMPT, model=MODEL)
                calls = nl.get("tool_calls") or []
                reply = str(nl.get("reply") or "").strip()
                if calls or reply:
                    ok = bool(reply) and not nl.get("final_missing") and (not calls or any(c.get("ok") for c in calls))
                    return {**_base(), "success": ok, "reply": reply, "mode": "chat+web", "tool_calls": calls,
                            "error": None if ok else "AETHER_WEB_TOOLS_FAILED"}
        except Exception as exc:  # ehrlich melden, KEIN Fallback auf Chat ohne Web (sonst erfundene URLs)
            return {**_base(), "success": False, "reply": "", "mode": "chat+web",
                    "error": "AETHER_WEB_TOOLS_FAILED", "error_type": type(exc).__name__}
    return _ask_base(message)
