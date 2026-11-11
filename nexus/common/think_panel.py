"""THINK-01..06 (2026-11-10): Denkprozess-Fenster fuer Master Home.

- Globaler Schalter (persistiert in workspace/chat/think_settings.json).
- Pro Anfrage gesetzter Think-Override (ContextVar), den ollama_client._think() liest.
  Ohne Override bleibt das alte Verhalten (Umgebungsvariable NEXUS_OLLAMA_THINK, Standard aus).
- <think>...</think>-Tags in Modellantworten werden erkannt und in den Denkprozess verschoben
  (auch im Stream, tag-uebergreifend ueber Chunk-Grenzen).
Nur lokal, keine Cloud, keine Passwoerter.
"""
from __future__ import annotations

import contextvars
import json
import re
from datetime import datetime
from pathlib import Path

try:
    from nexus.common.paths import CHAT_DIR
except Exception:  # pragma: no cover - Notfall fuer isolierte Tests
    CHAT_DIR = Path(__file__).resolve().parents[2] / "workspace" / "chat"

SETTINGS_FILE = Path(CHAT_DIR) / "think_settings.json"
THINK_NUM_PREDICT = 2048  # mit Denken reicht 320 nicht (Denken verbraucht das Budget sonst komplett)

_override: contextvars.ContextVar = contextvars.ContextVar("nexus_think_override", default=None)

OPEN_TAG = "<think>"
CLOSE_TAG = "</think>"
_TAG_RE = re.compile(r"<think>(.*?)(?:</think>|$)", re.S | re.I)


def _settings_path() -> Path:
    return SETTINGS_FILE


def get_enabled() -> bool:
    p = _settings_path()
    try:
        return bool(json.loads(p.read_text(encoding="utf-8")).get("enabled", False))
    except Exception:
        return False


def set_enabled(enabled: bool) -> dict:
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"enabled": bool(enabled), "updated": datetime.now().isoformat(timespec="seconds")}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
    return data


def settings() -> dict:
    return {"success": True, "enabled": get_enabled(), "file": str(_settings_path())}


def current_override():
    """True/False wenn fuer diese Anfrage gesetzt, sonst None."""
    return _override.get()


def set_override(value):
    """Gibt ein Token zurueck; mit reset_override(token) wiederherstellen."""
    return _override.set(None if value is None else bool(value))


def reset_override(token) -> None:
    try:
        _override.reset(token)
    except Exception:
        _override.set(None)


def resolve_request_think(body: dict | None) -> bool:
    """Body-Feld 'think' (bool) hat Vorrang, sonst globaler Schalter."""
    if isinstance(body, dict) and "think" in body and body.get("think") is not None:
        v = body.get("think")
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "on", "an", "yes", "ja")
        return bool(v)
    return get_enabled()


def split_think_tags(text: str) -> tuple[str, str]:
    """('<think>a</think>b') -> ('a', 'b'). Unvollstaendiges <think> am Ende zaehlt als Denken."""
    if not text or "<think" not in text.lower():
        return "", text or ""
    thinking = "\n".join(m.strip() for m in _TAG_RE.findall(text) if m.strip())
    content = _TAG_RE.sub("", text)
    content = re.sub(r"</think>", "", content, flags=re.I).strip()
    return thinking, content


class ThinkTagSplitter:
    """Streaming-Zerleger: feed(chunk) -> [(kind, text)], kind in {'thinking','token'}."""

    def __init__(self) -> None:
        self.inside = False
        self.buf = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self.buf += chunk or ""
        while self.buf:
            tag = CLOSE_TAG if self.inside else OPEN_TAG
            low = self.buf.lower()
            i = low.find(tag)
            if i >= 0:
                if i:
                    out.append(("thinking" if self.inside else "token", self.buf[:i]))
                self.buf = self.buf[i + len(tag):]
                self.inside = not self.inside
                continue
            # moegliches Tag-Fragment am Ende zurueckhalten
            keep = 0
            for k in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(low[-k:]):
                    keep = k
                    break
            emit = self.buf[:len(self.buf) - keep] if keep else self.buf
            if emit:
                out.append(("thinking" if self.inside else "token", emit))
            self.buf = self.buf[len(self.buf) - keep:] if keep else ""
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        if not self.buf:
            return []
        out = [("thinking" if self.inside else "token", self.buf)]
        self.buf = ""
        return out


def thinking_index(session: dict) -> list[dict]:
    """Assistant-Nachrichten mit gespeichertem Denkprozess (fuer Wiederaufklappen nach Reload)."""
    out = []
    for i, m in enumerate((session or {}).get("messages") or []):
        if m.get("role") == "assistant" and m.get("thinking"):
            out.append({"index": i, "ts": m.get("ts"), "model": m.get("model"),
                        "content_head": str(m.get("content") or "")[:200],
                        "thinking": str(m.get("thinking")), "length": len(str(m.get("thinking")))})
    return out
