"""LT-08: Zugang fuer Live Talk.

Erlaubt sind nur direkte Verbindungen aus localhost, Heim-LAN (192.168/16, 10/8, 172.16/12) und Tailscale
(192.0.2.11/10, fd7a:115c:a1e0::/48); Proxy-/Funnel-Header werden abgewiesen.
Ausserhalb von localhost ist ein Token noetig:
  - das bestehende NEXUS-OS-Pairing-Token der App (pc_remote, Bearer) ODER
  - ein eigenes Live-Talk-Geraetetoken fuer den Handy-Browser, ausgegeben gegen einen 6-stelligen Code,
    den NUR ein lokaler Aufruf am PC erzeugt (livetalk_pair.ps1). Gespeichert wird nur der SHA-256-Hash.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ROOT / "workspace" / "secrets"
TOKENS = SECRETS / "livetalk_tokens.json"
PAIRING = SECRETS / "livetalk_pairing.json"
CODE_TTL = 180
MAX_ATTEMPTS = 5
MAX_DEVICES = 5
_LOCK = threading.RLock()
_NETS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128", "192.0.2.12/16", "10.0.0.0/8", "172.16.0.0/12", "192.0.2.11/10", "fd7a:115c:a1e0::/48"))
_PROXY = ("x-forwarded-for", "x-forwarded-host", "forwarded", "via", "x-real-ip", "cf-connecting-ip",
          "tailscale-user-login", "x-tailscale-funnel-request")


def _addr(ip: str):
    a = ipaddress.ip_address((ip or "").split("%")[0])
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a


def has_proxy_headers(headers: Any) -> bool:
    try:
        return any(str(k).lower() in _PROXY or str(k).lower().startswith("tailscale-") for k in (headers or {}).keys())
    except Exception:
        return True


def is_allowed_client(ip: str, headers: Any = None) -> bool:
    try:
        if headers is not None and has_proxy_headers(headers):
            return False
        a = _addr(ip)
        return any(a.version == n.version and a in n for n in _NETS)
    except (ValueError, TypeError):
        return False


def is_loopback(ip: str, headers: Any = None) -> bool:
    try:
        return _addr(ip).is_loopback and not (headers is not None and has_proxy_headers(headers))
    except (ValueError, TypeError):
        return False


def _h(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
    except Exception:
        return {}


def _write(p: Path, obj: dict) -> None:
    if os.name == "nt" and p.resolve().drive.upper() != "G:":
        raise RuntimeError("LIVETALK_SECRETS_MUST_BE_ON_G")
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, p)


def check_token(token: str | None) -> str | None:
    """Rueckgabe: 'app' (NEXUS-OS-Pairing), 'device' (Live-Talk-Geraet) oder None."""
    tok = str(token or "").strip()
    if len(tok) < 20:
        return None
    try:
        from nexus.pc_remote import _token as _app_token
        exp = _app_token()
        if exp and hmac.compare_digest(tok.encode(), exp.encode()):
            return "app"
    except Exception:
        pass
    h = _h(tok)
    with _LOCK:
        for d in _read(TOKENS).get("devices", []):
            if hmac.compare_digest(h, str(d.get("hash") or "")):
                return "device"
    return None


def start_pairing() -> dict:
    """NUR lokal (CLI am PC) aufrufen. Liefert einen 6-stelligen Code (3 Minuten gueltig, 5 Versuche)."""
    code = f"{secrets.randbelow(10 ** 6):06d}"
    salt = secrets.token_hex(16)
    with _LOCK:
        _write(PAIRING, {"salt": salt, "hash": _h(salt + code), "expires": time.time() + CODE_TTL, "attempts": 0})
    return {"code": code, "expires_in": CODE_TTL}


def exchange(code: str, ip: str, headers: Any, label: str = "") -> tuple[int, dict]:
    if not is_allowed_client(ip, headers):
        return 403, {"success": False, "error": "NETWORK_NOT_ALLOWED"}
    code = str(code or "").strip()
    with _LOCK:
        st = _read(PAIRING)
        if not st or st.get("used"):
            return 409, {"success": False, "error": "LIVETALK_PAIRING_NOT_ACTIVE"}
        if time.time() > float(st.get("expires") or 0):
            return 410, {"success": False, "error": "LIVETALK_PAIRING_EXPIRED"}
        if int(st.get("attempts") or 0) >= MAX_ATTEMPTS:
            return 429, {"success": False, "error": "LIVETALK_PAIRING_LOCKED"}
        if not (len(code) == 6 and code.isdigit() and hmac.compare_digest(_h(st["salt"] + code), st["hash"])):
            st["attempts"] = int(st.get("attempts") or 0) + 1
            _write(PAIRING, st)
            return 401, {"success": False, "error": "LIVETALK_PAIRING_CODE_INVALID",
                         "attempts_left": MAX_ATTEMPTS - st["attempts"]}
        st["used"] = True
        _write(PAIRING, st)
        token = secrets.token_urlsafe(32)
        data = _read(TOKENS)
        devs = [d for d in data.get("devices", []) if isinstance(d, dict)][-(MAX_DEVICES - 1):]
        devs.append({"hash": _h(token), "label": str(label or "")[:40], "created": time.time()})
        _write(TOKENS, {"devices": devs})
    return 200, {"success": True, "token": token}


if __name__ == "__main__":  # python -m nexus.livetalk.auth pair
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "pair":
        r = start_pairing()
        print(f"Live-Talk-Code: {r['code']}  (gueltig {r['expires_in'] // 60} Minuten) - am Handy eingeben.")
    else:
        print("Aufruf: python -m nexus.livetalk.auth pair")
