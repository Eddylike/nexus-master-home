"""Local, short-lived pairing for the scoped 005D PC command token.

There is deliberately no network operation to start pairing. The interactive
local CLI alone displays the code; the HTTP exchange returns the token once.
Persistent state contains a salted code digest, never the code or issued token.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATH = ROOT / "workspace" / "secrets" / "pc_remote_token.txt"
STATE_PATH = TOKEN_PATH.parent / "pc_remote_pairing.json"
LOCK_PATH = TOKEN_PATH.parent / "pc_remote_pairing.lock"
CODE_TTL = 120
MAX_ATTEMPTS = 5
_LOCK = threading.RLock()
_CODE = re.compile(r"[0-9]{12}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_NETS = tuple(ipaddress.ip_network(net) for net in (
    "127.0.0.0/8", "::1/128", "100.64.0.0/10", "fd7a:115c:a1e0::/48"))


class PairingError(Exception):
    """A safe error identifier; exception text never includes credentials."""


def _check_paths() -> None:
    for path in (TOKEN_PATH, STATE_PATH, LOCK_PATH):
        if path.is_symlink():
            raise PairingError("PC_PAIRING_UNSAFE_PATH")
        if os.name == "nt" and path.resolve().drive.upper() != "G:":
            raise PairingError("PC_PAIRING_STATE_MUST_BE_ON_G")


@contextmanager
def _locked():
    """Serialize both server threads and independently started local CLIs."""
    with _LOCK:
        _check_paths()
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(str(LOCK_PATH), os.O_RDWR | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "r+b") as stream:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"0")
                stream.flush()
            acquired = False
            deadline = time.monotonic() + 5
            try:
                while not acquired:
                    try:
                        stream.seek(0)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise PairingError("PC_PAIRING_BUSY") from None
                        time.sleep(0.02)
                yield
            finally:
                if acquired:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, content: str) -> None:
    _check_paths()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pc-pair-", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _save(state: dict[str, Any]) -> None:
    _atomic_write(STATE_PATH, json.dumps(state, allow_nan=False, separators=(",", ":")))


def _read_state() -> dict[str, Any] | None:
    if not STATE_PATH.exists():
        return None
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("version") != 1:
            raise ValueError
        if state.get("status") not in ("pending", "consumed", "expired", "locked", "invalid"):
            raise ValueError
        for key in ("code_hash", "salt"):
            if not isinstance(state.get(key), str) or not _DIGEST.fullmatch(state[key]):
                raise ValueError
        for key in ("created", "expires"):
            if type(state.get(key)) not in (int, float) or not math.isfinite(state[key]):
                raise ValueError
        if not 0 < state["expires"] - state["created"] <= CODE_TTL:
            raise ValueError
        if type(state.get("attempts")) is not int or not 0 <= state["attempts"] <= MAX_ATTEMPTS:
            raise ValueError
        fingerprint = state.get("token_fingerprint")
        if fingerprint is not None and (not isinstance(fingerprint, str) or not _DIGEST.fullmatch(fingerprint)):
            raise ValueError
        if type(state.get("replace")) is not bool or (fingerprint is not None and not state["replace"]):
            raise ValueError
        return state
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise PairingError("PC_PAIRING_STORE_CORRUPT") from None


def _fingerprint() -> str | None:
    try:
        # Hash raw bytes so even an external formatting change requires a new
        # conscious local start rather than silently overwriting another token.
        if TOKEN_PATH.stat().st_size > 8192:
            raise PairingError("PC_PAIRING_TOKEN_FILE_INVALID")
        return hashlib.sha256(TOKEN_PATH.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _environment_token() -> bool:
    # Even an invalid configured environment token is an explicit configuration.
    return bool((os.environ.get("NEXUS_PC_REMOTE_TOKEN") or "").strip())


def _code_hash(code: str, salt: str) -> str:
    return hmac.new(bytes.fromhex(salt), code.encode("ascii"), hashlib.sha256).hexdigest()


def start_pairing(replace: bool = False) -> dict[str, Any]:
    """Start only from a conscious local caller; never expose this over HTTP.

    ``replace=True`` asserts the CLI's explicit local replacement confirmation.
    An environment token cannot be replaced by writing the scoped token file.
    The returned code is for the local terminal only and must never be logged.
    """
    if type(replace) is not bool:
        raise PairingError("PC_PAIRING_REPLACE_REQUIRED")
    try:
        with _locked():
            if _environment_token():
                raise PairingError("PC_PAIRING_ENV_TOKEN_CONFIGURED")
            fingerprint = _fingerprint()
            if fingerprint is not None and not replace:
                raise PairingError("PC_PAIRING_REPLACE_REQUIRED")
            previous = _read_state()
            now = time.time()
            if (previous and previous["status"] == "pending" and previous["expires"] > now
                    and previous["attempts"] < MAX_ATTEMPTS and not replace):
                raise PairingError("PC_PAIRING_ALREADY_ACTIVE")
            code = f"{secrets.randbelow(10 ** 12):012d}"
            salt = secrets.token_hex(32)
            _save({"version": 1, "status": "pending", "created": now,
                   "expires": now + CODE_TTL, "attempts": 0, "salt": salt,
                   "code_hash": _code_hash(code, salt), "replace": replace,
                   "token_fingerprint": fingerprint})
            return {"success": True, "code": code, "expires_in": CODE_TTL,
                    "max_attempts": MAX_ATTEMPTS}
    except PairingError:
        raise
    except OSError:
        raise PairingError("PC_PAIRING_STORE_ERROR") from None


def _allowed(ip: str, headers: Any) -> bool:
    try:
        for name in headers or ():
            lowered = str(name).lower()
            if (lowered.startswith(("x-forwarded-", "tailscale-")) or lowered in (
                    "forwarded", "via", "x-real-ip", "cf-connecting-ip",
                    "x-original-forwarded-for", "x-tailscale-funnel-request")):
                return False
        address = ipaddress.ip_address((ip or "").split("%")[0])
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address.version == network.version and address in network for network in _NETS)
    except (ValueError, TypeError):
        return False


def exchange(body: Any, ip: str, headers: Any) -> tuple[int, dict[str, Any]]:
    """Consume a locally started code once, over direct Tailscale/loopback only."""
    def error(status: int, name: str):
        return status, {"success": False, "error": name}

    if not _allowed(ip, headers):
        return error(403, "NETWORK_NOT_ALLOWED")
    if not isinstance(body, dict) or set(body) != {"code"}:
        return error(400, "PC_PAIRING_INVALID_REQUEST")
    try:
        with _locked():
            if _environment_token():
                return error(409, "PC_PAIRING_ENV_TOKEN_CONFIGURED")
            state = _read_state()
            if not state or state["status"] in ("consumed", "invalid"):
                return error(409, "PC_PAIRING_NOT_ACTIVE")
            if state["status"] == "locked" or state["attempts"] >= MAX_ATTEMPTS:
                return error(429, "PC_PAIRING_ATTEMPTS_EXHAUSTED")
            if state["status"] == "expired" or state["expires"] <= time.time():
                state["status"] = "expired"
                _save(state)
                return error(410, "PC_PAIRING_EXPIRED")
            if state["token_fingerprint"] != _fingerprint():
                state["status"] = "invalid"
                _save(state)
                return error(409, "PC_PAIRING_CONFIGURATION_CHANGED")
            code = body["code"]
            valid = isinstance(code, str) and bool(_CODE.fullmatch(code))
            if not valid or not hmac.compare_digest(_code_hash(code, state["salt"]), state["code_hash"]):
                state["attempts"] += 1
                if state["attempts"] >= MAX_ATTEMPTS:
                    state["status"] = "locked"
                _save(state)
                return error(429 if state["status"] == "locked" else 401,
                             "PC_PAIRING_ATTEMPTS_EXHAUSTED" if state["status"] == "locked"
                             else "PC_PAIRING_CODE_INVALID")
            # Persist consumption first. A crash/write failure cannot issue a
            # second token on retry; the user starts a new local pairing instead.
            state["status"] = "consumed"
            _save(state)
            token = secrets.token_urlsafe(48)
            _atomic_write(TOKEN_PATH, token + "\n")
            return 200, {"success": True, "token": token, "token_type": "Bearer"}
    except PairingError as exc:
        return error(503, str(exc))
    except OSError:
        return error(503, "PC_PAIRING_STORE_ERROR")
