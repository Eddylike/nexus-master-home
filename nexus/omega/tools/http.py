"""Generisches HTTP-Tool. GET/HEAD frei, schreibende Methoden freigabepflichtig."""
from __future__ import annotations

from nexus.omega.sanitize import sanitize_untrusted, wrap_untrusted
from nexus.omega.tools import _http


def is_destructive(params: dict) -> bool:
    return str(params.get("method") or "GET").upper() not in ("GET", "HEAD")


def http_request(params: dict, ctx: dict) -> dict:
    method = str(params.get("method") or "GET").upper()
    url = str(params.get("url") or "").strip()
    resp = _http.request(method, url,
                         headers=dict(params.get("headers") or {}),
                         json_body=params.get("json_body"),
                         body=params.get("body"),
                         timeout=float(params.get("timeout") or 30),
                         max_bytes=1_000_000)
    out = {
        "success": bool(resp.get("success")),
        "status": resp.get("status"),
        "status_code": resp.get("status_code"),
        "method": method,
        "url": resp.get("url"),
        "content_type": resp.get("content_type"),
        "headers": resp.get("headers"),
        "json": resp.get("json"),
        "truncated": resp.get("truncated"),
        "error": resp.get("error"),
    }
    body = resp.get("body") or ""
    if resp.get("json") is None and body:
        guarded = wrap_untrusted(body, source=url, max_chars=60_000)
        out["body"] = guarded["text"]
        out["wrapped"] = guarded["wrapped"]
        out["suspicious"] = guarded["suspicious"]
    elif resp.get("json") is not None:
        guarded = sanitize_untrusted(str(resp.get("json")), max_chars=60_000)
        out["untrusted"] = True
        out["suspicious"] = guarded["suspicious"]
    return out


HANDLERS = {"http_request": http_request}
