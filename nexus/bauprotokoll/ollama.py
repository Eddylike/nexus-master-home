"""Minimaler Ollama-Client (nur Standardbibliothek)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_URL = "http://127.0.0.1:11434"


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, url: str | None = None, timeout: float = 120):
        self.url = (url or DEFAULT_URL).rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload=None, timeout: float | None = None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            self.url + path, data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8") or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            raise OllamaError(f"HTTP {e.code}: {detail}") from e
        except Exception as e:
            raise OllamaError(f"{type(e).__name__}: {e}") from e

    def available(self) -> bool:
        try:
            self.tags()
            return True
        except OllamaError:
            return False

    def tags(self):
        return [m.get("name") for m in self._request("GET", "/api/tags", timeout=5).get("models", [])]

    def loaded(self):
        return self._request("GET", "/api/ps", timeout=5).get("models", [])

    def embed(self, text: str, model: str):
        try:
            data = self._request("POST", "/api/embeddings", {"model": model, "prompt": text}, timeout=60)
            vec = data.get("embedding")
            if vec:
                return [float(x) for x in vec]
        except OllamaError:
            pass
        data = self._request("POST", "/api/embed", {"model": model, "input": text}, timeout=60)
        vectors = data.get("embeddings") or []
        return [float(x) for x in vectors[0]] if vectors else None

    def embed_or_none(self, text: str, model: str):
        try:
            return self.embed(text, model)
        except OllamaError:
            return None

    def generate(self, model: str, prompt: str, system: str | None = None,
                 options: dict | None = None, keep_alive=None, timeout=None) -> str:
        payload = {"model": model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        if options:
            payload["options"] = options
        if keep_alive is not None:
            payload["keep_alive"] = keep_alive
        data = self._request("POST", "/api/generate", payload, timeout=timeout)
        return data.get("response", "")

    def unload(self, model: str) -> bool:
        try:
            self._request("POST", "/api/generate", {"model": model, "keep_alive": 0}, timeout=30)
            return True
        except OllamaError:
            return False

    def load(self, model: str, keep_alive: str = "5m") -> bool:
        try:
            self._request("POST", "/api/generate", {"model": model, "prompt": "", "keep_alive": keep_alive}, timeout=120)
            return True
        except OllamaError:
            return False
