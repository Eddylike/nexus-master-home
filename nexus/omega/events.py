"""SQLite-Logging fuer OMEGA: Runs, Tool-Calls, Hooks, Cache, Rate-Limit.

Best effort: Logging darf einen Tool-Aufruf nie zum Absturz bringen. Jede
Operation oeffnet eine eigene Verbindung (WAL), damit Threads sich nicht
gegenseitig blockieren.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from typing import Any

from nexus.omega import config

_LOCK = threading.RLock()
_READY: set[str] = set()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    created TEXT NOT NULL,
    updated TEXT NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}',
    state_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    tool TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    approval_id TEXT,
    started TEXT NOT NULL,
    finished TEXT,
    duration_ms INTEGER,
    result_json TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS hooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    phase TEXT NOT NULL,
    tool TEXT,
    verdict TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    expires REAL NOT NULL,
    created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_key_ts ON rate_events(key, ts);
CREATE INDEX IF NOT EXISTS idx_calls_run ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_hooks_run ON hooks(run_id);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    created TEXT NOT NULL,
    expires REAL NOT NULL,
    decided TEXT,
    decided_by TEXT
);
"""


def _connect() -> sqlite3.Connection:
    path = config.db_path()
    key = str(path)
    if key not in _READY:
        with _LOCK:
            if key not in _READY:
                conn = sqlite3.connect(path, timeout=15)
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(_SCHEMA)
                    conn.commit()
                finally:
                    conn.close()
                _READY.add(key)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id(prefix: str = "RUN") -> str:
    return f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


_SECRET_KEY = re.compile(r"(?i)(?:token|secret|password|passwd|authorization|api[_-]?key|client[_-]?secret|private[_-]?key|cookie|webhook)")
_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_SLACK_HOOK = re.compile(r"https?://hooks\.slack\.com/services/[A-Za-z0-9/_-]+", re.I)
_QUERY_SECRET = re.compile(r"(?i)([?&](?:access_token|api_key|apikey|token|secret|signature|sig|key|auth|password|client_secret)=)[^&#\s]+")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{16,}|sk-[A-Za-z0-9]{20,})\b")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): ("[REDACTED]" if _SECRET_KEY.search(str(k)) else _redact(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        text = _BEARER.sub(r"\1[REDACTED]", value)
        text = _SLACK_HOOK.sub("https://hooks.slack.com/services/[REDACTED]", text)
        text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
        text = _GITHUB_TOKEN.sub("[REDACTED_TOKEN]", text)
        stripped = text.lstrip()
        if len(text) <= 100_000 and stripped[:1] in ("{", "["):
            try:
                parsed = json.loads(text)
                return json.dumps(_redact(parsed), ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                pass
        return text
    return value


def _json(value: Any, limit: int = 60_000) -> str:
    try:
        text = json.dumps(_redact(value), ensure_ascii=False, default=str)
    except Exception:
        text = json.dumps({"_unserializable": type(value).__name__})
    return text[:limit]


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


# --- Runs -------------------------------------------------------------------
def start_run(goal: str, meta: dict | None = None, state: dict | None = None) -> dict:
    run_id = new_id("RUN")
    stamp = now()
    try:
        with _LOCK, _connect() as conn:
            conn.execute(
                "INSERT INTO runs(id, goal, status, created, updated, meta_json, state_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, str(_redact(str(goal)))[:8_000], "running", stamp, stamp,
                 _json(meta or {}), _json(state or {})),
            )
    except sqlite3.Error:
        pass
    return {"id": run_id, "goal": str(goal), "status": "running", "created": stamp,
            "updated": stamp, "meta": meta or {}, "state": state or {}}


def save_run(run_id: str, *, status: str | None = None, state: dict | None = None,
             result: dict | None = None, error: str | None = None) -> None:
    try:
        with _LOCK, _connect() as conn:
            row = conn.execute("SELECT status, state_json FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return
            conn.execute(
                "UPDATE runs SET status=?, updated=?, state_json=?, "
                "result_json=COALESCE(?, result_json), error=? WHERE id=?",
                (status or row["status"], now(),
                 _json(state) if state is not None else row["state_json"],
                 _json(result) if result is not None else None,
                 str(error)[:4_000] if error else None, run_id),
            )
    except sqlite3.Error:
        pass


def get_run(run_id: str) -> dict | None:
    try:
        with _connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["meta"] = _loads(out.pop("meta_json"), {})
        out["state"] = _loads(out.pop("state_json"), {})
        out["result"] = _loads(out.pop("result_json"), None)
        return out
    except sqlite3.Error:
        return None


def list_runs(limit: int = 25) -> list[dict]:
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT id, goal, status, created, updated, error FROM runs ORDER BY created DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


# --- Tool calls / hooks -----------------------------------------------------
def log_call(run_id: str | None, tool: str, params: dict, *, status: str,
             approval_id: str | None = None, duration_ms: int | None = None,
             result: Any = None, error: str | None = None) -> None:
    try:
        with _LOCK, _connect() as conn:
            conn.execute(
                "INSERT INTO tool_calls(run_id, tool, params_json, status, approval_id, started, "
                "finished, duration_ms, result_json, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, tool, _json(params), status, approval_id, now(), now(), duration_ms,
                 _json(result) if result is not None else None,
                 str(_redact(str(error)))[:4_000] if error else None),
            )
    except sqlite3.Error:
        pass


def log_hook(run_id: str | None, phase: str, tool: str | None, verdict: str,
             details: dict | None = None) -> None:
    try:
        with _LOCK, _connect() as conn:
            conn.execute(
                "INSERT INTO hooks(run_id, phase, tool, verdict, details_json, created) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, phase, tool, verdict, _json(details or {}), now()),
            )
    except sqlite3.Error:
        pass


def list_calls(run_id: str | None = None, limit: int = 100) -> list[dict]:
    try:
        with _connect() as conn:
            if run_id:
                rows = conn.execute(
                    "SELECT * FROM tool_calls WHERE run_id=? ORDER BY id DESC LIMIT ?",
                    (run_id, max(1, min(int(limit), 1000))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tool_calls ORDER BY id DESC LIMIT ?",
                    (max(1, min(int(limit), 1000)),),
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["params"] = _loads(item.pop("params_json"), {})
            item["result"] = _loads(item.pop("result_json"), None)
            out.append(item)
        return out
    except sqlite3.Error:
        return []


def list_hooks(run_id: str | None = None, limit: int = 100) -> list[dict]:
    try:
        with _connect() as conn:
            if run_id:
                rows = conn.execute(
                    "SELECT * FROM hooks WHERE run_id=? ORDER BY id DESC LIMIT ?",
                    (run_id, max(1, min(int(limit), 1000))),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM hooks ORDER BY id DESC LIMIT ?",
                    (max(1, min(int(limit), 1000)),),
                ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["details"] = _loads(item.pop("details_json"), {})
            out.append(item)
        return out
    except sqlite3.Error:
        return []


# --- Cache / Rate-Limit -----------------------------------------------------
def cache_get(key: str) -> Any | None:
    try:
        with _connect() as conn:
            row = conn.execute("SELECT value_json, expires FROM cache WHERE key=?", (key,)).fetchone()
        if not row or float(row["expires"]) < time.time():
            return None
        return _loads(row["value_json"], None)
    except sqlite3.Error:
        return None


def cache_set(key: str, value: Any, ttl_s: float = 300) -> None:
    try:
        with _LOCK, _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache(key, value_json, expires, created) VALUES (?, ?, ?, ?)",
                (key, _json(value), time.time() + max(0.0, float(ttl_s)), now()),
            )
    except sqlite3.Error:
        pass


def rate_allow(key: str, limit: int, window_s: float) -> bool:
    limit = max(1, int(limit))
    window_s = max(1.0, float(window_s))
    cutoff = time.time() - window_s
    try:
        with _LOCK, _connect() as conn:
            conn.execute("DELETE FROM rate_events WHERE ts < ?", (cutoff,))
            count = conn.execute("SELECT COUNT(*) FROM rate_events WHERE key=? AND ts >= ?",
                                 (key, cutoff)).fetchone()[0]
            if count >= limit:
                return False
            conn.execute("INSERT INTO rate_events(key, ts) VALUES (?, ?)", (key, time.time()))
        return True
    except sqlite3.Error:
        # Logging-Ausfall darf externe Calls nicht unbegrenzt lassen: konservativ erlauben.
        return True


# --- Approval shadow table --------------------------------------------------
def record_approval(approval_id: str, tool: str, params: dict, reason: str,
                    status: str = "pending", expires: float = 0) -> None:
    try:
        with _LOCK, _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO approvals(id, tool, params_json, reason, status, created, expires) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (approval_id, tool, _json(params), str(_redact(reason))[:2_000], status, now(), float(expires)),
            )
    except sqlite3.Error:
        pass


def set_approval_status(approval_id: str, status: str, decided_by: str = "") -> None:
    try:
        with _LOCK, _connect() as conn:
            conn.execute(
                "UPDATE approvals SET status=?, decided=?, decided_by=? WHERE id=?",
                (status, now(), decided_by, approval_id),
            )
    except sqlite3.Error:
        pass


def list_approvals(limit: int = 50) -> list[dict]:
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT * FROM approvals ORDER BY created DESC LIMIT ?",
                                (max(1, min(int(limit), 500)),)).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["params"] = _loads(item.pop("params_json"), {})
            out.append(item)
        return out
    except sqlite3.Error:
        return []
