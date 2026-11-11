"""005D: Identitaet + Reconnect-Watchdog in nexus/phone_actions.py (adb komplett gemockt)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from nexus import phone_actions as pa  # noqa: E402

MDNS = ("List of discovered mdns services\n"
        "adb-249ac5d7-ce0m6u\t_adb-tls-connect._tcp\t192.0.2.56:38041\n"
        "adb-deadbeef-xx\t_adb-tls-connect._tcp\t192.0.2.99:40000\n"
        "adb-249ac5d7-ce0m6u\t_adb-tls-pairing._tcp\t192.0.2.56:41111\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.p = [patch.object(pa, "IDENTITY_PATH", d / "id.json"), patch.object(pa, "RECONNECT_LOG", d / "r.log"),
                  patch.object(pa, "CONFIG_PATH", d / "cfg.json")]
        for x in self.p:
            x.start()
        pa._id_cache.clear()
        pa._WD.update({"last_seen": None, "reconnect_attempts": 0, "last_error": None, "last_attempt": None,
                       "last_endpoint": None, "blocked": None})

    def tearDown(self):
        for x in self.p:
            x.stop()
        self.tmp.cleanup()


def props(serialno="249ac5d7", device="sapphiren", model="23124RA7EO"):
    m = {"ro.serialno": serialno, "ro.product.device": device, "ro.product.model": model}
    return lambda serial, args, timeout=15: {"ok": True, "out": m.get(args[-1], "") + "\n"}


class Identity(Base):
    def test_fallback_then_persist_then_serialno(self):
        ident = {"serialno": "249ac5d7", "device": "sapphiren", "model": "23124RA7EO"}
        self.assertTrue(pa.identity_matches(dict(ident), known={}))
        pa.remember_identity(ident, "adb-249ac5d7-x._adb-tls-connect._tcp")
        known = json.loads(pa.IDENTITY_PATH.read_text(encoding="utf-8"))
        self.assertEqual(known["serialno"], "249ac5d7")
        i2 = dict(ident)
        self.assertTrue(pa.identity_matches(i2))
        self.assertEqual(i2["_matched_by"], "serialno")

    def test_other_sapphiren_rejected_once_known(self):
        pa.remember_identity({"serialno": "249ac5d7", "device": "sapphiren", "model": "M"}, "s1")
        self.assertFalse(pa.identity_matches({"serialno": "ffff0000", "device": "sapphiren", "model": "M"}))

    def test_device_model_when_no_serialno(self):
        known = {"device": "sapphiren", "model": "M"}
        self.assertTrue(pa.identity_matches({"device": "sapphiren", "model": "M"}, known))
        self.assertFalse(pa.identity_matches({"device": "sapphiren", "model": "X"}, known))
        self.assertFalse(pa.identity_matches({}, {}))
        self.assertFalse(pa.identity_matches({"device": "other"}, {}))

    def test_select_device_wifi(self):
        with patch.object(pa, "adb_devices", return_value={"adb-249ac5d7-q._adb-tls-connect._tcp": "device"}), \
             patch.object(pa, "_adb", side_effect=props()):
            r = pa.select_device(connect=False)
        self.assertEqual((r["status"], r["transport"]), ("OK", "wifi"))
        self.assertTrue(pa.IDENTITY_PATH.is_file())
        self.assertIsNotNone(pa._WD["last_seen"])

    def test_select_device_foreign_phone_not_taken(self):
        pa.remember_identity({"serialno": "249ac5d7", "device": "sapphiren", "model": "M"}, "s1")
        with patch.object(pa, "adb_devices", return_value={"adb-ffff-q._adb-tls-connect._tcp": "device"}), \
             patch.object(pa, "_adb", side_effect=props(serialno="ffff0000")):
            self.assertEqual(pa.select_device(connect=False)["status"], "UNAVAILABLE")


class Reconnect(Base):
    def test_parse_and_candidates(self):
        md = pa.parse_mdns(MDNS)
        self.assertEqual(len(md), 2)  # pairing-Dienst ignoriert
        c = pa.reconnect_candidates(md, {"serialno": "249ac5d7"})
        self.assertEqual(c, ["192.0.2.56:38041", "192.0.2.10:38041"])
        self.assertEqual(pa.reconnect_candidates([], {"serialno": "249ac5d7", "last_port": 38041}), ["192.0.2.10:38041"])
        self.assertEqual(pa.reconnect_candidates(md, {}), [])  # unbekannt + mehrere -> nichts raten

    def _run(self, connect_out):
        calls = []
        def run(cmd, timeout):
            calls.append(cmd[1:])
            if cmd[1:] == ["mdns", "services"]:
                return {"ok": True, "out": MDNS, "err": "", "rc": 0}
            return {"ok": True, "out": connect_out(cmd[-1]), "err": "", "rc": 0}
        return calls, run

    def test_success_lan_first_and_no_forbidden_cmds(self):
        pa.remember_identity({"serialno": "249ac5d7", "device": "sapphiren", "model": "M"}, "s1")
        calls, run = self._run(lambda ep: f"connected to {ep}")
        with patch.object(pa, "_run", side_effect=run):
            r = pa.reconnect_once()
        self.assertEqual(r, {"success": True, "endpoint": "192.0.2.56:38041"})
        for c in calls:
            self.assertIn(c[0], ("mdns", "connect"))
        self.assertEqual(json.loads(pa.IDENTITY_PATH.read_text(encoding="utf-8"))["last_port"], 38041)
        self.assertIn("connected to", pa.RECONNECT_LOG.read_text(encoding="utf-8"))

    def test_lan_fails_tailscale_used(self):
        pa.remember_identity({"serialno": "249ac5d7", "device": "sapphiren", "model": "M"}, "s1")
        calls, run = self._run(lambda ep: f"connected to {ep}" if ep.startswith("100.") else f"failed to connect to {ep}")
        with patch.object(pa, "_run", side_effect=run):
            self.assertEqual(pa.reconnect_once()["endpoint"], "192.0.2.10:38041")

    def test_blocked_no_mdns(self):
        pa.remember_identity({"serialno": "249ac5d7", "device": "sapphiren", "model": "M"}, "s1")
        with patch.object(pa, "_run", return_value={"ok": True, "out": "List of discovered mdns services\n", "err": "", "rc": 0}):
            r = pa.reconnect_once()
        self.assertFalse(r["success"])
        st = pa.watchdog_state()
        self.assertEqual((st["last_error"], st["reconnect_attempts"]), ("NO_MDNS_SERVICE_FOR_DEVICE", 1))
        self.assertTrue(st["blocked"])

    def test_needs_pairing(self):
        pa.remember_identity({"serialno": "249ac5d7", "device": "sapphiren", "model": "M"}, "s1")
        calls, run = self._run(lambda ep: f"failed to authenticate to {ep}")
        with patch.object(pa, "_run", side_effect=run):
            pa.reconnect_once()
        self.assertEqual(pa._WD["last_error"], "NEEDS_PAIRING")

    def test_tick_skips_when_ok(self):
        with patch.object(pa, "select_device", return_value={"status": "OK"}), \
             patch.object(pa, "reconnect_once") as rc:
            self.assertEqual(pa.watchdog_tick()["action"], "none")
            rc.assert_not_called()

    def test_log_rotation(self):
        pa.RECONNECT_LOG.write_text("x" * (300 * 1024), encoding="utf-8")
        pa._rlog("neu")
        self.assertTrue(pa.RECONNECT_LOG.with_suffix(".log.1").is_file())
        self.assertLess(pa.RECONNECT_LOG.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
