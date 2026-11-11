"""THINK-01..04 (2026-11-10): Denkprozess-Backend ohne echtes Ollama (gemockt)."""
import io
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nexus.common import think_panel as tp  # noqa: E402
from nexus.common import ollama_client as oc  # noqa: E402


def test_split_think_tags():
    assert tp.split_think_tags("<think>abc</think>Antwort") == ("abc", "Antwort")
    assert tp.split_think_tags("ohne Tags") == ("", "ohne Tags")
    th, co = tp.split_think_tags("<think>nur gedacht")
    assert th == "nur gedacht" and co == ""


def test_splitter_over_chunk_boundaries():
    s = tp.ThinkTagSplitter(); out = []
    for c in ["Hal", "lo <th", "ink>ich de", "nke</thi", "nk> fertig"]:
        out += s.feed(c)
    out += s.flush()
    think = "".join(t for k, t in out if k == "thinking")
    tok = "".join(t for k, t in out if k == "token")
    assert think == "ich denke"
    assert tok == "Hallo  fertig"


def test_settings_persist(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "SETTINGS_FILE", tmp_path / "think_settings.json")
    assert tp.get_enabled() is False
    tp.set_enabled(True)
    assert tp.get_enabled() is True
    assert json.loads((tmp_path / "think_settings.json").read_text(encoding="utf-8"))["enabled"] is True
    tp.set_enabled(False)
    assert tp.settings()["enabled"] is False


def test_request_think_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "SETTINGS_FILE", tmp_path / "s.json")
    tp.set_enabled(True)
    assert tp.resolve_request_think({}) is True
    assert tp.resolve_request_think({"think": False}) is False
    assert tp.resolve_request_think({"think": "an"}) is True
    tp.set_enabled(False)
    assert tp.resolve_request_think({"message": "x"}) is False


def test_override_controls_ollama_think(monkeypatch):
    monkeypatch.delenv("NEXUS_OLLAMA_THINK", raising=False)
    assert oc._think() is False
    tok = tp.set_override(True)
    try:
        assert oc._think() is True
    finally:
        tp.reset_override(tok)
    assert oc._think() is False
    tok = tp.set_override(False)
    try:
        monkeypatch.setenv("NEXUS_OLLAMA_THINK", "1")
        assert oc._think() is False  # Override (Aus) schlaegt Umgebungsvariable
    finally:
        tp.reset_override(tok)


class _Resp:
    def __init__(self, lines):
        self._b = io.BytesIO(b"".join((json.dumps(x) + "\n").encode() for x in lines))
    def readline(self):
        return self._b.readline()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _client(monkeypatch, lines, sent):
    def fake_urlopen(req, timeout=None):
        sent.append(json.loads(req.data.decode("utf-8")))
        return _Resp(lines)
    monkeypatch.setattr(oc.urllib.request, "urlopen", fake_urlopen)
    c = oc.OllamaClient.__new__(oc.OllamaClient)
    c.base_url = "http://127.0.0.1:11434"; c.model = "qwen3.5:9b"; c.timeout = 5; c.keep_alive = "30m"; c.lock_wait = 1
    monkeypatch.setattr(oc.OllamaClient, "_effective_keep_alive", lambda self: "30m", raising=False)
    return c


def test_stream_thinking_separate_from_content(monkeypatch):
    lines = [{"message": {"thinking": "Ich rechne 17*23. "}}, {"message": {"thinking": "Wait, pruefe: 391."}},
             {"message": {"content": "391"}}, {"done": True, "message": {}}]
    sent = []
    c = _client(monkeypatch, lines, sent)
    tok = tp.set_override(True)
    try:
        evs = list(c.chat_stream([{"role": "user", "content": "17*23?"}]))
    finally:
        tp.reset_override(tok)
    assert sent[0]["think"] is True
    assert sent[0]["options"]["num_predict"] >= tp.THINK_NUM_PREDICT
    th = "".join(e["text"] for e in evs if e["type"] == "thinking")
    co = "".join(e["text"] for e in evs if e["type"] == "token")
    done = [e for e in evs if e["type"] == "done"][-1]
    assert th == "Ich rechne 17*23. Wait, pruefe: 391."
    assert co == "391" and done["reply"] == "391"
    assert done["thinking"] == th and done["think"] is True


def test_stream_think_off_sends_false(monkeypatch):
    sent = []
    c = _client(monkeypatch, [{"message": {"content": "OK"}}, {"done": True}], sent)
    tok = tp.set_override(False)
    try:
        evs = list(c.chat_stream([{"role": "user", "content": "x"}]))
    finally:
        tp.reset_override(tok)
    assert sent[0]["think"] is False
    assert sent[0]["options"]["num_predict"] == oc.DEFAULT_CHAT_OPTIONS["num_predict"]
    assert not [e for e in evs if e["type"] == "thinking"]
    assert evs[-1]["thinking"] == "" and evs[-1]["reply"] == "OK"


def test_stream_think_tags_moved_to_panel(monkeypatch):
    lines = [{"message": {"content": "<thi"}}, {"message": {"content": "nk>versteckt</think>Sichtbar"}}, {"done": True}]
    sent = []
    c = _client(monkeypatch, lines, sent)
    evs = list(c.chat_stream([{"role": "user", "content": "x"}]))
    done = evs[-1]
    assert done["reply"] == "Sichtbar"
    assert done["thinking"] == "versteckt"
    assert "<think" not in "".join(e.get("text", "") for e in evs if e["type"] == "token")


def test_chat_once_returns_thinking_and_strips_tags(monkeypatch):
    """Gilt auch fuer den 4b-Fallback (laeuft ueber _chat_once)."""
    c = oc.OllamaClient.__new__(oc.OllamaClient)
    c.base_url = "x"; c.model = "qwen3.5:4b"; c.timeout = 5; c.keep_alive = "30m"
    sent = []
    def fake_req(self, method, path, payload):
        sent.append(payload)
        return True, {"message": {"content": "<think>innen</think>Antwort", "thinking": "nativ"}, "done": True}
    monkeypatch.setattr(oc.OllamaClient, "_req", fake_req, raising=False)
    monkeypatch.setattr(oc.OllamaClient, "_effective_keep_alive", lambda self: "30m", raising=False)
    tok = tp.set_override(True)
    try:
        r = c._chat_once([{"role": "user", "content": "x"}], model="qwen3.5:4b", temperature=0.3)
    finally:
        tp.reset_override(tok)
    assert sent[0]["think"] is True and sent[0]["options"]["num_predict"] >= tp.THINK_NUM_PREDICT
    assert r["reply"] == "Antwort"
    assert r["thinking"].startswith("nativ") and "innen" in r["thinking"]


def test_engine_persists_thinking():
    from nexus.chat.engine import NexusEngine
    fake = types.SimpleNamespace(_now=lambda: "2026-11-10T12:00:00", _save_sessions=lambda: None)
    session = {"messages": []}
    NexusEngine._append_response(fake, session, {"reply": "391", "thinking": "17*23 ..."}, {"partner": "nexus"})
    NexusEngine._append_response(fake, session, {"reply": "ohne"}, {"partner": "nexus"})
    assert session["messages"][0]["thinking"] == "17*23 ..."
    assert "thinking" not in session["messages"][1]
    idx = tp.thinking_index(session)
    assert len(idx) == 1 and idx[0]["index"] == 0 and idx[0]["length"] == len("17*23 ...")


def test_server_routes_present():
    src = (ROOT / "nexus" / "chat" / "server.py").read_text(encoding="utf-8")
    assert 'path == "/api/chat/think"' in src and 'path == "/api/chat/thinking"' in src
    assert '{"type": "thinking", "text": ev.get("text", "")}' in src
    assert "_tp.reset_override(_think_tok)" in src
