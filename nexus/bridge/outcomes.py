"""Pure, task-specific completion checks. See docs/BRIDGE_OUTCOMES.md.

No I/O, recursive payload guessing or executor invocation belongs here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from nexus.bridge.permissions import TASK_PERMISSIONS


@dataclass(frozen=True)
class ExecutorOutcome:
    data: Any
    verdict: str  # succeeded | failed | verification_unknown
    reason: str | None = None

    @property
    def status(self) -> str:
        return "succeeded" if self.verdict == "succeeded" else "failed"

    @property
    def error_code(self) -> str | None:
        if self.verdict == "succeeded":
            return None
        return "VERIFICATION_UNKNOWN" if self.verdict == "verification_unknown" else "TASK_FAILED"


class _Unknown(Exception):
    pass


class _Failed(Exception):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Unknown(reason)


def _fields(data: dict, fields: dict[str, type]) -> None:
    for key, kind in fields.items():
        _require(type(data.get(key)) is kind, f"missing or invalid {key}")


def _text(data: dict, *keys: str) -> None:
    _fields(data, dict.fromkeys(keys, str))
    for key in keys:
        _require(bool(data[key]), f"empty {key}")


def _integer(data: dict, key: str, minimum: int = 0) -> None:
    _fields(data, {key: int})
    _require(data[key] >= minimum, f"invalid {key}")


def _envelope(data: Any) -> dict:
    """Negative evidence only, at a known executor-result boundary.

    Never call this on file content, echo, config or arbitrary metric payloads.
    It cannot establish success; a named task rule must do that.
    """
    _require(type(data) is dict, "executor result is not a dictionary")
    for key in ("error", "error_code"):
        if data.get(key):
            raise _Failed(f"executor reported {key}")
    for key in ("ok", "success"):
        if data.get(key) is False:
            raise _Failed(f"executor reported {key}=false")
    for key in ("returncode", "exit_code"):
        if type(data.get(key)) is int and data[key] != 0:
            raise _Failed(f"executor reported {key}={data[key]}")
    if data.get("status") in ("FAIL", "FAILED", "PARTIAL", "BLOCKED", "failed", "cancelled"):
        raise _Failed("executor reported failure or incomplete result")
    if "status" in data and data["status"] not in ("PASS", "OK", "ok", "success", "succeeded"):
        raise _Unknown("executor status does not establish completion")
    for key in ("ok", "success"):
        if key in data:
            _fields(data, {key: bool})
    for key in ("returncode", "exit_code"):
        if key in data:
            _fields(data, {key: int})
    if data.get("scheduled") is True or data.get("awaiting_approval") is True or data.get("approval_required") is True:
        raise _Unknown("execution scheduled or approval pending; completion not verified")
    return data


def _true(data: dict, *keys: str) -> None:
    for key in keys:
        _fields(data, {key: bool})
        if data[key] is False:
            raise _Failed(f"required {key}=false")


def _process(data: dict) -> None:
    _envelope(data)
    _fields(data, {"returncode": int, "stdout": str, "stderr": str})
    # _envelope already rejects every nonzero integer, including negative codes.


def _static(data: dict, params: dict) -> None:
    """Static AST verification of a code file; no execution involved."""
    if data.get("syntax") == "error":
        raise _Failed("source does not parse: " + str(data.get("message") or "syntax error"))
    _require(data.get("syntax") == "ok", "syntax not verified")
    _text(data, "path")
    _integer(data, "bytes")
    _integer(data, "lines")
    _fields(data, {"imports": list, "defs": list, "forbidden": list,
                   "stdlib": list, "local": list, "external": list, "missing": list})
    groups = (data["imports"], data["defs"], data["forbidden"],
              data["stdlib"], data["local"], data["external"], data["missing"])
    _require(all(type(x) is str for group in groups for x in group), "invalid static metadata")
    if data["forbidden"]:
        raise _Failed("forbidden modules/calls: " + ", ".join(data["forbidden"]))
    if data["external"]:
        raise _Failed("non-standard-library imports: " + ", ".join(data["external"]))
    if data["missing"]:
        raise _Failed("unresolved local dependencies: " + ", ".join(data["missing"]))


def _sandbox_run(data: dict, params: dict) -> None:
    """Bounded code execution: success only with a proven OS containment boundary."""
    _process(data)
    _true(data, "sandbox", "env_sanitized")
    _require(data.get("containment") in ("windows_appcontainer", "windows_job_object", "posix_rlimit"),
             "no OS containment boundary established")
    _fields(data, {"network_guard": str, "filesystem_isolation": str})
    if data.get("timed_out") is True:
        raise _Failed("sandbox run exceeded its time limit")
    _require(data.get("timed_out") is False, "timeout state not verified")


def _git(data: dict, params: dict) -> None:
    _process(data)
    _true(data, "available")


def _read(data: dict, params: dict) -> None:
    _text(data, "path", "abs_path")
    _integer(data, "size")
    _fields(data, {"content": str, "truncated": bool})
    _require(data.get("encoding") in ("utf-8", "utf-8-replace"), "unknown text encoding")


def _listing(data: dict, params: dict) -> None:
    _fields(data, {"path": str, "entries": list, "truncated": bool})
    _integer(data, "count")
    _require(data["count"] == len(data["entries"]), "entry count mismatch")
    for entry in data["entries"]:
        _require(type(entry) is dict, "invalid directory entry")
        _text(entry, "name")
        _require(entry.get("type") in ("file", "dir"), "entry metadata unavailable")
        if entry["type"] == "file":
            _integer(entry, "size")


def _file_action(task: str, data: dict) -> None:
    paths = {"move_file": ("src", "dst"), "copy_file": ("path", "destination")}
    _text(data, *paths.get(task, ("path",)))
    flags = {"write_file": "atomic", "create_file": "created", "create_dir": "created",
             "patch_file": "changed", "move_file": "moved", "copy_file": "copied", "delete_file": "deleted"}
    _true(data, flags[task])
    if task in ("write_file", "create_file", "patch_file"):
        _integer(data, "bytes")
    if task == "create_dir":
        _true(data, "is_dir")
    if task == "patch_file":
        _integer(data, "replacements", 1)
    if task == "delete_file":
        _require(data.get("kind") in ("file", "dir"), "missing deleted object kind")


def _service(data: dict, expected: str) -> dict:
    _process(data)
    # PowerShell can report nonterminating errors while returning zero.
    if data["stderr"].strip():
        raise _Failed("service query/control reported stderr")
    try:
        service = json.loads(data["stdout"])
    except (ValueError, TypeError):
        raise _Unknown("missing Get-Service JSON") from None
    _require(type(service) is dict and service.get("Name") == expected, "service identity not verified")
    _require(service.get("Status") in (1, 2, 3, 4, 5, 6, 7,
                                      "Stopped", "StartPending", "StopPending", "Running",
                                      "ContinuePending", "PausePending", "Paused")
             and type(service.get("Status")) in (str, int), "service state unavailable")
    return service


def _services(data: dict, params: dict) -> None:
    name = str(params.get("name") or params.get("service") or "").strip()
    if name:
        _require(name in ("Ollama", "Tailscale") and data.get("name") == name, "service name mismatch")
        _service(data, name)
    else:
        _fields(data, {"services": dict, "allowed": list})
        _require(set(data["services"]) == {"Ollama", "Tailscale"}
                 and set(data["allowed"]) == {"Ollama", "Tailscale"}, "missing allowed service results")
        for name, item in data["services"].items():
            _service(item, name)


def _control(data: dict, params: dict, verb: str) -> None:
    name = str(params.get("name") or "").strip()
    _require(name in ("Ollama", "Tailscale") and data.get("name") == name and data.get("verb") == verb,
             "service control identity mismatch")
    service = _service(data, name)
    if verb == "restart":
        raise _Unknown("final service state cannot verify restart transition")
    target = (1, "Stopped") if verb == "stop" else (4, "Running")
    if service["Status"] not in target:
        raise _Failed("requested service state not reached")


def _inspect(data: dict, params: dict) -> None:
    _text(data, "server", "handler", "host", "version", "model", "root", "bridge_protocol", "bridge_version", "process_level")
    _integer(data, "port", 1)
    _integer(data, "ssl_port", 1)
    _fields(data, {"registered_tasks": list, "endpoints": list})
    _require(set(data["registered_tasks"]) == set(TASK_PERMISSIONS) and bool(data["endpoints"]), "incomplete server snapshot")


def _capabilities(data: dict, params: dict) -> None:
    _text(data, "protocol", "version", "process_level", "path_root", "notes")
    _fields(data, {"permissions": list, "tasks": list, "executors": dict})
    expected = [{"name": name, "permission": level.value} for name, level in TASK_PERMISSIONS.items()]
    _require(data["tasks"] == expected and set(data["permissions"]) == {"READ_ONLY", "SAFE_WRITE", "PRIVILEGED"},
             "incomplete capability registry")
    _require(set(data["executors"]) == {"PowerShell", "Python", "File", "Git", "System", "Approval"}
             and all(type(v) is bool for v in data["executors"].values()), "incomplete executor declarations")


def _system(data: dict, params: dict) -> None:
    _text(data, "platform", "release", "machine", "python", "cwd", "root", "process_level")
    _integer(data, "nexus_port", 1)
    _integer(data, "nexus_ssl_port", 1)
    metrics = _envelope(data.get("metrics"))
    _true(metrics, "success")
    _require(type(data.get("disk_free_gb")) in (int, float) and data["disk_free_gb"] >= 0, "disk measurement unavailable")
    _require(all(k in metrics for k in ("timestamp", "cpu", "ram", "disk", "gpu", "platform")), "incomplete metrics snapshot")
    _require(metrics["timestamp"] is not None, "metrics timestamp unavailable")


def _nexus(data: dict, params: dict) -> None:
    _text(data, "root", "process_level")
    _integer(data, "port", 1)
    if data.get("health_error") or data.get("origin") == "DOWN" or data.get("gateway") == "DOWN":
        raise _Failed("NEXUS status probe failed")
    _require(data.get("origin") == "UP" and data.get("gateway") == "UP", "missing NEXUS probe state")
    _require(type(data.get("gateway_code")) is int and data["gateway_code"] in (200, 401), "missing gateway response")
    _envelope(data.get("health"))
    _require(bool(data["health"]), "empty health response")


def _nexus_control(data: dict, params: dict) -> None:
    _nexus(data, params)
    _services(_envelope(data.get("tailscale")), {"name": "Tailscale"})
    if data.get("external") == "DOWN":
        raise _Failed("external probe failed")
    _integer(data, "pid", 1)
    _text(data, "public_url")
    _require(data.get("external") == "UP" and type(data.get("external_code")) is int
             and data["external_code"] in (200, 401), "external probe not verified")


def _llm(data: dict, params: dict) -> None:
    _text(data, "routing", "cost_rule")
    ollama = _envelope(data.get("ollama"))
    _true(ollama, "reachable")
    _fields(ollama, {"models": list})
    _text(ollama, "preferred")
    _require(all(type(x) is str for x in ollama["models"]), "invalid model listing")


def _unproven(reason: str) -> Callable:
    def check(data: dict, params: dict) -> None:
        raise _Unknown(reason)
    return check


def _voice(data: dict, params: dict) -> None:
    _voice_nested(data)
    _true(data, "ok")
    _fields(data, {"voice_config": dict, "tts_edge": bool, "verification": dict})
    verification = data["verification"]
    _true(verification, "config_read", "edge_tts_importable", "faster_whisper_importable")
    _require(data["tts_edge"] is True and data.get("stt") == "faster-whisper",
             "voice dependencies not verified")



def _voice_nested(data: dict) -> None:
    """voice_config is an executor sub-result: nested error -> failed (like llm/system/nexus)."""
    cfg = data.get("voice_config")
    if type(cfg) is dict and (cfg.get("error") or cfg.get("error_code")):
        raise _Failed("voice config reported error")


def _search_log(data: dict, params: dict) -> None:
    _text(data, "path", "pattern", "redaction")
    _integer(data, "max_lines", 1)
    _integer(data, "count")
    _fields(data, {"matches": list, "streaming": bool})
    _require(data["redaction"] == "fail-closed", "redaction mode not verified")
    _require(data["count"] == len(data["matches"]) <= data["max_lines"], "match count mismatch")
    for m in data["matches"]:
        _require(type(m) is dict and type(m.get("line")) is int and m["line"] >= 1
                 and type(m.get("text")) is str, "invalid match entry")


def _request_approval(data: dict, params: dict) -> None:
    _text(data, "approval_id", "task")
    # A pending approval is not a completed task; execution happens only after human approval.
    raise _Unknown("approval pending; completion only after human approval")


def _install_krita(data: dict, params: dict) -> None:
    _text(data, "version", "source", "target", "sha256")
    _require(re.fullmatch(r"[0-9a-fA-F]{64}", data["sha256"]) is not None, "archive hash not verified")
    _require(data["source"] == "download.kde.org", "download source not verified")
    _fields(data, {"krita_launched": bool, "c_write_exception": bool})
    if data["krita_launched"] or data["c_write_exception"]:
        raise _Failed("installer contract violated (launch or C: write)")


def _openai_once(data: dict, params: dict) -> None:
    _true(data, "success", "cloud_success")
    _text(data, "model")
    _require(type(data.get("request_count")) is int and data["request_count"] == 1, "request count not verified")
    _require(data.get("http_status") == 200, "HTTP status not verified")


def _media(data: dict, params: dict) -> None:
    _true(data, "ok")
    _text(data, "media_dir")
    _integer(data, "files")
    _require(not data.get("error"), "media enumeration reported an error")


def _adb(data: dict, params: dict) -> None:
    _process(data)
    _true(data, "adb")


def _screenshot(data: dict, params: dict) -> None:
    _true(data, "ok")
    _text(data, "path")
    _integer(data, "bytes", 1)


def _android_push_item(data: dict) -> None:
    """A successful Android transfer must carry verified executor evidence."""
    _require(type(data) is dict, "invalid Android transfer result")
    _require(data.get("status") == "PASS", "Android transfer not passed")
    _text(data, "source", "destination", "sha256")
    _integer(data, "size")
    _require(
        re.fullmatch(r"[0-9a-fA-F]{64}", data["sha256"]) is not None,
        "invalid transfer SHA-256"
    )
    _fields(data, {"hash_verified": bool})
    # False is valid only after the executor's size-based fallback verification.


def _android_push_file(data: dict, params: dict) -> None:
    _android_push_item(data)


def _android_push_tree(data: dict, params: dict) -> None:
    _require(type(data) is dict, "invalid Android tree result")
    _require(data.get("status") == "PASS", "Android tree transfer not passed")
    _text(data, "source", "destination")
    _integer(data, "count")
    _fields(data, {"files": list})
    _require(data["count"] == len(data["files"]), "transferred file count mismatch")
    for item in data["files"]:
        _android_push_item(item)

def _mouse(data: dict, params: dict) -> None:
    """Mausbewegung gilt nur als erledigt, wenn die Rueckleseposition passt."""
    _true(data, "ok", "moved")
    _fields(data, {"x": int, "y": int, "tolerance": int, "within_tolerance": bool})
    _text(data, "backend", "host")
    _require(data["x"] == params.get("x") and data["y"] == params.get("y"),
             "reported coordinates do not match the request")
    _require(data["tolerance"] >= 0, "invalid tolerance")
    for key in ("before", "after", "screen"):
        pos = data.get(key)
        _require(type(pos) is list and len(pos) in (2, 4) and all(type(v) is int for v in pos),
                 f"invalid {key} coordinates")
    after = data["after"]
    _require(abs(after[0] - data["x"]) <= data["tolerance"]
             and abs(after[1] - data["y"]) <= data["tolerance"],
             "cursor position after move is not within tolerance")
    _require(data["within_tolerance"] is True, "executor did not confirm tolerance")
    _require(data["backend"] == "user32.SetCursorPos", "unknown input backend")


def _web_check(data: dict, params: dict) -> None:
    """Browserprüfung gilt als ausgeführt, wenn ein Prüfbericht erhoben wurde (passed separat)."""
    _true(data, "report_collected")
    _fields(data, {"passed": bool, "criteria": dict, "errors": list, "sha256": dict})
    _text(data, "path")


def _patch_test(data: dict, params: dict) -> None:
    _fields(data, {"passed": bool, "regressions": list, "tests": list})
    _require(data.get("containment") == "windows_appcontainer", "patch test not isolated")


RULES: dict[str, Callable[[dict, dict], None]] = {
    "ping": lambda d, p: _true(d, "ok", "pong"),
    "read_file": _read, "list_files": _listing,
    "inspect_server": _inspect, "capability_check": _capabilities,
    "system_status": _system, "service_status": _services,
    "nexus_status": _nexus, "nexus_control_status": _nexus_control, "llm_status": _llm,
    "media_status": _media,
    "voice_status": _voice,
    "android_screenshot": _screenshot,
    "android_push_file": _android_push_file,
    "android_push_tree": _android_push_tree,
    "hermes_check_code": _static, "hermes_run_code_tests": _sandbox_run,
    "mouse_move": _mouse,
    "hermes_check_web": _web_check,
    "hermes_publish_preview": lambda d, p: (_true(d, "published"), _text(d, "url")),
    "hermes_prepare_nexus_patch": lambda d, p: (_true(d, "prepared"), _text(d, "patch_id")),
    "hermes_test_nexus_patch": _patch_test,
    "nexus_apply_patch": lambda d, p: _true(d, "applied", "verified"),
    "nexus_rollback_patch": lambda d, p: _true(d, "rolled_back"),
}
for _name in ("write_file", "create_file", "create_dir", "patch_file", "move_file", "copy_file", "delete_file"):
    RULES[_name] = lambda d, p, name=_name: _file_action(name, d)
for _name in ("git_status", "git_diff", "git_log", "git_add", "git_commit", "git_push"):
    RULES[_name] = _git
for _name in ("python_run_project_script", "powershell_registered_action", "android_install_apk"):
    RULES[_name] = lambda d, p: _process(d)
for _verb in ("start", "stop", "restart"):
    RULES[f"service_{_verb}"] = lambda d, p, verb=_verb: _control(d, p, verb)
for _name in ("android_status", "android_devices", "termux_status", "android_version", "android_build",
              "android_security_patch", "android_fingerprint", "android_slot", "android_storage",
              "android_battery", "android_network"):
    RULES[_name] = _adb
for _name in ("nexus_start", "nexus_stop", "nexus_restart", "restart_nexus_bridge_exact"):
    RULES[_name] = _unproven("completion/transition is not verified; scheduling or existing health only")
for _name in ("android_bootloader_status", "android_termux_status"):
    RULES[_name] = _unproven("underlying exit status was discarded; observation cannot be verified")
RULES["search_log"] = _search_log
RULES["request_approval"] = _request_approval
RULES["install_krita_portable"] = _install_krita
RULES["krita_gui_test"] = _unproven("process started only; GUI test result is not verified")
# Phase 2: In-Process-Typen; ueber die Bridge lehnen die Executoren ab -> nie Erfolg ueber Bridge
RULES["pc_music"] = _unproven("in-process only; bridge call rejected")
RULES["shell_run"] = _unproven("in-process only; bridge call rejected")
RULES["openai_diagnostic_once"] = _openai_once


# 008-P03: Abschlussregeln fuer erweiterte READ_ONLY-Werkzeuge
def _search_files(d, p):
    _fields(d, {"query": str, "hits": list, "truncated": bool})
    _require(d.get("count") == len(d["hits"]), "hit count mismatch")
def _code_analyze(d, p):
    _require(type(d.get("syntax_ok")) is bool and type(d.get("issues")) is list, "analysis incomplete")
def _system_diagnose(d, p):
    _require(type(d.get("disks")) is dict and type(d.get("ports")) is dict and type(d.get("healthy")) is bool, "diagnosis incomplete")
RULES["search_files"] = _search_files
RULES["code_analyze"] = _code_analyze
RULES["system_diagnose"] = _system_diagnose


def evaluate(task: str, params: dict, data: Any) -> ExecutorOutcome:
    """Normalize known executor contracts without modifying their data."""
    try:
        rule = RULES.get(task)
        _require(rule is not None, "no completion rule for task")
        _envelope(data)
        rule(data, params)
    except _Failed as exc:
        return ExecutorOutcome(data, "failed", str(exc))
    except _Unknown as exc:
        return ExecutorOutcome(data, "verification_unknown", str(exc))
    except (TypeError, ValueError, KeyError, AttributeError):
        # Malformed nested structures must never escape into implicit success.
        return ExecutorOutcome(data, "verification_unknown", "malformed completion evidence")
    return ExecutorOutcome(data, "succeeded")
