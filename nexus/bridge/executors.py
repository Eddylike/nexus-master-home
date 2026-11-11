"""Registered-task executors for Agent Bridge Master (no arbitrary shell)."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.bridge.errors import (
    BridgeError,
    PATH_DENIED,
    PATH_TRAVERSAL,
    INVALID_REQUEST,
    NOT_FOUND,
    SCRIPT_DENIED,
    ACTION_DENIED,
    NOT_IMPLEMENTED,
)
from nexus.bridge.models import TaskRecord
from nexus.bridge.permissions import TASK_PERMISSIONS, PermissionLevel, process_level
from nexus.bridge.tasks import REGISTERED_TASKS, TaskManager
from nexus.common.config import Config
from nexus.common.paths import ROOT, WORKSPACE, APEX, MEDIA_DIR

ALLOWED_ROOT = ROOT.resolve()
_MAX_READ = 200_000
_MAX_LIST = 500
_MAX_WRITE = 2_000_000
_ACTIONS_DIR = WORKSPACE / "scripts" / "bridge_actions"
_ACTIONS_REGISTRY = _ACTIONS_DIR / "registry.json"
_SECRET_NAMES = {".token", ".bridge_token", "assistant_token.txt", "openai.env", ".env"}
BACKUP_DIR = WORKSPACE / "backups"  # Snapshot bestehender Dateien vor Änderung (Repair-Sicherung)


def _safe_path(rel_or_abs: str, *, must_exist: bool = False, allow_new: bool = False) -> Path:
    raw = (rel_or_abs or "").strip()
    if not raw:
        raise BridgeError(INVALID_REQUEST, "path required", http_status=400)
    norm = raw.replace("\\", "/")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise BridgeError(PATH_TRAVERSAL, "path traversal blocked", http_status=403)
    root = ALLOWED_ROOT
    try:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        if allow_new and not candidate.exists():
            parent = candidate.parent.resolve()
            fp = parent / candidate.name
        else:
            fp = candidate.resolve()
    except Exception as e:
        raise BridgeError(PATH_DENIED, f"invalid path: {e}", http_status=403) from e
    root_s = str(root)
    fp_s = str(fp)
    if not (fp_s == root_s or fp_s.startswith(root_s + os.sep)):
        raise BridgeError(
            PATH_DENIED,
            f"path outside allowed root {root_s}",
            http_status=403,
            details={"root": root_s},
        )
    if must_exist and not fp.exists():
        raise BridgeError(INVALID_REQUEST, f"path not found: {fp}", http_status=404)
    if fp.name.lower() in _SECRET_NAMES or "secret" in {x.lower() for x in fp.parts}:
        raise BridgeError(PATH_DENIED, "refusing secret/token path", http_status=403)
    return fp


def _rel(fp: Path) -> str:
    try:
        return str(fp.relative_to(ALLOWED_ROOT))
    except Exception:
        return str(fp)


def _atomic_write(fp: Path, data: bytes) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".nxw_", dir=str(fp.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(fp))
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _backup_before_write(fp: Path) -> str | None:
    """Vor einer Änderung bestehender Dateien eine Sicherungskopie ablegen.

    NEXUS_FILE_BACKUP_V1 (fail-closed): Bestehende Inhalte werden nie ohne belegbare
    Sicherung überschrieben. Schlägt der Schnappschuss fehl, wird die Änderung mit
    ACTION_DENIED abgebrochen. None nur, wenn keine bestehende Datei zu sichern war
    (neue Datei) — dann ist kein Abbruch nötig.
    """
    if not (fp.exists() and fp.is_file()):
        return None
    try:
        rel = _rel(fp)
        flat = rel.replace("\\", "/").replace("/", "__")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        target = BACKUP_DIR / f"{stamp}_{flat}.bak"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fp, target)
    except Exception as exc:
        raise BridgeError(
            ACTION_DENIED,
            f"backup failed; file change aborted: {exc}",
            http_status=500,
        ) from exc
    return _rel(target)


def exec_ping(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    return {
        "ok": True,
        "pong": True,
        "protocol": "NEXUS_AGENT_BRIDGE_PROTOCOL",
        "version": "1.1.0",
        "task_id": rec.id,
        "echo": params.get("echo"),
        "process_level": process_level().value,
    }


def exec_system_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    disk_free = None
    try:
        disk_free = round(shutil.disk_usage(str(ALLOWED_ROOT)).free / (1024**3), 2)
    except Exception:
        disk_free = None
    metrics = None
    try:
        from nexus.computer.metrics import metrics as get_metrics
        metrics = get_metrics()
    except Exception as e:
        metrics = {"success": False, "error": str(e)}
    return {
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cwd": os.getcwd(),
        "root": str(ALLOWED_ROOT),
        "disk_free_gb": disk_free,
        "nexus_port": Config.PORT,
        "nexus_ssl_port": Config.SSL_PORT,
        "process_level": process_level().value,
        "metrics": metrics,
    }


def exec_inspect_server(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    # SAFE_WRITE/READ_ONLY probe: sys.modules lookup only. Never imports or reloads
    # hermes_quarantine and never executes candidate or protection code.
    import hashlib
    import sys
    import types

    def _fp_const(value: Any) -> Any:
        if isinstance(value, types.CodeType):
            return {"code": _fp_record(value)}
        if isinstance(value, tuple):
            return {"tuple": [_fp_const(item) for item in value]}
        if isinstance(value, frozenset):
            return {"frozenset": sorted((_fp_const(item) for item in value), key=repr)}
        return {"type": type(value).__name__, "repr": repr(value)}

    def _fp_record(value: types.CodeType) -> dict[str, Any]:
        result = {name: getattr(value, name) for name in
                  ("co_name", "co_qualname", "co_filename", "co_firstlineno", "co_names",
                   "co_varnames", "co_freevars", "co_cellvars", "co_argcount",
                   "co_posonlyargcount", "co_kwonlyargcount", "co_flags", "co_nlocals", "co_stacksize")}
        result.update(co_code=value.co_code.hex(), co_linetable=value.co_linetable.hex(),
                      co_exceptiontable=value.co_exceptiontable.hex(),
                      co_consts=[_fp_const(item) for item in value.co_consts])
        return result

    source = ALLOWED_ROOT / "nexus" / "integrations" / "hermes_quarantine.py"
    probe = {
        "server_pid": os.getpid(),
        "hermes_quarantine_load_state": "NOT_LOADED",
        "report_candidates_code_sha256": None,
        "hermes_quarantine_source_sha256": None,
        "probe_mode": "sys.modules_lookup_only_no_import_no_dynamic_code",
    }
    try:
        probe["hermes_quarantine_source_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    except OSError as exc:
        probe["probe_error"] = type(exc).__name__
    module = sys.modules.get("nexus.integrations.hermes_quarantine")
    if module is not None:
        probe["hermes_quarantine_load_state"] = "LOADED"
        fn = getattr(module, "report_candidates", None)
        if isinstance(fn, types.FunctionType):
            try:
                probe["report_candidates_code_sha256"] = hashlib.sha256(
                    json.dumps(_fp_record(fn.__code__), sort_keys=True).encode("utf-8")).hexdigest()
            except (TypeError, ValueError) as exc:
                probe["probe_error"] = type(exc).__name__
        else:
            probe["report_candidates_code_sha256"] = None
            probe["probe_error"] = "REPORT_CANDIDATES_NOT_A_FUNCTION"
    return {
        "server": "NexusAI/1.0",
        "handler": "ThreadingHTTPServer",
        "host": Config.HOST,
        "port": Config.PORT,
        "ssl_port": Config.SSL_PORT,
        "version": Config.VERSION,
        "model": Config.PREFERRED_MODEL,
        "root": str(ALLOWED_ROOT),
        "bridge_protocol": "NEXUS_AGENT_BRIDGE_PROTOCOL",
        "bridge_version": "1.1.0",
        "registered_tasks": list(REGISTERED_TASKS),
        "process_level": process_level().value,
        "protection_probe": probe,
        "endpoints": [
            "POST /api/task",
            "GET /api/task/{id}",
            "POST /api/task/{id}/cancel",
            "GET /api/task/{id}/log",
            "GET /api/capabilities",
            "POST /api/message",
            "GET /api/bridge/health",
            "GET/POST /api/bridge/approvals (local only)",
            "GET /api/v1/* aliases",
        ],
    }


def exec_read_file(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    path = params.get("path") or params.get("file") or ""
    fp = _safe_path(str(path))
    if not fp.exists():
        raise BridgeError(NOT_FOUND, f"path not found: {fp}", http_status=404)
    if not fp.is_file():
        raise BridgeError(INVALID_REQUEST, "not a file", http_status=400)
    data = fp.read_bytes()
    truncated = False
    if len(data) > _MAX_READ:
        data = data[:_MAX_READ]
        truncated = True
    try:
        text = data.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        encoding = "utf-8-replace"
    return {
        "path": _rel(fp),
        "abs_path": str(fp),
        "size": fp.stat().st_size,
        "truncated": truncated,
        "encoding": encoding,
        "content": text,
    }


_LOG_ROOT = (WORKSPACE / "logs").resolve()
_LOG_SUFFIXES = {".log", ".jsonl"}
_LOG_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)((?:openai[_-]?api[_-]?key|api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def _redact_log_line(line: str) -> str | None:
    if "\x00" in line:
        return None
    out = line.rstrip("\r\n")
    try:
        for rx in _LOG_SECRET_PATTERNS:
            out = rx.sub(lambda m: m.group(1) + "[REDACTED]" if m.lastindex else "[REDACTED]", out)
    except Exception:
        return None
    # Fail closed if common credential forms survived redaction.
    if re.search(r"(?i)authorization\s*[:=]\s*bearer\s+[^\s,;]+", out):
        return None
    if re.search(r"\bsk-[A-Za-z0-9_-]{8,}\b", out):
        return None
    return out[:4000]


def exec_search_log(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    allowed = {"path", "pattern", "max_lines"}
    extra = set(params) - allowed
    if extra:
        raise BridgeError(INVALID_REQUEST, f"unknown params: {', '.join(sorted(extra))}", http_status=400)
    raw = str(params.get("path") or "").strip()
    pattern = str(params.get("pattern") or "")
    if not raw or not pattern:
        raise BridgeError(INVALID_REQUEST, "path and pattern required", http_status=400)
    if len(pattern) > 200 or "\x00" in pattern:
        raise BridgeError(INVALID_REQUEST, "pattern must be 1..200 safe text characters", http_status=400)
    try:
        max_lines = int(params.get("max_lines", 20))
    except (TypeError, ValueError):
        raise BridgeError(INVALID_REQUEST, "max_lines must be an integer", http_status=400)
    if not 1 <= max_lines <= 20:
        raise BridgeError(INVALID_REQUEST, "max_lines must be between 1 and 20", http_status=400)
    norm = raw.replace("\\", "/")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise BridgeError(PATH_TRAVERSAL, "path traversal blocked", http_status=403)
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = ALLOWED_ROOT / candidate
    # Reject symlinks in every existing component before resolving.
    cur = Path(candidate.anchor) if candidate.is_absolute() else ALLOWED_ROOT
    for part in candidate.parts[1:] if candidate.is_absolute() else candidate.parts:
        cur = cur / part
        if cur.exists() and cur.is_symlink():
            raise BridgeError(PATH_DENIED, "symlink log path blocked", http_status=403)
    try:
        fp = candidate.resolve(strict=True)
    except Exception as e:
        raise BridgeError(INVALID_REQUEST, f"log path not found: {e}", http_status=404) from e
    root_s, fp_s = str(_LOG_ROOT), str(fp)
    if not fp_s.startswith(root_s + os.sep):
        raise BridgeError(PATH_DENIED, "log path outside workspace/logs", http_status=403)
    if not fp.is_file() or fp.suffix.lower() not in _LOG_SUFFIXES:
        raise BridgeError(PATH_DENIED, "only .log/.jsonl files under workspace/logs are allowed", http_status=403)
    matches: deque[dict[str, Any]] = deque(maxlen=max_lines)
    needle = pattern.casefold()
    with fp.open("r", encoding="utf-8", errors="replace", buffering=64 * 1024) as handle:
        for line_no, line in enumerate(handle, 1):
            if needle not in line.casefold():
                continue
            clean = _redact_log_line(line)
            if clean is None:
                matches.append({"line": line_no, "text": "[REDACTED_UNSAFE_LINE]"})
            else:
                matches.append({"line": line_no, "text": clean})
    return {"path": _rel(fp), "pattern": pattern, "max_lines": max_lines,
            "matches": list(matches), "count": len(matches), "streaming": True,
            "redaction": "fail-closed"}


def exec_list_files(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    path = params.get("path") or params.get("dir") or "."
    fp = _safe_path(str(path), must_exist=True)
    if not fp.is_dir():
        raise BridgeError(INVALID_REQUEST, "not a directory", http_status=400)
    try:
        children = sorted(fp.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except Exception as e:
        raise BridgeError(INVALID_REQUEST, f"list failed: {e}", http_status=400) from e
    entries = []
    for child in children[:_MAX_LIST]:
        try:
            st = child.stat()
            entries.append({
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": st.st_size if child.is_file() else None,
            })
        except Exception:
            entries.append({"name": child.name, "type": "unknown"})
    return {
        "path": _rel(fp) if _rel(fp) != "." else "",
        "count": len(entries),
        "truncated": len(children) > _MAX_LIST,
        "entries": entries,
    }


def exec_write_file(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    path = params.get("path") or params.get("file") or ""
    content = params.get("content")
    if content is None:
        raise BridgeError(INVALID_REQUEST, "content required", http_status=400)
    if isinstance(content, str):
        data = content.encode("utf-8")
    elif isinstance(content, (bytes, bytearray)):
        data = bytes(content)
    else:
        data = str(content).encode("utf-8")
    if len(data) > _MAX_WRITE:
        raise BridgeError(INVALID_REQUEST, f"content exceeds {_MAX_WRITE} bytes", http_status=413)
    fp = _safe_path(str(path), allow_new=True)
    if fp.exists() and fp.is_dir():
        raise BridgeError(INVALID_REQUEST, "path is a directory", http_status=400)
    backup = _backup_before_write(fp)
    _atomic_write(fp, data)
    return {"path": _rel(fp), "bytes": len(data), "atomic": True, "backup_snapshot": backup}


def exec_create_file(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    path = params.get("path") or params.get("file") or ""
    fp = _safe_path(str(path), allow_new=True)
    if fp.exists():
        raise BridgeError(INVALID_REQUEST, "already exists", http_status=409)
    content = params.get("content", "")
    data = content.encode("utf-8") if isinstance(content, str) else bytes(content or b"")
    if len(data) > _MAX_WRITE:
        raise BridgeError(INVALID_REQUEST, f"content exceeds {_MAX_WRITE} bytes", http_status=413)
    _atomic_write(fp, data)
    return {"path": _rel(fp), "bytes": len(data), "created": True}


def exec_create_dir(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    path = params.get("path") or params.get("dir") or ""
    fp = _safe_path(str(path), allow_new=True)
    fp.mkdir(parents=True, exist_ok=True)
    return {"path": _rel(fp), "created": True, "is_dir": True}


def exec_patch_file(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    path = params.get("path") or params.get("file") or ""
    fp = _safe_path(str(path), must_exist=True)
    if not fp.is_file():
        raise BridgeError(INVALID_REQUEST, "not a file", http_status=400)
    # Canonical external schema: old_text/new_text. Legacy find/replace is
    # accepted internally for backwards compatibility only.
    find = params.get("old_text")
    replace = params.get("new_text")
    if find is None and "find" in params:
        find = params.get("find")
    if replace is None and "replace" in params:
        replace = params.get("replace")
    if find is None or replace is None:
        raise BridgeError(INVALID_REQUEST, "old_text and new_text required", http_status=400)
    text = fp.read_text(encoding="utf-8")
    count = text.count(str(find))
    if count == 0:
        return {"path": _rel(fp), "replacements": 0, "changed": False}
    new = text.replace(str(find), str(replace))
    data = new.encode("utf-8")
    if len(data) > _MAX_WRITE:
        raise BridgeError(INVALID_REQUEST, f"patched content exceeds {_MAX_WRITE} bytes", http_status=413)
    backup = _backup_before_write(fp)
    _atomic_write(fp, data)
    return {"path": _rel(fp), "replacements": count, "changed": True, "bytes": len(data),
            "backup_snapshot": backup}


def exec_move_file(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    src = _safe_path(str(params.get("src") or params.get("from") or ""), must_exist=True)
    dst = _safe_path(str(params.get("dst") or params.get("to") or ""), allow_new=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"src": _rel(src), "dst": _rel(dst), "moved": True}


def exec_copy_file(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    # Canonical external schema: path/destination. Keep legacy aliases internally.
    source_value = params.get("path") or params.get("src") or params.get("from") or ""
    destination_value = params.get("destination") or params.get("dst") or params.get("to") or ""
    if not str(source_value).strip():
        raise BridgeError(INVALID_REQUEST, "SOURCE_MISSING", http_status=400)
    if not str(destination_value).strip():
        raise BridgeError(INVALID_REQUEST, "DESTINATION_MISSING", http_status=400)
    try:
        src = _safe_path(str(source_value), must_exist=True)
    except BridgeError as e:
        if e.code == INVALID_REQUEST:
            raise BridgeError(INVALID_REQUEST, "SOURCE_NOT_FOUND", http_status=404) from e
        raise
    dst = _safe_path(str(destination_value), allow_new=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if src.is_dir():
            shutil.copytree(str(src), str(dst), dirs_exist_ok=bool(params.get("exist_ok")))
        else:
            shutil.copy2(str(src), str(dst))
    except Exception as e:
        raise BridgeError(INVALID_REQUEST, f"COPY_FAILED: {e}", http_status=500) from e
    return {"path": _rel(src), "destination": _rel(dst), "copied": True}


def exec_delete_file(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    fp = _safe_path(str(params.get("path") or params.get("file") or ""), must_exist=True)
    confirm = bool(params.get("confirm"))
    if not confirm:
        raise BridgeError(INVALID_REQUEST, "confirm=true required for delete", http_status=400)
    rel = _rel(fp)
    if fp.is_dir():
        if not bool(params.get("recursive")):
            raise BridgeError(INVALID_REQUEST, "recursive=true required for directories", http_status=400)
        shutil.rmtree(str(fp))
        kind = "dir"
    else:
        backup = _backup_before_write(fp)
        fp.unlink()
        kind = "file"
    result = {"path": rel, "deleted": True, "kind": kind}
    if kind == "file":
        result["backup_snapshot"] = backup
    return result


def _git(args: list[str], timeout: int = 60) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(ALLOWED_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except FileNotFoundError:
        return {"available": False, "error": "git not found", "root": str(ALLOWED_ROOT)}
    except Exception as e:
        return {"available": False, "error": str(e), "root": str(ALLOWED_ROOT)}
    return {
        "available": True,
        "root": str(ALLOWED_ROOT),
        "args": args,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[:20000],
        "stderr": (proc.stderr or "")[:4000],
    }


def exec_git_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    return _git(["status", "--porcelain=v1", "-b"])


def exec_git_diff(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    args = ["diff"]
    if params.get("staged"):
        args.append("--cached")
    path = params.get("path")
    if path:
        fp = _safe_path(str(path), must_exist=False)
        args.extend(["--", str(fp)])
    return _git(args)


def exec_git_log(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    n = int(params.get("n") or params.get("limit") or 10)
    n = max(1, min(n, 50))
    return _git(["log", f"-{n}", "--oneline", "--no-decorate"])


def exec_git_add(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    paths = params.get("paths") or params.get("path") or []
    if isinstance(paths, str):
        paths = [paths]
    if not paths:
        raise BridgeError(INVALID_REQUEST, "paths required", http_status=400)
    resolved = []
    for p in paths:
        fp = _safe_path(str(p), must_exist=True)
        resolved.append(str(fp))
    return _git(["add", "--", *resolved])


def exec_git_commit(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    msg = (params.get("message") or params.get("msg") or "").strip()
    if not msg:
        raise BridgeError(INVALID_REQUEST, "commit message required", http_status=400)
    if len(msg) > 500:
        raise BridgeError(INVALID_REQUEST, "message too long", http_status=400)
    return _git(["commit", "-m", msg])


def exec_git_push(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    remote = (params.get("remote") or "origin").strip()
    branch = (params.get("branch") or "").strip()
    args = ["push", remote]
    if branch:
        args.append(branch)
    return _git(args, timeout=120)


def exec_request_approval(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """Create a pending approval for an existing PRIVILEGED task only."""
    allowed = {"task", "params", "reason"}
    extra = set(params) - allowed
    if extra:
        raise BridgeError(INVALID_REQUEST, f"unknown params: {', '.join(sorted(extra))}", http_status=400)
    task = params.get("task")
    if not isinstance(task, str) or not task.strip():
        raise BridgeError(INVALID_REQUEST, "task required", http_status=400)
    task = task.strip()
    target_params = params.get("params", {})
    if not isinstance(target_params, dict):
        raise BridgeError(INVALID_REQUEST, "params must be an object", http_status=400)
    reason = params.get("reason", "")
    if not isinstance(reason, str):
        raise BridgeError(INVALID_REQUEST, "reason must be a string", http_status=400)
    if len(reason) > 1000:
        raise BridgeError(INVALID_REQUEST, "reason too long", http_status=400)
    if task not in TASK_PERMISSIONS or TASK_PERMISSIONS[task] != PermissionLevel.PRIVILEGED:
        raise BridgeError(INVALID_REQUEST, "task must be a registered PRIVILEGED task", http_status=400)
    from nexus.bridge.approvals import approvals
    approval = approvals().request(task, target_params, reason=reason)
    # ApprovalStore.request always creates pending records; fail closed if that invariant changes.
    if approval.status != "pending":
        raise BridgeError(ACTION_DENIED, "approval request did not enter pending state", http_status=500)
    return {"approval_id": approval.approval_id, "task": approval.task, "status": approval.status,
            "created_at": approval.created_at, "expires_at": approval.expires_at, "ui": "/approvals"}


def exec_capability_check(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    return {
        "protocol": "NEXUS_AGENT_BRIDGE_PROTOCOL",
        "version": "1.1.0",
        "permissions": [p.value for p in PermissionLevel],
        "process_level": process_level().value,
        "tasks": [
            {"name": n, "permission": TASK_PERMISSIONS[n].value}
            for n in REGISTERED_TASKS
        ],
        "executors": {
            "PowerShell": True,
            "Python": True,
            "File": True,
            "Git": True,
            "System": True,
            "Approval": True,
        },
        "path_root": str(ALLOWED_ROOT),
        "notes": "Master: SAFE_WRITE live; PRIVILEGED needs local approval_id; no arbitrary shell.",
    }


def exec_python_run_project_script(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    rel = (params.get("script") or params.get("path") or "").strip()
    if not rel:
        raise BridgeError(INVALID_REQUEST, "script required", http_status=400)
    fp = _safe_path(rel, must_exist=True)
    allowed_prefixes = [
        str((WORKSPACE / "scripts").resolve()),
        str((WORKSPACE / "projects").resolve()),
    ]
    fps = str(fp.resolve())
    if not any(fps == p or fps.startswith(p + os.sep) for p in allowed_prefixes):
        raise BridgeError(SCRIPT_DENIED, "script must live under workspace/scripts or workspace/projects", http_status=403)
    if fp.suffix.lower() != ".py":
        raise BridgeError(SCRIPT_DENIED, "only .py scripts allowed", http_status=403)
    args = params.get("args") or []
    if not isinstance(args, list):
        raise BridgeError(INVALID_REQUEST, "args must be a list", http_status=400)
    argv = [str(a) for a in args][:20]
    timeout = min(int(params.get("timeout") or 60), 180)
    # Use an explicit process-group/job boundary instead of subprocess.run(). On
    # Windows, TimeoutExpired from run() can leave descendants (Edge/driver/etc.)
    # alive while pipe-reader cleanup waits indefinitely. A dedicated process
    # group lets us terminate the registered script tree deterministically.
    cmd = [os.environ.get("PYTHON_EXE") or "python", str(fp), *argv]
    # NEXUS-Terminal (20261031): Hermes-Skript laeuft live ueber das Terminal (Prozessgruppe,
    # taskkill /T bei Timeout, Rechtestufe, Erfolgspruefung, Audit). Rueckgabeformat unveraendert.
    from nexus.terminal import service as _term
    _disp = " ".join(["python", str(fp), *argv])
    _r = _term.execute(_disp, agent="Hermes", shell="argv", argv=cmd, cwd=str(ALLOWED_ROOT), timeout=timeout, wait=True)
    if _r.get("status") in ("BLOCKED", "LEVEL_DENIED", "APPROVAL_REQUIRED"):
        raise BridgeError(SCRIPT_DENIED, "terminal: " + str(_r.get("error") or _r.get("status")), http_status=403,
                          details={"terminal_run": _r.get("run"), "approval_id": _r.get("approval_id")})
    if _r.get("status") == "TIMEOUT":
        raise BridgeError(INVALID_REQUEST, f"project script timed out after {timeout}s; process tree terminated",
                          http_status=408, details={"script": _rel(fp), "timeout": timeout,
                                                   "child_pid": _r.get("pid"), "terminated": True})
    if _r.get("status") in ("ERROR", "CANCELLED") or _r.get("returncode") is None:
        raise BridgeError(INVALID_REQUEST, f"script failed to start: {_r.get('error') or _r.get('status')}", http_status=500)
    return {
        "script": _rel(fp),
        "returncode": _r.get("returncode"),
        "stdout": (_r.get("stdout") or "")[:20000],
        "stderr": (_r.get("stderr") or "")[:4000],
        "timeout": timeout,
        "child_pid": _r.get("pid"),
        "terminal_run": _r.get("run"),
        "verdict": _r.get("verdict"),
    }


def exec_hermes_check_code(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    from nexus.bridge.code_sandbox import check_sources, SandboxError

    rel = str(params.get("path") or "").strip().replace("\\", "/")
    if not rel:
        raise BridgeError(INVALID_REQUEST, "path required", http_status=400)
    try:
        return check_sources(ALLOWED_ROOT, rel)
    except SandboxError as exc:
        raise BridgeError(ACTION_DENIED, str(exc), http_status=403) from None


def exec_hermes_run_code_tests(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    from nexus.bridge.code_sandbox import run_code_tests, SandboxError

    rel = str(params.get("path") or "").strip().replace("\\", "/")
    if not rel:
        raise BridgeError(INVALID_REQUEST, "path required", http_status=400)
    timeout = params.get("timeout")
    if timeout is not None and (type(timeout) is not int or not 1 <= timeout <= 60):
        raise BridgeError(INVALID_REQUEST, "timeout must be an integer between 1 and 60", http_status=400)
    try:
        _res = run_code_tests(ALLOWED_ROOT, rel, timeout=timeout)
    except SandboxError as exc:
        raise BridgeError(ACTION_DENIED, "code execution refused: " + str(exc), http_status=403) from None
    try:  # NEXUS-Terminal (20261031): nur protokollieren
        from nexus.terminal.service import record as _trec
        _d = _res if isinstance(_res, dict) else {}
        _trec("Hermes", "run_code_tests " + rel, cwd=ALLOWED_ROOT, rc=_d.get("returncode", _d.get("rc")),
              stdout=str(_d.get("stdout") or "")[-6000:], stderr=str(_d.get("stderr") or "")[-3000:], kind="code_tests",
              ok=_d.get("success", _d.get("ok")))
    except Exception:
        pass
    return _res


def _load_ps_registry() -> dict[str, Any]:
    _ACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    if not _ACTIONS_REGISTRY.is_file():
        default = {
            "echo_host": {
                "description": "Echo computer name (safe demo)",
                "script": "echo_host.ps1",
            },
            "nexus_health_probe": {
                "description": "Probe local Nexus health",
                "script": "nexus_health_probe.ps1",
            },
        }
        _ACTIONS_REGISTRY.write_text(json.dumps(default, indent=2), encoding="utf-8")
        (_ACTIONS_DIR / "echo_host.ps1").write_text(
            'Write-Output ("HOST=" + $env:COMPUTERNAME)\n', encoding="utf-8"
        )
        (_ACTIONS_DIR / "nexus_health_probe.ps1").write_text(
            'try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:8299/api/health" -UseBasicParsing -TimeoutSec 5; "CODE=$($r.StatusCode)" } catch { "ERR=$($_.Exception.Message)" }\n',
            encoding="utf-8",
        )
    try:
        return json.loads(_ACTIONS_REGISTRY.read_text(encoding="utf-8"))
    except Exception as e:
        raise BridgeError(ACTION_DENIED, f"registry unreadable: {e}", http_status=500) from e


_KRITA_PORTABLE_URL = "https://download.kde.org/stable/krita/5.3.4/krita-x64-5.3.4.zip"
_KRITA_PORTABLE_SHA256 = ""  # fail closed until official digest is pinned by a reviewed SAFE_WRITE update
_KRITA_STUDIO = (ALLOWED_ROOT / "tools" / "avatar-studio").resolve()

def exec_install_krita_portable(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """Install one pinned official Krita portable archive into the fixed G: project path.

    PRIVILEGED by registry. No configurable URL/destination. Fails closed until an
    official SHA-256 is pinned. Extraction rejects absolute/traversal paths and
    symlink-like ZIP entries. TEMP/TMP/HOME/XDG/cache dirs are redirected to G:.
    Krita is never launched by this task.
    """
    import hashlib, urllib.request, zipfile
    extra=set(params)-{"approval_id"}
    if extra: raise BridgeError(INVALID_REQUEST,"install_krita_portable accepts no configurable parameters",http_status=400)
    target=_KRITA_STUDIO
    root_s=str(ALLOWED_ROOT); target_s=str(target)
    if not target_s.startswith(root_s+os.sep): raise BridgeError(PATH_DENIED,"fixed Krita target outside project root",http_status=403)
    if not _KRITA_PORTABLE_SHA256 or not re.fullmatch(r"[0-9a-fA-F]{64}",_KRITA_PORTABLE_SHA256):
        raise BridgeError(ACTION_DENIED,"official Krita SHA-256 not pinned; installation blocked",http_status=409)
    target.mkdir(parents=True,exist_ok=True)
    temp=target/"temp"; cache=target/"cache"; profile=target/"profile"; app=target/"krita"
    for d in (temp,cache,profile,app): d.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy(); env.update({"TEMP":str(temp),"TMP":str(temp),"HOME":str(profile),"USERPROFILE":str(profile),"XDG_CACHE_HOME":str(cache),"XDG_CONFIG_HOME":str(profile/"config"),"XDG_DATA_HOME":str(profile/"data")})
    # Refuse execution if inherited Windows roots point outside G:. We do not mutate them.
    c_refs={k:v for k,v in env.items() if k in {"APPDATA","LOCALAPPDATA"} and v and not str(v).lower().startswith(str(ALLOWED_ROOT.drive).lower())}
    if c_refs: raise BridgeError(ACTION_DENIED,"unavoidable user-profile C: paths detected before first launch; explicit exception required",http_status=409,details={"variables":sorted(c_refs)})
    archive=temp/"krita-portable.zip"
    req=urllib.request.Request(_KRITA_PORTABLE_URL,headers={"User-Agent":"NEXUS-Krita-Installer/1.0"})
    with urllib.request.urlopen(req,timeout=120) as resp, archive.open("wb") as out:
        final=str(resp.geturl());
        if not (final.startswith("https://download.kde.org/") or final.startswith("https://mirror.kumi.systems/kde/")):
            raise BridgeError(ACTION_DENIED,"Krita download redirected outside approved KDE source",http_status=403)
        shutil.copyfileobj(resp,out)
    digest=_sha256(archive)
    if digest.lower()!=_KRITA_PORTABLE_SHA256.lower():
        archive.unlink(missing_ok=True); raise BridgeError(ACTION_DENIED,"Krita archive SHA-256 mismatch",http_status=409)
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name=info.filename.replace("\\","/")
            parts=[p for p in name.split("/") if p not in ("",".")]
            if name.startswith("/") or re.match(r"^[A-Za-z]:",name) or any(p==".." for p in parts):
                raise BridgeError(PATH_TRAVERSAL,"unsafe Krita archive member",http_status=403)
            mode=(info.external_attr>>16)&0o170000
            if mode==0o120000: raise BridgeError(ACTION_DENIED,"Krita archive symlink blocked",http_status=403)
            dest=(app/Path(*parts)).resolve() if parts else app
            if not (str(dest)==str(app) or str(dest).startswith(str(app)+os.sep)):
                raise BridgeError(PATH_TRAVERSAL,"Krita archive member escapes target",http_status=403)
        zf.extractall(app)
    return {"status":"PASS","version":"5.3.4","source":"download.kde.org","target":_rel(target),"sha256":digest,"krita_launched":False,"c_write_exception":False}


_KRITA_GUI_EXE = (_KRITA_STUDIO / "krita" / "krita-x64-5.3.4.1" / "bin" / "krita.exe").resolve()


def exec_krita_gui_test(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """Launch only the fixed local Krita executable for the approved GUI test."""
    extra = set(params) - {"approval_id"}
    if extra:
        raise BridgeError(INVALID_REQUEST, "krita_gui_test accepts no configurable parameters", http_status=400)
    exe = _KRITA_GUI_EXE
    expected_root = (_KRITA_STUDIO / "krita").resolve()
    if not (str(exe).startswith(str(expected_root) + os.sep) and exe.name.lower() == "krita.exe"):
        raise BridgeError(PATH_DENIED, "fixed Krita executable escaped approved G: root", http_status=403)
    if not exe.is_file():
        raise BridgeError(INVALID_REQUEST, "fixed Krita executable missing", http_status=404)
    runtime = _KRITA_STUDIO / "runtime"
    dirs = {
        "TEMP": runtime / "temp", "TMP": runtime / "temp",
        "HOME": runtime / "profile", "USERPROFILE": runtime / "profile",
        "XDG_CACHE_HOME": runtime / "cache", "XDG_CONFIG_HOME": runtime / "config",
        "XDG_DATA_HOME": runtime / "data",
    }
    for fp in dirs.values():
        fp.mkdir(parents=True, exist_ok=True)
        if not str(fp.resolve()).startswith(str(_KRITA_STUDIO) + os.sep):
            raise BridgeError(PATH_DENIED, "Krita runtime path outside approved G: studio", http_status=403)
    env = os.environ.copy()
    env.update({k: str(v.resolve()) for k, v in dirs.items()})
    # APPDATA/LOCALAPPDATA are deliberately not rewritten: 005D.57M permits only Krita's
    # normal unavoidable per-user config/cache writes there. No other C: path is configured.
    try:
        proc = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env, shell=False, close_fds=True)
    except Exception as exc:
        raise BridgeError(INVALID_REQUEST, f"fixed Krita launch failed: {exc}", http_status=500) from exc
    return {"status": "STARTED", "pid": proc.pid, "executable": _rel(exe),
            "runtime_root": _rel(runtime), "config_exception": "APPDATA/LOCALAPPDATA per 005D.57M only"}


def exec_powershell_registered_action(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    action = (params.get("action") or params.get("name") or "").strip()
    if not action:
        raise BridgeError(INVALID_REQUEST, "action required", http_status=400)
    reg = _load_ps_registry()
    if action not in reg:
        raise BridgeError(
            ACTION_DENIED,
            f"unknown action '{action}'",
            http_status=403,
            details={"registered": list(reg.keys())},
        )
    entry = reg[action]
    script_name = entry.get("script") or ""
    sp = (_ACTIONS_DIR / script_name).resolve()
    if not str(sp).startswith(str(_ACTIONS_DIR.resolve()) + os.sep) and sp != _ACTIONS_DIR.resolve():
        raise BridgeError(ACTION_DENIED, "script outside actions dir", http_status=403)
    if not sp.is_file():
        raise BridgeError(ACTION_DENIED, f"script missing: {script_name}", http_status=404)
    timeout = min(int(params.get("timeout") or 60), 120)
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(sp)],
            cwd=str(ALLOWED_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except Exception as e:
        raise BridgeError(INVALID_REQUEST, f"powershell failed: {e}", http_status=500) from e
    return {
        "action": action,
        "script": script_name,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[:20000],
        "stderr": (proc.stderr or "")[:4000],
    }


def _service_query(name: str) -> dict[str, Any]:
    try:
        safe = name.replace("'", "")
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"Get-Service -Name '{safe}' | Select-Object Name,Status,StartType | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=20, shell=False, check=False,
        )
        return {"returncode": proc.returncode, "stdout": (proc.stdout or "")[:4000], "stderr": (proc.stderr or "")[:1000]}
    except Exception as e:
        return {"error": str(e)}


_ALLOWED_SERVICES = {"Tailscale", "Ollama"}


def exec_service_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    name = (params.get("name") or params.get("service") or "").strip()
    if not name:
        out = {}
        for n in sorted(_ALLOWED_SERVICES):
            out[n] = _service_query(n)
        return {"services": out, "allowed": sorted(_ALLOWED_SERVICES)}
    if name not in _ALLOWED_SERVICES:
        raise BridgeError(ACTION_DENIED, f"service not in allowlist: {name}", http_status=403,
                          details={"allowed": sorted(_ALLOWED_SERVICES)})
    return {"name": name, **_service_query(name)}


def _service_control(name: str, verb: str) -> dict[str, Any]:
    if name not in _ALLOWED_SERVICES:
        raise BridgeError(ACTION_DENIED, f"service not in allowlist: {name}", http_status=403,
                          details={"allowed": sorted(_ALLOWED_SERVICES)})
    cmd_map = {"start": "Start-Service", "stop": "Stop-Service", "restart": "Restart-Service"}
    cmd = cmd_map[verb]
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"{cmd} -Name '{name}'; Get-Service -Name '{name}' | Select-Object Name,Status | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=60, shell=False, check=False,
        )
        return {"name": name, "verb": verb, "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[:4000], "stderr": (proc.stderr or "")[:1000]}
    except Exception as e:
        raise BridgeError(INVALID_REQUEST, str(e), http_status=500) from e


def exec_service_start(params, rec):
    return _service_control((params.get("name") or "").strip(), "start")


def exec_service_stop(params, rec):
    return _service_control((params.get("name") or "").strip(), "stop")


def exec_service_restart(params, rec):
    return _service_control((params.get("name") or "").strip(), "restart")


def exec_nexus_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    import urllib.request
    import urllib.error
    out: dict[str, Any] = {"root": str(ALLOWED_ROOT), "port": Config.PORT}
    try:
        with urllib.request.urlopen("http://127.0.0.1:8299/api/health", timeout=5) as resp:
            out["health"] = json.loads(resp.read().decode("utf-8"))
            out["origin"] = "UP"
    except Exception as e:
        out["origin"] = "DOWN"
        out["health_error"] = type(e).__name__
    try:
        with urllib.request.urlopen("http://127.0.0.1:8300/api/bridge/health", timeout=3) as resp:
            out["gateway"] = "UP"
            out["gateway_code"] = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        if e.code in (401, 200):
            out["gateway"] = "UP"
            out["gateway_code"] = e.code
        else:
            out["gateway"] = "DOWN"
            out["gateway_code"] = e.code
    except Exception:
        out["gateway"] = "DOWN"
    out["process_level"] = process_level().value
    return out


def exec_nexus_control_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    import urllib.request
    out = exec_nexus_status(params, rec)
    out["pid"] = None
    out["pid_source"] = "workspace/logs/nexus.pid (master process)"
    out["bridge_pid"] = os.getpid()
    out["bridge_pid_source"] = "current Agent Bridge executor process"
    try:
        pf = WORKSPACE / "logs" / "nexus.pid"
        if pf.is_file():
            out["pid"] = int(pf.read_text(encoding="utf-8").strip())
    except Exception:
        pass
    out["tailscale"] = exec_service_status({"name": "Tailscale"}, rec)
    out["public_url"] = None
    try:
        uf = WORKSPACE / "bridge" / "tunnel.url"
        if uf.is_file():
            out["public_url"] = uf.read_text(encoding="utf-8").strip()
            with urllib.request.urlopen(out["public_url"] + "/api/bridge/health", timeout=5) as resp:
                out["external"] = "UP"
                out["external_code"] = getattr(resp, "status", 200)
    except urllib.error.HTTPError as e:
        out["external"] = "UP" if e.code == 401 else "DOWN"
        out["external_code"] = e.code
    except Exception:
        out["external"] = "DOWN"
    return out


def _spawn_control(script_name: str, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    ps1 = ALLOWED_ROOT / script_name
    if not ps1.is_file():
        raise BridgeError(NOT_IMPLEMENTED, f"{script_name} missing", http_status=500)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", str(ps1)], cwd=str(ALLOWED_ROOT), shell=False, close_fds=True, creationflags=flags, env=env)
    except Exception as e:
        raise BridgeError(INVALID_REQUEST, f"control spawn failed: {e}", http_status=500) from e
    return {"scheduled": True, "via": script_name}


def exec_nexus_start(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    return _spawn_control("start_nexus_all.ps1")


def exec_nexus_stop(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    return _spawn_control("stop_nexus_all.ps1")


def exec_nexus_restart(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    # A bridge-triggered restart must not perpetuate a stale OpenAI credential
    # from the currently running NEXUS process.  Copy the environment for this
    # child only; never mutate os.environ or alter the start/stop paths.
    restart_env = os.environ.copy()
    restart_env.pop("OPENAI_API_KEY", None)
    return _spawn_control("restart_nexus_all.ps1", env=restart_env)


def exec_openai_diagnostic_once(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """One approved, fixed-cost-shape OpenAI connectivity diagnostic."""
    extra = set(params) - {"approval_id"}
    if extra:
        raise BridgeError(INVALID_REQUEST, f"openai_diagnostic_once accepts no configurable parameters: {', '.join(sorted(extra))}", http_status=400)
    if os.environ.get("NEXUS_ALLOW_PAID") != "1":  # Kostenregel: Bezahl-API nur explizit
        raise BridgeError(ACTION_DENIED, "paid API disabled (set NEXUS_ALLOW_PAID=1)", http_status=403)
    from nexus.master.openai_orchestrator import diagnose_openai_once
    result = diagnose_openai_once("gpt-5", "Reply only: OK")
    # Fail closed if the diagnostic contract ever drifts.
    if int(result.get("request_count") or 0) > 1:
        raise BridgeError(ACTION_DENIED, "diagnostic request-count invariant violated", http_status=500)
    if result.get("model") not in (None, "gpt-5"):
        raise BridgeError(ACTION_DENIED, "diagnostic model invariant violated", http_status=500)
    if result.get("bridge_fallback") not in (None, False):
        raise BridgeError(ACTION_DENIED, "diagnostic fallback invariant violated", http_status=500)
    return result


def exec_media_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    media = MEDIA_DIR
    if not media.is_dir():
        return {"media_dir": str(media), "files": 0, "ok": False, "error": "media directory missing"}
    try:
        count = sum(1 for p in media.rglob("*") if p.is_file())
    except Exception as exc:
        return {"media_dir": str(media), "files": None, "ok": False,
                "error": f"{type(exc).__name__}: {exc}"}
    return {"media_dir": str(media), "files": count, "ok": True}


def exec_voice_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    import importlib.util
    cfg = APEX / "voice_config.json"
    if not cfg.is_file():
        return {"ok": False, "voice_config": None, "error": "voice config missing",
                "tts_edge": False, "stt": False}
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "voice_config": None, "error": f"voice config unreadable: {type(exc).__name__}",
                "tts_edge": False, "stt": False}
    edge = importlib.util.find_spec("edge_tts") is not None
    whisper = importlib.util.find_spec("faster_whisper") is not None
    return {"ok": bool(edge and whisper), "voice_config": data,
            "tts_edge": edge, "stt": "faster-whisper" if whisper else False,
            "verification": {"config_read": True, "edge_tts_importable": edge,
                             "faster_whisper_importable": whisper}}


def exec_llm_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    import urllib.request
    ollama = {"reachable": False}
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name") for m in tags.get("models") or []]
            ollama = {"reachable": True, "models": models, "preferred": Config.PREFERRED_MODEL}
    except Exception as e:
        ollama = {"reachable": False, "error": type(e).__name__}
    openai_set = bool((os.environ.get("OPENAI_API_KEY") or "").strip())
    return {
        "routing": "local-first",
        "ollama": ollama,
        "openai_key_present": openai_set,
        "openai_required": False,
        "cost_rule": "LOCAL->FREE->NOT",
    }


def exec_termux_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    base = exec_android_status(params, rec)
    base["termux_note"] = "status via adb if device present"
    return base


def exec_android_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            ["adb", "devices", "-l"], capture_output=True, text=True, timeout=10, shell=False, check=False,
        )
        return {
            "adb": True,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[:4000],
            "stderr": (proc.stderr or "")[:1000],
        }
    except FileNotFoundError:
        return {"adb": False, "error": "adb not found"}
    except Exception as e:
        return {"adb": False, "error": str(e)}


def exec_android_devices(params, rec):
    return exec_android_status(params, rec)


def _adb_ro(params: dict[str, Any], args: list[str], timeout: int = 15) -> dict[str, Any]:
    device = str(params.get("device") or "").strip()
    cmd = ["adb"] + (["-s", device] if device else []) + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=False, check=False)
        return {"adb": True, "device": device or None, "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[:12000].strip(), "stderr": (proc.stderr or "")[:2000].strip()}
    except FileNotFoundError:
        return {"adb": False, "error": "adb not found"}
    except Exception as e:
        return {"adb": False, "error": str(e)}


def _adb_prop(params: dict[str, Any], prop: str) -> dict[str, Any]:
    out = _adb_ro(params, ["shell", "getprop", prop])
    out["property"] = prop
    out["value"] = out.get("stdout", "")
    return out


def exec_android_version(params, rec):
    return _adb_prop(params, "ro.build.version.release")


def exec_android_build(params, rec):
    return _adb_ro(params, ["shell", "getprop"])


def exec_android_security_patch(params, rec):
    return _adb_prop(params, "ro.build.version.security_patch")


def exec_android_fingerprint(params, rec):
    return _adb_prop(params, "ro.build.fingerprint")


def exec_android_slot(params, rec):
    return _adb_prop(params, "ro.boot.slot_suffix")


def exec_android_bootloader_status(params, rec):
    unlocked = _adb_prop(params, "ro.boot.flash.locked")
    vbmeta = _adb_prop(params, "ro.boot.verifiedbootstate")
    return {"flash_locked": unlocked.get("value"), "verified_boot_state": vbmeta.get("value"),
            "device": unlocked.get("device"), "adb": unlocked.get("adb", False)}


def exec_android_storage(params, rec):
    return _adb_ro(params, ["shell", "df", "-h", "/data", "/sdcard"])


def exec_android_battery(params, rec):
    return _adb_ro(params, ["shell", "dumpsys", "battery"])


def exec_android_network(params, rec):
    return _adb_ro(params, ["shell", "dumpsys", "connectivity"])


def exec_android_termux_status(params, rec):
    packages = _adb_ro(params, ["shell", "pm", "list", "packages", "com.termux"])
    return {"adb": packages.get("adb", False), "device": packages.get("device"),
            "installed": "package:com.termux" in packages.get("stdout", ""), "packages": packages.get("stdout", ""),
            "note": "read-only package presence check; no Termux HOME modification"}


def exec_android_screenshot(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    out = WORKSPACE / "screens" / "android_bridge.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            ["adb", "exec-out", "screencap", "-p"],
            capture_output=True, timeout=30, shell=False, check=False,
        )
        if proc.returncode != 0 or not proc.stdout:
            return {"ok": False, "stderr": (proc.stderr or b"")[:500].decode("utf-8", "replace")}
        out.write_bytes(proc.stdout)
        return {"ok": True, "path": _rel(out), "bytes": len(proc.stdout)}
    except FileNotFoundError:
        return {"ok": False, "error": "adb not found"}
    except Exception as e:
        return {"ok": False, "error": str(e)}



_PHASE_A_ROOT = (WORKSPACE / "NEXUS_ANDROID_FINAL").resolve()
_ANDROID_STAGE = "/sdcard/Download/NEXUS_TRANSFER"
_PHASE_A_SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".pytest_cache", ".mypy_cache"}
_PHASE_A_SKIP_NAMES = {".env", ".token", ".bridge_token", "assistant_token.txt", "openai.env"}
_PHASE_A_EXECUTABLES = {"start.sh", "stop.sh", "restart.sh", "status.sh"}


def _phase_rel(value: Any, label: str) -> str:
    raw = str(value or "").strip()
    if not raw or "\x00" in raw:
        raise BridgeError(INVALID_REQUEST, f"{label} required", http_status=400)
    norm = raw.replace("\\", "/")
    if norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
        raise BridgeError(PATH_DENIED, f"absolute {label} blocked", http_status=403)
    parts = [x for x in norm.split("/") if x not in ("", ".")]
    if not parts or any(x == ".." for x in parts):
        raise BridgeError(PATH_TRAVERSAL, f"{label} traversal blocked", http_status=403)
    return "/".join(parts)


def _phase_source(rel: str, *, directory: bool | None = None) -> Path:
    raw = Path(rel)
    # Source is strictly project-root relative under the fixed Phase-A allowlist.
    # Existing symlinks are rejected even when their resolved target would remain in-tree.
    cur = _PHASE_A_ROOT
    for part in raw.parts:
        cur = cur / part
        if cur.is_symlink():
            raise BridgeError(PATH_DENIED, "symlink source blocked", http_status=403)
    fp = (_PHASE_A_ROOT / rel).resolve()
    base = str(_PHASE_A_ROOT)
    if not (str(fp) == base or str(fp).startswith(base + os.sep)):
        raise BridgeError(PATH_DENIED, "source outside Phase A allowlist", http_status=403)
    if not fp.exists():
        raise BridgeError(INVALID_REQUEST, f"source not found: {rel}", http_status=404)
    if directory is True and not fp.is_dir():
        raise BridgeError(INVALID_REQUEST, "source is not a directory", http_status=400)
    if directory is False and not fp.is_file():
        raise BridgeError(INVALID_REQUEST, "source is not a file", http_status=400)
    return fp


def _phase_allowed_file(fp: Path) -> bool:
    low = fp.name.lower()
    if low in _PHASE_A_SKIP_NAMES or "secret" in low or "token" in low or "api_key" in low or "apikey" in low:
        return False
    return not any(part.lower() in _PHASE_A_SKIP_DIRS for part in fp.parts)


def _adb_device_cmd(params: dict[str, Any], args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    device = str(params.get("device") or "").strip()
    cmd = ["adb"] + (["-s", device] if device else []) + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=False, check=False)
    except FileNotFoundError as ex:
        raise BridgeError(NOT_IMPLEMENTED, "adb not found", http_status=500) from ex
    except subprocess.TimeoutExpired as ex:
        raise BridgeError(INVALID_REQUEST, "adb transfer timeout", http_status=500) from ex


def _sha256(fp: Path) -> str:
    import hashlib
    h=hashlib.sha256()
    with fp.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _push_one(params: dict[str, Any], source_rel: str, dest_rel: str, overwrite: bool) -> dict[str, Any]:
    src=_phase_source(source_rel, directory=False)
    if not _phase_allowed_file(src):
        raise BridgeError(PATH_DENIED, "secret/cache file blocked", http_status=403)
    dest=_phase_rel(dest_rel, "destination")
    remote=f"{_ANDROID_STAGE}/{dest}"
    parent=remote.rsplit("/",1)[0]
    mk=_adb_device_cmd(params,["shell","mkdir","-p",parent])
    if mk.returncode != 0:
        raise BridgeError(INVALID_REQUEST, "failed to create staging directory", http_status=500)
    if not overwrite:
        chk=_adb_device_cmd(params,["shell","test","!","-e",remote])
        if chk.returncode != 0:
            raise BridgeError(INVALID_REQUEST, "staging destination exists; overwrite=false", http_status=409)
    proc=_adb_device_cmd(params,["push",str(src),remote],timeout=180)
    if proc.returncode != 0:
        raise BridgeError(INVALID_REQUEST, "adb push failed", http_status=500, details={"stderr":(proc.stderr or "")[:1000]})
    size=src.stat().st_size
    digest=_sha256(src)
    verify=_adb_device_cmd(params,["shell","sha256sum",remote])
    verified=False
    if verify.returncode == 0 and verify.stdout.strip():
        verified=verify.stdout.strip().split()[0].lower()==digest
        if not verified:
            raise BridgeError(INVALID_REQUEST, "device hash mismatch", http_status=500)
    else:
        stat=_adb_device_cmd(params,["shell","stat","-c","%s",remote])
        if stat.returncode != 0 or stat.stdout.strip()!=str(size):
            raise BridgeError(INVALID_REQUEST, "device size verification failed", http_status=500)
    return {"status":"PASS","source":source_rel,"destination":dest,"size":size,"sha256":digest,"hash_verified":verified}


def exec_android_push_file(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    allowed={"source","destination","overwrite","device"}
    extra=set(params)-allowed
    if extra:
        raise BridgeError(INVALID_REQUEST,f"unknown params: {', '.join(sorted(extra))}",http_status=400)
    src=_phase_rel(params.get("source"),"source")
    dst=_phase_rel(params.get("destination"),"destination")
    return _push_one(params,src,dst,bool(params.get("overwrite",False)))


def exec_android_push_tree(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    allowed={"source","destination","overwrite","device"}
    extra=set(params)-allowed
    if extra:
        raise BridgeError(INVALID_REQUEST,f"unknown params: {', '.join(sorted(extra))}",http_status=400)
    src_rel=_phase_rel(params.get("source"),"source")
    dst_rel=_phase_rel(params.get("destination"),"destination")
    src=_phase_source(src_rel,directory=True)
    files=[]
    for fp in sorted(src.rglob("*")):
        if fp.is_symlink() or not fp.is_file() or not _phase_allowed_file(fp):
            continue
        rel=fp.relative_to(src).as_posix()
        files.append(_push_one(params,f"{src_rel}/{rel}",f"{dst_rel}/{rel}",bool(params.get("overwrite",False))))
    return {"status":"PASS","source":src_rel,"destination":dst_rel,"count":len(files),"files":files}


def _manifest_local() -> Path:
    return _PHASE_A_ROOT / "phase_a_manifest.json"


# ---- Phone control path (2026-10-06): the retired NEXUS_TERMUX_AGENT on retired-port is no longer used.
# All phone actions go to the Phase-A control service (Termux, 127.0.0.1:8766 on the phone)
# through an idempotent host-side adb forward tcp:18766 -> tcp:8766 (nexus_os.ensure_adb_forward).
_DEVICE_ERROR_CODES = {"NO_DEVICE", "DEVICE_UNAUTHORIZED", "DEVICE_OFFLINE", "SERIAL_NOT_FOUND", "ADB_NOT_FOUND"}
_PHASE_A_NOT_RUNNING = ("Phase-A control (127.0.0.1:8766 am Handy) antwortet nicht - Termux/Phase-A laeuft nicht. "
                        "Einmalig in Termux: bash ~/storage/downloads/NEXUS_TRANSFER/termux/bootstrap_termux.sh")


def _phase_a_forward(params: dict[str, Any]) -> int:
    from nexus import nexus_os
    device = str(params.get("device") or "").strip()
    if device:
        # An explicit target is a constraint, not a preferred device. The shared
        # status helper may otherwise fall back to another connected phone.
        devices = nexus_os.adb_devices()
        target = next((d for d in devices.get("devices", []) if d.get("serial") == device), None)
        code = None
        if not devices.get("adb"):
            code = "ADB_NOT_FOUND"
        elif target is None:
            code = "ADB_ERROR" if devices.get("error") == "ADB_ERROR" else "SERIAL_NOT_FOUND"
        elif target.get("state") != "device":
            code = "DEVICE_UNAUTHORIZED" if target.get("state") == "unauthorized" else "DEVICE_OFFLINE"
        if code:
            raise BridgeError(INVALID_REQUEST, "ADB target " + device + ": " + code,
                              http_status=503 if code in _DEVICE_ERROR_CODES else 500,
                              details={"code": code, "device": device, "actions": []})
        # Reuse the checked snapshot so selection cannot fall back on a second
        # device listing. Explicit requests never clean other legacy forwards.
        fw = nexus_os.ensure_adb_forward(nexus_os.PHASE_A_FORWARD_PORT, nexus_os.PHASE_A_REMOTE_PORT,
                                         device, devices=devices, cleanup_legacy=False)
        if fw.get("ok") and fw.get("serial") != device:
            raise BridgeError(INVALID_REQUEST, "ADB forward target does not match requested device",
                              http_status=503, details={"code": "ADB_TARGET_MISMATCH", "device": device})
    else:
        fw = nexus_os.ensure_adb_forward(nexus_os.PHASE_A_FORWARD_PORT, nexus_os.PHASE_A_REMOTE_PORT, device)
    if not fw.get("ok"):
        raise BridgeError(INVALID_REQUEST, str(fw.get("message") or "ADB forward to Termux agent failed"),
                          http_status=503 if fw.get("code") in _DEVICE_ERROR_CODES else 500,
                          details={"code": fw.get("code"), "actions": fw.get("actions")})
    return int(nexus_os.PHASE_A_FORWARD_PORT)


def _phase_a_health(params: dict[str, Any]) -> dict[str, Any] | None:
    import urllib.request
    port = _phase_a_forward(params)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data if data.get("service") == "NEXUS_PHASE_A_CONTROL" else None
    except Exception:
        return None


def _phase_a_post(params: dict[str, Any], action: str, timeout: float = 70) -> dict[str, Any]:
    import urllib.request, urllib.error
    port = _phase_a_forward(params)
    body = json.dumps({"action": action}).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{port}/action", data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        try: detail = json.loads(ex.read().decode("utf-8"))
        except Exception: detail = {"error": "Phase-A control request failed"}
        raise BridgeError(INVALID_REQUEST, str(detail.get("error") or "Phase-A control action failed"), http_status=500, details=detail) from ex
    except Exception as ex:
        raise BridgeError(INVALID_REQUEST, _PHASE_A_NOT_RUNNING, http_status=503) from ex


# legacy termux_* task -> Phase-A action (same permission levels as before)
_LEGACY_TERMUX_ACTIONS = {
    "file_status": "file_status",
    "install_phase_a": "install_package",
    "status_nexus": "status_nexus",
    "start_nexus": "health",          # if Phase-A answers it is running; starting needs Termux (boot/bootstrap)
    "restart_nexus": "restart_control",
    "stop_nexus": "stop_nexus",
}


def _termux_agent_request(params: dict[str, Any], action: str) -> dict[str, Any]:
    if action == "patch_nexus_start_agent":
        raise BridgeError(ACTION_DENIED, "obsolete: legacy agent retired; autostart via Phase-A autostart_ensure (~/.termux/boot/nexus-boot.sh)", http_status=410)
    target = _LEGACY_TERMUX_ACTIONS.get(action)
    if not target:
        raise BridgeError(ACTION_DENIED, "termux action denied", http_status=403)
    if target == "health":
        h = _phase_a_health(params)
        if not h:
            raise BridgeError(INVALID_REQUEST, _PHASE_A_NOT_RUNNING, http_status=503)
        return {"status": "PASS", "running": True, "version": h.get("version"), "pid": h.get("pid")}
    return _phase_a_post(params, target)


def exec_termux_file_status(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    extra=set(params)-{"device"}
    if extra: raise BridgeError(INVALID_REQUEST,f"unknown params: {', '.join(sorted(extra))}",http_status=400)
    return _termux_agent_request(params,"file_status")


def exec_termux_patch_nexus_start_agent(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    # Fixed repair only. No client path, content, command, or generic file proxy.
    extra=set(params)-{"device"}
    if extra: raise BridgeError(INVALID_REQUEST,f"unknown params: {', '.join(sorted(extra))}",http_status=400)
    return _termux_agent_request(params,"patch_nexus_start_agent")


def exec_termux_install_nexus_phase_a(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    extra=set(params)-{"device"}
    if extra: raise BridgeError(INVALID_REQUEST,f"unknown params: {', '.join(sorted(extra))}",http_status=400)
    return _termux_agent_request(params,"install_phase_a")


def exec_termux_start_nexus(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    return _termux_agent_request(params,"start_nexus")

def exec_termux_stop_nexus(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    return _termux_agent_request(params,"stop_nexus")

def exec_termux_restart_nexus(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    return _termux_agent_request(params,"restart_nexus")

def exec_termux_status_nexus(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    return _termux_agent_request(params,"status_nexus")

_PHASE_A_CONTROL_ACTIONS = {"health", "chat_e2e", "shared_room_e2e", "files_e2e", "memory_e2e", "wozipc_probe", "autostart_ensure", "device_control", "quick_actions", "agents_skills", "voice_status", "file_status", "status_nexus", "autostart_status"}

def exec_termux_phase_a_control(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    import urllib.request, urllib.error
    # Keep Bridge credentials on WoZiPC. The phone never receives or stores them.
    if str(params.get("action") or "").strip() == "agents_skills":
        from nexus.bridge.auth import get_bridge_token
        token = get_bridge_token()
        req = urllib.request.Request("http://127.0.0.1:8299/api/bridge/capabilities", headers={"Authorization":"Bearer "+token}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                caps=json.loads(r.read().decode("utf-8"))
            agents=((caps.get("agents") or {}).get("agents") or [])
            # Return only non-secret registry data; never proxy arbitrary paths or headers.
            return {"status":"PASS" if agents else "FAIL","agents":[a.get("id") for a in agents],"orchestrator":(caps.get("agents") or {}).get("orchestrator"),"skills_endpoint":caps.get("skills_endpoint"),"auth":"bridge-local-runtime"}
        except urllib.error.HTTPError as ex:
            return {"status":"FAIL","error":"AUTH" if ex.code in (401,403) else "HTTP","http":ex.code}
        except Exception as ex:
            return {"status":"FAIL","error":"NETWORK","type":type(ex).__name__}
    extra = set(params) - {"action", "device"}
    if extra:
        raise BridgeError(INVALID_REQUEST, f"unknown params: {', '.join(sorted(extra))}", http_status=400)
    action = str(params.get("action") or "").strip()
    if action not in _PHASE_A_CONTROL_ACTIONS:
        raise BridgeError(ACTION_DENIED, "phase-a control action denied", http_status=403, details={"allowed": sorted(_PHASE_A_CONTROL_ACTIONS)})
    if action == "health":
        h = _phase_a_health(params)
        if not h:
            raise BridgeError(INVALID_REQUEST, _PHASE_A_NOT_RUNNING, http_status=503)
        return h
    return _phase_a_post(params, action)

def exec_check_phase_a_stage_cleaned(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    if params:
        raise BridgeError(INVALID_REQUEST,"check_phase_a_stage_cleaned accepts no parameters",http_status=400)
    device="249ac5d7"
    path="/sdcard/Download/NEXUS_TRANSFER/phase_a_control.py"
    p=subprocess.run(["adb","-s",device,"shell","test","-e",path],capture_output=True,text=True,timeout=15,shell=False,check=False)
    stderr=(p.stderr or "").strip()
    if p.returncode not in (0, 1):
        return {"status":"FAILED","device":device,"path":path,"stage_exists":None,
                "stage_cleaned":False,"adb_returncode":p.returncode,"stderr":stderr}
    exists=p.returncode==0
    return {"status":"PASS","device":device,"path":path,"stage_exists":exists,
            "stage_cleaned":not exists,"adb_returncode":p.returncode,"stderr":stderr}


def exec_restart_nexus_bridge_exact(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """Restart only the fixed NEXUS Bridge process listening on TCP 8299."""
    if set(params)-{"approval_id"}:
        raise BridgeError(INVALID_REQUEST,"restart_nexus_bridge_exact accepts no parameters",http_status=400)
    import time, urllib.request
    exe=Path(r"C:\Users\gustl\AppData\Local\Python\pythoncore-3.14-64\python.exe").resolve()
    script=(ALLOWED_ROOT/"run_server.py").resolve()
    port=8299
    ps=(
        "$c=Get-NetTCPConnection -LocalPort 8299 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1;"
        "if($null -eq $c){exit 3};"
        "$p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$c.OwningProcess);"
        "$p | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    )
    q=subprocess.run(["powershell.exe","-NoProfile","-Command",ps],capture_output=True,text=True,timeout=15,shell=False,check=False)
    if q.returncode!=0 or not (q.stdout or "").strip():
        raise BridgeError(INVALID_REQUEST,"PROCESS_MISMATCH",http_status=409)
    try: info=json.loads(q.stdout)
    except Exception as ex: raise BridgeError(INVALID_REQUEST,"PROCESS_MISMATCH",http_status=409) from ex
    old_pid=int(info.get("ProcessId") or 0)
    actual_exe=str(info.get("ExecutablePath") or "").strip()
    cmdline=str(info.get("CommandLine") or "")
    if not old_pid or actual_exe.lower()!=str(exe).lower() or str(script).lower() not in cmdline.lower():
        raise BridgeError(INVALID_REQUEST,"PROCESS_MISMATCH",http_status=409)
    subprocess.Popen(
        ["powershell.exe","-NoProfile","-Command",
         f"Start-Sleep -Milliseconds 750; Stop-Process -Id {old_pid} -ErrorAction Stop; "
         f"Start-Process -FilePath '{str(exe).replace(chr(39),chr(39)*2)}' -ArgumentList '{str(script).replace(chr(39),chr(39)*2)}' -WorkingDirectory '{str(ALLOWED_ROOT).replace(chr(39),chr(39)*2)}'"],
        cwd=str(ALLOWED_ROOT),shell=False,close_fds=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),
    )
    # The current request process will be terminated by the delayed helper, so
    # successful post-restart health/new PID are verified by the caller afterward.
    return {"status":"PASS","old_pid":old_pid,"new_pid":None,"bridge_health":"RESTARTING","script":str(script),"port":port}


def exec_deploy_phase_a_control(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """Deploy the manifest-listed Phase-A package WITHOUT Android run-as (2026-10-06).

    1) local integrity: every file must match phase_a_manifest.json (else: rebuild manifest)
    2) stage via adb push to /sdcard/Download/NEXUS_TRANSFER (+ manifest, bootstrap) - hash verified
    3) running Phase-A installs it into ~/nexus itself (install_package; old agents: install_phase_a)
    4) restart_control (same PID, new code) -> /health code_sha256 must equal the local file hash
    5) autostart_ensure (single Termux:Boot entry) -> autostart_status
    If Phase-A is not running the package stays staged and the one manual bootstrap step is returned.
    No free paths, no generic shell, no secrets (token files are blocked by _phase_allowed_file)."""
    import time, urllib.request
    extra=set(params)-{"device","approval_id"}
    if extra: raise BridgeError(INVALID_REQUEST,f"unknown params: {', '.join(sorted(extra))}",http_status=400)
    push_params={"device":str(params.get("device") or "").strip()} if params.get("device") else {}
    mf=_manifest_local()
    if not mf.is_file(): raise BridgeError(INVALID_REQUEST,"phase_a_manifest.json missing",http_status=404)
    manifest=json.loads(mf.read_text(encoding="utf-8"))
    files=[str(it.get("path") or "") for it in manifest.get("files") or []]
    for it in manifest.get("files") or []:
        fp=_phase_source(_phase_rel(it.get("path"),"manifest path"),directory=False)
        if fp.stat().st_size!=int(it.get("size",-1)) or _sha256(fp)!=it.get("sha256"):
            raise BridgeError(INVALID_REQUEST,f"local manifest stale for {it.get('path')} - run workspace/scripts/build_phase_a_manifest.py",http_status=409)
    # 2) stage (device errors from adb surface as clear BridgeErrors)
    fw_check=_phase_a_forward(params)  # also validates device presence/authorization with a clear message
    staged=[_push_one(push_params,rel,rel,True)["destination"] for rel in files]
    staged.append(_push_one(push_params,"phase_a_manifest.json","phase_a_manifest.json",True)["destination"])
    staged.append(_push_one(push_params,"termux/bootstrap_termux.sh","termux/bootstrap_termux.sh",True)["destination"])
    local_sha=_sha256(_PHASE_A_ROOT/"backend"/"phase_a_control.py")
    result={"status":"PENDING","staged":len(staged),"stage":_ANDROID_STAGE,"local_code_sha256":local_sha,"token_exposed":False,"forward_port":fw_check}
    # 3) install through the running Phase-A service
    h=_phase_a_health(params)
    if not h:
        result.update({"status":"BLOCKED","reason":"PHASE_A_NOT_RUNNING","next_step":"Termux oeffnen: bash ~/storage/downloads/NEXUS_TRANSFER/termux/bootstrap_termux.sh"})
        raise BridgeError(INVALID_REQUEST,_PHASE_A_NOT_RUNNING,http_status=503,details=result)
    actions=set(h.get("actions") or [])
    if "install_package" in actions:
        inst=_phase_a_post(params,"install_package")
        result["install"]={"action":"install_package","count":inst.get("count"),"version":inst.get("version")}
    else:
        legacy=_push_one(push_params,"backend/phase_a_control.py","phase_a_control.py",True)
        inst=_phase_a_post(params,"install_phase_a")
        result["install"]={"action":"install_phase_a(legacy single file)","staged":legacy.get("destination")}
    if inst.get("status")!="PASS":
        raise BridgeError(INVALID_REQUEST,"fixed Termux deployment failed",http_status=500,details={"install":inst})
    # 4) restart into the new code and prove it via /health
    if "restart_control" in actions or "install_package" in actions:
        _phase_a_post(params,"restart_control",timeout=15)
    else:
        result.update({"status":"PARTIAL","reason":"OLD_AGENT_NO_RESTART","next_step":"Phase-A in Termux neu starten: bash ~/nexus/restart.sh"})
        return result
    running=None
    for _ in range(15):
        time.sleep(1)
        running=_phase_a_health(params)
        if running and running.get("code_sha256")==local_sha:
            break
    if not running or running.get("code_sha256")!=local_sha:
        raise BridgeError(INVALID_REQUEST,"Phase-A restart not verified (code_sha256 mismatch or no health)",http_status=500,
                          details={"running_sha":(running or {}).get("code_sha256"),"local_sha":local_sha})
    result["running"]={"version":running.get("version"),"pid":running.get("pid"),"code_sha256":running.get("code_sha256")}
    # 5) single autostart entry
    auto=_phase_a_post(params,"autostart_ensure")
    stat=_phase_a_post(params,"autostart_status")
    result["autostart"]={"ensure":auto.get("status"),"status":stat.get("status"),"entries":stat.get("boot_entries")}
    # legacy single-file stage is removed; the package stage stays for re-bootstrap (non-secret)
    _adb_device_cmd(push_params,["shell","rm","-f",f"{_ANDROID_STAGE}/phase_a_control.py"],timeout=10)
    result["status"]="PASS"
    return result


def exec_provision_bridge_token_to_phone(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """Provision the fixed Bridge token through Phase-A localhost control without exposing it."""
    extra=set(params)-{"device","approval_id"}
    if extra: raise BridgeError(INVALID_REQUEST,f"unknown params: {', '.join(sorted(extra))}",http_status=400)
    source=(WORKSPACE/"bridge"/".token").resolve()
    if not source.is_file(): raise BridgeError(INVALID_REQUEST,"bridge token source missing",http_status=404)
    token=source.read_bytes()
    if len(token)!=64: raise BridgeError(INVALID_REQUEST,"bridge token source length invalid",http_status=500)
    device=str(params.get("device") or "").strip()
    port=_phase_a_forward(params)
    import urllib.request, urllib.error
    req=urllib.request.Request(f"http://127.0.0.1:{port}/provision/bridge-token",data=token,headers={"Content-Type":"application/octet-stream"},method="POST")
    try:
        with urllib.request.urlopen(req,timeout=30) as r: out=json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex: raise BridgeError(INVALID_REQUEST,"private token transfer unavailable",http_status=500) from ex
    except Exception as ex: raise BridgeError(INVALID_REQUEST,"private token transfer failed",http_status=500) from ex
    if out.get("status")!="PASS" or out.get("length")!=64 or not out.get("written"):
        raise BridgeError(INVALID_REQUEST,"private token verification failed",http_status=500)
    return {"task":"provision_bridge_token_to_phone","device":device or None,"written":True,"target_length":64,"token_exposed":False}


def exec_android_install_apk(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    apk = params.get("apk") or params.get("path") or ""
    fp = _safe_path(str(apk), must_exist=True)
    if fp.suffix.lower() != ".apk":
        raise BridgeError(INVALID_REQUEST, "apk path required", http_status=400)
    try:
        proc = subprocess.run(
            ["adb", "install", "-r", str(fp)],
            capture_output=True, text=True, timeout=180, shell=False, check=False,
        )
        return {"apk": _rel(fp), "returncode": proc.returncode,
                "stdout": (proc.stdout or "")[:4000], "stderr": (proc.stderr or "")[:2000]}
    except FileNotFoundError:
        raise BridgeError(NOT_IMPLEMENTED, "adb not found", http_status=500)


def exec_mouse_move(params: dict[str, Any], rec: TaskRecord) -> dict[str, Any]:
    """Setzt den Windows-Mauszeiger exakt auf (x, y) und belegt die neue Position.

    Nur die reine Zeigerbewegung — kein Klick, keine Tastatureingabe, kein Fenster-
    oder Programmstart. Koordinaten werden gegen den gesamten virtuellen Desktop
    (alle Monitore) geprueft. Es gibt keinen Erfolg ohne Rueckleseposition.
    """
    extra = set(params) - {"x", "y", "approval_id"}
    if extra:
        raise BridgeError(INVALID_REQUEST, f"unknown params: {', '.join(sorted(extra))}", http_status=400)
    x, y = params.get("x"), params.get("y")
    if type(x) is not int or type(y) is not int:
        raise BridgeError(INVALID_REQUEST, "x and y must be integers", http_status=400)
    if os.name != "nt":
        raise BridgeError(NOT_IMPLEMENTED, "mouse control is only available on Windows", http_status=501)

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetSystemMetrics.restype = ctypes.c_int
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]

    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    if vw <= 0 or vh <= 0:
        raise BridgeError(NOT_IMPLEMENTED, "virtual screen size unavailable", http_status=500)
    if not (vx <= x < vx + vw and vy <= y < vy + vh):
        raise BridgeError(
            INVALID_REQUEST,
            f"coordinate ({x}, {y}) outside the virtual desktop "
            f"[{vx}..{vx + vw - 1}] x [{vy}..{vy + vh - 1}]",
            http_status=400,
            details={"min_x": vx, "max_x": vx + vw - 1, "min_y": vy, "max_y": vy + vh - 1},
        )

    before = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(before)):
        raise BridgeError(ACTION_DENIED, "GetCursorPos failed before move", http_status=500)
    moved_ok = bool(user32.SetCursorPos(int(x), int(y)))
    after = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(after)):
        raise BridgeError(ACTION_DENIED, "GetCursorPos failed after move", http_status=500)
    tolerance = 2
    within = abs(after.x - x) <= tolerance and abs(after.y - y) <= tolerance
    return {
        "ok": bool(moved_ok and within),
        "moved": bool(moved_ok),
        "x": int(x),
        "y": int(y),
        "before": [int(before.x), int(before.y)],
        "after": [int(after.x), int(after.y)],
        "tolerance": tolerance,
        "within_tolerance": bool(within),
        "screen": [int(vx), int(vy), int(vw), int(vh)],
        "backend": "user32.SetCursorPos",
        "host": platform.node(),
        "task_id": rec.id,
        "moved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


# --- Phase 2 Schritt 7/8 (02.11.2026): pc_music + shell_run als Registry-Typen ---
# Nur In-Process (rec is None). Ueber die Bridge-HTTP-API gesperrt: keine Remote-Shell.
def exec_pc_music(params: dict[str, Any], rec: TaskRecord | None) -> dict[str, Any]:
    if rec is not None:
        return {"success": False, "error": "IN_PROCESS_ONLY"}
    from nexus.computer.remote_executor import execute as _rx
    from nexus.maintenance import maintenance as _mt
    with _mt().work("pc_remote"):
        return _rx(params.get("intent"), params.get("args") or {})


def exec_shell_run(params: dict[str, Any], rec: TaskRecord | None) -> dict[str, Any]:
    if rec is not None:
        return {"success": False, "ok": False, "error": "IN_PROCESS_ONLY"}
    from nexus.terminal import service as _svc
    kw = dict(params)
    cmd = kw.pop("command", "")
    return _svc.execute(cmd, **kw)


def bind_default_executors(manager: TaskManager) -> None:
    from nexus.bridge import tools_008 as _t008
    mapping = {
        "ping": exec_ping,
        "system_status": exec_system_status,
        "inspect_server": exec_inspect_server,
        "read_file": exec_read_file,
        "search_log": exec_search_log,
        "list_files": exec_list_files,
        "search_files": _t008.exec_search_files,  # 008-P03
        "code_analyze": _t008.exec_code_analyze,  # 008-P03
        "system_diagnose": _t008.exec_system_diagnose,  # 008-P03
        "write_file": exec_write_file,
        "patch_file": exec_patch_file,
        "create_file": exec_create_file,
        "create_dir": exec_create_dir,
        "move_file": exec_move_file,
        "copy_file": exec_copy_file,
        "delete_file": exec_delete_file,
        "git_status": exec_git_status,
        "git_diff": exec_git_diff,
        "git_log": exec_git_log,
        "git_add": exec_git_add,
        "git_commit": exec_git_commit,
        "git_push": exec_git_push,
        "capability_check": exec_capability_check,
        "request_approval": exec_request_approval,
        "python_run_project_script": exec_python_run_project_script,
        "hermes_check_code": exec_hermes_check_code,
        "hermes_run_code_tests": exec_hermes_run_code_tests,
        "powershell_registered_action": exec_powershell_registered_action,
        "service_status": exec_service_status,
        "nexus_control_status": exec_nexus_control_status,
        "service_start": exec_service_start,
        "service_stop": exec_service_stop,
        "service_restart": exec_service_restart,
        "nexus_status": exec_nexus_status,
        "nexus_start": exec_nexus_start,
        "nexus_stop": exec_nexus_stop,
        "nexus_restart": exec_nexus_restart,
        "openai_diagnostic_once": exec_openai_diagnostic_once,
        "media_status": exec_media_status,
        "voice_status": exec_voice_status,
        "llm_status": exec_llm_status,
        "termux_status": exec_termux_status,
        "android_status": exec_android_status,
        "android_devices": exec_android_devices,
        "android_version": exec_android_version,
        "android_build": exec_android_build,
        "android_security_patch": exec_android_security_patch,
        "android_fingerprint": exec_android_fingerprint,
        "android_slot": exec_android_slot,
        "android_bootloader_status": exec_android_bootloader_status,
        "android_storage": exec_android_storage,
        "android_battery": exec_android_battery,
        "android_network": exec_android_network,
        "android_termux_status": exec_android_termux_status,
        "android_screenshot": exec_android_screenshot,
        "android_push_file": exec_android_push_file,
        "android_push_tree": exec_android_push_tree,
        "restart_nexus_bridge_exact": exec_restart_nexus_bridge_exact,
        "install_krita_portable": exec_install_krita_portable,
        "krita_gui_test": exec_krita_gui_test,
        "android_install_apk": exec_android_install_apk,
        "mouse_move": exec_mouse_move,
        "pc_music": exec_pc_music,
        "shell_run": exec_shell_run,
    }
    from nexus.bridge.dev_executors import EXECUTORS as _dev_executors
    mapping.update(_dev_executors)
    for name, fn in mapping.items():
        manager.register(name, fn)

