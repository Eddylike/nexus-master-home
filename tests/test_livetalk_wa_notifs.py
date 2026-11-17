"""Live Talk: WhatsApp & Co. beim Vorlesen (Store + ADB-Merge, MessagingStyle, Reihenfolge).

Keine Geraetezugriffe, keine Dateien, kein Metriklog.
"""
from nexus.livetalk import agents
from nexus.livetalk import notif_util as nu
from nexus.livetalk.phone_link import PhoneLink, speak_notifications

DUMP = """
NOTIFICATION MANAGER (dumpsys notification)
  Notification List:
    NotificationRecord(0x01: pkg=com.whatsapp.w4b user=UserHandle{0} id=1 tag=null importance=4 key=0|com.whatsapp.w4b|1|null|10367 bbbc=0: Notification(channel=group_chat_defaults_5 flags=ONLY_ALERT_ONCE|GROUP_SUMMARY category=msg vis=PRIVATE))
      flags=ONLY_ALERT_ONCE|GROUP_SUMMARY
      key=0|com.whatsapp.w4b|1|null|10367
      notification=
            when=1794895346684/1794895346684
            extras={
                android.title=String (WA Business)
                android.text=String (2 Nachrichten aus 2 Chats)
            }
      mUpdateTimeMs=1794896639615
    NotificationRecord(0x02: pkg=com.whatsapp.w4b user=UserHandle{0} id=11 tag=ramona@s.whatsapp.net importance=4 key=0|com.whatsapp.w4b|11|ramona@s.whatsapp.net|10367 bbbc=0: Notification(channel=individual_chat_defaults_5 flags=ONLY_ALERT_ONCE category=msg actions=2 vis=PRIVATE))
      flags=ONLY_ALERT_ONCE
      key=0|com.whatsapp.w4b|11|ramona@s.whatsapp.net|10367
      notification=
            when=1794895453056/1794895453056
            extras={
                android.title=String (Ramona)
                android.text=String (ja und du?)
                android.selfDisplayName=String (Du)
                android.messages=Bundle[] (2)
                  [0] Bundle[{sender_person=android.app.Person@1, sender=Ramona, text=Hallo Eddie, wie gehts, time=1794895400000}]
                  [1] Bundle[{sender_person=android.app.Person@1, sender=Ramona, text=ja und du?, time=1794895453056}]
                android.isGroupConversation=Boolean (false)
            }
      publicNotification=
            extras={
                android.title=String (WA Business)
                android.text=String (1 neue Nachricht)
            }
      mUpdateTimeMs=1794895453306
    NotificationRecord(0x03: pkg=com.whatsapp.w4b user=UserHandle{0} id=12 tag=chef@s.whatsapp.net importance=4 key=0|com.whatsapp.w4b|12|chef@s.whatsapp.net|10367 bbbc=0: Notification(channel=individual_chat_defaults_5 flags=ONLY_ALERT_ONCE category=msg vis=PRIVATE))
      flags=ONLY_ALERT_ONCE
      key=0|com.whatsapp.w4b|12|chef@s.whatsapp.net|10367
      notification=
            extras={
                android.title=String (Du)
                android.text=String (Ich suche)
                android.messages=Bundle[] (2)
                  [0] Bundle[{sender=Chef, text=Wo bist du, time=1794895040000}]
                  [1] Bundle[{sender=null, text=Ich suche, time=1794895047000}]
            }
      mUpdateTimeMs=1794895346718
    NotificationRecord(0x04: pkg=com.whatsapp.w4b user=UserHandle{0} id=13 tag=g1 importance=4 key=0|com.whatsapp.w4b|13|g1|10367 bbbc=0: Notification(channel=group_chat_defaults_5 flags=0 category=msg vis=PRIVATE))
      flags=0
      key=0|com.whatsapp.w4b|13|g1|10367
      notification=
            extras={
                android.title=String (Urbex Crew)
                android.text=String (Morgen 10 Uhr)
                android.conversationTitle=String (Urbex Crew)
                android.selfDisplayName=String (Eddy)
                android.messages=Bundle[] (2)
                  [0] Bundle[{sender=Tom, text=Morgen 10 Uhr, time=1794895000000}]
                  [1] Bundle[{sender=Eddy, text=passt, time=1794895010000}]
                android.isGroupConversation=Boolean (true)
            }
      mUpdateTimeMs=1794895010000
    NotificationRecord(0x05: pkg=com.zhiliaoapp.musically user=UserHandle{0} id=5 tag=x importance=4 key=0|com.zhiliaoapp.musically|5|x|10200 bbbc=0: Notification(channel=im flags=AUTO_CANCEL category=msg vis=PRIVATE))
      flags=AUTO_CANCEL
      key=0|com.zhiliaoapp.musically|5|x|10200
      notification=
            extras={
                android.title=String (Lena)
                android.text=String (hast du das gesehen)
            }
      mUpdateTimeMs=1794896259131
    NotificationRecord(0x06: pkg=net.lovoo.android user=UserHandle{0} id=6 tag=appboy importance=3 key=0|net.lovoo.android|6|appboy|10201 bbbc=0: Notification(channel=promo flags=AUTO_CANCEL vis=PRIVATE))
      flags=AUTO_CANCEL
      key=0|net.lovoo.android|6|appboy|10201
      notification=
            extras={
                android.title=String (Others are noticing you)
                android.text=String (Do you know who is looking)
            }
      mUpdateTimeMs=1794896637225
    NotificationRecord(0x07: pkg=org.videolan.vlc user=UserHandle{0} id=3 tag=null importance=2 key=0|org.videolan.vlc|3|null|10313 bbbc=0: Notification(channel=vlc_playback flags=0 category=transport vis=PUBLIC))
      flags=0
      key=0|org.videolan.vlc|3|null|10313
      notification=
            extras={
                android.title=String (Kalt wie Beton)
                android.text=String (eddyurbexer)
            }
      mUpdateTimeMs=1794387156270
    NotificationRecord(0x08: pkg=com.huawei.health user=UserHandle{0} id=10010 tag=null importance=3 key=0|com.huawei.health|10010|null|10336 bbbc=0: Notification(channel=steps flags=ONGOING_EVENT|NO_CLEAR vis=PRIVATE))
      flags=ONGOING_EVENT|NO_CLEAR
      key=0|com.huawei.health|10010|null|10336
      notification=
            extras={
                android.title=String (1.133 Schritte)
                android.text=String (Heute 11 % abgeschlossen)
            }
      mUpdateTimeMs=1794897060597
"""


def test_parse_whatsapp_business_messagingstyle():
    keys = set()
    items = nu.parse_android_notification_dump(DUMP, keys)
    by_title = {i["title"]: i for i in items}
    # Summary, eigene Nachricht, Medien, Schrittzaehler raus
    assert "WA Business" not in by_title
    assert "Du" not in by_title and not any(i["text"] == "Ich suche" for i in items)
    assert "Kalt wie Beton" not in by_title
    assert not any(i["pkg"] == "com.huawei.health" for i in items)
    ramona = by_title["Ramona"]
    assert ramona["pkg"] == "com.whatsapp.w4b" and ramona["app"] == "WhatsApp"
    assert ramona["text"] == "ja und du?" and ramona["sender"] == "Ramona"
    # Gruppe: letzte Nachricht vom Gegenueber, nicht die eigene
    assert by_title["Tom in Urbex Crew"]["text"] == "Morgen 10 Uhr"
    assert by_title["Lena"]["category"] == "msg"
    # alle aktiven Keys (auch Summary/VLC) fuer den Weggewischt-Abgleich
    assert "0|com.whatsapp.w4b|1|null|10367" in keys and "0|org.videolan.vlc|3|null|10313" in keys


def test_merge_store_and_adb_messenger_first():
    adb = nu.parse_android_notification_dump(DUMP)
    store = [
        {"id": "0|net.lovoo.android|6|appboy|10201", "key": "0|net.lovoo.android|6|appboy|10201",
         "pkg": "net.lovoo.android", "app": "LOVOO", "title": "Others are noticing you",
         "text": "Do you know who is looking", "when": 1794896634295},
        {"id": "0|com.whatsapp.w4b|11|ramona@s.whatsapp.net|10367", "key": "0|com.whatsapp.w4b|11|ramona@s.whatsapp.net|10367",
         "pkg": "com.whatsapp.w4b", "app": "WA Business", "title": "Ramona", "text": "ja und du?",
         "can_reply": True, "actions": [{"title": "Antworten", "remote": True}], "category": "msg"},
    ]
    ranked = nu.rank_notifications(nu.merge_notifications(store, adb))
    titles = [n["title"] for n in ranked]
    assert titles.count("Ramona") == 1  # dedupliziert
    ram = next(n for n in ranked if n["title"] == "Ramona")
    assert ram["can_reply"] is True  # Antworten-Aktion aus dem Store bleibt
    assert titles[-1] == "Others are noticing you"  # Werbung nachrangig
    msg_part = [n for n in ranked if nu.is_message(n)]
    assert ranked[:len(msg_part)] == msg_part
    # neueste Nachricht zuerst
    assert titles[0] == "Lena"


def test_speak_reads_whatsapp_fully_through_reply_stream(monkeypatch):
    adb_items = nu.parse_android_notification_dump(DUMP)
    keys = set()
    nu.parse_android_notification_dump(DUMP, keys)
    monkeypatch.setattr(nu, "fetch_android_snapshot",
                        lambda: {"ok": True, "items": adb_items, "active_keys": keys})

    class L(PhoneLink):
        def request(self, cmd, timeout=4.0, **payload):
            if cmd == "get_notifications":
                # Store hat nur VLC + Spam (WhatsApp ist nach 10 Min aus dem App-Store gefallen)
                return {"ok": True, "notifications": [
                    {"id": "0|org.videolan.vlc|3|null|10313", "pkg": "org.videolan.vlc", "title": "Kalt wie Beton",
                     "text": "eddyurbexer", "category": "transport"},
                    {"id": "0|net.lovoo.android|6|appboy|10201", "pkg": "net.lovoo.android", "app": "LOVOO",
                     "title": "Others are noticing you", "text": "Do you know who is looking"}]}
            return None

    link = L()
    # alter, am Handy weggewischter Eintrag darf nicht mehr vorgelesen werden
    link.remember_notification({"id": "0|com.whatsapp.w4b|99|old|10367", "pkg": "com.whatsapp.w4b",
                                "title": "Alt", "text": "weggewischt"})
    meta = {"phone": link, "from_phone": True}
    out = "".join(agents.reply_stream(None, "NEXUS", "phone", "Lies mir meine Nachrichten vor.", [], "LTtest01", meta))
    assert meta.get("intent") == "phone_read_notifications"
    assert "Ramona" in out and "ja und du" in out and "WhatsApp" in out
    assert "Tom in Urbex Crew" in out and "Lena" in out
    assert "Kalt wie Beton" not in out and "weggewischt" not in out and "Ich suche" not in out
    assert out.index("Ramona") < out.index("Others are noticing")
    assert link.find_notification("Ramona")


def test_speak_numbering_not_cut_by_sentence_limit():
    items = [{"id": f"n{i}", "pkg": "com.whatsapp", "title": f"Kontakt{i}", "text": f"Text {i}", "when": 1000 + i}
             for i in range(4)]
    spoken = agents._spoken_result({"reply": speak_notifications(items)}, full=True)
    for i in range(4):
        assert f"Kontakt{i}" in spoken
    assert spoken.index("Kontakt3") < spoken.index("Kontakt0")  # neueste zuerst