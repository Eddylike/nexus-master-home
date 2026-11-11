"""MOBILE 20261109: phone link via the PAIRED NEXUS OS app (authenticated /api/nexus-os/sync).

Synthetic credentials only, isolated link state on G:. A plain (unauthenticated) heartbeat
must never count as a phone link; only a valid pairing Bearer does, and only while fresh.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from nexus import nexus_os, nexus_os_link as L

GOOD = "Bearer synthetic-mobile-test-token-000000000000000000"


def _auth(headers):
    v = str(headers.get("Authorization") or "")
    if v == GOOD:
        return "owner-hash", None
    return None, "PC_AUTH_REQUIRED"


class PairedAppLinkTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "workspace" / "nexus_os_link"
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="mobile-test-", dir=str(root))
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(L, "DIR", Path(self.tmp.name))
        p.start(); self.addCleanup(p.stop)
        a = mock.patch("nexus.pc_remote.authenticate", side_effect=_auth)
        a.start(); self.addCleanup(a.stop)
        L._PAIRED_SEEN.clear()
        self.addCleanup(L._PAIRED_SEEN.clear)

    def _get_sync(self, headers):
        return L.handle("GET", "/api/nexus-os/sync", "192.0.2.56", headers, qs={"since": ["0"]})

    def test_anonymous_poll_is_not_a_link(self):
        self.assertEqual(self._get_sync({})[0], 200)
        self.assertIsNone(L.paired_contact_get())
        self.assertIsNone(nexus_os._paired_app_link())

    def test_wrong_bearer_rejected_and_not_noted(self):
        self.assertEqual(self._get_sync({"Authorization": "Bearer wrong"})[0], 401)
        self.assertIsNone(L.paired_contact_get())

    def test_valid_bearer_noted_without_secret(self):
        self.assertEqual(self._get_sync({"Authorization": GOOD})[0], 200)
        rec = L.paired_contact_get()
        self.assertEqual(rec["route_ip_kind"], "lan")
        self.assertLess(rec["age_s"], 5)
        raw = (Path(self.tmp.name) / "paired_contact.json").read_text(encoding="utf-8")
        self.assertNotIn("synthetic-mobile-test-token", raw)
        self.assertNotIn("owner-hash", raw)
        self.assertNotIn("Bearer", raw)
        link = nexus_os._paired_app_link()
        self.assertEqual(link["route"], "lan")

    def test_contact_survives_restart_via_file(self):
        self._get_sync({"Authorization": GOOD})
        L._PAIRED_SEEN.clear()  # simulated core restart: memory gone, file on G: remains
        self.assertIsNotNone(L.paired_contact_get())

    def test_heartbeat_alone_is_not_a_link(self):
        code, _ = L.handle("POST", "/api/nexus-os/device", "192.0.2.56", {}, body={"battery": 50})
        self.assertEqual(code, 200)
        self.assertIsNone(nexus_os._paired_app_link())

    def test_freshness_window(self):
        self.assertIsNotNone(nexus_os._paired_app_link(lambda: {"age_s": 10, "route_ip_kind": "lan"}))
        self.assertIsNone(nexus_os._paired_app_link(lambda: {"age_s": nexus_os.PHONE_APP_LINK_MAX_AGE_S + 1}))
        self.assertIsNone(nexus_os._paired_app_link(lambda: {"age_s": -5}))
        self.assertIsNone(nexus_os._paired_app_link(lambda: {"age_s": "x"}))
        self.assertIsNone(nexus_os._paired_app_link(lambda: None))
        self.assertIsNone(nexus_os._paired_app_link(lambda: (_ for _ in ()).throw(RuntimeError("boom"))))


class PhoneStatusRuleTests(unittest.TestCase):
    """Real _probe_cycle; only the device sources are replaced."""

    @staticmethod
    def _cycle(app_link):
        no_dev = lambda: {"adb": True, "devices": [], "ready": [], "state": nexus_os.UNAVAILABLE_TEMPORARILY, "error": "NO_DEVICE"}
        patches = {"adb_devices": no_dev, "_tailscale_peer": lambda ts, cfg: None,
                   "_paired_app_link": lambda getter=None: app_link}
        saved = {k: getattr(nexus_os, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(nexus_os, k, v)
            return nexus_os._probe_cycle()
        finally:
            for k, v in saved.items():
                setattr(nexus_os, k, v)

    def test_paired_app_link_makes_phone_online_without_adb(self):
        d = self._cycle({"age_s": 12.0, "route": "lan", "path": "GET /api/nexus-os/sync"})
        ph = d["components"]["NEXUSPHONE"]
        self.assertEqual(ph["status"], nexus_os.ONLINE)
        self.assertEqual(ph["method"], "app_link")
        self.assertEqual(ph["transport"], "lan")
        self.assertIn("Bearer", ph["source"])
        if d["components"]["WOZIPC_LOCAL_AI"]["status"] == nexus_os.ONLINE and d["CORE_OPERATIONAL"]:
            self.assertEqual(d["OPERATING_MODE"], "FULL")

    def test_no_app_link_no_adb_stays_fallback(self):
        d = self._cycle(None)
        ph = d["components"]["NEXUSPHONE"]
        self.assertNotEqual(ph["status"], nexus_os.ONLINE)
        if d["components"]["WOZIPC_LOCAL_AI"]["status"] == nexus_os.ONLINE and d["CORE_OPERATIONAL"]:
            self.assertEqual(d["OPERATING_MODE"], "PC_FALLBACK")
        self.assertNotIn("synthetic", json.dumps(d))


if __name__ == "__main__":
    unittest.main()
