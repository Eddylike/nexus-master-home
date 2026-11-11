"""PC-Seite der Live-Talk-Handy-Bruecke (App 1.1.0): Nachrichten, Foto, Outbox.

Die Android-App wird hier nicht veraendert. Ereignisse kommen als WS-Typen
phone_notification / phone_file / phone_result; Kommandos gehen als phone_cmd
an die Seite (NexusLiveTalk / NexusPhone).
"""
from __future__ import annotations

import base64
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
NOTIF_TTL_S = 10 * 60


def _norm(s: str) -> str:
    t = str(s or "").strip().lower()
    for a, b in (("\u00e4", "ae"), ("\u00f6", "oe"), ("\u00fc", "ue"), ("\u00df", "ss")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t)


class PhoneLink:
    """Pro Sitzung: Benachrichtigungs-Cache, ausstehende Antwort, letztes Foto."""

    def __init__(self, send_cmd: Callable[..., Any] | None = None):
        self._send_cmd = send_cmd
        self.notifications: list[dict] = []
        self.pending_reply: dict | None = None
        self.last_image: dict | None = None
        self._seen_files: set[str] = set()
        self._result: dict | None = None
        self._wait = None

    def remember_notification(self, data: dict | None) -> None:
        if not isinstance(data, dict):
            return
        rec = dict(data)
        rec["_ts"] = time.time()
        nid = str(rec.get("id") or rec.get("key") or "")
        if nid:
            self.notifications = [n for n in self.notifications if str(n.get("id") or n.get("key") or "") != nid]
        self.notifications.insert(0, rec)
        self.notifications = self.fresh_notifications()

    def fresh_notifications(self) -> list[dict]:
        now = time.time()
        self.notifications = [n for n in self.notifications if now - float(n.get("_ts") or now) <= NOTIF_TTL_S]
        return list(self.notifications)

    def remember_file(self, rec: dict | None) -> str | None:
        """Merkt sich ein eingehendes phone_file. Gibt Ansagetext oder None (Duplikat)."""
        if not isinstance(rec, dict):
            return None
        key = str(rec.get("saved_as") or rec.get("path") or rec.get("name") or "")
        if key and key in self._seen_files:
            return None
        if key:
            self._seen_files.add(key)
        kind = rec.get("kind") or ""
        path = rec.get("path") or rec.get("rel")
        if kind == "image" or (not kind and str(path or "").lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"))):
            self.last_image = rec
            name = rec.get("name") or rec.get("saved_as") or "Foto"
            return "Foto erhalten."
        return "Datei erhalten."

    def find_notification(self, who: str) -> dict | None:
        q = _norm(who)
        if not q:
            return None
        for n in self.fresh_notifications():
            blob = _norm(" ".join(str(n.get(k) or "") for k in ("title", "app", "text", "pkg")))
            if q in blob:
                return n
        return None

    def on_result(self, msg: dict) -> None:
        self._result = msg
        ev = self._wait
        if ev is not None:
            ev.set()

    def request(self, cmd: str, timeout: float = 4.0, **payload) -> dict | None:
        """Sendet phone_cmd und wartet auf phone_result (gleicher cmd)."""
        if not self._send_cmd:
            return None
        import threading
        ev = threading.Event()
        self._result = None
        self._wait = ev
        try:
            self._send_cmd(cmd=cmd, **payload)
            if ev.wait(timeout):
                return self._result
            return None
        finally:
            self._wait = None

    def notifications_live(self) -> list[dict]:
        r = self.request("get_notifications")
        if not r:
            return self.fresh_notifications()
        items = r.get("notifications") or r.get("items") or r.get("data") or r.get("result") or []
        if isinstance(items, dict):
            items = items.get("notifications") or items.get("items") or []
        if not isinstance(items, list):
            items = []
        now = time.time()
        out = []
        for it in items:
            if isinstance(it, dict):
                it = dict(it)
                it.setdefault("_ts", now)
                out.append(it)
                self.remember_notification(it)
        return out or self.fresh_notifications()


def speak_notifications(items: list[dict]) -> str:
    items = [n for n in items if isinstance(n, dict)]
    if not items:
        return "Ich habe gerade keine Nachrichten vom Handy."
    parts = []
    for n in items[:5]:
        who = (n.get("title") or n.get("app") or "Nachricht").strip()
        text = (n.get("text") or "").strip()
        if text:
            parts.append(f"{who} schreibt: {text}")
        else:
            parts.append(f"Nachricht von {who}.")
    extra = f" Es liegen noch {len(items) - 5} weitere vor." if len(items) > 5 else ""
    if len(parts) == 1:
        return parts[0] + extra
    return " ".join(f"{i+1}. {p}" for i, p in enumerate(parts)) + extra


def describe_image(path: str | Path | None) -> dict:
    """Kurze Bildbeschreibung mit lokalem llava, ohne Modell-Download."""
    if not path:
        return {"success": False, "spoken": "Ich habe gerade kein Foto zum Beschreiben."}
    p = Path(str(path))
    if not p.is_file():
        rel = ROOT / str(path)
        p = rel if rel.is_file() else p
    if not p.is_file():
        return {"success": False, "spoken": "Das Foto liegt nicht mehr vor."}
    try:
        if p.resolve().drive.upper() == "C:":
            return {"success": False, "spoken": "Die Datei liegt auf einem gesperrten Laufwerk."}
    except Exception:
        pass
    model = _llava_name()
    if not model:
        return {"success": False, "spoken": "Ein Bildmodell ist lokal nicht verfuegbar, deshalb beschreibe ich das Foto nicht."}
    try:
        payload = json.dumps({
            "model": model,
            "prompt": "Beschreibe dieses Foto kurz auf Deutsch in ein bis zwei Saetzen, sachlich. Erfinde nichts.",
            "images": [base64.b64encode(p.read_bytes()).decode("ascii")],
            "stream": False,
        }).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        text = re.sub(r"\s+", " ", str(data.get("response") or "")).strip()
        if not text:
            return {"success": False, "spoken": "Das Bildmodell hat keine Beschreibung geliefert.", "model": model}
        return {"success": True, "spoken": text, "model": model}
    except Exception:
        return {"success": False, "spoken": "Ich komme gerade nicht an das Bildmodell.", "model": model}


def _llava_name() -> str | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read().decode("utf-8", "replace"))
        names = [str(m.get("name") or "") for m in (tags.get("models") or [])]
        return next((n for n in names if n.lower().startswith("llava")), None)
    except Exception:
        return None


def find_and_stage_file(name: str) -> dict:
    """Sucht eine Datei auf G: (Workspace zuerst) und legt sie in die Live-Talk-Outbox."""
    from nexus.mobile_inbox import put_outbox

    raw = str(name or "").strip().strip("\"'")
    raw = re.sub(r"^(die|den|das|meine?n?)\s+", "", raw, flags=re.I).strip()
    if not raw:
        return {"success": False, "reply": "Welche Datei soll aufs Handy?"}
    found = _find_file(raw)
    if not found:
        return {"success": False, "reply": f"Die Datei {raw} habe ich auf dem PC nicht gefunden."}
    return put_outbox(found)


def _find_file(raw: str) -> Path | None:
    p = Path(raw)
    try:
        if p.is_file():
            if p.resolve().drive.upper() == "C:":
                return None
            return p
    except Exception:
        pass
    rel = ROOT / raw
    try:
        if rel.is_file() and rel.resolve().drive.upper() != "C:":
            return rel
    except Exception:
        pass
    base = Path(str(raw).replace("\\", "/")).name
    if not base or base in {".", ".."}:
        return None
    roots = [
        ROOT / "workspace" / "tmp",
        ROOT / "workspace" / "inbox" / "mobile" / "outbox",
        ROOT / "workspace" / "inbox" / "mobile",
        ROOT / "workspace" / "reports",
        ROOT / "workspace",
    ]
    low = base.lower()
    exact, fuzzy = [], []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for cand in root.iterdir():
                if not cand.is_file():
                    continue
                try:
                    if cand.resolve().drive.upper() == "C:":
                        continue
                except Exception:
                    continue
                if cand.name.lower() == low:
                    exact.append(cand)
                elif low in cand.name.lower():
                    fuzzy.append(cand)
        except Exception:
            continue
        if exact:
            break
    return exact[0] if exact else (fuzzy[0] if fuzzy else None)
