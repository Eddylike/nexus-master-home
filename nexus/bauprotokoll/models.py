"""Phase 4: Model-Manager.

Nur ein grosses Modell gleichzeitig im VRAM. Das Embedding-Modell darf parallel laufen.
Modellwechsel laufen ausschliesslich ueber diesen Manager (keep_alive-Steuerung).
"""
from __future__ import annotations

from nexus.bauprotokoll.config import load_config
from nexus.bauprotokoll.ollama import OllamaClient, OllamaError

CODE_HINTS = (
    "code", "python", "javascript", "funktion", "klasse", "modul", "bug", "fix",
    "implementier", "refactor", "pytest", "unittest", "html", "css", "json",
)
RESEARCH_HINTS = ("recherch", "quelle", "vergleich", "markt", "studie", "web", "news")
ROUTER_HINTS = ("klassifiz", "routing", "ja oder nein", "kategorie")


class ModelUnavailable(RuntimeError):
    pass


class ModelManager:
    def __init__(self, config=None, client=None):
        self.cfg = config or load_config()
        self.models = dict(self.cfg.get("models", {}))
        self.keep_alive = self.cfg.get("ollama.keep_alive", "5m")
        self.embedding_model = self.models.get("embeddings", "nomic-embed-text")
        self.client = client or OllamaClient(self.cfg.get("ollama.url"),
                                            timeout=self.cfg.get("ollama.timeout_s", 120))

    def resolve(self, role: str) -> str:
        return self.models.get(role, role)

    def installed(self) -> set[str]:
        try:
            return set(self.client.tags())
        except OllamaError as e:
            raise ModelUnavailable(f"Ollama nicht erreichbar: {e}") from e

    def loaded(self) -> list[str]:
        try:
            return [m.get("name") or m.get("model") for m in self.client.loaded()]
        except OllamaError:
            return []

    def ensure(self, role: str) -> str:
        """Laedt das Modell und entlaedt vorher alle anderen grossen Modelle."""
        name = self.resolve(role)
        if name not in self.installed():
            raise ModelUnavailable(f"Modell nicht installiert: {name}")
        for other in self.loaded():
            if other and other != name and other != self.embedding_model:
                self.client.unload(other)
        if name != self.embedding_model:
            self.client.load(name, keep_alive=self.keep_alive)
        return name

    def unload_all(self) -> None:
        for other in self.loaded():
            if other:
                self.client.unload(other)

    def classify(self, text: str) -> str:
        low = (text or "").lower()
        if any(h in low for h in ROUTER_HINTS) or len(low) < 25:
            return "router"
        if any(h in low for h in RESEARCH_HINTS):
            return "generalist"
        if any(h in low for h in CODE_HINTS):
            return "coder"
        return "generalist"

    def generate(self, role: str, prompt: str, system: str | None = None,
                 options: dict | None = None) -> str:
        name = self.ensure(role)
        return self.client.generate(name, prompt, system=system, options=options,
                                    keep_alive=self.keep_alive)
