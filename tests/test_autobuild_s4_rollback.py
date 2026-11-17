"""Auftrag 20261117 Schritt 4c: provozierter Rollback-Test.

Nur in einem Temp-Git-Repo (NIE im echten Arbeitsbaum): kaputte Aenderung einspielen, der echte
Validator (validator/validator.py) lehnt ab, Rollback auf baseline-v0 bzw. den letzten gruenen Stand,
danach ist der Stand byte-identisch (HEAD, git status, SHA-256 aller Dateien).
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "validator" / "validator.py"


def _env(repo):
    # GIT_CEILING_DIRECTORIES: git darf nie zum echten nexus-ai-Repo hochwandern
    return dict(os.environ, GIT_CEILING_DIRECTORIES=str(Path(repo).resolve().parent))


def git(repo, *args):
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=_env(repo))
    assert p.returncode == 0, f"git {args}: {p.stdout}{p.stderr}"
    return p.stdout.strip()


def _assert_temp_repo(repo):
    top = Path(git(repo, "rev-parse", "--show-toplevel")).resolve()
    assert top == Path(repo).resolve(), top
    assert top != ROOT.resolve()


def rollback_to(repo, ref):
    """Rollback im Temp-Repo: Arbeitsbaum und Index auf ref, neue Dateien entfernen."""
    _assert_temp_repo(repo)
    git(repo, "reset", "-q", "--hard", ref)
    git(repo, "clean", "-fdq")


def snapshot(repo):
    files = {}
    for fp in sorted(Path(repo).rglob("*")):
        rel = fp.relative_to(repo)
        if not fp.is_file() or ".git" in rel.parts or "__pycache__" in rel.parts:
            continue
        files[rel.as_posix()] = hashlib.sha256(fp.read_bytes()).hexdigest()
    return {"head": git(repo, "rev-parse", "HEAD"),
            "status": git(repo, "status", "--porcelain", "--untracked-files=all"),
            "files": files}


def write(repo, rel, text):
    fp = Path(repo) / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_bytes(text.encode("utf-8"))


def run_validator(repo, work, task):
    task = dict(task, task_id="S4C-ROLLBACK")
    tp, rp = work / "task.json", work / "report.json"
    tp.write_text(json.dumps(task), encoding="utf-8")
    rp.unlink(missing_ok=True)
    env = dict(_env(repo), NEXUS_VALIDATOR_HITL_DIR=str(work / "hitl"))
    p = subprocess.run([sys.executable, str(VALIDATOR), "--task", str(tp), "--repo", str(repo), "--report", str(rp)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, env=env)
    return p.returncode, (json.loads(rp.read_text(encoding="utf-8")) if rp.is_file() else None)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    _assert_temp_repo(r)
    git(r, "config", "user.email", "rollback@test.local")
    git(r, "config", "user.name", "Rollback Test")
    git(r, "config", "core.autocrlf", "false")
    write(r, "skills/__init__.py", "")
    write(r, "skills/mod.py", "def f():\n    return 1\n")
    write(r, "tests/test_mod.py", "from skills.mod import f\n\n\ndef test_f():\n    assert f() == 1\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "baseline")
    git(r, "tag", "baseline-v0")
    return r


def _make_last_green(repo, work):
    write(repo, "skills/mod.py", "def f():\n    return 1\n\n\ndef g():\n    return 2\n")
    write(repo, "tests/test_mod.py", "from skills.mod import f, g\n\n\ndef test_f():\n    assert f() == 1\n\n\n"
                                     "def test_g():\n    assert g() == 2\n")
    rc, rep = run_validator(repo, work, {"allowed_paths": ["skills/*", "tests/*"], "base_ref": "baseline-v0"})
    assert rc == 0 and rep["status"] == "PASS", rep
    git(repo, "add", "skills", "tests")
    git(repo, "commit", "-q", "-m", "gruen: g()")
    git(repo, "tag", "last-green")
    git(repo, "clean", "-fdq")  # Validator-Log (untracked) entfernen


def _break(repo):
    write(repo, "skills/mod.py", "def f(:\n    return 1\n")      # Syntaxfehler
    (Path(repo) / "tests" / "test_mod.py").unlink()             # Test geloescht
    write(repo, "skills/evil.py", "import subprocess\nsubprocess.run(['icacls', 'G:/', '/grant', 'Everyone:F'])\n")


@pytest.mark.parametrize("ref", ["baseline-v0", "last-green"])
def test_broken_change_rejected_and_rolled_back(repo, tmp_path, ref):
    work = tmp_path / "work"
    work.mkdir()
    if ref == "last-green":
        _make_last_green(repo, work)
    before = snapshot(repo)
    assert before["status"] == ""

    _break(repo)
    rc, rep = run_validator(repo, work, {"allowed_paths": ["skills/*", "tests/*"], "base_ref": ref})
    assert rc == 1 and rep["status"] == "FAIL" and rep["next_state"] == "IN_PROGRESS", rep
    assert snapshot(repo) != before

    rollback_to(repo, ref)
    after = snapshot(repo)
    assert after == before                       # HEAD, Status und alle Datei-Hashes identisch
    assert after["head"] == git(repo, "rev-parse", ref + "^{commit}")

    # Nach dem Rollback kein Rest-Diff mehr ...
    rc2, rep2 = run_validator(repo, work, {"allowed_paths": ["skills/*", "tests/*"], "base_ref": ref})
    assert rc2 == 1 and "leerer Diff" in rep2["steps"][-1]["detail"]
    # ... und die Tests des zurueckgesetzten Standes sind gruen.
    ini = work / "pytest.ini"
    ini.write_text("[pytest]\n", encoding="utf-8")
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", str(ini),
                        "--rootdir", str(repo), "--confcutdir", str(repo), "--basetemp", str(work / "bt")],
                       cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert p.returncode == 0 and " passed" in p.stdout, p.stdout + p.stderr


def test_rollback_refuses_real_worktree():
    with pytest.raises(AssertionError):
        _assert_temp_repo(ROOT)
