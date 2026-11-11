"""Slim token-gated inbox for the NEXUS Live Talk Android app.

Uploads land under workspace/inbox/mobile (never on C:). Auth uses the existing
Live Talk device/app token (Authorization: Bearer). This module is intentionally
outside nexus.livetalk so the parallel Live Talk strand can keep editing that tree.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "workspace" / "inbox" / "mobile"
OUTBOX = INBOX / "outbox"
LOG = INBOX / "inbox.jsonl"
MAX_BYTES = 25 * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp"}


def _must_g(path: Path) -> Path:
    resolved = path.resolve()
    if os.name == "nt" and resolved.drive.upper() != "G:":
        raise RuntimeError("INBOX_MUST_BE_ON_G")
    return resolved


def _ensure_dirs() -> None:
    inbox = _must_g(INBOX)
    outbox = _must_g(OUTBOX)
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    raw = (name or "datei").replace("\\", "/").split("/")[-1]
    stem, dot, ext = raw.rpartition(".")
    if not dot:
        stem, ext = raw, ""
    else:
        ext = "." + ext
    stem = _SAFE.sub("_", stem).strip("._")[:80] or "datei"
    ext = _SAFE.sub("", ext)[:8]
    if ext and not ext.startswith("."):
        ext = "." + ext
    return stem + ext.lower()


def _kind(name: str, mime: str) -> str:
    mime = (mime or "").lower()
    ext = Path(name).suffix.lower()
    if mime.startswith("image/") or ext in _IMAGE_EXT:
        return "image"
    if mime.startswith("video/") or ext in {".mp4", ".mov", ".mkv", ".webm"}:
        return "video"
    if mime.startswith("audio/") or ext in {".m4a", ".mp3", ".wav", ".ogg", ".aac"}:
        return "audio"
    return "file"


def _token_kind(handler: Any) -> str | None:
    from nexus.livetalk.auth import check_token, is_allowed_client
    ip = handler.client_address[0] if handler.client_address else ""
    if not is_allowed_client(ip, handler.headers):
        return None
    hdr = handler.headers.get("Authorization") or handler.headers.get("authorization") or ""
    tok = ""
    if hdr.lower().startswith("bearer "):
        tok = hdr[7:].strip()
    if not tok:
        tok = (handler.headers.get("X-LiveTalk-Token") or "").strip()
    return check_token(tok)


def _append_log(rec: dict) -> None:
    _ensure_dirs()
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _must_g(LOG).open("a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())



_LISTENERS: list = []


def on_upload(fn) -> None:
    """Live-Talk-Sitzungen koennen sich fuer neue Inbox-Dateien anmelden."""
    if fn not in _LISTENERS:
        _LISTENERS.append(fn)


def off_upload(fn) -> None:
    _LISTENERS[:] = [x for x in _LISTENERS if x is not fn]


def _emit(rec: dict) -> None:
    try:
        from nexus.common import live_events as _le
        kind = rec.get("kind") or "file"
        name = rec.get("name") or rec.get("saved_as") or "Datei"
        text = ("Foto erhalten: " + name) if kind == "image" else ("Datei erhalten: " + name)
        _le.emit("NEXUS-LiveTalk", "phone_file", text, target="NEXUS", ok=True, data=rec)
    except Exception:
        pass
    for fn in list(_LISTENERS):
        try:
            fn(rec)
        except Exception:
            pass


def put_outbox(src, name: str | None = None) -> dict:
    """Kopiert eine Datei (nur G:) in workspace/inbox/mobile/outbox/."""
    from pathlib import Path as _P
    src_p = _P(src)
    if not src_p.is_file():
        return {"success": False, "error": "not_found", "reply": "Die Datei habe ich nicht gefunden."}
    try:
        resolved = src_p.resolve()
        if os.name == "nt" and resolved.drive.upper() != "G:":
            return {"success": False, "error": "C_TABU",
                    "reply": "Die Datei liegt nicht auf dem erlaubten Laufwerk."}
    except Exception as e:
        return {"success": False, "error": type(e).__name__, "reply": "Die Datei konnte ich nicht pruefen."}
    _ensure_dirs()
    dest_name = _safe_name(name or src_p.name)
    dest = _must_g(_unique(OUTBOX, dest_name))
    if OUTBOX.resolve() not in dest.parents and dest.parent.resolve() != OUTBOX.resolve():
        return {"success": False, "error": "OUTBOX_PATH_ESCAPE", "reply": "Zielpfad ungueltig."}
    dest.write_bytes(src_p.read_bytes())
    rec = {
        "success": True,
        "event": "outbox",
        "name": dest.name,
        "original": src_p.name,
        "path": str(dest),
        "rel": "workspace/inbox/mobile/outbox/" + dest.name,
        "size": dest.stat().st_size,
        "kind": _kind(dest.name, ""),
        "reply": f"Die Datei {dest.name} liegt bereit und geht aufs Handy.",
    }
    _append_log(rec)
    return rec


def _unique(dirpath: Path, name: str) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}_{name}"
    path = dirpath / base
    n = 1
    while path.exists():
        path = dirpath / f"{stamp}_{n}_{name}"
        n += 1
    return path


def _list_dir(dirpath: Path, role: str) -> list[dict]:
    if not dirpath.is_dir():
        return []
    items = []
    for p in sorted(dirpath.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not p.is_file() or p.name == "inbox.jsonl" or p.name.startswith("."):
            continue
        if dirpath == INBOX and p.parent.resolve() != INBOX.resolve():
            continue
        st = p.stat()
        items.append({
            "name": p.name,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "kind": _kind(p.name, ""),
            "role": role,
        })
    return items[:200]


def _parse_multipart(body: bytes, content_type: str) -> list[dict]:
    m = re.search(r"boundary=([^;]+)", content_type or "", re.I)
    if not m:
        return []
    boundary = m.group(1).strip().strip('"').encode("ascii", "ignore")
    if not boundary:
        return []
    parts = body.split(b"--" + boundary)
    out = []
    for part in parts:
        if not part or part in (b"--", b"--\r\n", b"\r\n"):
            continue
        if part.startswith(b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        header_txt = head.decode("utf-8", "replace")
        disp = ""
        ctype = "application/octet-stream"
        for line in header_txt.split("\r\n"):
            low = line.lower()
            if low.startswith("content-disposition:"):
                disp = line
            elif low.startswith("content-type:"):
                ctype = line.split(":", 1)[1].strip()
        fn = ""
        mfn = re.search(r'filename\*=UTF-8\'\'([^;]+)', disp, re.I)
        if mfn:
            fn = unquote(mfn.group(1).strip())
        else:
            mfn = re.search(r'filename="([^"]*)"', disp, re.I)
            if mfn:
                fn = mfn.group(1)
            else:
                mfn = re.search(r"filename=([^;]+)", disp, re.I)
                if mfn:
                    fn = mfn.group(1).strip().strip('"')
        if not fn:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        out.append({"filename": fn, "mime": ctype, "data": data})
    return out


def _save_one(filename: str, mime: str, data: bytes) -> dict:
    if data is None:
        raise ValueError("empty")
    if len(data) > MAX_BYTES:
        raise ValueError("too_large")
    _ensure_dirs()
    name = _safe_name(filename)
    dest = _must_g(_unique(INBOX, name))
    if INBOX.resolve() not in dest.parents and dest.parent.resolve() != INBOX.resolve():
        raise RuntimeError("INBOX_PATH_ESCAPE")
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    rec = {
        "event": "uploaded",
        "type": "phone_file",
        "name": name,
        "original": filename,
        "saved_as": dest.name,
        "path": str(dest),
        "rel": "workspace/inbox/mobile/" + dest.name,
        "mime": mime or "application/octet-stream",
        "size": len(data),
        "kind": _kind(name, mime or ""),
        "ts": time.time(),
        "analyze": _kind(name, mime or "") == "image",
        "analyze_with": "llava_if_available",
    }
    _append_log(rec)
    _emit(rec)
    return rec


def _under(base: Path, name: str) -> Path | None:
    safe = _safe_name(unquote(name or ""))
    if not safe or safe.startswith("."):
        return None
    target = _must_g(base / safe)
    if target.parent.resolve() != base.resolve() or not target.is_file():
        return None
    return target


def _mime_of(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".gif": "image/gif", ".webp": "image/webp", ".heic": "image/heic",
        ".mp4": "video/mp4", ".pdf": "application/pdf",
        ".txt": "text/plain; charset=utf-8", ".json": "application/json",
        ".m4a": "audio/mp4", ".mp3": "audio/mpeg",
    }.get(ext, "application/octet-stream")


def handle_http(handler: Any, method: str, path: str) -> bool:
    """Return True if the request was handled (including auth failures)."""
    parsed = urlparse(getattr(handler, "path", path) or path)
    path = parsed.path
    inbox = path == "/api/livetalk/inbox" or path.startswith("/api/livetalk/inbox/")
    outbox = path == "/api/livetalk/outbox" or path.startswith("/api/livetalk/outbox/")
    if not inbox and not outbox:
        return False

    kind = _token_kind(handler)
    if not kind:
        handler._json(401, {"success": False, "error": "LIVETALK_TOKEN_REQUIRED"})
        return True

    try:
        _ensure_dirs()
        if method == "GET" and inbox:
            handler._json(200, {"success": True, "role": "inbox", "auth": kind,
                                "items": _list_dir(INBOX, "inbox")})
            return True
        if method == "GET" and path == "/api/livetalk/outbox":
            handler._json(200, {"success": True, "role": "outbox", "auth": kind,
                                "items": _list_dir(OUTBOX, "outbox")})
            return True
        if method == "GET" and path.startswith("/api/livetalk/outbox/"):
            name = path[len("/api/livetalk/outbox/"):]
            target = _under(OUTBOX, name)
            if target is None:
                handler._json(404, {"success": False, "error": "not_found"})
                return True
            data = target.read_bytes()
            handler._bytes(200, data, _mime_of(target))
            return True
        if method == "POST" and inbox:
            length = int(handler.headers.get("Content-Length") or 0)
            if length <= 0:
                handler._json(400, {"success": False, "error": "empty_body"})
                return True
            if length > MAX_BYTES:
                handler._json(413, {"success": False, "error": "too_large", "max": MAX_BYTES})
                return True
            body = handler.rfile.read(length)
            ctype = handler.headers.get("Content-Type") or ""
            saved = []
            if "multipart/form-data" in ctype.lower():
                parts = _parse_multipart(body, ctype)
                if not parts:
                    handler._json(400, {"success": False, "error": "no_file_part"})
                    return True
                for part in parts:
                    saved.append(_save_one(part["filename"], part.get("mime") or "", part["data"]))
            else:
                fn = handler.headers.get("X-Filename") or "datei.bin"
                saved.append(_save_one(fn, ctype, body))
            msg = "Foto erhalten" if any(x.get("kind") == "image" for x in saved) else "Datei erhalten"
            handler._json(200, {"success": True, "message": msg, "files": saved, "auth": kind})
            return True
        handler._json(405, {"success": False, "error": "method_not_allowed"})
        return True
    except ValueError as e:
        code = 413 if str(e) == "too_large" else 400
        handler._json(code, {"success": False, "error": str(e)})
        return True
    except Exception as e:
        handler._json(500, {"success": False, "error": "inbox_error", "detail": type(e).__name__})
        return True