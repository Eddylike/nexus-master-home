"""Regression: 'lies mir meine whatsapp-nachrichten vor' darf nicht in research/Websuche landen."""
import sqlite3
from nexus.master.decision import decide
from nexus.computer import notifications as nt


def test_whatsapp_read_routes_to_tool():
    for m in ("lies mir meine whatsapp-nachrichten vor", "Hey Nexus, lies meine WhatsApp Nachrichten vor",
              "lies mir meine benachrichtigung vor", "lies mir meine benachrichtigungen vor"):
        d = decide(m)
        assert d["intent"] == "notifications_read", (m, d)
    assert decide("lies mir meine whatsapp-nachrichten vor")["app"] == "whatsapp"


def test_send_and_news_not_hijacked():
    assert decide("schreib Mama auf WhatsApp ich komme spaeter")["intent"] != "notifications_read"
    assert decide("nachrichten heute")["intent"] != "notifications_read"


def test_reader_readonly_tmpdb(tmp_path):
    db = tmp_path / "wpn.db"
    c = sqlite3.connect(db)
    c.execute("create table NotificationHandler(RecordId int, PrimaryId text)")
    c.execute("create table Notification(HandlerId int, Type text, Payload blob, ArrivalTime int)")
    c.execute("insert into NotificationHandler values(1,'5319275A.WhatsAppDesktop_x!App'),(2,'Other!App')")
    p = b"<toast><visual><binding><text>Anna</text><text>Hallo</text></binding></visual></toast>"
    c.execute("insert into Notification values(1,'toast',?,134382608986554336),(2,'toast',?,134382608986554336)", (p, p))
    c.commit(); c.close()
    out = nt.read_notifications(app="whatsapp", db_path=str(db))
    assert out["success"] and out["count"] == 1 and out["items"][0]["sender"] == "Anna"
    assert nt.read_notifications(app="whatsapp", db_path=str(tmp_path / "none.db"))["success"] is False
