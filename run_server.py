#!/usr/bin/env python3

"""NEXUS AI Master entrypoint — Port 8299, Host aus NEXUS_HOST (Default 127.0.0.1)."""
from __future__ import annotations
# 20261031: Konsole kann fehlen (WinError 50) -> Ausgaben nie den Server/Stream crashen lassen
import sys as _sys, io as _io, os as _os
class _SafeOut(_io.TextIOBase):
    def __init__(self, inner, path):
        self._i = inner; self._p = path
    def write(self, t):
        try:
            return self._i.write(t) if self._i else len(t)
        except (OSError, ValueError):
            try:
                with open(self._p, "a", encoding="utf-8", errors="replace") as f: f.write(t)
            except Exception: pass
            return len(t)
    def flush(self):
        try:
            self._i and self._i.flush()
        except (OSError, ValueError): pass
_lp = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "workspace", "logs", "server_console.log")
_sys.stdout = _SafeOut(_sys.stdout, _lp); _sys.stderr = _SafeOut(_sys.stderr, _lp)
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _safe_stdio() -> None:
    """Windows: wenn stdout/stderr geschlossen/ungültig sind, auf DEVNULL umbiegen."""
    import os
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        try:
            if stream is None or getattr(stream, "closed", False):
                raise RuntimeError("closed")
            stream.fileno()
        except Exception:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8", errors="replace"))
            except Exception:
                pass


_safe_stdio()

from nexus.chat.server import main

if __name__ == "__main__":
    main()
