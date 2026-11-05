"""Runtime-Config aus Env — keine Secrets im Code."""
from __future__ import annotations
import os


class Config:
    # Default nur lokal; LAN/Handy (/m) bewusst per NEXUS_HOST=0.0.0.0 (setzen start_nexus.ps1/.sh)
    HOST = os.environ.get("NEXUS_HOST", "127.0.0.1")
    PORT = int(os.environ.get("NEXUS_PORT", "8299"))
    SSL_PORT = int(os.environ.get("NEXUS_SSL_PORT", "8443"))
    # Relative to project root; resolved in server via paths.WORKSPACE
    SSL_CERT = os.environ.get("NEXUS_SSL_CERT", "workspace/certs/cert.pem")
    SSL_KEY = os.environ.get("NEXUS_SSL_KEY", "workspace/certs/key.pem")
    OLLAMA_URL = os.environ.get("NEXUS_OLLAMA_URL", "http://127.0.0.1:11434")
    PREFERRED_MODEL = os.environ.get("NEXUS_MODEL", "qwen3.5:9b")
    FALLBACK_MODEL = "qwen3.5:9b"
    VISION_MODEL = os.environ.get("NEXUS_VISION_MODEL", "llava:latest")
    # Phase 8: 0 = unload immediately after request (no permanent VRAM)
    OLLAMA_KEEP_ALIVE = os.environ.get("NEXUS_KEEP_ALIVE", "0")
    OLLAMA_TIMEOUT = float(os.environ.get("NEXUS_OLLAMA_TIMEOUT", "300"))
    VERSION = "1.0.0"
    CORS_ORIGIN = "*"
    CODE_TIMEOUT = int(os.environ.get("NEXUS_CODE_TIMEOUT", "15"))
    DOWNLOAD_TIMEOUT = int(os.environ.get("NEXUS_DL_TIMEOUT", "120"))
    # Shell: Defaults fuer Build/npm/vite (lange Workloads)
    SHELL_TIMEOUT = int(os.environ.get("NEXUS_SHELL_TIMEOUT", "300"))
    SHELL_TIMEOUT_BUILD = int(os.environ.get("NEXUS_SHELL_TIMEOUT_BUILD", "600"))
    SHELL_TIMEOUT_MAX = int(os.environ.get("NEXUS_SHELL_TIMEOUT_MAX", "900"))
    SHELL_STDOUT_MAX = int(os.environ.get("NEXUS_SHELL_STDOUT_MAX", "48000"))
    SHELL_STDOUT_TAIL_LINES = int(os.environ.get("NEXUS_SHELL_STDOUT_TAIL", "200"))
    SHELL_STDERR_MAX = int(os.environ.get("NEXUS_SHELL_STDERR_MAX", "12000"))
