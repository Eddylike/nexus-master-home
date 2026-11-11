"""Safe display capability detection for interactive-only work."""
from __future__ import annotations

import ctypes
import os


class DisplayUnavailable(RuntimeError):
    """Raised when an operation requires an interactive Windows display."""


def display_available() -> bool:
    if os.environ.get("NEXUS_HEADLESS") == "1":
        return False
    if os.name != "nt":
        return False
    try:
        session_id = ctypes.c_uint()
        if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
            return False
        if session_id.value == 0:
            return False
        return ctypes.windll.user32.GetSystemMetrics(0) > 0
    except (AttributeError, OSError):
        return False


def require_display() -> None:
    if not display_available():
        raise DisplayUnavailable("interactive display unavailable")
