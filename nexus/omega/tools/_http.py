"""Gemeinsamer HTTP-Client fuer OMEGA-Tools (stdlib only)."""
from __future__ import annotations

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from nexus.omega.sanitize import sanitize_url

_SENSITIVE = {"authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization",
              "x-slack-signature", "x-slack-signature-version"}


def _public_target(url: str) -> bool:
    """Block local/private destinations and non-web ports, including redirects."""
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        if parsed.scheme.lower() not in ("http", "https") or not host or port not in (80, 443):
            return False
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home", ".lan")):
            return False
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            addresses = [ipaddress.ip_address(item[4][0].split("%", 1)[0]) for item in infos]
        return bool(addresses) and all(addr.is_global for addr in addresses)
    except (ValueError, OSError, socket.gaierror):
        return False


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        safe = urllib.parse.urljoin(req.full_url, newurl)
        if not _public_target(safe):
            return None
        return super().redirect_request(req, fp, code, msg, headers, safe)


def redact_headers(headers: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        out[str(key)] = "***" if str(key).lower() in _SENSITIVE else str(value)[:300]
    return out


def request(method: str, url: str, *, headers: dict[str, str] | None = None,
            json_body: Any = None, body: Any = None, timeout: float = 30,
            max_bytes: int = 2_000_000) -> dict:
    method = str(method or "GET").strip().upper()
    if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        return {"success": False, "status": "BAD_METHOD", "error": "method_not_allowed"}
    safe_url = sanitize_url(url)
    if not safe_url:
        return {"success": False, "status": "BAD_URL",
                "error": "Nur http(s) ohne eingebettete Zugangsdaten erlaubt"}
    data: bytes | None = None
    send_headers = {str(k): str(v) for k, v in (headers or {}).items()}
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        send_headers.setdefault("Content-Type", "application/json")
    elif body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else bytes(body)
    if method in ("GET", "HEAD") and data:
        return {"success": False, "status": "BAD_BODY", "error": "body_not_allowed_for_get"}

    req = urllib.request.Request(safe_url, data=data, method=method, headers=send_headers)
    try:
        with urllib.request.urlopen(req, timeout=max(1.0, min(float(timeout or 30), 120))) as resp:
            status = int(getattr(resp, "status", 0) or 0)
            raw = resp.read(max_bytes + 1)
            truncated = len(raw) > max_bytes
            payload = raw[:max_bytes].decode("utf-8", "replace")
            resp_headers = redact_headers(getattr(resp, "headers", {}))
            content_type = str(getattr(resp, "headers", {}).get("Content-Type") or "")
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read(max_bytes)
        except Exception:
            pass
        payload = raw.decode("utf-8", "replace")
        parsed = None
        try:
            parsed = json.loads(payload)
        except ValueError:
            pass
        return {"success": False, "status": "HTTP_" + str(getattr(exc, "code", 0)),
                "status_code": int(getattr(exc, "code", 0) or 0),
                "headers": redact_headers(getattr(exc, "headers", {})),
                "body": payload[:max_bytes], "json": parsed,
                "error": "HTTP " + str(getattr(exc, "code", 0)), "url": safe_url, "method": method}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return {"success": False, "status": "TRANSPORT_ERROR",
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                "url": safe_url, "method": method}

    parsed = None
    try:
        parsed = json.loads(payload)
    except ValueError:
        pass
    return {
        "success": 200 <= status < 400,
        "status": "OK" if 200 <= status < 400 else "HTTP_" + str(status),
        "status_code": status,
        "headers": resp_headers,
        "content_type": content_type,
        "body": payload,
        "json": parsed,
        "truncated": truncated,
        "url": safe_url,
        "method": method,
    }


def quote(value: str) -> str:
    return urllib.parse.quote(str(value), safe="")
