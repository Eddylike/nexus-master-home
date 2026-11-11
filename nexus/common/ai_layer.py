"""NEXUS AI LAYER — thin model/provider abstraction (Phase 8).

Architecture:
  LOCAL MODEL (Ollama or future local backend)
    → NEXUS AI LAYER (this module: config + resolve + residency policy)
      → NEXUS Master Home (MasterController / chat engine)
        → Tools / Agents / Memory / Files / Device / Skills

No single model is baked into the architecture. Defaults are config/env only.
External KI (OpenAI) stays optional via providers.manager / openai_orchestrator.

VRAM / pull policy (Eddie + zipfeglatscha):
  - never_pull: NEVER call ollama pull without Eddie approval
  - on_demand_only / keep_alive=0: unload after each request (no permanent VRAM)
  - qwen3.5:9b is preferred when present, but only loaded on-demand
  - memory_fallback=lightest_present: if chat fails under memory pressure, use
    the smallest already-installed model — never pull
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

from nexus.common.config import Config
from nexus.common.paths import APEX, ensure_dirs

CONFIG_PATH = APEX / "ai_models.json"

# Defaults — swappable via file/env; not architectural hardcodes
DEFAULT_CONFIG: dict[str, Any] = {
    "provider": "local",
    "chat_model": "qwen3.5:9b",
    "chat_model_source": None,
    "num_ctx": 8192,
    "vision_model": "llava:latest",
    "roles": {},
    "role_fallbacks": {},
    "ollama_url": None,
    "keep_alive": "0",
    "unload_after_request": True,
    "never_pull": True,
    "on_demand_only": True,
    "memory_fallback": "lightest_present",
    "notes": (
        "keep_alive=0 → unload after request (no permanent VRAM). "
        "qwen3.5:9b on-demand only. NEVER ollama pull without Eddie. "
        "Memory pressure → lightest locally present model. "
        "Swap: UI / POST /api/ai/models / Env NEXUS_MODEL|NEXUS_VISION_MODEL|"
        "NEXUS_OLLAMA_URL|NEXUS_KEEP_ALIVE / edit ai_models.json."
    ),
}

_CONFIG_KEYS = tuple(DEFAULT_CONFIG.keys())


def _env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    m = (os.environ.get("NEXUS_MODEL") or "").strip()
    # 2026-10-28: UI-Auswahl (chat_model_source=ui) ist dauerhaft und schlaegt Env NEXUS_MODEL
    if m and str(out.get("chat_model_source") or "") != "ui":
        out["chat_model"] = m
    v = (os.environ.get("NEXUS_VISION_MODEL") or "").strip()
    if v:
        out["vision_model"] = v
    u = (os.environ.get("NEXUS_OLLAMA_URL") or "").strip()
    if u:
        out["ollama_url"] = u
    p = (os.environ.get("NEXUS_AI_PROVIDER") or "").strip().lower()
    if p in ("local", "openai"):
        out["provider"] = p
    ka = (os.environ.get("NEXUS_KEEP_ALIVE") or "").strip()
    if ka:
        out["keep_alive"] = ka
        out["unload_after_request"] = ka in ("0", "0s", "0m")
    return out


def load_config() -> dict[str, Any]:
    ensure_dirs()
    base = deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in _CONFIG_KEYS:
                    if k in data and data[k] is not None:
                        base[k] = data[k]
        except Exception:
            pass
    if not base.get("chat_model"):
        base["chat_model"] = getattr(Config, "PREFERRED_MODEL", None) or "qwen3.5:9b"
    if not base.get("vision_model"):
        base["vision_model"] = getattr(Config, "VISION_MODEL", None) or "llava:latest"
    if base.get("keep_alive") is None:
        base["keep_alive"] = getattr(Config, "OLLAMA_KEEP_ALIVE", None) or "0"
    return _env_overrides(base)


def save_config(updates: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist model/provider prefs. Env still wins at read time for NEXUS_* overrides."""
    ensure_dirs()
    raw = deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.is_file():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in _CONFIG_KEYS:
                    if k in data:
                        raw[k] = data[k]
        except Exception:
            pass
    if updates:
        for k, v in updates.items():
            if k not in DEFAULT_CONFIG:
                continue
            if k == "provider":
                pv = str(v or "local").strip().lower()
                raw["provider"] = pv if pv in ("local", "openai") else "local"
            elif k == "ollama_url":
                raw["ollama_url"] = (str(v).strip() if v else None) or None
            elif k in ("chat_model", "vision_model", "keep_alive", "memory_fallback", "notes"):
                if v is None and k in ("keep_alive", "memory_fallback", "notes"):
                    continue
                s = str(v).strip() if v is not None else ""
                if k in ("chat_model", "vision_model"):
                    if s:
                        raw[k] = s
                else:
                    raw[k] = s if s or k == "notes" else raw.get(k)
            elif k in ("unload_after_request", "never_pull", "on_demand_only"):
                raw[k] = bool(v)
            elif k == "chat_model_source":
                raw[k] = (str(v).strip() or None) if v is not None else None
    CONFIG_PATH.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return load_config()


def unified_num_ctx(cfg: dict[str, Any] | None = None) -> int:
    """2026-10-28 ctx: EIN num_ctx fuer alle NEXUS-Aufrufe (Wechsel = Modell-Reload 25-60 s)."""
    try:
        v = int((cfg or load_config()).get("num_ctx") or 8192)
    except Exception:
        v = 8192
    return max(2048, v)


def ollama_base_url(cfg: dict[str, Any] | None = None) -> str:
    c = cfg or load_config()
    return (c.get("ollama_url") or Config.OLLAMA_URL or "http://127.0.0.1:11434").rstrip("/")


def keep_alive_value(cfg: dict[str, Any] | None = None) -> Any:
    """Ollama keep_alive: 0 / '0' unloads immediately after the request."""
    c = cfg or load_config()
    if c.get("unload_after_request", True):
        ka = c.get("keep_alive")
        if ka is None or ka == "":
            return 0
        if isinstance(ka, str) and ka.isdigit():
            return int(ka)
        return ka
    return c.get("keep_alive") if c.get("keep_alive") not in (None, "") else "5m"


def never_pull(cfg: dict[str, Any] | None = None) -> bool:
    c = cfg or load_config()
    return bool(c.get("never_pull", True))


def _model_size_map(client: Any) -> dict[str, int]:
    """Best-effort name→size from tags_detailed or ping names only."""
    sizes: dict[str, int] = {}
    if client is None:
        return sizes
    if hasattr(client, "tags_detailed"):
        try:
            for m in client.tags_detailed() or []:
                name = m.get("name") or ""
                if name:
                    sizes[name] = int(m.get("size") or 0)
            return sizes
        except Exception:
            pass
    if hasattr(client, "ping"):
        try:
            st = client.ping()
            for n in st.get("models") or []:
                sizes[str(n)] = 0
        except Exception:
            pass
    return sizes


_NON_CHAT_MARKERS = ("llava", "bakllava", "moondream", "embed", "nomic-bert", "bge-", "minilm", "rerank")


def _is_chat_model(name: str) -> bool:
    """THINK-06/MH4D 2026-11-10: Embedding-/Vision-Modelle nie als Chat-Fallback waehlen."""
    low = str(name or "").lower()
    return not any(m in low for m in _NON_CHAT_MARKERS)


def lightest_present(client: Any) -> str | None:
    """Smallest already-installed model by size; never pulls."""
    sizes = _model_size_map(client)
    if not sizes:
        return None
    named = list(sizes.items())
    _chat = [(n, sz) for n, sz in named if _is_chat_model(n)]
    if _chat:
        named = _chat
    if all(sz == 0 for _, sz in named):
        chatish = [
            n for n, _ in named
            if "llava" not in n.lower()
            and "bakllava" not in n.lower()
            and "moondream" not in n.lower()
        ]
        pool = chatish or [n for n, _ in named]
        for pref in ("qwen3.5:9b", "qwen3.5:4b", "llama3.2"):
            for n in pool:
                if n == pref or n.startswith(pref):
                    return n
        return sorted(pool)[0] if pool else None
    return sorted(named, key=lambda x: (x[1], x[0]))[0][0]


def is_memory_pressure_error(err: str | None) -> bool:
    if not err:
        return False
    low = str(err).lower()
    keys = (
        "out of memory",
        "oom",
        "cuda",
        "insufficient memory",
        "not enough memory",
        "resource exhausted",
        "enomem",
        "failed to allocate",
        "memory pressure",
        "vram",
        "requires more system memory",
        "than is available",
    )
    return any(k in low for k in keys)


def resolve_chat_model(client: Any = None, override: str | None = None) -> str | None:
    """Pick chat model: per-request override → config preferred → client.pick_model (installed only)."""
    ov = (override or "").strip()
    if ov:
        if client is not None:
            client.model = ov
        return ov
    cfg = load_config()
    preferred = (cfg.get("chat_model") or Config.PREFERRED_MODEL or "").strip() or None
    if client is not None and hasattr(client, "pick_model"):
        return client.pick_model(preferred=preferred)
    return preferred


def resolve_role_model(role: str, client: Any = None, override: str | None = None) -> str | None:
    """F-Fusion 24.10.2026: Rollenbasierte Modellwahl (strong/fast/vision) nur unter INSTALLIERTEN Modellen.
    Reihenfolge: override -> cfg['roles'][role] -> cfg['role_fallbacks'][role] -> resolve_chat_model.
    Vision-Rolle nutzt die bestehende Vision-Aufloesung. Zieht nie ein Modell (never pull)."""
    ov = (override or "").strip()
    if ov:
        if client is not None:
            client.model = ov
        return ov
    role = (role or "").strip().lower()
    if role in ("vision", "image", "sehen"):
        return resolve_vision_model(client, None)
    cfg = load_config()
    roles = cfg.get("roles") or {}
    fallbacks = (cfg.get("role_fallbacks") or {}).get(role) or []
    installed = []
    if client is not None and hasattr(client, "ping"):
        try:
            installed = (client.ping() or {}).get("models") or []
        except Exception:
            installed = []

    def _match(name):
        if not name:
            return None
        for n in installed:          # exakt zuerst
            if n == name:
                return n
        for n in installed:          # dann Familien-Praefix (z.B. llava ohne Tag)
            if ":" not in name and n.split(":")[0] == name:
                return n
        return None

    wanted = roles.get(role)
    candidates = ([wanted] if wanted else []) + list(fallbacks)
    for c in candidates:
        hit = _match(c) if installed else c
        if hit:
            if client is not None:
                client.model = hit
            return hit
    # Keine Rolle passt -> normaler Chat-Default (installiert)
    return resolve_chat_model(client, None)


def resolve_vision_model(client: Any = None, override: str | None = None) -> str | None:
    ov = (override or "").strip()
    if ov:
        return ov
    cfg = load_config()
    preferred = (cfg.get("vision_model") or getattr(Config, "VISION_MODEL", None) or "llava").strip()
    if client is not None and hasattr(client, "has_vision"):
        found = client.has_vision()
        if found:
            st = client.ping() if hasattr(client, "ping") else {}
            names = st.get("models") or []
            for n in names:
                if n == preferred or n.startswith(preferred.split(":")[0]):
                    return n
            return found
    return preferred


def resolve_fallback_model(client: Any, failed_model: str | None = None) -> str | None:
    """Under memory pressure: lightest locally present model != failed; never pull."""
    cfg = load_config()
    mode = (cfg.get("memory_fallback") or "lightest_present").strip().lower()
    if mode in ("", "none", "off", "false"):
        return None
    light = lightest_present(client)
    if not light:
        return None
    if failed_model and light == failed_model:
        sizes = _model_size_map(client)
        others = [n for n in sizes if n != failed_model]
        if not others:
            return None
        chatish = [n for n in others if _is_chat_model(n)]
        if not chatish:
            return None
        pool = chatish
        if all((sizes.get(n) or 0) == 0 for n in pool):
            return sorted(pool)[0]
        return sorted(pool, key=lambda n: (sizes.get(n) or 0, n))[0]
    return light


def apply_to_client(client: Any) -> dict[str, Any]:
    """Bind local client to current AI-layer config (URL + preferred + keep_alive)."""
    cfg = load_config()
    url = ollama_base_url(cfg)
    if hasattr(client, "base_url"):
        client.base_url = url
    if hasattr(client, "keep_alive"):
        client.keep_alive = keep_alive_value(cfg)
    if hasattr(client, "never_pull"):
        client.never_pull = never_pull(cfg)
    model = resolve_chat_model(client, None)
    return {
        "provider": cfg.get("provider") or "local",
        "chat_model": model,
        "vision_model": cfg.get("vision_model"),
        "ollama_url": url,
        "keep_alive": keep_alive_value(cfg),
        "never_pull": never_pull(cfg),
        "on_demand_only": bool(cfg.get("on_demand_only", True)),
    }


def status(client: Any = None) -> dict[str, Any]:
    cfg = load_config()
    url = ollama_base_url(cfg)
    out: dict[str, Any] = {
        "layer": "nexus_ai_layer",
        "provider": cfg.get("provider") or "local",
        "chat_model_preferred": cfg.get("chat_model"),
        "chat_model_source": cfg.get("chat_model_source") or "default",
        "chat_model_label": "Hermes (" + str(cfg.get("chat_model") or "?") + ")",
        "roles": cfg.get("roles") or {},
        "role_fallbacks": cfg.get("role_fallbacks") or {},
        "vision_model_preferred": cfg.get("vision_model"),
        "ollama_url": url,
        "keep_alive": keep_alive_value(cfg),
        "unload_after_request": bool(cfg.get("unload_after_request", True)),
        "never_pull": never_pull(cfg),
        "on_demand_only": bool(cfg.get("on_demand_only", True)),
        "memory_fallback": cfg.get("memory_fallback") or "lightest_present",
        "config_path": str(CONFIG_PATH),
        "external_optional": True,
        "paid_core_deps": False,
        "swap_howto": (
            "1) UI Einstellungen → Chat-Modell → Übernehmen "
            "2) POST /api/ai/models {\"chat_model\":\"...\",\"vision_model\":\"...\",\"keep_alive\":\"0\"} "
            "3) Env NEXUS_MODEL / NEXUS_VISION_MODEL / NEXUS_OLLAMA_URL / NEXUS_KEEP_ALIVE "
            "4) Edit workspace/apex/ai_models.json "
            "NEVER ollama pull without Eddie. Models unload after request (keep_alive=0)."
        ),
    }
    if client is not None and hasattr(client, "ping"):
        ping = client.ping()
        out["reachable"] = ping.get("reachable")
        out["models_installed"] = ping.get("models") or []
        out["chat_model_active"] = resolve_chat_model(client)
        out["vision_model_active"] = resolve_vision_model(client)
        out["lightest_present"] = lightest_present(client)
        out["error"] = ping.get("error")
        if hasattr(client, "loaded_models"):
            try:
                out["loaded_in_vram"] = client.loaded_models()
            except Exception:
                out["loaded_in_vram"] = None
    return out
