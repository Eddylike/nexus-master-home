"""Lokaler RAG-Speicher auf SQLite-Basis.

Standard ist FTS5 (kein Zusatzpaket). Wenn FTS5 fehlt, faellt die Suche ehrlich
auf LIKE zurueck. Chroma/Qdrant koennen spaeter als Adapter ergaenzt werden,
sind aber keine Voraussetzung.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from typing import Any

from nexus.omega import config, events

_LOCK = threading.RLock()
_INIT: set[str] = set()
_WORD = re.compile(r"[A-Za-z0-9\u00e4\u00f6\u00fc\u00df_]{2,}")


def _conn() -> sqlite3.Connection:
    path = config.db_path()
    key = str(path)
    conn = events._connect()
    if key not in _INIT:
        with _LOCK:
            if key not in _INIT:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS rag ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, title TEXT, "
                    "content TEXT NOT NULL, meta_json TEXT NOT NULL DEFAULT '{}', created TEXT NOT NULL)"
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_source ON rag(source)")
                try:
                    conn.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS rag_fts USING fts5("
                        "title, content, content='rag', content_rowid='id')"
                    )
                    conn.executescript(
                        "CREATE TRIGGER IF NOT EXISTS rag_ai AFTER INSERT ON rag BEGIN "
                        "INSERT INTO rag_fts(rowid, title, content) VALUES (new.id, new.title, new.content); END; "
                        "CREATE TRIGGER IF NOT EXISTS rag_ad AFTER DELETE ON rag BEGIN "
                        "INSERT INTO rag_fts(rag_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content); END; "
                        "CREATE TRIGGER IF NOT EXISTS rag_au AFTER UPDATE ON rag BEGIN "
                        "INSERT INTO rag_fts(rag_fts, rowid, title, content) VALUES('delete', old.id, old.title, old.content); "
                        "INSERT INTO rag_fts(rowid, title, content) VALUES (new.id, new.title, new.content); END;"
                    )
                except sqlite3.Error:
                    pass
                conn.commit()
                _INIT.add(key)
    return conn


def _fts_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("SELECT rowid FROM rag_fts LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


def add_document(source: str, title: str, content: str, meta: dict | None = None) -> dict:
    text = str(content or "").strip()
    if not text:
        return {"success": False, "status": "EMPTY", "error": "content_required"}
    with _LOCK, _conn() as conn:
        cur = conn.execute(
            "INSERT INTO rag(source, title, content, meta_json, created) VALUES (?, ?, ?, ?, ?)",
            (str(source or "external")[:500], str(title or "")[:500], text[:400_000],
             events._json(meta or {}), events.now()),
        )
        doc_id = int(cur.lastrowid)
    return {"success": True, "status": "OK", "id": doc_id, "chars": len(text)}


def search(query: str, limit: int = 5) -> list[dict]:
    words = _WORD.findall(str(query or ""))
    if not words:
        return []
    limit = max(1, min(int(limit), 50))
    with _conn() as conn:
        if _fts_available(conn):
            fts_query = " OR ".join('"' + w.replace('"', "") + '"' for w in words[:12])
            try:
                rows = conn.execute(
                    "SELECT r.id, r.source, r.title, r.created, "
                    "snippet(rag_fts, 1, '[', ']', ' ... ', 16) AS snippet, "
                    "bm25(rag_fts) AS rank FROM rag_fts "
                    "JOIN rag r ON r.id = rag_fts.rowid WHERE rag_fts MATCH ? "
                    "ORDER BY rank LIMIT ?",
                    (fts_query, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.Error:
                pass
        clauses = " OR ".join(["content LIKE ?"] * len(words[:12]))
        params = [f"%{w}%" for w in words[:12]]
        rows = conn.execute(
            f"SELECT id, source, title, created, substr(content, 1, 600) AS snippet "
            f"FROM rag WHERE {clauses} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def count() -> int:
    try:
        with _conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM rag").fetchone()[0])
    except sqlite3.Error:
        return 0


def recent(limit: int = 20) -> list[dict]:
    try:
        with _conn() as conn:
            rows = conn.execute(
                "SELECT id, source, title, created, length(content) AS chars "
                "FROM rag ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []
