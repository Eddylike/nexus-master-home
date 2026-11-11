"""Generischer Webhook-Trigger (Zapier/Make/n8n). Freigabepflichtig."""
from __future__ import annotations

from urllib.parse import urlsplit

from nexus.omega import config
from nexus.omega.sanitize import sanitize_url
from nexus.omega.tools import _http


def is_destructive(params: dict) -> bool:
    return True


def trigger_webhook(params: dict, ctx: dict) -> dict:
    raw_url = params.get("url") or config.secret("OMEGA_ZAPIER_WEBHOOK_URL")
    if not raw_url:
        return {"success": False, "status": "NOT_CONFIGURED",
                "error": ("Kein Webhook konfiguriert. Setze OMEGA_ZAPIER_WEBHOOK_URL "
                          "oder uebergebe params.url.")}
    url = sanitize_url(raw_url)
    if not url:
        return {"success": False, "status": "BAD_URL",
                "error": "Webhook-URL muss http(s) ohne eingebettete Credentials sein"}
    host = (urlsplit(url).hostname or "").lower()
    allow = config.webhook_allowlist()
    if allow and host not in allow:
        return {"success": False, "status": "HOST_NOT_ALLOWED",
                "error": f"Host {host} ist nicht in OMEGA_WEBHOOK_ALLOWLIST"}
    payload = params.get("payload")
    if not isinstance(payload, dict):
        return {"success": False, "status": "INVALID_PARAMS",
                "error": "payload_must_be_object"}
    method = str(params.get("method") or "POST").upper()
    if method not in ("POST", "PUT", "PATCH"):
        return {"success": False, "status": "INVALID_PARAMS", "error": "method_not_allowed"}
    resp = _http.request(method, url, json_body=payload, timeout=20)
    return {
        "success": bool(resp.get("success")),
        "status": "OK" if resp.get("success") else "WEBHOOK_FAILED",
        "host": host,
        "status_code": resp.get("status_code"),
        "error": None if resp.get("success") else (resp.get("error") or "webhook_failed"),
        "payload_keys": sorted(payload)[:50],
    }


HANDLERS = {"trigger_webhook": trigger_webhook}
