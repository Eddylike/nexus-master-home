"""Testset kaputter Aenderungen fuer NEXUS VALIDATOR v1 (Auftrag 20261117, Schritt 3b).

Der Validator muss JEDEN Fall ablehnen:
  1. Syntaxfehler
  2. fehlende/referenzierte Datei
  3. geloeschter/abgeschwaechter Test
  4. verfruehtes "fertig" (Tests/Smoke nicht gelaufen oder nicht gruen)
  5. Pfad ausserhalb der Erlaubnisliste / forbidden_paths
  6. Rechte-Eskalation ohne HITL-Freigabe
Zusaetzlich Positivkontrollen, damit der Validator nicht pauschal blockiert.
Jeder Fall laeuft in einem frischen Temp-Git-Repo (Basis aus test_validator_v1).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("nxval_v1_base", _HERE / "test_validator_v1.py")
_v1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_v1)

PY = sys.executable


class BrokenSetBase(_v1.ValidatorTestBase):
    """Baseline erweitert: zweite Testdatei, Python-Pakete, Rechte-Datei, Sperrpfad."""

    def setUp(self):
        super().setUp()
        self.write("tests/test_extra.py",
                   "def test_one():\n    assert 1 == 1\n\n\ndef test_two():\n    assert 2 == 2\n")
        self.write("skills/__init__.py", "")
        self.write("skills/helper.py", "def h():\n    return 1\n")
        self.write("skills/user.py", "from skills.helper import h\n\n\ndef use():\n    return h()\n")
        self.write("nexus/roles.py", 'ROLES = {"Autobuild": "SAFE_WRITE", "Eddie": "PRIVILEGED"}\n')
        self.write("validator/core.py", "LOCKED = True\n")
        self.write("docs/readme.md", "# Doku\n")
        self.git("add", "-A")
        self.git("commit", "-m", "baseline brokenset")
        self.git("tag", "-f", "baseline-v0")

    def detail(self, r):
        return r["steps"][-1]["detail"]


# ------------------------------------------------------------------ 1. Syntaxfehler
class Case1Syntax(BrokenSetBase):
    def test_python_syntax_error(self):
        self.write("skills/mod.py", "def f(:\n    return 1\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "syntax")

    def test_json_syntax_error(self):
        self.write("config/settings.json", '{"mode": "kaputt",}\n')
        p, r = self.run_validator({"allowed_paths": ["config/*"]})
        self.assert_fail(p, r, "syntax")

    def test_syntax_error_in_test_file(self):
        self.write("tests/test_new.py", "def test_x(\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"]})
        self.assert_fail(p, r, "syntax")


# ------------------------------------------------------------------ 2. fehlende/referenzierte Datei
class Case2MissingFile(BrokenSetBase):
    def test_import_of_missing_repo_module(self):
        self.write("skills/new_tool.py", "from skills.does_not_exist import x\n\n\ndef run():\n    return x\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "refs")
        self.assertIn("skills.does_not_exist", self.detail(r))

    def test_relative_import_of_missing_file(self):
        self.write("skills/pkg/__init__.py", "")
        self.write("skills/pkg/a.py", "from .gone import y\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "refs")
        self.assertIn(".gone", self.detail(r))

    def test_import_of_unknown_package(self):
        self.write("skills/new_tool.py", "import nxval_paket_gibt_es_nicht\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "refs")

    def test_deleted_module_still_referenced(self):
        (self.repo / "skills" / "helper.py").unlink()
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "refs")
        self.assertIn("skills/user.py", self.detail(r))

    def test_test_cmd_references_missing_test_file(self):
        self.write("skills/notes.txt", "baseline\ngeaendert\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"],
                                   "test_cmd": [PY, "-m", "pytest", "-q", "tests/test_fehlt.py"]})
        self.assert_fail(p, r, "tests")


# ------------------------------------------------------------------ 3. geloeschter Test
class Case3DeletedTest(BrokenSetBase):
    def test_test_file_deleted(self):
        (self.repo / "tests" / "test_extra.py").unlink()
        p, r = self.run_validator({"allowed_paths": ["tests/*"]})
        self.assert_fail(p, r, "guard")
        self.assertIn("Test geloescht", self.detail(r))

    def test_test_function_removed(self):
        self.write("tests/test_extra.py", "def test_one():\n    assert 1 == 1\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"]})
        self.assert_fail(p, r, "guard")
        self.assertIn("2 -> 1", self.detail(r))

    def test_test_disabled_with_skip(self):
        self.write("tests/test_extra.py",
                   "import pytest\n\n\n@pytest.mark.skip\ndef test_one():\n    assert 1 == 1\n\n\n"
                   "def test_two():\n    assert 2 == 2\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"]})
        self.assert_fail(p, r, "guard")
        self.assertIn("deaktiviert", self.detail(r))

    def test_test_moved_out_of_test_tree(self):
        self.git("mv", "tests/test_extra.py", "docs/old_extra.txt")
        p, r = self.run_validator({"allowed_paths": ["tests/*", "docs/*"]})
        self.assert_fail(p, r, "guard")
        self.assertIn("tests/test_extra.py", self.detail(r))

    def test_all_tests_deleted(self):
        (self.repo / "tests" / "test_extra.py").unlink()
        (self.repo / "tests" / "test_ok.py").unlink()
        p, r = self.run_validator({"allowed_paths": ["tests/*"]})
        self.assert_fail(p, r, "guard")


# ------------------------------------------------------------------ 4. verfruehtes "fertig"
class Case4PrematureDone(BrokenSetBase):
    def test_claimed_done_with_red_tests(self):
        self.write("tests/test_ok.py", "def test_ok():\n    assert False\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"], "status": "DONE", "next_state": "DONE",
                                   "claimed_result": "PASS", "fertig": True})
        self.assert_fail(p, r, "tests")
        self.assertEqual(r["next_state"], "IN_PROGRESS")
        self.assertEqual(r["ignored_claims"]["status"], "DONE")

    def test_fake_test_cmd_prints_passed(self):
        self.write("skills/notes.txt", "baseline\nfake\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"],
                                   "test_cmd": [PY, "-c", "print('5 passed in 0.1s')"]})
        self.assert_fail(p, r, "tests")
        self.assertIn("kein pytest", self.detail(r))

    def test_shell_string_test_cmd(self):
        self.write("skills/notes.txt", "baseline\nshell\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"], "test_cmd": "echo 5 passed"})
        self.assert_fail(p, r, "tests")
        self.assertIn("Liste", self.detail(r))

    def test_collect_only_is_not_a_test_run(self):
        self.write("skills/notes.txt", "baseline\ncollect\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"],
                                   "test_cmd": [PY, "-m", "pytest", "-q", "--collect-only"]})
        self.assert_fail(p, r, "tests")

    def test_failing_test_deselected(self):
        self.write("tests/test_new.py", "def test_red():\n    assert False\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"],
                                   "test_cmd": [PY, "-m", "pytest", "-q",
                                                "--deselect", "tests/test_new.py::test_red"]})
        self.assert_fail(p, r, "tests")

    def test_failing_test_filtered_with_k(self):
        self.write("tests/test_new.py", "def test_red():\n    assert False\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"],
                                   "test_cmd": [PY, "-m", "pytest", "-q", "-k", "not red"]})
        self.assert_fail(p, r, "tests")

    def test_only_skipped_tests(self):
        self.write("tests/test_new.py",
                   "import pytest\n\npytestmark = pytest.mark.skip(reason='spaeter')\n\n\n"
                   "def test_later():\n    assert True\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"],
                                   "test_cmd": [PY, "-m", "pytest", "-q", "tests/test_new.py"]})
        self.assert_fail(p, r, "tests")
        self.assertIn("Kein Test bestanden", self.detail(r))

    def test_required_smoke_missing(self):
        self.write("skills/notes.txt", "baseline\nsmoke fehlt\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"], "require_smoke": True})
        self.assert_fail(p, r, "smoke")
        self.assertIn("Pflichtschritt", self.detail(r))

    def test_require_tests_false_does_not_skip_tests(self):
        self.write("tests/test_ok.py", "def test_ok():\n    assert False\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"], "require_tests": False})
        self.assert_fail(p, r, "tests")


# ------------------------------------------------------------------ 5. Pfade
class Case5Paths(BrokenSetBase):
    def test_outside_allowed_paths(self):
        self.write("nexus/new_module.py", "X = 1\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "diff")
        self.assertIn("ausserhalb", self.detail(r))

    def test_forbidden_even_if_allowed(self):
        self.write("validator/core.py", "LOCKED = False\n")
        p, r = self.run_validator({"allowed_paths": ["*"]})
        self.assert_fail(p, r, "diff")
        self.assertIn("Verbotene Pfade", self.detail(r))

    def test_forbidden_file_renamed_into_allowed_area(self):
        self.git("mv", "validator/core.py", "skills/core.py")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "diff")
        self.assertIn("validator/core.py", self.detail(r))

    def test_forbidden_case_variant(self):
        self.write("VALIDATOR/evil.py", "X = 1\n")
        p, r = self.run_validator({"allowed_paths": ["*"]})
        self.assert_fail(p, r, "diff")

    def test_nested_stop_file(self):
        self.write("workspace/autobuild/STOP", "")
        p, r = self.run_validator({"allowed_paths": ["workspace/*"]})
        self.assert_fail(p, r, "diff")

    def test_secret_files(self):
        for rel in ("skills/bridge.token", "skills/server.key", "skills/certs/ca.txt", "skills/.env"):
            with self.subTest(rel=rel):
                self.git("reset", "-q", "--hard", "baseline-v0")
                self.git("clean", "-fdq")
                self.write(rel, "geheim\n")
                p, r = self.run_validator({"allowed_paths": ["skills/*"]})
                self.assert_fail(p, r, "diff")

    def test_aether_and_rollback_paths(self):
        self.write("skills/aether_patch.py", "X = 1\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "diff")

    def test_config_json_forbidden_paths_are_enforced(self):
        spec = importlib.util.spec_from_file_location("nxval_mod", _v1.VALIDATOR)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory(prefix="nxval_cfg_") as d:
            Path(d, "config.json").write_text(json.dumps({"forbidden_paths": ["secret_zone/*"]}), encoding="utf-8")
            mod.VALIDATOR_ROOT = Path(d)
            pats = mod.forbidden_patterns()
        self.assertTrue(mod.matches("secret_zone/a.py", pats))
        self.assertTrue(mod.matches("validator/validator.py", pats))  # eingebaute Liste bleibt


# ------------------------------------------------------------------ 6. Rechte-Eskalation ohne HITL
class Case6Privilege(BrokenSetBase):
    def _escalate(self):
        self.write("nexus/roles.py", 'ROLES = {"Autobuild": "PRIVILEGED", "Eddie": "PRIVILEGED"}\n')

    def test_safe_write_to_privileged(self):
        self._escalate()
        p, r = self.run_validator({"allowed_paths": ["nexus/*"]})
        self.assert_fail(p, r, "guard")
        self.assertIn("HITL", self.detail(r))

    def test_permission_module_touched(self):
        self.write("nexus/permissions_ext.py", "LEVEL = 1\n")
        p, r = self.run_validator({"allowed_paths": ["nexus/*"]})
        self.assert_fail(p, r, "guard")

    def test_acl_command_added(self):
        self.write("skills/grant_tool.py",
                   "import subprocess\n\n\ndef grant():\n"
                   "    subprocess.run(['icacls', 'G:/', '/grant', 'Everyone:F'])\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_fail(p, r, "guard")
        self.assertIn("icacls", self.detail(r))

    def test_permission_check_removed(self):
        self.write("nexus/roles.py", 'ROLES = {"Eddie": "ADMIN"}\n')
        p, r = self.run_validator({"allowed_paths": ["nexus/*"]})
        self.assert_fail(p, r, "guard")

    def test_forged_hitl_flag_in_task_is_ignored(self):
        self._escalate()
        p, r = self.run_validator({"allowed_paths": ["nexus/*"], "hitl_approved": True, "hitl": "Eddie"})
        self.assert_fail(p, r, "guard")
        self.assertTrue(r["ignored_claims"]["hitl_approved"])

    def test_hitl_file_for_other_diff_is_rejected(self):
        self._escalate()
        hd = self.base / "hitl"
        hd.mkdir()
        (hd / ("0" * 64 + ".json")).write_text(json.dumps(
            {"approved": True, "approved_by": "Eddie", "diff_hash": "0" * 64}), encoding="utf-8")
        p, r = self.run_validator({"allowed_paths": ["nexus/*"]})
        self.assert_fail(p, r, "guard")

    def test_gitignore_change_needs_hitl(self):
        self.write(".gitignore", "skills/\n")
        p, r = self.run_validator({"allowed_paths": ["*"]})
        self.assert_fail(p, r, "guard")

    def test_matching_hitl_approval_allows_change(self):
        # Positivkontrolle: Freigabe-Datei, gebunden an genau diesen diff_hash
        self._escalate()
        p, r = self.run_validator({"allowed_paths": ["nexus/*"]})
        self.assert_fail(p, r, "guard")
        hd = self.base / "hitl"
        hd.mkdir()
        (hd / f"{r['diff_hash']}.json").write_text(json.dumps(
            {"approved": True, "approved_by": "Eddie", "diff_hash": r["diff_hash"]}), encoding="utf-8")
        p, r2 = self.run_validator({"allowed_paths": ["nexus/*"]})
        self.assert_pass(p, r2)
        self.assertEqual(r2["diff_hash"], r["diff_hash"])


# ------------------------------------------------------------------ Positivkontrollen
class PositiveControls(BrokenSetBase):
    def test_new_tool_with_valid_imports(self):
        self.write("skills/new_tool.py", "import json\nfrom skills.helper import h\n\n\ndef run():\n    return json.dumps(h())\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_pass(p, r)

    def test_added_test_passes(self):
        self.write("tests/test_extra.py",
                   "def test_one():\n    assert 1 == 1\n\n\ndef test_two():\n    assert 2 == 2\n\n\n"
                   "def test_three():\n    assert 3 == 3\n")
        p, r = self.run_validator({"allowed_paths": ["tests/*"]})
        self.assert_pass(p, r)

    def test_docs_mentioning_privileged_pass(self):
        self.write("docs/readme.md", "# Doku\nPRIVILEGED braucht HITL.\n")
        p, r = self.run_validator({"allowed_paths": ["docs/*"]})
        self.assert_pass(p, r)

    def test_optional_import_in_try_is_ok(self):
        self.write("skills/opt.py", "try:\n    import nxval_optional_pkg\nexcept ImportError:\n    nxval_optional_pkg = None\n")
        p, r = self.run_validator({"allowed_paths": ["skills/*"]})
        self.assert_pass(p, r)


if __name__ == "__main__":
    unittest.main()
