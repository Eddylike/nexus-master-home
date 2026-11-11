"""Sprechzimmer: Sitzungsmanager (SZ-03). Speicher: <base_dir>/sessions/<id>.json (nur lokal)."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _session_path(session_id: str, base_dir: Path) -> Path:
    sid = _SAFE_ID.sub("", str(session_id or ""))
    return base_dir / "sessions" / f"{sid}.json"


def create_session(participants: list[str], base_dir: Path, title: str = "") -> dict:
    """Erstellt eine neue Session mit allen notwendigen Metadaten und persistiert sie sicher lokal."""

    # 1. Generierung der Session-ID (formatierte Zeit + UUID)
    timestamp_str = datetime.now().strftime("%Y%m%d-%H%M%S-")
    unique_id = "SZ-" + timestamp_str + uuid.uuid4().hex[:6]

    # 2. Erstellung des Dicts für die Session
    session: dict = {
        "id": unique_id,
        "title": str(title) if title else "",
        "participants": list(participants),
        "messages": [],
        "status": "aktiv",
        "paused": False,
        "interrupt": False,
        "created": datetime.now().isoformat()
    }

    # 3. Sicherstellung der Ziel-Pfad-Struktur
    dir_path = base_dir / "sessions"

    # Erstellen des Ordners 'sessions', falls nicht vorhanden (lokal)
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        if "permission denied" in str(e).lower() or "read-only file system" in str(e).lower():
            raise IOError(f"Fehler beim Erstellen des Session-Verzeichnisses (Fehlende Schreibrechte): {e}") from e
        raise

    # 4. Persistenzspeicherung mit Backup-Logik vorab
    current_file_path = dir_path / f"{unique_id}.json"

    try:
        content_str = json.dumps(session, ensure_ascii=False)

        # Schreibzugriff auf das Zielfile
        with open(current_file_path, "w", encoding="utf-8") as f:
            f.write(content_str)

    except FileNotFoundError:
        # Sollte passieren, wenn der Elternteil existiert, aber das File nicht (hier unwahrscheinlich, da wir den Ordner machen).
        # Falls der Pfad ungültig ist.
        raise ValueError(f"Ungültiger Pfad oder Schreibzugriff verweigert für: {current_file_path}") from None

    except PermissionError:
        raise ValueError("Schreibrechte für den Speicherort fehlen.") from None

    except OSError as e:
        # Fallback für andere Systemefehler
        raise IOError(f"Fehler beim Speichern der Session-Datei: {e}") from e

    return session


def add_message(session: dict, speaker: str, text: str) -> dict:
    message = {"speaker": str(speaker), "text": str(text), "ts": _now()}
    session.setdefault("messages", []).append(message)
    return message


def save_session(session: dict, base_dir: Path) -> Path:
    path = _session_path(session["id"], base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_session(session_id: str, base_dir: Path) -> dict:
    path = _session_path(session_id, base_dir)
    if not path.is_file():
        raise FileNotFoundError(session_id)
    return json.loads(path.read_text(encoding="utf-8"))


def list_sessions(base_dir: Path) -> list[dict]:
    folder = base_dir / "sessions"
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"id": data.get("id") or path.stem, "title": data.get("title") or "",
                    "status": data.get("status") or "unbekannt"})
    return out


def delete_session(session_id: str, base_dir: Path) -> bool:
    path = _session_path(session_id, base_dir)
    if not path.is_file():
        return False
    path.unlink()
    return True


def resume_session(session_id: str, base_dir: Path) -> dict:
    session = load_session(session_id, base_dir)
    session["status"] = "aktiv"
    session["paused"] = False
    session["interrupt"] = False
    save_session(session, base_dir)
    return session
