"""Abnahmetests fuer NEXUS VALIDATOR v1 (validator/validator.py).

5 gute Faelle muessen PASS liefern, 7 absichtlich kaputte Faelle FAIL.
Jeder Fall laeuft in einem frischen temporaeren Git-Repo mit Tag baseline-v0.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

VALIDATOR = Path(__file__).resolve().parents[1] / "validator" / "validator.py"

APP_TEMPLATE = """
import http.server


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = b"ok"
            self.send_response(200)
        else:
            body = b"not found"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", {port}), Handler).serve_forever()
"""


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


class ValidatorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nxval_")
        self.base = Path(self._tmp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.git("init")
        self.git("config", "user.email", "validator@test.local")
        self.git("config", "user.name", "Validator Test")
        self.write("tests/test_ok.py", "def test_ok():\n    assert True\n")
        self.write("skills/notes.txt", "baseline\n")
        self.write("config/settings.json", '{"mode": "baseline"}\n')
        self.git("add", "-A")
        self.git("commit", "-m", "baseline")
        self.git("tag", "baseline-v0")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        p = subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
        if p.returncode != 0:
            self.fail(f"git {' '.join(args)} fehlgeschlagen:\n{p.stdout}{p.stderr}")
        return p

    def write(self, rel, content):
        fp = self.repo / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")

    def run_validator(self, task):
        task = dict(task)
        task.setdefault("task_id", self.id().split(".")[-1])
        task.setdefault("base_ref", "baseline-v0")
        task_path = self.base / "task.json"
        report_path = self.base / "report.json"
        task_path.write_text(json.dumps(task), encoding="utf-8")
        p = subprocess.run(
            [sys.executable, str(VALIDATOR), "--task", str(task_path),
             "--repo", str(self.repo), "--report", str(report_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180,
        )
        report = None
        if report_path.is_file():
            report = json.loads(report_path.read_text(encoding="utf-8"))
        return p, report

    def assert_pass(self, p, report):
        self.assertEqual(p.returncode, 0, msg=p.stdout + p.stderr)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["next_state"], "DONE")
        self.assertIn("diff_hash", report)
        results = {s["step"]: s["result"] for s in report["steps"]}
        for step in ("diff", "syntax", "tests"):
            self.assertEqual(results.get(step), "PASS", msg=json.dumps(report, indent=2))
        self.assertNotIn("FAIL", results.values())
        self.assertIn("PASS", results.values())

    def assert_fail(self, p, report, step):
        self.assertEqual(p.returncode, 1, msg=p.stdout + p.stderr)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["next_state"], "IN_PROGRESS")
        self.assertEqual(report["steps"][-1]["step"], step)
        self.assertEqual(report["steps"][-1]["result"], "FAIL")


class GoodCases(ValidatorTestBase):
    def test_good_tracked_file_change(self):
        self.write("skills/notes.txt", "baseline\nergaenzt\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_pass(p, r)
        self.assertEqual(r["changed_files"], ["skills/notes.txt"])
        log = self.repo / "logs" / "validator_log.jsonl"
        self.assertTrue(log.is_file())
        self.assertEqual(len(log.read_text(encoding="utf-8").strip().splitlines()), 1)

    def test_good_new_untracked_file(self):
        self.write("skills/code_review/skill.md", "# Code Review\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_pass(p, r)
        self.assertIn("skills/code_review/skill.md", r["changed_files"])

    def test_good_explicit_test_cmd(self):
        self.write("skills/notes.txt", "baseline\nmehr\n")
        p, r = self.run_validator({
            "allowed_paths": ["skills/*"],
            "test_cmd": [sys.executable, "-m", "pytest", "-q"],
        })
        self.assert_pass(p, r)

    def test_good_json_change(self):
        self.write("config/settings.json", '{"mode": "geaendert"}\n')
        p, r = self.run_validator({"allowed_paths": ["config/*"]})
        self.assert_pass(p, r)

    def test_good_smoke_health_200(self):
        port = free_port()
        self.write("app.py", APP_TEMPLATE.format(port=port))
        p, r = self.run_validator({
            "allowed_paths": ["app.py", "tests/*"],
            "smoke": {
                "start_cmd": [sys.executable, "app.py"],
                "health_url": f"http://127.0.0.1:{port}/health",
                "wait_s": 15,
            },
        })
        self.assert_pass(p, r)
        self.assertEqual(r["steps"][-1]["step"], "smoke")


class BrokenCases(ValidatorTestBase):
    def test_broken_forbidden_path(self):
        self.write("config.json", '{"evil": true}\n')
        p, r = self.run_validator({"allowed_paths": ["config.json"]})
        self.assert_fail(p, r, "diff")
        self.assertIn("Verbotene Pfade", r["steps"][-1]["detail"])

    def test_broken_outside_allowed_paths(self):
        self.write("skills/notes.txt", "baseline\nausserhalb\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"]})
        self.assert_fail(p, r, "diff")
        self.assertIn("ausserhalb", r["steps"][-1]["detail"])

    def test_broken_syntax_error(self):
        self.write("skills/broken.py", "def nope(:\n    pass\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "syntax")

    def test_broken_failing_test(self):
        self.write("tests/test_ok.py", "def test_ok():\n    assert False\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"]})
        self.assert_fail(p, r, "tests")

    def test_broken_empty_diff(self):
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "diff")
        self.assertIn("leerer Diff", r["steps"][-1]["detail"])

    def test_broken_missing_allowed_paths(self):
        self.write("skills/notes.txt", "baseline\nohne erlaubnis\n")
        p, r = self.run_validator({})
        self.assert_fail(p, r, "diff")
        self.assertIn("allowed_paths", r["steps"][-1]["detail"])

    def test_broken_smoke_unreachable(self):
        self.write("skills/notes.txt", "baseline\nsmoke\n")
        p, r = self.run_validator({
            "allowed_paths": ["skills/*"],
            "smoke": {
                "start_cmd": [sys.executable, "-c", "import time; time.sleep(30)"],
                "health_url": f"http://127.0.0.1:{free_port()}/health",
                "wait_s": 2,
            },
        })
        self.assert_fail(p, r, "smoke")

    def test_broken_unknown_base_ref_is_config_error(self):
        self.write("skills/notes.txt", "baseline\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"], "base_ref": "does-not-exist"})
        self.assertEqual(p.returncode, 2)
        self.assertIsNone(r)
        self.assertIn("Konfigurationsfehler", p.stderr)


if __name__ == "__main__":
    unittest.main()
