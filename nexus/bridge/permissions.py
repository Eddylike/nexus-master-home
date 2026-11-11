"""Permission levels + task registry for Agent Bridge (Master)."""
from __future__ import annotations

import os
from enum import Enum


class PermissionLevel(str, Enum):
    READ_ONLY = "READ_ONLY"
    SAFE_WRITE = "SAFE_WRITE"
    PRIVILEGED = "PRIVILEGED"


# Expanded registered tasks (no arbitrary remote shell)
TASK_PERMISSIONS: dict[str, PermissionLevel] = {
    # READ_ONLY
    "ping": PermissionLevel.READ_ONLY,
    "system_status": PermissionLevel.READ_ONLY,
    "inspect_server": PermissionLevel.READ_ONLY,
    "read_file": PermissionLevel.READ_ONLY,
    "search_log": PermissionLevel.READ_ONLY,
    "list_files": PermissionLevel.READ_ONLY,
    "search_files": PermissionLevel.READ_ONLY,  # 008-P03
    "code_analyze": PermissionLevel.READ_ONLY,  # 008-P03
    "system_diagnose": PermissionLevel.READ_ONLY,  # 008-P03
    "git_status": PermissionLevel.READ_ONLY,
    "git_diff": PermissionLevel.READ_ONLY,
    "git_log": PermissionLevel.READ_ONLY,
    "capability_check": PermissionLevel.READ_ONLY,
    "nexus_status": PermissionLevel.READ_ONLY,
    "media_status": PermissionLevel.READ_ONLY,
    "voice_status": PermissionLevel.READ_ONLY,
    "llm_status": PermissionLevel.READ_ONLY,
    "termux_status": PermissionLevel.READ_ONLY,
    "android_status": PermissionLevel.READ_ONLY,
    "android_devices": PermissionLevel.READ_ONLY,
    "android_version": PermissionLevel.READ_ONLY,
    "android_build": PermissionLevel.READ_ONLY,
    "android_security_patch": PermissionLevel.READ_ONLY,
    "android_fingerprint": PermissionLevel.READ_ONLY,
    "android_slot": PermissionLevel.READ_ONLY,
    "android_bootloader_status": PermissionLevel.READ_ONLY,
    "android_storage": PermissionLevel.READ_ONLY,
    "android_battery": PermissionLevel.READ_ONLY,
    "android_network": PermissionLevel.READ_ONLY,
    "android_termux_status": PermissionLevel.READ_ONLY,
    "service_status": PermissionLevel.READ_ONLY,
    "nexus_control_status": PermissionLevel.READ_ONLY,
    "hermes_check_code": PermissionLevel.READ_ONLY,
    # Creates only a pending local approval request; never approves or consumes it.
    "request_approval": PermissionLevel.READ_ONLY,
    # Hermes-Entwicklung: Browserprüfung, geprüfte Vorschau, isolierte NEXUS-Patchvorbereitung
    "hermes_check_web": PermissionLevel.SAFE_WRITE,
    "hermes_publish_preview": PermissionLevel.SAFE_WRITE,
    "hermes_prepare_nexus_patch": PermissionLevel.SAFE_WRITE,
    "hermes_test_nexus_patch": PermissionLevel.SAFE_WRITE,
    # SAFE_WRITE (root = project ROOT)
    "write_file": PermissionLevel.SAFE_WRITE,
    "patch_file": PermissionLevel.SAFE_WRITE,
    "create_file": PermissionLevel.SAFE_WRITE,
    "create_dir": PermissionLevel.SAFE_WRITE,
    "move_file": PermissionLevel.SAFE_WRITE,
    "copy_file": PermissionLevel.SAFE_WRITE,
    "delete_file": PermissionLevel.SAFE_WRITE,
    "git_add": PermissionLevel.SAFE_WRITE,
    "git_commit": PermissionLevel.SAFE_WRITE,
    "python_run_project_script": PermissionLevel.SAFE_WRITE,
    "hermes_run_code_tests": PermissionLevel.SAFE_WRITE,
    "android_screenshot": PermissionLevel.SAFE_WRITE,
    # PRIVILEGED (local approval_id required)
    "restart_nexus_bridge_exact": PermissionLevel.PRIVILEGED,
    # Fixed official Krita Portable installer; no arbitrary URL/path, local approval required.
    "install_krita_portable": PermissionLevel.PRIVILEGED,
    # Fixed local Krita GUI test launcher; exact G: executable only, approval required.
    "krita_gui_test": PermissionLevel.PRIVILEGED,
    "powershell_registered_action": PermissionLevel.PRIVILEGED,
    "service_start": PermissionLevel.PRIVILEGED,
    "service_stop": PermissionLevel.PRIVILEGED,
    "service_restart": PermissionLevel.PRIVILEGED,
    "nexus_start": PermissionLevel.PRIVILEGED,
    "nexus_stop": PermissionLevel.PRIVILEGED,
    "nexus_restart": PermissionLevel.PRIVILEGED,
    # Exactly-one OpenAI connectivity diagnostic; local one-shot approval required.
    "openai_diagnostic_once": PermissionLevel.PRIVILEGED,
    # Produktive Selbständerung von NEXUS: nur mit lokaler Freigabe
    "nexus_apply_patch": PermissionLevel.PRIVILEGED,
    "nexus_rollback_patch": PermissionLevel.PRIVILEGED,
    "git_push": PermissionLevel.PRIVILEGED,
    "android_install_apk": PermissionLevel.PRIVILEGED,
    "android_push_file": PermissionLevel.PRIVILEGED,
    "android_push_tree": PermissionLevel.PRIVILEGED,
    "android_push_file": PermissionLevel.PRIVILEGED,
    "android_push_tree": PermissionLevel.PRIVILEGED,
    # PC-Eingabesteuerung: echte Mausbewegung, daher PRIVILEGED (lokale Freigabe)
    "mouse_move": PermissionLevel.PRIVILEGED,
    # Phase 2 (02.11.2026): nur In-Process (Executor lehnt Bridge-Aufrufe ab)
    "pc_music": PermissionLevel.SAFE_WRITE,
    "shell_run": PermissionLevel.SAFE_WRITE,  # Stufe im Gate aus terminal.guard; Freigaben dort
}


def _parse_level(raw: str | None) -> PermissionLevel:
    v = (raw or "").strip().upper()
    if v in PermissionLevel.__members__:
        return PermissionLevel[v]
    # Master default: SAFE_WRITE enabled; PRIVILEGED still needs approval
    return PermissionLevel.SAFE_WRITE


DEFAULT_PROCESS_LEVEL = _parse_level(os.environ.get("NEXUS_BRIDGE_PROCESS_LEVEL"))


def allows(required: PermissionLevel, granted: PermissionLevel | None = None) -> bool:
    granted = granted or DEFAULT_PROCESS_LEVEL
    order = {
        PermissionLevel.READ_ONLY: 0,
        PermissionLevel.SAFE_WRITE: 1,
        PermissionLevel.PRIVILEGED: 2,
    }
    return order[granted] >= order[required]


def process_level() -> PermissionLevel:
    return _parse_level(os.environ.get("NEXUS_BRIDGE_PROCESS_LEVEL")) or DEFAULT_PROCESS_LEVEL
