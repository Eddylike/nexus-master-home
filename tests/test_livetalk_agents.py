from pathlib import Path
from nexus.livetalk import agents


def test_route():
    assert agents.route("Hermes, wie ist die CPU-Last?") == ("Hermes", "talk")
    assert agents.route("Hey NEXUS GPT, erzähl mir was.")[0] == "NEXUS GPT"
    assert agents.route("Äther, wie geht es dem System?")[0] == "Aether"
    assert agents.route("Aether bitte kurz.")[0] == "Aether"
    assert agents.route("Auftrag: lege eine Notiz an") == ("NEXUS", "order")
    assert agents.route("Hallo NEXUS, wie geht's dir?") == ("NEXUS", "talk")
    assert agents.route("Ich habe Hermes gestern gefragt") == ("NEXUS", "talk")  # nur Anrede am Anfang


class _LLM:
    def __init__(self):
        self.seen = None
        self.override = "unset"

    def chat_stream(self, messages, temperature=0.3, model=None):
        from nexus.common import think_panel
        self.override = think_panel.current_override()
        self.seen = messages
        self.seen_model = model
        for t in ("Hallo ", "Eddie. ", "Alles gut."):
            yield {"type": "token", "text": t}
        yield {"type": "done", "model": "qwen3.5:9b", "success": True}


class _Eng:
    def __init__(self):
        self.master = type("M", (), {})()
        self.master.llm = _LLM()


def test_nexus_stream_uses_context_and_no_think():
    eng = _Eng()
    meta = {}
    hist = [{"role": "user", "content": "Mein Hund heißt Bello."},
            {"role": "assistant", "content": "Schöner Name!", "agent": "NEXUS"}]
    out = "".join(agents.reply_stream(eng, "NEXUS", "talk", "Wie heißt mein Hund?", hist, "LTtest01", meta))
    assert out == "Hallo Eddie. Alles gut."
    assert eng.master.llm.override is False
    msgs = eng.master.llm.seen
    assert msgs[0]["role"] == "system" and "NEXUS" in msgs[0]["content"]
    assert msgs[1]["content"] == "Mein Hund heißt Bello." and msgs[-1]["content"] == "Wie heißt mein Hund?"
    assert meta["model"] == "qwen3.5:9b" and meta["agent"] == "NEXUS"


def test_busy_message():
    class L(_LLM):
        def chat_stream(self, messages, temperature=0.3, model=None):
            yield {"type": "done", "success": False, "error": "ollama_busy"}
    eng = _Eng(); eng.master.llm = L(); meta = {}
    out = "".join(agents.reply_stream(eng, "NEXUS", "talk", "Hallo", [], "LTtest01", meta))
    assert "belegt" in out and meta.get("failed")


def test_guard_sentence():
    meta = {}
    s, stop = agents.guard_sentence("Ich habe YouTube geöffnet.", meta)
    assert stop and s != "Ich habe YouTube geöffnet."
    s, stop = agents.guard_sentence("Schön, dich zu hören.", {})
    assert not stop and s == "Schön, dich zu hören."


def test_action_route_uses_chat_intents():
    # Geraetebefehle ohne "Auftrag" / "am PC" gehen an den Orchestrator.
    assert agents.route("oeffne YouTube") == ("NEXUS", "phone")
    assert agents.route("öffne YouTube") == ("NEXUS", "phone")
    assert agents.route("öffne YouTube am PC") in (("NEXUS", "pc"), ("NEXUS", "action"))
    assert agents.route("oeffne YouTube", from_phone=False) == ("NEXUS", "action")
    assert agents.route("Systemstatus") == ("NEXUS", "action")
    assert agents.route("wie spät ist es") == ("NEXUS", "phone")
    assert agents.route("wie spaet ist es") == ("NEXUS", "phone")
    assert agents.route("lauter") == ("NEXUS", "phone")
    assert agents.route("wie spät ist es", from_phone=False) == ("NEXUS", "action")
    assert agents.route("lauter", from_phone=False) == ("NEXUS", "action")
    assert agents.route("suche Python Tutorials") == ("NEXUS", "phone")
    assert agents.route("suche Python Tutorials", from_phone=False) == ("NEXUS", "action")
    # Plaudern bleibt schnell (kein Engine-Pfad), auch wenn "heute" den Crew-Auto-Router treffen wuerde.
    assert agents.route("Hallo, wie gehts dir?") == ("NEXUS", "talk")
    assert agents.route("Was machen wir heute?") == ("NEXUS", "talk")
    # Bestehende Spezialfaelle unveraendert
    assert agents.route("Auftrag: lege eine Notiz an") == ("NEXUS", "order")
    assert agents.route("Hermes, wie ist die CPU-Last?") == ("Hermes", "talk")


class _ActionEng:
    def __init__(self, result):
        self.sessions = {}
        self.session_id = "main"
        self.called = []
        self._result = result
        self.master = type("M", (), {})()

    def _empty_session(self, sid):
        return {"id": sid, "messages": []}

    def switch_session(self, sid):
        self.session_id = sid

    def chat(self, text, sid):
        self.called.append((text, sid))
        return dict(self._result)


def test_action_opens_youtube_via_engine_chat():
    eng = _ActionEng({"success": True, "reply": "App geoeffnet: youtube", "intent": "os_launch_app", "model": None})
    meta = {}
    out = "".join(agents.reply_stream(eng, "NEXUS", "action", "oeffne YouTube", [], "LTtest01", meta))
    assert len(eng.called) == 1 and eng.called[0][1] == "CALL-LTtest01"
    assert "youtube" in eng.called[0][0].lower() and "ffne" in eng.called[0][0].lower()
    assert "youtube" in out.lower()
    assert meta.get("verified") is True
    assert meta.get("engine") is True
    assert meta.get("intent") == "os_launch_app"
    assert meta.get("success") is True


def test_action_privileged_not_faked():
    eng = _ActionEng({
        "success": False,
        "reply": "BLOCKED: Dieser Befehl braucht eine Freigabe (approval_id).",
        "intent": "shell",
        "error": "PRIVILEGED",
        "model": None,
    })
    meta = {}
    out = "".join(agents.reply_stream(eng, "NEXUS", "action", "powershell: Remove-Item C:\\Windows", [], "LTtest01", meta))
    assert "Freigabe" in out or "BLOCKED" in out
    assert meta.get("success") is False
    assert meta.get("verified") is True
    assert not eng.called or "C:\\Windows" in eng.called[0][0]


def test_talk_does_not_call_engine_chat():
    eng = _Eng()
    def _boom(*a, **k):
        raise AssertionError("engine.chat must not run on talk path")
    eng.chat = _boom
    meta = {}
    out = "".join(agents.reply_stream(eng, "NEXUS", "talk", "Wie gehts dir?", [], "LTtest01", meta))
    assert out == "Hallo Eddie. Alles gut."
    assert meta.get("engine") is None


def test_spoken_result_shortens_and_strips_markdown():
    long_reply = "# Status\n\n" + ("NEXUS ist online. " * 40) + "Zweiter Satz bleibt. Dritter Satz auch. Vierter Satz nicht mehr."
    out = agents._spoken_result({"reply": long_reply, "success": True})
    assert "#" not in out
    assert len(out) <= 420
    assert "ist online" in out.lower()
    assert agents._spoken_result({"reply": ""}) == "Der Befehl kam ohne Antwort zurueck."

def test_talk_uses_qwen_9b():
    eng = _Eng()
    meta = {}
    "".join(agents.reply_stream(eng, "NEXUS", "talk", "Hallo", [], "LTtest01", meta))
    assert eng.master.llm.seen_model == "qwen3.5:9b"


def test_phone_routes():
    assert agents.route("oeffne YouTube auf dem Handy") == ("NEXUS", "phone")
    assert agents.route("mach das Handy lauter") == ("NEXUS", "phone")
    assert agents.route("wie ist der Akku vom Handy") == ("NEXUS", "phone")
    assert agents.route("Screenshot vom Handy") == ("NEXUS", "phone")
    # Vom Handy ohne "am PC" geht YouTube an das Handy; am PC bleibt der Orchestrator.
    assert agents.route("oeffne YouTube") == ("NEXUS", "phone")
    assert agents.route("oeffne YouTube", from_phone=False) == ("NEXUS", "action")
    assert agents.route("Was machen wir heute?") == ("NEXUS", "talk")


def test_phone_open_youtube_mocked(monkeypatch):
    called = {}
    def fake_open(name, source="voice"):
        called["name"] = name
        called["source"] = source
        return {"success": True, "reply": "YouTube ist auf dem Handy offen."}
    import nexus.phone_actions as pa
    monkeypatch.setattr(pa, "open_phone_app", fake_open)
    meta = {}
    eng = _Eng()
    out = "".join(agents.reply_stream(eng, "NEXUS", "phone", "oeffne YouTube auf dem Handy", [], "LTtest01", meta))
    assert "YouTube" in out and called.get("source") == "livetalk"
    assert meta.get("verified") and meta.get("intent") == "open_phone_app"
    assert meta.get("model") == "phone_adb"


def test_short_app_commands_go_to_phone():
    assert agents.route("YouTube") == ("NEXUS", "phone")
    assert agents.route("YouTube, YouTube.") == ("NEXUS", "phone")
    assert agents.route("spiel Musik") == ("NEXUS", "phone")
    assert agents.route("spiel Musik ab") == ("NEXUS", "phone")
    assert agents.route("spiel Musik am PC") == ("NEXUS", "action")
    # Plaudern bleibt Plaudern
    assert agents.route("Wie gehts dir?") == ("NEXUS", "talk")


def test_web_and_clock_and_weather_routes():
    assert agents.route("Was ist in Deutschland aktuell los?") == ("NEXUS", "web")
    assert agents.route("Was machen wir heute?") == ("NEXUS", "talk")
    assert agents.route("Wie wird das Wetter morgen in Gelsenkirchen?") == ("NEXUS", "weather")
    assert agents.route("Wie spät ist es?") == ("NEXUS", "phone")
    assert "Europe/Berlin" in agents._now_berlin()
    assert "keine Live-Daten" not in agents.LIVE_SYSTEM
    assert "koenntest Geraete nicht steuern" in agents.LIVE_SYSTEM


def test_talk_includes_berlin_clock():
    eng = _Eng()
    meta = {}
    "".join(agents.reply_stream(eng, "NEXUS", "talk", "Wie gehts dir?", [], "LTtest01", meta))
    sysmsg = eng.master.llm.seen[0]["content"]
    assert "Europe/Berlin" in sysmsg
    # Verlauf bleibt an Index 1, wenn es History gibt
    eng = _Eng()
    hist = [{"role": "user", "content": "Mein Hund heißt Bello."},
            {"role": "assistant", "content": "Schöner Name!", "agent": "NEXUS"}]
    "".join(agents.reply_stream(eng, "NEXUS", "talk", "Wie heißt mein Hund?", hist, "LTtest01", {}))
    assert eng.master.llm.seen[1]["content"] == "Mein Hund heißt Bello."


def test_web_stream_filler_and_source(monkeypatch):
    class R:
        @staticmethod
        def search(query, limit=4, news=False):
            return {"success": True, "source": "Bing News", "results": [
                {"title": "Kurz gemeldet", "snippet": "In Deutschland ist heute wenig los.", "url": "https://www.tagesschau.de/x"}
            ]}
    import nexus.knowledge.research as rs
    monkeypatch.setattr(rs, "Research", lambda: R)
    chunks = list(agents.reply_stream(_Eng(), "NEXUS", "web", "Was ist in Deutschland aktuell los?", [], "LTtest01", {}))
    assert chunks[0].startswith("Moment, ich schau nach")
    assert any("tagesschau.de" in c.lower() or "Laut" in c for c in chunks)
    assert "In Deutschland ist heute wenig los" in "".join(chunks)


def test_web_stream_honest_failure(monkeypatch):
    class R:
        @staticmethod
        def search(query, limit=4, news=False):
            raise RuntimeError("offline")
    import nexus.knowledge.research as rs
    monkeypatch.setattr(rs, "Research", lambda: R)
    out = "".join(agents.reply_stream(_Eng(), "NEXUS", "web", "News", [], "LTtest01", {}))
    assert "Moment, ich schau nach" in out
    assert "fehlgeschlagen" in out.lower() or "nicht an die Suche" in out


def test_weather_default_place_and_tomorrow(monkeypatch):
    seen = {}
    def fake_weather(text, default_place=None):
        seen["place"] = default_place
        seen["text"] = text
        return {"success": True, "found": True, "place": default_place, "source": "open-meteo.com",
                "day": "Morgen", "spoken": "Morgen wird es in Gelsenkirchen bewoelkt, zwischen 7 und 12 Grad."}
    import nexus.voice.live as live
    monkeypatch.setattr(live, "weather", fake_weather)
    out = "".join(agents.reply_stream(_Eng(), "NEXUS", "weather", "Wie wird das Wetter morgen in Gelsenkirchen?", [], "LTtest01", {}))
    assert seen["place"] == "Gelsenkirchen"
    assert "Morgen" in out and "Gelsenkirchen" in out


def test_phone_messages_and_outbox(monkeypatch, tmp_path):
    from nexus.livetalk.phone_link import PhoneLink, speak_notifications, find_and_stage_file
    items = [{"id": "n1", "title": "Anna", "text": "Bin in 10 Min da", "app": "WhatsApp", "can_reply": True,
              "actions": [{"title": "Antworten", "remote": True}]}]
    assert "Anna" in speak_notifications(items) and "10 Min" in speak_notifications(items)
    assert "keine Nachrichten" in speak_notifications([])

    class L(PhoneLink):
        def notifications_live(self):
            return items
        def request(self, cmd, timeout=4.0, **payload):
            self.called = getattr(self, "called", [])
            self.called.append((cmd, payload))
            if cmd == "prepare_reply":
                return {"ok": True, "spoken": "Antwort an Anna: Bin unterwegs. Sag senden, wenn ich sie abschicken soll."}
            return {"ok": True}
    link = L()
    link.remember_notification(items[0])
    meta = {"phone": link, "from_phone": True}
    out = "".join(agents.reply_stream(_Eng(), "NEXUS", "phone", "lies meine Nachrichten", [], "LTtest01", meta))
    assert "Anna" in out
    out = "".join(agents.reply_stream(_Eng(), "NEXUS", "phone", "antworte Anna: Bin unterwegs", [], "LTtest01", meta))
    assert "senden" in out.lower() and link.pending_reply
    out = "".join(agents.reply_stream(_Eng(), "NEXUS", "phone", "senden", [], "LTtest01", meta))
    assert "unterwegs" in out.lower() and link.pending_reply is None

    # Outbox: Datei nur auf G:
    root = Path(r"G:\meine-ki\nexus-ai\workspace\tmp")
    root.mkdir(parents=True, exist_ok=True)
    src = root / "lt_outbox_probe.txt"
    src.write_text("hallo vom pc", encoding="utf-8")
    r = find_and_stage_file("lt_outbox_probe.txt")
    assert r.get("success"), r
    assert "outbox" in (r.get("rel") or "")
    assert Path(r["path"]).is_file()
    assert agents.route("schick mir Datei lt_outbox_probe.txt aufs Handy") == ("NEXUS", "phone")


def test_weather_day_offset():
    from nexus.voice.live import day_offset
    assert day_offset("Wie wird das Wetter morgen in Gelsenkirchen?") == (1, "Morgen")
    assert day_offset("Wetter heute")[0] == 0
    assert day_offset("Wetter uebermorgen")[0] == 2


def test_spiel_techno_is_youtube_play():
    import nexus.phone_voice as pv
    from nexus.phone_actions import find_first_youtube_video, _youtube_search_query
    cmd = pv.parse("spiel techno")
    assert cmd and cmd["intent"] == "youtube_play" and cmd["query"] == "techno"
    assert agents.route("spiel techno") == ("NEXUS", "phone")
    assert agents.route("spiel techno am PC") != ("NEXUS", "phone")
    assert _youtube_search_query("techno") == "techno mix"
    xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy><node bounds="[40,450][1040,900]" clickable="true"
 content-desc="Techno Mix 2026. 1 Stunde. 2 Mio. Aufrufe."
 resource-id="com.google.android.youtube:id/thumbnail" /></hierarchy>"""
    hit = find_first_youtube_video(xml)
    assert hit and hit["x"] == 540 and "Techno Mix" in hit["desc"]
