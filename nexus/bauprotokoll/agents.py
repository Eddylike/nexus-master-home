"""Phase 5: Getrennte Agenten-Rollen + Team-Pipeline.

Researcher -> Coder -> Tester -> Reviewer -> Validator.
Limits: max. 3 Versuche, Zeitbudget pro Task. Abbrechen mit Bericht an Eddy.
"""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from nexus.bauprotokoll.config import load_config

FORBIDDEN = ["validator/*", "*aether*", "config.json", "*whitelist*", "rollback*",
             "*logging_core*", ".git/*", ".env", "*.env", "stop", "STOP"]
SHELL_META = set(";&|<>`$\n\r")
SECRET_HINTS = re.compile(r"(api[_-]?key|secret|password|passwd|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}", re.I)


class PolicyError(RuntimeError):
    pass


def _norm(path: str) -> str:
    p = str(path).replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lower()


def _matches(path: str, patterns) -> bool:
    return any(fnmatch.fnmatch(_norm(path), _norm(p)) for p in patterns)


class Hermes:
    """Fuehrt ausschliesslich Whitelist-Befehle aus. Keine Shell, keine Metazeichen."""

    def __init__(self, whitelist, allowed_modules=None, cwd=None, timeout=120):
        self.whitelist = {w.lower().removesuffix(".exe") for w in whitelist}
        self.allowed_modules = {m.lower() for m in (allowed_modules or [])}
        self.cwd = Path(cwd) if cwd else Path.cwd()
        self.timeout = timeout

    def check(self, argv) -> str:
        if not isinstance(argv, (list, tuple)) or not argv:
            raise PolicyError("Befehl muss eine nicht-leere Argumentliste sein (keine Shell-Zeile).")
        exe = Path(str(argv[0])).name.lower().removesuffix(".exe")
        if exe not in self.whitelist:
            raise PolicyError(f"Nicht in der Whitelist: {exe}")
        for arg in argv:
            if any(ch in str(arg) for ch in SHELL_META):
                raise PolicyError(f"Metazeichen im Argument: {arg!r}")
        if exe in ("python", "python3", "py"):
            if len(argv) > 1 and argv[1] in ("-c",):
                raise PolicyError("python -c ist nicht erlaubt.")
            if len(argv) > 2 and argv[1] == "-m" and argv[2].lower() not in self.allowed_modules:
                raise PolicyError(f"Python-Modul nicht erlaubt: {argv[2]}")
        if exe == "node" and any(a in ("-e", "-p", "--eval", "--print") for a in argv[1:]):
            raise PolicyError("node -e/-p ist nicht erlaubt.")
        return exe

    def run(self, argv, timeout=None) -> dict:
        self.check(argv)
        try:
            p = subprocess.run(
                [str(a) for a in argv], cwd=str(self.cwd), capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=timeout or self.timeout,
            )
            return {"ok": p.returncode == 0, "rc": p.returncode,
                    "out": (p.stdout + p.stderr)[-4000:], "argv": list(argv)}
        except subprocess.TimeoutExpired:
            return {"ok": False, "rc": 124, "out": f"TIMEOUT nach {timeout or self.timeout}s",
                    "argv": list(argv)}


class Coder:
    """Schreibt nur innerhalb des Repos und nur in erlaubte Pfade."""

    def __init__(self, root, forbidden=None):
        self.root = Path(root).resolve()
        self.forbidden = forbidden or FORBIDDEN

    def _target(self, path: str, allowed_paths) -> Path:
        rel = str(path).replace("\\", "/")
        if rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or ".." in Path(rel).parts:
            raise PolicyError(f"Pfad ausserhalb des Repos: {path}")
        if _matches(rel, self.forbidden):
            raise PolicyError(f"Verbotener Pfad: {path}")
        if not _matches(rel, allowed_paths or []):
            raise PolicyError(f"Pfad nicht in der Erlaubnisliste: {path}")
        target = (self.root / rel).resolve()
        if self.root not in target.parents and target != self.root:
            raise PolicyError(f"Pfad verlaesst das Repo: {path}")
        return target

    def write(self, path: str, content: str, allowed_paths) -> dict:
        target = self._target(path, allowed_paths)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": str(target.relative_to(self.root)).replace("\\", "/"),
                "bytes": len(content.encode("utf-8"))}

    def apply(self, files: dict, allowed_paths) -> list[dict]:
        if not isinstance(files, dict) or not files:
            raise PolicyError("Keine Dateien zum Schreiben.")
        written = []
        for path, content in files.items():
            written.append(self.write(path, content, allowed_paths))
        return written


class Researcher:
    """Liest/sucht nur, schreibt nie. Search-Funktion wird injiziert."""

    def __init__(self, search=None):
        self.search = search

    def research(self, topic: str, sources=None) -> dict:
        found = []
        if callable(self.search):
            found = list(self.search(topic) or [])
        elif sources:
            found = list(sources)
        return {
            "thema": topic,
            "quellen": found,
            "fazit": f"{len(found)} Quelle(n) gefunden." if found else "Keine Quellen verfuegbar.",
            "offene_fragen": [] if found else ["Quellen pruefen"],
        }


class Tester:
    __test__ = False  # keine pytest-Testklasse

    def __init__(self, hermes: Hermes, coder: Coder):
        self.hermes = hermes
        self.coder = coder

    def write_test(self, path: str, content: str, allowed_paths) -> dict:
        return self.coder.write(path, content, allowed_paths)

    def run(self, target: str = "tests", python_exe: str | None = None) -> dict:
        exe = python_exe or sys.executable
        return self.hermes.run([exe, "-m", "pytest", "-q", target])


class Reviewer:
    """Zusaetzliche Pruefung. Nie allein entscheidend (Validator entscheidet)."""

    def review(self, task: str, files: dict) -> dict:
        befunde, risiken = [], []
        for path, content in (files or {}).items():
            text = content if isinstance(content, str) else str(content)
            for i, line in enumerate(text.splitlines(), 1):
                if "TODO" in line or "FIXME" in line:
                    befunde.append(f"{path}:{i} offener TODO/FIXME")
                if SECRET_HINTS.search(line):
                    risiken.append(f"{path}:{i} moegliches Secret im Code")
                if len(line) > 200:
                    befunde.append(f"{path}:{i} Zeile > 200 Zeichen")
            if path.endswith(".py") and "def test_" not in text and "test_" not in path:
                pass
        return {
            "befund": befunde,
            "risiken": risiken,
            "empfehlung": "blockieren" if risiken else "freigeben",
        }


def run_validator(task: dict, repo, python_exe: str | None = None) -> dict:
    """Ruft den echten Validator als Subprozess auf und liefert den JSON-Report."""
    root = Path(repo).resolve()
    state = root / "state" / "bauprotokoll"
    state.mkdir(parents=True, exist_ok=True)
    task_file = state / f"task_{task.get('task_id', 'unknown')}.json"
    task_file.write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")
    exe = python_exe or sys.executable
    p = subprocess.run(
        [exe, "validator/validator.py", "--task", str(task_file), "--repo", str(root)],
        cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=900,
    )
    try:
        return json.loads(p.stdout)
    except ValueError:
        return {
            "task_id": task.get("task_id", "unknown"), "status": "FAIL",
            "steps": [{"step": "validator", "result": "FAIL",
                       "detail": (p.stdout + p.stderr)[-1500:] or "kein Report"}],
        }


class TeamPipeline:
    def __init__(self, coder: Coder, tester: Tester, reviewer: Reviewer, validator,
                 memory=None, max_attempts=3, task_timeout_s=900):
        self.coder = coder
        self.tester = tester
        self.reviewer = reviewer
        self.validator = validator
        self.memory = memory
        self.max_attempts = max(1, int(max_attempts))
        self.task_timeout_s = float(task_timeout_s)

    def run(self, plan: dict, implement=None) -> dict:
        started = time.monotonic()
        allowed = plan.get("allowed_paths") or []
        attempts = []
        last_report = None
        for attempt in range(1, self.max_attempts + 1):
            if time.monotonic() - started > self.task_timeout_s:
                return self._abort(plan, attempts, last_report, "Zeitbudget ueberschritten")
            try:
                files = implement(plan, attempt) if callable(implement) else plan.get("files")
                if not files:
                    raise PolicyError("Keine Implementierung geliefert.")
                written = self.coder.apply(files, allowed)
            except PolicyError as e:
                report = self._fail(plan, "coder", str(e))
                attempts.append({"attempt": attempt, "written": [], "report": report})
                last_report = report
                self._remember(report)
                continue
            review = self.reviewer.review(plan.get("goal", {}).get("ziel", ""), files)
            report = self.validator(plan, files)
            attempts.append({"attempt": attempt, "written": written, "review": review,
                             "report": report})
            last_report = report
            if report.get("status") == "PASS":
                return {"task_id": plan.get("task_id"), "status": "PASS",
                        "attempts": attempts, "report": report,
                        "steps": report.get("steps", [])}
            self._remember(report)
        return self._abort(plan, attempts, last_report, "Max. Versuche erreicht")

    def _fail(self, plan, step, detail):
        return {"task_id": plan.get("task_id"), "status": "FAIL",
                "steps": [{"step": step, "result": "FAIL", "detail": detail}]}

    def _remember(self, report):
        if self.memory is not None and report.get("status") in ("PASS", "FAIL"):
            try:
                self.memory.record_validation(report)
            except Exception:
                pass

    def _abort(self, plan, attempts, last_report, reason):
        report = last_report or self._fail(plan, "pipeline", reason)
        report["status"] = "FAIL"
        return {"task_id": plan.get("task_id"), "status": "FAIL", "attempts": attempts,
                "report": report, "steps": report.get("steps", []),
                "abgebrochen": True, "eskalation": f"Bericht an Eddy: {reason}"}


def extract_files(text: str) -> dict:
    """Liest {"files": {pfad: inhalt}} aus einer LLM-Antwort (auch mit Code-Fence)."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise PolicyError("Antwort enthaelt kein JSON-Objekt.")
        try:
            data = json.loads(raw[start:end + 1])
        except ValueError as e:
            raise PolicyError(f"JSON nicht lesbar: {e}") from e
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise PolicyError("JSON braucht das Feld 'files'.")
    clean = {}
    for path, content in files.items():
        if not isinstance(path, str) or not isinstance(content, str):
            raise PolicyError("files muss {pfad: text} sein.")
        clean[path] = content
    return clean


def llm_implementer(models, skill_hint: str = ""):
    """Erzeugt einen implement-Callback fuer die Pipeline (LLM schreibt Dateien)."""
    def implement(plan, attempt):
        steps = plan.get("steps") or []
        allowed = plan.get("allowed_paths") or []
        prompt = (
            "Aufgabe: " + plan.get("goal", {}).get("ziel", "") + "\n"
            "Erlaubte Pfade: " + ", ".join(allowed) + "\n"
            "Teilschritte: " + "; ".join(s.get("text", "") for s in steps) + "\n"
            + (f"Skill: {skill_hint}\n" if skill_hint else "")
            + "Antworte NUR mit JSON: {\"files\": {\"pfad/datei.py\": \"inhalt\"}}. "
            "Keine Erklaerung, keine Markdown-Fences."
        )
        text = models.generate("coder", prompt)
        return extract_files(text)
    return implement


def build_default(root, config=None):
    """Erzeugt Hermes/Coder/Tester/Reviewer mit Werten aus config.json."""
    cfg = config or load_config()
    whitelist = cfg.get("whitelist", ["python", "pytest", "git"])
    modules = cfg.get("whitelist_modules", ["pytest", "unittest"])
    forbidden = cfg.get("forbidden_paths", FORBIDDEN)
    hermes = Hermes(whitelist, modules, cwd=root)
    coder = Coder(root, forbidden=forbidden)
    return {
        "hermes": hermes,
        "coder": coder,
        "tester": Tester(hermes, coder),
        "reviewer": Reviewer(),
        "researcher": Researcher(),
    }
