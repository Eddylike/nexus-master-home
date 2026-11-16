"""Gemeinsame Web-/Browser-Werkzeuge fuer ALLE Master-Home-Agenten (2026-11-16, Eddie-Auftrag 11.11.).

NEXUS, Hermes, NEXUS GPT und AETHER bekommen hier exakt dieselben Rechte und dieselben
Funktionen: web_search (DuckDuckGo ueber NEXUS-Research), fetch_url (oeffentliche Seite als Text)
und open_url / close_opened (Standardbrowser auf dem PC). Feste Grenzen gelten fuer alle gleich:
- nur http/https, keine Zugangsdaten in URL/Query, fetch_url keine privaten/Loopback-Netze
- keine Kosten (kostenlose Quellen), kein Senden/Anrufen, kein C:-Schreiben (diese Tools schreiben nichts)
- jeder Aufruf landet in workspace/partner_tools/audit.jsonl
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "workspace" / "partner_tools" / "audit.jsonl"

# Kanonische Agentennamen (wie in workspace/terminal/permissions.json)
AGENTS = {"nexus": "NEXUS", "hermes": "Hermes", "gpt": "NEXUS-GPT", "nexus-gpt": "NEXUS-GPT",
          "gpt_local": "NEXUS-GPT", "aether": "AETHER"}
SHARED_TOOLS = ("web_search", "fetch_url", "open_url", "close_opened")
_SECRET_RE = re.compile(r"(?i)(passw|token|api[_-]?key|secret|bearer|sk-[a-z0-9]|ghp_|authorization)")
_WEB_INTENT = re.compile(
    r"(?i)\b(internet|im\s+netz|online|web\s*suche|websuche|web_search|fetch_url|open_url|google|duckduckgo|"
    r"recherchier\w*|such\w*\s+(im|nach|online)|webseite|website|homepage|browser|url|https?://|www\.|"
    r"aktuell\w*\s+(news|nachrichten)|nachrichten|news)\b")

SCHEMAS = [
    {"type": "function", "function": {"name": "web_search",
     "description": "Kostenlose Live-Websuche (DuckDuckGo). Liefert Titel, URL, Snippet.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "fetch_url",
     "description": "Oeffentliche Webseite (http/https) als Text lesen.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "open_url",
     "description": "URL im Standardbrowser auf Eddies PC oeffnen (nur http/https).",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "close_opened",
     "description": "Von NEXUS geoeffnete Browser-Seiten wieder schliessen.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}}}},
]


def canonical_agent(agent: str) -> str | None:
    return AGENTS.get(str(agent or "").strip().lower())


def wants_web(text: str) -> bool:
    return bool(text) and bool(_WEB_INTENT.search(text))


def rights() -> dict[str, Any]:
    """Rechte-Matrix aus einer Quelle: Terminal-Stufe (permissions.json) + gemeinsame Web-Tools."""
    try:
        perm = json.loads((ROOT / "workspace" / "terminal" / "permissions.json").read_text(encoding="utf-8")).get("agents", {})
    except Exception:
        perm = {}
    out = {}
    for a in ("NEXUS", "Hermes", "NEXUS-GPT", "AETHER"):
        out[a] = {"terminal_level": perm.get(a), "web_tools": list(SHARED_TOOLS)}
    levels = {v["terminal_level"] for v in out.values()}
    return {"agents": out, "equal": len(levels) == 1 and None not in levels,
            "limits": ["C: tabu", "Senden/Anrufe nur nach Bestaetigung", "keine Kosten", "kein Root/Bypass",
                       "nur http/https", "keine Secrets in URL/Query", "fetch_url ohne private Netze"]}


def _audit(rec: dict) -> None:
    try:
        AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _check_http(url: str) -> str:
    u = str(url or "").strip()
    if u and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", u):
        u = "https://" + u
    pr = urllib.parse.urlparse(u)
    if pr.scheme not in ("http", "https") or not pr.hostname or pr.username or pr.password or _SECRET_RE.search(u):
        raise PermissionError("URL_DENIED")
    return u


def _web_search(query: str, limit: int = 5) -> dict:
    q = str(query or "").strip()
    if not q:
        raise ValueError("QUERY_REQUIRED")
    if _SECRET_RE.search(q):
        raise PermissionError("SECRET_IN_QUERY")
    from nexus.knowledge.research import Research
    r = Research().search(q[:200], limit=max(1, min(int(limit or 5), 8)))
    res = [{"title": x.get("title"), "url": x.get("url"), "snippet": str(x.get("snippet") or "")[:300]}
           for x in (r.get("results") or [])][:8]
    return {"success": bool(r.get("success")) and bool(res), "error": r.get("error") or (None if res else "NO_RESULTS"),
            "results": res}


def _ssl_contexts():
    import ssl
    yield ssl.create_default_context()  # Windows-Zertifikatsspeicher
    try:
        import certifi  # Fallback: Mozilla-CA-Bundle, weiterhin VOLLE Zertifikatspruefung
        yield ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return


def _fetch_url(url: str, max_chars: int = 4000) -> dict:
    import ipaddress, socket, ssl
    u = _check_http(url)
    host = urllib.parse.urlparse(u).hostname
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
    except (socket.gaierror, ValueError):
        raise PermissionError("DNS_FAIL")
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        raise PermissionError("PRIVATE_NET_DENIED")
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 NEXUS-Partner"})
    last = None
    for ctx in _ssl_contexts():
        try:
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                raw = r.read(600_000).decode("utf-8", errors="replace")
            break
        except urllib.error.URLError as e:
            last = e
            if not isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError):
                raise
    else:
        raise last
    txt = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", raw)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"\s+", " ", txt).strip()[: min(int(max_chars or 4000), 8000)]
    return {"success": bool(txt), "url": u, "text": txt, "error": None if txt else "EMPTY"}


def _open_url(url: str) -> dict:
    u = _check_http(url)
    from nexus.computer.os_actions import get_os_actions
    r = get_os_actions().open_url(u)
    return {"success": bool(r.get("success", r.get("ok"))), "url": u, "method": r.get("method"),
            "reply": r.get("reply"), "error": r.get("error")}


def _close_opened(url: str | None = None) -> dict:
    from nexus.computer.os_actions import get_os_actions
    r = get_os_actions().close_opened(url or None)
    return {"success": bool(r.get("success", r.get("ok"))), "reply": r.get("reply"), "error": r.get("error")}


def run(agent: str, tool: str, args: dict | None = None) -> dict:
    """Ein Werkzeug fuer einen Agenten ausfuehren - identisch fuer alle Agenten."""
    a = canonical_agent(agent)
    t = str(tool or "").strip().lower()
    args = args if isinstance(args, dict) else {}
    t0 = time.time()
    if not a:
        return {"success": False, "agent": agent, "tool": t, "error": "AGENT_UNKNOWN"}
    if t not in SHARED_TOOLS:
        return {"success": False, "agent": a, "tool": t, "error": "TOOL_NOT_SHARED"}
    try:
        if t == "web_search":
            r = _web_search(args.get("query") or args.get("q") or "", args.get("limit") or 5)
        elif t == "fetch_url":
            r = _fetch_url(args.get("url") or "", int(args.get("max_chars") or 4000))
        elif t == "open_url":
            r = _open_url(args.get("url") or "")
        else:
            r = _close_opened(args.get("url"))
    except PermissionError as e:
        r = {"success": False, "error": str(e), "status": "DENIED"}
    except Exception as e:  # ehrlich melden
        r = {"success": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}
    out = {"agent": a, "tool": t, **r, "ms": int((time.time() - t0) * 1000)}
    _audit({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "agent": a, "tool": t,
            "args": {k: str(v)[:200] for k, v in args.items()}, "success": out.get("success"), "error": out.get("error")})
    return out


def native_loop(agent: str, task: str, system: str, model: str = "qwen3.5:9b", max_turns: int = 4, timeout: int = 240) -> dict:
    """Ollama-native Tool-Loop nur mit den gemeinsamen Web-Tools (fuer Chat von GPT/AETHER)."""
    from nexus.common.ai_layer import keep_alive_value, load_config, ollama_base_url, unified_num_ctx
    cfg = load_config()
    sys = (system + " WEB-WERKZEUGE (gleiche Rechte wie das ganze Team): web_search, fetch_url, open_url, close_opened. "
           "Nutze sie ueber echte tool_calls, wenn die Aufgabe Internet/Webseiten/Browser braucht. "
           "Antworte danach nur aus echten Werkzeug-Ergebnissen, mit URL als Quelle, kurz auf Deutsch.")
    msgs = [{"role": "system", "content": sys}, {"role": "user", "content": task}]
    calls: list[dict] = []
    t0 = time.time()
    for _ in range(max_turns):
        left = timeout - (time.time() - t0)
        if left < 10:
            break
        body = {"model": model, "stream": False, "think": False, "keep_alive": keep_alive_value(cfg),
                "options": {"num_ctx": unified_num_ctx()}, "messages": msgs, "tools": SCHEMAS}
        req = urllib.request.Request(ollama_base_url(cfg) + "/api/chat", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=int(left)) as resp:
            msg = (json.loads(resp.read().decode("utf-8")).get("message") or {})
        tcs = msg.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tcs})
        if not tcs:
            return {"reply": (msg.get("content") or "").strip(), "tool_calls": calls}
        for tc in tcs:
            fn = tc.get("function", {}) or {}
            fargs = fn.get("arguments") or {}
            if isinstance(fargs, str):
                try:
                    fargs = json.loads(fargs)
                except ValueError:
                    fargs = {}
            r = run(agent, fn.get("name", ""), fargs)
            calls.append({"tool": r.get("tool"), "ok": bool(r.get("success")), "error": r.get("error"),
                          "args": {k: str(v)[:200] for k, v in (fargs or {}).items()}})
            msgs.append({"role": "tool", "tool_name": fn.get("name", ""), "content": json.dumps(r, ensure_ascii=False)[:6000]})
    lines = [f"- {c['tool']}: {'OK' if c['ok'] else 'FEHLER ' + str(c['error'])}" for c in calls]
    return {"reply": "Werkzeug-Ergebnisse (Schritt-/Zeitlimit):\n" + "\n".join(lines) if calls else "WEB_TOOL_LOOP_LIMIT",
            "tool_calls": calls, "final_missing": True}