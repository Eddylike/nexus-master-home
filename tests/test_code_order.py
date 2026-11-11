"""Offline contracts for the Hermes code-development workflow.

No network, no bridge, no LLM: the derivation is exercised with a fake LLM that
returns canned JSON, and routing is checked by monkeypatching the workflow entry
point. The static checker and executor contracts live in test_code_sandbox.py.
"""
import json
import re
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from nexus.bridge.outcomes import evaluate
from nexus.integrations.hermes_brain import HermesBrain
from nexus.master import code_order as co

GOAL = "Entwickle einen einfachen Python-Rechner für die vier Grundrechenarten und teste ihn."

VALID_PLAN = {
    "project": "rechner",
    "files": [
        {"path": "workspace/projects/hermes/code/rechner/rechner.py",
         "content": "def add(a, b):\n    return a + b\n"},
        {"path": "workspace/projects/hermes/code/rechner/test_rechner.py",
         "content": "from rechner import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"},
        {"path": "workspace/projects/hermes/code/rechner/README.md", "content": "# Rechner\n"},
    ],
}


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        return {"success": True, "reply": json.dumps(self.payload), "model": "fake"}


def brain(payload):
    return HermesBrain(SimpleNamespace(llm=FakeLLM(payload)))


class IsCodeOrderTests(unittest.TestCase):
    def test_detects_development_goal(self):
        self.assertTrue(co.is_code_order(GOAL))
        self.assertTrue(co.is_code_order("Schreibe ein Python-Programm rechner.py mit Tests"))
        self.assertTrue(co.is_code_order("Baue ein Modul mit einer Funktion und pytest-Tests"))

    def test_rejects_questions_and_negation(self):
        self.assertFalse(co.is_code_order("Wie erstelle ich ein Python-Skript?"))
        self.assertFalse(co.is_code_order("Erstelle bitte keine Python-Datei"))
        self.assertFalse(co.is_code_order("Gib mir ein Beispiel für Python-Code"))

    def test_rejects_explicit_content(self):
        self.assertFalse(co.is_code_order('Schreibe rechner.py inhalt: "print(1)"'))

    def test_rejects_non_code_request(self):
        self.assertFalse(co.is_code_order("Schreibe einen kurzen Bericht über Python"))


class DeriveCodePlanTests(unittest.TestCase):
    def test_valid_plan(self):
        d = brain(VALID_PLAN).derive_code_plan("rechner")
        self.assertTrue(d["success"], d)
        self.assertEqual(d["project"], "rechner")
        self.assertEqual(d["test_path"].rsplit("/", 1)[-1], "test_rechner.py")
        self.assertEqual(len(d["main_paths"]), 1)
        self.assertEqual(len(d["files"]), 3)

    def test_missing_test_module(self):
        plan = {"project": "x", "files": [
            {"path": "workspace/projects/hermes/code/x/x.py", "content": "a = 1\n"}]}
        self.assertEqual(brain(plan).derive_code_plan("x")["error"], "CODE_PLAN_TEST_MODULE_REQUIRED")

    def test_not_flat(self):
        plan = {"project": "x", "files": [
            {"path": "workspace/projects/hermes/code/x/a/x.py", "content": "a = 1\n"},
            {"path": "workspace/projects/hermes/code/x/test_x.py", "content": "a = 1\n"}]}
        self.assertEqual(brain(plan).derive_code_plan("x")["error"], "CODE_PLAN_NOT_FLAT")

    def test_slug_mismatch(self):
        plan = {"project": "x", "files": [
            {"path": "workspace/projects/hermes/code/x/x.py", "content": "a = 1\n"},
            {"path": "workspace/projects/hermes/code/y/test_y.py", "content": "a = 1\n"}]}
        self.assertEqual(brain(plan).derive_code_plan("x")["error"], "CODE_PLAN_SLUG_MISMATCH")

    def test_bad_extension(self):
        plan = {"project": "x", "files": [
            {"path": "workspace/projects/hermes/code/x/x.py", "content": "a = 1\n"},
            {"path": "workspace/projects/hermes/code/x/test_x.txt", "content": "a = 1\n"}]}
        self.assertEqual(brain(plan).derive_code_plan("x")["error"], "CODE_PLAN_PATH_NOT_AUTHORIZED")


class AggregateTests(unittest.TestCase):
    def test_status_matrix(self):
        pass_batch = {"batches": [{"status": "PASS"}], "static": []}
        self.assertEqual(co._aggregate(dict(pass_batch), True, True, None), "PARTIAL")
        self.assertEqual(co._aggregate(dict(pass_batch), True, True, {"status": "succeeded"}), "PASS")
        self.assertEqual(co._aggregate({"batches": [{"status": "PASS"}], "static": [{"path": "a"}]},
                                       True, False, None), "PARTIAL")
        self.assertEqual(co._aggregate({"batches": []}, False, False, None), "PLANNED")
        self.assertEqual(co._aggregate({"batches": [{"status": "BLOCKED"}]}, False, False, None), "BLOCKED")


class OutcomeRuleTests(unittest.TestCase):
    OK_STATIC = {"path": "ws/x/a.py", "syntax": "ok", "bytes": 10, "lines": 2,
                 "imports": [], "defs": ["f"], "forbidden": [],
                 "stdlib": [], "local": [], "external": [], "missing": []}
    OK_RUN = {"path": "ws/x/test_a.py", "returncode": 0, "stdout": "", "stderr": "",
              "timed_out": False, "sandbox": True, "env_sanitized": True,
              "containment": "windows_job_object", "network_guard": "best_effort_inprocess",
              "filesystem_isolation": "none"}

    def test_static_rule(self):
        self.assertEqual(evaluate("hermes_check_code", {}, dict(self.OK_STATIC)).verdict, "succeeded")
        bad = {**self.OK_STATIC, "forbidden": ["import socket"]}
        self.assertEqual(evaluate("hermes_check_code", {}, bad).verdict, "failed")
        broken = {**self.OK_STATIC, "syntax": "error", "message": "bad"}
        self.assertEqual(evaluate("hermes_check_code", {}, broken).verdict, "failed")
        external = {**self.OK_STATIC, "external": ["pytest"]}
        self.assertEqual(evaluate("hermes_check_code", {}, external).verdict, "failed")
        missing = {**self.OK_STATIC, "missing": ["rechner.mul"]}
        self.assertEqual(evaluate("hermes_check_code", {}, missing).verdict, "failed")
        incomplete = {k: v for k, v in self.OK_STATIC.items() if k != "external"}
        self.assertEqual(evaluate("hermes_check_code", {}, incomplete).verdict, "verification_unknown")

    def test_sandbox_rule_requires_containment(self):
        self.assertEqual(evaluate("hermes_run_code_tests", {}, dict(self.OK_RUN)).verdict, "succeeded")
        self.assertEqual(evaluate("hermes_run_code_tests", {},
                                  {**self.OK_RUN, "containment": "none"}).verdict, "verification_unknown")
        self.assertEqual(evaluate("hermes_run_code_tests", {},
                                  {**self.OK_RUN, "timed_out": True, "returncode": -1}).verdict, "failed")
        self.assertEqual(evaluate("hermes_run_code_tests", {},
                                  {**self.OK_RUN, "sandbox": False}).verdict, "failed")


class RoutingTests(unittest.TestCase):
    def test_routes_code_goal_to_code_order(self):
        from nexus.master import evidence
        sentinel = {"intent": "hermes_work", "status": "PARTIAL", "reply": "code"}
        with mock.patch.object(co, "run", return_value=sentinel) as run_code:
            result = evidence.route_work(SimpleNamespace(), GOAL, [], {})
        self.assertIs(result, sentinel)
        self.assertTrue(run_code.called)

    def test_code_dispatch_takes_precedence_over_file_request(self):
        from nexus.master import evidence
        sentinel = {"intent": "hermes_work", "status": "PARTIAL", "reply": "code"}
        text = "Erstelle rechner.py als Python-Programm mit Tests"
        with mock.patch.object(co, "run", return_value=sentinel) as run_code:
            evidence.route_work(SimpleNamespace(), text, [], {})
        self.assertTrue(run_code.called)


class TwoPhaseLLM:
    """Phase 1 (format=json) returns only file names; later calls return raw content."""

    def __init__(self, files):
        self.files = list(files)
        self.calls = []

    def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        if kwargs.get("format") == "json":
            payload = {"project": "calc", "files": [name for name, _ in self.files]}
            return {"success": True, "reply": json.dumps(payload), "model": "fake"}
        user = messages[-1]["content"]
        name = re.search(r"File to write: (\S+)", user).group(1)
        content = dict(self.files)[name]
        return {"success": True, "reply": "```python\n" + content + "\n```", "model": "fake"}


class TwoPhaseDeriveTests(unittest.TestCase):
    MAIN = "def add(a, b):\n    return a + b\n"
    TEST = "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    README = "# Calc\n"

    def brain(self, files):
        llm = TwoPhaseLLM(files)
        return HermesBrain(SimpleNamespace(llm=llm)), llm

    def test_names_then_contents(self):
        brain, llm = self.brain([("calc.py", self.MAIN), ("test_calc.py", self.TEST),
                                 ("README.md", self.README)])
        d = brain.derive_code_plan("baue einen Taschenrechner")
        self.assertTrue(d["success"], d)
        self.assertEqual(d["project"], "calc")
        contents = {f["path"].rsplit("/", 1)[-1]: f["content"] for f in d["files"]}
        self.assertEqual(contents["calc.py"], self.MAIN.rstrip("\n"))
        self.assertEqual(contents["test_calc.py"], self.TEST.rstrip("\n"))
        self.assertEqual(contents["README.md"], self.README.rstrip("\n"))
        self.assertEqual(llm.calls[0][1].get("format"), "json")
        self.assertEqual(len(llm.calls), 4)

    def test_test_module_sees_main_source(self):
        brain, llm = self.brain([("calc.py", self.MAIN), ("test_calc.py", self.TEST)])
        brain.derive_code_plan("baue einen Taschenrechner")
        test_call = next(m for m, _ in llm.calls
                         if "File to write: test_calc.py" in m[-1]["content"])
        self.assertIn(self.MAIN.rstrip("\n"), test_call[-1]["content"])

    def test_missing_content_fails_closed(self):
        brain, _ = self.brain([("calc.py", self.MAIN), ("test_calc.py", "")])
        self.assertEqual(brain.derive_code_plan("x")["error"], "CODE_PLAN_CONTENT_MISSING")


class FakeBridge:
    """In-memory bridge: deterministic create_file/read_file plus code checks."""

    def __init__(self, exec_enabled=False):
        self.fs = {}
        self.creates = []
        self.calls = []
        self.exec_enabled = exec_enabled

    def __call__(self, args):
        operation, task = args.get("operation"), args.get("task")
        params = args.get("params") or {}
        self.calls.append((operation, task))
        task_id = "NX-20261009-%04d" % (len(self.calls))
        if operation != "submit":
            raise AssertionError(args)
        if task == "read_file":
            path = params["path"]
            if path not in self.fs:
                return {"success": False, "status": "failed", "error_code": "NOT_FOUND",
                        "task_id": task_id, "result": None}
            return {"success": True, "status": "succeeded", "task_readback_verified": True,
                    "execution_instance_verified": True, "task_id": task_id,
                    "result": {"path": path, "content": self.fs[path], "truncated": False}}
        if task == "create_file":
            path = params["path"]
            if path in self.fs:
                return {"success": False, "status": "failed", "error_code": "TASK_FAILED",
                        "task_id": task_id, "result": None}
            self.fs[path] = params["content"]
            self.creates.append(path)
            return {"success": True, "status": "succeeded", "task_readback_verified": True,
                    "execution_instance_verified": True, "task_id": task_id,
                    "result": {"path": path, "bytes": len(params["content"])}}
        if task == "hermes_check_code":
            return {"success": True, "status": "succeeded", "task_id": task_id,
                    "result": {"path": params["path"], "syntax": "ok", "bytes": 1, "lines": 1,
                               "imports": [], "defs": [], "forbidden": [], "stdlib": [],
                               "local": [], "external": [], "missing": []}}
        if task == "hermes_run_code_tests":
            if not self.exec_enabled:
                return {"success": False, "status": "failed", "error_code": "CODE_EXECUTION_DISABLED",
                        "task_id": task_id, "result": None}
            return {"success": True, "status": "succeeded", "task_id": task_id,
                    "result": {"path": params["path"], "returncode": 0, "stdout": "", "stderr": "",
                               "timed_out": False, "sandbox": True, "env_sanitized": True,
                               "containment": "windows_job_object", "network_guard": "best_effort_inprocess",
                               "filesystem_isolation": "none"}}
        raise AssertionError(task)


class ApplyRequirementsTests(unittest.TestCase):
    REQ = {"path": "workspace/projects/hermes/code/p/x.py", "content": "x = 1\n"}

    def _brain(self, bridge):
        patcher = mock.patch("nexus.integrations.hermes_client.invoke", bridge)
        patcher.start()
        self.addCleanup(patcher.stop)
        return HermesBrain(SimpleNamespace(llm=None, project_fs=SimpleNamespace(root=".")))

    def test_writes_and_verifies_byte_exact(self):
        bridge = FakeBridge()
        out = self._brain(bridge).apply_requirements([dict(self.REQ)])
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(bridge.fs[self.REQ["path"]], self.REQ["content"])
        self.assertEqual(out["verification"][0]["equal"], True)
        self.assertEqual(out["verification"][0]["reused"], False)

    def test_resume_reuses_existing_without_rewrite(self):
        bridge = FakeBridge()
        bridge.fs[self.REQ["path"]] = self.REQ["content"]
        out = self._brain(bridge).apply_requirements([dict(self.REQ)])
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(bridge.creates, [])
        self.assertEqual(out["verification"][0]["reused"], True)

    def test_existing_different_content_blocks(self):
        bridge = FakeBridge()
        bridge.fs[self.REQ["path"]] = "other\n"
        out = self._brain(bridge).apply_requirements([dict(self.REQ)])
        self.assertEqual(out["status"], "BLOCKED")
        self.assertEqual(out["error"], "CONTENT_MISMATCH_NO_OVERWRITE")

    def test_rejects_loose_requirement_shape(self):
        bridge = FakeBridge()
        out = self._brain(bridge).apply_requirements([{**self.REQ, "extra": 1}])
        self.assertEqual(out["error"], "EXPLICIT_FILE_REQUIREMENTS_REQUIRED")


class CodeOrderRunTests(unittest.TestCase):
    MAIN = "def add(a, b):\n    return a + b\n"
    TEST = "from calc import add\n\nif __name__ == '__main__':\n    assert add(1, 2) == 3\n    print('ok')\n"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="code-order-")
        self.bridge = FakeBridge()
        self.master = SimpleNamespace(
            project_fs=SimpleNamespace(root=self.tmp),
            llm=TwoPhaseLLM([("calc.py", self.MAIN), ("test_calc.py", self.TEST)]))
        patcher = mock.patch("nexus.integrations.hermes_client.invoke", self.bridge)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_disabled_executor_never_reports_pass(self):
        out = co.run(self.master, GOAL, {})
        self.assertEqual(out["status"], "PARTIAL")
        report_path = "workspace/projects/hermes/code/calc/bericht.json"
        self.assertIn(report_path, self.bridge.fs)
        report = json.loads(self.bridge.fs[report_path])
        self.assertEqual(report["overall_status"], "PARTIAL")
        self.assertEqual(report["run"]["status"], "failed")

    def test_resume_reuses_state_without_rewriting(self):
        co.run(self.master, GOAL, {})
        written = list(self.bridge.creates)
        out = co.run(self.master, GOAL, {})
        self.assertEqual(self.bridge.creates, written)
        self.assertEqual(out["status"], "PARTIAL")


if __name__ == "__main__":
    unittest.main()
