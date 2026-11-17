"""LT-CHAT (17.11.2026): Chatverlauf (dauerhaft, JSONL) + Texteingabe mit/ohne Vorlesen."""
import json, shutil, time, uuid
from pathlib import Path

import pytest

from nexus.livetalk import engines, server
from nexus.livetalk.server import Session

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "nexus" / "chat" / "static" / "livetalk.html"


@pytest.fixture
def hdir(monkeypatch):
    d = ROOT / "workspace" / "tmp" / ("lt_hist_" + uuid.uuid4().hex[:8])
    monkeypatch.setattr(server, "_TEST_HISTORY_DIR", d)
    monkeypatch.delenv("NEXUS_LIVETALK_HISTORY_DIR", raising=False)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def mk(conv=None, tokens=("Hallo Eddie. ", "Alles klar.")):
    sent, bins, synth_calls = [], [], []

    def reply(agent, mode, text, history, call_id, meta):
        meta["model"] = "fake"
        yield from tokens

    def synth(text):
        synth_calls.append(text)
        return iter([(22050, b"\x01\x00" * 2205)])

    s = Session(conv or ("ct" + uuid.uuid4().hex[:10]), lambda raw: sent.append(json.loads(raw)), bins.append,
                authed=True, cfg=dict(engines.DEFAULTS), stt=lambda pcm: {"text": "", "stt_ms": 1}, synth=synth,
                reply=reply, route=lambda t: ("NEXUS", "talk"), warm=False)
    return s, sent, bins, synth_calls


def wait_for(cond, t=5.0):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def types(sent):
    return [m["type"] for m in sent]


def test_history_dir_never_on_c(hdir):
    p = server._history_dir()
    assert p == hdir and not str(p.resolve()).upper().startswith("C:")


def test_text_turn_silent_and_persisted(hdir):
    s, sent, bins, synth_calls = mk()
    s.on_message(json.dumps({"type": "start"}))
    assert "history" in types(sent)
    s.on_message(json.dumps({"type": "text", "text": "Wie spät ist es?", "speak": False}))
    assert wait_for(lambda: "tts_end" in types(sent))
    assert synth_calls == [] and bins == []          # nichts vorgelesen
    end = [m for m in sent if m["type"] == "tts_end"][0]
    assert end["text"] == "Hallo Eddie. Alles klar."
    assert any(m["type"] == "tts_chunk" and m["sr"] == 0 and m["text"] for m in sent)
    assert s.history[0] == {"role": "user", "content": "Wie spät ist es?"}
    rows = [json.loads(x) for x in (hdir / f"{s.conv_id}.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["typed"] is True and rows[1]["agent"] == "NEXUS" and rows[0]["ts"] > 0


def test_text_turn_speaks_by_default(hdir):
    s, sent, bins, synth_calls = mk()
    s.on_message(json.dumps({"type": "start"}))
    s.on_message(json.dumps({"type": "text", "text": "Hallo"}))
    assert wait_for(lambda: "tts_end" in types(sent))
    assert synth_calls and bins and bins[0][:1] == b"A"


def test_history_survives_restart_and_is_sent_on_start(hdir):
    s, sent, _, _ = mk()
    s.on_message(json.dumps({"type": "start"}))
    s.on_message(json.dumps({"type": "text", "text": "Merk dir: Blau", "speak": False}))
    assert wait_for(lambda: "tts_end" in types(sent))
    cid = s.conv_id
    server._CONVS.pop(cid, None)                     # simuliert Core-Neustart (RAM leer)
    s2, sent2, _, _ = mk(conv=cid)
    assert s2.history[0] == {"role": "user", "content": "Merk dir: Blau"}   # Kontext wieder da
    s2.on_message(json.dumps({"type": "start", "conversation_id": cid}))
    h = [m for m in sent2 if m["type"] == "history"][0]
    assert [i["content"] for i in h["items"]][:1] == ["Merk dir: Blau"] and len(h["items"]) == 2
    s2.on_message(json.dumps({"type": "history"}))
    assert types(sent2).count("history") == 2


def test_history_clear(hdir):
    s, sent, _, _ = mk()
    s.on_message(json.dumps({"type": "start"}))
    s.on_message(json.dumps({"type": "text", "text": "Hallo", "speak": False}))
    assert wait_for(lambda: "tts_end" in types(sent))
    f = hdir / f"{s.conv_id}.jsonl"
    assert f.exists()
    s.on_message(json.dumps({"type": "history_clear"}))
    assert [m for m in sent if m["type"] == "history_clear"][0]["ok"] is True
    assert not f.exists() and s.history == []
    assert server.history_load(s.conv_id) == []


def test_history_clear_needs_auth(hdir):
    s, sent, _, _ = mk()
    s.authed = False
    s.on_message(json.dumps({"type": "history_clear"}))
    assert sent[-1]["error"] == "AUTH_REQUIRED"


def test_no_phone_numbers_in_file(hdir):
    cid = "ct" + uuid.uuid4().hex[:10]
    server.history_append(cid, "user", "Ruf 0171 1234567 an oder +49 (171) 123-45-67, um 10:30 Uhr am 17.11.2026, 12 Grad")
    txt = (hdir / f"{cid}.jsonl").read_text(encoding="utf-8")
    assert "1234567" not in txt and "123-45-67" not in txt and "[Nummer]" in txt and "10:30" in txt and "17.11.2026" in txt and "12 Grad" in txt


def test_bad_conv_id_not_written(hdir):
    server.history_append("../../evil", "user", "x")
    assert not hdir.exists() or not any(hdir.iterdir())


def test_html_chat_ui_and_hooks():
    h = HTML.read_text(encoding="utf-8")
    for i in ("orb", "main", "mute", "barge", "pill", "statetxt", "agent", "you", "ai", "meta", "pair",
              "log", "msg", "send", "speak", "clear", "confirmclr", "hist"):
        assert f'id="{i}"' in h, i
    for hook in ("window.NXLT", "window.NXOcular", "window.NXChat", "visualViewport", "type:'text'",
                 "history_clear", "lt_chat_v1"):
        assert hook in h, hook
    assert h.count("requestAnimationFrame(frame)") == 2   # eine Render-Schleife (Start + Fortsetzung)
    assert "192.0.2.42" not in h and "QWEN 2.5 SYNC" not in h
