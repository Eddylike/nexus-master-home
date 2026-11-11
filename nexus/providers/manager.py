from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any

from nexus.common.ollama_client import OllamaClient
from nexus.common import ai_layer
from nexus.common.claude_client import default_client


@dataclass
class ProviderResult:
    success: bool
    reply: str
    provider: str
    model: str | None = None
    error: str | None = None
    session_id: str | None = None
    rounds: int = 1

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class LocalLLMProvider:
    name = "local"

    def __init__(self, timeout: float = 300.0) -> None:
        self.client = OllamaClient(timeout=timeout)

    def status(self) -> dict[str, Any]:
        ai_layer.apply_to_client(self.client)
        st = self.client.ping()
        model = ai_layer.resolve_chat_model(self.client) if st.get("reachable") else None
        return {"provider": self.name, "configured": True, **st, "model": model, "keep_alive": ai_layer.keep_alive_value(), "never_pull": True}

    def chat(self, message: str, context: list[dict] | None = None, *, session_id: str | None = None, timeout: float | None = None, format: str | None = None, num_predict: int | None = None) -> dict[str, Any]:
        if timeout is not None:
            self.client.timeout = float(timeout)
        ai_layer.apply_to_client(self.client)
        use = ai_layer.resolve_chat_model(self.client)
        messages = list(context or [])
        messages.append({"role": "user", "content": message})
        out = self.client.chat(messages, model=use, format=format, num_predict=num_predict)
        return ProviderResult(bool(out.get("success")), out.get("reply") or "", self.name, out.get("model"), out.get("error"), session_id).as_dict()


class OpenAIProvider:
    """Optional provider. Disabled unless OPENAI_API_KEY is explicitly configured."""
    name = "openai"

    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout
        self.api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        self.model = (os.environ.get("NEXUS_OPENAI_MODEL") or "gpt-5.6").strip()

    def status(self) -> dict[str, Any]:
        return {"provider": self.name, "configured": bool(self.api_key), "reachable": None, "model": self.model, "optional": True, "status": "OPTIONAL", "blocks_health": False}

    def chat(self, message: str, context: list[dict] | None = None, *, session_id: str | None = None, timeout: float | None = None) -> dict[str, Any]:
        if not self.api_key:
            return ProviderResult(False, "", self.name, self.model, "openai_not_configured", session_id).as_dict()
        payload = {"model": self.model, "input": [*(context or []), {"role": "user", "content": message}]}
        req = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=float(timeout or self.timeout)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data.get("output_text") or ""
            if not text:
                bits = []
                for item in data.get("output") or []:
                    for c in item.get("content") or []:
                        if c.get("type") == "output_text": bits.append(c.get("text") or "")
                text = "".join(bits)
            return ProviderResult(True, text.strip(), self.name, self.model, None, session_id).as_dict()
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            return ProviderResult(False, "", self.name, self.model, str(e), session_id).as_dict()


class ClaudeProvider:
    """Anthropic Claude as a normal NEXUS provider (env-gated).

    This provider is plain chat. Full tool use, approvals and background tasks
    live in nexus.integrations.claude_agent and are selected by the controller.
    """

    name = "claude"

    def __init__(self, timeout: float = 120.0) -> None:
        self.timeout = timeout
        self.client = default_client()

    def status(self) -> dict[str, Any]:
        return self.client.status()

    @staticmethod
    def _messages(message: str, context: list[dict] | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in context or []:
            if not isinstance(item, dict) or item.get("role") not in ("user", "assistant"):
                continue
            text = item.get("content")
            if not isinstance(text, str) or not text.strip():
                continue
            if out and out[-1]["role"] == item["role"]:
                out[-1]["content"] += "\n\n" + text
            else:
                out.append({"role": item["role"], "content": text})
        while out and out[0]["role"] != "user":
            out.pop(0)
        if out and out[-1]["role"] == "user":
            out[-1]["content"] += "\n\n" + message
        else:
            out.append({"role": "user", "content": message})
        return out

    def chat(self, message: str, context: list[dict] | None = None, *,
             session_id: str | None = None, timeout: float | None = None,
             **_: Any) -> dict[str, Any]:
        if not self.client.api_key:
            return ProviderResult(False, "", self.name, self.client.model,
                                  "claude_not_configured", session_id).as_dict()
        out = self.client.messages(self._messages(message, context),
                                   timeout=float(timeout or self.timeout))
        return ProviderResult(bool(out.get("success")), out.get("text") or "", self.name,
                              out.get("model") or self.client.model,
                              out.get("error"), session_id).as_dict()


# Logical NEXUS OS provider ids -> concrete provider keys.
LOGICAL_PROVIDERS: dict[str, str] = {
    "WOZIPC_LOCAL_AI": "local",
    "CLAUDE": "claude",
    "CHATGPT_OPTIONAL": "openai",
}

PROVIDER_FALLBACK_ORDER: tuple[str, ...] = ("local", "claude", "openai")


class AIProviderManager:
    """Local-first provider facade with bounded sessions and local fallback."""

    def __init__(self, default: str | None = None, *, max_rounds: int = 4, timeout: float = 120.0) -> None:
        self.default = (default or os.environ.get("NEXUS_AI_PROVIDER") or "local").strip().lower()
        self.max_rounds = max(1, min(int(os.environ.get("NEXUS_AI_MAX_ROUNDS") or max_rounds), 8))
        self.timeout = float(os.environ.get("NEXUS_AI_TIMEOUT") or timeout)
        wozipc = LocalLLMProvider(self.timeout)
        chatgpt = OpenAIProvider(min(self.timeout, 120.0))
        claude = ClaudeProvider(min(self.timeout, 300.0))
        # One instance per backend; logical aliases share it.
        self.providers = {
            "local": wozipc,
            "openai": chatgpt,
            "claude": claude,
        }
        self._instances = {"wozipc_local": wozipc, "chatgpt_optional": chatgpt, "claude": claude}
        self._cancelled: set[str] = set()

    def resolve_key(self, provider: str | None) -> str:
        """Map logical id, alias or provider key to a concrete provider key."""
        name = (provider or self.default or "local").strip()
        low = name.lower()
        for logical, key in LOGICAL_PROVIDERS.items():
            if low == logical.lower():
                return key
        if low in self.providers:
            return low
        for logical, key in LOGICAL_PROVIDERS.items():
            if low == key:
                return key
        return "local"

    def logical_name(self, key: str) -> str:
        for logical, mapped in LOGICAL_PROVIDERS.items():
            if mapped == key:
                return logical
        return key

    def provider_status(self) -> dict[str, Any]:
        """Flat NEXUS OS provider status (phone/wozipc/chatgpt)."""
        out: dict[str, Any] = {}
        for logical, key in LOGICAL_PROVIDERS.items():
            inst = self.providers.get(key) or self._instances.get(key)
            if inst is None:
                out[logical] = {"status": "UNAVAILABLE", "reachable": False, "error": "not_registered"}
                continue
            try:
                out[logical] = inst.status()
            except Exception as e:
                out[logical] = {"status": "UNAVAILABLE", "reachable": False, "error": type(e).__name__}
        out["default"] = self.logical_name(self.resolve_key(self.default))
        out["fallback_order"] = [self.logical_name(k) for k in PROVIDER_FALLBACK_ORDER]
        out["cloud_dependency"] = False
        return out

    def route(self, message: str | None = None, *, provider: str | None = None, allow_chatgpt: bool | None = None) -> dict[str, Any]:
        """NEXUS Master Home intent -> provider. Local-first, no cloud by default."""
        if provider:
            key = self.resolve_key(provider)
            inst = self.providers.get(key)
            return {"provider": key, "logical": self.logical_name(key), "reason": "explicit_request", "usable": bool(inst is not None)}

        if allow_chatgpt is None:
            allow_chatgpt = (os.environ.get("NEXUS_CHATGPT_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")
        # A spoken/text "chatgpt" hint only enables the optional slot, it never
        # skips the local chain and never creates a cloud dependency.
        if not allow_chatgpt and message and self.route_natural(message) == "openai":
            allow_chatgpt = True

        try:
            wozipc_ok = bool(self.providers["local"].status().get("reachable"))
        except Exception:
            wozipc_ok = False
        if wozipc_ok:
            reason = "intent_chatgpt_ignored_local_first" if allow_chatgpt else "local_fallback"
            return {"provider": "local", "logical": "WOZIPC_LOCAL_AI", "reason": reason, "usable": True}

        if allow_chatgpt:
            try:
                if self.providers["openai"].status().get("configured"):
                    return {"provider": "openai", "logical": "CHATGPT_OPTIONAL", "reason": "chatgpt_explicitly_enabled", "usable": True}
            except Exception:
                pass

        return {
            "provider": "local",
            "logical": "WOZIPC_LOCAL_AI",
            "reason": "no_local_provider_reachable",
            "usable": False,
            "error": "local_ai_unavailable",
        }

    def status(self) -> dict[str, Any]:
        return {"default": self.default, "fallback": "local", "max_rounds": self.max_rounds, "timeout": self.timeout, "providers": {k: p.status() for k, p in self.providers.items()}, "nexus_os": self.provider_status()}

    def cancel(self, session_id: str) -> None:
        self._cancelled.add(session_id)

    def chat(self, message: str, context: list[dict] | None = None, *, provider: str | None = None, session_id: str | None = None, round_no: int = 1, timeout: float | None = None, fallback: bool = True, format: str | None = None, num_predict: int | None = None) -> dict[str, Any]:
        sid = session_id or "AI-" + uuid.uuid4().hex[:16]
        if sid in self._cancelled:
            return ProviderResult(False, "", provider or self.default, error="cancelled", session_id=sid, rounds=round_no).as_dict()
        if round_no > self.max_rounds:
            return ProviderResult(False, "", provider or self.default, error="max_rounds_exceeded", session_id=sid, rounds=round_no).as_dict()
        chosen = self.resolve_key(provider)
        p = self.providers.get(chosen)
        if p is None:
            chosen, p = "local", self.providers["local"]
        kwargs = {"session_id": sid, "timeout": timeout or self.timeout}
        if chosen == "local":
            kwargs.update({"format": format, "num_predict": num_predict})
        out = p.chat(message, context, **kwargs)
        out["rounds"] = round_no
        out["logical_provider"] = self.logical_name(chosen)
        if not out.get("success") and fallback and chosen != "local":
            local = self.providers["local"].chat(message, context, session_id=sid, timeout=timeout or self.timeout)
            local["fallback_from"] = self.logical_name(chosen)
            local["rounds"] = round_no
            local["logical_provider"] = "WOZIPC_LOCAL_AI"
            return local
        return out

    def route_natural(self, message: str) -> str:
        low = (message or "").lower()
        if any(x in low for x in ("frag lokal", "lokale modell", "lokales modell", "benutze lokal", "benutze das lokale")):
            return "local"
        if any(x in low for x in ("claude", "anthropic")):
            return "claude" if self.providers["claude"].status().get("configured") else "local"
        if any(x in low for x in ("chatgpt", "andere ki", "zweiten modell", "zweites modell")):
            return "openai" if self.providers["openai"].status().get("configured") else "local"
        return self.default if self.default in self.providers else "local"
