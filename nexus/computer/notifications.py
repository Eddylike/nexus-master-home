"""Windows-Benachrichtigungen (inkl. WhatsApp Desktop) NUR LESEND auslesen.

Quelle: %LOCALAPPDATA%\\Microsoft\\Windows\\Notifications\\wpndatabase.db,
geoeffnet mit sqlite ?mode=ro (kein Schreibzugriff, keine Kopie auf C:).
"""
from __future__ import annotations

import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

DB_PATH = os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Notifications\wpndatabase.db")

_READ = re.compile(r"\b(lies|lese|les|vorlesen|vorles\w*|zeig\w*|was\s+(?:steht|gibt\s+es)|hab\w*\s+ich|check\w*|pruef\w*|prüf\w*)\b", re.I)
_WA = re.compile(r"whats\s*-?\s*app|watsapp|whatapp", re.I)
_NOTIF = re.compile(r"benachrichtigung\w*|notification\w*|mitteilung\w*", re.I)
_MSG = re.compile(r"nachricht\w*", re.I)
_SEND = re.compile(r"\b(schreib\w*|send\w*|schick\w*)\b", re.I)


def match_request(text: str) -> dict | None:
    """'lies mir meine whatsapp-nachrichten vor' / 'lies mir meine benachrichtigungen vor'."""
    t = (text or "").strip()
    if not t or _SEND.search(t) or not _READ.search(t):
        return None
    if _WA.search(t) and (_MSG.search(t) or _NOTIF.search(t) or re.search(r"\bvor\b", t, re.I)):
        return {"app": "whatsapp"}
    if _NOTIF.search(t):
        return {"app": None}
    return None


def _ft(v: int) -> datetime | None:
    try:
        return datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(v) // 10)
    except Exception:
        return None


def _texts(payload: Any) -> list[str]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "ignore")
    try:
        root = ET.fromstring(payload)
    except Exception:
        return []
    return [(e.text or "").strip() for e in root.iter("text") if (e.text or "").strip()]


def read_notifications(app: str | None = None, limit: int = 20, db_path: str | None = None) -> dict[str, Any]:
    path = db_path or DB_PATH
    if not os.path.exists(path):
        return {"success": False, "error": "db_missing", "source": path,
                "reply": "Die Windows-Benachrichtigungsdatenbank wurde nicht gefunden."}
    try:
        con = sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True, timeout=3)
        rows = con.execute(
            "SELECT h.PrimaryId, n.Payload, n.ArrivalTime FROM Notification n "
            "JOIN NotificationHandler h ON h.RecordId = n.HandlerId "
            "WHERE n.Type = 'toast' ORDER BY n.ArrivalTime DESC LIMIT 400").fetchall()
        con.close()
    except Exception as exc:
        return {"success": False, "error": f"{type(exc).__name__}: {exc}", "source": path,
                "reply": f"Benachrichtigungen konnten nicht gelesen werden: {type(exc).__name__}"}
    items = []
    for pid, payload, at in rows:
        if app == "whatsapp" and "whatsapp" not in str(pid).lower():
            continue
        tx = _texts(payload)
        if not tx:
            continue
        dt = _ft(at)
        items.append({"app": "WhatsApp" if "whatsapp" in str(pid).lower() else str(pid).split("_")[0].split("!")[0],
                      "sender": tx[0], "text": " ".join(tx[1:]).strip(),
                      "time": dt.astimezone().strftime("%d.%m. %H:%M") if dt else ""})
        if len(items) >= limit:
            break
    label = "WhatsApp-Nachrichten" if app == "whatsapp" else "Benachrichtigungen"
    if not items:
        reply = f"Keine neuen {label} in den Windows-Benachrichtigungen."
    else:
        lines = [f"Du hast {len(items)} {label}:"]
        for it in items:
            pre = "" if app == "whatsapp" else f"[{it['app']}] "
            lines.append(f"- {pre}{it['sender']} ({it['time']}): {it['text']}".rstrip(": "))
        reply = "\n".join(lines)
    return {"success": True, "action": "notifications_read", "app": app, "count": len(items),
            "items": items, "source": "wpndatabase.db (read-only)", "verified": True, "reply": reply}
