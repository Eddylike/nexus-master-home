"""OMEGA Layer 1: Pfade, Feature-Flags und Secret-Zugriff.

Keine Secrets im Code. Der Kern funktioniert ohne Zusatzpakete; alle Pfade
lassen sich ueber Umgebungsvariablen isolieren (Tests, CI).
"""
from __future__ import annotations

import os
from pathlib import Path

from nexus.common.paths import ROOT, WORKSPACE

OMEGA_VERSION = "1.0.0"
AGENT_NAME = "OMEGA"
OMEGA_MD = ROOT / "OMEGA.md"


def home() -> Path:
    raw = (os.environ.get("OMEGA_HOME") or "").strip()
    base = Path(raw).expanduser() if raw else (WORKSPACE / "omega")
    base.mkdir(parents=True, exist_ok=True)
    return base.resolve()


def db_path() -> Path:
    raw = (os.environ.get("OMEGA_DB") or "").strip()
    p = Path(raw).expanduser().resolve() if raw else (home() / "omega.db")
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def sandbox_dir() -> Path:
    d = home() / "sandbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def plugins_dir() -> Path:
    d = home() / "plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def secrets_file() -> Path:
    p = WORKSPACE / "secrets" / "omega.env"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            out[key] = value
    return out


def secrets() -> dict[str, str]:
    """Env gewinnt, Datei fuellt fehlende Werte. Nie loggen."""
    data = _read_env_file(secrets_file())
    for key, value in os.environ.items():
        if key.startswith("OMEGA_") and value:
            data[key] = value
    return data


def secret(name: str, default: str = "") -> str:
    return str(secrets().get(name) or default).strip()


def flag(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def hitl_mode() -> str:
    mode = (os.environ.get("OMEGA_HITL") or "confirm").strip().lower()
    return mode if mode in ("confirm", "interactive", "allow") else "confirm"


def allow_local_exec() -> bool:
    return flag("OMEGA_ALLOW_LOCAL_EXEC", False)


def docker_image() -> str:
    return (os.environ.get("OMEGA_DOCKER_IMAGE") or "python:3.12-alpine").strip()


def webhook_allowlist() -> set[str]:
    raw = (os.environ.get("OMEGA_WEBHOOK_ALLOWLIST") or "").strip()
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def search_api_key() -> str:
    return secret("OMEGA_SEARCH_API_KEY")
