"""Phase 2: Memory (SQLite + Embeddings).

Tabellen: decisions, results, lessons, project_state.
Schreiben nur nach Validator-Ergebnis (record_validation) oder explizit durch Eddy.
Agenten lesen ausschliesslich ueber context_for()/search().
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from nexus.bauprotokoll.config import load_config
from nexus.bauprotokoll.ollama import OllamaClient
from nexus.bauprotokoll.vectors import cosine, dump_embedding, hashed_embedding, parse_embedding

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  datum TEXT NOT NULL, projekt TEXT, entscheidung TEXT NOT NULL,
  begruendung TEXT, ergebnis TEXT, embedding TEXT
);
CREATE TABLE IF NOT EXISTS results(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT, status TEXT NOT NULL, fehlerursache TEXT, loesung TEXT,
  datum TEXT NOT NULL, embedding TEXT
);
CREATE TABLE IF NOT EXISTS lessons(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL, tags TEXT, quelle TEXT, confidence REAL DEFAULT 1.0,
  datum TEXT NOT NULL, embedding TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lessons_text ON lessons(text);
CREATE TABLE IF NOT EXISTS project_state(
  projekt TEXT PRIMARY KEY, stand TEXT, offene_punkte TEXT, aktualisiert TEXT
);
CREATE INDEX IF NOT EXISTS idx_results_status ON results(status);
CREATE INDEX IF NOT EXISTS idx_results_task ON results(task_id);
"""

TABLES = {
    "decisions": ("entscheidung || ' ' || coalesce(begruendung,'') || ' ' || coalesce(ergebnis,'')"),
    "results": ("coalesce(task_id,'') || ' ' || coalesce(fehlerursache,'') || ' ' || coalesce(loesung,'')"),
    "lessons": ("text || ' ' || coalesce(tags,'')"),
}

_FIELD_FOR = {"decisions": "entscheidung", "results": "fehlerursache", "lessons": "text"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_error(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())[:500]


class Memory:
    def __init__(self, db_path: str | Path | None = None, config=None,
                 ollama=None, use_ollama: bool = True):
        cfg = config or load_config()
        self.db_path = Path(db_path or cfg.path_for("memory.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.dim = int(cfg.get("memory.embedding_dim", 64))
        self.top_k = int(cfg.get("memory.top_k", 5))
        self.embedding_model = cfg.get("models.embeddings", "nomic-embed-text")
        self.use_ollama = use_ollama
        self.ollama = ollama if ollama is not None else OllamaClient(cfg.get("ollama.url"))
        self._init()

    # ---------------------------------------------------------------- infra
    def _conn(self):
        con = sqlite3.connect(str(self.db_path), timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init(self):
        with self._conn() as con:
            con.executescript(SCHEMA)

    def embed(self, text: str):
        if self.use_ollama:
            vec = self.ollama.embed_or_none(text, self.embedding_model)
            if vec:
                return vec
        return hashed_embedding(text, self.dim)

    # ---------------------------------------------------------------- writes
    def add_decision(self, entscheidung: str, begruendung: str = "", projekt: str = "",
                     ergebnis: str = "", datum: str | None = None) -> int:
        with self._conn() as con:
            cur = con.execute(
                "INSERT INTO decisions(datum,projekt,entscheidung,begruendung,ergebnis,embedding)"
                " VALUES(?,?,?,?,?,?)",
                (datum or _now(), projekt, entscheidung, begruendung, ergebnis,
                 dump_embedding(self.embed(entscheidung + " " + begruendung))),
            )
            return int(cur.lastrowid)

    def add_result(self, task_id: str, status: str, fehlerursache: str = "",
                   loesung: str = "", datum: str | None = None) -> int:
        with self._conn() as con:
            cur = con.execute(
                "INSERT INTO results(task_id,status,fehlerursache,loesung,datum,embedding)"
                " VALUES(?,?,?,?,?,?)",
                (task_id, status, fehlerursache, loesung, datum or _now(),
                 dump_embedding(self.embed(fehlerursache + " " + loesung))),
            )
            return int(cur.lastrowid)

    def add_lesson(self, text: str, tags: str = "", quelle: str = "manual",
                   confidence: float = 1.0, datum: str | None = None) -> int:
        with self._conn() as con:
            try:
                cur = con.execute(
                    "INSERT INTO lessons(text,tags,quelle,confidence,datum,embedding)"
                    " VALUES(?,?,?,?,?,?)",
                    (text, tags, quelle, float(confidence), datum or _now(),
                     dump_embedding(self.embed(text))),
                )
                return int(cur.lastrowid)
            except sqlite3.IntegrityError:
                row = con.execute("SELECT id FROM lessons WHERE text=?", (text,)).fetchone()
                return int(row["id"])

    def record_validation(self, report: dict, projekt: str = "") -> dict:
        """Schreibt ein Validator-Ergebnis und markiert wiederkehrende Fehler als Lesson."""
        status = (report.get("status") or "").upper()
        if status not in ("PASS", "FAIL"):
            raise ValueError("Nur PASS/FAIL aus dem Validator darf gespeichert werden.")
        fehler, loesung = "", ""
        if status == "FAIL":
            last = (report.get("steps") or [{}])[-1]
            fehler = f"{last.get('step', '?')}: {last.get('detail', '')}"[:1000]
        else:
            loesung = "Validator PASS"
        result_id = self.add_result(report.get("task_id", "unknown"), status, fehler, loesung)
        lesson_id = None
        if status == "FAIL" and fehler:
            sig = _norm_error(fehler)
            with self._conn() as con:
                rows = con.execute(
                    "SELECT fehlerursache FROM results WHERE status='FAIL' AND task_id<>''"
                ).fetchall()
            count = sum(1 for r in rows if _norm_error(r["fehlerursache"]) == sig)
            if count >= 2:
                lesson_id = self.add_lesson(
                    f"Wiederkehrender Fehler: {fehler}",
                    tags="auto,fehler", quelle="auto:memory", confidence=0.6,
                )
        return {"result_id": result_id, "status": status, "lesson_id": lesson_id}

    def set_project_state(self, projekt: str, stand: str, offene_punkte: str = "") -> None:
        with self._conn() as con:
            con.execute(
                "INSERT INTO project_state(projekt,stand,offene_punkte,aktualisiert)"
                " VALUES(?,?,?,?) ON CONFLICT(projekt) DO UPDATE SET"
                " stand=excluded.stand, offene_punkte=excluded.offene_punkte,"
                " aktualisiert=excluded.aktualisiert",
                (projekt, stand, offene_punkte, _now()),
            )

    # ----------------------------------------------------------------- reads
    def get_project_state(self, projekt: str) -> dict | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM project_state WHERE projekt=?", (projekt,)).fetchone()
        return dict(row) if row else None

    def search(self, table: str, query: str, top_k: int | None = None) -> list[dict]:
        if table not in TABLES:
            raise ValueError(f"Unbekannte Tabelle: {table}")
        limit = top_k or self.top_k
        qvec = self.embed(query)
        with self._conn() as con:
            rows = con.execute(f"SELECT * FROM {table}").fetchall()
        scored = []
        for row in rows:
            item = dict(row)
            vec = parse_embedding(item.get("embedding")) or self.embed(
                " ".join(str(item.get(f) or "") for f in item if f != "embedding")
            )
            item["score"] = round(cosine(qvec, vec), 4)
            item.pop("embedding", None)
            scored.append(item)
        scored.sort(key=lambda i: i["score"], reverse=True)
        return scored[:limit]

    def context_for(self, task: str, top_k: int | None = None) -> dict:
        limit = top_k or self.top_k
        with self._conn() as con:
            states = [dict(r) for r in con.execute(
                "SELECT * FROM project_state ORDER BY aktualisiert DESC LIMIT ?", (limit,)
            ).fetchall()]
        return {
            "decisions": self.search("decisions", task, limit),
            "results": self.search("results", task, limit),
            "lessons": self.search("lessons", task, limit),
            "project_state": states,
        }

    def stats(self) -> dict:
        with self._conn() as con:
            return {
                table: con.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                for table in ("decisions", "results", "lessons", "project_state")
            }
