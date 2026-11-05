"""Durable phone command security contracts; all executors and publishers are local fakes.

Run with ``python -B -m unittest discover -s tests -p test_pc_remote.py -v``.
Temporary state is explicitly confined to this repository's report directory on G:.
No real browser, microphone, device action, credential, or network is used.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from nexus import nexus_os_link as link
from nexus import pc_remote as remote


REPORT_ROOT = Path(__file__).resolve().parents[1] / "workspace" / "reports" / "pc_remote_20261022"
TEST_TOKEN = "offline-unit-test-credential-never-a-real-token"
OWNER = hashlib.sha256(TEST_TOKEN.encode("utf-8")).hexdigest()
OTHER_OWNER = hashlib.sha256(b"another-offline-test-credential").hexdigest()


class IsolatedState(unittest.TestCase):
    def setUp(self):
        if os.name == "nt" and REPORT_ROOT.resolve().drive.upper() != "G:":
            self.fail("PC remote test state must stay on G:")
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="pc-remote-test-", dir=str(REPORT_ROOT))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.resolve().relative_to(REPORT_ROOT.resolve())
        self.events = []
        self.executor = mock.Mock(return_value={
            "success": True, "reply": "Observed test effect.", "evidence": {"verified": True, "test": True},
        })
        self.service = self.make_service()
        self.counter = 0

    def make_service(self, *, publisher=None):
        return remote.PCRemoteService(
            self.root / "service", executor=self.executor, start_worker=False,
            publisher=publisher or (lambda event, owner: self.events.append((copy.deepcopy(event), owner))),
        )

    def item(self, intent="app.open", args=None, **extra):
        self.counter += 1
        value = {"id": "unit-" + str(self.counter), "kind": "pc_command", "ts": time.time(),
                 "source": "voice", "text": "", "intent": intent,
                 "args": {"app": "youtube"} if args is None and intent == "app.open" else (args or {})}
        value.update(extra)
        return value

    def send(self, item, owner=OWNER):
        accepted, receipt = self.service.accept(item, owner)
        self.assertTrue(accepted, receipt)
        self.service.process_pending()
        return self.service.records[item["id"]]

    def ask(self, **extra):
        record = self.send(self.item(**extra))
        self.assertEqual(record["status"], "needs_confirm")
        self.executor.assert_not_called()
        return record

    def answer(self, record, answer="Ja", *, owner=OWNER, intent="command.confirm", **extra):
        return self.send(self.item(intent, {"confirm_id": record["confirm_id"], "answer": answer},
                                   text=answer, utterance=answer, **extra), owner)

    def disk_record(self, iid):
        path = self.root / "service" / "commands" / (iid + ".json")
        return json.loads(path.read_text(encoding="utf-8"))


class Authentication(IsolatedState):
    def test_missing_or_short_configuration_fails_closed(self):
        with mock.patch.object(remote, "TOKEN_PATH", self.root / "absent-token"):
            for token in ("", "short"):
                with self.subTest(configured=bool(token)), mock.patch.dict(
                        os.environ, {"NEXUS_PC_REMOTE_TOKEN": token}):
                    principal, error = remote.authenticate({"Authorization": "Bearer " + TEST_TOKEN})
                    self.assertIsNone(principal)
                    self.assertEqual(error, "PC_AUTH_UNCONFIGURED")

    def test_bearer_is_required_and_identity_depends_on_credential(self):
        with mock.patch.object(remote, "_token", return_value=TEST_TOKEN):
            for headers in ({}, {"Authorization": TEST_TOKEN}, {"Authorization": "Bearer wrong"}):
                self.assertEqual(remote.authenticate(headers), (None, "PC_AUTH_REQUIRED"))
            self.assertEqual(remote.authenticate({"Authorization": "Bearer " + TEST_TOKEN}), (OWNER, None))
            self.assertEqual(remote.authenticate({"authorization": "bearer " + TEST_TOKEN}), (OWNER, None))
        with mock.patch.object(remote, "_token", return_value="another-offline-test-credential"):
            principal, error = remote.authenticate({"Authorization": "Bearer another-offline-test-credential"})
            self.assertIsNone(error)
            self.assertEqual(principal, OTHER_OWNER)
            self.assertNotEqual(principal, OWNER)

    def test_scoped_token_file_is_read_without_global_credentials(self):
        path = self.root / "test-token.txt"
        path.write_text(TEST_TOKEN, encoding="utf-8")
        with mock.patch.object(remote, "TOKEN_PATH", path), mock.patch.dict(
                os.environ, {"NEXUS_PC_REMOTE_TOKEN": ""}):
            self.assertEqual(remote.authenticate({"Authorization": "Bearer " + TEST_TOKEN}), (OWNER, None))


class Confirmations(IsolatedState):
    def test_ack_is_durable_queued_receipt_and_never_action_success(self):
        item = self.item()
        accepted, receipt = self.service.accept(item, OWNER)
        self.assertTrue(accepted)
        self.assertEqual(receipt["status"], "queued")
        self.assertNotIn("success", receipt)
        self.assertEqual(self.disk_record(item["id"])["status"], "queued")
        self.executor.assert_not_called()
        self.service.process_pending()
        record = self.service.records[item["id"]]
        self.assertEqual(record["status"], "needs_confirm")
        self.assertFalse(record["success"])
        self.assertTrue(record["confirm_id"].startswith("pc-"))
        self.assertEqual(self.events[-1][0]["status"], "needs_confirm")
        self.executor.assert_not_called()

    def test_ja_consumes_confirmation_on_disk_before_executor_runs(self):
        record = self.ask()
        iid = record["id"]

        def observed(intent, args):
            saved = self.disk_record(iid)
            self.assertEqual(saved["status"], "executing")
            self.assertNotIn("confirm_id", saved)
            self.assertNotIn("expires", saved)
            self.assertIn("confirmed_by", saved)
            self.assertEqual((intent, args), ("app.open", {"app": "youtube"}))
            return {"success": True, "reply": "Test observed.", "evidence": {"verified": True}}

        self.executor.side_effect = observed
        receipt = self.answer(record)
        self.assertEqual(record["status"], "done")
        self.assertEqual(receipt["status"], "done")
        self.assertEqual(receipt["action_ref_id"], iid)
        self.executor.assert_called_once()

    def test_only_exact_ja_not_other_affirmations_or_attached_commands(self):
        record = self.ask()
        for phrase in ("genau", "bestätigt", "jawohl", "ja bitte", "ja mach", "ja und öffne Spotify",
                       "ja; powershell", "okay", "yes", "Ja Ja", "j a", ""):
            with self.subTest(phrase=phrase):
                answer = self.answer(record, phrase)
                self.assertEqual(answer["status"], "rejected")
                self.assertEqual(answer["error"], "PC_EXPLICIT_JA_REQUIRED")
                self.assertEqual(record["status"], "needs_confirm")
                self.executor.assert_not_called()
        self.answer(record, "JA!")
        self.executor.assert_called_once()

    def test_text_or_utterance_cannot_turn_a_spoken_nein_into_ja(self):
        record = self.ask()
        for fields in ({"text": "Nein", "utterance": "Ja"}, {"text": "Ja", "utterance": "Nein"}):
            with self.subTest(fields=fields):
                response = self.send(self.item("command.confirm", {
                    "confirm_id": record["confirm_id"], "answer": "Ja"}, **fields))
                self.assertEqual(response["status"], "rejected")
                self.assertEqual(response["error"], "PC_CONFIRM_SPEECH_MISMATCH")
                self.executor.assert_not_called()

    def test_mutation_and_confirmation_require_voice_source(self):
        for source in (None, "chat", "text", "phone-voice"):
            with self.subTest(source=source):
                command = self.item(source=source)
                if source is None:
                    command.pop("source")
                response = self.send(command)
                self.assertEqual(response["error"], "PC_VOICE_REQUIRED")
        record = self.ask()
        for source in (None, "chat", "text"):
            with self.subTest(confirm_source=source):
                result = self.answer(record, source=source)
                self.assertEqual(result["error"], "PC_CONFIRM_INVALID")
        self.executor.assert_not_called()

    def test_confirmation_is_bound_to_original_credential(self):
        record = self.ask()
        response = self.answer(record, owner=OTHER_OWNER)
        self.assertEqual(response["error"], "PC_CONFIRM_UNKNOWN")
        self.assertEqual(record["status"], "needs_confirm")
        self.executor.assert_not_called()
        self.answer(record)
        self.executor.assert_called_once()

    def test_cancel_invalidates_pending_action_and_later_ja(self):
        record = self.ask()
        old_id = record["confirm_id"]
        cancelled = self.answer(record, "Nein.")
        self.assertEqual(record["error"], "PC_CANCELLED")
        self.assertEqual(cancelled["error"], "PC_CANCELLED")
        replay = self.send(self.item("command.confirm", {"confirm_id": old_id, "answer": "Ja"}, text="Ja"))
        self.assertEqual(replay["error"], "PC_CONFIRM_UNKNOWN")
        self.executor.assert_not_called()

    def test_expired_prompt_rejects_both_action_and_confirmation(self):
        record = self.ask()
        record["expires"] = time.time() - 1
        self.service._save(record)
        response = self.answer(record)
        self.assertEqual(record["error"], "CONFIRM_EXPIRED")
        self.assertEqual(response["error"], "CONFIRM_EXPIRED")
        self.executor.assert_not_called()

    def test_confirmation_schema_and_confirmation_type_cannot_be_swapped(self):
        record = self.ask()
        response = self.answer(record, intent="command.clarify")
        self.assertEqual(response["error"], "PC_CONFIRM_UNKNOWN")
        malformed = self.send(self.item("command.confirm", {
            "confirm_id": record["confirm_id"], "answer": "Ja", "intent": "media.next"}, text="Ja"))
        self.assertEqual(malformed["error"], "PC_CONFIRM_INVALID")
        self.executor.assert_not_called()

    def test_program_clarification_requires_separate_new_ja_and_no_replay(self):
        command = self.item(text="Nexus, starte ein Programm auf dem Computer.")
        command.pop("intent")
        command.pop("args")
        original = self.send(command)
        self.assertEqual(original["status"], "needs_clarification")
        self.assertEqual(original["slot"], "app")
        clarification_id = original["confirm_id"]
        response = self.answer(original, "YouTube", intent="command.clarify")
        self.assertEqual(original["status"], "needs_confirm")
        self.assertNotEqual(original["confirm_id"], clarification_id)
        self.assertEqual(response["confirm_id"], original["confirm_id"])
        self.assertEqual(response["action_ref_id"], original["id"])
        self.executor.assert_not_called()
        confirmation_id = original["confirm_id"]
        self.answer(original)
        self.executor.assert_called_once_with("app.open", {"app": "youtube"})
        replay = self.send(self.item("command.confirm", {"confirm_id": confirmation_id, "answer": "Ja"}, text="Ja"))
        self.assertEqual(replay["status"], "rejected")
        self.assertEqual(replay["error"], "PC_CONFIRM_UNKNOWN")
        self.executor.assert_called_once()


class DurabilityAndTruthfulness(IsolatedState):
    def test_duplicate_same_request_is_acknowledged_without_second_execution(self):
        command = self.item()
        record = self.send(command)
        answer = self.item("command.confirm", {"confirm_id": record["confirm_id"], "answer": "Ja"}, text="Ja")
        self.send(answer)
        for item in (command, answer):
            accepted, receipt = self.service.accept(copy.deepcopy(item), OWNER)
            self.assertTrue(accepted)
            self.assertTrue(receipt["duplicate"])
        self.service.process_pending()
        self.executor.assert_called_once()
        second_answer = self.send(self.item("command.confirm", {
            "confirm_id": answer["args"]["confirm_id"], "answer": "Ja"}, text="Ja"))
        self.assertEqual(second_answer["error"], "PC_CONFIRM_UNKNOWN")
        self.executor.assert_called_once()

    def test_same_id_different_payload_or_owner_is_conflict(self):
        command = self.item()
        self.send(command)
        changed = copy.deepcopy(command)
        changed["args"] = {"app": "spotify"}
        for item, owner in ((changed, OWNER), (command, OTHER_OWNER)):
            self.assertEqual(self.service.accept(item, owner), (False, {"error": "PC_ID_CONFLICT"}))
        self.executor.assert_not_called()

    def test_stale_missing_boolean_or_future_timestamp_never_reaches_executor(self):
        for ts in (None, True, "now", time.time() - remote.COMMAND_TTL - 5, time.time() + 65):
            with self.subTest(ts=ts):
                response = self.send(self.item("pc.status", ts=ts))
                self.assertEqual(response["status"], "rejected")
                self.assertEqual(response["error"], "PC_COMMAND_EXPIRED")
                self.executor.assert_not_called()
        for ts in (float("nan"), float("inf")):
            accepted, receipt = self.service.accept(self.item(ts=ts), OWNER)
            self.assertFalse(accepted)
            self.assertEqual(receipt["error"], "PC_ENVELOPE_INVALID")

    def test_unapproved_intent_and_extra_args_are_never_confirmed_or_executed(self):
        for intent, args in (("shell", {"command": "calc"}), ("app.open", {"app": "powershell"}),
                             ("media.pause", {"script": "arbitrary"}), ("volume.set", {"percent": 101})):
            with self.subTest(intent=intent, args=args):
                record = self.send(self.item(intent, args))
                self.assertEqual(record["status"], "rejected")
                self.assertNotIn("confirm_id", record)
                self.executor.assert_not_called()

    def test_read_only_status_can_execute_without_spoken_confirmation(self):
        record = self.send(self.item("pc.status", source="text"))
        self.assertEqual(record["status"], "done")
        self.executor.assert_called_once_with("pc.status", {})
        self.assertNotIn("confirm_id", record)

    def test_success_requires_explicit_boolean_verified_evidence(self):
        for result in ({"success": True}, {"success": True, "evidence": {"verified": False}},
                       {"success": True, "evidence": {"verified": "true"}},
                       {"success": True, "evidence": ["verified"]},
                       {"success": 1, "evidence": {"verified": True}}):
            with self.subTest(result=result):
                self.executor.return_value = result
                record = self.send(self.item("pc.status"))
                self.assertEqual(record["status"], "failed")
                self.assertFalse(record["success"])
                self.assertEqual(record["error"], "PC_EXECUTION_UNVERIFIED")

    def test_executor_error_is_failed_without_sensitive_exception_message(self):
        self.executor.side_effect = RuntimeError("private-token-value")
        record = self.send(self.item("pc.status"))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "PC_EXECUTOR_RuntimeError")
        self.assertTrue(record["reply"])
        self.assertNotIn("private-token-value", json.dumps(self.events))

    def test_restart_does_not_retry_an_interrupted_executing_mutation(self):
        record = self.ask()
        record.update(status="executing", started_ts=time.time())
        record.pop("confirm_id")
        record.pop("expires")
        self.service._save(record)
        restarted = self.make_service()
        restarted.process_pending()
        recovered = restarted.records[record["id"]]
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["error"], "EXECUTION_UNCERTAIN")
        self.executor.assert_not_called()

    def test_restart_recovers_unexecuted_queue_and_preserves_completed_ids(self):
        command = self.item("pc.status")
        self.service.accept(command, OWNER)
        restarted = self.make_service()
        restarted.process_pending()
        self.executor.assert_called_once()
        again = self.make_service()
        accepted, receipt = again.accept(command, OWNER)
        self.assertTrue(accepted)
        self.assertTrue(receipt["duplicate"])
        self.assertEqual(receipt["status"], "done")
        again.process_pending()
        self.executor.assert_called_once()

    def test_restart_preserves_valid_confirmation_and_consumes_it_once(self):
        original = self.ask()
        confirm_id, expires = original["confirm_id"], original["expires"]
        self.service = self.make_service()
        recovered = self.service.records[original["id"]]
        self.assertEqual(recovered["status"], "needs_confirm")
        self.assertEqual((recovered["confirm_id"], recovered["expires"]), (confirm_id, expires))
        self.executor.assert_not_called()

        response = self.answer(recovered)
        self.assertEqual((recovered["status"], response["status"]), ("done", "done"))
        self.assertNotIn("confirm_id", self.disk_record(original["id"]))
        replay = self.send(self.item("command.confirm", {
            "confirm_id": confirm_id, "answer": "Ja"}, text="Ja"))
        self.assertEqual(replay["error"], "PC_CONFIRM_UNKNOWN")
        self.executor.assert_called_once_with("app.open", {"app": "youtube"})

    def test_restart_keeps_completed_mutation_and_confirmation_as_duplicates(self):
        command = self.item()
        original = self.send(command)
        confirmation = self.item("command.confirm", {
            "confirm_id": original["confirm_id"], "answer": "Ja"}, text="Ja")
        self.send(confirmation)
        restarted = self.make_service()

        for item in (command, confirmation):
            with self.subTest(request=item["id"]):
                accepted, receipt = restarted.accept(copy.deepcopy(item), OWNER)
                self.assertTrue(accepted)
                self.assertTrue(receipt["duplicate"])
                self.assertEqual(receipt["status"], "done")
        restarted.process_pending()
        self.assertEqual(restarted.records[confirmation["id"]]["action_ref_id"], command["id"])
        self.executor.assert_called_once_with("app.open", {"app": "youtube"})

    def test_restart_after_effect_with_processing_answer_never_repeats_effect(self):
        original = self.ask()
        confirmation = self.item("command.confirm", {
            "confirm_id": original["confirm_id"], "answer": "Ja"}, text="Ja")
        self.service.accept(confirmation, OWNER)

        class SimulatedCrash(BaseException):
            pass

        # A process can disappear after its external effect, before recording its result.
        self.executor.side_effect = SimulatedCrash()
        with self.assertRaises(SimulatedCrash):
            self.service.process_pending()
        self.assertEqual(self.disk_record(original["id"])["status"], "executing")
        self.assertEqual(self.disk_record(confirmation["id"])["status"], "processing")
        self.executor.side_effect = None

        restarted = self.make_service()
        restarted.process_pending()
        self.assertEqual(restarted.records[original["id"]]["error"], "EXECUTION_UNCERTAIN")
        self.assertEqual(restarted.records[confirmation["id"]]["error"], "PC_CONFIRM_UNKNOWN")
        for item in (original["request"], confirmation):
            accepted, receipt = restarted.accept(copy.deepcopy(item), OWNER)
            self.assertTrue(accepted)
            self.assertTrue(receipt["duplicate"])
        restarted.process_pending()
        self.executor.assert_called_once_with("app.open", {"app": "youtube"})

    def test_restart_expires_pending_confirmation_without_execution(self):
        record = self.ask()
        record["expires"] = time.time() - 1
        self.service._save(record)
        restarted = self.make_service()
        restarted.process_pending()
        self.assertEqual(restarted.records[record["id"]]["error"], "CONFIRM_EXPIRED")
        self.executor.assert_not_called()

    def test_corrupt_store_blocks_startup_instead_of_forgetting_ids(self):
        store = self.root / "service" / "commands"
        store.mkdir(parents=True)
        (store / "broken.json").write_text("{broken-json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.make_service()
        self.executor.assert_not_called()

    def test_publication_failure_retries_result_without_repeating_execution(self):
        self.service.publisher = mock.Mock(side_effect=OSError("offline publisher"))
        item = self.item("pc.status")
        record = self.send(item)
        self.assertEqual(record["status"], "done")
        self.assertLess(record.get("delivered_revision", 0), record["revision"])
        delivered = []
        self.service.publisher = lambda event, owner: delivered.append((event, owner))
        accepted, receipt = self.service.accept(item, OWNER)
        self.assertTrue(accepted)
        self.assertTrue(receipt["duplicate"])
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0][0]["ref_id"], item["id"])
        self.assertEqual(record["delivered_revision"], record["revision"])
        self.executor.assert_called_once()


class WorkerAndStorageSafety(IsolatedState):
    def test_parallel_fresh_answers_share_one_confirmation_and_one_machine_effect(self):
        original = self.ask()
        answers = [self.item("command.confirm", {
            "confirm_id": original["confirm_id"], "answer": "Ja"}, text="Ja") for _ in range(6)]
        barrier = threading.Barrier(len(answers) + 1)
        receipts, errors = [], []
        stop = object()

        class StopWorker(BaseException):
            pass

        class BoundedQueue(queue.Queue):
            def get(self, *args, **kwargs):
                item = super().get(*args, **kwargs)
                if item is stop:
                    self.task_done()
                    raise StopWorker()
                return item

        pending = BoundedQueue()
        self.service.pending = pending

        def send_answer(item):
            try:
                barrier.wait(3)
                receipts.append(self.service.accept(item, OWNER))
            except BaseException as exc:
                errors.append(type(exc).__name__)

        def worker_loop():
            try:
                self.service._run()
            except StopWorker:
                pass
            except BaseException as exc:
                errors.append(type(exc).__name__)

        worker = threading.Thread(target=worker_loop, daemon=True)
        self.service.worker = worker
        senders = [threading.Thread(target=send_answer, args=(item,), daemon=True) for item in answers]
        worker.start()
        try:
            for sender in senders:
                sender.start()
            barrier.wait(3)
            for sender in senders:
                sender.join(3)
            self.assertFalse(any(sender.is_alive() for sender in senders))
        finally:
            pending.put(stop)
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(receipts), len(answers))
        self.assertTrue(all(accepted and receipt["status"] == "queued" for accepted, receipt in receipts))
        records = [self.service.records[item["id"]] for item in answers]
        self.assertEqual(sum(record["status"] == "done" for record in records), 1)
        self.assertEqual(sum(record.get("error") == "PC_CONFIRM_UNKNOWN" for record in records), len(answers) - 1)
        self.assertEqual(original["status"], "done")
        self.executor.assert_called_once_with("app.open", {"app": "youtube"})

    def test_executing_record_write_failure_prevents_the_machine_effect(self):
        original = self.ask()
        original_id = original["id"]
        write = remote._write

        def disk_failure(path, value):
            if value["id"] == original_id and value["status"] == "executing":
                raise OSError("injected disk write failure")
            return write(path, value)

        with mock.patch.object(remote, "_write", side_effect=disk_failure):
            response = self.answer(original)
        self.executor.assert_not_called()
        self.assertEqual(response["status"], "failed")
        self.assertEqual(response["error"], "PC_PROCESSING_OSError")
        self.assertEqual(self.disk_record(original_id)["status"], "needs_confirm")
        self.assertFalse(any(event.get("success") for event, _owner in self.events))

    def test_worker_survives_store_failure_and_processes_next_request(self):
        bad, good = self.item("pc.status"), self.item("pc.status")
        self.service.accept(bad, OWNER)
        self.service.accept(good, OWNER)
        write = remote._write

        def disk_failure(path, value):
            if value["id"] == bad["id"] and value["status"] == "processing":
                raise OSError("injected disk write failure")
            return write(path, value)

        class StopWorker(BaseException):
            pass

        # Bound the production worker loop after exactly these two queue entries.
        pending = mock.Mock()
        pending.get.side_effect = [bad["id"], good["id"], StopWorker()]
        self.service.pending = pending
        with mock.patch.object(remote, "_write", side_effect=disk_failure), self.assertRaises(StopWorker):
            self.service._run()
        self.assertEqual(self.service.last_error, "PC_STORE_OSError")
        self.assertEqual(pending.task_done.call_count, 2)
        self.assertEqual(self.disk_record(bad["id"])["status"], "queued")
        self.assertEqual(self.disk_record(good["id"])["status"], "done")
        self.executor.assert_called_once_with("pc.status", {})

    def test_production_executor_branch_honors_maintenance_drain_before_effect(self):
        from nexus.maintenance import MaintenanceActive
        self.service.executor = None
        context = mock.MagicMock()
        context.__enter__.side_effect = MaintenanceActive("isolated test drain")
        gate = mock.Mock()
        gate.work.return_value = context
        with mock.patch("nexus.maintenance.maintenance", return_value=gate), mock.patch(
                "nexus.computer.remote_executor.execute") as machine_effect:
            record = self.send(self.item("pc.status"))
        machine_effect.assert_not_called()
        gate.work.assert_called_once_with("pc_remote")
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["error"], "PC_EXECUTOR_MaintenanceActive")
        self.assertFalse(record["success"])
        self.assertFalse(any(event.get("success") for event, _owner in self.events))

    def test_accept_and_duplicate_replay_remain_available_during_slow_effect(self):
        original = self.ask()
        confirm = self.item("command.confirm", {"confirm_id": original["confirm_id"], "answer": "Ja"}, text="Ja")
        self.service.accept(confirm, OWNER)
        entered, release, accepted = threading.Event(), threading.Event(), threading.Event()
        observed = []
        requests = []
        errors = []
        stop = object()

        class StopWorker(BaseException):
            pass

        class BoundedQueue(queue.Queue):
            def get(self, *args, **kwargs):
                item = super().get(*args, **kwargs)
                if item is stop:
                    self.task_done()
                    raise StopWorker()
                return item

        pending = BoundedQueue()
        pending.put(self.service.pending.get_nowait())
        self.service.pending = pending

        def machine_effect(intent, args):
            observed.append(intent)
            if intent == "app.open":
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("isolated test executor gate")
            return {"success": True, "evidence": {"verified": True}, "reply": "Observed isolated test."}

        self.executor.side_effect = machine_effect

        def worker_loop():
            try:
                self.service._run()
            except StopWorker:
                pass
            except BaseException as exc:
                errors.append(type(exc).__name__)

        def accept_more():
            try:
                requests.append(self.service.accept(copy.deepcopy(confirm), OWNER))
                requests.append(self.service.accept(self.item("pc.status"), OWNER))
            except BaseException as exc:
                errors.append(type(exc).__name__)
            finally:
                accepted.set()

        worker = threading.Thread(target=worker_loop, daemon=True)
        self.service.worker = worker
        sender = threading.Thread(target=accept_more, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(1), "worker never entered the isolated fake executor")
            sender.start()
            self.assertTrue(accepted.wait(0.75), "slow PC effect blocked transport acceptance")
            self.assertEqual(errors, [])
            self.assertTrue(requests[0][0])
            self.assertTrue(requests[0][1]["duplicate"])
            self.assertTrue(requests[1][0])
            self.assertEqual(requests[1][1]["status"], "queued")
        finally:
            release.set()
            if sender.ident is not None:
                sender.join(1)
            pending.put(stop)
            worker.join(2)
        self.assertFalse(worker.is_alive(), "bounded worker did not finish")
        self.assertFalse(sender.is_alive(), "transport acceptance did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(observed, ["app.open", "pc.status"])
        self.assertEqual(original["status"], "done")
        self.executor.assert_any_call("app.open", {"app": "youtube"})


class SyncTransport(IsolatedState):
    def setUp(self):
        super().setUp()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(link, "DIR", self.root / "link").start()
        mock.patch.object(link, "link_status", return_value={"server": "ONLINE"}).start()
        mock.patch.object(remote, "_SERVICE", self.service).start()
        mock.patch.object(remote, "_token", return_value=TEST_TOKEN).start()
        self.service.publisher = link.publish_pc_event
        self.headers = {"Authorization": "Bearer " + TEST_TOKEN}

    def request(self, method="POST", *, headers=None, body=None, qs=None, ip="100.64.0.10"):
        return link.handle(method, "/api/nexus-os/sync", ip,
                           self.headers if headers is None else headers, body=body, qs=qs)

    def test_old_voice_events_stay_idempotent_and_never_become_actions(self):
        item = {"id": "old-voice", "kind": "voice", "text": "Öffne YouTube am PC."}
        for _ in range(2):
            code, result = self.request(headers={}, body={"items": [item]})
            self.assertEqual(code, 200)
            self.assertEqual(result["acked"], ["old-voice"])
        lines = (link.DIR / "inbox.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(self.service.records, {})
        self.executor.assert_not_called()

    def test_pc_batch_rejected_before_acceptance_without_auth(self):
        items = [{"id": "legacy", "kind": "chat", "text": "hi"}, self.item("pc.status")]
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            code, response = self.request(headers=headers, body={"items": items})
            self.assertEqual(code, 401)
            self.assertFalse(response["success"])
            self.assertEqual(self.service.records, {})
            self.assertFalse((link.DIR / "inbox.jsonl").exists())
        self.executor.assert_not_called()

    def test_unconfigured_credentials_and_network_proxy_guards_fail_closed(self):
        with mock.patch.object(remote, "_token", return_value=""):
            code, response = self.request(body={"items": [self.item()]})
            self.assertEqual((code, response["error"]), (401, "PC_AUTH_UNCONFIGURED"))
        for ip, headers in (("8.8.8.8", self.headers), ("100.64.0.10", {
                **self.headers, "X-Forwarded-For": "100.64.0.10"})):
            code, response = self.request(ip=ip, headers=headers, body={"items": [self.item()]})
            self.assertEqual((code, response["error"]), (403, "NETWORK_NOT_ALLOWED"))
        self.executor.assert_not_called()

    def test_batch_receipts_are_durable_and_mixed_legacy_payloads_compatible(self):
        status = self.item("pc.status")
        mutation = self.item("media.pause")
        items = [status, mutation, {"id": "legacy", "kind": "chat", "text": "hi"},
                 {"id": "bad", "kind": "shell", "text": "whoami"}]
        code, response = self.request(body={"items": items})
        self.assertEqual(code, 200)
        self.assertEqual(set(response["acked"]), {status["id"], mutation["id"], "legacy"})
        self.assertEqual(response["rejected"], ["bad"])
        self.assertEqual([r["status"] for r in response["pc_commands"]], ["queued", "queued"])
        self.executor.assert_not_called()
        self.service.process_pending()
        self.executor.assert_called_once_with("pc.status", {})
        self.assertEqual(self.service.records[mutation["id"]]["status"], "needs_confirm")

    def test_private_result_events_only_reach_same_credential_and_are_idempotent(self):
        command = self.item("pc.status")
        self.request(body={"items": [command]})
        self.service.process_pending()
        event = self.service.event(self.service.records[command["id"]])
        link.publish_pc_event(event, OWNER)
        code, owner = self.request("GET", qs={"since": ["0"]})
        self.assertEqual(code, 200)
        self.assertEqual(len(owner["items"]), 1)
        self.assertEqual(owner["items"][0]["ref_id"], command["id"])
        self.assertEqual(owner["items"][0]["status"], "done")
        self.assertNotIn("_pc_owner", owner["items"][0])
        anonymous_code, anonymous = self.request("GET", headers={}, qs={"since": ["0"]})
        self.assertEqual(anonymous_code, 200)
        self.assertEqual(anonymous["items"], [])
        self.assertNotIn("pc_cursor", anonymous)
        with mock.patch.object(remote, "_token", return_value="another-offline-test-credential"):
            other_headers = {"Authorization": "Bearer another-offline-test-credential"}
            self.assertEqual(self.request("GET", headers=other_headers, qs={"since": ["0"]})[1]["items"], [])
        self.assertEqual(len((link.DIR / "outbox.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_legacy_push_survives_private_events_and_separate_pc_cursor_recovers_them(self):
        command = self.item("pc.status")
        self.request(body={"items": [command]})
        self.service.process_pending()
        link.push({"kind": "chat", "text": "public after private result"})
        code, legacy = self.request("GET", headers={}, qs={"since": ["0"]})
        self.assertEqual(code, 200)
        self.assertEqual([r["text"] for r in legacy["items"]], ["public after private result"])
        code, recovered = self.request("GET", qs={"since": [str(legacy["cursor"])], "pc_since": ["0"]})
        self.assertEqual(code, 200)
        self.assertEqual(recovered["items"][0]["ref_id"], command["id"])
        self.assertEqual(recovered["pc_cursor"], 1)
        self.assertEqual(self.request("GET", qs={"since": [str(recovered["cursor"])],
                                                 "pc_since": [str(recovered["pc_cursor"])]})[1]["items"], [])

    def test_legacy_public_message_is_not_delayed_by_private_result_backlog(self):
        for index in range(105):
            link.publish_pc_event({"event_id": "private-" + str(index), "kind": "event", "text": "private"}, OWNER)
        link.push({"text": "public immediately visible"})
        code, result = self.request("GET", headers={}, qs={"since": ["0"]})
        self.assertEqual(code, 200)
        self.assertEqual([item["text"] for item in result["items"]], ["public immediately visible"])
        self.assertEqual(result["cursor"], 106)
        paired = self.request("GET", qs={"since": ["106"], "pc_since": ["0"]})[1]
        self.assertEqual(len(paired["items"]), 100)
        final = self.request("GET", qs={"since": ["106"], "pc_since": [str(paired["pc_cursor"])]})[1]
        self.assertEqual(len(final["items"]), 5)

    def test_invalid_supplied_bearer_never_advances_even_public_cursor(self):
        link.push({"kind": "chat", "text": "public message"})
        code, result = self.request("GET", headers={"Authorization": "Bearer wrong"}, qs={"since": ["0"]})
        self.assertEqual((code, result["error"]), (401, "PC_AUTH_REQUIRED"))
        self.assertNotIn("cursor", result)
        code, result = self.request("GET", headers={}, qs={"since": ["0"]})
        self.assertEqual(code, 200)
        self.assertEqual(result["items"][0]["text"], "public message")

    def test_clarification_and_confirm_receipts_do_not_duplicate_spoken_events(self):
        command = self.item(text="Öffne ein Programm auf dem PC.")
        command.pop("intent")
        original = self.send(command)
        self.answer(original, "YouTube", intent="command.clarify")
        self.answer(original)
        events = self.request("GET", qs={"since": ["0"]})[1]["items"]
        self.assertEqual([event["status"] for event in events], ["needs_clarification", "needs_confirm", "done"])
        self.assertEqual({event["ref_id"] for event in events}, {original["id"]})
        self.executor.assert_called_once()

    def test_corrupt_or_unterminated_outbox_never_marks_result_delivered(self):
        link.DIR.mkdir(parents=True)
        path = link.DIR / "outbox.jsonl"
        for content in ('{"seq":1,"kind":"chat","text":"partial"}', '{broken}\n'):
            with self.subTest(content=content):
                path.write_text(content, encoding="utf-8")
                record = self.send(self.item("pc.status"))
                self.assertEqual(record["status"], "done")
                self.assertLess(record.get("delivered_revision", 0), record["revision"])
                self.assertEqual(path.read_text(encoding="utf-8"), content)
                with self.assertRaises(ValueError):
                    link.publish_pc_event(self.service.event(record), OWNER)

    def test_malformed_or_oversized_batch_is_rejected_without_partial_actions(self):
        for items in (None, {}, [self.item("pc.status")] * 201):
            with self.subTest(kind=type(items).__name__):
                code, response = self.request(body={"items": items})
                self.assertEqual((code, response["error"]), (400, "ITEMS_INVALID"))
                self.assertEqual(self.service.records, {})
        self.executor.assert_not_called()

    def test_push_cannot_execute_commands_or_be_written_from_phone(self):
        code, result = link.handle("POST", "/api/nexus-os/push", "100.64.0.10",
                                   self.headers, body={"kind": "event", "text": "fake done"})
        self.assertEqual((code, result["error"]), (403, "LOCAL_ONLY"))
        code, result = link.push({"kind": "pc_command", "text": "open"})
        self.assertEqual((code, result["error"]), (400, "KIND_OR_TEXT_INVALID"))
        self.executor.assert_not_called()

    def test_existing_voice_confirm_adapter_is_authenticated_and_queued(self):
        original = self.ask()
        body = {"id": original["confirm_id"], "answer": "Ja", "source": "voice", "request_id": "adapter-confirm"}
        code, result = remote.voice_confirm(body, "100.64.0.10", {})
        self.assertEqual((code, result["error"]), (401, "PC_AUTH_REQUIRED"))
        code, result = remote.voice_confirm(body, "100.64.0.10", self.headers)
        self.assertEqual(code, 202)
        self.assertEqual(result["receipt"]["status"], "queued")
        self.executor.assert_not_called()
        self.service.process_pending()
        self.assertEqual(original["status"], "done")
        self.executor.assert_called_once()
        code, duplicate = remote.voice_confirm(body, "100.64.0.10", self.headers)
        self.assertEqual(code, 202)
        self.assertTrue(duplicate["receipt"]["duplicate"])
        self.service.process_pending()
        self.executor.assert_called_once()


if __name__ == "__main__":
    unittest.main()
