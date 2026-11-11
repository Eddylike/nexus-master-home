"""Fallback unter Speicherdruck: nie Embedding-/Vision-Modell, echte Ollama-Meldung erkannt (THINK-06)."""
from nexus.common import ai_layer


class _C:
    def __init__(self, models):
        self._m = models

    def tags_detailed(self):
        return [{"name": n, "size": s} for n, s in self._m]


def test_ollama_memory_message_detected():
    msg = "model requires more system memory (9.1 GiB) than is available (2.0 GiB)"
    assert ai_layer.is_memory_pressure_error(msg)
    assert not ai_layer.is_memory_pressure_error("model not found")


def test_fallback_skips_embedding_and_vision():
    c = _C([("nomic-embed-text:latest", 274_000_000), ("llava:latest", 4_700_000_000),
            ("qwen3.5:9b", 6_600_000_000), ("qwen3.5:4b", 3_400_000_000)])
    assert ai_layer.lightest_present(c) == "qwen3.5:4b"
    assert ai_layer.resolve_fallback_model(c, failed_model="qwen3.5:9b") == "qwen3.5:4b"


def test_fallback_none_when_only_failed_chat_model():
    c = _C([("nomic-embed-text:latest", 274_000_000), ("qwen3.5:9b", 6_600_000_000)])
    assert ai_layer.resolve_fallback_model(c, failed_model="qwen3.5:9b") is None
    assert ai_layer.lightest_present(c) == "qwen3.5:9b"