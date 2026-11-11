"""Sprechzimmer: Gespraechsgedaechtnis pro Agent (SZ-01).

Speicher: <base_dir>/memory/<agent>.jsonl. Ein Agent liest NUR seinen eigenen Speicher.
"""
from __future__ import annotations

from pathlib import Path
import json


def memory_path(agent: str, base_dir: Path) -> Path:
    """Pfad der JSONL-Datei des Agenten (Agentname dateisicher: Leerzeichen -> '_')."""
    agent_safe = agent.replace(" ", "_")
    return base_dir / "memory" / f"{agent_safe}.jsonl"


def append_memory(agent: str, entry: dict, base_dir: Path) -> None:
    """Haengt entry als eine JSON-Zeile an den Speicher von agent an (Ordner anlegen)."""
    path = memory_path(agent, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        raise RuntimeError("Schreibzugriff auf Speicher verweigert (Sicherheitsrichtlinie)")


def read_memory(agent: str, requester: str, base_dir: Path) -> list[dict]:
    """Liest den Speicher von agent. requester != agent -> PermissionError. Fehlt die Datei -> []."""
    if agent != requester:
        raise PermissionError("Fremder Agent darf Speicher nicht lesen")

    target_path = memory_path(agent, base_dir)

    if not target_path.exists():
        return []

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()
            if not content.strip():
                return []
            return [json.loads(line) for line in content.splitlines() if line.strip()]
    except (FileNotFoundError, PermissionError):
        raise
