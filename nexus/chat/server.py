from __future__ import annotations

import os
import base64
import json
import mimetypes
import sys
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nexus.chat.engine import IDENTITY_FIELDS, NexusEngine, response_identity
from nexus.chat.shared_room import SharedRoom
from nexus.master.activity import get_activity_log
from nexus.common.config import Config
from nexus.common.paths import STATIC_DIR, MEDIA_DIR, DOWNLOADS_DIR, SCREENS_DIR, ensure_dirs

HOST = Config.HOST
PORT = Config.PORT
_CORS_ORIGINS = frozenset(o.strip() for o in os.environ.get("NEXUS_CORS_ORIGINS", "").split(",") if o.strip())
ENGINE = NexusEngine()
SHARED_ROOM = SharedRoom()

# HTTP server instances owned by this process (used for graceful maintenance shutdown).
_HTTP_SERVERS: list = []


class _ClientDisconnected(ConnectionError):
    """The HTTP peer disconnected while its response was being written."""


def _assistant_token_path() -> Path:
    """Lokale Token-Datei (nie committen): workspace/apex/assistant_token.txt."""
    return ROOT / "workspace" / "apex" / "assistant_token.txt"


def _load_assistant_token() -> str:
    """Bearer-Token: Env NEXUS_ASSISTANT_TOKEN, sonst Datei. Leer = Auth aktiv, aber kein gültiger Token."""
    env = (os.environ.get("NEXUS_ASSISTANT_TOKEN") or "").strip()
    if env:
        return env
    fp = _assistant_token_path()
    try:
        if fp.is_file():
            return fp.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def _mask_token(tok: str) -> str:
    if not tok:
        return "(leer)"
    if len(tok) <= 8:
        return "..." + tok[-4:]
    return "..." + tok[-4:]


ASSISTANT_TOKEN = _load_assistant_token()
if ASSISTANT_TOKEN:
    print(f"Assistant-API Auth: Bearer aktiv (Token {_mask_token(ASSISTANT_TOKEN)})", flush=True)
else:
    print("Assistant-API Auth: WARN - kein Token (Env/Datei). /api/assistant -> 401 bis Token gesetzt.", flush=True)


# Required service: abort startup if the Bridge cannot register its routes.
# Otherwise /api/health could report ONLINE while every Bridge route returns 404.
from nexus.bridge import api as bridge_api
bridge_api.boot()


# --- Controlled Automations (LOCAL only; NOT on Funnel allowlist) ---
try:
    from nexus.automation import api as automation_api
except Exception as _auto_boot_err:
    automation_api = None  # type: ignore
    print(f"Automation: import failed: {_auto_boot_err}", flush=True)


# --- Multimodal Layer (LOCAL only; NOT on Funnel allowlist; honest statuses) ---
try:
    from nexus.multimodal import api as multimodal_api
except Exception as _mm_boot_err:
    multimodal_api = None  # type: ignore
    print(f"Multimodal: import failed: {_mm_boot_err}", flush=True)





def _dev_orders(session_id: str) -> dict:
    """Alle Aufträge einer Sitzung mit getrenntem Zustand; nur belegte Orchestrator-Ereignisse."""
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9_-]{1,80}", session_id or ""):
        return {"success": False, "error": "session_id required"}
    try:
        room = SHARED_ROOM.load(session_id)
    except Exception:
        return {"success": False, "error": "session_unavailable"}
    runs = room.get("collaboration_runs") or {}
    if not runs and isinstance(room.get("collaboration"), dict):
        runs = {"legacy": room["collaboration"]}
    out = []
    for state in runs.values():
        if not isinstance(state, dict):
            continue
        events = [e for e in state.get("events") or [] if isinstance(e, dict)]
        dev = [e for e in events if e.get("kind") == "dev"]
        tasks = [e for e in events if e.get("kind") in ("task", "verification")]
        execution = state.get("execution") or {}
        out.append({"run_id": state.get("run_id"), "goal": str(state.get("goal") or "")[:400],
                    "status": state.get("status"), "phase": state.get("phase"), "error": state.get("error"),
                    "dev": dev[-80:], "receipts": tasks[-60:],
                    "task_ids": execution.get("task_ids") or [],
                    "superseded_task_ids": execution.get("superseded_task_ids") or [],
                    "preview_url": execution.get("preview_url"), "approval_id": execution.get("approval_id"),
                    "verification": state.get("verification"),
                    "superseded_by": state.get("superseded_by"), "resumes": state.get("resumes"),
                    "updated": max([str(e.get("ts") or "") for e in events] or [""])})
    out.sort(key=lambda r: r["updated"], reverse=True)
    return {"success": True, "session_id": session_id, "orders": out,
            "active_run_id": (room.get("collaboration") or {}).get("run_id")}


def _preview_current(meta_path) -> bool:
    """Nur Vorschauen, deren Prüfung die aktuelle Kriterienversion erfüllt hat."""
    import json as _json
    from nexus.bridge.web_check import CRITERIA_VERSION
    try:
        info = _json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return info.get("criteria_version") == CRITERIA_VERSION


def _preview_list() -> dict:
    """Geprüfte Vorschauen mit Startlink und Prüfbeleg (für Chamber und Oberfläche)."""
    import json as _json
    out = []
    base = ROOT / "workspace" / "projects" / "hermes" / "preview"
    for meta in sorted(base.glob("*/preview.json")) if base.is_dir() else []:
        try:
            info = _json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not _preview_current(meta):
            continue
        entry = next((n for n in info.get("sha256", {}) if n.endswith("index.html")), None)
        out.append({"project": info.get("project"), "url": "/preview/" + str(info.get("project")) + "/" + str(entry or "index.html"),
                    "check_task_id": info.get("check_task_id"), "publish_task_id": info.get("publish_task_id"),
                    "published_at": info.get("published_at"), "stage": info.get("stage")})
    return {"success": True, "previews": out}


class Handler(BaseHTTPRequestHandler):
    server_version = "NexusAI/1.0"

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (_ClientDisconnected, BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # A closed peer cannot receive an error response or a final flush.
            self.close_connection = True

    def end_headers(self) -> None:
        try:
            super().end_headers()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            self.close_connection = True
            raise _ClientDisconnected() from exc

    def _write_response_data(self, data: bytes) -> None:
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            self.close_connection = True
            raise _ClientDisconnected() from exc

    def _flush_response_data(self) -> None:
        try:
            self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
            self.close_connection = True
            raise _ClientDisconnected() from exc

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _cors(self) -> None:
        # Kein "*" mehr: Das UI läuft same-origin und braucht kein CORS. Fremde Origins nur per
        # expliziter Allowlist (NEXUS_CORS_ORIGINS="http://a:8299,http://b:8299").
        origin = self.headers.get("Origin") if getattr(self, "headers", None) else None
        if origin and origin in _CORS_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _cross_site_blocked(self) -> bool:
        """CSRF-Schutz für POST: Browser-Requests von fremden Seiten ablehnen.

        Native Clients (Handy-App, curl, Agenten) senden weder Origin noch Sec-Fetch-Site
        und bleiben unberührt. Der Loopback-Check allein schützt nicht, weil ein Browser
        auf demselben Rechner ebenfalls von 127.0.0.1 kommt.
        """
        h = self.headers
        if h.get("Sec-Fetch-Site", "") == "cross-site":
            return True
        origin = h.get("Origin")
        if not origin or origin in _CORS_ORIGINS:
            return False
        if origin == "null":
            return True
        return urlparse(origin).netloc.lower() != (h.get("Host") or "").lower()

    def _serve_evidence(self, rel: str) -> None:
        """Nur PNG-Screenshots der Browserprüfungen (Beleg), keine anderen Dateien."""
        import re as _re
        from urllib.parse import unquote
        parts = unquote(rel).split("/")
        if (len(parts) != 2 or not _re.fullmatch(r"[A-Za-z0-9_-]{1,80}", parts[0])
                or not _re.fullmatch(r"(?:check|preview)-\d{8}-\d{6}\.png", parts[1])):
            return self._json(404, {"success": False, "error": "evidence_not_found"})
        target = ROOT / "workspace" / "projects" / "hermes" / "preview_evidence" / parts[0] / parts[1]
        if not target.is_file():
            return self._json(404, {"success": False, "error": "evidence_not_found"})
        return self._bytes(200, target.read_bytes(), "image/png")

    def _serve_preview(self, rel: str) -> None:
        """Nur geprüfte Vorschauen (workspace/projects/hermes/preview/<projekt>/), streng isoliert.

        CSP "sandbox allow-scripts": eigene, undurchsichtige Herkunft ohne Zugriff auf NEXUS-
        APIs, Cookies oder Speicher; connect-src 'none' sperrt jede Netzwerkanfrage.
        """
        import re as _re
        from urllib.parse import unquote
        rel = unquote(rel)
        parts = rel.split("/")
        types = {"html": "text/html; charset=utf-8", "css": "text/css; charset=utf-8",
                 "js": "application/javascript; charset=utf-8", "json": "application/json; charset=utf-8"}
        if (len(parts) != 2 or not _re.fullmatch(r"[A-Za-z0-9_-]{1,80}", parts[0])
                or not _re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", parts[1]) or parts[1].startswith(".")
                or parts[1].rsplit(".", 1)[-1].lower() not in types or parts[1] == "preview.json"):
            return self._json(404, {"success": False, "error": "preview_not_found"})
        base = (ROOT / "workspace" / "projects" / "hermes" / "preview" / parts[0]).resolve()
        target = (base / parts[1]).resolve()
        if base not in target.parents or not target.is_file() or not (base / "preview.json").is_file():
            return self._json(404, {"success": False, "error": "preview_not_found"})
        if not _preview_current(base / "preview.json"):
            # Nach älteren (schwächeren) Kriterien geprüft: nicht mehr als geprüfte Vorschau ausliefern.
            return self._json(409, {"success": False, "error": "preview_outdated_recheck_required"})
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", types[parts[1].rsplit(".", 1)[-1].lower()])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy",
                         "sandbox allow-scripts allow-pointer-lock; default-src 'none'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
                         "media-src 'self' data: blob:; connect-src 'none'; base-uri 'none'; form-action 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._write_response_data(data)

    def _json(self, code: int, payload: dict) -> None:
        _lp = getattr(self, "_live_path", None)
        if _lp:
            self._live_path = None
            try:
                from nexus.common import live_events as _le
                _src = _LIVE_POST.get(next((x for x in _LIVE_POST if _lp.startswith(x)), ""), "Hermes")
                _ok = bool(payload.get("success")) if isinstance(payload, dict) else None
                _txt = ""
                if isinstance(payload, dict):
                    _txt = payload.get("reply") or payload.get("status") or payload.get("error") or ""
                _le.emit(_src, "antwort", f"HTTP {code}: {_txt}", target="NEXUS", ok=_ok,
                         data={k: payload.get(k) for k in ("status", "error", "run_id", "agent") if isinstance(payload, dict) and payload.get(k)})
            except Exception:
                pass
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self._write_response_data(data)

    def _bytes(self, code: int, data: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if content_type.startswith("text/html"):  # 20261028: kein veraltetes Master Home (alte JS-Version) aus Browser-Cache
            self.send_header("Cache-Control", "no-cache")
        _ck = getattr(self, "_nx_set_cookie", None)
        if _ck:
            self.send_header("Set-Cookie", _ck)
            self._nx_set_cookie = None
        self._cors()
        self.end_headers()
        self._write_response_data(data)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}


    def _assistant_authorized(self) -> bool:
        """Bearer-Auth nur für /api/assistant. Constant-time Vergleich wenn Token gesetzt."""
        expected = ASSISTANT_TOKEN
        if not expected:
            return False
        hdr = self.headers.get("Authorization") or self.headers.get("authorization") or ""
        if not hdr.lower().startswith("bearer "):
            return False
        got = hdr[7:].strip()
        if not got:
            return False
        # compare_digest braucht gleiche Länge — bei Längen-Mismatch False
        import hmac
        try:
            return hmac.compare_digest(got.encode("utf-8"), expected.encode("utf-8"))
        except Exception:
            return got == expected

    def _reject_assistant_unauthorized(self) -> None:
        self._json(401, {"success": False, "error": "unauthorized"})

    def _sse_start(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()

    def _sse_event(self, payload) -> None:
        if isinstance(payload, str):
            line = payload
        else:
            line = json.dumps(payload, ensure_ascii=False)
        self._write_response_data(f"data: {line}\n\n".encode("utf-8"))
        self._flush_response_data()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:  # NEXUS OS live link evidence (memory only): last request per client IP
            from nexus import nexus_os as _nos
            _nos.note_client(self.client_address[0], path)
        except Exception:
            pass
        qs = parse_qs(parsed.query)
        try:  # 005D NEXUS OS link (device heartbeat / sync), network-restricted
            from nexus import nexus_os_link as _nol
            _r = _nol.handle("GET", path, self.client_address[0], self.headers, qs=qs)
            if _r is not None:
                return self._json(_r[0], _r[1])
        except Exception:
            traceback.print_exc()
            if path in ("/api/nexus-os/device", "/api/nexus-os/sync", "/api/nexus-os/push", "/api/nexus-os/link", "/api/nexus-os/pair"):
                return self._json(503, {"success": False, "error": "NEXUS_OS_LINK_UNAVAILABLE"})
        # Local-only maintenance status (Bearer + loopback enforced in bridge api).
        if bridge_api is not None and bridge_api.is_maintenance_path(path):
            return bridge_api.handle_maintenance_get(self, path, qs)
        try:
            if path in ("/", "/index.html"):
                try:  # NEXUS-Terminal: UI-Geheimnis nur fuer lokale Browser-Navigation (Same-Origin-Cookie)
                    if (self.client_address[0] in ("127.0.0.1", "::1") and not self.headers.get("X-Forwarded-For")
                            and not self.headers.get("Tailscale-User-Login") and self.headers.get("Sec-Fetch-Mode") == "navigate"
                            and self.headers.get("Sec-Fetch-Dest") == "document"):
                        from nexus.terminal import guard as _tg
                        self._nx_set_cookie = _tg.ui_cookie()
                except Exception:
                    pass
                return self._bytes(200, (STATIC_DIR / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path in ("/m", "/m/", "/m.html"):
                return self._bytes(200, (STATIC_DIR / "m.html").read_bytes(), "text/html; charset=utf-8")
            if path in ("/m-classic", "/m-classic/"):  # NEXUS OS GPT 3.0: alte /m-Ansicht bleibt erreichbar
                return self._bytes(200, (STATIC_DIR / "m_classic.html").read_bytes(), "text/html; charset=utf-8")
            if path in ("/m2", "/m2/", "/m2.html"):  # NEXUS Phone UI v2 (Viki+Crystal OS+Smart View), Audit 2026-10-21
                return self._bytes(200, (STATIC_DIR / "m2.html").read_bytes(), "text/html; charset=utf-8")
            # PWA: SW at scope root (recommended for scope "/")
            if path == "/sw.js":
                sw = STATIC_DIR / "sw.js"
                if not sw.is_file():
                    return self._json(404, {"success": False, "error": "sw_missing"})
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Content-Length", str(sw.stat().st_size))
                self.send_header("Service-Worker-Allowed", "/")
                self.send_header("Cache-Control", "no-cache")
                self._cors()
                self.end_headers()
                self._write_response_data(sw.read_bytes())
                return
            if path in ("/manifest.webmanifest", "/static/manifest.webmanifest"):
                mf = STATIC_DIR / "manifest.webmanifest"
                if not mf.is_file():
                    return self._json(404, {"success": False, "error": "manifest_missing"})
                return self._bytes(200, mf.read_bytes(), "application/manifest+json; charset=utf-8")
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            if path.startswith("/preview/"):
                return self._serve_preview(path[len("/preview/"):])
            if path == "/api/previews":
                return self._json(200, _preview_list())
            if path == "/api/dev/orders":
                return self._json(200, _dev_orders((qs.get("session_id") or [""])[0]))
            if path.startswith("/preview-evidence/"):
                return self._serve_evidence(path[len("/preview-evidence/"):])
            if path.startswith("/media/"):
                return self._serve_file(MEDIA_DIR, path[len("/media/"):])
            if path.startswith("/downloads/"):
                return self._serve_file(DOWNLOADS_DIR, path[len("/downloads/"):])
            if path.startswith("/screens/"):
                return self._serve_file(SCREENS_DIR, path[len("/screens/"):])

            # Local Approval UI (NOT on Funnel allowlist)
            if path in ("/approvals", "/approvals.html"):
                return self._bytes(200, (STATIC_DIR / "approvals.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/nexusall" or path.startswith(("/nexusall/", "/nexusall.html", "/api/nexusall/")):  # NEXUS All (20261028)
                from nexus.nexusall.api import handle as _nxa
                if _nxa(self, "GET", path, qs, None):
                    return
            if path in ("/mail", "/mail.html"):  # NEXUS Mail: PC + Handy ueber Tailscale
                return self._bytes(200, (STATIC_DIR / "mail.html").read_bytes(), "text/html; charset=utf-8")

            if path in ("/api/mail/send", "/api/mail/validate"):  # Blockade fuer keine direkte Freigabe (/mail) und Phishing-Schutz
                from nexus.mail import security as mail_sec
                if not mail_sec.is_safe_access(self):
                    return self._json(401, {"success": False, "error": "unauthorized", "hint": "NEXUS Mail: Login ueber Tailscale erforderlich (kein direktes /mail POST ohne Authentifizierung)"})
                # Hinweis-Logik: Phishing/Spam-Warnung in die Antwort integrieren
                if path == "/api/mail/validate":
                    try:
                        sender = qs.get("from", [""])[0]
                        to_addr = qs.get("to", [""])[0]
                        mail_sec.check_phishing_hints(sender=sender, to=to_addr)
                    except Exception as e:
                        pass  # Keine Ausnahmen aufwerfen im HTTP Handler
                return self._json(200, {"success": True, "message": "Mail-API Zugriff: Nur freigegebene Adressen (send_allowed) permitted."})
            if path in ("/studio", "/studio.html"):  # NEXUS Studio (20261028)
                return self._bytes(200, (STATIC_DIR / "studio.html").read_bytes(), "text/html; charset=utf-8")
            if path.startswith("/api/studio/"):
                return self._studio("GET", path, qs, None)
            if path == "/api/swarm/status" or path.startswith("/api/swarm/runs/"):  # NEXUS Swarm AI (20261104)
                from nexus.swarm.core import get_master as _gsm
                if path == "/api/swarm/status":
                    return self._json(200, _gsm().status())
                _r = _gsm().get(path.rsplit("/", 1)[-1])
                return self._json(200 if _r else 404, {"success": bool(_r), "run": _r} if _r else {"success": False, "error": "not_found"})
            if path in ("/swarm", "/swarm.html"):
                return self._bytes(200, (STATIC_DIR / "swarm.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/autobuild/status":  # Selbstbau-Engine (20261028)
                from nexus.autobuild.loop import get_autobuild
                return self._json(200, get_autobuild().status())
            if path in ("/live", "/live_monitor.html"):  # Live-Monitor (20261028)
                return self._bytes(200, (STATIC_DIR / "live_monitor.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/api/live/events":
                from nexus.common import live_events as _le
                return self._json(200, {"success": True, "events": _le.tail(int((qs.get("limit") or ["200"])[0]))})
            if path == "/api/live/config":  # von Hermes per Bridge erstellt (Abschlusstest)
                _cf = ROOT / "workspace" / "projects" / "hermes" / "live_monitor_agents.json"
                try:
                    return self._json(200, {"success": True, "source": str(_cf), **json.loads(_cf.read_text(encoding="utf-8"))})
                except Exception:
                    return self._json(200, {"success": False, "error": "config_missing"})
            if path == "/api/live/stream":
                return self._live_stream(qs)
            if path.startswith("/api/terminal/"):  # NEXUS-Terminal (20261031)
                from nexus.terminal import service as _term
                if path == "/api/terminal/stream":
                    return self._live_stream(qs, fp=_term.LOG_FILE)
                if path == "/api/terminal/history":
                    _ag = (qs.get("agent") or [""])[0] or None
                    _rid = (qs.get("run") or [""])[0] or None
                    return self._json(200, {"success": True, "events": _term.history(int((qs.get("limit") or ["300"])[0]), agent=_ag, run_id=_rid)})
                if path == "/api/terminal/state":
                    from nexus.terminal import guard as _tg
                    return self._json(200, {"success": True, "running": _term.active_runs(), "approvals": _tg.pending_approvals(),
                                            "levels": _tg.agent_levels()})
                if path == "/api/terminal/audit":
                    from nexus.terminal import guard as _tg
                    return self._json(200, {"success": True, "audit": _tg.audit_tail(int((qs.get("limit") or ["200"])[0]))})
                if path == "/api/terminal/sessions":
                    return self._json(200, {"success": True, "pwsh": _term.pwsh_path(), "sessions": _term.sessions()})
            if path == "/api/health":
                return self._json(200, ENGINE.health())
            if path == "/api/ai/models" or path == "/api/ai/status":
                from nexus.common import ai_layer
                return self._json(200, {"success": True, "ok": True, **ai_layer.status(ENGINE.master.llm)})
            if path == "/api/status":
                return self._json(200, ENGINE.status())
            if path == "/api/system/metrics":
                from nexus.computer.monitor import collect as monitor_collect
                return self._json(200, monitor_collect())
            if path == "/api/system/monitor":
                from nexus.computer.monitor import collect as monitor_collect
                return self._json(200, monitor_collect())
            if path == "/api/system/monitor/history":
                from nexus.computer.monitor import history_read
                n = 60
                try:
                    n = int((qs.get("n") or ["60"])[0])
                except Exception:
                    n = 60
                return self._json(200, {"success": True, "samples": history_read(n)})
            if path == "/api/system/alerts":
                from nexus.computer.monitor import collect as monitor_collect
                data = monitor_collect(write_history=False)
                return self._json(200, {"success": True, "alerts": data.get("alerts") or [], "timestamp": data.get("timestamp")})

            if path == "/api/sessions":
                return self._json(200, {"success": True, "active": ENGINE.session_id, "sessions": ENGINE.list_sessions()})
            if path == "/api/session":
                sid = (qs.get("id") or [None])[0]
                s = ENGINE.get_session(sid)
                return self._json(200, {"success": True, "session": s})
            if path == "/api/session/rev":
                # 2026-10-28 SYNC: leichter Aenderungs-Stempel fuer PC <-> Handy (NEXUS OS GPT)
                sid = (qs.get("id") or [None])[0] or ENGINE.session_id
                s = ENGINE.get_session(sid) or {}
                msgs = s.get("messages") or []
                return self._json(200, {"success": True, "id": s.get("id") or sid, "active": ENGINE.session_id,
                                        "count": len(msgs), "updated": s.get("updated"), "title": s.get("title")})
            if path == "/api/shared-room":
                sid = (qs.get("session_id") or qs.get("id") or [ENGINE.session_id])[0]
                return self._json(200, SHARED_ROOM.load(str(sid)))
            if path == "/api/shared-room/events":
                sid = str((qs.get("session_id") or [ENGINE.session_id])[0])
                room = SHARED_ROOM.load(sid)
                state = room.get("collaboration") or {}
                run_id = state.get("run_id")
                messages = [m for m in room.get("messages", []) if m.get("source") == "nexus_collaboration" and (m.get("metadata") or {}).get("run_id") == run_id]
                return self._json(200, {"success": True, "session_id": sid,
                    "run_id": run_id, "status": state.get("status", "UNKNOWN"), "phase": state.get("phase"),
                    "messages": messages, "events": state.get("events", []), "max_rounds": 2})
            if path == "/api/shared-room/status":
                sid = (qs.get("session_id") or qs.get("id") or [ENGINE.session_id])[0]
                return self._json(200, SHARED_ROOM.status(str(sid)))

            if path == "/api/projects":
                return self._json(200, {"success": True, "active": ENGINE.master.projects.active_id, "projects": ENGINE.master.projects.list()})
            if path == "/api/tasks":
                status = (qs.get("status") or [None])[0]
                return self._json(200, {"success": True, "tasks": ENGINE.master.tasks.list(status)})
            if path == "/api/capabilities/evidence":
                from nexus.master.evidence import capability_snapshot
                return self._json(200, {"success": True, **capability_snapshot()})
            if path == "/api/orders":
                from nexus.master.orders_view import orders_snapshot
                return self._json(200, {"success": True, **orders_snapshot()})

            # NEXUS Phone 002: Kontakte/Notizen/Kalender/Timer/Geraet (nexus/phone_api.py)
            if path in ("/api/contacts", "/api/notes", "/api/calendar", "/api/timer", "/api/device"):
                from nexus import phone_api as _phone_api
                _pr = _phone_api.handle_get(path, qs)
                if _pr is not None:
                    return self._json(_pr[0], _pr[1])
            if path == "/api/memory":
                q = (qs.get("q") or [""])[0]
                return self._json(200, ENGINE.master.memory.recall(q))
            if path == "/api/corrections":
                items = ENGINE.master.corrections.list(limit=int(qs.get("limit", ["40"])[0] or 40))
                return self._json(200, {"success": True, "items": items,
                    "context": ENGINE.master.corrections.context_snippets()})
            if path == "/api/files":
                sub = (qs.get("path") or [""])[0]
                return self._json(200, ENGINE.master.files.list(sub))
            if path == "/api/media":
                return self._json(200, {"success": True, "state": ENGINE.master.media.state()})
            if path == "/api/media/library":
                ENGINE.master.media.refresh()
                return self._json(200, {"success": True, "state": ENGINE.master.media.state()})
            if path == "/api/downloads":
                return self._json(200, ENGINE.master.downloads.list())
            if path == "/api/apps":
                return self._json(200, ENGINE.master.apps.list())
            if path == "/api/crew":
                return self._json(200, ENGINE.master.crew.list_agents())
            if path == "/api/crew/active":
                return self._json(200, ENGINE.master.crew.get_active())
            if path == "/api/videos":
                from nexus.media.video_library import VideoLibrary
                return self._json(200, VideoLibrary().list())
            if path == "/api/gallery":
                from nexus.media.gallery import list_gallery
                return self._json(200, list_gallery())
            # heynexus-20261006: Weckwort-/Sprachbefehl-Status (additiv)
            if path in ("/api/voice/wakeword/state", "/api/voice/commands/status"):
                from nexus.voice import commands as _vc
                _code, _payload = _vc.handle_get(path, qs, ENGINE)
                return self._json(_code, _payload)
            if path == "/api/voice/status":
                vs = ENGINE.master.voice.status()
                try:
                    vs["provider_route"] = ENGINE.master.ai.route()
                except Exception as e:
                    vs["provider_route"] = {"error": type(e).__name__}
                return self._json(200, vs)
            # NEXUS OS — Identity, Provider-Slots, realer Systemstatus (lokale Shell)
            if path == "/api/nexus-os/status":
                from nexus import nexus_os
                # v2: monitor-fed snapshot (periodic re-check), ?refresh=1 runs a real cycle now,
                # ?e2e=chat additionally runs one local chat E2E (on demand only).
                nexus_os.start_monitor(lambda: ENGINE.master.ai)
                force = (qs.get("refresh") or ["0"])[0] in ("1", "true", "yes")
                if (qs.get("e2e") or [""])[0] == "chat":
                    from nexus.maintenance import block_payload as _mbp
                    from nexus.maintenance import maintenance as _mnt
                    if _mnt().is_draining():
                        return self._json(503, _mbp())
                    with _mnt().work("nexus_os_e2e"):
                        nexus_os.run_chat_e2e()
                    force = True
                _nos_out = nexus_os.collect(provider=ENGINE.master.ai if force else None, force=force)
                try:  # 003A: ALL-Modus-Status (nur lesen; ACTIVE kommt vom Admin-Dienst)
                    from nexus.integrations import hermes_allmode as _ham
                    _nos_out = dict(_nos_out); _nos_out["all_mode"] = _ham.status()
                except Exception as _e:
                    _nos_out = dict(_nos_out); _nos_out["all_mode"] = {"state": "OFF", "error": type(_e).__name__}
                return self._json(200, _nos_out)
            if path == "/api/nexus-os/status/lite":
                from nexus import nexus_os
                # read-only reduced view for the phone app; never forces a probe cycle
                nexus_os.start_monitor(lambda: ENGINE.master.ai)
                return self._json(200, nexus_os.public_view(nexus_os.collect()))
            if path == "/api/nexus-os/identity":
                from nexus import nexus_os
                return self._json(200, {"success": True, **nexus_os.identity(), "config_path": str(nexus_os.CONFIG_PATH)})
            if path == "/api/nexus-os/providers":
                try:
                    out = ENGINE.master.ai.provider_status()
                except Exception as e:
                    return self._json(200, {"success": False, "error": type(e).__name__})
                out["success"] = True
                return self._json(200, out)
            if path == "/api/streams":
                return self._json(200, ENGINE.master.streams.list())
            # AETHER — eigenstaendiger lokaler Forschungspartner (Ollama, kein Cloud-Fallback)
            if path == "/api/aether/health":
                from nexus.integrations.aether_agent import health as _aether_health
                _a = _aether_health()
                return self._json(200 if _a.get("reachable") else 503, _a)

            # NEXUS Master Home — HermesBrain Status (integrierte Denk-/Ausführungs-Schleife)
            if path == "/api/hermes/brain":
                from nexus.integrations.hermes_brain import HermesBrain
                return self._json(200, HermesBrain(ENGINE.master).availability())
            # Auftrag 003: echte Hermes-Erreichbarkeit (CLI oneshot, lokal)
            if path == "/api/hermes/health":
                from nexus.integrations.hermes_agent import health as _hermes_health
                _h = _hermes_health()
                return self._json(200 if _h.get("reachable") else 503, _h)


            # Agent Bridge v1 (Bearer NEXUS_BRIDGE_TOKEN) — does not touch /api/status or /api/tasks
            # Controlled Automations LOCAL-ONLY (NOT on Funnel/Gateway allowlist; no Chat/Voice public)
            if automation_api is not None and automation_api.is_automation_get(path):
                return automation_api.handle_get(self, path, qs)

            if multimodal_api is not None and multimodal_api.is_multimodal_get(path):
                return multimodal_api.handle_get(self, path, qs, master=ENGINE.master)

            if bridge_api is not None and bridge_api.is_bridge_get(path):
                return bridge_api.handle_get(self, path, qs)

            if path == "/api/activity":
                lim = 100
                try:
                    lim = int((qs.get("limit") or ["100"])[0])
                except Exception:
                    lim = 100
                return self._json(200, get_activity_log().snapshot(limit=lim))

            self._json(404, {"success": False, "error": "not_found", "path": path})
        except _ClientDisconnected:
            raise
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"success": False, "error": str(e)})

    def _agent_guard(self) -> tuple[bool, str]:
        """Phase 1 (WP1): Agenten-Endpunkte nur Loopback oder explizit tokenisiert."""
        from nexus.common.agent_guard import allow
        addr = getattr(self, "client_address", None) or ("127.0.0.1", 0)
        ip = addr[0]
        ok, why = allow(ip, getattr(self, "headers", None) or {})
        if not ok:
            try:
                from nexus.terminal.guard import audit
                audit("agent_endpoint_denied", path=str(getattr(self, "path", ""))[:200],
                      ip=str(ip), reason=why)
            except Exception:
                pass
        return ok, why

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if self._cross_site_blocked():
            try:
                from nexus.terminal.guard import audit
                audit("cross_site_post_denied", path=path[:200], origin=str(self.headers.get("Origin"))[:200])
            except Exception:
                pass
            return self._json(403, {"success": False, "error": "CROSS_SITE_DENIED"})
        try:  # NEXUS OS live link evidence (memory only): last request per client IP
            from nexus import nexus_os as _nos
            _nos.note_client(self.client_address[0], path)
        except Exception:
            pass
        body = self._read_json()
        try:  # Live-Monitor (20261028): Agent-Auftraege sichtbar machen
            from nexus.common import live_events as _le
            if path == "/api/live/emit":
                if self.client_address[0] not in ("127.0.0.1", "::1"):
                    return self._json(403, {"success": False, "error": "loopback_only"})
                b = body if isinstance(body, dict) else {}
                rec = _le.emit(str(b.get("agent") or "Hermes-PS"), str(b.get("kind") or "info"), b.get("text") or "",
                               target=b.get("target"), ok=b.get("ok"), data=b.get("data"))
                return self._json(200, {"success": rec is not None, "event": rec})
            if path.startswith("/api/nexusall/"):  # NEXUS All (20261028)
                from nexus.nexusall.api import handle as _nxa
                if _nxa(self, "POST", path, {}, body):
                    return
            if path.startswith("/api/studio/"):
                return self._studio("POST", path, {}, body)
            if path.startswith("/api/terminal/"):  # NEXUS-Terminal (20261031), nur lokal
                if self.client_address[0] not in ("127.0.0.1", "::1") or self.headers.get("X-Forwarded-For"):
                    return self._json(403, {"success": False, "error": "LOOPBACK_ONLY"})
                from nexus.terminal import service as _term
                b = body if isinstance(body, dict) else {}
                if path == "/api/terminal/run":
                    _cmd = str(b.get("command") or "")
                    if not _cmd.strip():
                        return self._json(400, {"success": False, "error": "command required"})
                    # Rechtestufe wird NICHT aus dem Body gelesen (level/role werden ignoriert) - nur Terminal-Konfiguration
                    from nexus.safety import dispatch as _p2d  # Phase 2 Schritt 8 (NEXUS_PHASE2_REGISTRY)
                    _r = _p2d.shell_execute(_cmd, agent=str(b.get("agent") or "Eddie"), shell=str(b.get("shell") or "session"),
                                       cwd=b.get("cwd") or None, timeout=b.get("timeout"), wait=b.get("wait", False) is not False,
                                       approval_id=b.get("approval_id") or None, allow_empty=b.get("allow_empty") is True)
                    _code = {"BLOCKED": 403, "LEVEL_DENIED": 403, "APPROVAL_REQUIRED": 202}.get(str(_r.get("status")), 200)
                    return self._json(_code, _r)
                if path == "/api/terminal/zocker":
                    # Zocker-Modus: NEXUS abschalten. Skript per Env konfigurierbar; Default liegt neben
                    # dem Repo (auf WoZiPC: G:\meine-ki\zocker_bombe.ps1).
                    import subprocess as _sp
                    if os.name != "nt":
                        return self._json(501, {"success": False, "error": "WINDOWS_ONLY"})
                    _zs = Path(os.environ.get("NEXUS_ZOCKER_SCRIPT") or (Path(__file__).resolve().parents[3] / "zocker_bombe.ps1"))
                    if not _zs.is_file():
                        return self._json(404, {"success": False, "error": "ZOCKER_SCRIPT_MISSING"})
                    _sp.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(_zs)],
                              creationflags=0x08000000)
                    return self._json(200, {"success": True})
                if path == "/api/terminal/cancel":
                    return self._json(200, _term.cancel(str(b.get("run") or ""), by=str(b.get("by") or "Eddie-UI")))
                if path == "/api/terminal/approve":
                    from nexus.terminal import guard as _tg
                    # nur aus dem Browser-UI (same-origin fetch mit UI-Header), nicht per Agent-API
                    if (not _tg.check_ui_cookie(self.headers.get("Cookie")) or self.headers.get("X-NEXUS-UI") != "approve"
                            or self.headers.get("Sec-Fetch-Site") != "same-origin"):
                        _tg.audit("approval_rejected_not_ui", approval_id=str(b.get("approval_id")), ua=str(self.headers.get("User-Agent"))[:120])
                        return self._json(403, {"success": False, "error": "NUR_EDDIE_UI"})
                    return self._json(200, _tg.decide(str(b.get("approval_id") or ""), b.get("approve", True) is not False))
                if path == "/api/terminal/reset":
                    from nexus.terminal import guard as _tg
                    _tg.audit("session_reset", agent=str(b.get("agent") or "Eddie"))
                    return self._json(200, _term.reset_session(str(b.get("agent") or "Eddie")))
                return self._json(404, {"success": False, "error": "unknown"})
            if path == "/api/swarm/run":  # NEXUS Swarm AI (20261104), nur lokal
                if self.client_address[0] not in ("127.0.0.1", "::1") or self.headers.get("X-Forwarded-For"):
                    return self._json(403, {"success": False, "error": "LOOPBACK_ONLY"})
                from nexus.swarm.core import get_master as _gsm
                b = body if isinstance(body, dict) else {}
                if not str(b.get("task") or "").strip():
                    return self._json(400, {"success": False, "error": "task required"})
                # Agent/Rechtestufe kommt aus permissions.json; Body darf nur bekannte Agenten nennen
                _s = _gsm().submit(str(b["task"]), str(b.get("mode") or "pipeline"), str(b.get("agent") or "NEXUS"))
                return self._json(202, {"success": True, "run_id": _s["id"], "status": _s["status"], "mode": _s["mode"]})
            if path.startswith("/api/autobuild/"):  # Selbstbau-Engine (20261028), nur lokal
                if self.client_address[0] not in ("127.0.0.1", "::1") or self.headers.get("X-Forwarded-For"):
                    return self._json(403, {"success": False, "error": "LOOPBACK_ONLY"})
                from nexus.autobuild.loop import get_autobuild
                _ab = get_autobuild()
                b = body if isinstance(body, dict) else {}
                if path == "/api/autobuild/start":
                    return self._json(200, _ab.start())
                if path == "/api/autobuild/stop":
                    return self._json(200, _ab.stop())
                if path == "/api/autobuild/run":
                    if _ab.running_cycle:
                        return self._json(409, {"success": False, "error": "CYCLE_RUNNING"})
                    import threading as _th
                    _th.Thread(target=_ab.cycle, kwargs={"item_id": b.get("item"), "full_suite": b.get("full_suite", True) is not False},
                                     name="nexus-autobuild-run", daemon=True).start()
                    return self._json(202, {"success": True, "started": True})
                return self._json(404, {"success": False, "error": "unknown"})
            if path == "/api/live/open":
                return self._json(200, {"success": True, **_le.ensure_monitor(force=True)})
            if any(path.startswith(x) for x in _LIVE_POST):
                self._live_path = path
                _b = body if isinstance(body, dict) else {}
                _le.emit("NEXUS" if not path.startswith("/api/crew") else "NEXUS", "auftrag",
                         _b.get("task") or _b.get("message") or _b.get("text") or path,
                         target=_LIVE_POST[next(x for x in _LIVE_POST if path.startswith(x))], data={"endpoint": path})
        except Exception:
            pass
        try:  # 005D NEXUS OS link (device heartbeat / sync), network-restricted
            from nexus import nexus_os_link as _nol
            _r = _nol.handle("POST", path, self.client_address[0], self.headers, body=body, engine=ENGINE)
            if _r is not None:
                return self._json(_r[0], _r[1])
        except Exception:
            traceback.print_exc()
            if path in ("/api/nexus-os/device", "/api/nexus-os/sync", "/api/nexus-os/push", "/api/nexus-os/link", "/api/nexus-os/pair"):
                return self._json(503, {"success": False, "error": "NEXUS_OS_LINK_UNAVAILABLE"})

        # PC confirmation IDs never enter the legacy, broader WhatsApp yes/no flow.
        if path == "/api/voice/confirm" and isinstance(body, dict) and str(body.get("id") or "").startswith("pc-"):
            try:
                from nexus.pc_remote import voice_confirm
                _code, _payload = voice_confirm(body, self.client_address[0], self.headers, ENGINE)
                return self._json(_code, _payload)
            except Exception:
                return self._json(503, {"success": False, "error": "PC_COMMAND_SERVICE_UNAVAILABLE"})

        # Local-only maintenance control (Bearer + loopback enforced in bridge api).
        if bridge_api is not None and bridge_api.is_maintenance_path(path):
            try:
                return bridge_api.handle_maintenance(self, path, body)
            except _ClientDisconnected:
                raise
            except Exception as e:
                traceback.print_exc()
                return self._json(500, {"success": False, "error": str(e)})

        from nexus.maintenance import MaintenanceActive, block_payload, maintenance

        # Drain gate: stop accepting new orders; in-flight work keeps draining.
        if maintenance().is_draining():
            return self._json(503, block_payload())
        try:
            with maintenance().work("http_post"):
                self._do_post_dispatch(path, body)
        except MaintenanceActive:
            return self._json(503, block_payload())
        except _ClientDisconnected:
            raise
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"success": False, "error": str(e)})

    def _do_post_dispatch(self, path: str, body: dict) -> None:
        try:
            # Only Master Home requests explicitly opt into persistent partner turns.
            # Never interpret a Hermes CLI session_id as a NEXUS chat identifier.
            direct_partner_route = path in ("/api/hermes/agent", "/api/aether/agent") or (
                path == "/api/chat" and str(body.get("mode") or body.get("orchestrator") or "").strip().lower()
                in ("cloud", "openai", "openai_cloud", "eddybangg"))
            if direct_partner_route and "chat_session_id" in body:
                sid = body.get("chat_session_id")
                if not isinstance(sid, str) or sid not in ENGINE.sessions:
                    return self._json(404, {"success": False, "error": "CHAT_SESSION_NOT_FOUND"})
            if path in ("/api/ai/models", "/api/ai/config"):
                from nexus.common import ai_layer
                updates = {}
                for k in ("provider", "chat_model", "vision_model", "ollama_url", "keep_alive",
                          "unload_after_request", "never_pull", "on_demand_only", "memory_fallback", "notes"):
                    if k in body:
                        updates[k] = body.get(k)
                if body.get("model") and "chat_model" not in updates:
                    updates["chat_model"] = body.get("model")
                if updates.get("chat_model"):
                    updates["chat_model_source"] = "ui"
                cfg = ai_layer.save_config(updates) if updates else ai_layer.load_config()
                applied = ai_layer.apply_to_client(ENGINE.master.llm)
                return self._json(200, {"success": True, "ok": True, "config": cfg, "applied": applied, "status": ai_layer.status(ENGINE.master.llm)})

            if path == "/api/shared-room/message":
                sid = str(body.get("session_id") or ENGINE.session_id)
                requested_sender = str(body.get("sender_type") or body.get("sender") or "human").lower()
                if requested_sender not in ("human", "eddy"):
                    return self._json(403, {"success": False, "error": "SERVER_OWNED_AI_IDENTITY"})
                result = SHARED_ROOM.append(
                    session_id=sid,
                    sender_type=str(body.get("sender_type") or body.get("sender") or "human"),
                    sender_name=str(body.get("sender_name") or body.get("sender") or "Eddy"),
                    content=str(body.get("content") or body.get("message") or ""),
                    source="human_input",
                    target=body.get("target"), role=str(body.get("role") or "assistant"),
                    round_no=int(body.get("round") or 0), parent_message_id=body.get("parent_message_id") or body.get("reply_to"),
                    task_id=body.get("task_id"), approval_id=body.get("approval_id"), agent=body.get("agent"),
                    provider=body.get("provider"), metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
                    mode=str(body.get("mode") or "user"),
                )
                return self._json(200 if result.get("success") else 409, result)

            if path == "/api/chat":
                # 003A: /hermes all modus | /hermes normal (Core schaltet nie selbst)
                from nexus.integrations import hermes_allmode as _ham
                _hm_msg = str(body.get("message") or body.get("text") or "")
                _hm_cmd = _ham.match_command(_hm_msg)
                _hm_src = str(body.get("source") or "chat").lower()
                if _hm_cmd:
                    _hm_owner = self.client_address[0] in ("127.0.0.1", "::1") and not any(self.headers.get(h) for h in ("X-Forwarded-For", "Tailscale-User-Login", "X-Forwarded-Host", "Forwarded"))
                    _hm_r = _ham.request_start(source=_hm_src, owner=_hm_owner, query=(str(body.get("query")).strip() if body.get("query") else _ham.split_start(_hm_msg)), in_dir=body.get("in_dir")) if _hm_cmd == "start" else _ham.request_end(source=_hm_src)
                    _hm_r.setdefault("session_id", body.get("session_id") or ENGINE.session_id)
                    return self._json(200, _hm_r)
                if _ham.status().get("state") == "ACTIVE" and _hm_msg.strip().lower().startswith(("hermes:", "@hermes")) and _ham.needs_separate_confirmation(_hm_msg):
                    _ham._log("separate_confirmation_required", task=_hm_msg[:300])
                    return self._json(200, {"success": False, "error": "SEPARATE_CONFIRMATION_REQUIRED", "reply": "Ordner loeschen/irreversible Schritte brauchen eine gesonderte Bestaetigung ueber den Admin-Dienst. Nichts ausgefuehrt.", "session_id": body.get("session_id") or ENGINE.session_id})
                mode = (body.get("mode") or body.get("orchestrator") or "").strip().lower()
                if mode in ("cloud", "openai", "openai_cloud", "eddybangg"):
                    from nexus.master.openai_orchestrator import orchestrate as _openai_orch
                    msg = body.get("message") or body.get("text") or ""
                    r = _openai_orch(str(msg))
                    out = dict(r) if isinstance(r, dict) else {"success": True, "reply": str(r)}
                    out.setdefault("session_id", body.get("session_id") or ENGINE.session_id)
                    return self._json(200, self._partner_response(body, out, "gpt"))
                msg = body.get("message") or body.get("text") or ""
                return self._json(200, ENGINE.chat(msg, body.get("session_id"), body.get("project_id"), model=body.get("model")))

            # AETHER — lokaler Agent, festes vorhandenes Ollama-Modell; keine Ersatzantwort.
            if path == "/api/aether/supervisor":  # 2026-11-02 Supervisor-MVP
                _ag = self._agent_guard()
                if not _ag[0]:
                    return self._json(403, {"success": False, "error": _ag[1]})
                from nexus.integrations.aether_agent import supervise as _aether_sv
                _sp = bool(body.get("special_order"))
                if _sp and not (self.client_address[0] in ("127.0.0.1", "::1") and not any(self.headers.get(h) for h in ("X-Forwarded-For", "Tailscale-User-Login", "X-Forwarded-Host", "Forwarded"))):
                    return self._json(403, {"success": False, "error": "SPECIAL_ORDER_LOCAL_OWNER_ONLY"})
                _a = _aether_sv(str(body.get("task") or body.get("message") or ""), body.get("approval_id"), _sp)
                return self._json(200 if _a.get("success") else 503, self._partner_response(body, _a, "aether"))
            if path == "/api/aether/agent":
                _ag = self._agent_guard()
                if not _ag[0]:
                    return self._json(403, {"success": False, "error": _ag[1]})
                from nexus.integrations.aether_agent import ask as _aether_ask
                _a = _aether_ask(str(body.get("message") or body.get("task") or body.get("text") or ""))
                return self._json(200 if _a.get("success") else 503, self._partner_response(body, _a, "aether"))

            # NEXUS Master Home — HermesBrain Auftrag (intern: Denken→Planen→Ausführen→Prüfen→Korrigieren)
            # Auftrag 003: Auftrag an Hermes Agent (lokal, Toolset file, Timeout 180 s)
            # t329u: NEXUS GPT lokal (Ollama, vorhandene Modelle, kein Pull, kein Cloud-Zwang)
            if path == "/api/gpt/tool":  # NEXUS GPT Werkzeug ueber NEXUS-Terminal (Rechtestufen, Erfolgspruefung)
                _ag = self._agent_guard()
                if not _ag[0]:
                    return self._json(403, {"success": False, "error": _ag[1]})
                from nexus.integrations import gpt_local_agent as _gla
                if str(body.get("command") or "").strip():
                    _gt = _gla.run_tool(str(body.get("command")), shell=str(body.get("shell") or "session"))
                else:
                    _gt = _gla.tool_ask(str(body.get("task") or body.get("message") or ""))
                return self._json(200, _gt)
            if path == "/api/gpt/agent":
                _ag = self._agent_guard()
                if not _ag[0]:
                    return self._json(403, {"success": False, "error": _ag[1]})
                from nexus.integrations.gpt_hermes_bridge import ask_with_hermes
                _g = ask_with_hermes(
                    str(body.get("message") or body.get("task") or body.get("text") or "")
                )
                _status = (
                    200 if _g.get("success")
                    else 409 if _g.get("approval_required")
                    else 503
                )
                return self._json(_status, self._partner_response(body, _g, "gpt"))
            if path == "/api/hermes/all/confirm":  # 003A: nur Nonce pruefen + Anfrage markieren
                from nexus.integrations import hermes_allmode as _ham
                if self.client_address[0] not in ("127.0.0.1", "::1") or self.headers.get("X-Forwarded-For") or self.headers.get("Tailscale-User-Login"):
                    _ham._log("confirm_rejected", reason="NOT_LOCAL")
                    return self._json(403, {"accepted": False, "error": "NONCE_INVALID"})
                _cr = _ham.confirm(body)
                return self._json(200 if _cr.get("accepted") else 409, _cr)
            if path == "/api/hermes/agent":
                _ag = self._agent_guard()
                if not _ag[0]:
                    return self._json(403, {"success": False, "error": _ag[1]})
                from nexus.integrations.hermes_agent import ask as _hermes_ask
                _r = _hermes_ask(str(body.get("task") or body.get("message") or ""))
                return self._json(200 if _r.get("success") else 503, self._partner_response(body, _r, "hermes"))
            if path == "/api/hermes/brain":
                _ag = self._agent_guard()
                if not _ag[0]:
                    return self._json(403, {"success": False, "error": _ag[1]})
                from nexus.integrations.hermes_brain import HermesBrain
                task = body.get("task") or body.get("message") or body.get("text") or ""
                if not str(task).strip():
                    return self._json(400, {
                        "success": False,
                        "error": "task_required",
                        "reply": "Feld 'task' ist erforderlich.",
                        "source": "hermes_brain_api",
                    })
                brain = HermesBrain(ENGINE.master)
                result = brain.run(str(task), session_messages=body.get("history"), requirements=body.get("requirements"))
                code = 200 if result.get("success") else (207 if result.get("status") in ("PARTIAL", "BLOCKED") else 200)
                out = dict(result)
                out["session_id"] = body.get("session_id") or ENGINE.session_id
                out["source"] = "hermes_brain_api"
                return self._json(code, out)

            # Dünner Alias für externe Assistenten (ChatGPT Custom GPT / Eddie PowerShell)
            if path == "/api/assistant":
                if not self._assistant_authorized():
                    return self._reject_assistant_unauthorized()
                msg = body.get("message") or body.get("text") or ""
                msg = str(msg).strip() if msg is not None else ""
                if not msg:
                    return self._json(400, {
                        "success": False,
                        "error": "message_required",
                        "reply": "",
                        "source": "assistant_api",
                    })
                req_source = body.get("source") or "assistant_api"
                # Handshake: Marker in der Message → zuverlässig NEXUS_API_OK zurück
                # (ohne die normale Chat-Pipeline für übrige Calls zu umgehen)
                if "NEXUS_API_OK" in msg:
                    return self._json(200, {
                        "success": True,
                        "reply": "NEXUS_API_OK - Nexus Assistant-API erreichbar.",
                        "source": "assistant_api",
                        "session_id": body.get("session_id") or ENGINE.session_id,
                        "request_source": req_source,
                    })
                result = ENGINE.chat(msg, body.get("session_id"), body.get("project_id"), model=body.get("model"))
                out = dict(result) if isinstance(result, dict) else {"success": True, "reply": str(result)}
                out["source"] = "assistant_api"
                if req_source and req_source != "assistant_api":
                    out["request_source"] = req_source
                return self._json(200, out)

            if path == "/api/chat/stream":
                return self._chat_stream(body)

            if path == "/api/voice/chat":
                msg = body.get("message") or body.get("text") or ""
                r = ENGINE.chat(msg, body.get("session_id"), body.get("project_id"), model=body.get("model"))
                r["tts_url"] = "/api/voice/tts"
                return self._json(200, r)

            # NEXUSPhone KI-Anruf: ein Gesprächszug (Sprache -> NEXUS oder Hermes -> Sprache).
            if path == "/api/voice/call/start":
                from nexus.voice import call as call_mod
                code, out = call_mod.warm(ENGINE, body)
                return self._json(code, out)
            if path == "/api/voice/call/turn":
                from nexus.voice import call as call_mod
                code, out = call_mod.turn(ENGINE, body)
                return self._json(code, out)
            if path == "/api/voice/call/next":
                from nexus.voice import call as call_mod
                code, out = call_mod.next_segment(body)
                return self._json(code, out)
            if path == "/api/voice/call/action":
                from nexus.voice import call as call_mod
                code, out = call_mod.action(body)
                return self._json(code, out)
            if path == "/api/voice/call/end":
                from nexus.voice import call as call_mod
                code, out = call_mod.end(body, ENGINE)
                return self._json(code, out)

            if path == "/api/voice/turn":
                # STT → chat → optional TTS (Amala). JSON in/out, no fake PASS.
                from nexus.voice import stt as stt_mod
                import base64 as _b64
                b64 = body.get("audio_b64") or body.get("audio") or ""
                if isinstance(b64, str) and "," in b64:
                    b64 = b64.split(",", 1)[1]
                raw = b""
                if b64:
                    try:
                        raw = _b64.b64decode(b64)
                    except Exception:
                        return self._json(400, {"success": False, "error": "bad_audio_b64"})
                text_in = (body.get("message") or body.get("text") or "").strip()
                stt_info = None
                if raw:
                    stt_info = stt_mod.transcribe_bytes(
                        raw,
                        filename=body.get("filename") or "audio.webm",
                        language=body.get("language") or "de",
                        model_size=body.get("model") or "tiny",
                    )
                    if not stt_info.get("success"):
                        return self._json(200, {
                            "success": False,
                            "error": stt_info.get("error") or "stt_failed",
                            "reply": stt_info.get("reply") or "STT fehlgeschlagen.",
                            "stt": stt_info,
                        })
                    text_in = (stt_info.get("text") or "").strip()
                if not text_in:
                    return self._json(200, {
                        "success": False,
                        "error": "empty_transcript",
                        "reply": "Kein Text erkannt — bitte erneut sprechen.",
                        "stt": stt_info,
                    })
                from nexus.voice.commands import voice_priv_block as _vpb  # nexus_voice_priv_guard
                _blk = _vpb(text_in)
                if _blk:
                    _blk["text"] = text_in
                    _blk["stt"] = stt_info
                    return self._json(200, _blk)
                chat = ENGINE.chat(text_in, body.get("session_id"), body.get("project_id"), model=body.get("model"))
                out = {
                    "success": bool(chat.get("success", True)),
                    "text": text_in,
                    "reply": chat.get("reply") or "",
                    "intent": chat.get("intent"),
                    "model": chat.get("model"),
                    "session_id": chat.get("session_id"),
                    "error": chat.get("error"),
                    "data": chat.get("data"),
                    **response_identity(chat),
                    "stt": stt_info,
                    "tts_url": "/api/voice/tts",
                }
                want_speak = body.get("speak", True)
                if want_speak and out["reply"]:
                    tts_r = ENGINE.master.voice.tts(out["reply"][:1800])
                    if tts_r.get("success") and tts_r.get("path"):
                        mp3 = Path(tts_r["path"]).read_bytes()
                        out["tts_b64"] = _b64.b64encode(mp3).decode("ascii")
                        out["tts_bytes"] = len(mp3)
                        out["tts_voice"] = tts_r.get("voice")
                        # This route uses the configured default synthesis profile;
                        # response partner identity does not imply a partner voice.
                        out["tts_profile"] = "configured_default"
                        for key in ("mode", "rate", "pitch", "volume"):
                            if tts_r.get(key) is not None:
                                out["tts_" + key] = tts_r[key]
                    else:
                        out["tts_error"] = tts_r.get("error") or "tts_failed"
                return self._json(200, out)

            # Sessions
            if path == "/api/sessions/new":
                return self._json(200, ENGINE.new_session(body.get("title", "Neuer Chat")))
            if path == "/api/sessions/rename":
                return self._json(200, ENGINE.rename_session(body.get("id") or body.get("session_id"), body.get("title", "")))
            if path == "/api/sessions/delete":
                return self._json(200, ENGINE.delete_session(body.get("id") or body.get("session_id")))
            if path == "/api/sessions/switch":
                return self._json(200, ENGINE.switch_session(body.get("id") or body.get("session_id")))
            if path == "/api/sessions/export":
                return self._json(200, ENGINE.export_session(body.get("id")))
            if path == "/api/sessions/summarize":
                return self._json(200, ENGINE.summarize_session(body.get("id")))

            # Projects
            if path == "/api/projects":
                return self._json(200, ENGINE.master.projects.create(body.get("name", "Projekt"), body.get("description", "")))
            if path == "/api/projects/update":
                return self._json(200, ENGINE.master.projects.update(body.get("id"), **{k: body.get(k) for k in ("name", "description", "notes", "files")}))
            if path == "/api/projects/delete":
                return self._json(200, ENGINE.master.projects.delete(body.get("id")))
            if path == "/api/projects/active":
                return self._json(200, ENGINE.master.projects.set_active(body.get("id")))

            # Tasks
            if path == "/api/tasks":
                return self._json(200, ENGINE.master.tasks.create(body.get("title", "Task"), body.get("plan"), body.get("status", "Active")))
            if path == "/api/tasks/update":
                return self._json(200, ENGINE.master.tasks.update(body.get("id"), **{k: body.get(k) for k in ("title", "status", "plan", "result")}))

            # NEXUS Phone 002: Kontakte/Notizen/Kalender/Timer/Geraet (nexus/phone_api.py)
            if path.split("/")[2:3] and path.split("/")[2] in ("contacts", "notes", "calendar", "timer", "device"):
                from nexus import phone_api as _phone_api
                _pr = _phone_api.handle_post(path, body)
                if _pr is not None:
                    return self._json(_pr[0], _pr[1])
            # Memory
            if path == "/api/memory/correct":
                return self._json(200, ENGINE.master.memory.correct(body.get("id"), body.get("text") or ""))

            if path == "/api/memory/delete":  # 12:03: nur lokal, Archiv vor dem Loeschen
                if self.client_address[0] not in ("127.0.0.1", "::1") or self.headers.get("X-Forwarded-For") or self.headers.get("Tailscale-User-Login"):
                    return self._json(403, {"success": False, "error": "LOCAL_ONLY"})
                _md = ENGINE.master.memory.delete(body.get("id"))
                return self._json(200 if _md.get("success") else (404 if _md.get("error") == "NOT_FOUND" else 500), _md)
            if path == "/api/memory/merk":
                return self._json(200, ENGINE.master.memory.merk(body.get("text") or body.get("message", "")))
            if path == "/api/corrections":
                return self._json(200, ENGINE.master.corrections.add(
                    body.get("text") or body.get("message") or "",
                    raw=body.get("raw"),
                    kind=body.get("kind"),
                ))
            if path == "/api/memory/recall":
                return self._json(200, ENGINE.master.memory.recall(body.get("query") or body.get("q", "")))

            # Files
            if path == "/api/files/read":
                return self._json(200, ENGINE.master.files.read(body.get("path", "")))
            if path == "/api/files/write":
                return self._json(200, ENGINE.master.files.write(body.get("path", ""), body.get("content", "")))

            # Code
            if path == "/api/code/run":
                return self._json(200, ENGINE.master.code.run(body.get("code") or body.get("source", "")))

            # Shell / PowerShell (echt) — timeout/cwd/build_mode für npm/vite
            if path == "/api/shell/run":
                cmd = body.get("command") or body.get("cmd") or ""
                sh = body.get("shell") or "auto"
                to = body.get("timeout")
                cwd = body.get("cwd")
                build_mode = bool(body.get("build") or body.get("build_mode"))
                return self._json(
                    200,
                    ENGINE.master.shell.run(
                        cmd, shell=sh, timeout=to, cwd=cwd, build_mode=build_mode
                    ),
                )
            if path == "/api/shell/run_script":
                script = body.get("path") or body.get("script") or ""
                args = body.get("args")
                to = body.get("timeout")
                cwd = body.get("cwd")
                return self._json(
                    200,
                    ENGINE.master.shell.run_script(script, args=args, timeout=to, cwd=cwd),
                )

            # Knowledge
            if path == "/api/knowledge/search":
                return self._json(200, ENGINE.master.research.search(body.get("query") or body.get("q", "")))

            # Media
            if path == "/api/media/play_at":
                return self._json(200, ENGINE.master.media.play_at(int(body.get("index") or 0)))
            if path == "/api/media/play":
                return self._json(200, ENGINE.master.media.play())
            if path == "/api/media/resume":
                return self._json(200, ENGINE.master.media.resume())
            if path == "/api/media/pause":
                return self._json(200, ENGINE.master.media.pause())
            if path == "/api/media/next":
                return self._json(200, ENGINE.master.media.next())
            if path == "/api/media/prev":
                return self._json(200, ENGINE.master.media.prev())
            if path == "/api/media/stop":
                return self._json(200, ENGINE.master.media.stop())

            # Streams
            if path == "/api/streams":
                return self._json(200, ENGINE.master.streams.add(body.get("url", ""), body.get("title")))
            if path == "/api/streams/remove":
                return self._json(200, ENGINE.master.streams.remove(body.get("id") or body.get("stream_id")))
            if path == "/api/streams/active":
                return self._json(200, ENGINE.master.streams.set_active(body.get("id") or body.get("stream_id")))
            if path == "/api/streams/comment":
                sid = body.get("id") or body.get("stream_id") or ENGINE.master.streams.active_id
                comment = body.get("comment") or body.get("text") or ""
                return self._json(200, ENGINE.master.streams.set_comment(sid, comment))

            # Downloads
            if path == "/api/downloads":
                return self._json(200, ENGINE.master.downloads.enqueue(body.get("url", "")))

            # Voice TTS
            # heynexus-20261006: Sprachbefehle + Bestaetigung + Weckwort-Steuerung (additiv)
            if path in ("/api/voice/command", "/api/voice/confirm", "/api/voice/wakeword/config",
                        "/api/voice/wakeword/heartbeat", "/api/voice/live_state"):
                from nexus.voice import commands as _vc
                _code, _payload = _vc.handle_post(path, body, ENGINE)
                return self._json(_code, _payload)

            if path == "/api/voice/speak_pc":
                # Default: warten bis gesprochen — bool(None) waere False
                w = body.get("wait")
                wait = True if w is None else bool(w)
                return self._json(200, ENGINE.master.voice.speak_pc(body.get("text", "") or "", wait=wait))

            if path == "/api/voice/tts":
                try:  # heynexus-20261006: Weckwort-Dienst pausiert, solange NEXUS spricht
                    from nexus.voice import commands as _vc
                    _vc.note_tts(body.get("text", "") or "", body.get("source"))
                except Exception:
                    pass
                # Partnerbezogene Edge-TTS-Optionen; bestehender Endpoint und VoiceManager bleiben unverändert.
                _tts_opts = {}
                if body.get("voice"):
                    _tts_opts["voice"] = str(body.get("voice"))
                if isinstance(body.get("voices"), list):
                    _tts_opts["voices"] = [str(v) for v in body.get("voices")[:3] if v]
                for _k in ("rate", "pitch", "volume"):
                    if body.get(_k) is not None:
                        _tts_opts[_k] = str(body.get(_k))
                r = ENGINE.master.voice.tts(body.get("text", ""), **_tts_opts)
                if r.get("success") and r.get("path"):
                    mp3 = Path(r["path"]).read_bytes()
                    return self._bytes(200, mp3, "audio/mpeg")
                return self._json(200, r)

            # 008-P04/P10: Ziel-Planer, Wiederaufnahme, Skill-Selbstbau
            if path == "/api/autonomy/goal":
                from nexus.tasks import goal_planner as _gp
                return self._json(200, _gp.run_goal(body.get("goal"), dry_run=bool(body.get("dry_run"))))
            if path == "/api/autonomy/resume":
                from nexus.tasks import goal_planner as _gp
                return self._json(200, _gp.resume_open())
            if path == "/api/skills/user/draft":
                from nexus.skills import registry as _sk
                return self._json(200, _sk.draft_user_skill(body.get("name"), body.get("description") or "", body.get("test_params")))
            if path == "/api/skills/user/approve":
                from nexus.skills import registry as _sk
                return self._json(200, _sk.approve_user_skill(body.get("name"), body.get("approve") is True))
            # 008-P10: selbst erstellte Skills (nur SAFE-Tasks via Orchestrator)
            if path in ("/api/skills/user/create", "/api/skills/user/run", "/api/skills/user/list"):
                from nexus.skills import registry as _sk
                if path.endswith("/create"):
                    return self._json(200, _sk.create_user_skill(body.get("name"), body.get("steps") or [], body.get("description") or "", body.get("author") or "api"))
                if path.endswith("/run"):
                    return self._json(200, _sk.run_user_skill(body.get("name"), body.get("params") or {}))
                return self._json(200, {"success": True, "skills": _sk.list_user_skills()})

            # 008-P06: lokale Sprechererkennung (additiv, offline)
            if path in ("/api/voice/speaker/enroll", "/api/voice/speaker/identify", "/api/voice/speaker/status"):
                from nexus.voice import speaker_id as _spk
                if path.endswith("/status"):
                    return self._json(200, _spk.status())
                def _dec(v):
                    v = str(v or "")
                    return base64.b64decode(v.split(",", 1)[1] if "," in v else v)
                try:
                    if path.endswith("/enroll"):
                        samples = [_dec(x) for x in (body.get("samples_b64") or [body.get("audio_b64")]) if x]
                        return self._json(200, _spk.enroll(body.get("name"), samples))
                    return self._json(200, _spk.identify(_dec(body.get("audio_b64"))))
                except Exception as exc:
                    return self._json(400, {"success": False, "error": str(exc)})
            if path == "/api/voice/stt":
                from nexus.voice import stt as stt_mod
                # JSON: {audio_b64|audio, filename?, language?, model?}
                b64 = body.get("audio_b64") or body.get("audio") or ""
                if isinstance(b64, str) and "," in b64:
                    b64 = b64.split(",", 1)[1]
                raw = b""
                if b64:
                    try:
                        raw = base64.b64decode(b64)
                    except Exception:
                        return self._json(400, {"success": False, "error": "bad_audio_b64"})
                if not raw:
                    # raw body fallback already consumed as JSON — empty
                    return self._json(400, {"success": False, "error": "empty_audio", "hint": "POST JSON {audio_b64, filename?, language?}"})
                return self._json(
                    200,
                    stt_mod.transcribe_bytes(
                        raw,
                        filename=body.get("filename") or "audio.wav",
                        language=body.get("language") or "de",
                        model_size=body.get("model") or "tiny",
                    ),
                )

            # Apps connect
            if path == "/api/crew/active":
                return self._json(200, ENGINE.master.crew.set_active(body.get("id") or body.get("tag") or ""))
            if path == "/api/crew/run":
                aid = body.get("id") or body.get("tag") or body.get("agent_id") or ""
                msg = body.get("message") or body.get("text") or ""
                sid = body.get("session_id") or ENGINE.session_id
                sess = ENGINE.get_session(sid)
                messages = sess.get("messages") or []
                hist = [
                    {"role": m.get("role"), "content": m.get("content", "")}
                    for m in messages
                    if m.get("role") in ("user", "assistant", "system")
                ]
                r = ENGINE.master.crew.run(ENGINE.master, aid, msg, hist)
                return self._json(200, r)
            if path == "/api/apps/connect":
                return self._json(200, ENGINE.master.apps.connect(body.get("id") or body.get("app_id") or ""))
            if path == "/api/apps/disconnect":
                return self._json(200, ENGINE.master.apps.disconnect(body.get("id") or body.get("app_id") or ""))
            if path == "/api/apps/open":
                # Echt oeffnen (native/URL), nicht nur Metadaten
                # Request-Shape: id | app_id | name | app (Katalog-id, launch-key oder Anzeigename)
                aid = (
                    body.get("id")
                    or body.get("app_id")
                    or body.get("name")
                    or body.get("app")
                    or ""
                )
                if body.get("info_only"):
                    return self._json(200, ENGINE.master.apps.open_info(aid))
                return self._json(200, ENGINE.master.apps.open(aid))

            # OS-Aktionen (Website / Datei / App / Musik)
            if path == "/api/os/open_url":
                return self._json(200, ENGINE.master.os_actions.open_url(body.get("url") or ""))
            if path == "/api/os/open_path":
                return self._json(
                    200,
                    ENGINE.master.os_actions.open_path(
                        body.get("path") or "",
                        reveal=bool(body.get("reveal") or body.get("folder")),
                    ),
                )
            if path == "/api/os/launch_app":
                return self._json(
                    200,
                    ENGINE.master.os_actions.launch_app(body.get("name") or body.get("app") or ""),
                )
            if path == "/api/os/play_media":
                return self._json(
                    200,
                    ENGINE.master.os_actions.play_media(body.get("path") or None),
                )
            if path == "/api/os/list_dir":
                return self._json(
                    200,
                    ENGINE.master.os_actions.list_dir_os(body.get("path") or ""),
                )

            if path == "/api/os/close_app":
                return self._json(
                    200,
                    ENGINE.master.os_actions.close_app(body.get("name") or body.get("app") or ""),
                )
            if path == "/api/os/close_pid":
                return self._json(
                    200,
                    ENGINE.master.os_actions.close_pid(body.get("pid")),
                )
            if path in ("/api/os/close_opened", "/api/os/close_url"):
                return self._json(
                    200,
                    ENGINE.master.os_actions.close_opened(body.get("url") or None),
                )

            # Videos resolve URL for in-app player
            if path == "/api/videos/resolve":
                from nexus.media.video_library import VideoLibrary
                return self._json(200, VideoLibrary().resolve_url(body.get("url") or ""))



            # OpenAI cloud orchestrator (Responses API + local Agent Bridge)
            if path == "/api/orchestrate":
                from nexus.master.openai_orchestrator import orchestrate as _openai_orch
                msg = body.get("message") or body.get("text") or body.get("prompt") or ""
                return self._json(200, _openai_orch(str(msg)))

            # Agent Bridge v1 (Bearer NEXUS_BRIDGE_TOKEN) — /api/task*, /api/message; not /api/assistant
            # Controlled Automations LOCAL-ONLY (NOT on Funnel allowlist)
            if automation_api is not None and automation_api.is_automation_post(path):
                return automation_api.handle_post(self, path, body)
            if multimodal_api is not None and multimodal_api.is_multimodal_post(path):
                return multimodal_api.handle_post(self, path, body, master=ENGINE.master)


            if bridge_api is not None and bridge_api.is_bridge_post(path):
                return bridge_api.handle_post(self, path, body)

            # Vision / Screen analyze
            if path in ("/api/vision", "/api/screen/analyze"):
                return self._vision_analyze(body, save_screen=(path == "/api/screen/analyze"))

            self._json(404, {"success": False, "error": "not_found", "path": path})
        except _ClientDisconnected:
            raise
        except Exception as e:
            traceback.print_exc()
            self._json(500, {"success": False, "error": str(e)})

    def _partner_response(self, body: dict, result: dict, partner: str) -> dict:
        if "chat_session_id" in body:
            message = str((body.get("task") if partner == "hermes" else None)
                          or body.get("message") or body.get("task") or body.get("text") or "")
            return ENGINE.record_partner_turn(message, result, body["chat_session_id"], partner)
        return {**result, **response_identity(result, partner)}

    def _chat_stream(self, body: dict) -> None:
        msg = body.get("message") or body.get("text") or ""
        self._sse_start()
        # 2026-10-28 CHAT04: Neu generieren / Bearbeiten; Generator bei Abbruch sofort schliessen (beendet Ollama-Request)
        _rw = "regenerate" if body.get("regenerate") else ("edit" if body.get("edit") else None)
        _gen = ENGINE.chat_stream(msg, body.get("session_id"), body.get("project_id"), model=body.get("model"), **({"rewrite": _rw} if _rw else {}))
        try:
            for ev in _gen:
                if ev.get("type") == "token":
                    self._sse_event({"type": "token", "text": ev.get("text", "")})
                elif ev.get("type") == "done":
                    self._sse_event({
                        "type": "done",
                        "reply": ev.get("reply", ""),
                        "session_id": ev.get("session_id"),
                        "model": ev.get("model"),
                        "intent": ev.get("intent"),
                        "success": ev.get("success", True),
                        "error": ev.get("error"),
                        "data": ev.get("data"),
                        "plan": ev.get("plan"),
                        **{key: ev[key] for key in IDENTITY_FIELDS if key in ev},
                    })
            self._sse_event("[DONE]")
        except _ClientDisconnected:
            self.close_connection = True
            try:
                _gen.close()
            except Exception:
                pass
        except Exception as e:
            traceback.print_exc()
            try:
                self._sse_event({
                    "type": "done",
                    "reply": f"Stream-Fehler: {e}",
                    "session_id": ENGINE.session_id,
                    "model": None,
                    "success": False,
                    "error": str(e),
                })
                self._sse_event("[DONE]")
            except Exception:
                pass

    def _vision_analyze(self, body: dict, save_screen: bool = False) -> None:
        b64 = body.get("image") or body.get("image_b64") or ""
        if "," in b64:
            b64 = b64.split(",", 1)[1]
        try:
            img = base64.b64decode(b64)
        except Exception:
            return self._json(400, {"success": False, "error": "bad_image"})
        if not img:
            return self._json(400, {"success": False, "error": "empty_image"})
        if save_screen or body.get("save"):
            ensure_dirs()
            SCREENS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fp = SCREENS_DIR / f"frame_{ts}.jpg"
            try:
                fp.write_bytes(img)
            except Exception:
                pass
            ENGINE.master.last_screen_frame = img
        prompt = body.get("prompt") or "Beschreibe das Bild auf Deutsch."
        # Phase 10: route through multimodal image facade (resolve_vision_model + keep_alive=0)
        try:
            from nexus.multimodal.image import analyze as mm_analyze
            result = mm_analyze(ENGINE.master, img, prompt)
        except Exception:
            result = ENGINE.master.llm.vision(prompt, img)
        return self._json(200, result)

    def _serve_static(self, rel: str) -> None:
        fp = (STATIC_DIR / rel).resolve()
        if not str(fp).startswith(str(STATIC_DIR.resolve())) or not fp.is_file():
            return self._json(404, {"success": False, "error": "not found"})
        ctype = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        if fp.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif fp.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif fp.name.endswith(".webmanifest") or fp.suffix == ".webmanifest":
            ctype = "application/manifest+json; charset=utf-8"
        headers_extra = {}
        if fp.name == "sw.js":
            headers_extra["Service-Worker-Allowed"] = "/"
            headers_extra["Cache-Control"] = "no-cache"
        if headers_extra:
            data = fp.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers_extra.items():
                self.send_header(k, v)
            self._cors()
            self.end_headers()
            self._write_response_data(data)
            return
        self._bytes(200, fp.read_bytes(), ctype)

    def _serve_file(self, root: Path, rel: str) -> None:
        root = root.resolve()
        fp = (root / rel).resolve()
        if not str(fp).startswith(str(root)) or not fp.is_file():
            return self._json(404, {"success": False, "error": "not found"})
        ctype = mimetypes.guess_type(str(fp))[0] or "application/octet-stream"
        self._bytes(200, fp.read_bytes(), ctype)


    def _studio(self, method, path, qs, body) -> None:
        from nexus.studio.api import handle as _sh
        code, payload = _sh(method, path, qs, body, self.client_address[0])
        if "__file__" in payload:
            fp = payload["__file__"]
            if not fp.is_file():
                return self._json(404, {"success": False, "error": "NOT_FOUND"})
            return self._bytes(200, fp.read_bytes(), payload["ctype"])
        return self._json(code, payload)

    def _live_stream(self, qs, fp=None) -> None:
        """SSE: tailt workspace/audit/live_events.jsonl; id = Byte-Offset (Reconnect ohne Duplikate)."""
        import time as _t
        from nexus.common import live_events as _le
        fp = fp or _le.LIVE_FILE
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self._cors()
        self.end_headers()
        try:
            size = fp.stat().st_size if fp.exists() else 0
            last = (self.headers.get("Last-Event-ID") or "").strip()
            if last.isdigit() and int(last) <= size:
                pos = int(last)
            else:
                n = max(0, min(int((qs.get("backlog") or ["150"])[0]), 1000))
                pos = size
                if n and size:
                    with fp.open("rb") as f:
                        f.seek(max(0, size - 400_000))
                        chunk = f.read()
                    lines = chunk.splitlines(keepends=True)[-n:]
                    pos = size - sum(len(x) for x in lines)
            idle = 0.0
            while True:
                try:
                    size = fp.stat().st_size
                except FileNotFoundError:
                    size = 0
                if size < pos:
                    pos = 0  # rotiert
                if size > pos:
                    with fp.open("rb") as f:
                        f.seek(pos)
                        chunk = f.read(size - pos)
                    end = chunk.rfind(b"\n") + 1
                    out = b""
                    for ln in chunk[:end].splitlines(keepends=True):
                        pos += len(ln)
                        if ln.strip():
                            out += b"id: " + str(pos).encode() + b"\ndata: " + ln.strip() + b"\n\n"
                    if out:
                        self.wfile.write(out)
                        self.wfile.flush()
                        idle = 0.0
                else:
                    idle += 0.5
                    if idle >= 15:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        idle = 0.0
                _t.sleep(0.5)
        except (BrokenPipeError, ConnectionError, OSError):
            return


_LIVE_POST = {"/api/hermes": "Hermes", "/api/crew": "Crew", "/api/aether": "Hermes", "/api/chat": "NEXUS", "/api/gpt": "NEXUS-GPT"}


def main() -> None:
    import ssl
    import threading
    import time
    from nexus.common.paths import ROOT

    ensure_dirs()
    host = Config.HOST
    port = Config.PORT
    ssl_port = Config.SSL_PORT
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"NEXUS AI Master {Config.VERSION} on http://{host}:{port}/", flush=True)
    print(f"UI: http://127.0.0.1:{port}/  Mobile: http://127.0.0.1:{port}/m", flush=True)

    httpsd = None
    cert = Path(Config.SSL_CERT)
    key = Path(Config.SSL_KEY)
    if not cert.is_absolute():
        cert = (ROOT / cert).resolve()
    if not key.is_absolute():
        key = (ROOT / key).resolve()
    if cert.is_file() and key.is_file():
        try:
            httpsd = ThreadingHTTPServer((host, ssl_port), Handler)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(cert), str(key))
            httpsd.socket = ctx.wrap_socket(httpsd.socket, server_side=True)
            t = threading.Thread(target=httpsd.serve_forever, name="nexus-https", daemon=True)
            t.start()
            print(f"HTTPS: https://127.0.0.1:{ssl_port}/m  (cert={cert})", flush=True)
            print(f"LAN Handy: https://<PC-IP>:{ssl_port}/m", flush=True)
        except OSError as e:
            print(f"HTTPS bind failed on :{ssl_port}: {e}", flush=True)
            httpsd = None
        except Exception as e:
            print(f"HTTPS setup failed: {e}", flush=True)
            httpsd = None
    else:
        print(f"HTTPS skipped (missing cert/key: {cert} / {key})", flush=True)

    # NEXUS 005D: ADB-Reconnect-Watchdog (Thread, 30 s, nur wenn kein Handy OK; NEXUS_ADB_WATCHDOG=0 schaltet ab)
    if os.environ.get("NEXUS_ADB_WATCHDOG", "1") != "0":
        try:
            from nexus import phone_actions as _pa_wd
            _pa_wd.start_watchdog(30.0)
        except Exception as e:
            print(f"ADB watchdog not started: {type(e).__name__}", flush=True)

    # Register the maintenance graceful-shutdown callback (local-only /api/bridge/maintenance/shutdown).
    try:
        from nexus.maintenance import maintenance as _maintenance
        _HTTP_SERVERS.clear()
        for _srv in (httpd, httpsd):
            if _srv is not None:
                _HTTP_SERVERS.append(_srv)

        def _graceful_shutdown_all() -> None:
            def _run() -> None:
                time.sleep(0.3)
                for srv in list(_HTTP_SERVERS):
                    try:
                        srv.shutdown()
                    except Exception:
                        pass
            threading.Thread(target=_run, name="nexus-maintenance-shutdown", daemon=True).start()

        _maintenance().set_shutdown_callback(_graceful_shutdown_all)
        print("Maintenance drain gate active (/api/bridge/maintenance, loopback-only)", flush=True)
    except Exception as e:
        print(f"Maintenance gate not registered: {type(e).__name__}", flush=True)

    try:
        # NEXUS OS status monitor: periodic real re-check (auto re-integration of phone/AI/voice)
        from nexus import nexus_os
        nexus_os.start_monitor(lambda: ENGINE.master.ai)
        print("NEXUS OS status monitor started", flush=True)
    except Exception as e:
        print(f"NEXUS OS status monitor not started: {type(e).__name__}", flush=True)

    try:
        # Selbstbau-Engine: startet automatisch (Stop: POST /api/autobuild/stop oder workspace/autobuild/STOP)
        from nexus.autobuild.loop import STOP_FILE as _AB_STOP, get_autobuild as _get_ab
        if not _AB_STOP.exists():
            _get_ab().start()
            print("NEXUS Autobuild loop started", flush=True)
    except Exception as e:
        print(f"Autobuild not started: {type(e).__name__}", flush=True)

    try:
        # AETHER: tail shell-audit.jsonl, lock shell_readonly after repeated DENIED.
        from nexus.integrations.aether_supervisor import start_shell_audit_watch
        if start_shell_audit_watch(5.0):
            print("Aether shell audit watcher started", flush=True)
    except Exception as e:
        print(f"Aether shell audit watcher not started: {type(e).__name__}", flush=True)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("shutdown", flush=True)
    finally:
        if httpsd is not None:
            try:
                httpsd.shutdown()
            except Exception:
                pass


if __name__ == "__main__":
    main()
