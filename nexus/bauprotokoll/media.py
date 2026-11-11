"""Phase 7a: NEXUS All - Medienindex (SQLite).

Indexiert Fotos/Dokumente (Pfad, Hash, Groesse, EXIF, Tags), erkennt Duplikate,
sucht per Text/Embedding und exportiert Stock-Metadaten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from nexus.bauprotokoll.config import load_config
from nexus.bauprotokoll.vectors import cosine, dump_embedding, hashed_embedding, parse_embedding

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
DOC_EXT = {".pdf", ".md", ".txt", ".docx", ".odt"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT UNIQUE NOT NULL,
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL,
  mtime REAL NOT NULL,
  kind TEXT,
  exif TEXT,
  tags TEXT,
  embedding TEXT,
  imported TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_hash ON assets(sha256);
CREATE INDEX IF NOT EXISTS idx_assets_kind ON assets(kind);
"""


def file_hash(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def read_exif(path: Path) -> dict:
    try:
        from PIL import Image, ExifTags
    except Exception:
        return {}
    try:
        with Image.open(path) as img:
            raw = img.getexif()
            return {ExifTags.TAGS.get(k, str(k)): str(v) for k, v in raw.items()}
    except Exception:
        return {}


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in DOC_EXT:
        return "document"
    if ext in VIDEO_EXT:
        return "video"
    return "other"


class MediaIndex:
    def __init__(self, db_path=None, root=None, config=None):
        cfg = config or load_config()
        self.root = Path(root or cfg.root).resolve()
        self.db_path = Path(db_path or (cfg.state_dir / "media.db"))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as con:
            con.executescript(SCHEMA)

    def _conn(self):
        con = sqlite3.connect(str(self.db_path), timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def _rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    def index_file(self, path, tags: str = "") -> dict:
        p = Path(path).resolve()
        if not p.is_file():
            raise FileNotFoundError(p)
        rel = self._rel(p)
        digest = file_hash(p)
        exif = read_exif(p) if kind_of(p) == "image" else {}
        text = " ".join([p.name, tags, kind_of(p)])
        with self._conn() as con:
            dup = con.execute(
                "SELECT id FROM assets WHERE sha256=? AND path<>?", (digest, rel)
            ).fetchone()
            con.execute(
                "INSERT INTO assets(path,sha256,size,mtime,kind,exif,tags,embedding,imported)"
                " VALUES(?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256,size=excluded.size,"
                " mtime=excluded.mtime,kind=excluded.kind,exif=excluded.exif,"
                " tags=excluded.tags,embedding=excluded.embedding,imported=excluded.imported",
                (rel, digest, p.stat().st_size, p.stat().st_mtime, kind_of(p),
                 json.dumps(exif, ensure_ascii=False), tags,
                 dump_embedding(hashed_embedding(text)),
                 datetime.now(timezone.utc).isoformat()),
            )
            row = con.execute("SELECT * FROM assets WHERE path=?", (rel,)).fetchone()
        return {"id": row["id"], "path": rel, "sha256": digest,
                "duplicate_of": dup["id"] if dup else None}

    def index_dir(self, directory, recursive: bool = True, tags: str = "") -> dict:
        base = Path(directory)
        pattern = "**/*" if recursive else "*"
        added, duplicates, errors = 0, 0, []
        for p in base.glob(pattern):
            if not p.is_file():
                continue
            try:
                res = self.index_file(p, tags=tags)
            except Exception as e:
                errors.append(f"{p}: {e}")
                continue
            added += 1
            if res["duplicate_of"]:
                duplicates += 1
        return {"indexed": added, "duplicates": duplicates, "errors": errors}

    def all(self, kind: str | None = None) -> list[dict]:
        with self._conn() as con:
            if kind:
                rows = con.execute("SELECT * FROM assets WHERE kind=? ORDER BY id", (kind,)).fetchall()
            else:
                rows = con.execute("SELECT * FROM assets ORDER BY id").fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["exif"] = json.loads(item.get("exif") or "{}")
            item["tag_list"] = [t.strip() for t in (item.get("tags") or "").split(",") if t.strip()]
            item.pop("embedding", None)
            out.append(item)
        return out

    def duplicates(self) -> list[dict]:
        with self._conn() as con:
            rows = con.execute(
                "SELECT sha256, COUNT(*) n, GROUP_CONCAT(path, '|') paths FROM assets"
                " GROUP BY sha256 HAVING n > 1 ORDER BY n DESC"
            ).fetchall()
        return [{"sha256": r["sha256"], "count": r["n"], "paths": r["paths"].split("|")}
                for r in rows]

    def add_tags(self, path: str, tags: str) -> bool:
        rel = self._rel(Path(path)) if Path(path).is_absolute() else path.replace("\\", "/")
        with self._conn() as con:
            row = con.execute("SELECT tags FROM assets WHERE path=?", (rel,)).fetchone()
            if not row:
                return False
            merged = [t for t in (row["tags"] or "").split(",") if t.strip()]
            for t in tags.split(","):
                t = t.strip()
                if t and t not in merged:
                    merged.append(t)
            con.execute("UPDATE assets SET tags=? WHERE path=?", (",".join(merged), rel))
        return True

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        q = hashed_embedding(query)
        scored = []
        for item in self.all():
            text = f"{item['path']} {item['tags']} {item['kind']}"
            score = cosine(q, hashed_embedding(text))
            if score > 0:
                item["score"] = round(score, 4)
                scored.append(item)
        scored.sort(key=lambda i: i["score"], reverse=True)
        return scored[:top_k]

    def export_stock(self, out_path, assets=None) -> list[dict]:
        rows = assets if assets is not None else self.all()
        out = []
        for item in rows:
            stem = Path(item["path"]).stem
            keywords = item.get("tag_list") or []
            out.append({
                "datei": item["path"],
                "titel": stem.replace("_", " ").replace("-", " ").strip().title(),
                "keywords": sorted(set(keywords + [item.get("kind", "")])),
                "quelle": "NEXUS All",
            })
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return out

    def stats(self) -> dict:
        with self._conn() as con:
            total = con.execute("SELECT COUNT(*) n FROM assets").fetchone()["n"]
            kinds = {r["kind"]: r["n"] for r in con.execute(
                "SELECT kind, COUNT(*) n FROM assets GROUP BY kind")}
            dup = con.execute(
                "SELECT COUNT(*) n FROM (SELECT sha256 FROM assets GROUP BY sha256"
                " HAVING COUNT(*)>1)"
            ).fetchone()["n"]
        return {"total": total, "kinds": kinds, "duplicate_groups": dup}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NEXUS All Medienindex")
    ap.add_argument("--index", metavar="DIR")
    ap.add_argument("--tags", default="")
    ap.add_argument("--search", metavar="TEXT")
    ap.add_argument("--duplicates", action="store_true")
    ap.add_argument("--export", metavar="DATEI")
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    idx = MediaIndex(db_path=a.db)
    if a.index:
        print(json.dumps(idx.index_dir(a.index, tags=a.tags), ensure_ascii=False))
    if a.search:
        print(json.dumps(idx.search(a.search), indent=2, ensure_ascii=False))
    if a.duplicates:
        print(json.dumps(idx.duplicates(), indent=2, ensure_ascii=False))
    if a.export:
        print(f"{len(idx.export_stock(a.export))} Eintraege -> {a.export}")
    if not any((a.index, a.search, a.duplicates, a.export)):
        print(json.dumps(idx.stats(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
