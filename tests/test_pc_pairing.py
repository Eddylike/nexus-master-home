"""Scoped PC pairing tests use synthetic credentials and an isolated G: store."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from nexus import nexus_os_link, pc_pairing, pc_remote


class PCPairingTests(unittest.TestCase):
    CODE = "123456789012"
    TOKEN = "synthetic-pc-token-for-isolated-tests-only-000000000000"

    def setUp(self):
        root = Path(__file__).resolve().parents[1] / "workspace" / "pc_remote"
        root.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="pairing-test-", dir=str(root))
        self.addCleanup(self.directory.cleanup)
        self.directory_path = Path(self.directory.name)
        self.paths = {"TOKEN_PATH": self.directory_path / "pc_remote_token.txt",
                      "STATE_PATH": self.directory_path / "pc_remote_pairing.json",
                      "LOCK_PATH": self.directory_path / "pc_remote_pairing.lock"}
        for name, value in self.paths.items():
            patcher = mock.patch.object(pc_pairing, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("NEXUS_PC_REMOTE_TOKEN", None)
        for target, value in (("secrets.randbelow", int(self.CODE)),
                              ("secrets.token_hex", "a" * 64),
                              ("secrets.token_urlsafe", self.TOKEN), ("time.time", 1000.0)):
            patcher = mock.patch("nexus.pc_pairing." + target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self, replace=False):
        return pc_pairing.start_pairing(replace=replace)

    def exchange(self, code=None, ip="100.101.102.103", headers=None):
        return pc_pairing.exchange({"code": self.CODE if code is None else code}, ip, headers or {})

    def restart(self):
        # A fresh module and fresh lock have no in-memory state from the start.
        spec = importlib.util.spec_from_file_location("isolated_pc_pairing", pc_pairing.__file__)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, value in self.paths.items():
            setattr(module, name, value)
        return module

    def test_success_once_and_restart_cannot_reissue(self):
        self.assertEqual(self.start()["code"], self.CODE)
        restarted = self.restart()
        status, response = restarted.exchange({"code": self.CODE}, "100.101.102.103", {})
        self.assertEqual(status, 200)
        self.assertEqual(response, {"success": True, "token": self.TOKEN, "token_type": "Bearer"})
        self.assertEqual(self.paths["TOKEN_PATH"].read_text().strip(), self.TOKEN)
        self.assertEqual(self.restart().exchange({"code": self.CODE}, "::1", {})[0], 409)
        state = self.paths["STATE_PATH"].read_text()
        self.assertNotIn(self.CODE, state)
        self.assertNotIn(self.TOKEN, state)
        self.assertEqual(json.loads(state)["status"], "consumed")

    def test_attempt_limit_persists_across_restart(self):
        self.start()
        for _ in range(4):
            self.assertEqual(self.exchange("999999999999")[0], 401)
        restarted = self.restart()
        status, response = restarted.exchange({"code": "999999999999"}, "127.0.0.1", {})
        self.assertEqual(status, 429)
        self.assertEqual(response["error"], "PC_PAIRING_ATTEMPTS_EXHAUSTED")
        self.assertEqual(self.exchange()[0], 429)
        self.assertFalse(self.paths["TOKEN_PATH"].exists())

    def test_non_string_or_malformed_code_counts_as_failed_attempt(self):
        self.start()
        for value in (123456789012, "", "12345678901", "１２３４５６７８９０１２"):
            self.assertEqual(self.exchange(value)[0], 401)
        self.assertEqual(json.loads(self.paths["STATE_PATH"].read_text())["attempts"], 4)

    def test_expiration_is_persistent_and_boundary_is_exclusive(self):
        self.start()
        with mock.patch("nexus.pc_pairing.time.time", return_value=1120):
            self.assertEqual(self.restart().exchange({"code": self.CODE}, "::1", {})[0], 410)
        self.assertEqual(self.exchange()[0], 410)
        self.assertFalse(self.paths["TOKEN_PATH"].exists())

    def test_network_and_proxy_rejections_do_not_consume_attempts(self):
        self.start()
        for ip in ("192.168.178.20", "10.0.0.5", "8.8.8.8", "100.63.255.255", "fd00::1", "invalid"):
            self.assertEqual(self.exchange(ip=ip)[0], 403)
        for header in ("X-Forwarded-For", "x-forwarded-host", "Forwarded", "Tailscale-User-Login",
                       "X-Forwarded-Proto", "Via", "X-Real-IP", "X-Tailscale-Funnel-Request"):
            self.assertEqual(self.exchange(headers={header: ""})[0], 403)
        self.assertEqual(json.loads(self.paths["STATE_PATH"].read_text())["attempts"], 0)
        self.assertEqual(self.exchange(ip="fd7a:115c:a1e0::1234")[0], 200)

    def test_ipv4_mapped_loopback_is_allowed(self):
        self.start()
        self.assertEqual(self.exchange(ip="::ffff:127.0.0.1")[0], 200)

    def test_unstarted_pairing_and_invalid_envelope(self):
        self.assertEqual(self.exchange()[0], 409)
        for body in (None, [], {}, {"code": self.CODE, "replace": True}):
            self.assertEqual(pc_pairing.exchange(body, "127.0.0.1", {})[0], 400)
        self.assertFalse(self.paths["TOKEN_PATH"].exists())

    def test_existing_link_routes_pair_exchange_and_refuses_network_start(self):
        path = "/api/nexus-os/pair"
        self.assertEqual(nexus_os_link.handle("GET", path, "127.0.0.1", {})[0], 405)
        self.assertEqual(nexus_os_link.handle("PUT", path, "127.0.0.1", {}, body={})[0], 405)
        self.assertEqual(nexus_os_link.handle("POST", path, "127.0.0.1", {},
                                             body={"start": True})[0], 400)
        self.assertFalse(self.paths["STATE_PATH"].exists())
        self.start()
        self.assertEqual(nexus_os_link.handle("POST", path, "192.168.178.20", {},
                                             body={"code": self.CODE})[0], 403)
        self.assertEqual(nexus_os_link.handle("POST", path, "100.101.102.103", {"x-forwarded-host": "proxy"},
                                             body={"code": self.CODE})[0], 403)
        status, response = nexus_os_link.handle("POST", path, "100.101.102.103", {}, body={"code": self.CODE})
        self.assertEqual(status, 200)
        self.assertEqual(response["token_type"], "Bearer")
        self.assertEqual(response["token"], self.TOKEN)

    def test_pairing_token_drives_documented_confirmed_005d_flow(self):
        """Production pairing/auth/receipts/events; only the PC effect is fake."""
        executor = mock.Mock(return_value={"success": True, "reply": "Synthetic effect observed.",
                                           "evidence": {"verified": True, "synthetic": True}})
        service = pc_remote.PCRemoteService(self.directory_path / "commands", executor=executor,
                                           publisher=nexus_os_link.publish_pc_event, start_worker=False)
        ip, path = "100.101.102.103", "/api/nexus-os/sync"
        with mock.patch.object(nexus_os_link, "DIR", self.directory_path / "link"), \
                mock.patch.object(pc_remote, "TOKEN_PATH", self.paths["TOKEN_PATH"]), \
                mock.patch.object(pc_remote, "_SERVICE", service):
            self.start()
            status, paired = nexus_os_link.handle("POST", "/api/nexus-os/pair", ip, {}, body={"code": self.CODE})
            self.assertEqual(status, 200)
            headers = {"Authorization": "Bearer " + paired["token"]}
            owner, error = pc_remote.authenticate(headers)
            self.assertIsNone(error)
            self.assertTrue(owner)
            command = {"id": "pairing-flow-action", "kind": "pc_command", "ts": 1000,
                       "intent": "app.open", "args": {"app": "youtube"},
                       "text": "Oeffne YouTube auf dem PC", "source": "voice"}
            self.assertEqual(nexus_os_link.handle("POST", path, ip, {}, body={"items": [command]})[0], 401)
            status, receipt = nexus_os_link.handle("POST", path, ip, headers, body={"items": [command]})
            self.assertEqual(status, 200)
            self.assertEqual(receipt["acked"], [command["id"]])
            self.assertEqual(receipt["pc_commands"][0]["status"], "queued")
            executor.assert_not_called()
            service.process_pending()
            status, poll = nexus_os_link.handle("GET", path, ip, headers,
                                                qs={"since": ["0"], "pc_since": ["0"]})
            self.assertEqual(status, 200)
            question = poll["items"][0]
            self.assertEqual(question["ref_id"], command["id"])
            self.assertEqual(question["status"], "needs_confirm")
            self.assertFalse(question["success"])
            self.assertEqual(question["expires_in"], 60)
            executor.assert_not_called()
            answer = {"id": "pairing-flow-answer", "kind": "pc_command", "ts": 1000,
                      "intent": "command.confirm", "args": {"confirm_id": question["confirm_id"], "answer": "Ja"},
                      "text": "Ja", "source": "voice"}
            status, receipt = nexus_os_link.handle("POST", path, ip, headers, body={"items": [answer]})
            self.assertEqual(status, 200)
            self.assertEqual(receipt["pc_commands"][0]["status"], "queued")
            service.process_pending()
            executor.assert_called_once_with("app.open", {"app": "youtube"})
            status, done = nexus_os_link.handle("GET", path, ip, headers,
                                                qs={"since": [str(poll["cursor"])],
                                                    "pc_since": [str(poll["pc_cursor"])]})
            self.assertEqual(status, 200)
            self.assertEqual(len(done["items"]), 1)
            event = done["items"][0]
            self.assertEqual(event["ref_id"], command["id"])
            self.assertEqual(event["status"], "done")
            self.assertIs(event["success"], True)
            self.assertIs(event["evidence"]["verified"], True)
            self.assertEqual(event["text"], event["reply"])
            self.assertNotIn("_pc_owner", event)
            self.assertGreater(done["pc_cursor"], poll["pc_cursor"])
            nexus_os_link.handle("POST", "/api/nexus-os/push", "127.0.0.1", {},
                                 body={"kind": "chat", "text": "Legacy public chat"})
            status, legacy = nexus_os_link.handle("GET", path, ip, {}, qs={"since": ["0"]})
            self.assertEqual(status, 200)
            self.assertNotIn("pc_cursor", legacy)
            self.assertEqual([item["text"] for item in legacy["items"]], ["Legacy public chat"])
            self.assertEqual(nexus_os_link.handle("GET", path, ip, {"Authorization": "Bearer wrong"})[0], 401)
            # Reload durable state, then repeat both identical envelopes. The
            # production service must preserve IDs and never repeat the effect.
            pc_remote._SERVICE = pc_remote.PCRemoteService(
                self.directory_path / "commands", executor=executor,
                publisher=nexus_os_link.publish_pc_event, start_worker=False)
            for item in (command, answer):
                status, receipt = nexus_os_link.handle("POST", path, ip, headers, body={"items": [item]})
                self.assertEqual(status, 200)
                self.assertIs(receipt["pc_commands"][0]["duplicate"], True)
            pc_remote._SERVICE.process_pending()
            executor.assert_called_once()

    def test_existing_coupling_requires_explicit_replace(self):
        original = "synthetic-old-pc-token-for-tests-0000000000000"
        self.paths["TOKEN_PATH"].write_text(original)
        with self.assertRaisesRegex(pc_pairing.PairingError, "PC_PAIRING_REPLACE_REQUIRED"):
            self.start()
        self.start(replace=True)
        self.assertEqual(self.paths["TOKEN_PATH"].read_text(), original)
        self.assertEqual(self.exchange()[0], 200)
        self.assertEqual(self.paths["TOKEN_PATH"].read_text().strip(), self.TOKEN)

    def test_environment_token_cannot_be_silently_replaced(self):
        for configured in ("invalid-short", "synthetic-env-token-for-tests-00000000000000"):
            with mock.patch.dict(os.environ, {"NEXUS_PC_REMOTE_TOKEN": configured}):
                for replace in (False, True):
                    with self.assertRaisesRegex(pc_pairing.PairingError, "PC_PAIRING_ENV_TOKEN_CONFIGURED"):
                        self.start(replace=replace)
        self.start()
        with mock.patch.dict(os.environ, {"NEXUS_PC_REMOTE_TOKEN": "changed-after-start"}):
            self.assertEqual(self.exchange()[1]["error"], "PC_PAIRING_ENV_TOKEN_CONFIGURED")
        self.assertFalse(self.paths["TOKEN_PATH"].exists())

    def test_external_token_change_invalidates_code(self):
        self.start()
        original = "synthetic-external-token-for-tests-000000000000"
        self.paths["TOKEN_PATH"].write_text(original)
        self.assertEqual(self.exchange()[1]["error"], "PC_PAIRING_CONFIGURATION_CHANGED")
        self.assertEqual(self.paths["TOKEN_PATH"].read_text(), original)
        self.paths["TOKEN_PATH"].unlink()
        self.assertEqual(self.exchange()[0], 409)

    def test_concurrent_exchange_issues_only_one_token(self):
        self.start()
        gate = threading.Barrier(8)

        def request(_):
            gate.wait()
            return self.exchange()[0]

        with ThreadPoolExecutor(max_workers=8) as executor:
            statuses = list(executor.map(request, range(8)))
        self.assertEqual(statuses.count(200), 1)
        self.assertEqual(statuses.count(409), 7)

    def test_code_consumed_before_failed_token_write(self):
        self.start()
        original_write = pc_pairing._atomic_write

        def fail_token(path, content):
            if path == self.paths["TOKEN_PATH"]:
                raise OSError("synthetic disk failure")
            return original_write(path, content)

        with mock.patch.object(pc_pairing, "_atomic_write", side_effect=fail_token):
            self.assertEqual(self.exchange()[1]["error"], "PC_PAIRING_STORE_ERROR")
        self.assertEqual(self.restart().exchange({"code": self.CODE}, "127.0.0.1", {})[0], 409)
        self.assertFalse(self.paths["TOKEN_PATH"].exists())

    def test_corrupt_state_fails_closed(self):
        self.paths["STATE_PATH"].write_text("{broken")
        self.assertEqual(self.exchange()[1]["error"], "PC_PAIRING_STORE_CORRUPT")
        with self.assertRaisesRegex(pc_pairing.PairingError, "PC_PAIRING_STORE_CORRUPT"):
            self.start(replace=True)

    def test_active_code_requires_explicit_replacement(self):
        self.start()
        with self.assertRaisesRegex(pc_pairing.PairingError, "PC_PAIRING_ALREADY_ACTIVE"):
            self.start()
        self.assertTrue(self.start(replace=True)["success"])

    def load_cli(self):
        path = Path(__file__).resolve().parents[1] / "workspace" / "scripts" / "pc_remote_pairing.py"
        spec = importlib.util.spec_from_file_location("isolated_pc_pairing_cli", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_cli_refuses_noninteractive_start_without_creating_state(self):
        cli = self.load_cli()
        with mock.patch.object(cli.sys, "stdin", io.StringIO()), mock.patch.object(cli.sys, "stderr", io.StringIO()):
            self.assertEqual(cli.main([]), 2)
        self.assertFalse(self.paths["STATE_PATH"].exists())

    def test_cli_replacement_needs_local_literal_confirmation(self):
        cli = self.load_cli()
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(cli.sys.stdout, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value="ja"), \
                mock.patch("builtins.print"), mock.patch.object(cli, "start_pairing") as start:
            self.assertEqual(cli.main(["--replace"]), 1)
            start.assert_not_called()
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=True), \
                mock.patch.object(cli.sys.stdout, "isatty", return_value=True), \
                mock.patch("builtins.input", return_value="ERSETZEN"), \
                mock.patch("builtins.print"), mock.patch.object(cli, "start_pairing", return_value={"code": self.CODE}) as start:
            self.assertEqual(cli.main(["--replace"]), 0)
            start.assert_called_once_with(replace=True)


if __name__ == "__main__":
    unittest.main()
