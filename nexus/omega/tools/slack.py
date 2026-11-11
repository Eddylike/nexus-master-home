"""Slack-Tools: Block-Kit-Post (Webhook oder Bot-Token) und Lesen."""
from __future__ import annotations

from nexus.omega import config
from nexus.omega.sanitize import sanitize_untrusted
from nexus.omega.tools import _http


def _chunks(text: str, size: int = 2_900) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    out: list[str] = []
    while text:
        if len(text) <= size:
            out.append(text)
            break
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    return out


def build_blocks(text: str, title: str | None = None) -> list[dict]:
    blocks: list[dict] = []
    if title:
        blocks.append({"type": "header", "text": {"type": "plain_text",
                                                   "text": str(title)[:150]}})
    for chunk in _chunks(text):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
    blocks.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": "Gesendet von *OMEGA* (NEXUS)"}]})
    return blocks


def _cfg() -> tuple[str, str, str]:
    s = config.secrets()
    return (str(s.get("OMEGA_SLACK_WEBHOOK_URL") or "").strip(),
            str(s.get("OMEGA_SLACK_BOT_TOKEN") or "").strip(),
            str(s.get("OMEGA_SLACK_CHANNEL") or "").strip())


def slack_post(params: dict, ctx: dict) -> dict:
    text = str(params.get("text") or "").strip()
    if not text:
        return {"success": False, "status": "INVALID_PARAMS", "error": "text_required"}
    title = params.get("title")
    blocks = params.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        blocks = build_blocks(text, str(title) if title else None)
    webhook, token, default_channel = _cfg()
    channel = str(params.get("channel") or default_channel or "").strip()
    thread_ts = str(params.get("thread_ts") or "").strip()

    if webhook:
        payload = {"text": text, "blocks": blocks}
        resp = _http.request("POST", webhook, json_body=payload, timeout=15)
        ok = bool(resp.get("success")) and str(resp.get("body") or "").strip().lower() in ("ok", "")
        return {
            "success": ok,
            "status": "OK" if ok else "SLACK_FAILED",
            "transport": "webhook",
            "channel": channel or "(webhook-default)",
            "status_code": resp.get("status_code"),
            "error": None if ok else (resp.get("error") or "webhook_rejected"),
            "blocks": len(blocks),
        }
    if token:
        if not channel:
            return {"success": False, "status": "INVALID_PARAMS",
                    "error": "channel_required (OMEGA_SLACK_CHANNEL oder params.channel)"}
        payload = {"channel": channel, "text": text, "blocks": blocks}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        resp = _http.request("POST", "https://slack.com/api/chat.postMessage",
                             headers={"Authorization": "Bearer " + token},
                             json_body=payload, timeout=20)
        data = resp.get("json") or {}
        ok = bool(data.get("ok")) and bool(resp.get("success"))
        return {
            "success": ok,
            "status": "OK" if ok else "SLACK_FAILED",
            "transport": "bot",
            "channel": data.get("channel") or channel,
            "ts": data.get("ts"),
            "status_code": resp.get("status_code"),
            "error": None if ok else str(data.get("error") or resp.get("error") or "slack_error"),
            "blocks": len(blocks),
        }
    return {"success": False, "status": "NOT_CONFIGURED",
            "error": ("Slack ist nicht konfiguriert. Setze OMEGA_SLACK_WEBHOOK_URL oder "
                      "OMEGA_SLACK_BOT_TOKEN (+ OMEGA_SLACK_CHANNEL) in workspace/secrets/omega.env.")}


def slack_read(params: dict, ctx: dict) -> dict:
    _, token, default_channel = _cfg()
    channel = str(params.get("channel") or default_channel or "").strip()
    limit = int(params.get("limit") or 20)
    if not token:
        return {"success": False, "status": "NOT_CONFIGURED",
                "error": "OMEGA_SLACK_BOT_TOKEN fehlt"}
    if not channel:
        return {"success": False, "status": "INVALID_PARAMS", "error": "channel_required"}
    url = ("https://slack.com/api/conversations.history?channel=" + _http.quote(channel)
           + "&limit=" + str(max(1, min(limit, 50))))
    resp = _http.request("GET", url, headers={"Authorization": "Bearer " + token}, timeout=20)
    data = resp.get("json") or {}
    if not data.get("ok"):
        return {"success": False, "status": "SLACK_FAILED",
                "error": str(data.get("error") or resp.get("error") or "slack_error")}
    messages = []
    for item in data.get("messages") or []:
        clean = sanitize_untrusted(item.get("text"), max_chars=4_000)
        messages.append({"ts": item.get("ts"), "user": item.get("user"),
                         "text": clean["text"], "suspicious": clean["suspicious"]})
    return {"success": True, "status": "OK", "channel": channel, "messages": messages,
            "count": len(messages), "untrusted": True}


def slack_channels(params: dict, ctx: dict) -> dict:
    _, token, _ = _cfg()
    limit = int(params.get("limit") or 100)
    if not token:
        return {"success": False, "status": "NOT_CONFIGURED",
                "error": "OMEGA_SLACK_BOT_TOKEN fehlt"}
    url = ("https://slack.com/api/conversations.list?types=public_channel,private_channel&limit="
           + str(max(1, min(limit, 200))))
    resp = _http.request("GET", url, headers={"Authorization": "Bearer " + token}, timeout=20)
    data = resp.get("json") or {}
    if not data.get("ok"):
        return {"success": False, "status": "SLACK_FAILED",
                "error": str(data.get("error") or resp.get("error") or "slack_error")}
    channels = [{"id": c.get("id"), "name": c.get("name"), "is_private": c.get("is_private")}
                for c in data.get("channels") or []]
    return {"success": True, "status": "OK", "channels": channels, "count": len(channels)}


HANDLERS = {
    "slack_post": slack_post,
    "slack_read": slack_read,
    "slack_channels": slack_channels,
}
