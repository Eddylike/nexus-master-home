"""Live Talk: Nachrichten per Sprache schreiben (Antwort ueber die Benachrichtigung, senden nur nach 'senden')."""
import json

import pytest

from nexus.livetalk import agents
from nexus.livetalk.phone_link import PhoneLink


def _items():
    return [
        {"id": "0|com.whatsapp.w4b|1|a|10", "key": "0|com.whatsapp.w4b|1|a|10", "pkg": "com.whatsapp.w4b",
         "app": "WhatsApp", "title": "Kathleen", "text": "Sprachnachricht (0:03)", "when": 2000,
         "can_reply": True, "actions": [{"title": "Antworten", "remote": True}]},
        {"id": "0|com.facebook.orca|2|b|10", "key": "0|com.facebook.orca|2|b|10", "pkg": "com.facebook.orca",
         "app": "Messenger", "title": "Ramona", "text": "Hi", "when": 1000,
         "can_reply": True, "actions": [{"title": "Antworten", "remote": True}]},
    ]


class FakeLink(PhoneLink):
    def __init__(self, items=None, confirm=None, prepare=None):
        super().__init__(send_cmd=None)
        self._items = _items() if items is None else items
        self.calls = []
        self._confirm = confirm or {"ok": True, "result": json.dumps({"ok": True, "sent": True})}
        self._prepare = prepare

    def notifications_live(self):
        for it in self._items:
            self.remember_notification(it)
        return self.fresh_notifications()

    def request(self, cmd, timeout=4.0, **payload):
        self.calls.append((cmd, payload))
        if cmd == "prepare_reply":
            return self._prepare if self._prepare is not None else {
                "ok": True, "result": json.dumps({"ok": True, "draft": payload.get("text")})}
        if cmd == "confirm_send":
            return self._confirm
        return {"ok": True}


class _Eng:
    pass


def _say(link, text):
    agent, mode = agents.route(text, from_phone=True, phone=link)
    if mode != "phone":
        return mode, ""
    meta = {"phone": link, "from_phone": True}
    return mode, "".join(agents.reply_stream(_Eng(), agent, mode, text, [], "LTwrite", meta))


def _cmds(link):
    return [c for c, _ in link.calls]


@pytest.mark.parametrize("text", [
    "Schreib Katlin Hallo", "Schreibt Katlin Hallo.", "Schreib mal Kathleen Hallo", "Schreibe Kathleen: Hallo",
    "Antworte Kathleen Hallo", "Schick Kathleen Hallo", "Schreib Kathleen auf WhatsApp Hallo",
    "Schreib eine Nachricht an Kathleen: Hallo",
])
def test_write_variants_draft_and_never_send(text):
    link = FakeLink()
    mode, out = _say(link, text)
    assert mode == "phone"
    assert out == "Antwort an Kathleen: Hallo. Sag senden, wenn ich sie abschicken soll."
    assert link.pending_reply and link.pending_reply["who"] == "Kathleen"
    assert "confirm_send" not in _cmds(link)
    prep = [p for c, p in link.calls if c == "prepare_reply"][0]
    assert prep["id"] == "0|com.whatsapp.w4b|1|a|10" and prep["text"] == "Hallo"


def test_two_step_who_then_text():
    link = FakeLink()
    assert _say(link, "Schreib eine Nachricht.")[1] == "An wen soll die Nachricht gehen?"
    assert agents.route("Katlin", phone=link) == ("NEXUS", "phone")
    assert _say(link, "Katlin")[1] == "Was soll ich Kathleen schreiben?"
    out = _say(link, "Ich ruf dich gleich an.")[1]
    assert out.startswith("Antwort an Kathleen: Ich ruf dich gleich an.")
    assert link.pending_write is None and link.pending_reply
    assert "confirm_send" not in _cmds(link)


def test_two_step_name_only():
    link = FakeLink()
    assert _say(link, "Schreib Katlin.")[1] == "Was soll ich Kathleen schreiben?"
    assert _say(link, "Hallo")[1].startswith("Antwort an Kathleen: Hallo.")


def test_senden_only_after_spoken_senden():
    link = FakeLink()
    _say(link, "Schreib Kathleen Hallo")
    for not_send in ("ja", "okay", "passt"):
        assert agents._phone_msg_command(not_send, link) is None or \
            agents._phone_msg_command(not_send, link).get("intent") != "phone_confirm_send"
    assert "confirm_send" not in _cmds(link)
    out = _say(link, "senden")[1]
    assert "unterwegs" in out and "Kathleen" in out
    assert _cmds(link).count("confirm_send") == 1 and link.pending_reply is None


def test_abbrechen_discards():
    link = FakeLink()
    _say(link, "Schreib Kathleen Hallo")
    out = _say(link, "abbrechen")[1]
    assert "verworfen" in out and link.pending_reply is None
    assert "cancel_reply" in _cmds(link) and "confirm_send" not in _cmds(link)
    link2 = FakeLink()
    _say(link2, "Schreib eine Nachricht")
    _say(link2, "abbrechen")
    assert link2.pending_write is None


def test_confirm_failure_is_reported_not_claimed():
    gone = {"ok": True, "result": json.dumps({"ok": False, "error": "notification_gone"})}
    link = FakeLink(confirm=gone)
    _say(link, "Schreib Kathleen Hallo")
    out = _say(link, "senden")[1]
    assert "unterwegs" not in out and "Statusleiste" in out
    link = FakeLink(confirm={"ok": True, "result": json.dumps({"ok": False, "error": "x"})})
    _say(link, "Schreib Kathleen Hallo")
    assert "nicht geklappt" in _say(link, "senden")[1]
    link = FakeLink(confirm=None)
    link._confirm = None
    link.request = lambda cmd, timeout=4.0, **p: None if cmd == "confirm_send" else {"ok": True}
    link.pending_reply = {"id": "x", "who": "Kathleen", "body": "Hallo"}
    out = "".join(agents.reply_stream(_Eng(), "NEXUS", "phone", "senden", [], "LTw", {"phone": link}))
    assert "unterwegs" not in out and "Rueckmeldung" in out


def test_prepare_not_found_is_honest():
    link = FakeLink(prepare={"ok": True, "result": json.dumps({"ok": False, "error": "not_found"})})
    out = _say(link, "Schreib Kathleen Hallo")[1]
    assert "nicht" in out and not link.pending_reply


def test_no_notification_honest_and_no_contacts(monkeypatch):
    from nexus import phone_actions as pa
    monkeypatch.setattr(pa, "resolve_contact", lambda *a, **k: pytest.fail("Kontakte duerfen nicht gelesen werden"),
                        raising=False)
    link = FakeLink()
    out = _say(link, "Schreib Heinz Hallo")[1]
    assert out.startswith("Ich kann Heinz gerade nur antworten, wenn eine Nachricht von Heinz in der Statusleiste liegt")
    assert "prepare_reply" not in _cmds(link)


def test_explicit_whatsapp_without_notification_uses_existing_flow(monkeypatch):
    from nexus.voice import commands as vc
    seen = {}
    monkeypatch.setattr(vc, "parse", lambda t: {"intent": "whatsapp_send", "contact": "Heinz", "message": "Hallo", "text": t})
    monkeypatch.setattr(vc, "_whatsapp_flow", lambda p, src: seen.update(p=p, src=src) or
                        {"success": True, "reply": "Nachricht an Heinz: Hallo. Soll ich senden? Sag ja oder nein."})
    link = FakeLink()
    out = _say(link, "Schreib Heinz auf WhatsApp Hallo")[1]
    assert seen["src"] == "livetalk" and "Heinz" in out


def test_notification_wins_over_commands_flow(monkeypatch):
    from nexus.voice import commands as vc
    monkeypatch.setattr(vc, "_whatsapp_flow", lambda p, src: pytest.fail("Benachrichtigung muss gewinnen"))
    link = FakeLink()
    out = _say(link, "Schreib Kathleen auf WhatsApp Hallo")[1]
    assert out.startswith("Antwort an Kathleen: Hallo.")


def test_senden_falls_back_to_commands_pending(monkeypatch):
    from nexus.voice import commands as vc
    monkeypatch.setattr(vc, "_pending", {"abc": {"id": "abc"}})
    got = {}
    monkeypatch.setattr(vc, "handle_confirm", lambda b, e: got.update(b) or {"success": True, "reply": "Gesendet."})
    link = FakeLink()
    out = _say(link, "senden")[1]
    assert got == {"id": "abc", "answer": "ja"} and "Gesendet" in out
    assert "confirm_send" not in _cmds(link)


def test_reply_to_last_read_sender():
    link = FakeLink()
    out = _say(link, "Lies mir meine Nachrichten vor.")[1]
    assert "Kathleen" in out
    assert link.last_sender == "Kathleen"
    assert _say(link, "Antworte ihr bin gleich da")[1].startswith("Antwort an Kathleen: bin gleich da.")


def test_ambiguous_name_asks():
    items = _items() + [{"id": "k3", "key": "k3", "pkg": "com.whatsapp", "app": "WhatsApp", "title": "Katrin",
                         "text": "Hey", "can_reply": True, "actions": [{"title": "Antworten", "remote": True}]}]
    link = FakeLink(items=items)
    out = _say(link, "Schreib Katlin Hallo")[1]
    assert out.startswith("Meinst du") and "Kathleen" in out and "Katrin" in out
    assert "prepare_reply" not in _cmds(link)
    out = _say(link, "Kathleen")[1]
    assert out.startswith("Antwort an Kathleen: Hallo.")


def test_sag_only_with_known_sender_or_separator():
    link = FakeLink()
    link.notifications_live()
    assert agents.route("Sag mal, wie geht's dir?", phone=link)[1] != "phone"
    assert agents.route("Sag Hallo", phone=link)[1] != "phone"
    assert agents.route("Sag Ramona bis gleich", phone=link) == ("NEXUS", "phone")
    assert agents.route("Sag Ramona, dass ich später komme", phone=link) == ("NEXUS", "phone")


def test_not_hijacked():
    for t in ("Schreib mir ein Gedicht", "Schreib das auf", "Wie geht es dir?", "Was machen wir heute?"):
        assert agents._parse_write(t) is None, t
    assert agents.route("schick mir Datei lt_outbox_probe.txt aufs Handy") == ("NEXUS", "phone")
    assert agents._phone_msg_command("schick mir Datei x.txt aufs Handy")["intent"] == "phone_send_file"


def test_name_fuzzy():
    assert agents._name_score("Katlin", "Kathleen") >= agents._MATCH_MIN
    assert agents._name_score("Heinz", "Kathleen") < agents._MATCH_MIN


def test_open_question_really_opens(monkeypatch):
    from nexus import phone_actions as pa
    opened = []
    monkeypatch.setattr(pa, "open_phone_app", lambda name, source="voice": opened.append(name) or
                        {"success": True, "reply": "WhatsApp ist auf dem Handy offen."})
    assert agents.route("Kannst du WhatsApp öffnen?") == ("NEXUS", "phone")
    out = "".join(agents.reply_stream(_Eng(), "NEXUS", "phone", "Kannst du WhatsApp öffnen?", [], "LTw", {}))
    assert opened == ["whatsapp"] and "offen" in out
    opened.clear()
    monkeypatch.setattr(pa, "open_phone_app", lambda name, source="voice": opened.append(name) or
                        {"success": False, "reply": f"Die App {name} kenne ich auf dem Handy nicht."})
    out = "".join(agents.reply_stream(_Eng(), "NEXUS", "phone", "Kannst du Zorblax öffnen?", [], "LTw", {}))
    assert opened == ["Zorblax"] and "kenne ich" in out
    # Kalender steht seit 17.11. im App-Katalog und wird kanonisch aufgeloest
    opened.clear()
    monkeypatch.setattr(pa, "open_phone_app", lambda name, source="voice": opened.append(name) or
                        {"success": True, "reply": "Kalender ist auf dem Handy offen."})
    out = "".join(agents.reply_stream(_Eng(), "NEXUS", "phone", "Kannst du den Kalender öffnen?", [], "LTw", {}))
    assert opened == ["kalender"] and "offen" in out


def test_guard_blocks_fake_open_and_send():
    for s in ("WhatsApp ist jetzt geöffnet.", "Der Kalender ist jetzt geöffnet.", "Ich habe die Nachricht gesendet."):
        out, stop = agents.guard_sentence(s, {})
        assert stop and "nichts" in out, s
    assert agents.guard_sentence("Kathleen hat dir eine Sprachnachricht gesendet.", {}) == \
        ("Kathleen hat dir eine Sprachnachricht gesendet.", False)
    assert agents.guard_sentence("WhatsApp ist auf dem Handy offen.", {"verified": True})[1] is False
