"""Git/GitHub-Tools ohne Shell-Injection. Schreibende Wirkung wird gegated."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from nexus.common.paths import WORKSPACE
from nexus.omega import config

_MAX_OUT = 30_000
_DESTRUCTIVE = {"push", "reset", "clean", "filter-branch", "gc", "prune", "rm", "restore"}
_DESTRUCTIVE_FLAGS = ("--hard", "-D", "-d", "--force", "-f", "--delete", "--prune")


def _roots() -> list[Path]:
    return [config.home() / "repos", WORKSPACE / "sandbox" / "repos"]


def _resolve_cwd(raw: str | None) -> Path | None:
    if not raw:
        base = _roots()[0]
        base.mkdir(parents=True, exist_ok=True)
        return base.resolve()
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        p = _roots()[0] / p
    try:
        p = p.resolve()
    except OSError:
        return None
    for root in _roots():
        try:
            p.relative_to(root.resolve())
            return p
        except (ValueError, OSError):
            continue
    return None


def _redact(text: str) -> str:
    text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1***@", str(text or ""))
    return text.replace("ghp_", "ghp_***").replace("github_pat_", "github_pat_***")


def _looks_destructive(args: list[str]) -> bool:
    if not args:
        return False
    first = str(args[0]).lower()
    if first in _DESTRUCTIVE:
        if first == "reset" and not any(a in args for a in ("--hard", "--keep", "--merge")):
            return False
        return True
    for arg in args:
        if str(arg) in _DESTRUCTIVE_FLAGS:
            return True
    if first in ("branch", "tag") and any(str(a) in ("-d", "-D", "--delete") for a in args):
        return True
    return False


def is_destructive(params: dict) -> bool:
    if "command" in params:
        return github_is_destructive(params)
    return _looks_destructive([str(a) for a in (params.get("args") or [])])


def git_repo(params: dict, ctx: dict) -> dict:
    args = [str(a) for a in (params.get("args") or []) if str(a).strip()]
    if not args:
        return {"success": False, "status": "INVALID_PARAMS", "error": "args_required"}
    if len(args) > 30:
        return {"success": False, "status": "INVALID_PARAMS", "error": "too_many_args"}
    cwd = _resolve_cwd(params.get("cwd"))
    if cwd is None:
        return {"success": False, "status": "PATH_DENIED",
                "error": "cwd muss in workspace/omega/repos oder workspace/sandbox/repos liegen"}
    cwd.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=90, env=env, shell=False,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except FileNotFoundError:
        return {"success": False, "status": "NOT_AVAILABLE", "error": "git ist nicht installiert"}
    except subprocess.TimeoutExpired:
        return {"success": False, "status": "TIMEOUT", "error": "git timeout (90s)"}
    except OSError as exc:
        return {"success": False, "status": "SPAWN_ERROR", "error": str(exc)[:300]}
    out = _redact(proc.stdout.decode("utf-8", "replace"))
    err = _redact(proc.stderr.decode("utf-8", "replace"))
    code = int(proc.returncode)
    return {"success": code == 0, "status": "OK" if code == 0 else "GIT_FAILED",
            "exit_code": code, "stdout": out[:_MAX_OUT], "stderr": err[_MAX_OUT // 2:],
            "cwd": str(cwd), "destructive": _looks_destructive(args),
            "truncated": len(out) > _MAX_OUT}


def github(params: dict, ctx: dict) -> dict:
    command = str(params.get("command") or "").strip()
    if not command:
        return {"success": False, "status": "INVALID_PARAMS", "error": "command_required"}
    from nexus.integrations import github_tool

    cwd = _resolve_cwd(params.get("cwd"))
    res = github_tool.run(command, agent="NEXUS", cwd=str(cwd) if cwd else None)
    return {
        "success": bool(res.get("success")),
        "status": res.get("status") or ("OK" if res.get("success") else "GITHUB_FAILED"),
        "level": res.get("level"), "exit_code": res.get("returncode"),
        "approval_id": res.get("approval_id"),
        "stdout": _redact(str(res.get("stdout") or ""))[:_MAX_OUT],
        "stderr": _redact(str(res.get("stderr") or ""))[_MAX_OUT // 2:],
        "error": res.get("error"),
    }


def github_is_destructive(params: dict) -> bool:
    try:
        from nexus.integrations import github_tool

        command = str(params.get("command") or "")
        if github_tool.hard_block(command)[0]:
            return True
        classified = github_tool.classify(command)
        return bool(classified and classified[0] == "PRIVILEGED")
    except Exception:
        return True


HANDLERS = {"git_repo": git_repo, "github": github}
