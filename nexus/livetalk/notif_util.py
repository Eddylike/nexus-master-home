"""Hilfen fuer Live-Talk-Benachrichtigungen: Parse, Filter, Merge, Rangfolge, ADB-Fallback.

Inhalte werden nicht geloggt und nicht auf die Platte geschrieben.
"""
from __future__ import annotations

import json
import re
from typing import Any


def coerce_notification_items(items: Any) -> list[dict]:
    """JS-Bridge liefert getNotifications() oft als JSON-String, nicht als Liste."""
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            return []
    if isinstance(items, dict):
        items = items.get("notifications") or items.get("items") or []
    if not isinstance(items, list):
        return []
    return [dict(it) for it in items if isinstance(it, dict)]


# Chat-Apps: werden immer als Nachricht behandelt und zuerst vorgelesen.
CHAT_APPS = {
    "com.whatsapp": "WhatsApp",
    "com.whatsapp.w4b": "WhatsApp",
    "org.telegram.messenger": "Telegram",
    "org.telegram.messenger.web": "Telegram",
    "org.thunderdog.challegram": "Telegram",
    "com.google.android.apps.messaging": "SMS",
    "com.android.mms": "SMS",
    "com.android.messaging": "SMS",
    "com.samsung.android.messaging": "SMS",
    "com.facebook.orca": "Messenger",
    "com.facebook.mlite": "Messenger",
    "org.thoughtcrime.securesms": "Signal",
    "com.instagram.android": "Instagram",
    "com.discord": "Discord",
    "com.snapchat.android": "Snapchat",
}
# Apps mit Nachrichten nur bei category=msg (sonst Likes/Werbung).
OTHER_APPS = {
    "com.zhiliaoapp.musically": "TikTok",
    "com.ss.android.ugc.trill": "TikTok",
    "com.linkedin.android": "LinkedIn",
    "net.lovoo.android": "Lovoo",
    "com.huawei.health": "Huawei Health",
    "org.videolan.vlc": "VLC",
    "com.android.shell": "Test",
}
# Kategorien, die nie vorgelesen werden (Medien, Dienste, Fortschritt, System).
SKIP_CATEGORIES = {"transport", "service", "progress", "sys", "status", "navigation", "call",
                   "alarm", "stopwatch", "location_sharing", "workout"}
SELF_NAMES = {"du", "you", "ich", "me", "sie", "mir"}
OWN_PKGS = {"com.nexus.livetalk"}
_ANDROID_KEY_RE = re.compile(r"^-?\d+\|[A-Za-z0-9._]+\|")


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def is_self_name(name: Any, self_name: Any = "") -> bool:
    n = _norm(name)
    if not n:
        return False
    return n in SELF_NAMES or (bool(_norm(self_name)) and n == _norm(self_name))


def is_message(n: dict | None) -> bool:
    if not isinstance(n, dict):
        return False
    pkg = str(n.get("pkg") or "").lower()
    if pkg in CHAT_APPS or str(n.get("category") or "").lower() == "msg":
        return True
    app = _norm(n.get("app"))
    return not pkg and bool(app) and any(app.startswith(_norm(v)) for v in set(CHAT_APPS.values()))


def is_speakable(n: dict | None) -> bool:
    if not isinstance(n, dict):
        return False
    if n.get("group_summary"):
        return False
    if n.get("from_self"):
        return False
    pkg = str(n.get("pkg") or "").lower()
    if n.get("own") or pkg in OWN_PKGS:
        return False
    cat = str(n.get("category") or "").lower()
    if cat in SKIP_CATEGORIES:
        return False
    title = str(n.get("title") or "").strip()
    text = str(n.get("text") or "").strip()
    if n.get("ongoing") and (cat != "msg" or (not title and not text)):
        return False
    if is_message(n) and is_self_name(title) and not n.get("sender"):
        return False  # eigene letzte Nachricht ("Du: ...")
    return bool(title or text)


def app_label(pkg: str) -> str:
    p = (pkg or "").lower()
    if p in CHAT_APPS:
        return CHAT_APPS[p]
    if p in OTHER_APPS:
        return OTHER_APPS[p]
    parts = [x for x in p.split(".") if x and x not in ("com", "net", "org", "de", "android", "app", "apps", "mobile")]
    return parts[0].capitalize() if parts else (pkg or "Nachricht")


def notif_key(n: dict) -> str:
    return str(n.get("key") or n.get("id") or "")


def _when(n: dict) -> float:
    for k in ("when", "post_time", "update_ms", "_ts"):
        try:
            v = float(n.get(k) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v / 1000.0 if v > 1e11 else v
    return 0.0


def merge_notifications(store: list[dict], adb: list[dict]) -> list[dict]:
    """Listener-Store + ADB-Dump zusammenfuehren, nach Key (sonst pkg|titel|text) dedupliziert.

    Store-Eintraege behalten ihre Aktionen (Antworten); Absender/Text aus dem Dump gewinnen,
    wenn der Store nur die eigene Nachricht ("Du") oder nichts hat.
    """
    out: dict[str, dict] = {}
    order: list[str] = []

    def _k(n: dict) -> str:
        return notif_key(n) or "|".join((str(n.get("pkg") or ""), _norm(n.get("title")), _norm(n.get("text"))))

    for n in list(adb or []) + list(store or []):
        if not isinstance(n, dict):
            continue
        k = _k(n)
        if k not in out:
            out[k] = dict(n)
            order.append(k)
            continue
        base = out[k]
        is_store = n.get("source") != "adb_dumpsys"
        merged = dict(base)
        for f, v in n.items():
            if v in (None, "", [], {}):
                continue
            if f in ("title", "text", "sender") and base.get("sender") and not n.get("sender"):
                continue  # Absender aus MessagingStyle (Dump) behalten
            if f in ("title", "text") and is_self_name(v) and base.get(f) and not is_self_name(base.get(f)):
                continue
            merged[f] = v
        if is_store:
            merged["source"] = "store+adb"
        out[k] = merged
    return [out[k] for k in order]


def rank_notifications(items: list[dict]) -> list[dict]:
    """Nur vorlesbare Eintraege; Messenger zuerst, dann der Rest; jeweils neueste zuerst."""
    seen: set[str] = set()
    res: list[dict] = []
    for n in items or []:
        if not is_speakable(n):
            continue
        k = notif_key(n) or "|".join((str(n.get("pkg") or ""), _norm(n.get("title")), _norm(n.get("text"))))
        dup = "|".join((str(n.get("pkg") or ""), _norm(n.get("title")), _norm(n.get("text"))))
        if k in seen or dup in seen:
            continue
        seen.add(k)
        seen.add(dup)
        res.append(n)
    res.sort(key=lambda n: (0 if is_message(n) else 1, -_when(n)))
    return res


def is_android_key(k: str) -> bool:
    return bool(_ANDROID_KEY_RE.match(k or ""))


_TITLE_RE = re.compile(r"android\.title=\w+\s+\((.*)\)\s*$")
_TEXT_RE = re.compile(r"android\.text=\w+\s+\((.*)\)\s*$")
_BIGTEXT_RE = re.compile(r"android\.bigText=\w+\s+\((.*)\)\s*$")
_CONV_RE = re.compile(r"android\.conversationTitle=\w+\s+\((.*)\)\s*$")
_SELF_RE = re.compile(r"android\.selfDisplayName=\w+\s+\((.*)\)\s*$")
_GROUPCONV_RE = re.compile(r"android\.isGroupConversation=\w+\s+\((true|false)\)", re.I)
_PKG_RE = re.compile(r"pkg=([A-Za-z0-9._]+)")
_KEY_RE = re.compile(r"\bkey=(\S+)")
_CAT_RE = re.compile(r"\bcategory=(\S+)")
_WHEN_RE = re.compile(r"\bwhen=(\d{10,})")
_UPD_RE = re.compile(r"\bmUpdateTimeMs=(\d{10,})")
_FLAGS_RE = re.compile(r"^\s*flags=(\S+)", re.M)
_SUMMARY_RE = re.compile(r"flags=(?:[^\n]*?)(?:GROUP_SUMMARY|FLAG_GROUP_SUMMARY)")
_RECORD_RE = re.compile(r"\n(?=\s*NotificationRecord\()")
_MSG_LINE_RE = re.compile(r"^\s*\[\d+\]\s*Bundle\[\{(.*)\}\]\s*$")
_EXTRA_KEY_RE = re.compile(r"^\s*[A-Za-z_][\w.]*=")
_MSG_KEYS = "sender|sender_person|text|time|type|uri|extras|key"


def _bundle_field(body: str, name: str) -> str:
    m = re.search(r"(?:^|, )" + name + r"=(.*?)(?=, (?:" + _MSG_KEYS + r")=|$)", body)
    return m.group(1).strip() if m else ""


def parse_messages_block(lines: list[str]) -> list[dict]:
    """android.messages aus dem Dump: [{sender, text, time}] (aelteste zuerst)."""
    msgs: list[dict] = []
    inside = False
    for line in lines:
        if "android.messages=" in line:
            inside = True
            continue
        if not inside:
            continue
        m = _MSG_LINE_RE.match(line)
        if m:
            body = m.group(1)
            sender = _bundle_field(body, "sender")
            if sender.lower() == "null":
                sender = ""
            t = _bundle_field(body, "time")
            msgs.append({"sender": sender, "text": _bundle_field(body, "text"),
                         "time": int(t) if t.isdigit() else 0})
            continue
        if _EXTRA_KEY_RE.match(line):
            inside = False
    return msgs


def last_incoming(msgs: list[dict], self_name: str = "") -> dict | None:
    for m in reversed(msgs or []):
        s = str(m.get("sender") or "").strip()
        if s and not is_self_name(s, self_name) and str(m.get("text") or "").strip():
            return m
    return None


def _first(rx: re.Pattern, lines: list[str]) -> str:
    for line in lines:
        m = rx.search(line)
        if m:
            return m.group(1).strip()
    return ""


def parse_android_notification_dump(dump: str, active_keys: set | None = None) -> list[dict]:
    """Parse `dumpsys notification`. Der Dump selbst wird nicht gespeichert.

    active_keys (optional) bekommt alle aktiven Keys, auch Summaries/nicht vorlesbare.
    """
    if not dump:
        return []
    dump = dump.replace("\r\n", "\n")
    parts = _RECORD_RE.split("\n" + dump)
    out: list[dict] = []
    seen: set[str] = set()
    for block in parts:
        if "NotificationRecord(" not in block:
            continue
        # publicNotification (Sperrbildschirm-Version) abschneiden, sonst falsche Titel
        main = block.split("publicNotification=", 1)[0]
        lines = main.splitlines()
        group_summary = bool(_SUMMARY_RE.search(main))
        pkg_m = _PKG_RE.search(main)
        key_m = _KEY_RE.search(main)
        cat_m = _CAT_RE.search(main)
        fl_m = _FLAGS_RE.search(main)
        flags = fl_m.group(1) if fl_m else ""
        title = _first(_TITLE_RE, lines)
        text = _first(_TEXT_RE, lines) or _first(_BIGTEXT_RE, lines)
        conv = _first(_CONV_RE, lines)
        self_name = _first(_SELF_RE, lines)
        is_group = _first(_GROUPCONV_RE, lines).lower() == "true"
        pkg = pkg_m.group(1) if pkg_m else ""
        key = key_m.group(1) if key_m else ""
        if key and active_keys is not None:
            active_keys.add(key)
        when_m = _WHEN_RE.search(main)
        upd_m = _UPD_RE.search(block)
        sender = ""
        from_self = False
        msgs = parse_messages_block(lines)
        if msgs:
            inc = last_incoming(msgs, self_name)
            if inc:
                sender = inc["sender"]
                text = inc["text"] or text
                group = conv or (title if is_group and _norm(title) != _norm(sender) else "")
                title = f"{sender} in {group}" if group and _norm(group) != _norm(sender) else sender
            elif is_self_name(title, self_name) or all(not m.get("sender") for m in msgs):
                from_self = True
        elif is_self_name(title, self_name):
            from_self = True
        rec = {
            "id": key,
            "key": key,
            "pkg": pkg,
            "app": app_label(pkg),
            "title": title,
            "text": text,
            "sender": sender,
            "category": (cat_m.group(1) if cat_m else "").rstrip(")"),
            "group_summary": group_summary,
            "ongoing": "ONGOING_EVENT" in flags or "FOREGROUND_SERVICE" in flags,
            "from_self": from_self,
            "when": int(upd_m.group(1)) if upd_m else (int(when_m.group(1)) if when_m else 0),
            "source": "adb_dumpsys",
        }
        nid = rec["id"] or f"{pkg}|{title}|{text}"
        if nid in seen:
            continue
        if not is_speakable(rec):
            continue
        seen.add(nid)
        out.append(rec)
    return out


def fetch_android_snapshot() -> dict:
    """ADB-Dump der Statusleiste: {ok, items, active_keys}. Nichts wird gespeichert."""
    res: dict = {"ok": False, "items": [], "active_keys": set()}
    try:
        from nexus.phone_actions import _adb, select_device
    except Exception:
        return res
    dev = select_device(connect=False)
    if dev.get("status") != "OK" or not dev.get("serial"):
        return res
    dump = ""
    for args in (
        ["shell", "dumpsys", "notification", "--noredact"],
        ["shell", "dumpsys", "notification"],
    ):
        r = _adb(dev["serial"], args, 15)
        if r.get("ok") and r.get("out"):
            dump = r.get("out") or ""
            break
    if not dump or "NotificationRecord(" not in dump:
        return res
    keys: set = set()
    res["items"] = parse_android_notification_dump(dump, keys)
    res["active_keys"] = keys
    res["ok"] = True
    return res


def fetch_android_notifications() -> list[dict]:
    """Aktive Statusleisten-Eintraege per ADB (wird immer mit dem Listener-Store zusammengefuehrt)."""
    return fetch_android_snapshot().get("items") or []