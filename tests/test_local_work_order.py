"""Local AI/device routing and receipts without a real phone or shell.

The integration cases retain the real Orchestrator, AutomationStore, TaskManager,
permission gates and persisted receipts. Only registered executor bodies are fake.
All fixtures and temporary files stay inside the project workspace.
"""
from __future__ import annotations

from contextlib import ExitStack
import importlib
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from nexus.bridge.outcomes import ExecutorOutcome, evaluate
from nexus.bridge.permissions import TASK_PERMISSIONS, PermissionLevel
from nexus.bridge.storage import TaskStorage
from nexus.bridge.tasks import TaskManager
from nexus.maintenance import MaintenanceController
from nexus.master import evidence, local_order
from nexus.tasks.local import capability_gaps, is_local_work, plan_local_work


ORIGINAL = (
    "**NEXUS MOBILE | QWEN3-INTEGRATION | SAFE_WRITE**\n"
    "Verbinde das vorhandene Qwen3-1.7B-Modell auf dem per ADB verbundenen Redmi Note 13 vollständig mit NEXUS Mobile.\n"
    "- ADB-Gerät `249ac5d7` verwenden.\n"
    "- Bestehende Termux-Architektur und KI-Schnittstellen selbstständig analysieren.\n"
    "- Ollama unter `127.0.0.1:11434` auf dem Handy anbinden.\n"
    "- Qwen3 als lokale Chat-KI mit Gesprächsverlauf integrieren.\n"
    "- Bestehende Sprachsteuerung, Oberfläche und PC-Verbindung erhalten.\n"
    "- Vor Änderungen Backups erstellen.\n"
    "- Integration einschließlich Offlinebetrieb prüfen.\n"
    "**Regeln:** Keine Neuinstallation, kein Root, keine Änderungen an C:.\n"
    "**Abnahme:** NEXUS Mobile kommuniziert direkt mit Qwen3, unabhängig vom PC und Internet."
)


def _plan(text=ORIGINAL):
    out = plan_local_work(text)
    out["request"] = text
    return out


def _phone_records(state="device"):
    return [{"task": "android_devices", "result": {"adb": True, "returncode": 0,
             "stdout": "List of devices attached\n249ac5d7\t" + state + " product:redmi\n", "stderr": ""}}]


class LocalPlanningTests(unittest.TestCase):
    def test_original_is_local_and_has_no_pc_ollama_or_browser_step(self):
        self.assertTrue(is_local_work(ORIGINAL))
        plan = _plan()
        self.assertFalse(plan["requires_web"])
        self.assertEqual(plan["device"], "249ac5d7")
        self.assertEqual([s["action"] for s in plan["steps"]], ["android_devices"])

    def test_plan_never_expands_to_uncontrolled_execution(self):
        plan = _plan(ORIGINAL + "\nShell cmd PowerShell python_run_project_script delete_file git_push root.")
        for step in plan["steps"]:
            self.assertIn(step["action"], TASK_PERMISSIONS)
            self.assertNotEqual(TASK_PERMISSIONS[step["action"]], PermissionLevel.PRIVILEGED)
        self.assertEqual({s["action"] for s in plan["steps"]}, {"android_devices"})

    def test_explanation_and_negated_work_are_not_execution_authorization(self):
        for text in ("Was ist Qwen3?", "Wie integriere ich Ollama?", "Erkläre die ADB-Schnittstelle.",
                     "Prüfe Ollama nicht.", "Integriere Qwen3 niemals."):
            with self.subTest(text=text):
                self.assertFalse(is_local_work(text))

    def test_explicit_file_and_python_orders_keep_existing_contract(self):
        for text in ('Erstelle die Datei ollama_notes.md mit Inhalt: "Prüfe Qwen3".',
                     'Erstelle ein Python-Skript qwen_probe.py, das Ollama prüft.'):
            with self.subTest(text=text):
                self.assertFalse(is_local_work(text))

    def test_reports_and_html_js_artifacts_do_not_become_local_probe_orders(self):
        for text in ("Erstelle einen Bericht über Qwen3 und Ollama.",
                     "Schreibe eine Markdown-Datei über den ADB-Status.",
                     "Erstelle qwen_status.md und beschreibe den lokalen Ollama-Status.",
                     "Baue eine HTML-Webseite über Ollama mit CSS und JavaScript.",
                     "Erstelle index.html mit einer Qwen3-Statusanzeige."):
            with self.subTest(text=text):
                self.assertFalse(is_local_work(text))

    def test_phone_ai_is_unknown_without_a_fresh_app_heartbeat(self):
        plan = _plan("Prüfe Qwen3.5-0.8B auf dem Handy. ADB-Gerät `249ac5d7` verwenden.")
        gaps = capability_gaps(plan, _phone_records())
        self.assertEqual(gaps[0]["code"], "PHONE_LOCAL_AI_UNVERIFIED")

    def test_unknown_task_contract_never_claims_a_keyword_proves_unavailability(self):
        for text in ("unabhängig vom PC und Internet", "ohne Browser", "online", "Browser"):
            self.assertNotIn("Web-/Browserzugriff", evidence._unhandled_capability(text))

    def test_original_routing_precedes_legacy_browser_and_self_change_routes(self):
        sentinel = {"intent": "hermes_work", "status": "PARTIAL"}
        with patch("nexus.master.local_order.run", return_value=sentinel) as local, \
             patch("nexus.master.web_order.run") as web, \
             patch("nexus.master.nexus_order.run") as nexus:
            out = evidence.route_work(SimpleNamespace(), ORIGINAL, [], {})
        self.assertIs(out, sentinel)
        local.assert_called_once()
        web.assert_not_called()
        nexus.assert_not_called()

    def test_phone_offline_is_exact_local_gap_even_when_pc_could_be_online(self):
        gaps = capability_gaps(_plan(), _phone_records())
        self.assertEqual(gaps[0]["code"], "PHONE_LOCAL_AI_UNVERIFIED")
        self.assertIn("Handy", gaps[0]["detail"])
        self.assertNotIn("Browser", gaps[0]["detail"])

    def test_phone_inventory_does_not_claim_a_model_or_ai_readiness(self):
        gaps = capability_gaps(_plan(), _phone_records())
        self.assertEqual(gaps[0]["code"], "PHONE_LOCAL_AI_UNVERIFIED")

    def test_wrong_or_unauthorized_device_is_a_device_gap(self):
        gaps = capability_gaps(_plan(), _phone_records(state="unauthorized"))
        self.assertEqual(gaps[0]["code"], "ADB_TARGET_UNAUTHORIZED")
        records = _phone_records()
        records[0]["result"]["stdout"] = "other-phone\tdevice\n"
        gaps = capability_gaps(_plan(), records)
        self.assertEqual(gaps[0]["code"], "ADB_TARGET_NOT_CONNECTED")

    def test_successful_probes_never_claim_full_integration(self):
        gaps = capability_gaps(_plan(), _phone_records())
        self.assertEqual(gaps[0]["code"], "PHONE_LOCAL_AI_UNVERIFIED")
        self.assertIn("Heartbeat", gaps[0]["detail"])

    def test_model_inventory_cannot_prove_chat_or_offline_operation(self):
        for text in ("Prüfe den Offlinebetrieb von Qwen3 auf dem PC.", "Prüfe, ob Qwen3 im Chat antwortet."):
            with self.subTest(text=text):
                plan = _plan(text)
                records = [{"task": "llm_status", "result": {"ollama": {"reachable": True, "models": ["qwen3:1.7b"]}}}]
                self.assertEqual(capability_gaps(plan, records)[0]["code"], "LOCAL_FUNCTIONAL_TEST_REQUIRED")

    def test_diagnostic_without_target_never_selects_old_phone(self):
        plan = _plan("Prüfe Qwen3 auf dem per ADB verbundenen Handy.")
        self.assertIsNone(plan["device"])
        self.assertEqual([s["action"] for s in plan["steps"]], ["android_devices"])
        self.assertEqual(capability_gaps(plan, _phone_records())[0]["code"], "ADB_TARGET_REQUIRED")


class LocalExecutionReceiptTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / "workspace" / "tmp" / "local_work_order_tests"
        base.mkdir(parents=True, exist_ok=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory(dir=base))
        self.root = Path(self.temp)
        self.stack.enter_context(patch.dict(os.environ, {"TMP": self.temp, "TEMP": self.temp,
                                                       "NEXUS_BRIDGE_PROCESS_LEVEL": "SAFE_WRITE"}))
        self.stack.enter_context(patch.object(tempfile, "tempdir", self.temp))
        self.stack.enter_context(patch.object(local_order, "ROOT", self.root))
        orchestrator = importlib.import_module("nexus.tasks.orchestrator")
        routines = importlib.import_module("nexus.automation.routines")
        self.stack.enter_context(patch.object(orchestrator, "STORE", self.root / "workspace/tasks/orchestrator"))
        self.stack.enter_context(patch.object(routines, "STORE_DIR", self.root / "workspace/automation/routines"))
        self.stack.enter_context(patch.object(routines, "RUNS_DIR", self.root / "workspace/automation/runs"))
        automation = routines.AutomationStore()
        self.stack.enter_context(patch.object(orchestrator, "routines", return_value=automation))
        self.manager = TaskManager(TaskStorage(self.root / "workspace/bridge/tasks"))
        self.stack.enter_context(patch("nexus.bridge.api.manager", return_value=self.manager))
        maintenance = MaintenanceController(self.root / "workspace/state/maintenance.json")
        self.stack.enter_context(patch("nexus.maintenance.maintenance", return_value=maintenance))
        actual_persisted = evidence.persisted_task
        self.stack.enter_context(patch.object(evidence, "persisted_task",
                                             side_effect=lambda task_id: actual_persisted(task_id, root=self.root)))
        self.calls = []
        self.models = ["qwen3:1.7b"]
        self.manager.register("android_devices", self._devices)
        self.manager.register("llm_status", self._llm)
        self.session = {"id": "local-test-session"}
        self.master = SimpleNamespace(_persist_chat_order=lambda: None)

    def _devices(self, params, record):
        self.calls.append((record.task, dict(params)))
        return _phone_records()[0]["result"]

    def _llm(self, params, record):
        self.calls.append((record.task, dict(params)))
        return {"routing": "local-first", "cost_rule": "LOCAL->FREE->NOT",
                "ollama": {"reachable": True, "models": self.models, "preferred": "qwen3:1.7b"}}

    def test_local_pc_diagnostic_uses_real_persisted_bridge_receipt(self):
        out = evidence.route_work(self.master, "Prüfe Qwen3 auf dem PC ohne Internet.", [], self.session)
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(self.calls, [("llm_status", {})])
        self.assertRegex(out["data"]["order_id"], r"^WO-\d{8}-")
        ids = out["data"]["task_ids"]
        self.assertEqual(len(ids), 1)
        self.assertRegex(ids[0], r"^NX-\d{8}-\d+$")
        receipt = self.manager.get(ids[0]).to_dict()
        self.assertEqual(receipt["task"], "llm_status")
        self.assertEqual(receipt["status"], "succeeded")
        self.assertEqual(len(list((self.root / "workspace/state").glob("local-work-*.json"))), 1)

    def test_original_runs_only_registered_phone_inventory_and_reports_ai_unknown(self):
        out = evidence.route_work(self.master, ORIGINAL, [], self.session)
        self.assertEqual(out["status"], "PARTIAL")
        self.assertEqual(out["error"], "PHONE_LOCAL_AI_UNVERIFIED")
        self.assertEqual(self.calls, [("android_devices", {})])
        self.assertEqual(len(out["data"]["task_ids"]), 1)
        self.assertNotIn("Web-/Browserzugriff", out["reply"])
        self.assertEqual(self.manager.get(out["data"]["task_ids"][0]).status, "succeeded")

    def test_read_only_process_allows_phone_inventory_but_not_ai_claim(self):
        with patch.dict(os.environ, {"NEXUS_BRIDGE_PROCESS_LEVEL": "READ_ONLY"}):
            out = evidence.route_work(self.master, ORIGINAL, [], self.session)
        self.assertTrue(out["success"] is False)
        self.assertEqual(out["status"], "PARTIAL")
        self.assertEqual(self.calls, [("android_devices", {})])
        self.assertIn("PHONE_LOCAL_AI_UNVERIFIED", out["reply"])
        self.assertEqual(len(out["data"]["task_ids"]), 1)

    def test_registered_action_without_executor_has_specific_error_and_order_id(self):
        self.manager._executors.pop("llm_status")
        out = evidence.route_work(self.master, "Prüfe Qwen3 auf dem PC.", [], self.session)
        self.assertFalse(out["success"])
        self.assertIn("no executor bound", out["reply"])
        self.assertIsNotNone(out["data"]["order_id"])
        self.assertEqual(out["data"]["task_ids"], [])
        self.assertEqual(self.calls, [])

    def test_missing_registry_action_reports_exact_action_without_execution(self):
        with patch.dict(TASK_PERMISSIONS) as registry:
            registry.pop("llm_status")
            out = evidence.route_work(self.master, "Prüfe Qwen3 auf dem PC.", [], self.session)
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "LOCAL_ACTION_NOT_REGISTERED")
        self.assertIn("llm_status", out["reply"])
        self.assertEqual(self.calls, [])

    def test_unsupported_endpoint_names_exact_contract_gap_without_execution(self):
        for endpoint in ("192.168.1.15:11434", "http://127.0.0.1:11435", "http://localhost:22434",
                         "https://example.invalid:11434"):
            with self.subTest(endpoint=endpoint):
                out = evidence.route_work(self.master, "Prüfe Qwen3 unter " + endpoint + " auf dem PC.", [], self.session)
                self.assertFalse(out["success"])
                self.assertEqual(out["error"], "LOCAL_AI_ENDPOINT_NOT_SUPPORTED")
                self.assertEqual(out["data"]["task_ids"], [])
                self.assertEqual(self.calls, [])
                self.assertNotIn("Web-/Browserzugriff", out["reply"])

    def test_retired_phase_a_deployment_is_not_registered(self):
        self.assertNotIn("deploy_phase_a_control", TASK_PERMISSIONS)
        orchestrator = importlib.import_module("nexus.tasks.orchestrator").Orchestrator()
        with self.assertRaisesRegex(ValueError, "unknown registered action"):
            orchestrator.create("retired Phase-A deployment", [
                {"action": "deploy_phase_a_control", "params": {"device": "249ac5d7"}}
            ])
        self.assertEqual(self.calls, [])

    def test_missing_model_never_receives_pass_from_successful_status_action(self):
        self.models = ["llama3:8b"]
        out = evidence.route_work(self.master, "Prüfe Qwen3 auf dem PC.", [], self.session)
        self.assertFalse(out["success"])
        self.assertEqual(out["error"], "QWEN3_MODEL_NOT_FOUND")
        self.assertEqual(len(out["data"]["task_ids"]), 1)

    def test_repeat_and_resume_keep_same_ids_without_reexecuting(self):
        text = "Prüfe Qwen3 auf dem PC."
        first = evidence.route_work(self.master, text, [], self.session)
        second = evidence.route_work(self.master, text, [], self.session)
        resumed = evidence.route_work(self.master, "weiter", [], self.session)
        self.assertEqual(len(self.calls), 1)
        for out in (second, resumed):
            self.assertEqual(out["data"]["order_id"], first["data"]["order_id"])
            self.assertEqual(out["data"]["task_ids"], first["data"]["task_ids"])

    def test_missing_persisted_receipt_is_not_success(self):
        with patch.object(evidence, "persisted_task", return_value={}):
            out = evidence.route_work(self.master, "Prüfe Qwen3 auf dem PC.", [], self.session)
        self.assertEqual(out["error"], "LOCAL_RECEIPT_MISMATCH")
        self.assertFalse(out["success"])
        self.assertEqual(out["data"]["task_ids"], [])

    def test_persisted_receipt_status_must_match_the_completed_plan(self):
        text = "Prüfe Qwen3 auf dem PC."
        first = evidence.route_work(self.master, text, [], self.session)
        self.assertEqual(first["status"], "PASS")
        receipt = self.manager.get(first["data"]["task_ids"][0])
        receipt.status = "failed"
        receipt.error_code = "TEST_RECEIPT_CHANGED"
        self.manager.storage.put(receipt)
        second = evidence.route_work(self.master, text, [], self.session)
        self.assertFalse(second["success"])
        self.assertEqual(second["error"], "LOCAL_RECEIPT_MISMATCH")
        self.assertEqual(len(self.calls), 1)

    def test_modified_or_expanded_persisted_plan_is_refused_before_execution(self):
        module = importlib.import_module("nexus.tasks.orchestrator")
        orchestrator = module.Orchestrator()
        text = "Prüfe Qwen3 auf dem PC."
        for mutation in ("extra_step", "action", "params", "repair", "dependency"):
            with self.subTest(mutation=mutation):
                plan = plan_local_work(text)
                order = orchestrator.create(plan["title"], plan["steps"])
                if mutation == "extra_step":
                    order.steps.append(module.Step(action="python_run_project_script", params={"path": "workspace/scripts/not_executed.py"}))
                elif mutation == "action":
                    order.steps[0].action = "python_run_project_script"
                elif mutation == "params":
                    order.steps[0].params = {"url": "http://example.invalid"}
                elif mutation == "repair":
                    order.steps[0].repair = {"action": "delete_file", "params": {"path": "workspace/not_deleted.txt"}}
                else:
                    order.steps[0].depends_on = [0]
                orchestrator._save(order)
                self.session["local_order"] = {"task": text, "order_id": order.id}
                out = evidence.route_work(self.master, text, [], self.session)
                self.assertFalse(out["success"])
                self.assertEqual(out["error"], "LOCAL_PLAN_MISMATCH")
                self.assertEqual(self.calls, [])
                self.assertEqual(self.manager.list(), [])



if __name__ == "__main__":
    unittest.main(verbosity=2)
