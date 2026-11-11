"""Tests fuer nexus/phone_api.py (NEXUS Phone 002). Nutzt nur temp-Verzeichnisse."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nexus import phone_api as pa  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self._old = (pa.DATA_DIR, pa.CONTACTS_PATH)
        pa.DATA_DIR = d / "phone_data"
        pa.CONTACTS_PATH = d / "apex" / "contacts.json"
        pa.CONTACTS_PATH.parent.mkdir(parents=True)
        pa.CONTACTS_PATH.write_text(json.dumps({"_info": "x", "contacts": [
            {"example": True, "name": "Beispiel", "aliases": [], "whatsapp": "+491510000001"},
            {"name": "Mama", "aliases": ["mutti"], "whatsapp": "+491510000002"}]}), encoding="utf-8")

    def tearDown(self):
        pa.DATA_DIR, pa.CONTACTS_PATH = self._old
        self.tmp.cleanup()

    def get(self, p, **q):
        return pa.handle_get(p, {k: [v] for k, v in q.items()})

    def post(self, p, b):
        return pa.handle_post(p, b)


class Contacts(Base):
    def test_list_skips_example_and_search(self):
        s, r = self.get("/api/contacts")
        self.assertEqual(s, 200)
        self.assertEqual([c["name"] for c in r["contacts"]], ["Mama"])
        self.assertEqual(self.get("/api/contacts", q="mutti")[1]["count"], 1)
        self.assertEqual(self.get("/api/contacts", q="zzz")[1]["count"], 0)

    def test_add_update_delete_keeps_format(self):
        s, r = self.post("/api/contacts", {"name": "Max", "phone": "+49 170 1234567", "aliases": ["maxi"]})
        self.assertEqual(s, 200)
        cid = r["contact"]["id"]
        s, r = self.post("/api/contacts", {"id": cid, "name": "Max M", "whatsapp": "+491701234567"})
        self.assertEqual((s, r["created"], r["contact"]["name"]), (200, False, "Max M"))
        doc = json.loads(pa.CONTACTS_PATH.read_text(encoding="utf-8"))
        self.assertEqual(doc["_info"], "x")
        self.assertTrue(doc["contacts"][0]["example"])
        s, r = self.post("/api/contacts/delete", {"id": cid})
        self.assertEqual(s, 200)
        arch = pa.CONTACTS_PATH.with_name("contacts_deleted.jsonl").read_text(encoding="utf-8")
        self.assertIn("Max M", arch)
        self.assertEqual(self.post("/api/contacts/delete", {"id": cid})[0], 404)

    def test_phone_actions_still_reads(self):
        from nexus import phone_actions
        old = phone_actions.CONTACTS_PATH
        try:
            phone_actions.CONTACTS_PATH = pa.CONTACTS_PATH
            self.post("/api/contacts", {"name": "Neu", "whatsapp": "+491709876543"})
            names = [c["name"] for c in phone_actions._load_local_contacts()]
            self.assertIn("Mama", names)
            self.assertIn("Neu", names)
        finally:
            phone_actions.CONTACTS_PATH = old

    def test_validation(self):
        self.assertEqual(self.post("/api/contacts", {})[0], 400)
        self.assertEqual(self.post("/api/contacts", {"name": "x" * 500})[0], 400)
        self.assertEqual(self.post("/api/contacts", {"name": "a", "phone": "abc"})[0], 400)
        self.assertEqual(self.post("/api/contacts/delete", {"id": "../x"})[0], 400)
        self.assertEqual(self.post("/api/contacts", ["x"])[0], 400)


class Notes(Base):
    def test_crud(self):
        s, r = self.post("/api/notes", {"text": "Milch kaufen\nund Brot"})
        self.assertEqual(s, 200)
        nid = r["note"]["id"]
        self.assertEqual(r["note"]["title"], "Milch kaufen")
        self.assertEqual(self.get("/api/notes")[1]["count"], 1)
        self.assertEqual(self.get("/api/notes", q="brot")[1]["count"], 1)
        self.assertEqual(self.post("/api/notes/delete", {"id": nid})[0], 200)
        self.assertEqual(self.get("/api/notes")[1]["count"], 0)
        self.assertTrue((pa.DATA_DIR / "notes_deleted.jsonl").is_file())
        self.assertEqual(self.post("/api/notes", {"text": "x" * 20000})[0], 400)
        self.assertEqual(self.post("/api/notes", {"text": ""})[0], 400)


class Calendar(Base):
    def test_crud_and_range(self):
        s, r = self.post("/api/calendar", {"title": "Arzt", "start": "2026-10-22T09:00", "end": "2026-10-22T10:00"})
        self.assertEqual(s, 200)
        eid = r["event"]["id"]
        self.post("/api/calendar", {"title": "Später", "start": "2026-11-05T09:00"})
        self.assertEqual(self.get("/api/calendar")[1]["count"], 2)
        r = self.get("/api/calendar", **{"from": "2026-10-22T00:00", "to": "2026-10-23T00:00"})[1]
        self.assertEqual([e["title"] for e in r["events"]], ["Arzt"])
        self.assertEqual(self.get("/api/calendar", **{"from": "kaputt"})[0], 400)
        self.assertEqual(self.post("/api/calendar", {"title": "x", "start": "nope"})[0], 400)
        self.assertEqual(self.post("/api/calendar", {"title": "x", "start": "2026-10-22T10:00", "end": "2026-10-22T09:00"})[0], 400)
        self.assertEqual(self.post("/api/calendar/delete", {"id": eid})[0], 200)
        self.assertTrue((pa.DATA_DIR / "calendar_deleted.jsonl").is_file())


class Timers(Base):
    def test_create_status_cancel(self):
        s, r = self.post("/api/timer", {"seconds": 60, "label": "Tee"})
        self.assertEqual(s, 200)
        tid = r["timer"]["id"]
        self.assertGreater(r["timer"]["remaining_s"], 50)
        self.assertFalse(r["timer"]["done"])
        s, r = self.post("/api/timer", {"seconds": 1, "label": "kurz"})
        time.sleep(1.2)
        lst = {t["label"]: t for t in self.get("/api/timer")[1]["timers"]}
        self.assertTrue(lst["kurz"]["done"])
        self.assertEqual(lst["kurz"]["remaining_s"], 0)
        self.assertEqual(self.post("/api/timer/cancel", {"id": tid})[0], 200)
        self.assertEqual(self.post("/api/timer/cancel", {"id": tid})[0], 409)
        self.assertTrue(json.loads((pa.DATA_DIR / "timers.json").read_text(encoding="utf-8"))["items"])

    def test_validation(self):
        for b in ({"seconds": 0}, {"seconds": -5}, {"seconds": "x"}, {"seconds": 10**9}, {}):
            self.assertEqual(self.post("/api/timer", b)[0], 400, b)


class Device(Base):
    def setUp(self):
        super().setUp()
        self.calls = []
        fake = types.SimpleNamespace(
            select_device=lambda connect=True: {"status": "OK", "serial": "abcd1234", "transport": "usb"},
            _adb=lambda serial, args, timeout=15: (self.calls.append(args) or {"ok": True, "out": "  level: 77\n  status: 2\n"}),
            phone_app_names=lambda: ["WhatsApp", "Uhr"],
            find_phone_app=lambda n: ("uhr", {}) if n.lower() == "uhr" else None,
            open_phone_app=lambda n, source="": {"success": True, "app": n},
            screen_state=lambda s: {"ok": True, "unlocked": True},
        )
        self._pa_old = pa._pa
        pa._pa = lambda: fake

    def tearDown(self):
        pa._pa = self._pa_old
        super().tearDown()

    def test_status_readonly(self):
        s, r = self.get("/api/device")
        self.assertEqual((s, r["connected"], r["battery"]["level"]), (200, True, "77"))
        self.assertEqual(self.calls, [["shell", "dumpsys", "battery"]])

    def test_actions_allowlist(self):
        for a in ("reboot", "install", "uninstall", "root", "shell", "delete", "whatsapp_send", None):
            self.assertEqual(self.post("/api/device", {"action": a})[0], 400, a)
        self.assertEqual(self.post("/api/device", {"action": "open_app", "app": "Uhr"})[0], 200)
        self.assertEqual(self.post("/api/device", {"action": "open_app", "app": "evil"})[0], 400)
        self.assertEqual(self.post("/api/device", {"action": "screen_state"})[0], 200)
        self.assertEqual(self.post("/api/device", {"action": "list_apps"})[1]["apps"], ["WhatsApp", "Uhr"])


class Dispatch(Base):
    def test_unknown_returns_none(self):
        self.assertIsNone(pa.handle_get("/api/memory", {}))
        self.assertIsNone(pa.handle_post("/api/files/write", {}))


if __name__ == "__main__":
    unittest.main()
