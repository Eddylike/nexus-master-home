"""Offline regression tests: no real executors, approvals, network or task files.

Run from project root: python -B -m unittest discover -s tests -p test_bridge_outcomes.py -v
"""
from __future__ import annotations

import ast
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from nexus.bridge.errors import BridgeError
from nexus.bridge.models import TaskCreateRequest
from nexus.bridge.outcomes import RULES, evaluate
from nexus.bridge.permissions import TASK_PERMISSIONS, PermissionLevel
from nexus.bridge.tasks import TaskManager

ROOT = Path(__file__).resolve().parents[1]


def process(code=0, stderr=""):
    return {"returncode": code, "stdout": "", "stderr": stderr}


def service(name="Ollama", status=4, code=0, stderr=""):
    return {**process(code, stderr), "name": name,
            "stdout": json.dumps({"Name": name, "Status": status, "StartType": 2})}


def nexus_status():
    return {"root": str(ROOT), "port": 8299, "process_level": "SAFE_WRITE",
            "origin": "UP", "gateway": "UP", "gateway_code": 401,
            "health": {"ok": True}}


def file_read():
    # A real read of a non-secret existing project source, without calling an executor.
    path = ROOT / "nexus" / "bridge" / "models.py"
    data = path.read_bytes()
    return {"path": "nexus/bridge/models.py", "abs_path": str(path), "size": len(data),
            "content": data.decode("utf-8"), "encoding": "utf-8", "truncated": False}


class MemoryStorage:
    def __init__(self):
        self.records = {}
        self.history = []

    def put(self, record):
        self.records[record.id] = copy.deepcopy(record)
        self.history.append(copy.deepcopy(record))

    def get(self, task_id):
        return self.records.get(task_id)


class OutcomesTest(unittest.TestCase):
    def assertVerdict(self, name, data, verdict, params=None):
        before = copy.deepcopy(data)
        outcome = evaluate(name, params or {}, data)
        self.assertEqual(outcome.verdict, verdict, (name, outcome.reason))
        self.assertIs(outcome.data, data)
        self.assertEqual(data, before)
        self.assertEqual(outcome.status, "succeeded" if verdict == "succeeded" else "failed")
        return outcome

    def test_every_registered_executor_has_an_explicit_rule(self):
        self.assertEqual(set(RULES), set(TASK_PERMISSIONS))
        tree = ast.parse((ROOT / "nexus/bridge/executors.py").read_text(encoding="utf-8"))
        binder = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "bind_default_executors")
        mapping = next(n.value for n in binder.body if isinstance(n, ast.Assign))
        from nexus.bridge.dev_executors import EXECUTORS as dev_executors
        self.assertEqual({ast.literal_eval(k) for k in mapping.keys} | set(dev_executors), set(RULES))
        mapping_doc = (ROOT / "docs/BRIDGE_OUTCOMES.md").read_text(encoding="utf-8")
        for name in RULES:
            self.assertIn(name, mapping_doc)

    def test_missing_results_never_succeed_for_any_registered_task(self):
        for name in RULES:
            for data in (None, {}, [], True, "PASS", {"ok": True}, {"status": "PASS"}):
                with self.subTest(name=name, data=data):
                    self.assertVerdict(name, data, "verification_unknown")

    def test_explicit_failure_wins_for_every_registered_task(self):
        for name in RULES:
            for data in ({"ok": False}, {"success": False}, {"error": "synthetic"},
                         {"error_code": "BROKEN"}, {"returncode": 9, "ok": True},
                         {"returncode": -9}, {"exit_code": 2}, {"status": "PARTIAL"},
                         {"status": "FAIL"}, {"status": "BLOCKED"}):
                with self.subTest(name=name, data=data):
                    self.assertVerdict(name, data, "failed")

    def test_process_success_and_bad_exit_types(self):
        for name in ("git_status", "git_diff", "git_log", "git_add", "git_commit", "git_push",
                     "python_run_project_script", "powershell_registered_action", "android_install_apk"):
            good = {**process(), "available": True}
            self.assertVerdict(name, good, "succeeded")
            self.assertVerdict(name, {**good, "stderr": "warning"}, "succeeded")
            for code in (None, False, True, "0", 0.0):
                self.assertVerdict(name, {**good, "returncode": code}, "verification_unknown")
            self.assertVerdict(name, {**good, "exit_code": 3}, "failed")
            self.assertVerdict(name, {**good, "stdout": None}, "verification_unknown")
        self.assertVerdict("git_status", {**process(), "available": False}, "failed")

    def test_file_read_listing_success_and_opaque_content(self):
        self.assertVerdict("read_file", file_read(), "succeeded")
        empty = {**file_read(), "content": "", "size": 0}
        self.assertVerdict("read_file", empty, "succeeded")
        opaque = {**empty, "content": '{"error":"content only","ok":false}'}
        self.assertVerdict("read_file", opaque, "succeeded")
        self.assertVerdict("read_file", {**empty, "truncated": True}, "succeeded")
        self.assertVerdict("read_file", {**empty, "size": None}, "verification_unknown")
        listing = {"path": "", "count": 0, "entries": [], "truncated": False}
        self.assertVerdict("list_files", listing, "succeeded")
        self.assertVerdict("list_files", {**listing, "count": 1, "entries": [
            {"name": "file.txt", "type": "file", "size": 0}]}, "succeeded")
        self.assertVerdict("list_files", {**listing, "count": 1, "entries": [
            {"name": "file.txt", "type": "unknown"}]}, "verification_unknown")
        self.assertVerdict("list_files", {**listing, "count": 5}, "verification_unknown")

    def test_file_write_evidence_only_no_file_actions(self):
        samples = {
            "write_file": {"path": "x", "bytes": 0, "atomic": True},
            "create_file": {"path": "x", "bytes": 0, "created": True},
            "create_dir": {"path": "x", "created": True, "is_dir": True},
            "patch_file": {"path": "x", "bytes": 2, "changed": True, "replacements": 1},
            "move_file": {"src": "x", "dst": "y", "moved": True},
            "copy_file": {"path": "x", "destination": "y", "copied": True},
            "delete_file": {"path": "x", "deleted": True, "kind": "file"},
        }
        for name, data in samples.items():
            self.assertVerdict(name, data, "succeeded")
            self.assertVerdict(name, {**data, "ok": False}, "failed")
            self.assertVerdict(name, {**data, "returncode": 1}, "failed")
        self.assertVerdict("patch_file", {"path": "x", "changed": False, "replacements": 0}, "failed")

    def test_service_single_and_nested_results(self):
        good = {"services": {name: service(name) for name in ("Ollama", "Tailscale")},
                "allowed": ["Ollama", "Tailscale"]}
        self.assertVerdict("service_status", good, "succeeded")
        for bad, verdict in ((service(code=1), "failed"), ({"error": "missing"}, "failed"),
                             (None, "verification_unknown"), (service(stderr="error"), "failed")):
            self.assertVerdict("service_status", {**good, "services": {
                **good["services"], "Ollama": bad}}, verdict)
        self.assertVerdict("service_status", service(status=1), "succeeded", {"name": "Ollama"})
        self.assertVerdict("service_status", {**service(), "stdout": ""}, "verification_unknown", {"name": "Ollama"})
        self.assertVerdict("service_status", service("Tailscale"), "verification_unknown", {"name": "Ollama"})

    def test_service_control_requires_target_and_restart_remains_unknown(self):
        self.assertVerdict("service_start", {**service(), "verb": "start"}, "succeeded", {"name": "Ollama"})
        self.assertVerdict("service_stop", {**service(status=1), "verb": "stop"}, "succeeded", {"name": "Ollama"})
        self.assertVerdict("service_stop", {**service(), "verb": "stop"}, "failed", {"name": "Ollama"})
        self.assertVerdict("service_restart", {**service(), "verb": "restart"}, "verification_unknown", {"name": "Ollama"})

    def test_status_queries_and_nested_failures(self):
        status = nexus_status()
        self.assertVerdict("nexus_status", status, "succeeded")
        self.assertVerdict("nexus_status", {**status, "gateway": "DOWN"}, "failed")
        self.assertVerdict("nexus_status", {**status, "health": {"ok": False}}, "failed")
        self.assertVerdict("nexus_status", {**status, "health": None}, "verification_unknown")
        control = {**status, "tailscale": service("Tailscale"), "pid": 42,
                   "public_url": "https://example.invalid", "external": "UP", "external_code": 401}
        self.assertVerdict("nexus_control_status", control, "succeeded")
        self.assertVerdict("nexus_control_status", {**control, "tailscale": service("Tailscale", code=2)}, "failed")
        llm = {"routing": "local-first", "cost_rule": "LOCAL->FREE->NOT",
               "ollama": {"reachable": True, "models": [], "preferred": "test"}}
        self.assertVerdict("llm_status", llm, "succeeded")
        self.assertVerdict("llm_status", {**llm, "ollama": {"reachable": False}}, "failed")
        self.assertVerdict("voice_status", {"voice_config": {"error": "unreadable"}}, "failed")
        self.assertVerdict("media_status", {"ok": False}, "failed")

    def test_static_and_metric_snapshots(self):
        registry = list(TASK_PERMISSIONS)
        inspect = dict.fromkeys(("server", "handler", "host", "version", "model", "root",
                                 "bridge_protocol", "bridge_version", "process_level"), "fixture")
        inspect.update(port=8299, ssl_port=8443, registered_tasks=registry, endpoints=["GET /api/task/{id}"])
        self.assertVerdict("inspect_server", inspect, "succeeded")
        caps = dict.fromkeys(("protocol", "version", "process_level", "path_root", "notes"), "fixture")
        caps.update(permissions=["READ_ONLY", "SAFE_WRITE", "PRIVILEGED"],
                    tasks=[{"name": n, "permission": p.value} for n, p in TASK_PERMISSIONS.items()],
                    executors=dict.fromkeys(("PowerShell", "Python", "File", "Git", "System", "Approval"), True))
        self.assertVerdict("capability_check", caps, "succeeded")
        system = dict.fromkeys(("platform", "release", "machine", "python", "cwd", "root", "process_level"), "fixture")
        system.update(nexus_port=8299, nexus_ssl_port=8443, disk_free_gb=1.2,
                      metrics={"success": True, "timestamp": "fixture", "cpu": {}, "ram": {}, "disk": {}, "gpu": None, "platform": {}})
        self.assertVerdict("system_status", system, "succeeded")
        self.assertVerdict("system_status", {**system, "metrics": {"success": False, "error": "fixture"}}, "failed")
        self.assertVerdict("system_status", {**system, "disk_free_gb": None}, "verification_unknown")

    def test_scheduled_and_unverifiable_executors_never_succeed(self):
        for name in ("nexus_start", "nexus_stop", "nexus_restart", "restart_nexus_bridge_exact"):
            self.assertVerdict(name, {"scheduled": True, "via": "test.ps1"}, "verification_unknown")
            self.assertVerdict(name, {"status": "PASS", "old_pid": 1, "new_pid": None,
                                      "bridge_health": "RESTARTING"}, "verification_unknown")
        for name in ("media_status", "voice_status", "service_restart", "nexus_start",
                     "android_bootloader_status", "android_termux_status"):
            self.assertNotEqual(evaluate(name, {}, {"status": "PASS", "ok": True}).status, "succeeded")
        self.assertVerdict("ping", {"ok": True, "pong": True, "scheduled": True}, "verification_unknown")
        self.assertVerdict("ping", {"ok": True, "pong": True, "approval_required": True}, "verification_unknown")
        for state in ("running", "pending", "unknown", None, "verification_unknown"):
            self.assertVerdict("ping", {"ok": True, "pong": True, "status": state}, "verification_unknown")
        self.assertVerdict("unregistered", {"ok": True}, "verification_unknown")

    def test_host_device_result_contracts_only_no_device_calls(self):
        for name in ("android_status", "android_devices", "termux_status", "android_version", "android_build",
                     "android_security_patch", "android_fingerprint", "android_slot", "android_storage",
                     "android_battery", "android_network"):
            self.assertVerdict(name, {**process(), "adb": True}, "succeeded")
            self.assertVerdict(name, {**process(1), "adb": True}, "failed")
        self.assertVerdict("android_screenshot", {"ok": True, "path": "fixture.png", "bytes": 10}, "succeeded")

    def test_task_lifecycle_preserves_data_and_records_failure(self):
        samples = [("ping", {"ok": True, "pong": True}, "succeeded", None),
                   ("ping", {"ok": False}, "failed", "TASK_FAILED"),
                   ("git_status", {**process(7, "synthetic error"), "available": True}, "failed", "TASK_FAILED"),
                   ("ping", None, "failed", "VERIFICATION_UNKNOWN"),
                   ("read_file", file_read(), "succeeded", None),
                   ("nexus_status", nexus_status(), "succeeded", None)]
        with patch("nexus.bridge.tasks.TaskStorage", side_effect=AssertionError("disk storage forbidden")), \
             patch("nexus.bridge.tasks.process_level", return_value=PermissionLevel.READ_ONLY):
            for name, data, status, code in samples:
                storage = MemoryStorage()
                manager = TaskManager(storage)
                manager.register(name, lambda params, rec, result=data: result)
                params = {"echo": "fixture"}
                record = manager.create_and_run(TaskCreateRequest(task=name, params=params))
                self.assertEqual((record.status, record.error_code), (status, code))
                self.assertIs(record.result, data)
                self.assertEqual(record.params, params)
                self.assertEqual(storage.history[0].status, "running")
                self.assertEqual(storage.history[-1].status, status)
                self.assertEqual(manager.get(record.id).result, data)
                self.assertIn(f"status={status}", record.log[-1])

    def test_exception_and_cancellation_paths_unchanged(self):
        manager = TaskManager(MemoryStorage())
        def throwing(params, rec):
            raise BridgeError("INVALID_REQUEST", "fixture failure")
        manager.register("ping", throwing)
        rec = manager.create_and_run(TaskCreateRequest(task="ping"))
        self.assertEqual((rec.status, rec.error_code), ("failed", "INVALID_REQUEST"))
        def cancel(params, rec):
            manager.cancel(rec.id)
            return {"ok": True, "pong": True}
        manager.register("ping", cancel)
        rec = manager.create_and_run(TaskCreateRequest(task="ping"))
        self.assertEqual((rec.status, rec.error_code), ("cancelled", "TASK_CANCELLED"))
        self.assertEqual(rec.result, {"ok": True, "pong": True})

    def test_http_caller_propagates_normalized_task_status_offline(self):
        # Compile only existing handle_post, avoiding API boot/import side effects.
        source = ast.parse((ROOT / "nexus/bridge/api.py").read_text(encoding="utf-8"))
        fn = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == "handle_post")
        manager = TaskManager(MemoryStorage())
        scope = {"require_auth": lambda handler: True, "_norm_path": lambda path: path,
                 "TaskCreateRequest": TaskCreateRequest, "manager": lambda: manager, "BridgeError": BridgeError}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), "api.handle_post", "exec"), scope)
        class Handler:
            def _json(self, code, body):
                self.code, self.body = code, body
        for data, expected in (({"ok": False}, 400), (None, 400), ({"ok": True, "pong": True}, 200)):
            manager.register("ping", lambda params, rec, result=data: result)
            handler = Handler()
            scope["handle_post"](handler, "/api/task", {"task": "ping"})
            self.assertEqual(handler.code, expected)
            self.assertEqual(handler.body["success"], expected == 200)
            self.assertEqual(handler.body["task"]["result"], data)


if __name__ == "__main__":
    unittest.main()
