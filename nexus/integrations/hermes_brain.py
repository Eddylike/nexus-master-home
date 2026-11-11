"""HermesBrain — NEXUS Master Home interne Denk-/Ausführungs-Komponente.

Hermes ist KEINE zweite Hauptsteuerung. Die Auftragssteuerung bleibt
vollständig bei NEXUS Master Home. HermesBrain führt innerhalb dieser
Steuerung den Zyklus  Denken → Planen → Ausführen → Prüfen → Korrigieren
aus und verwendet dafür die gemeinsamen NEXUS-Werkzeuge (Master-Tools).

Langfristziel: NEXUS Master Home führt Aufgaben eigenständig aus, ohne
einen separat betriebenen Hermes-Agenten vorauszusetzen. Ein externer
Hermes-Agent darf nur dann delegiert werden, wenn er explizit über
NEXUS_HERMES_DELEGATE=1 freigeschaltet UND nachweislich verfügbar ist;
andernfalls wird intern ausgeführt. Delegate-Ergebnisse gelten nur über
reale, zurückgelesene Ausführungsnachweise, nie über behaupteten Text.

Begrenzungen (Ressourcenlimits):
  - max. Runden und Schritte sind hart begrenzt
  - kein unendlicher Korrektur-Loop
  - jede Runde endet entweder mit Nachweis, Blockade oder Grenze
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.master.tools import format_tool_result, parse_tool_calls, run_tools

DELEGATE_ENV = "NEXUS_HERMES_DELEGATE"
PYTHON_ENV = "NEXUS_HERMES_PYTHON"
EVIDENCE_KEY_ENV = "NEXUS_EVIDENCE_KEY"
RUNNER = "workspace/scripts/hermes_core_runner.py"
HERMES_INSTALL = Path(r"G:\eddyzeug\Projekte\hermes\hermes-agent")
MAX_ROUNDS = 4
MAX_STEPS = 8
MAX_PLAN_FILES = 8
MAX_WAIT_DELEGATE_SECONDS = 300
SCHEMA = "nexus.hermes-brain.v1"

_REDACT = [
    (re.compile(r"(?i)\bbearer\s+[^\s\"']+"), "Bearer REDACTED"),
    (re.compile(r"(?i)\b(api[_-]?key|token)\s*[=:]\s*[\"']?[^\s\"']+"), r"\1=REDACTED"),
]

_PYTHON_CACHE: dict[str, str] = {}


def resolve_hermes_python() -> str | None:
    """Einen einzigen, echten Hermes-Interpreter auflösen.

    Reihenfolge: NEXUS_HERMES_PYTHON → pm.environments.project_python →
    installierte venv aus facts.json. Kein zweiter, konkurrierender
    Laufzeitpfad; kein Rückschritt auf ein System-Python ohne Hermes-Abhängigkeiten.
    """
    override = str(os.environ.get(PYTHON_ENV) or "").strip()
    if override:
        candidate = Path(override)
        return str(candidate) if candidate.is_file() else None

    cached = _PYTHON_CACHE.get("python")
    if cached:
        return cached

    added = False
    if str(HERMES_INSTALL) not in sys.path:
        sys.path.insert(0, str(HERMES_INSTALL))
        added = True
    try:
        try:
            from pm.environments import project_python

            found = project_python(HERMES_INSTALL)
            if found and Path(found).is_file():
                _PYTHON_CACHE["python"] = str(found)
                return str(found)
        except Exception:
            pass

        for facts in sorted((HERMES_INSTALL.parent / "installs").glob("*/facts.json")):
            try:
                env = json.loads(facts.read_text(encoding="utf-8-sig"))[
                    "packages"
                ]["venv"]["environment"]
            except Exception:
                continue
            python = Path(env) / "Scripts" / "python.exe"
            if python.is_file():
                _PYTHON_CACHE["python"] = str(python)
                return str(python)
    finally:
        if added:
            try:
                sys.path.remove(str(HERMES_INSTALL))
            except ValueError:
                pass
    return None


def _redact(text: str) -> str:
    for pattern, replacement in _REDACT:
        text = pattern.sub(replacement, text)
    return text


def _json_object(text: str) -> dict | None:
    """Ein einziges JSON-Objekt aus der Modellantwort; kein Suchen bis etwas passt."""
    candidate = (text or "").strip()
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", candidate)
    if fence:
        candidate = fence.group(1).strip()
    chunks = [candidate]
    start, end = candidate.find("{"), candidate.rfind("}")
    if start >= 0 and end > start:
        chunks.append(candidate[start:end + 1])
    for chunk in chunks:
        try:
            value = json.loads(chunk)
        except ValueError:
            continue
        return value if isinstance(value, dict) else None
    return None


_CODE_EXTENSIONS = (
    "py pyw pyi js mjs cjs jsx ts tsx java kt kts scala c cc cpp cxx h hh hpp "
    "cs go rs rb php swift dart sh bash zsh fish bat cmd ps1 psm1 sql html htm "
    "css scss sass less xml yaml yml toml ini cfg conf dll exe apk ipynb vue svelte lua pl r"
).split()
_UNSUPPORTED_FILE = re.compile(
    r"(?<![\w./\\-])([\w][\w./\\-]*\.(?:" + "|".join(re.escape(e) for e in _CODE_EXTENSIONS) + r"))\b",
    re.I,
)
_UNSUPPORTED_SUBDIR = re.compile(r"workspace/projects/hermes/[A-Za-z0-9_-]+/", re.I)
_UNSUPPORTED_EXECUTION = re.compile(
    r"codeentwicklung|"
    r"python[-\s]?(?:ausf(?:ü|ue)hrung|rechner|programm|quellcode|skript|script|code|datei|interpret\w*)|"
    r"(?:programm|skript|script|code|test(?:s)?|rechner)\s+(?:ausf(?:ü|ue)hr\w*|start\w*)|"
    r"ausf(?:ü|ue)hr\w*\s+(?:programm|skript|script|code|test(?:s)?|rechner)|"
    r"\bpytest\b|\bunittest\b|\bkompilier\w*|\bcompil\w*|\bbuild\b",
    re.I,
)
_NEGATED_EXEC = re.compile(
    r"\b(?:keine?|nicht|ohne|verboten|untersagt)\s+(?:[\w-]+\s+(?:oder|und)\s+)?\s*$",
    re.I,
)


def unsupported_actions(text: str) -> list[str]:
    """Konkrete, nicht unterstützte Aktionen eines Projektziels (vor der Planung).

    Der freigegebene Arbeitsbereich erlaubt nur neue Textdateien (.txt/.md/.json) direkt
    unter workspace/projects/hermes/. Code-Dateien, Unterverzeichnisse und Programm- oder
    Testausführung werden hier deterministisch benannt, statt als generischer Planfehler.
    """
    norm = str(text or "").replace("\\", "/")
    reasons: list[str] = []
    for match in dict.fromkeys(_UNSUPPORTED_FILE.findall(norm)):
        reasons.append("Programm-/Codedatei '" + match + "' (erlaubt sind nur .txt/.md/.json)")
    subdir = _UNSUPPORTED_SUBDIR.search(norm)
    if subdir:
        reasons.append("Unterverzeichnis '" + subdir.group(0)
                        + "' (Dateien nur direkt unter workspace/projects/hermes/)")
    exec_match = _UNSUPPORTED_EXECUTION.search(norm)
    if exec_match is not None and not _NEGATED_EXEC.search(norm[:exec_match.start()]):
        reasons.append("Programm-, Code- oder Testausführung (nicht freigegeben)")
    return reasons[:4]


def _strip_code_fence(text: str) -> str:
    """Genau einen umgebenden Markdown-Zaun entfernen; sonst Inhalt unverändert lassen."""
    value = (text or "").strip()
    fence = re.match(r"^```[A-Za-z0-9_+\-]*\s*\n?([\s\S]*?)\n?```$", value)
    if fence:
        return fence.group(1).strip("\n")
    return value


def _json_failure(text: str) -> str:
    """Unparsebare Planantwort einordnen: Prosa, unvollständig oder abgeschnitten."""
    candidate = (text or "").strip()
    fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", candidate)
    if fence:
        candidate = fence.group(1).strip()
    if not (candidate.startswith("{") or candidate.startswith("[")):
        return "PLAN_NOT_JSON"
    if candidate.count("{") > candidate.count("}"):
        return "PLAN_TRUNCATED"
    return "PLAN_INCOMPLETE"


def _utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dup_key(call: dict) -> str:
    args = call.get("args") or {}
    try:
        sig = json.dumps(args, sort_keys=True, ensure_ascii=False)[:2000]
    except Exception:
        sig = repr(args)[:2000]
    return f"{str(call.get('name') or '').lower()}|{sig}"



LESSONS_FILE = Path(__file__).resolve().parents[2] / "workspace" / "fusion" / "HERMES_LESSONS.md"
_LESSONS_CACHE: dict = {"mtime": None, "text": ""}


def hermes_lessons_block(max_chars: int = 4000) -> str:
    """Durable Hermes lessons from workspace/fusion/HERMES_LESSONS.md (cached by mtime)."""
    try:
        if not LESSONS_FILE.is_file():
            return ""
        mtime = LESSONS_FILE.stat().st_mtime
        if _LESSONS_CACHE["mtime"] == mtime and _LESSONS_CACHE["text"]:
            return _LESSONS_CACHE["text"]
        raw = LESSONS_FILE.read_text(encoding="utf-8-sig").strip()
        if not raw:
            return ""
        block = "HERMES LESSONS (durable, mandatory):\n" + raw
        if len(block) > max_chars:
            block = block[: max_chars - 20].rstrip() + "\n...(truncated)"
        _LESSONS_CACHE["mtime"] = mtime
        _LESSONS_CACHE["text"] = block
        return block
    except OSError:
        return ""


class HermesBrain:
    """Einmalige, begrenzte Denk-Plan-Ausführungs-Schleife unter NEXUS-Kontrolle."""

    def __init__(self, master: Any, llm: Any = None) -> None:
        self.master = master
        self.llm = llm if llm is not None else getattr(master, "llm", None)
        self.sync_central = True

    # ------------------------------------------------------------------
    # Zentrale Auftragssteuerung (NEXUS bleibt die EINZIGE Steuerung)
    # ------------------------------------------------------------------
    def _central_tools(self, trace: list) -> list:
        summary = getattr(self.master, "_tool_state_summary", None)
        if callable(summary):
            try:
                return list(summary(trace) or [])
            except Exception:
                pass
        out = []
        for item in trace or []:
            if not isinstance(item, dict):
                continue
            out.append({
                "name": str(item.get("name") or ""),
                "ok": self._tool_succeeded(item.get("result")),
                "args": item.get("args") if isinstance(item.get("args"), dict) else {},
            })
        return out

    def _central_write(
        self,
        state: dict,
        trace: list,
        *,
        checkpoint: str,
        resumed: bool = False,
        next_step: str | None = None,
    ) -> None:
        write = getattr(self.master, "_write_task_state", None)
        if not callable(write) or not self.sync_central:
            return
        try:
            write(
                status=str(state.get("status") or "ACTIVE"),
                message=str(state.get("task") or ""),
                intent="hermes_brain",
                tools=self._central_tools(trace),
                error=state.get("error"),
                result=state.get("result"),
                run_id=state.get("run_id"),
                checkpoint=checkpoint,
                next_step=next_step,
                resumed=resumed,
            )
        except Exception:
            # Die zentrale Steuerung darf den Lauf nie zerstören.
            pass

    def _central_restore(self, task_text: str) -> list[dict]:
        """Bereits real ausgeführte Schritte desselben Auftrags übernehmen."""
        read = getattr(self.master, "_read_task_state", None)
        if not callable(read) or not self.sync_central:
            return []
        try:
            saved = read()
        except Exception:
            return []
        if not isinstance(saved, dict):
            return []
        if str(saved.get("task") or "").strip() != str(task_text or "").strip():
            return []
        if str(saved.get("status") or "").upper() not in ("ACTIVE", "PARTIAL", "BLOCKED"):
            return []
        restored: list[dict] = []
        for item in saved.get("tools") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            if item.get("error"):
                result = {"success": False, "error": str(item.get("error"))}
            else:
                result = {"success": bool(item.get("ok"))}
            if not self._tool_succeeded(result):
                continue
            restored.append({
                "name": name,
                "args": args,
                "result": result,
                "restored": True,
                "run_id": str(saved.get("run_id") or ""),
            })
        return restored

    # ------------------------------------------------------------------
    # Zustand & Verfügbarkeit
    # ------------------------------------------------------------------
    def _state_path(self) -> Path:
        base = Path(self.master.project_fs.root)
        state_dir = base / "workspace" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir / "hermes_brain.json"

    def _persist(self, payload: dict) -> None:
        try:
            path = self._state_path()
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except Exception:
            pass

    def delegate_enabled(self) -> bool:
        return str(os.environ.get(DELEGATE_ENV) or "0").strip() in ("1", "true", "yes")

    def external_hermes_available(self) -> dict:
        """Prüft nur Vorhandensein; keine Credentials, kein Start."""
        missing = []
        if not HERMES_INSTALL.is_dir():
            missing.append("hermes_install")
        if not (Path(self.master.project_fs.root) / RUNNER).is_file():
            missing.append("runner_script")
        if not resolve_hermes_python():
            missing.append("hermes_python")
        if missing:
            return {"available": False, "missing": missing}
        return {"available": True, "missing": []}

    def converse(self, messages: list[dict], keep_alive=None) -> dict:
        """One bounded local Hermes turn; text is planning, never execution evidence.
        keep_alive: optional override (voice call keeps the model warm only while the call is active)."""
        if keep_alive is None:
            return self.llm.chat(messages, temperature=0.1, num_predict=180)
        return self.llm.chat(messages, temperature=0.1, num_predict=180, keep_alive=keep_alive)

    def execute_collaboration(self, goal: str, session: dict) -> dict:
        from nexus.master.evidence import route_work, blocked
        result = route_work(self.master, goal, session.get("messages", []), session)
        return result if result is not None else blocked("NO_AUTHORIZED_CONTRACT", "Dieser Auftrag hat keinen vorhandenen freigegebenen Werkzeugvertrag.")

    def availability(self) -> dict:
        ext = self.external_hermes_available()
        return {
            "schema": SCHEMA,
            "internal": True,
            "execution_contract": "hermes.structured.v1",
            "requires": "requirements: [{path, content}] (create, read, exact comparison)",
            "delegate_enabled": self.delegate_enabled(),
            "external_available": ext,
            "mode": (
                "external_if_available"
                if self.delegate_enabled() and ext["available"]
                else "internal"
            ),
            "limits": {
                "max_rounds": MAX_ROUNDS,
                "max_steps": MAX_STEPS,
                "max_wait_delegate_seconds": MAX_WAIT_DELEGATE_SECONDS,
            },
        }

    # ------------------------------------------------------------------
    # Planung
    # ------------------------------------------------------------------
    def plan(self, task: str) -> list[dict]:
        """Strukturierter, begrenzter Plan. Reihenfolge und Ziele bleiben sichtbar."""
        text = (task or "").strip()
        steps = [
            {"id": 1, "phase": "denken", "action": "verstehen",
             "detail": "Auftrag und Zielbedingungen erfassen"},
            {"id": 2, "phase": "planen", "action": "plan_schreiben",
             "detail": "Schritte + erforderliche Werkzeuge festlegen"},
            {"id": 3, "phase": "ausfuehren", "action": "tools_ausfuehren",
             "detail": "Reale Werkzeugausführung mit Nachweisen"},
            {"id": 4, "phase": "pruefen", "action": "nachweise_pruefen",
             "detail": "Erfolgreiche Werkzeugnachweise gegen den Auftrag prüfen"},
            {"id": 5, "phase": "korrigieren", "action": "korrektur",
             "detail": "Offene Punkte beheben oder begrenzt abbrechen"},
        ]
        return steps

    def derive_plan(self, task: str, *, required_paths: list | None = None,
                    max_files: int = MAX_PLAN_FILES, context: list | None = None) -> dict:
        """Aus einem genehmigten Projektziel die benötigten Dateien und Inhalte ableiten.

        Die Ableitung ist ein Vorschlag: jede Datei wird sofort gegen die vorhandenen
        Pfad- und Berechtigungsgrenzen geprüft und nur in diesem geprüften Zustand
        zurückgegeben. Scheitert die Ableitung oder eine Prüfung, wird nichts geschrieben
        und keine Ersatzliste erdacht.
        """
        from nexus.integrations.hermes_client import validate_task

        if not isinstance(task, str) or not task.strip() or len(task) > 6000:
            return {"success": False, "error": "GOAL_INVALID"}
        blocked_actions = unsupported_actions(task)
        if blocked_actions:
            return {"success": False, "error": "UNSUPPORTED_ACTIONS", "details": blocked_actions}
        if self.llm is None:
            return {"success": False, "error": "MODEL_UNAVAILABLE"}
        required: list[str] = []
        for candidate in (required_paths or []):
            if not isinstance(candidate, str) or not candidate:
                continue
            path = candidate.replace("\\", "/")
            if "/" not in path and ":" not in path:
                path = "workspace/projects/hermes/" + path
            required.append(path)
        context_note = ""
        for item in (context or []):
            if not isinstance(item, dict):
                continue
            source = str(item.get("path") or "")
            body = item.get("content")
            if source and isinstance(body, str) and body.strip():
                context_note += ("\n\nAlready existing input file (" + source
                                 + ") - READ ONLY, never recreate it; use its content to derive the "
                                 "requested new result file:\n---\n" + body.strip()[:4000] + "\n---")
        messages = [
            {"role": "system", "content":
                "You are the NEXUS Hermes project planner. You turn one approved project goal into a "
                "concrete file work order. Answer with exactly ONE JSON object, no prose, no markdown fence:\n"
                '{"files":[{"path":"workspace/projects/hermes/<name>.<txt|md|json>","content":"<complete exact file content>"}]}\n'
                "Rules:\n"
                "1. Create EXACTLY the files the goal explicitly names - no more and no fewer. When the user "
                "message lists required files, your plan MUST contain exactly those paths (same count) and no "
                "other file. Never invent extra helper, README, index, log or notes files. A goal that names a "
                "work plan, a JSON status file and a final report means exactly three files: one .txt plan, one "
                ".json status and one .md report.\n"
                f"2. Path pattern workspace/projects/hermes/<short_german_name>.<txt|md|json>; extension must fit the "
                f"deliverable; unique paths; 1 to {max_files} files.\n"
                "3. Every content is the COMPLETE final file content, written in German, with real concrete text "
                "derived from the goal (no placeholders, no '...', no Chinese, no English). Keep every content "
                "short and finite; never repeat a value in a long list.\n"
                "4. Only text files: NEVER a .py or other code file, NEVER a subdirectory, and never run programs "
                "or tests. No credentials, no private paths, no shell commands, no other tools. Only these files "
                "are authorized."},
            {"role": "user", "content": task + ("\nRequired files that must be included: "
                                                 + json.dumps(required, ensure_ascii=False) if required else "")
                                                 + context_note},
        ]
        def plan_once(user_addendum: str) -> dict:
            msgs = messages + ([{"role": "user", "content": user_addendum}] if user_addendum else [])
            reply = {"success": False, "error": "MODEL_UNAVAILABLE"}
            for attempt in range(3):
                try:
                    try:
                        reply = self.llm.chat(msgs, temperature=0, format="json", num_predict=4096)
                    except TypeError:
                        reply = self.llm.chat(msgs, temperature=0)
                except Exception as exc:
                    reply = {"success": False, "error": type(exc).__name__}
                if reply.get("success"):
                    break
                error = str(reply.get("error") or "").lower()
                retryable = any(x in error for x in ("429", "timeout", "timed out", "temporarily", "503"))
                if not retryable or attempt == 2:
                    return {"success": False, "error": "MODEL_UNAVAILABLE"}
                time.sleep(min(2 ** attempt, 4))
            payload = _json_object(str(reply.get("reply") or ""))
            if payload is None:
                return {"success": False, "error": _json_failure(str(reply.get("reply") or ""))}
            files = payload.get("files")
            if not isinstance(files, list) or not files:
                return {"success": False, "error": "PLAN_STRUCTURE_INVALID"}
            if len(files) > max(1, int(max_files)):
                return {"success": False, "error": "PLAN_TOO_MANY_FILES"}
            requirements: list[dict] = []
            seen: set[str] = set()
            for item in files:
                if not isinstance(item, dict) or not {"path", "content"} <= set(item):
                    return {"success": False, "error": "PLAN_STRUCTURE_INVALID"}
                path, content = item.get("path"), item.get("content")
                if isinstance(path, str) and path.lower().endswith(".json") and isinstance(content, (dict, list)):
                    content = json.dumps(content, ensure_ascii=False, indent=2)
                if not isinstance(path, str) or not isinstance(content, str):
                    return {"success": False, "error": "PLAN_STRUCTURE_INVALID"}
                requirement = {"path": path.replace("\\", "/"), "content": content}
                try:
                    validate_task("create_file", requirement)
                    validate_task("read_file", {"path": requirement["path"]})
                except Exception as exc:
                    return {"success": False, "error": str(exc) or "PLAN_PATH_NOT_AUTHORIZED"}
                if requirement["path"] in seen:
                    return {"success": False, "error": "PLAN_DUPLICATE_PATH"}
                seen.add(requirement["path"])
                requirements.append(requirement)
            missing = [path for path in required if path not in seen]
            if missing:
                return {"success": False, "error": "PLAN_REQUIRED_PATH_MISSING",
                        "missing": missing, "model": reply.get("model")}
            return {"success": True, "requirements": requirements, "model": reply.get("model")}

        first = plan_once("")
        if first.get("success"):
            return first
        # Bei nicht korrigierbaren Vorabfehlern kein zweiter Modellaufruf.
        if first.get("error") in ("MODEL_UNAVAILABLE", "GOAL_INVALID", "UNSUPPORTED_ACTIONS"):
            return first
        # Genau EIN begrenzter Korrekturversuch bei ungültiger Planung; danach konkreter Fehler.
        # Jede Ableitung wird weiterhin gegen die bestehenden Grenzen geprüft.
        if first.get("missing"):
            addendum = ("Die folgenden zwingend geforderten Dateien fehlen im Plan. "
                        "Gib erneut genau EIN JSON-Objekt mit ALLEN Dateien deines Plans zurück und "
                        "ergänze diese Pfade mit vollständigem finalem Inhalt:\n"
                        + json.dumps(first["missing"], ensure_ascii=False))
        else:
            code = str(first.get("error") or "PLAN_NOT_DERIVED")
            addendum = ("Dein vorheriger Plan war ungültig (" + code + "). Gib erneut genau EIN "
                        "JSON-Objekt zurück, das AUSSCHLIESSLICH die im Ziel genannten Dateien enthält "
                        f"(höchstens {int(max_files)}), keine zusätzlichen Dateien, keine doppelten "
                        "Pfade und vollständiges gültiges JSON.")
        second = plan_once(addendum)
        if second.get("success"):
            return second
        if second.get("missing"):
            return {"success": False, "error": "PLAN_REQUIRED_PATH_MISSING"}
        return {"success": False, "error": str(second.get("error") or first.get("error") or "PLAN_NOT_DERIVED")}


    def derive_code_plan(self, task: str, *, max_files: int = 6) -> dict:
        """Aus einem genehmigten Entwicklungsziel einen geprüften Python-Dateiplan ableiten.

        Zweiphasig, damit ein kleines Modell keinen Programmcode in JSON einbetten muss:
        Phase 1 liefert nur die Dateiliste, Phase 2 je Datei den rohen Inhalt über einen
        eigenen Aufruf. Erlaubt sind ausschließlich neue .py/.md-Dateien flach unter
        workspace/projects/hermes/code/<projekt>/. Es wird genau ein Hauptmodul und genau
        ein Testmodul (test_*.py) verlangt. Die Ableitung ist ein Vorschlag: jede Datei wird
        sofort gegen den Codebereich geprüft, geschrieben wird nichts.
        """
        from nexus.bridge.code_sandbox import code_path

        if not isinstance(task, str) or not task.strip() or len(task) > 6000:
            return {"success": False, "error": "GOAL_INVALID"}
        if self.llm is None:
            return {"success": False, "error": "MODEL_UNAVAILABLE"}
        code_dir = "workspace/projects/hermes/code"
        plan_messages = [
            {"role": "system", "content":
                "You are the NEXUS Hermes Python assistant. Turn one approved goal into a small, complete "
                "Python project. Answer with exactly ONE JSON object, no prose, no markdown fence. List ONLY "
                "the file names, never their code:\n"
                '{"project":"<slug>","files":["<name>.py","<name>.md"]}\n'
                "Rules:\n"
                f"1. 2 to {max_files} entries. Exactly one main module (.py) implementing the goal, and exactly "
                "one test module named test_<something>.py that imports the main module and checks the "
                "requested behavior. Optionally one README.md.\n"
                "2. <slug> matches [a-z0-9_-]{1,40}. Each entry is a bare file name only (no folder, no path).\n"
                "3. Only the Python standard library. NEVER import socket, urllib, http, requests, subprocess, "
                "multiprocessing, ctypes, ftplib, smtplib or anything network/OS-command related."},
            {"role": "user", "content": task},
        ]
        reply = {"success": False, "error": "MODEL_UNAVAILABLE"}
        for attempt in range(3):
            try:
                try:
                    reply = self.llm.chat(plan_messages, temperature=0, format="json", num_predict=1024)
                except TypeError:
                    reply = self.llm.chat(plan_messages, temperature=0)
            except Exception as exc:
                reply = {"success": False, "error": type(exc).__name__}
            if reply.get("success"):
                break
            error = str(reply.get("error") or "").lower()
            retryable = any(x in error for x in ("429", "timeout", "timed out", "temporarily", "503"))
            if not retryable or attempt == 2:
                return {"success": False, "error": "MODEL_UNAVAILABLE"}
            time.sleep(min(2 ** attempt, 4))
        payload = _json_object(str(reply.get("reply") or ""))
        if payload is None:
            return {"success": False, "error": _json_failure(str(reply.get("reply") or ""))}
        files = payload.get("files")
        if not isinstance(files, list) or not files:
            return {"success": False, "error": "PLAN_STRUCTURE_INVALID"}
        if len(files) > max(2, int(max_files)):
            return {"success": False, "error": "PLAN_TOO_MANY_FILES"}
        slug = payload.get("project")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9_-]{1,40}", slug):
            slug = ""
        entries: list[dict] = []
        seen: set[str] = set()
        slugs: set[str] = set()
        test_paths: list[str] = []
        main_paths: list[str] = []
        for item in files:
            if isinstance(item, str):
                path = item if "/" in item else (f"{code_dir}/{slug}/{item}" if slug else None)
                content = None
            elif isinstance(item, dict):
                path = item.get("path")
                raw = item.get("content")
                content = raw if isinstance(raw, str) and raw.strip() else None
            else:
                return {"success": False, "error": "PLAN_STRUCTURE_INVALID"}
            if not isinstance(path, str):
                return {"success": False, "error": "CODE_PLAN_SLUG_MISMATCH"}
            normalized = code_path(path)
            if normalized is None or not normalized.lower().endswith((".py", ".md")):
                return {"success": False, "error": "CODE_PLAN_PATH_NOT_AUTHORIZED"}
            parts = normalized[len(code_dir) + 1:].split("/")
            if len(parts) != 2:
                return {"success": False, "error": "CODE_PLAN_NOT_FLAT"}
            file_slug, name = parts
            if name.lower().endswith(".py"):
                (test_paths if name.lower().startswith("test_") else main_paths).append(normalized)
            slugs.add(file_slug)
            if normalized in seen:
                return {"success": False, "error": "PLAN_DUPLICATE_PATH"}
            seen.add(normalized)
            entries.append({"path": normalized, "content": content})
        if len(slugs) != 1:
            return {"success": False, "error": "CODE_PLAN_SLUG_MISMATCH"}
        if len(test_paths) != 1:
            return {"success": False, "error": "CODE_PLAN_TEST_MODULE_REQUIRED"}
        if not main_paths:
            return {"success": False, "error": "CODE_PLAN_MAIN_MODULE_REQUIRED"}
        project = next(iter(slugs))
        main_path = main_paths[0]
        main_name = main_path.rsplit("/", 1)[-1]
        siblings = ", ".join(e["path"].rsplit("/", 1)[-1] for e in entries)

        def is_test(filename):
            return filename.lower().startswith("test_") and filename.lower().endswith(".py")

        # Non-test files first, so the test module can be written against the real main module.
        for entry in entries:
            name = entry["path"].rsplit("/", 1)[-1]
            if is_test(name) or entry["content"] is not None:
                continue
            entry["content"] = self._derive_file_content(task, name, main_name, siblings)
            if entry["content"] is None:
                return {"success": False, "error": "CODE_PLAN_CONTENT_MISSING"}
        main_source = next((e["content"] for e in entries if e["path"] == main_path), None)
        for entry in entries:
            name = entry["path"].rsplit("/", 1)[-1]
            if not is_test(name) or entry["content"] is not None:
                continue
            entry["content"] = self._derive_file_content(task, name, main_name, siblings, main_source=main_source)
            if entry["content"] is None:
                return {"success": False, "error": "CODE_PLAN_CONTENT_MISSING"}
        requirements = [{"path": e["path"], "content": e["content"]} for e in entries]
        return {"success": True, "project": project, "files": requirements,
                "test_path": test_paths[0], "main_paths": main_paths, "model": reply.get("model")}

    def _derive_file_content(self, task: str, name: str, main_name: str, siblings: str,
                             *, main_source: str | None = None) -> str | None:
        """Rohen Inhalt genau einer Datei ableiten; kein JSON, nur der Dateiinhalt."""
        messages = [
            {"role": "system", "content":
                "You are writing exactly ONE file of a small Python project. Output ONLY the complete raw "
                "content of the file, nothing else: no JSON, no surrounding markdown code fence, no "
                "explanation. Use only the Python standard library (a module that is part of CPython itself) "
                "and never import socket, urllib, http, requests, subprocess, multiprocessing, ctypes, ftplib, "
                "smtplib or anything network/OS-command related. NEVER import a third-party package such as "
                "pytest, numpy or requests - only modules that ship with Python. If the file is the test module "
                f"it must import from '{main_name[:-3]}', match the main module's real behavior and be runnable "
                "directly as `python test_x.py`: use plain asserts inside an `if __name__ == '__main__':` block "
                "that prints a short result, never pytest functions. Design for testing: put the program "
                "logic into pure functions that take parameters and return values (no input() or print() inside "
                "them); all input()/print() stays in main(), which only runs under `if __name__ == '__main__':`. "
                "Randomness must be injectable (a parameter such as secret=None or a random.Random(seed)). The "
                "test module calls only these pure functions with fixed values and never calls main() or input()."},
            {"role": "user", "content":
                f"Goal: {task}\n\nFile to write: {name}\nOther files in the project: {siblings}"
                + (f"\n\nCurrent content of {main_name}:\n{main_source}" if main_source else "")},
        ]
        for attempt in range(3):
            try:
                try:
                    reply = self.llm.chat(messages, temperature=0, num_predict=4096)
                except TypeError:
                    reply = self.llm.chat(messages)
            except Exception:
                reply = {"success": False}
            if reply.get("success"):
                content = _strip_code_fence(str(reply.get("reply") or ""))
                if content.strip():
                    return content
            if attempt < 2:
                time.sleep(min(2 ** attempt, 4))
        return None


    def apply_requirements(self, requirements: list) -> dict:
        """Exakt vorgegebene Dateien deterministisch über die Bridge schreiben und prüfen.

        Kein Modell im Schreibpfad: create_file erhaelt den Inhalt direkt, danach wird
        read_file bytegenau gegen den erwarteten Inhalt verglichen. Die Vertragspruefung
        von run() bleibt unveraendert aktiv (identische Pfad-/Inhaltsgrenzen). Bereits
        vorhandene Dateien mit exakt passendem Inhalt werden wiederverwendet und nicht
        neu geschrieben; abweichender Inhalt wird nie ueberschrieben.
        """
        import hashlib
        from nexus.integrations.hermes_client import validate_task, invoke

        def result(status, error, task_ids, verification):
            return {"schema": SCHEMA, "success": status == "PASS", "status": status,
                    "error": error, "task_ids": [t for t in task_ids if t],
                    "verification": verification}

        try:
            if not isinstance(requirements, list) or not requirements:
                raise ValueError()
            seen: set[str] = set()
            for requirement in requirements:
                if type(requirement) is not dict or set(requirement) != {"path", "content"}:
                    raise ValueError()
                validate_task("create_file", requirement)
                validate_task("read_file", {"path": requirement["path"]})
                if requirement["path"] in seen:
                    raise ValueError()
                seen.add(requirement["path"])
        except Exception:
            return result("BLOCKED", "EXPLICIT_FILE_REQUIREMENTS_REQUIRED", [], [])

        task_ids: list[str] = []
        verification: list[dict] = []
        for requirement in requirements:
            path, content = requirement["path"], requirement["content"]
            read = invoke({"operation": "submit", "task": "read_file", "params": {"path": path}})
            existing = ((read.get("result") or {}).get("content")
                        if read.get("success") is True and isinstance(read.get("result"), dict) else None)
            if existing is not None:
                if existing != content:
                    task_ids.append(read.get("task_id"))
                    return result("BLOCKED", "CONTENT_MISMATCH_NO_OVERWRITE", task_ids, verification)
                task_ids.append(read.get("task_id"))
                verification.append({"path": path, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                                     "bytes": len(content.encode("utf-8")), "create_task_id": None,
                                     "read_task_id": read.get("task_id"), "equal": True, "reused": True})
                continue
            if (read.get("error_code") or read.get("error")) not in ("NOT_FOUND", "FILE_NOT_FOUND"):
                task_ids.append(read.get("task_id"))
                return result("BLOCKED", read.get("error_code") or read.get("error") or "READ_FAILED",
                              task_ids, verification)
            created = invoke({"operation": "submit", "task": "create_file", "params": requirement})
            if not (created.get("success") is True and created.get("task_readback_verified")
                    and created.get("execution_instance_verified")):
                task_ids.append(created.get("task_id"))
                return result("FAILED" if created.get("backend_status") == "failed" else "BLOCKED",
                              created.get("error_code") or "BRIDGE_RESULT_UNVERIFIED", task_ids, verification)
            task_ids.append(created.get("task_id"))
            verified = invoke({"operation": "submit", "task": "read_file", "params": {"path": path}})
            task_ids.append(verified.get("task_id"))
            fields = verified.get("result") if isinstance(verified.get("result"), dict) else {}
            if not (verified.get("success") is True and verified.get("task_readback_verified")
                    and verified.get("execution_instance_verified")):
                return result("BLOCKED", verified.get("error_code") or "READBACK_UNVERIFIED", task_ids, verification)
            if fields.get("truncated") is not False or fields.get("content") != content:
                return result("BLOCKED", "CONTENT_MISMATCH", task_ids, verification)
            verification.append({"path": path, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                                 "bytes": len(content.encode("utf-8")), "create_task_id": created.get("task_id"),
                                 "read_task_id": verified.get("task_id"), "equal": True, "reused": False})
        return result("PASS", None, task_ids, verification)


    # ------------------------------------------------------------------
    # Kernschleife
    # ------------------------------------------------------------------

    def run(self, task: str, *, max_rounds: int = MAX_ROUNDS,
            session_messages: list | None = None, script: list | None = None,
            requirements: list | None = None) -> dict:
        """Execute only a caller-supplied, verifiable file contract via the Bridge.

        No inferred completion criteria and no text-to-shell fallback. An uncertain
        submit remains pending; it is never automatically repeated on resumption.
        """
        import hashlib
        import uuid
        from contextlib import contextmanager
        from nexus.common.atomic import atomic_write_json
        from nexus.integrations.hermes_client import validate_task, invoke

        def blocked(code):
            return {"schema": SCHEMA, "success": False, "status": "BLOCKED", "error": code,
                    "run_id": None, "data": {"execution_status": "NOT_EXECUTED", "tools": []}}

        if script is not None:
            return blocked("SCRIPTED_LIVE_EXECUTION_DENIED")
        try:
            if not isinstance(task, str) or not task.strip() or len(task) > 6000:
                raise ValueError()
            if not isinstance(requirements, list) or not 1 <= len(requirements) <= 4:
                raise ValueError()
            paths = set()
            for requirement in requirements:
                if type(requirement) is not dict or set(requirement) != {"path", "content"}:
                    raise ValueError()
                validate_task("create_file", requirement)
                if requirement['path'] in paths:
                    raise ValueError()
                paths.add(requirement['path'])
        except Exception:
            return blocked("EXPLICIT_FILE_REQUIREMENTS_REQUIRED")

        base = self._state_path()
        fingerprint = hashlib.sha256(json.dumps([task, requirements], sort_keys=True,
                                               ensure_ascii=False).encode()).hexdigest()
        journal = base.parent / ("hermes-order-" + fingerprint + ".json")

        class _Busy(Exception):
            pass

        @contextmanager
        def locked():
            # OS-released cross-process lock per order (journal fingerprint): process death
            # does not leave a stale lease, different orders never block or overwrite each
            # other, and the SAME order cannot run twice at the same time.
            with open(journal.with_suffix('.lock'), 'a+b') as stream:
                stream.seek(0)
                try:
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    raise _Busy() from None
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream, fcntl.LOCK_UN)

        def save(state):
            state['updated_at'] = _utc_ts()
            # Journal first (authoritative, per order). Fail closed before any side effect.
            # Reuse bounded PermissionError retries for short Windows reader locks.
            atomic_write_json(journal, state)
            # hermes_brain.json is only a "last activity" view and never read back for
            # decisions; parallel orders must not collide on a shared temp file.
            try:
                snapshot = base.with_name(base.name + '.' + uuid.uuid4().hex[:8] + '.tmp')
                snapshot.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(snapshot, base)
            except OSError:
                try:
                    snapshot.unlink()
                except (OSError, UnboundLocalError):
                    pass

        def finish(state, status, error=None):
            state.update(status=status, error=error, phase='pruefen')
            save(state)
            self._central_write(state, state['trace'], checkpoint='structured_final',
                                resumed=state.get('resumed', False))
            result = self._summary(state, state['trace'], success=status == 'PASS')
            result.update(model=state.get('model'), verification=state.get('verification'),
                          journal=str(journal), requirements=requirements,
                          task_ids=[t['result'].get('task_id') for t in state['trace']])
            failed_id = (state.get('last_tool_error') or {}).get('task_id')
            if failed_id and failed_id not in result['task_ids']:
                result['task_ids'].append(failed_id)
            return result

        def verified(state):
            trace = state['trace']
            def receipt(entry):
                r = entry.get('result', {})
                return (r.get('success') is True and r.get('task_readback_verified') is True
                        and r.get('execution_instance_verified') is True
                        and r.get('task') == entry.get('name')
                        and r.get('params') == entry.get('args'))
            if not trace or not all(receipt(t) for t in trace):
                return False
            if len({t['result'].get('task_id') for t in trace}) != len(trace):
                return False
            checks = []
            for req in requirements:
                writes = [i for i,t in enumerate(trace) if t['name']=='create_file' and t['args']==req]
                reads = [(i,t) for i,t in enumerate(trace) if t['name']=='read_file' and t['args']=={'path':req['path']}]
                matches = [(i,t) for i,t in reads if writes and i>writes[0]
                           and t['result'].get('result',{}).get('content') == req['content']
                           and t['result'].get('result',{}).get('truncated') is False]
                if len(writes) != 1 or not matches:
                    return False
                checks.append({'path':req['path'], 'content':req['content'],
                               'sha256':hashlib.sha256(req['content'].encode()).hexdigest(),
                               'create_task_id':trace[writes[0]]['result']['task_id'],
                               'read_task_id':matches[-1][1]['result']['task_id'], 'equal':True})
            state['verification'] = checks
            return True

        def artifacts_unchanged():
            for req in requirements:
                target = Path(self.master.project_fs.root) / req['path']
                if not target.is_file() or target.read_bytes() != req['content'].encode('utf-8'):
                    return False
            return True

        try:
            with locked():
                if journal.exists():
                    state = json.loads(journal.read_text(encoding='utf-8'))
                    if (type(state) is not dict or state.get('contract') != 'hermes.structured.v1'
                            or state.get('requirements') != requirements or state.get('task') != task):
                        return blocked('JOURNAL_CONTRACT_MISMATCH')
                    trace = state.get('trace')
                    if (type(trace) is not list
                            or any(type(entry) is not dict or type(entry.get('result')) is not dict for entry in trace)):
                        return blocked('INVALID_JOURNAL_TRACE')
                    state['resumed'] = True
                else:
                    state = {'contract':'hermes.structured.v1', 'run_id':'BRAIN-'+uuid.uuid4().hex,
                             'task':task, 'requirements':requirements, 'status':'ACTIVE', 'phase':'denken',
                             'trace':[], 'pending':None, 'model':None, 'round':0, 'resumed':False,
                              'messages':[{'role':'system','content':
                                  'You are NEXUS HermesBrain. Execute the explicit file requirements using the provided tools. '
                                  'First create_file, then read_file for each path, compare exact contents. '
                                  'Do not describe tool calls: invoke the structured tools. '
                                  'Use only paths and contents in the requirements. Never overwrite. '
                                  'The value of every create_file "content" argument MUST be a byte-for-byte copy of the exact '
                                  '"content" string given in the requirements: preserve every space, tab, newline and quote. '
                                  'Never reformat, re-indent, minify or pretty-print any content, especially JSON; the '
                                  'requirement contents are already final and exact. '
                                  'After all results confirm exact content, give a brief final response without tool calls.'},
                                 {'role':'user','content':task+'\nExact authorized requirements: '+json.dumps(requirements,ensure_ascii=False)}]}
                save(state)
                if state.get('pending') is not None:
                    return finish(state, 'BLOCKED', 'UNCERTAIN_SUBMIT_REQUIRES_RECONCILIATION')
                # Re-fetch and bind receipts before EVERY resume completion branch,
                # including a batch interrupted after its last successful tool.
                seen_task_ids = set()
                for entry in state['trace']:
                    name, args = entry.get('name'), entry.get('args')
                    allowed = (name == 'create_file' and args in requirements or
                               name == 'read_file' and args in [{'path': r['path']} for r in requirements])
                    if not allowed:
                        return finish(state, 'BLOCKED', 'TRACE_CONTRACT_MISMATCH')
                    r = entry['result']
                    if not r.get('task_id') or not r.get('correlation_id'):
                        return finish(state,'BLOCKED','UNVERIFIABLE_RECEIPT')
                    if r['task_id'] in seen_task_ids:
                        return finish(state, 'BLOCKED', 'DUPLICATE_TASK_RECEIPT')
                    seen_task_ids.add(r['task_id'])
                    if r.get('task') != name or r.get('params') != args:
                        return finish(state, 'BLOCKED', 'RECEIPT_BINDING_MISMATCH')
                    refreshed = invoke({'operation':'status','task_id':r['task_id'],'correlation_id':r['correlation_id']})
                    if (refreshed.get('success') is not True
                            or refreshed.get('task_readback_verified') is not True
                            or refreshed.get('execution_instance_verified') is not True
                            or any(refreshed.get(key) != r.get(key) for key in
                                   ('task_id', 'correlation_id', 'task', 'params', 'execution_instance', 'result'))):
                        return finish(state,'BLOCKED','RECEIPT_RECHECK_FAILED')
                    entry['result'] = refreshed
                if state.get('batch_open'):
                    if not verified(state):
                        return finish(state, 'BLOCKED', 'INCOMPLETE_TOOL_BATCH_REQUIRES_RECONCILIATION')
                    if not artifacts_unchanged():
                        return finish(state, 'BLOCKED', 'COMPLETED_ARTIFACT_CHANGED_NO_REWRITE')
                    state['batch_open'] = False
                    state['result'] = state.get('result') or ''
                    return finish(state, 'PASS')
                if state.get('status') == 'PASS':
                    if not artifacts_unchanged():
                        return finish(state, 'BLOCKED', 'COMPLETED_ARTIFACT_CHANGED_NO_REWRITE')
                    return finish(state, 'PASS' if verified(state) else 'BLOCKED',
                                  None if verified(state) else 'VERIFICATION_FAILED')
                for _ in range(min(max(int(max_rounds),1), MAX_ROUNDS)):
                    state['round'] += 1
                    state['status'] = 'ACTIVE'
                    save(state)
                    reply = self._chat(state['messages'])
                    if not reply.get('success'):
                        return finish(state,'PARTIAL' if state['trace'] else 'BLOCKED', str(reply.get('error') or 'MODEL_ERROR')[:300])
                    state['model'] = reply.get('model')
                    calls = reply.get('tool_calls') or []
                    state['batch_open'] = bool(calls)
                    state['messages'].append(reply.get('message') or {'role':'assistant','content':reply.get('reply',''),'tool_calls':calls})
                    save(state)
                    if not calls:
                        if verified(state):
                            state['result'] = reply.get('reply')
                            return finish(state,'PASS')
                        return finish(state,'PARTIAL' if state['trace'] else 'BLOCKED','REQUIRED_RESULTS_MISSING')
                    if type(calls) is not list or len(calls)>MAX_STEPS:
                        # If requirements are already satisfied by existing trace, prefer finishing cleanly
                        try:
                            if verified(state):
                                state['result'] = reply.get('reply') or state.get('result')
                                return finish(state,'PASS')
                        except Exception:
                            pass
                        return finish(state,'BLOCKED','INVALID_TOOL_CALLS')
                    for call in calls:
                        fn = call.get('function', {}) if type(call) is dict else {}
                        name, args = fn.get('name'), fn.get('arguments')
                        if type(args) is str:
                            try: args=json.loads(args)
                            except ValueError: args=None
                        allowed = (name=='create_file' and args in requirements or
                                   name=='read_file' and args in [{'path':r['path']} for r in requirements])
                        if not allowed:
                            return finish(state,'BLOCKED','TOOL_OUTSIDE_AUTHORIZED_CONTRACT')
                        previous = next((t for t in state['trace'] if t['name']==name and t['args']==args),None)
                        if previous:
                            result = previous['result']
                        else:
                            if len(state['trace']) >= MAX_STEPS:
                                return finish(state,'PARTIAL','STEP_LIMIT')
                            if name=='read_file' and not any(t['name']=='create_file' and t['args']['path']==args['path'] for t in state['trace']):
                                return finish(state,'BLOCKED','READ_BEFORE_CREATE')
                            correlation = str(uuid.uuid4())
                            state['pending'] = {'name':name,'args':args,'correlation_id':correlation}
                            save(state)
                            result = invoke({'operation':'submit','task':name,'params':args,'correlation_id':correlation})
                            if result.get('success') is not True or not result.get('task_readback_verified') or not result.get('execution_instance_verified'):
                                state['last_tool_error']=result
                                return finish(state,'FAILED' if result.get('backend_status') == 'failed' else 'BLOCKED',result.get('error_code') or 'BRIDGE_RESULT_UNVERIFIED')
                            state['trace'].append({'name':name,'args':args,'result':result})
                            state['pending']=None
                        tool_message = {'role':'tool','tool_name':name,'content':json.dumps(result,ensure_ascii=False)}
                        if call.get('id'): tool_message['tool_call_id']=call['id']
                        state['messages'].append(tool_message)
                        save(state)
                    state['batch_open'] = False
                    save(state)
                return finish(state,'PARTIAL','ROUND_LIMIT')
        except _Busy:
            return blocked('ORDER_ALREADY_RUNNING')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return blocked('STATE_OR_LOCK_ERROR:'+type(exc).__name__)


    # ------------------------------------------------------------------
    # Interne Ausführung
    # ------------------------------------------------------------------
    def _system_context(self) -> str:
        base = ""
        try:
            ctx = self.master.build_context([])
            if isinstance(ctx, list) and ctx and isinstance(ctx[0], dict):
                base = str(ctx[0].get("content") or "")
        except Exception:
            pass
        if not base and hasattr(self.master, "_system_prompt"):
            try:
                base = str(self.master._system_prompt() or "")
            except Exception:
                pass
        if not base:
            base = "Du bist NEXUS Master Home (HermesBrain). Werkzeuge ausfuehren und Nachweise liefern."
        lessons = hermes_lessons_block()
        if lessons:
            base = base.rstrip() + "\n\n" + lessons
        return base[:12000]


    def _chat(self, msgs: list) -> dict:
        if self.llm is None:
            return {"success":False,"error":"no_llm"}
        tools=[]
        for name, description, properties in (
            ('create_file','Create a new authorized file once. Never overwrite.',
             {'path':{'type':'string'},'content':{'type':'string'}}),
            ('read_file','Read the created file to verify its exact content.',
             {'path':{'type':'string'}})):
            tools.append({'type':'function','function':{'name':name,'description':description,
                'parameters':{'type':'object','properties':properties,'required':list(properties),
                              'additionalProperties':False}}})
        for attempt in range(3):
            try:
                result = self.llm.chat(msgs, tools=tools, temperature=0)
            except Exception as exc:
                result = {"success": False, "error": type(exc).__name__}
            try:  # Live-Monitor (20261028)
                from nexus.common.live_events import emit as _emit
                _calls = [((c.get("function") or {}).get("name")) for c in (result.get("tool_calls") or []) if isinstance(c, dict)]
                _emit("Hermes", "denkt", (result.get("reply") or "") if result.get("success") else f"LLM-Fehler: {result.get('error')}",
                      ok=bool(result.get("success")), data={"model": result.get("model"), "tool_calls": _calls} if result.get("success") else None)
            except Exception:
                pass
            if result.get("success"):
                return result
            error = str(result.get("error") or "").lower()
            retryable = any(x in error for x in ("429", "timeout", "timed out", "temporarily", "503"))
            if not retryable or attempt == 2:
                return result
            time.sleep(min(2 ** attempt, 4))
        return {"success": False, "error": "MODEL_RETRY_EXHAUSTED"}


    def _run_one(self, call: dict) -> dict:
        # Execution is only legal inside run() after contract validation and journaling.
        return {"success": False, "error": "EXPLICIT_JOURNALED_CONTRACT_REQUIRED"}

    def _evidence_ok(self, trace: list, task: str) -> bool:
        # Legacy callers have no explicit acceptance contract: fail closed.
        return False

    @staticmethod
    def _tool_succeeded(result) -> bool:
        if not isinstance(result, dict):
            return bool(result)
        if result.get("success"):
            return not (
                result.get("error")
                or result.get("failed")
                or result.get("failed_writes")
            )
        return False

    def _summary(self, state: dict, trace: list, *, success: bool) -> dict:
        return {
            "schema": SCHEMA,
            "run_id": state.get("run_id"),
            "status": state.get("status"),
            "success": success,
            "task": state.get("task"),
            "phase": state.get("phase"),
            "error": state.get("error"),
            "result": state.get("result"),
            "delegate": state.get("delegate") or {},
            "data": {
                "execution_status": (
                    "TOOLS_SUCCEEDED" if success and trace else
                    "NOT_EXECUTED" if not trace else "INCOMPLETE"
                ),
                "tools": [
                    {
                        "name": t.get("name"),
                        "ok": self._tool_succeeded(t.get("result")),
                        "restored": bool(t.get("restored")),
                    }
                    for t in trace
                ],
            },
        }

    # ------------------------------------------------------------------
    # Externe Hermes-Delegation (nur bei expliziter Freigabe)
    # ------------------------------------------------------------------
    def _delegate(self, run_id: str, task_text: str) -> dict:
        """Grenzt die Delegation auf den Nexus-eingebetteten Hermes-Läufer ein."""
        import subprocess

        root = Path(self.master.project_fs.root)
        runner = root / RUNNER
        task_file = root / "workspace" / "state" / f"hermes_task_{run_id}.txt"
        python = resolve_hermes_python()
        if not python:
            return {"completed": False, "note": "hermes_python_unresolved", "evidence": []}
        started = time.time()
        try:
            task_file.parent.mkdir(parents=True, exist_ok=True)
            task_file.write_text(task_text, encoding="utf-8")
            env = dict(os.environ)
            env[EVIDENCE_KEY_ENV] = run_id
            env[PYTHON_ENV] = python
            cmd = [python, "-B", str(runner), "--query-file", str(task_file)]
            proc = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=MAX_WAIT_DELEGATE_SECONDS,
                env=env,
            )
            evidence_file, evidence = self._delegate_evidence(run_id, since=started)
            if proc.returncode != 0:
                return {
                    "completed": False,
                    "note": f"hermes_runner_rc_{proc.returncode}",
                    "detail": _redact((proc.stderr or proc.stdout or ""))[-400:].strip(),
                    "evidence": evidence,
                    "evidence_file": evidence_file,
                }
            if not evidence:
                return {
                    "completed": False,
                    "note": "hermes_runner_no_evidence",
                    "detail": _redact((proc.stderr or proc.stdout or ""))[-400:].strip(),
                    "evidence": [],
                    "evidence_file": evidence_file,
                }
            return {
                "completed": True,
                "evidence": evidence,
                "evidence_file": evidence_file,
                "note": "hermes_delegate_finished",
            }
        except FileNotFoundError:
            return {"completed": False, "note": "managed_hermes_python_missing", "evidence": []}
        except subprocess.TimeoutExpired:
            return {
                "completed": False,
                "note": "delegate_timeout",
                "evidence": [],
            }
        except Exception as exc:
            return {
                "completed": False,
                "note": f"delegate_error_{type(exc).__name__}",
                "evidence": [],
            }
        finally:
            try:
                task_file.unlink()
            except Exception:
                pass

    @staticmethod
    def _delegate_trace(evidence: list) -> list[dict]:
        """Rückgelesene Delegations-Nachweise in Brain-Traceform übersetzen."""
        trace = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            name = str(item.get("task") or "").strip()
            if not name:
                continue
            result: dict = {"success": bool(item.get("success"))}
            if item.get("error_code"):
                result["error"] = str(item.get("error_code"))
            if not result["success"] and item.get("status"):
                result.setdefault("error", str(item.get("status")))
            trace.append({
                "name": name,
                "args": item.get("params") if isinstance(item.get("params"), dict) else {},
                "result": result,
                "task_id": item.get("task_id"),
                "verified": bool(
                    item.get("task_readback_verified")
                    and item.get("execution_instance_verified")
                ),
            })
        return trace

    def _delegate_evidence(self, run_id: str, *, since: float = 0.0) -> tuple[str | None, list]:
        """Nachweisdatei zu `run_id` über das vom Runner gestempelte `delegate_key` finden."""
        base = Path(self.master.project_fs.root) / "workspace" / "hermes-core" / "evidence"
        try:
            files = sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True) \
                if base.is_dir() else []
        except Exception:
            return None, []
        fallback: tuple[str | None, list] = (None, [])
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("delegate_key") or "") == run_id:
                return str(path), list(payload.get("tasks") or [])
            if not fallback[0]:
                try:
                    fresh = since and path.stat().st_mtime >= since
                except Exception:
                    fresh = False
                if fresh and isinstance(payload.get("tasks"), list):
                    fallback = (str(path), list(payload.get("tasks") or []))
        return fallback


