"""Hermes native Tool-Loop (2026-10-26, Freigabe Eddie 'Ja, Hermes reparieren').

Ursache: Der Hermes-CLI-Lauf (OpenAI-Stream, ~5,5k Token Systemprompt) liefert bei qwen3.5 den
Werkzeugaufruf nur als TEXT (z. B. ``read_file(path="...")``), tool_turns=0. Ollama selbst kann
native tool_calls (/api/chat, belegt 26.10.). Dieses Modul erkennt Text-Toolcalls und fuehrt den
Auftrag dann ueber Ollama-native Tools aus - echt, mit Sicherheitspruefung:
- Pfade nur unter <ROOT>/workspace, keine Geheimnisdateien
- write_file nur im Hermes-Arbeitsverzeichnis, max 200 kB
- jeder Aufruf in workspace/hermes_service/tool_calls.log
"""
from __future__ import annotations

import fnmatch, json, re, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WS = ROOT / "workspace"
LOG = WS / "hermes_service" / "tool_calls.log"
OLLAMA = "http://127.0.0.1:11434"
MAX_TURNS = 6
MAX_READ = 20000
MAX_WRITE = 200_000
SECRET = re.compile(r"(\.env$|secret|token|credential|passw|\.key$|\.pem$|id_rsa)", re.I)
TEXT_CALL = re.compile(r"\b(read_file|write_file|list_directory|list_files|search_files|patch|shell_readonly|project_write_request|project_write_file)\s*\(", re.I)

SHELL_READONLY = {
    "type": "function",
    "function": {
        "name": "shell_readonly",
        "description": "Nur lesen. Keine Schreib-, Loesch-, Netzwerk-Aktionen, keine absoluten Pfade, kein .., kein ~.",
        "parameters": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string", "enum": ["ls", "cat", "grep", "find", "wc", "head", "tail",
                                                   "stat", "du", "df", "pwd", "date", "uname", "echo"]},
                "args": {"type": "array", "items": {"type": "string"}, "default": []},
                "cwd": {"type": "string", "default": "."},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 60, "default": 20},
            },
            "required": ["cmd"],
            "additionalProperties": False,
        },
    },
}

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "project_write_request",
            "description": "Beantragt eine explizite Freigabe zum Schreiben einer NEXUS-Projektdatei. Danach muss project_write_file mit approval_id aufgerufen werden.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "reason": {"type": "string"}
                },
                "required": ["path", "content", "reason"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "project_write_file",
            "description": "Schreibt eine NEXUS-Projektdatei nach erfolgreicher Freigabe. Benötigt approval_id aus project_write_request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "approval_id": {"type": "string"}
                },
                "required": ["path", "content", "approval_id"]
            }
        }
    },

    SHELL_READONLY,
    {"type": "function", "function": {"name": "read_file", "description": "Textdatei lesen",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "list_directory", "description": "Verzeichnisinhalt auflisten",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "search_files", "description": "Dateinamen per Muster suchen (z. B. *.txt)",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}}, "required": ["pattern"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Textdatei im Arbeitsverzeichnis schreiben",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "github", "description": "GitHub per gh-CLI (eingeloggt). command MUSS mit 'gh' oder 'git' beginnen. Beispiele: 'gh search repos NAME --limit 5', 'gh repo view OWNER/REPO' (zeigt README), 'gh repo clone OWNER/REPO G:\\meine-ki\\nexus-ai\\workspace\\sandbox\\repos\\REPO'. Lesen frei, Schreiben SAFE_WRITE, Push main/Loeschen nur mit approval_id.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "cwd": {"type": "string"}, "approval_id": {"type": "string"}}, "required": ["command"]}}},
]


WANTS = re.compile(r"\b(read_file|write_file|list_directory|search_files|github|gh|repo|issue|pull request|project_write_request|project_write_file|shell_readonly|cat|lies|lese|datei)\b", re.I)


def wants_tools(task: str) -> bool:
    """True nur bei echtem Werkzeugbedarf.

    2026-11-09 FIXALL: Negationen (kein Tool / keine Datei / kein Shell) und reine
    Antwortaufgaben duerfen den Native-Tool-Loop nicht ausloesen. Sonst antwortet
    das Modell korrekt ohne Tool, und hermes_agent meldet fälschlich 503 HERMES_NO_TOOL_CALL.
    """
    if not task:
        return False
    cleaned = re.sub(
        r"\bkein(?:e|en|em|er)?\s+(?:tools?|dateien?|shell|werkzeuge?)\b",
        " ",
        task,
        flags=re.I,
    )
    cleaned = re.sub(r"\bohne\s+(?:tools?|dateien?|werkzeuge?)\b", " ", cleaned, flags=re.I)
    # reine Kurzantwort / Rollenspiel-Chat ohne konkrete Datei-/Repo-Aktion
    if re.search(r"\b(?:antworte|sage|sag)\b.{0,60}\b(?:wort|satz|ok)\b", cleaned, re.I):
        if not re.search(r"\b(?:lies|lese|erstelle|schreibe|github|gh|repo|patch)\b", cleaned, re.I):
            return False
    return bool(WANTS.search(cleaned))


def has_text_toolcall(reply: str) -> bool:
    return bool(reply) and bool(TEXT_CALL.search(reply))


def _log(e: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")


def safe_path(p: str, wd: Path, write: bool = False) -> Path:
    raw = str(p or ".").strip()
    q_input = Path(raw)

    if q_input.is_absolute():
        q = q_input.resolve()
    else:
        normalized = raw.replace("\\\\", "/").lstrip("./")
        if normalized == "nexus" or normalized.startswith("nexus/"):
            q = (ROOT / normalized).resolve()
        else:
            q = (wd / q_input).resolve()

    if SECRET.search(q.name):
        raise PermissionError(f"SECRET_FILE_BLOCKED: {q.name}")

    if write:
        base = wd.resolve()
        try:
            q.relative_to(base)
        except ValueError:
            raise PermissionError(
                f"PATH_NOT_AUTHORIZED_WRITE: {q} ausserhalb {base}"
            )
    else:
        allowed = (WS.resolve(), (ROOT / "nexus").resolve())
        if not any(q == base or base in q.parents for base in allowed):
            raise PermissionError(
                f"PATH_NOT_AUTHORIZED_READ: {q} ausserhalb erlaubter Lesebereiche"
            )

    return q


def run_tool(name: str, args: dict, wd: Path) -> str:
    if name == "shell_readonly":
        from nexus.terminal import shell_worker
        r = shell_worker.run(args.get("cmd"), args.get("args"), args.get("cwd", "."),
                             args.get("timeout", 20), agent="Hermes")
        if r.get("ok"):
            out = str(r.get("stdout") or "")
            if r.get("truncated"):
                out += "\n[WORKER_OUTPUT_TRUNCATED]"
            return out
        return str(r.get("error") or r.get("stderr") or "SHELL_READONLY_ERROR")
    if name == "read_file":
        p = safe_path(args.get("path", ""), wd)
        return p.read_text(encoding="utf-8-sig", errors="replace")[:MAX_READ]
    if name in ("list_directory", "list_files"):
        p = safe_path(args.get("path", "."), wd)
        return "\n".join(sorted((x.name + ("/" if x.is_dir() else "")) for x in p.iterdir())[:200])
    if name == "search_files":
        p = safe_path(args.get("path", "."), wd)
        pat = args.get("pattern", "*")
        hits = [str(x.relative_to(ROOT)) for x in p.rglob("*") if x.is_file() and fnmatch.fnmatch(x.name, pat)][:100]
        return "\n".join(hits) or "(keine Treffer)"
    if name == "write_file":
        p = safe_path(args.get("path", ""), wd, write=True)
        c = str(args.get("content", ""))
        if len(c.encode("utf-8")) > MAX_WRITE:
            raise ValueError("WRITE_TOO_LARGE")
        if p.exists():
            bak = p.with_name(p.name + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
            bak.write_bytes(p.read_bytes())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(c, encoding="utf-8")
        return f"geschrieben: {p} ({len(c)} Zeichen)"

    if name in ("project_write_request", "project_write_file"):
        import hashlib
        import os
        from nexus.terminal import guard

        raw = str(args.get("path", "")).strip().replace(chr(92), "/")

        if not raw or raw.startswith("/") or ":" in raw:
            raise PermissionError("PROJECT_PATH_INVALID: relativer Projektpfad erforderlich")

        target = (ROOT / raw).resolve()
        project_root = (ROOT / "nexus").resolve()

        if target != project_root and project_root not in target.parents:
            raise PermissionError("PROJECT_PATH_DENIED: nur nexus/ ist erlaubt")

        if SECRET.search(target.name):
            raise PermissionError("SECRET_FILE_BLOCKED")

        content = args.get("content")
        if not isinstance(content, str):
            raise ValueError("PROJECT_CONTENT_INVALID")

        encoded = content.encode("utf-8")
        if len(encoded) > MAX_WRITE:
            raise ValueError("WRITE_TOO_LARGE")

        params = {
            "path": str(target),
            "content": content
        }

        if name == "project_write_request":
            reason = str(args.get("reason", "")).strip()
            if not reason:
                raise ValueError("APPROVAL_REASON_REQUIRED")

            approval_id = guard.request_action(
                agent="Hermes",
                action="project_write_file",
                params=params,
                reason=reason,
                description=f"NEXUS-Projektdatei ändern: {target.relative_to(ROOT)}"
            )

            return json.dumps({
                "status": "APPROVAL_REQUIRED",
                "approval_id": approval_id,
                "path": str(target),
                "content_sha256": hashlib.sha256(encoded).hexdigest(),
                "message": "Freigabe erforderlich. Erst danach project_write_file aufrufen."
            }, ensure_ascii=False)

        approval_id = str(args.get("approval_id", "")).strip()
        if not approval_id:
            raise PermissionError("APPROVAL_ID_REQUIRED")

        approved = guard.consume_action(
            approval_id=approval_id,
            agent="Hermes",
            action="project_write_file",
            params=params
        )

        if not approved:
            raise PermissionError("PROJECT_WRITE_NOT_APPROVED")

        target.parent.mkdir(parents=True, exist_ok=True)

        backup = None
        if target.exists():
            backup = target.with_name(
                target.name + ".backup_" + time.strftime("%Y%m%d_%H%M%S")
            )
            import shutil
            shutil.copy2(target, backup)

        temporary = target.with_name(target.name + ".hermes_tmp")
        try:
            temporary.write_bytes(encoded)
            os.replace(str(temporary), str(target))
        finally:
            if temporary.exists():
                temporary.unlink()

        written = target.read_bytes()
        if written != encoded:
            raise IOError("PROJECT_WRITE_VERIFY_FAILED")

        return json.dumps({
            "status": "SUCCESS",
            "path": str(target),
            "bytes": len(encoded),
            "verified": True,
            "backup": str(backup) if backup else None,
            "sha256": hashlib.sha256(written).hexdigest()
        }, ensure_ascii=False)

    if name == "github":
        from nexus.integrations import github_tool
        r = github_tool.run(str(args.get("command", "")), agent="Hermes", cwd=args.get("cwd"), approval_id=args.get("approval_id"))
        return json.dumps({k: r.get(k) for k in ("success", "status", "level", "approval_id", "error", "returncode", "stdout", "stderr")}, ensure_ascii=False)[:MAX_READ]
    raise ValueError(f"UNKNOWN_TOOL: {name}")


def _num_ctx() -> int:
    try:
        from nexus.common.ai_layer import unified_num_ctx
        return int(unified_num_ctx())
    except Exception:
        return 8192


def _keep_alive():
    try:
        from nexus.common.ai_layer import keep_alive_value
        return keep_alive_value()
    except Exception:
        return "30m"


def _chat_body(model: str, messages: list) -> dict:
    # 2026-10-29: gleicher num_ctx/keep_alive wie NEXUS -> kein Modell-Neuladen durch abweichenden Kontext
    return {"model": model, "messages": messages, "tools": TOOLS, "stream": False, "think": False,
            "keep_alive": _keep_alive(), "options": {"num_ctx": _num_ctx()}}


def _chat(model: str, messages: list, timeout: int) -> dict:
    body = json.dumps(_chat_body(model, messages)).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())["message"]


def _tool_summary_reply(calls: list, reason: str) -> str:
    """2026-11-04: Ehrliche Kurzantwort mit echtem Werkzeugergebnis, wenn der Modellabschluss fehlt."""
    lines = [f"Das Modell hat keine Abschlussantwort geliefert ({reason}). Echte Werkzeugergebnisse:"]
    for c in calls[-5:]:
        arg = c.get("args", {}).get("path") or c.get("args", {}).get("cmd") or ""
        prev = str(c.get("result_preview", "")).strip()
        if c.get("ok"):
            lines.append(f"- {c['tool']}({arg}) OK:\n{prev}")
        else:
            hint = ""
            if "PATH_NOT_AUTHORIZED_READ" in prev:
                hint = " Hinweis: lesbar sind nur workspace\\ und nexus\\."
            lines.append(f"- {c['tool']}({arg}) FEHLER: {prev}{hint}")
    return "\n".join(lines)


def native_loop(task: str, wd: Path, model: str, rid: str, timeout: int = 170) -> dict:
    t0 = time.time()
    sys = ("Du bist Hermes, der Werkzeug-Agent von NEXUS. Arbeitsverzeichnis: " + str(wd) +
           ". Nutze die bereitgestellten Werkzeuge ueber echte tool_calls, nie als Text. Antworte danach knapp auf Deutsch."
           " GITHUB: Du hast echten Zugriff ueber das Werkzeug 'github' (gh ist eingeloggt, alle Repos von Eddie und alle oeffentlichen Repos lesbar)."
           " Ist ein Repo nur per Name bekannt: 1) gh search repos NAME --limit 5  2) gh repo view OWNER/REPO (liefert README)"
           "  3) bei Bedarf gh repo clone OWNER/REPO G:\\meine-ki\\nexus-ai\\workspace\\sandbox\\repos\\REPO."
           " Frag NIE nach einer URL, wenn du sie selbst suchen kannst. Fasse README-Inhalte nur aus echter Werkzeug-Ausgabe zusammen."
           " Fuer Dateilesezugriffe read_file verwenden. Projektquellen liegen unter nexus/ und sind lesbar. shell_readonly nur fuer erlaubte relative Sandbox-Befehle verwenden. Schreibzugriffe ausschliesslich ueber autorisierte Schreibwerkzeuge."
           " Bei DENIED nicht umgehen, sondern dem Nutzer melden.")
    msgs = [{"role": "system", "content": sys}, {"role": "user", "content": task}]
    calls = []
    for turn in range(MAX_TURNS):
        left = timeout - (time.time() - t0)
        if left < 10:
            break
        try:
            msg = _chat(model, msgs, int(left))
        except Exception as e:
            if not calls:
                raise
            return {"reply": _tool_summary_reply(calls, type(e).__name__),
                    "tool_calls": calls, "turns": turn, "final_missing": True}
        tcs = msg.get("tool_calls") or []
        msgs.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tcs})
        if not tcs:
            return {"reply": (msg.get("content") or "").strip(), "tool_calls": calls, "turns": turn + 1}
        for tc in tcs:
            fn = tc.get("function", {}); name = fn.get("name", ""); args = fn.get("arguments") or {}
            if isinstance(args, str):
                try: args = json.loads(args)
                except Exception: args = {}
            tool_status = "SUCCESS"
            try:
                res = run_tool(name, args, wd)
                ok = True

                if name in ("project_write_request", "project_write_file", "github"):
                    try:
                        payload = json.loads(res)
                    except Exception:
                        payload = {}

                    if name == "project_write_request":
                        if payload.get("status") == "APPROVAL_REQUIRED":
                            ok = False
                            tool_status = "APPROVAL_REQUIRED"
                        else:
                            ok = False
                            tool_status = "INVALID_APPROVAL_RESPONSE"

                    elif name == "project_write_file":
                        ok = (
                            payload.get("status") == "SUCCESS"
                            and payload.get("verified") is True
                        )
                        tool_status = "SUCCESS" if ok else "WRITE_NOT_VERIFIED"

                    elif name == "github":
                        if payload.get("status") == "APPROVAL_REQUIRED":
                            ok = False
                            tool_status = "APPROVAL_REQUIRED"
                        elif payload.get("success") is not True:
                            ok = False
                            tool_status = "GITHUB_ACTION_FAILED"

                elif name == "shell_readonly":
                    if str(res).startswith("SHELL_READONLY_ERROR"):
                        ok = False
                        tool_status = "SHELL_READONLY_FAILED"

            except Exception as e:
                res = f"{type(e).__name__}: {e}"
                ok = False
                tool_status = "TOOL_EXCEPTION"

            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "request_id": rid,
                "tool": name,
                "args": {k: str(v)[:200] for k, v in args.items()},
                "ok": ok,
                "status": tool_status,
                "result_preview": str(res)[:300]
            }
            _log(rec); calls.append(rec)
            msgs.append({"role": "tool", "tool_name": name, "content": res})
    if calls:
        return {"reply": _tool_summary_reply(calls, "Zeit-/Schrittlimit"), "tool_calls": calls,
                "turns": MAX_TURNS, "final_missing": True}
    return {"reply": "HERMES_TOOL_LOOP_LIMIT", "tool_calls": calls, "turns": MAX_TURNS}

# NEXUS_EXTENDED_TOOL_INTENT_20261103
# Erweiterte Erkennung von konkreten Arbeitsauftraegen.
import re as _nexus_intent_re
WANTS = _nexus_intent_re.compile(
    r"\\b(read_file|write_file|list_directory|search_files|github|gh|repo|"
    r"issue|pull request|project_write_request|project_write_file|"
    r"shell_readonly|cat|lies|lese|erstelle|erstellen|erzeuge|erzeugen|"
    r"schreibe|schreiben|bearbeite|bearbeiten|implementiere|implementieren|"
    r"programmiere|programmieren|aendere|ändere|entwickle|entwickeln|"
    r"teste|testen|pruefe|prüfe|repariere|reparieren|"
    r"datei|dateien)\\b|\\w*datei\\w*",
    _nexus_intent_re.IGNORECASE
)


# NEXUS_REGEX_FIX_20261103
WANTS = _nexus_intent_re.compile(
    r"\b(read_file|write_file|list_directory|search_files|github|gh|repo|"
    r"issue|pull request|project_write_request|project_write_file|"
    r"shell_readonly|cat|lies|lese|erstelle|erstellen|erzeuge|erzeugen|"
    r"schreibe|schreiben|bearbeite|bearbeiten|implementiere|implementieren|"
    r"programmiere|programmieren|aendere|ändere|entwickle|entwickeln|"
    r"teste|testen|pruefe|prüfe|repariere|reparieren|"
    r"datei|dateien)\b|\w*datei\w*",
    _nexus_intent_re.IGNORECASE
)
