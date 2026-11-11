from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.common.atomic import atomic_write_json, file_lock
from nexus.common.paths import CHAT_DIR, ensure_dirs

SHARED_DIR = CHAT_DIR / "shared_room"
_ROOM_LOCK = threading.RLock()
_ALLOWED_SENDERS = {"human", "chatgpt", "nexus", "local_ai", "agent", "system"}
_AI_SENDERS = {"chatgpt", "nexus", "local_ai", "agent"}

# NEXUS OS Teilnehmer (logisch). Namen werden auf die bestehenden kanonischen
# sender_types normalisiert - die Loop-Guards bleiben unveraendert.
PARTICIPANTS = (
    {"name": "EDDY", "sender_type": "human", "role": "operator", "optional": False},
    {"name": "NEXUS", "sender_type": "nexus", "role": "lead_agent", "optional": False},
    {"name": "HERMES", "sender_type": "agent", "role": "internal_work_core", "optional": False},
    {"name": "NEXUS OS", "sender_type": "system", "role": "system", "optional": False},
    {"name": "PHONE LOCAL AI", "sender_type": "local_ai", "role": "provider", "optional": True, "slot": "PHONE_LOCAL_AI"},
    {"name": "WOZIPC LOCAL AI", "sender_type": "local_ai", "role": "provider", "optional": False, "slot": "WOZIPC_LOCAL_AI"},
    {"name": "CHATGPT OPTIONAL", "sender_type": "chatgpt", "role": "provider", "optional": True, "slot": "CHATGPT_OPTIONAL"},
)

_SENDER_ALIASES = {
    "eddy": "human",
    "human": "human",
    "nexus": "nexus",
    "nexus os": "system",
    "system": "system",
    "phone local ai": "local_ai",
    "phone_local_ai": "local_ai",
    "wozipc local ai": "local_ai",
    "wozipc_local_ai": "local_ai",
    "local ai": "local_ai",
    "local_ai": "local_ai",
    "chatgpt optional": "chatgpt",
    "chatgpt_optional": "chatgpt",
    "chatgpt": "chatgpt",
    "agent": "agent",
    "hermes": "agent",
}


def normalize_sender(sender_type: str | None) -> str:
    """Map a NEXUS OS participant name onto a canonical sender_type."""
    raw = (sender_type or "").strip().lower()
    if raw in _ALLOWED_SENDERS:
        return raw
    return _SENDER_ALIASES.get(raw, raw)


def participants() -> list[dict[str, Any]]:
    return [dict(p) for p in PARTICIPANTS]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SharedRoom:
    """Persistent NEXUS ROOM transcript with bounded AI-to-AI routing guards."""

    def __init__(self, max_rounds: int | None = None) -> None:
        ensure_dirs()
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        configured = int(os.environ.get("NEXUS_AI_MAX_ROUNDS") or max_rounds or 4)
        self.max_rounds = max(1, min(configured, 8))
        self._lock = _ROOM_LOCK

    def _path(self, session_id: str) -> Path:
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
        return SHARED_DIR / f"{safe or 'default'}.json"

    def load(self, session_id: str) -> dict[str, Any]:
        fp = self._path(session_id)
        if fp.is_file():
            try:
                return json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError("SHARED_ROOM_UNREADABLE") from exc
        return {"session_id": session_id, "created_at": _now(), "updated_at": _now(), "messages": []}

    def _save(self, room: dict[str, Any]) -> None:
        room["updated_at"] = _now()
        # Eindeutige tmp-Datei + Replace-Wiederholung (WinError 5 bei geöffnetem Ziel).
        atomic_write_json(self._path(room["session_id"]), room)

    @staticmethod
    def _hash(sender_type: str, target: str | None, content: str) -> str:
        raw = f"{sender_type}|{target or ''}|{content.strip()}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def append(self, *, session_id: str, sender_type: str, sender_name: str, content: str,
               source: str, target: str | None = None, role: str = "assistant", round_no: int = 0,
               parent_message_id: str | None = None, task_id: str | None = None,
               approval_id: str | None = None, agent: str | None = None,
               provider: str | None = None, metadata: dict[str, Any] | None = None,
               mode: str = "user") -> dict[str, Any]:
        requested = (sender_type or "").strip().lower()
        sender_type = normalize_sender(sender_type)
        target_norm = (target or "").strip().lower() or None
        if target_norm:
            target_norm = normalize_sender(target_norm)
        text = (content or "").strip()
        if sender_type not in _ALLOWED_SENDERS:
            return {"success": False, "blocked": "invalid_route"}
        if not text:
            return {"success": False, "blocked": "empty"}
        if sender_type in _AI_SENDERS and target_norm == sender_type:
            return {"success": False, "blocked": "same_sender_loop"}
        if sender_type in _AI_SENDERS and target_norm in _AI_SENDERS and mode != "collaboration":
            return {"success": False, "blocked": "invalid_route"}
        if sender_type in _AI_SENDERS and target_norm in _AI_SENDERS and round_no > self.max_rounds:
            return {"success": False, "blocked": "max_rounds", "max_rounds": self.max_rounds}
        scope = str((metadata or {}).get("run_id") or "") if source == "nexus_collaboration" else ""
        digest = self._hash(sender_type, target_norm, scope + "|" + text)
        with self._lock, file_lock(self._path(session_id)):
            room = self.load(session_id)
            if not room.get("messages"):
                room["messages"] = []
            recent = room["messages"][-64:]
            if any(m.get("message_hash") == digest for m in recent):
                return {"success": False, "blocked": "duplicate"}
            # Status-Logik fuer Agenten-Raum: FAIL/PARTIAL/BLOCKED bei Fehlern
            rec = {
                "message_id": "MSG-" + uuid.uuid4().hex[:16], "session_id": session_id,
                "timestamp": _now(), "sender_type": sender_type, "sender_name": sender_name,
                "sender_requested": requested,
                "source": source, "target": target_norm, "role": role, "content": text,
                "round": int(round_no or 0), "task_id": task_id,
                "parent_message_id": parent_message_id, "approval_id": approval_id,
                "agent": agent, "provider": provider, "metadata": metadata or {},
                "message_hash": digest,
            }
            room.setdefault("messages", []).append(rec)
            self._save(room)
            return {"success": True, "message": rec, "max_rounds": self.max_rounds}

    def collaboration(self, session_id: str, value=None):
        with self._lock, file_lock(self._path(session_id)):
            room = self.load(session_id)
            if value is not None:
                room["collaboration"] = value
                self._save(room)
            return room.get("collaboration")

    def collaboration_run(self, session_id: str, key: str, value=None):
        """Zustand genau eines Auftrags (Schlüssel = Auftrags-Hash) lesen oder schreiben.

        Jeder Auftrag hat seinen eigenen Eintrag; ein neuer oder paralleler Auftrag
        überschreibt nie den Zustand eines anderen. "collaboration" bleibt nur der Zeiger
        auf den zuletzt aktiven Lauf für Oberfläche und Statusanzeige.
        """
        with self._lock, file_lock(self._path(session_id)):
            room = self.load(session_id)
            runs = room.get("collaboration_runs")
            if not isinstance(runs, dict):
                runs = {}
                legacy = room.get("collaboration")
                if isinstance(legacy, dict) and legacy.get("key"):
                    runs[legacy["key"]] = legacy
            if value is not None:
                runs[key] = value
                room["collaboration_runs"] = runs
                room["collaboration"] = value
                self._save(room)
            return runs.get(key)

    def status(self, session_id: str) -> dict[str, Any]:
        room = self.load(session_id)
        msgs = room.get("messages", [])
        present = sorted({str(m.get("sender_type") or "") for m in msgs if m.get("sender_type")})
        return {"success": True, "session_id": session_id, "message_count": len(msgs),
                "max_rounds": self.max_rounds, "last_message": msgs[-1] if msgs else None,
                "collaboration": {k: (room.get("collaboration") or {}).get(k) for k in ("run_id", "status", "phase", "error")},
                "participants": participants(),
                "participants_present": present,
                "chatgpt_optional_missing_is_ok": True}
