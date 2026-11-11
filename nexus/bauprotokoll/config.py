"""Zentrale Konfiguration (config.json) fuer das Bauprotokoll."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from nexus.common.paths import ROOT

DEFAULT_CONFIG = {
    "version": 1,
    "ports": {"master_home": 8299, "bridge": 8300, "bauprotokoll": 8310},
    "ollama": {"url": "http://127.0.0.1:11434", "keep_alive": "0", "timeout_s": 120},
    "models": {
        "router": "qwen2.5:3b",
        "generalist": "qwen3.5:9b",
        "coder": "qwen2.5-coder:7b",
        "embeddings": "nomic-embed-text",
    },
    "paths": {
        "workspace": "workspace",
        "state": "state/bauprotokoll",
        "skills": "skills",
        "logs": "logs",
        "docs": "docs",
    },
    "whitelist": ["python", "python3", "pytest", "git", "node", "npm", "ruff", "ls", "dir"],
    "whitelist_modules": ["pytest", "unittest", "py_compile", "compileall", "json.tool"],
    "budgets": {
        "max_steps": 7,
        "max_attempts": 3,
        "step_timeout_s": 120,
        "task_timeout_s": 900,
        "max_tokens": 8000,
    },
    "memory": {"db": "state/bauprotokoll/memory.db", "top_k": 5, "embedding_dim": 64},
    "selfbuild": {
        "interval_s": 900,
        "staging": "staging",
        "main": "main",
        "stop_file": "STOP",
        "successes_per_stage": 20,
        "observe_runs": 3,
        "stage_dirs": [
            ["docs/*", "tests/*"],
            ["skills/*"],
            ["nexus/bauprotokoll/agents.py", "nexus/bauprotokoll/brain.py"],
            ["nexus/*"],
        ],
    },
    "health": {
        "timeout_s": 2,
        "services": {
            "master_home": "http://127.0.0.1:8299/health",
            "bridge": "http://127.0.0.1:8299/api/bridge/health",
            "gateway": "http://127.0.0.1:8300/api/bridge/health",
            "ollama": "http://127.0.0.1:11434/api/tags",
        },
    },
    "hardening": {
        "backup_keep": 14,
        "log_max_bytes": 5242880,
        "log_keep": 5,
        "max_cpu_percent": 90,
        "max_ram_percent": 90,
        "max_vram_percent": 95,
    },
    "forbidden_paths": [
        "validator/*", "*aether*", "config.json", "*whitelist*",
        "rollback*", "*logging_core*", ".git/*", ".env", "*.env", "stop", "STOP",
    ],
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    def __init__(self, root: str | Path | None = None, path: str | Path | None = None):
        self.root = Path(root or ROOT).resolve()
        self.path = Path(path) if path else self.root / "config.json"
        data = {}
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8"))
        self.data = _deep_merge(DEFAULT_CONFIG, data)

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path_for(self, dotted: str) -> Path:
        value = self.get(dotted)
        if not value:
            raise KeyError(f"Pfad nicht in Config: {dotted}")
        p = Path(value)
        return p if p.is_absolute() else (self.root / p)

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return self.path

    def ensure_dirs(self) -> dict[str, Path]:
        dirs = {}
        for key in ("state", "logs", "workspace", "skills"):
            p = self.path_for(f"paths.{key}")
            p.mkdir(parents=True, exist_ok=True)
            dirs[key] = p
        return dirs

    @property
    def state_dir(self) -> Path:
        return self.path_for("paths.state")

    @property
    def logs_dir(self) -> Path:
        return self.path_for("paths.logs")

    @property
    def skills_dir(self) -> Path:
        return self.path_for("paths.skills")

    @property
    def workspace_dir(self) -> Path:
        return self.path_for("paths.workspace")

    def resolve(self, rel: str | Path) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p)


def load_config(root: str | Path | None = None) -> Config:
    return Config(root=root)


def write_default_config(root: str | Path | None = None) -> Path:
    root = Path(root or ROOT)
    path = root / "config.json"
    if path.is_file():
        return path
    cfg = Config(root=root, path=path)
    return cfg.save()


def rel_to_root(path: str | Path, root: str | Path | None = None) -> str:
    root = Path(root or ROOT).resolve()
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return Path(path).resolve().as_posix()


def env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}
