"""OMEGA Code-Sandbox: Docker-first, lokaler Fallback nur mit explizitem Flag.

Wahrheit statt Sicherheitstheater: Ohne Docker (oder ohne
OMEGA_ALLOW_LOCAL_EXEC=1) wird Code NICHT ausgefuehrt. Der lokale Fallback ist
ein Komfortpfad fuer Entwicklung und ausdruecklich keine Sicherheitsgrenze.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from nexus.omega import config, hooks

_MAX_SOURCE = 200_000
_MAX_STDOUT = 40_000
_MAX_STDERR = 16_000
_FORBIDDEN_LOCAL = {
    "socket", "ssl", "subprocess", "multiprocessing", "ctypes", "urllib", "http",
    "ftplib", "smtplib", "telnetlib", "xmlrpc", "requests", "httpx", "aiohttp",
    "websockets", "winreg", "shutil",
}


def docker_available() -> bool:
    return bool(shutil.which("docker"))


def _forbidden_imports(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    bad: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_LOCAL:
                    bad.add(root)
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in _FORBIDDEN_LOCAL:
                bad.add(root)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "os":
                if node.func.attr in ("system", "popen", "execv", "execve", "execvp",
                                      "execvpe", "execl", "execle", "fork", "forkpty",
                                      "spawnv", "spawnve", "spawnl", "spawnle"):
                    bad.add("os." + node.func.attr)
    return sorted(bad)


def _run(cmd: list[str], cwd: Path, timeout: int, stdin: str = "") -> dict:
    started = time.time()
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), input=(stdin or "").encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        code = int(proc.returncode)
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        return {"success": False, "status": "TIMEOUT", "exit_code": None,
                "stdout": (exc.stdout or b"").decode("utf-8", "replace")[:_MAX_STDOUT],
                "stderr": (exc.stderr or b"").decode("utf-8", "replace")[:_MAX_STDERR],
                "error": f"Timeout nach {timeout}s", "duration_ms": int((time.time() - started) * 1000)}
    except OSError as exc:
        return {"success": False, "status": "SPAWN_ERROR", "exit_code": None,
                "stdout": "", "stderr": "", "error": f"{type(exc).__name__}: {exc}",
                "duration_ms": int((time.time() - started) * 1000)}
    out_trunc = len(out) > _MAX_STDOUT
    err_trunc = len(err) > _MAX_STDERR
    return {
        "success": code == 0,
        "status": "OK" if code == 0 else "EXIT_" + str(code),
        "exit_code": code,
        "stdout": out[:_MAX_STDOUT],
        "stderr": err[:_MAX_STDERR],
        "truncated": out_trunc or err_trunc,
        "duration_ms": int((time.time() - started) * 1000),
    }


def _clean_env() -> dict[str, str]:
    keep = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT", "SYSTEMDRIVE",
            "HOME", "USERPROFILE", "LANG", "LC_ALL", "COMSPEC", "OS",
            "NUMBER_OF_PROCESSORS", "APPDATA", "LOCALAPPDATA")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_python(code: str, timeout: int = 20, stdin: str = "", *,
               allow_local: bool | None = None) -> dict:
    text = str(code or "")
    if not text.strip():
        return {"success": False, "status": "EMPTY", "error": "code_required"}
    if len(text.encode("utf-8")) > _MAX_SOURCE:
        return {"success": False, "status": "TOO_LARGE", "error": "code_too_large"}
    lint = hooks.lint_python(text)
    if not lint["ok"]:
        return {"success": False, "status": "LINT_FAILED",
                "error": "Syntaxfehler: " + "; ".join(lint["errors"]), "lint": lint}
    timeout = max(1, min(int(timeout or 20), 60))
    if allow_local is None:
        allow_local = config.allow_local_exec()

    work = Path(tempfile.mkdtemp(prefix="omega-sandbox-", dir=str(config.sandbox_dir())))
    target = work / "main.py"
    target.write_text(text, encoding="utf-8")
    try:
        if docker_available() and not config.flag("OMEGA_DISABLE_DOCKER", False):
            image = config.docker_image()
            cmd = [
                "docker", "run", "--rm", "--network", "none", "--memory", "256m",
                "--cpus", "0.5", "--pids-limit", "64",
                "--mount", f"type=bind,source={work},target=/work,readonly",
                "-w", "/work", "-e", "PYTHONIOENCODING=utf-8", image,
                "python", "-I", "-B", "/work/main.py",
            ]
            res = _run(cmd, work, timeout, stdin)
            res["isolation"] = "docker (network none, memory/cpu/pids limits)"
            return res

        if not allow_local:
            return {
                "success": False, "status": "SANDBOX_UNAVAILABLE",
                "error": ("Docker ist nicht verfuegbar. Code wird nicht ausgefuehrt. "
                          "Entweder Docker installieren oder OMEGA_ALLOW_LOCAL_EXEC=1 "
                          "bewusst setzen (kein Sicherheitsversprechen)."),
                "isolation": "none",
            }
        bad = _forbidden_imports(text)
        if bad:
            return {"success": False, "status": "SANDBOX_BLOCKED",
                    "error": "Lokaler Fallback blockiert Module/Aufrufe: " + ", ".join(bad),
                    "isolation": "local-process"}
        res = _run([sys.executable, "-I", "-B", str(target)], work, timeout, stdin)
        res["isolation"] = "local-process (NOT a security boundary)"
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def status() -> dict:
    return {
        "docker": docker_available(),
        "docker_image": config.docker_image(),
        "local_fallback_allowed": config.allow_local_exec(),
        "sandbox_dir": str(config.sandbox_dir()),
    }
