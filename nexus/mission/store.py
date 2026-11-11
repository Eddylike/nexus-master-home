"""Persistenz und gemeinsames Gedaechtnis fuer Missionen.

Jede Mission ist eine JSON-Datei unter workspace/missions. Das gemeinsame
Gedaechtnis ist append-only (JSONL) und speichert Herkunft sowie eine
Zugriffsklasse (public/internal/sensitive).
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

from nexus.common.paths import WORKSPACE

BASE = WORKSPACE / "missions"
SENSITIVE_RX = re.compile(
    r"(?i)(passwort|password|token|secret|api[_-]?key|privat|persoenlich|persönlich|adresse|telefon|konto|iban)", re.I
)
_LOCK = threading.RLock()


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(value or ""))


class MissionStore:
    def __init__(self, base: Path = BASE) -> None:
        self.base = Path(base)
        self.base.mkdir(parents=True, exist_ok=True)
        self.memory_file = self.base / "memory.jsonl"
        self.events_file = self.base / "events.jsonl"

    def _path(self, mission_id: str) -> Path:
        return self.base / f"{_safe(mission_id)}.json"

    def save(self, mission: dict) -> dict:
        mission["updated"] = now()
        path = self._path(mission["id"])
        tmp = path.with_suffix(".tmp")
        with _LOCK:
            tmp.write_text(json.dumps(mission, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            tmp.replace(path)
        return mission

    def get(self, mission_id: str) -> dict | None:
        path = self._path(mission_id)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def list(self, limit: int = 20) -> list[dict]:
        files = sorted(self.base.glob("MS-*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        out = []
        for path in files[: max(1, min(int(limit), 100))]:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({k: data.get(k) for k in
                        ("id", "goal", "status", "stage", "created", "updated", "final")})
        return out

    def event(self, mission: dict, actor: str, kind: str, text: str, data: dict | None = None) -> dict:
        rec = {"ts": now(), "mission_id": mission.get("id"), "actor": actor, "kind": kind,
               "text": str(text)[:2000], "data": data or {}}
        mission.setdefault("trace", []).append(rec)
        with _LOCK, self.events_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec

    @staticmethod
    def classify(text: str, requested: str | None = None) -> str:
        level = str(requested or "").lower()
        if level in ("public", "internal", "sensitive"):
            return level
        return "sensitive" if SENSITIVE_RX.search(str(text or "")) else "internal"

    def remember(self, mission_id: str, agent: str, text: str, *, evidence: str = "",
                 classification: str | None = None) -> dict:
        entry = {"id": "MEM-" + uuid.uuid4().hex[:10], "mission_id": mission_id, "agent": agent,
                 "text": str(text)[:8000], "evidence": str(evidence)[:8000],
                 "classification": self.classify(text, classification), "created": now()}
        with _LOCK, self.memory_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry

    def recall(self, mission_id: str | None, query: str = "", requester: str = "NEXUS",
               limit: int = 20) -> list[dict]:
        out: list[dict] = []
        if not self.memory_file.is_file():
            return out
        words = {w for w in re.findall(r"[A-Za-z0-9\u00e4\u00f6\u00fc\u00df]{3,}", str(query or "").lower())}
        for line in self.memory_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if mission_id and entry.get("mission_id") != mission_id:
                continue
            if entry.get("classification") == "sensitive" and requester not in ("NEXUS", "Aether") \
                    and entry.get("agent") != requester:
                continue
            if words and not (words & set(re.findall(r"[A-Za-z0-9\u00e4\u00f6\u00fc\u00df]{3,}",
                                                   str(entry.get("text", "")).lower()))):
                continue
            out.append(entry)
        return out[-max(1, min(int(limit), 100)):]
