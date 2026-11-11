"""Bridge error codes and helpers."""
from __future__ import annotations

from typing import Any


class BridgeError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 400,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "success": False,
            "error": self.code,
            "message": self.message,
        }
        if self.details:
            out["details"] = self.details
        return out


UNAUTHORIZED = "UNAUTHORIZED"
TOKEN_MISSING = "TOKEN_MISSING"
UNKNOWN_TASK = "UNKNOWN_TASK"
INVALID_REQUEST = "INVALID_REQUEST"
NOT_FOUND = "NOT_FOUND"
PERMISSION_DENIED = "PERMISSION_DENIED"
PATH_DENIED = "PATH_DENIED"
PATH_TRAVERSAL = "PATH_TRAVERSAL"
TASK_NOT_FOUND = "TASK_NOT_FOUND"
TASK_CANCELLED = "TASK_CANCELLED"
TASK_FAILED = "TASK_FAILED"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
INTERNAL = "INTERNAL"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
APPROVAL_DENIED = "APPROVAL_DENIED"
APPROVAL_NOT_FOUND = "APPROVAL_NOT_FOUND"
APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
RATE_LIMITED = "RATE_LIMITED"
BODY_TOO_LARGE = "BODY_TOO_LARGE"
SCRIPT_DENIED = "SCRIPT_DENIED"
ACTION_DENIED = "ACTION_DENIED"
