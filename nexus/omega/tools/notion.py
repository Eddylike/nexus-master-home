"""Notion-Tools (offizielle HTTP-API, Token aus Secrets)."""
from __future__ import annotations

from nexus.omega import config
from nexus.omega.sanitize import sanitize_untrusted
from nexus.omega.tools import _http

_BASE = "https://api.notion.com/v1"
_VERSION = "2022-06-28"


def _token() -> str:
    return config.secret("OMEGA_NOTION_TOKEN")


def _call(method: str, path: str, payload: dict | None = None) -> dict:
    token = _token()
    if not token:
        return {"success": False, "status": "NOT_CONFIGURED",
                "error": "OMEGA_NOTION_TOKEN fehlt (workspace/secrets/omega.env)"}
    resp = _http.request(method, _BASE + path,
                         headers={"Authorization": "Bearer " + token,
                                  "Notion-Version": _VERSION},
                         json_body=payload, timeout=25)
    data = resp.get("json") or {}
    if not resp.get("success") or data.get("object") == "error":
        return {"success": False, "status": "NOTION_FAILED",
                "status_code": resp.get("status_code"),
                "error": str(data.get("message") or resp.get("error") or "notion_error")[:500]}
    return {"success": True, "status": "OK", "json": data}


def _plain(prop: dict) -> str:
    kind = prop.get("type")
    value = prop.get(kind) if kind else None
    if isinstance(value, list):
        return "".join(str(v.get("plain_text") or "") for v in value if isinstance(v, dict))
    if isinstance(value, dict):
        return str(value.get("name") or value.get("id") or "")
    return ""


def notion_query(params: dict, ctx: dict) -> dict:
    database_id = str(params.get("database_id") or "").strip()
    if not database_id:
        return {"success": False, "status": "INVALID_PARAMS", "error": "database_id_required"}
    payload: dict = {"page_size": max(1, min(int(params.get("page_size") or 25), 100))}
    if isinstance(params.get("filter"), dict):
        payload["filter"] = params["filter"]
    res = _call("POST", f"/databases/{database_id}/query", payload)
    if not res.get("success"):
        return res
    pages = []
    for page in (res["json"].get("results") or []):
        props = page.get("properties") or {}
        flat = {name: _plain(value) for name, value in props.items() if isinstance(value, dict)}
        raw = "\n".join(f"{k}: {v}" for k, v in flat.items() if v)
        clean = sanitize_untrusted(raw, max_chars=8_000)
        pages.append({"id": page.get("id"), "url": page.get("url"),
                      "properties": flat, "text": clean["text"],
                      "suspicious": clean["suspicious"]})
    return {"success": True, "status": "OK", "results": pages, "count": len(pages),
            "untrusted": True}


def notion_create_page(params: dict, ctx: dict) -> dict:
    database_id = str(params.get("database_id") or "").strip()
    title = str(params.get("title") or "").strip()
    content = str(params.get("content") or "").strip()
    if not database_id or not title:
        return {"success": False, "status": "INVALID_PARAMS",
                "error": "database_id_and_title_required"}
    properties = params.get("properties")
    if not isinstance(properties, dict) or not properties:
        properties = {"Name": {"title": [{"type": "text", "text": {"content": title[:2000]}}]}}
    children = []
    for chunk in [content[i:i + 1900] for i in range(0, len(content), 1900)][:40]:
        children.append({"object": "block", "type": "paragraph", "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": chunk}}]}})
    payload = {"parent": {"database_id": database_id}, "properties": properties}
    if children:
        payload["children"] = children
    res = _call("POST", "/pages", payload)
    if not res.get("success"):
        return res
    page = res["json"]
    return {"success": True, "status": "OK", "id": page.get("id"), "url": page.get("url"),
            "title": title, "children": len(children)}


HANDLERS = {
    "notion_query": notion_query,
    "notion_create_page": notion_create_page,
}
