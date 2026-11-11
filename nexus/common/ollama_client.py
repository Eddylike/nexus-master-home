from __future__ import annotations
import os
import base64
import json
import urllib.error
import urllib.request
import threading
import time
from contextlib import nullcontext

_INFERENCE_LOCK = threading.RLock()
def _unified_ctx() -> int:
    try:
        from nexus.common import ai_layer
        ctx = ai_layer.unified_num_ctx()
        # Sicherstellung: Qwen 3.5 9B wird mindestens so groes Kontextfenster zuweisen wie die Alternative, um Entladen zu verhindern
        return max(ctx, 4096)
    except Exception:
        # Fallback auf eine minimale sichere Groesse, keine automatische Reduktion auf 8192 bei Fehlern, die unter 3.5 9B fallen
        return 8192


CHAT_LOCK_WAIT_SEC = 3.0  # 2026-10-28 CHAT01: max. Wartezeit auf belegte lokale KI, dann ehrlich ollama_busy


class _LockTimeout(Exception):
    pass


class _lock_or_busy:
    def __init__(self, wait: float):
        self.wait = wait

    def __enter__(self):
        if not _INFERENCE_LOCK.acquire(timeout=max(0.0, float(self.wait))):
            raise _LockTimeout()
        return self

    def __exit__(self, *exc):
        _INFERENCE_LOCK.release()
        return False
from typing import Any, Iterator

from .config import Config

# Preference order among ALREADY INSTALLED models only — never triggers pull.
PREFERRED_MODELS = ("qwen3.5:9b", "qwen3.5:4b", "llama3.2:latest", "llama3.2", "llama3")
def _think():
    # 2026-10-26: qwen3.5 denkt sonst standardmaessig und verbraucht num_predict komplett -> leere Antwort ('Keine Antwort.').
    # THINK-01 2026-11-10: pro Anfrage gesetzter Override (Denkprozess-Fenster) hat Vorrang.
    try:
        from nexus.common import think_panel as _tp
        _ov = _tp.current_override()
        if _ov is not None:
            return bool(_ov)
    except Exception:
        pass
    return os.environ.get("NEXUS_OLLAMA_THINK", "0") == "1"


def _think_budget() -> int:
    try:
        from nexus.common.think_panel import THINK_NUM_PREDICT
        return int(THINK_NUM_PREDICT)
    except Exception:
        return 2048


def _new_splitter():
    try:
        from nexus.common.think_panel import ThinkTagSplitter
        return ThinkTagSplitter()
    except Exception:
        return None


DEFAULT_CHAT_OPTIONS = {"num_ctx": 8192, "num_predict": 320, "temperature": 0.35}


def _safe_messages(messages):
    """2026-10-26 BLOCKED-Fix: Literale <tool_call>-Tags im Prompt bringen den qwen3.5-Renderer
    von Ollama zum Absturz (HTTP 500 {"error":"EOF"}). Textuell zu <tool-call> entschaerfen."""
    out = []
    for m in messages or []:
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, str) and "tool_call>" in c:
            m = dict(m); m["content"] = c.replace("<tool_call>", "<tool-call>").replace("</tool_call>", "</tool-call>")
        out.append(m)
    return out
VISION_MODELS = ("llava", "llava:latest", "bakllava", "moondream")


class OllamaClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or Config.OLLAMA_URL).rstrip("/")
        self.timeout = float(timeout if timeout is not None else getattr(Config, "OLLAMA_TIMEOUT", 300))
        self.model: str | None = None
        # Phase 8: unload after request by default (no permanent VRAM residency)
        self.keep_alive: Any = getattr(Config, "OLLAMA_KEEP_ALIVE", 0)
        self.never_pull: bool = True

    def _req(self, method: str, path: str, payload: dict | None = None, timeout: float | None = None) -> tuple[bool, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            # 2026-10-28 ctx: jeder Inferenz-Call mit einheitlichem num_ctx (sonst Reload 25-60 s)
            if method == "POST" and path in ("/api/chat", "/api/generate") and (payload.get("prompt") or payload.get("messages")):
                payload = dict(payload); payload["options"] = dict(payload.get("options") or {})
                payload["options"].setdefault("num_ctx", _unified_ctx())
            data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with (_INFERENCE_LOCK if method == "POST" else nullcontext()), urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return True, (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
            return False, {"error": f"HTTP {e.code}: {err}"}
        except Exception as e:
            return False, {"error": str(e)}

    def ping(self) -> dict:
        ok, data = self._req("GET", "/api/tags", timeout=5.0)
        if not ok:
            return {"ok": False, "reachable": False, "error": data.get("error", "unreachable"), "models": []}
        models = [m.get("name", "") for m in (data.get("models") or [])]
        return {"ok": True, "reachable": True, "models": models, "error": None}

    def tags_detailed(self) -> list[dict]:
        ok, data = self._req("GET", "/api/tags", timeout=5.0)
        if not ok:
            return []
        return list(data.get("models") or [])

    def loaded_models(self) -> list[str]:
        """Models currently resident in VRAM (/api/ps). Empty = none loaded."""
        ok, data = self._req("GET", "/api/ps", timeout=5.0)
        if not ok:
            return []
        return [m.get("name") or m.get("model") or "" for m in (data.get("models") or []) if (m.get("name") or m.get("model"))]

    def pick_model(self, preferred: str | None = None) -> str | None:
        """Select among INSTALLED models only. Never pulls."""
        st = self.ping()
        if not st["reachable"]:
            return None
        names = st["models"]
        candidates = []
        if preferred:
            candidates.append(preferred)
        candidates.append(Config.PREFERRED_MODEL)
        candidates.extend(PREFERRED_MODELS)
        for c in candidates:
            for n in names:
                if n == c or n.startswith(c + ":") or (c.split(":")[0] and c.split(":")[0] == n.split(":")[0] and c in n):
                    self.model = n
                    return n
            for n in names:
                if c in n:
                    self.model = n
                    return n
        if names:
            self.model = names[0]
            return names[0]
        return None

    def has_vision(self) -> str | None:
        st = self.ping()
        if not st["reachable"]:
            return None
        preferred = (getattr(Config, "VISION_MODEL", None) or "llava").lower()
        for n in st["models"]:
            if n.lower() == preferred or n.lower().startswith(preferred.split(":")[0]):
                return n
        for n in st["models"]:
            low = n.lower()
            for v in VISION_MODELS:
                if v in low:
                    return n
        return None

    def _effective_keep_alive(self) -> Any:
        try:
            from nexus.common import ai_layer
            return ai_layer.keep_alive_value()
        except Exception:
            ka = self.keep_alive
            if ka is None or ka == "":
                return 0
            if isinstance(ka, str) and ka.isdigit():
                return int(ka)
            return ka

    def _chat_once(self, messages: list[dict], *, model: str, temperature: float, tools: list[dict] | None = None,
                   format: str | None = None, num_predict: int | None = None, keep_alive: Any = None) -> dict:
        opts = dict(DEFAULT_CHAT_OPTIONS)
        opts["num_ctx"] = _unified_ctx()  # 2026-10-28 einheitlich
        opts["temperature"] = temperature
        payload = {
            "model": model,
            "messages": _safe_messages(messages),
            "stream": False,
            "think": _think(),
            "options": opts,
            # keep_alive override only for callers that need a warm model for a bounded time (voice call);
            # everyone else keeps the configured unload policy.
            "keep_alive": self._effective_keep_alive() if keep_alive is None else keep_alive,
        }
        if tools is not None:
            payload["tools"] = tools
            opts["num_predict"] = 2048
        if num_predict:
            opts["num_predict"] = int(num_predict)
        if format:
            payload["format"] = format
        if payload["think"]:  # THINK-01: Denken braucht eigenes Token-Budget
            opts["num_predict"] = max(int(opts.get("num_predict") or 0), _think_budget())
        t0 = time.monotonic()
        ok, data = self._req("POST", "/api/chat", payload)
        wall_ms = int((time.monotonic() - t0) * 1000)
        if not ok:
            return {"success": False, "reply": f"Ollama-Fehler: {data.get('error', 'unbekannt')}", "error": data.get("error"), "model": model}
        msg = data.get("message") or {}
        reply = (msg.get("content") or data.get("response") or "").strip()
        thinking = str(msg.get("thinking") or "")
        try:  # THINK-03: <think>-Tags aus der Antwort in den Denkprozess verschieben
            from nexus.common.think_panel import split_think_tags
            _tt, reply = split_think_tags(reply)
            if _tt:
                thinking = (thinking + "\n" + _tt).strip()
        except Exception:
            pass
        ns = lambda k: int((data.get(k) or 0) / 1e6)
        # Phasenzeiten (für Latenzanalyse): wall = inkl. Warten auf _INFERENCE_LOCK; Rest kommt von Ollama.
        timings = {"wall_ms": wall_ms, "ollama_total_ms": ns("total_duration"), "load_ms": ns("load_duration"),
                   "prompt_ms": ns("prompt_eval_duration"), "eval_ms": ns("eval_duration"),
                   "prompt_tokens": data.get("prompt_eval_count"), "eval_tokens": data.get("eval_count")}
        timings["queue_ms"] = max(0, wall_ms - timings["ollama_total_ms"])
        return {"success": True, "reply": reply, "model": model, "message": msg, "thinking": thinking,
                "tool_calls": msg.get("tool_calls") or [], "raw": {"done": data.get("done")}, "timings": timings}

    def chat(self, messages: list[dict], *, model: str | None = None, temperature: float = 0.3, tools: list[dict] | None = None,
             format: str | None = None, num_predict: int | None = None, keep_alive: Any = None) -> dict:
        use = model or self.model or self.pick_model()
        if not use:
            ping = self.ping()
            url = self.base_url
            if not ping["reachable"]:
                return {
                    "success": False,
                    "reply": f"Ollama ist nicht erreichbar unter {url}. Bitte Ollama auf dem PC starten.",
                    "error": "ollama_down",
                    "model": None,
                }
            return {
                "success": False,
                "reply": "Ollama läuft, aber kein Modell gefunden. Bitte Eddie um `ollama pull …` fragen — kein Auto-Pull.",
                "error": "no_model",
                "model": None,
            }
        out = self._chat_once(messages, model=use, temperature=temperature, tools=tools,
                               format=format, num_predict=num_predict, keep_alive=keep_alive)
        if out.get("success"):
            return out
        # Memory-pressure fallback: lightest present, never pull
        try:
            from nexus.common import ai_layer
            if ai_layer.is_memory_pressure_error(out.get("error")):
                fb = ai_layer.resolve_fallback_model(self, failed_model=use)
                if fb and fb != use:
                    retry = self._chat_once(messages, model=fb, temperature=temperature, tools=tools,
                                            format=format, num_predict=num_predict)
                    retry["fallback_from"] = use
                    retry["fallback_reason"] = "memory_pressure"
                    return retry
        except Exception:
            pass
        return out

    def chat_stream(self, messages: list[dict], *, model: str | None = None, temperature: float = 0.3) -> Iterator[dict]:
        """Yields dicts: {"type":"token","text":...} then {"type":"done",...}. keep_alive unloads after."""
        use = model or self.model or self.pick_model()
        if not use:
            ping = self.ping()
            url = self.base_url
            if not ping["reachable"]:
                reply = f"Ollama ist nicht erreichbar unter {url}. Bitte Ollama auf dem PC starten."
                yield {"type": "done", "reply": reply, "model": None, "success": False, "error": "ollama_down"}
                return
            reply = "Ollama läuft, aber kein Modell gefunden. Bitte Eddie um `ollama pull …` fragen — kein Auto-Pull."
            yield {"type": "done", "reply": reply, "model": None, "success": False, "error": "no_model"}
            return

        opts = dict(DEFAULT_CHAT_OPTIONS)
        opts["temperature"] = temperature
        opts["num_ctx"] = _unified_ctx()
        payload = {
            "model": use,
            "messages": _safe_messages(messages),
            "stream": True,
            "think": _think(),
            "options": opts,
            "keep_alive": self._effective_keep_alive(),
        }
        if payload["think"]:  # THINK-01
            opts["num_predict"] = max(int(opts.get("num_predict") or 0), _think_budget())
        _tsplit = _new_splitter()
        thinking_parts: list[str] = []
        url = f"{self.base_url}/api/chat"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        full: list[str] = []
        stream_error: str | None = None
        try:
            with _lock_or_busy(getattr(self, "lock_wait", CHAT_LOCK_WAIT_SEC)), urllib.request.urlopen(req, timeout=self.timeout) as resp:
                while True:
                    line = resp.readline()
                    if not line:
                        break
                    line = line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("error"):
                        err = str(obj["error"])
                        stream_error = err
                        yield {
                            "type": "done",
                            "reply": f"Ollama-Fehler: {err}",
                            "model": use,
                            "success": False,
                            "error": err,
                        }
                        # attempt non-stream fallback on memory pressure
                        try:
                            from nexus.common import ai_layer
                            if ai_layer.is_memory_pressure_error(err):
                                fb = ai_layer.resolve_fallback_model(self, failed_model=use)
                                if fb and fb != use:
                                    retry = self._chat_once(messages, model=fb, temperature=temperature)
                                    yield {
                                        "type": "done",
                                        "reply": retry.get("reply") or "",
                                        "model": retry.get("model"),
                                        "success": bool(retry.get("success")),
                                        "error": retry.get("error"),
                                        "thinking": retry.get("thinking") or "",
                                        "fallback_from": use,
                                        "fallback_reason": "memory_pressure",
                                    }
                        except Exception:
                            pass
                        return
                    msg = obj.get("message") or {}
                    _th = msg.get("thinking") or ""
                    if _th:  # THINK-02: Denk-Tokens getrennt vom Inhalt streamen
                        thinking_parts.append(_th)
                        yield {"type": "thinking", "text": _th}
                    chunk = msg.get("content") or obj.get("response") or ""
                    if chunk:
                        for _kind, _part in (_tsplit.feed(chunk) if _tsplit else [("token", chunk)]):
                            if _kind == "thinking":
                                thinking_parts.append(_part)
                                yield {"type": "thinking", "text": _part}
                            else:
                                full.append(_part)
                                yield {"type": "token", "text": _part}
                    if obj.get("done"):
                        break
        except _LockTimeout:
            yield {"type": "done", "reply": "Lokale KI ist gerade belegt (andere Anfrage laeuft). Bitte gleich erneut versuchen.",
                   "model": use, "success": False, "error": "ollama_busy"}
            return
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
            err_s = f"HTTP {e.code}: {err}"
            # memory fallback
            try:
                from nexus.common import ai_layer
                if ai_layer.is_memory_pressure_error(err_s):
                    fb = ai_layer.resolve_fallback_model(self, failed_model=use)
                    if fb and fb != use:
                        retry = self._chat_once(messages, model=fb, temperature=temperature)
                        yield {
                            "type": "done",
                            "reply": retry.get("reply") or "",
                            "model": retry.get("model"),
                            "success": bool(retry.get("success")),
                            "error": retry.get("error"),
                            "thinking": retry.get("thinking") or "",
                            "fallback_from": use,
                            "fallback_reason": "memory_pressure",
                        }
                        return
            except Exception:
                pass
            yield {
                "type": "done",
                "reply": f"Ollama-Fehler: {err_s}",
                "model": use,
                "success": False,
                "error": f"HTTP {e.code}",
            }
            return
        except Exception as e:
            yield {
                "type": "done",
                "reply": f"Ollama-Fehler: {e}",
                "model": use,
                "success": False,
                "error": str(e),
            }
            return

        if stream_error:
            return
        for _kind, _part in (_tsplit.flush() if _tsplit else []):
            (thinking_parts if _kind == "thinking" else full).append(_part)
        reply = "".join(full).strip() or "Keine Antwort erzeugt."
        yield {"type": "done", "reply": reply, "model": use, "success": True, "error": None,
               "thinking": "".join(thinking_parts).strip(), "think": bool(payload["think"])}

    def vision(self, prompt: str, image_bytes: bytes, model: str | None = None) -> dict:
        use = model or self.has_vision()
        if not use:
            return {
                "success": False,
                "reply": "Kein Vision-Modell (z.B. llava) in Ollama gefunden. Bildanalyse nicht möglich. Kein Auto-Pull.",
                "error": "no_vision_model",
            }
        b64 = base64.b64encode(image_bytes).decode("ascii")
        messages = [{"role": "user", "content": prompt, "images": [b64]}]
        return self.chat(messages, model=use)
