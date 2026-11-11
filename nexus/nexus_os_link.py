"""NEXUS OS <-> NEXUS Master Home link (Auftrag 005D).

Endpoints (only from loopback, Tailscale 192.0.2.11/10 or home LAN 192.0.2.12/16;
proxied/Funnel requests are refused):
  POST /api/nexus-os/device      phone heartbeat {battery, charging, net, app_version, version_code, ...}
  GET  /api/nexus-os/device      last heartbeat + age (missing values stay null, nothing invented)
  POST /api/nexus-os/sync        phone outbox {items:[{id, kind, text, ts}]} -> ack ids (idempotent)
  POST /api/nexus-os/pair        exchange a locally started one-time code (Tailscale/loopback only)
  GET  /api/nexus-os/sync?since= PC -> phone messages after cursor
  POST /api/nexus-os/push        PC side (loopback only): queue a message for the phone
  GET  /api/nexus-os/link        PC status for the app (server, ollama, last phone contact)
State is persisted under workspace/nexus_os_link/ (no secrets stored).
"""
from __future__ import annotations

import ipaddress
import re
import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "workspace" / "nexus_os_link"
_LOCK = threading.Lock()
_PAIRED_SEEN: dict[str, Any] = {}
_NETS = [ipaddress.ip_network(n) for n in ("127.0.0.0/8", "192.0.2.11/10", "192.0.2.12/16", "::1/128", "fd7a:115c:a1e0::/48")]
_PROXY_HDRS = ("X-Forwarded-For", "Tailscale-User-Login", "X-Forwarded-Host", "Forwarded")
KINDS = ("chat", "voice", "ai", "event", "pc_command", "ctx")
# F-05: gemeinsamer Gespraechskontext (kind "ctx"), Last-Writer-Wins pro id nach ts, nie Secrets
CTX_MAX = 50
_SECRET_RE = re.compile(r"(?i)(passw|kennwort|token|bearer|api[_-]?key|secret|\bpin\b|iban|[A-Za-z0-9_\-]{32,})")
DEVICE_KEYS = {"battery": int, "charging": bool, "net": str, "route": str, "app_version": str,
               "version_code": int, "wake_word": bool, "a11y": bool, "pc_online_seen": bool, "uptime_s": int,
               # F-03: lokaler Ollama-Status der App (nur bool/Version/Modellname/Pruefzeit, keine sensiblen Daten)
               "ollama_up": bool, "ollama_version": str, "ollama_model": str, "ollama_checked_ts": int}
PATHS = ("/api/nexus-os/device", "/api/nexus-os/sync", "/api/nexus-os/push", "/api/nexus-os/link",
         "/api/nexus-os/pair")


def _f(name: str) -> Path:
    return DIR / name


def allowed(ip: str, headers: Any) -> bool:
    try:
        if any(headers.get(h) for h in _PROXY_HDRS):
            return False
        a = ipaddress.ip_address((ip or "").split("%")[0])
        if a.version == 6 and a.ipv4_mapped:
            a = a.ipv4_mapped
        return any(a.version == n.version and a in n for n in _NETS)
    except Exception:
        return False


def _loopback(ip: str) -> bool:
    return ip in ("127.0.0.1", "::1")


def _read_lines(p: Path, strict: bool = False) -> list[dict[str, Any]]:
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(ln)
            if not isinstance(row, dict):
                raise ValueError("LINK_STORE_ROW_INVALID")
            out.append(row)
        except Exception:
            if strict:
                raise ValueError("LINK_STORE_CORRUPT")
    return out


def _append(p: Path, obj: dict[str, Any]) -> None:
    if os.name == "nt" and p.resolve().drive.upper() != "G:":
        raise ValueError("LINK_STATE_MUST_BE_ON_G")
    DIR.mkdir(parents=True, exist_ok=True)
    if p.is_file() and p.stat().st_size:
        with p.open("rb") as stream:
            stream.seek(-1, 2)
            if stream.read(1) != b"\n":
                raise ValueError("LINK_STORE_INCOMPLETE")
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def device_post(body: dict[str, Any], ip: str) -> tuple[int, dict[str, Any]]:
    rec: dict[str, Any] = {}
    for k, t in DEVICE_KEYS.items():
        v = body.get(k)
        if v is None:
            continue
        try:
            if t is bool and not isinstance(v, bool):
                continue  # F-03: nur echte JSON-Booleans, "false" darf nicht zu True werden
            rec[k] = str(v)[:40] if t is str else t(v)
        except Exception:
            continue
    rec["received_ts"] = time.time()
    rec["route_ip_kind"] = "tailscale" if ip.startswith("100.") else ("lan" if ip.startswith("192.168.") else "local")
    with _LOCK:
        DIR.mkdir(parents=True, exist_ok=True)
        _f("device.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    return 200, {"success": True, "stored": sorted(k for k in rec if k in DEVICE_KEYS), "pc": link_status()}


def note_paired_contact(ip: str, path: str) -> None:
    """MOBILE 20261109: remembers the last request that carried a VALID pairing Bearer
    (only called after pc_remote.authenticate succeeded). Stores time + route kind only,
    never the token or its hash."""
    try:
        rec = {"ts": time.time(), "path": str(path)[:60],
               "route_ip_kind": "tailscale" if ip.startswith("100.") else ("lan" if ip.startswith("192.168.") else "local")}
        with _LOCK:
            _PAIRED_SEEN.clear()
            _PAIRED_SEEN.update(rec)
            if os.name != "nt" or DIR.resolve().drive.upper() == "G:":
                DIR.mkdir(parents=True, exist_ok=True)
                _f("paired_contact.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def paired_contact_get() -> dict[str, Any] | None:
    """Last authenticated (paired) app contact with age_s, or None if never seen."""
    with _LOCK:
        rec = dict(_PAIRED_SEEN)
    if not rec:
        try:
            rec = json.loads(_f("paired_contact.json").read_text(encoding="utf-8"))
        except Exception:
            return None
    try:
        rec["age_s"] = round(time.time() - float(rec.get("ts") or 0), 1)
    except (TypeError, ValueError):
        return None
    return rec


def device_get() -> tuple[int, dict[str, Any]]:
    p = _f("device.json")
    if not p.is_file():
        return 200, {"success": True, "device": None, "state": "UNKNOWN"}
    d = json.loads(p.read_text(encoding="utf-8"))
    age = time.time() - float(d.get("received_ts") or 0)
    return 200, {"success": True, "device": d, "age_s": round(age, 1),
                 "state": "ONLINE" if age < 180 else "OFFLINE"}


def sync_post(body: dict[str, Any], principal: str | None = None, engine=None) -> tuple[int, dict[str, Any]]:
    items = body.get("items")
    if not isinstance(items, list) or len(items) > 200:
        return 400, {"success": False, "error": "ITEMS_INVALID"}
    acked, rejected, command_receipts = [], [], []
    # Never hold the link lock while scheduling/executing PC work: results use it too.
    regular = []
    for item in items:
        if not isinstance(item, dict) or item.get("kind") != "pc_command":
            regular.append(item)
            continue
        if not principal:
            return 401, {"success": False, "error": "PC_AUTH_REQUIRED"}
        from nexus.pc_remote import service
        accepted, receipt = service(engine).accept(item, principal)
        iid = item.get("id") if isinstance(item.get("id"), str) else ""
        (acked if accepted else rejected).append(iid)
        command_receipts.append({"id": iid, **receipt})
    with _LOCK:
        seen = {str(x.get("id")) for x in _read_lines(_f("inbox.jsonl"))}
        for it in regular:
            if not isinstance(it, dict):
                continue
            iid = str(it.get("id") or "")[:64]
            kind = str(it.get("kind") or "")
            text = str(it.get("text") or "")[:4000]
            if not iid or kind not in KINDS:
                rejected.append(iid)
                continue
            if kind == "ctx":
                (acked if ctx_put(iid, text, it.get("ts")) else rejected).append(iid)
                continue
            if iid not in seen:
                _append(_f("inbox.jsonl"), {"id": iid, "kind": kind, "text": text, "ts": it.get("ts"),
                                            "received_ts": time.time()})
                seen.add(iid)
            acked.append(iid)
    response = {"success": True, "acked": acked, "rejected": rejected}
    if command_receipts:
        response["pc_commands"] = command_receipts
    return 200, response


def ctx_put(iid: str, text: str, ts: Any) -> bool:
    """F-05 LWW: Eintrag nur ersetzen, wenn ts >= gespeichertem ts. Secrets/ungueltig -> False (rejected)."""
    try:
        ts_f = float(ts)
    except (TypeError, ValueError):
        return False
    if not iid or not text or _SECRET_RE.search(text):
        return False
    p = _f("ctx.json")
    data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    cur = data.get(iid)
    if cur is None or ts_f >= float(cur.get("ts") or 0):
        data[iid] = {"text": text[:1000], "ts": ts_f, "received_ts": time.time()}
    if len(data) > CTX_MAX:
        data = dict(sorted(data.items(), key=lambda kv: kv[1]["ts"])[-CTX_MAX:])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return True


def ctx_get(limit: int = 12) -> list[dict[str, Any]]:
    """F-05: letzte Kontext-Eintraege (aelteste zuerst)."""
    p = _f("ctx.json")
    if not p.is_file():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return sorted(({"id": k, **v} for k, v in data.items()), key=lambda r: r["ts"])[-limit:]


def sync_get(qs: dict[str, list[str]], principal: str | None = None) -> tuple[int, dict[str, Any]]:
    try:
        since = max(0, int((qs.get("since") or ["0"])[0]))
    except (ValueError, TypeError):
        since = 0
    try:
        pc_since = max(0, int((qs.get("pc_since") or [str(since)])[0]))
    except (ValueError, TypeError):
        return 400, {"success": False, "error": "PC_CURSOR_INVALID"}
    with _LOCK:
        stored = _read_lines(_f("outbox.jsonl"), strict=True)
    window = [r for r in stored if "_pc_owner" not in r and int(r.get("seq") or 0) > since][:100]
    cursor = (window[-1]["seq"] if len(window) == 100 else
              max(since, int(stored[-1].get("seq") or 0)) if stored else since)
    # Legacy clients keep their original cursor and never receive private PC events.
    # Paired clients track a second cursor so an anonymous poll cannot lose results.
    rows = list(window)
    response = {"success": True, "cursor": cursor}
    if principal:
        private = [r for r in stored if r.get("_pc_owner") == principal
                   and int(r.get("seq") or 0) > pc_since][:100]
        rows += private
        response["pc_cursor"] = private[-1]["seq"] if private else pc_since
    response["items"] = [{k: v for k, v in r.items() if k != "_pc_owner"}
                         for r in sorted(rows, key=lambda r: int(r["seq"]))]
    return 200, response


def push(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    kind = str(body.get("kind") or "chat")
    text = str(body.get("text") or "")[:4000]
    if kind not in KINDS or kind in ("pc_command", "ctx") or not text:
        return 400, {"success": False, "error": "KIND_OR_TEXT_INVALID"}
    with _LOCK:
        rows = _read_lines(_f("outbox.jsonl"), strict=True)
        seq = (int(rows[-1].get("seq") or 0) + 1) if rows else 1
        rec = {"seq": seq, "kind": kind, "text": text, "ts": time.time()}
        _append(_f("outbox.jsonl"), rec)
    return 200, {"success": True, "item": rec}


def publish_pc_event(event: dict[str, Any], principal: str) -> None:
    """Durable, idempotent result delivery, private to the paired PC credential."""
    with _LOCK:
        rows = _read_lines(_f("outbox.jsonl"), strict=True)
        if any(r.get("event_id") == event["event_id"] and r.get("_pc_owner") == principal for r in rows):
            return
        seq = int(rows[-1].get("seq") or 0) + 1 if rows else 1
        _append(_f("outbox.jsonl"), {**event, "seq": seq, "ts": time.time(), "_pc_owner": principal})


def link_status() -> dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
            ollama = r.status == 200
    except Exception:
        ollama = False
    dev = None
    p = _f("device.json")
    if p.is_file():
        try:
            dev = round(time.time() - float(json.loads(p.read_text(encoding="utf-8")).get("received_ts") or 0), 1)
        except Exception:
            dev = None
    return {"server": "ONLINE", "ollama": "ONLINE" if ollama else "OFFLINE", "ts": time.time(),
            "phone_last_seen_s": dev}


def handle(method: str, path: str, ip: str, headers: Any, qs: dict | None = None, body: Any = None, engine=None):
    """Returns (status, payload) or None if the path is not ours."""
    if path not in PATHS:
        return None
    if not allowed(ip, headers):
        return 403, {"success": False, "error": "NETWORK_NOT_ALLOWED"}
    if method == "GET":
        if path == "/api/nexus-os/device":
            return device_get()
        if path == "/api/nexus-os/sync":
            from nexus.pc_remote import authenticate
            principal, error = authenticate(headers)
            if error and (headers.get("Authorization") or headers.get("authorization")):
                return 401, {"success": False, "error": error}
            if principal:
                note_paired_contact(ip, "GET /api/nexus-os/sync")
            return sync_get(qs or {}, principal)
        if path == "/api/nexus-os/link":
            from nexus.pc_remote import capabilities
            return 200, {"success": True, **link_status(), "pc_commands": capabilities()}
        return 405, {"success": False, "error": "METHOD"}
    if method != "POST":
        return 405, {"success": False, "error": "METHOD"}
    if not isinstance(body, dict):
        return 400, {"success": False, "error": "BODY_MUST_BE_OBJECT"}
    if path == "/api/nexus-os/pair":
        from nexus.pc_pairing import exchange
        return exchange(body, ip, headers)
    if path == "/api/nexus-os/device":
        return device_post(body, ip)
    if path == "/api/nexus-os/sync":
        principal = None
        items = body.get("items")
        if isinstance(items, list) and any(isinstance(i, dict) and i.get("kind") == "pc_command" for i in items):
            from nexus.pc_remote import authenticate
            principal, error = authenticate(headers)
            if error:
                return 401, {"success": False, "error": error}
            note_paired_contact(ip, "POST /api/nexus-os/sync")
        return sync_post(body, principal, engine)
    if path == "/api/nexus-os/push":
        if not _loopback(ip):
            return 403, {"success": False, "error": "LOCAL_ONLY"}
        return push(body)
    return 405, {"success": False, "error": "METHOD"}
