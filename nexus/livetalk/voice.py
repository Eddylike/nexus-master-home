"""LT-06: Eine einheitliche Piper-Stimme fuer NEXUS, NEXUS GPT, Hermes und Aether (lokal, CPU)."""
from __future__ import annotations
from pathlib import Path
from typing import Dict, Union
import json

AGENTS = ("NEXUS", "NEXUS GPT", "Hermes", "Aether")
DEFAULT_MODEL = r"G:\meine-ki\models\piper\de_DE-thorsten-high.onnx"


def voice_for(agent: str, cfg: dict) -> dict:
    """Erzeugt die konfigurierte Stimmdatenstruktur fuer einen Agenten.

    Waegt den Agent-Namen gegen die erlaubte Liste AGENTS.
    Fuehrt eine Leerpruefung der Modell-Pfad-Variable durch um ValueError zu werfen.
    Konstruiert das Ausgabe-Dict mit engine, model und config (mit .json Extension).
    """
    if agent not in AGENTS:
        raise KeyError(f"Unbekannter Agent: {agent}")

    if not cfg.get("model"):
        raise ValueError("Modell-Pfad darf nicht leer oder fehlen.")

    base_model = cfg["model"]
    config_path = base_model + ".json"
    return {"engine": "piper", "model": base_model, "config": config_path, "language": "de"}


def load_cfg(path: Path) -> dict:
    """Laesst Konfigurations-JSON aus dem angegebenen Pfad.

    Fuerht Existenzueberpruefung des Dateipfades durch.
    Fuerholt JSON-Inhalt bei Vorliegen der Datei.
    Falls Datei fehlt oder Inhalt ungultig ist, nutzt das Standard-Modell und default Engine.
    """
    if not path.exists():
        return {"engine": "piper", "model": DEFAULT_MODEL, "language": "de"}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "engine" in data and "model" in data:
            return {"engine": data["engine"], "model": data["model"], "language": "de"}
    except Exception:
        pass

    return {"engine": "piper", "model": DEFAULT_MODEL, "language": "de"}
