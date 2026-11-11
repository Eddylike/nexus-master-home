"""OpenAPI / capability snippets for ChatGPT Custom GPT Actions."""
from __future__ import annotations

from typing import Any


def openapi_snippet(base_url: str = "https://wozipc-nexus.example.ts.net") -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "NEXUS Agent Bridge", "version": "1.1.0"},
        "servers": [{"url": base_url.rstrip("/")}],
        "paths": {
            "/api/bridge/health": {"get": {"operationId": "bridgeHealth", "security": [{"bearer": []}]}},
            "/api/capabilities": {"get": {"operationId": "capabilities", "security": [{"bearer": []}]}},
            "/api/message": {"post": {"operationId": "bridgeMessage", "security": [{"bearer": []}]}},
            "/api/task": {"post": {"operationId": "createTask", "security": [{"bearer": []}]}},
        },
        "components": {
            "securitySchemes": {
                "bearer": {"type": "http", "scheme": "bearer"}
            }
        },
        "notes": [
            "Only bridge paths via Funnel. Chat/Voice/Media are local-only.",
            "PRIVILEGED tasks need local approval_id.",
            "OpenAI not required.",
        ],
    }


def capabilities_for_gpt() -> dict[str, Any]:
    return {
        "name": "NEXUS Bridge",
        "auth": "Bearer NEXUS_BRIDGE_TOKEN",
        "public_url": "https://wozipc-nexus.example.ts.net",
        "local_chat": "http://127.0.0.1:8299/api/assistant",
        "cost": "LOCAL->FREE->NOT",
    }
