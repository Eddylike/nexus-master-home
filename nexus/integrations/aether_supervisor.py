"""AETHER Supervisor + AgentFactory (MVP Schritt 1+2, 2026-11-02).

Qwen (qwen3.5:9b, lokal) entscheidet nur per JSON (self/spawn/call/final).
Eine deterministische AgentFactory baut Worker NUR aus festen Templates
(researcher, fileops, reviewer). Freie Specs: Feature-Flag ALLOW_FREE_SPECS=False.
PolicyGuard prueft unter dem Modell: Tool erlaubt, Tiefe, Netzwerk, Destruktiv->approval_id.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
import threading
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from nexus.safety.budget import Budget, BudgetExceeded

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "workspace" / "aether_supervisor"
OUT_DIR = WORK / "out"            # einziger SAFE_WRITE-Bereich
LOG = ROOT / "workspace" / "logs" / "aether_supervisor.jsonl"
APPROVALS = WORK / "approvals.json"
SANDBOX = ROOT / "workspace" / "sandbox"   # Team-Sandbox: frei arbeiten, kein approval
BACKUP_DIR = ROOT / "workspace" / "backups" / "aether_special"
MODEL = "qwen3.5:9b"
TEMPERATURE = 0.2
LLM_TIMEOUT = 120
RUN_TIMEOUT = 600
SUPERVISOR_MAX_STEPS = 8
MAX_AGENTS = 6
MAX_DEPTH = 2
MAX_TOOLS_PER_WORKER = 8
ALLOW_FREE_SPECS = False          # MVP Schritt 3 (freie Specs) bewusst AUS
_LOCK = threading.Lock()


def swarm_enabled() -> bool:
    """Phase 1: Budget, Blackboard, strenge Belegpruefung. Default AUS (Rollback)."""
    return os.environ.get("NEXUS_SWARM", "0").strip() != "0"


def gateway_enabled() -> bool:
    """Phase 1 WP5: Worker rufen Werkzeuge nur noch ueber das Gateway auf."""
    return os.environ.get("NEXUS_TOOL_GATEWAY", "1").strip() != "0"

_SECRET_RE = re.compile(r"(?i)(api[_-]?key|token|secret|passw(or)?d|bearer|authorization)\s*[:=]\s*\S+|sk-[A-Za-z0-9]{12,}|gh[pousr]_[A-Za-z0-9]{20,}")
_SECRET_FILE_RE = re.compile(r"(?i)(\.env$|secret|token|credential|passw|\.pem$|\.key$|id_rsa|cookies?)")
SECRETS_DIR = ROOT / "workspace" / "secrets"


def _is_secret_path(p: Any) -> bool:
    """Wahr fuer Secret-Namen UND alles unter workspace/secrets (fail-closed)."""
    try:
        q = Path(str(p))
        q = q.resolve() if q.is_absolute() else (ROOT / q).resolve()
        sec = SECRETS_DIR.resolve()
        return q == sec or sec in q.parents or bool(_SECRET_FILE_RE.search(q.name))
    except Exception:
        return bool(_SECRET_FILE_RE.search(Path(str(p)).name))


def redact(text: Any) -> str:
    return _SECRET_RE.sub("[REDACTED]", str(text))


def log(event: str, **data: Any) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event}
        rec.update({k: json.loads(redact(json.dumps(v, ensure_ascii=False, default=str))) if not isinstance(v, str) else redact(v)[:4000] for k, v in data.items()})
        with _LOCK, open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


# ---------------- Tools (Least Privilege) ----------------
def _safe_path(p: str, base: Path = ROOT) -> Path:
    q = (base / p).resolve() if not os.path.isabs(p) else Path(p).resolve()
    if not str(q).lower().startswith(str(base.resolve()).lower()):
        raise PermissionError("PATH_OUTSIDE_ROOT")
    if _is_secret_path(q):
        raise PermissionError("SECRET_FILE_DENIED")
    return q


def t_list_files(path: str = ".", limit: int = 50) -> dict:
    p = _safe_path(path)
    items = sorted(x.name + ("/" if x.is_dir() else "") for x in p.iterdir())[: min(int(limit), 200)]
    return {"path": str(p), "items": items}


def t_read_file(path: str, max_chars: int = 4000) -> dict:
    p = _safe_path(path)
    if not p.is_file():
        raise FileNotFoundError(f"FILE_NOT_FOUND: {path} - erst search_files/list_files nutzen")
    if p.stat().st_size > 2_000_000:
        raise ValueError("FILE_TOO_LARGE")
    return {"path": str(p), "content": redact(p.read_text(encoding="utf-8", errors="replace")[: min(int(max_chars), 8000)])}


def t_search_files(query: str, path: str = "docs", limit: int = 10) -> dict:
    base = _safe_path(path)
    q = str(query).lower()
    hits: list[str] = []
    for fp in base.rglob("*"):
        if len(hits) >= min(int(limit), 30):
            break
        if any(s in fp.parts for s in ("__pycache__", ".git", "node_modules", "backups")) or not fp.is_file():
            continue
        if fp.suffix.lower() not in (".md", ".txt", ".py", ".json", ".ps1", ".js", ".html", ".yaml", ".yml") or _is_secret_path(fp):
            continue
        if q in fp.name.lower():
            hits.append(str(fp.relative_to(ROOT))); continue
        try:
            if fp.stat().st_size < 500_000 and q in fp.read_text(encoding="utf-8", errors="ignore").lower():
                hits.append(str(fp.relative_to(ROOT)))
        except OSError:
            pass
    return {"query": query, "hits": hits}


def t_search_memory(query: str, limit: int = 5) -> dict:
    from nexus.memory.store import MemoryStore
    res = MemoryStore().search(str(query), limit=min(int(limit), 10))
    return {"query": query, "items": [{"id": i.get("id"), "text": redact(i.get("text", ""))[:500], "score": i.get("score")} for i in res]}


def t_write_note(name: str, content: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}\.(md|txt)", str(name)):
        raise ValueError("BAD_NAME")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    p = _safe_path(name, OUT_DIR)
    p.write_text(str(content)[:20000], encoding="utf-8")
    return {"written": str(p)}


def t_delete_temp(name: str) -> dict:
    p = _safe_path(str(name), OUT_DIR)
    if not p.is_file():
        raise FileNotFoundError("NOT_FOUND")
    p.unlink()
    return {"deleted": str(p)}


def _sandbox_path(p: str) -> Path:
    """Strikt in SANDBOX: normalisiert, kein .., kein Symlink/Junction auf dem Weg."""
    base = SANDBOX.resolve()
    raw = Path(str(p))
    if raw.is_absolute() or raw.drive or ".." in raw.parts:
        raise PermissionError("SANDBOX_ESCAPE")
    q = (base / raw)
    cur = base
    for part in raw.parts:
        cur = cur / part
        if cur.is_symlink() or (cur.exists() and os.path.realpath(cur) != str(cur.resolve())):
            raise PermissionError("SANDBOX_SYMLINK")
    if not q.resolve().is_relative_to(base) or q.resolve() == base:
        raise PermissionError("SANDBOX_ESCAPE")
    return q.resolve()


def t_sandbox_write(path: str, content: str) -> dict:
    p = _sandbox_path(path); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(content)[:200000], encoding="utf-8"); return {"written": str(p)}


def t_sandbox_delete(path: str) -> dict:
    import shutil
    p = _sandbox_path(path)
    if p.is_dir(): shutil.rmtree(p)
    elif p.is_file(): p.unlink()
    else: raise FileNotFoundError("NOT_FOUND")
    return {"deleted": str(p)}


def t_project_write(path: str, content: str) -> dict:
    """Nur im Spezialauftrag: Datei unter NEXUS-Root schreiben, vorher Backup."""
    p = _safe_path(path)
    if not str(p).lower().startswith("g:\\") and os.name == "nt":
        raise PermissionError("DRIVE_DENIED")
    if p.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        bk = BACKUP_DIR / (time.strftime("%Y%m%d_%H%M%S_") + p.name)
        bk.write_bytes(p.read_bytes())
    else:
        bk = None
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(str(content)[:200000], encoding="utf-8")
    return {"written": str(p), "backup": str(bk) if bk else None}


# --- 2026-11-02 fix2: deterministische Sandbox-Vorgabe + Ergebnispruefung ---
_SANDBOX_WORD = re.compile(r"(?i)\bsandbox\b")
_FILE_RE = re.compile(r"\b([A-Za-z0-9_\-]{1,80}\.(?:txt|md|json|csv|log|py|ps1|html))\b")
_CONTENT_RE = re.compile(r"(?i)(?:mit\s+(?:dem\s+)?Inhalt|Inhalt:?)\s+[\"'„“]?([^\"'“”\n]+?)[\"'“”]?(?:\s+(?:an|und|anlegen|schreiben)\b|[.,;]|$)")
_NON_SANDBOX_WRITE = {"write_note", "project_write"}


def task_targets(task: str) -> dict:
    t = str(task or "")
    m = _CONTENT_RE.search(t)
    return {"sandbox": bool(_SANDBOX_WORD.search(t)), "files": _FILE_RE.findall(t),
            "content": m.group(1).strip() if m else None,
            "delete": bool(re.search(r"(?i)\bl(oe|ö)sch", t))}


def file_sha256(path: str | Path) -> str | None:
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > 5_000_000:
            return None
        h = hashlib.sha256()
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def verify_result(task: str, run_id: str | None = None) -> dict:
    """Deterministisch: geforderte Datei am geforderten Ort (Sandbox) vorhanden + Inhalt.

    Mit run_id (Phase 1 WP4/WP6) zusaetzlich: Blackboard-Records, Hashes, Ownership.
    """
    tg = task_targets(task)
    if not (tg["sandbox"] and tg["files"]):
        result: dict = {"checked": False, "ok": True, "reason": "keine pruefbare Pfadvorgabe"}
    else:
        result = {"checked": True, "ok": True, "reason": "Datei(en) am geforderten Ort geprueft", "files": tg["files"]}
        for name in tg["files"]:
            try:
                p = _sandbox_path(name)
            except PermissionError as e:
                return {"checked": True, "ok": False, "reason": f"{name}: {e}"}
            if tg["delete"]:
                if p.exists():
                    return {"checked": True, "ok": False, "reason": f"{p} existiert noch"}
                continue
            if not p.is_file():
                return {"checked": True, "ok": False, "reason": f"{p} fehlt - Datei muss mit sandbox_write in workspace/sandbox liegen"}
            if tg["content"] is not None and p.read_text(encoding="utf-8", errors="replace").strip() != tg["content"]:
                return {"checked": True, "ok": False, "reason": f"{p}: Inhalt passt nicht"}
    if run_id and swarm_enabled():
        merged = blackboard_merge(run_id)
        bad: list[str] = []
        for rec in merged.get("records") or []:
            if rec.get("status") not in ("PASS", "DELETED"):
                continue
            target = rec.get("path")
            if not target:
                bad.append(f"{rec.get('worker')}: Pfad fehlt im Beleg")
                continue
            if rec.get("status") == "PASS":
                p = Path(str(target))
                if not p.is_file():
                    bad.append(f"{target}: Datei fehlt")
                elif rec.get("sha256") and file_sha256(p) != rec.get("sha256"):
                    bad.append(f"{target}: Hash passt nicht")
            elif rec.get("status") == "DELETED" and Path(str(target)).exists():
                bad.append(f"{target}: existiert trotz Loeschbeleg")
        if merged.get("conflicts"):
            bad.append("Ownership-Konflikt: " + ", ".join(merged["conflicts"]))
        if bad:
            result.update(ok=False, reason="Belegkette: " + "; ".join(bad[:5]), merge=merged)
        else:
            result["merge"] = merged
    return result


# --- 2026-11-02 fix3: Web (vorhandene NEXUS-Research, DuckDuckGo ohne Key) + Terminal (bestehendes shell_run-Gate) ---
WEB_TIMEOUT = 20


def t_web_search(query: str, limit: int = 5) -> dict:
    q = str(query)
    if _SECRET_RE.search(q):
        raise PermissionError("SECRET_IN_QUERY")
    from nexus.knowledge.research import Research
    r = Research().search(q[:200], limit=min(int(limit), 8))
    return {"success": bool(r.get("success")), "error": r.get("error"),
            "results": [{"title": x.get("title"), "url": x.get("url"), "snippet": str(x.get("snippet") or "")[:300]} for x in (r.get("results") or [])][:8]}


def t_fetch_url(url: str, max_chars: int = 4000) -> dict:
    import urllib.parse, ipaddress, socket
    u = str(url)
    pr = urllib.parse.urlparse(u)
    if pr.scheme not in ("http", "https") or not pr.hostname or _SECRET_RE.search(u) or pr.username or pr.password:
        raise PermissionError("URL_DENIED")
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(pr.hostname))
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise PermissionError("PRIVATE_NET_DENIED")
    except (socket.gaierror, ValueError):
        raise PermissionError("DNS_FAIL")
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 NEXUS-Aether"})
    with urllib.request.urlopen(req, timeout=WEB_TIMEOUT) as r:
        raw = r.read(600_000).decode("utf-8", errors="replace")
    txt = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", raw)
    txt = re.sub(r"<[^>]+>", " ", txt); txt = re.sub(r"\s+", " ", txt)
    return {"url": u, "text": txt[: min(int(max_chars), 8000)]}


_SHELL_MUTATE = re.compile(r"(?i)(>|\b(remove-item|rm|del|erase|rd|rmdir|ri|clear-content|clear-item|move-item|mv|move|mi|rename-item|ren|rni|"
                           r"set-content|sc|add-content|ac|out-file|new-item|ni|mkdir|md|copy-item|cp|copy|cpi|set-item|si|tee-object|tee|"
                           r"expand-archive|compress-archive|robocopy|xcopy|attrib|takeown|format|cipher|git)\b)")
_SHELL_ESCAPE = re.compile(r"(?i)([a-z]:|\.\.|^[\\/]|\s[\\/~]|~|\$|%|\b(set-location|cd|chdir|sl|push-location|pushd|invoke-expression|iex|start-process|cmd|powershell|pwsh)\b|\[|`)")


def _sandbox_shell_check(command: str) -> str | None:
    """Aether-Vorpruefung VOR dem Gate: veraendernde Befehle nur mit relativen Pfaden in der Sandbox."""
    c = str(command)
    if _SHELL_MUTATE.search(c) and _SHELL_ESCAPE.search(c):
        return "AETHER_WRITE_OUTSIDE_SANDBOX: veraendernde Befehle nur mit relativen Pfaden in workspace/sandbox"
    return None


def t_shell_run(command: str, approval_id: str | None = None, timeout: int = 60) -> dict:
    """Ueber das BESTEHENDE Gate (safety.dispatch -> Registry shell_run -> terminal.service + guard).
    Stufe kommt aus der Terminal-Konfiguration fuer Agent AETHER, nicht von hier. cwd = Sandbox."""
    why = None if approval_id else _sandbox_shell_check(command)  # mit approval_id entscheidet das Gate
    if why:
        log("shell_precheck_blocked", command=command, reason=why)
        return {"ok": False, "success": False, "status": "BLOCKED", "error": why, "stdout": "", "stderr": ""}
    from nexus.safety.dispatch import shell_execute
    SANDBOX.mkdir(parents=True, exist_ok=True)
    r = shell_execute(str(command), agent="AETHER", shell="powershell", cwd=str(SANDBOX), timeout=min(int(timeout), 120),
                      approval_id=approval_id)
    keep = ("ok", "success", "status", "error", "verdict", "reasons", "returncode", "run", "level_agent", "level_required")
    out = {k: r.get(k) for k in keep if k in r}
    out["stdout"] = redact(str(r.get("stdout") or ""))[:3000]
    out["stderr"] = redact(str(r.get("stderr") or ""))[:1000]
    return out


@dataclass
class ToolDef:
    fn: Callable[..., dict]
    level: str            # READ_ONLY | SAFE_WRITE | PRIVILEGED
    destructive: bool = False
    network: bool = False
    desc: str = ""
    special_only: bool = False


TOOLS: dict[str, ToolDef] = {
    "list_files": ToolDef(t_list_files, "READ_ONLY", desc="list_files(path) Ordner unter NEXUS-Root auflisten"),
    "read_file": ToolDef(t_read_file, "READ_ONLY", desc="read_file(path, max_chars) Textdatei unter NEXUS-Root lesen"),
    "search_files": ToolDef(t_search_files, "READ_ONLY", desc="search_files(query, path) Text in Dateien unter path suchen (Default docs)"),
    "search_memory": ToolDef(t_search_memory, "READ_ONLY", desc="search_memory(query) NEXUS-Gedaechtnis durchsuchen"),
    "write_note": ToolDef(t_write_note, "SAFE_WRITE", desc="write_note(name, content) Notiz nach workspace/aether_supervisor/out schreiben"),
    "delete_temp": ToolDef(t_delete_temp, "PRIVILEGED", destructive=True, desc="delete_temp(name) Datei in workspace/aether_supervisor/out loeschen (nur mit approval_id)"),
    "sandbox_write": ToolDef(t_sandbox_write, "SAFE_WRITE", desc="sandbox_write(path, content) frei in workspace/sandbox schreiben (relativer Pfad)"),
    "sandbox_delete": ToolDef(t_sandbox_delete, "SAFE_WRITE", desc="sandbox_delete(path) frei in workspace/sandbox loeschen (relativer Pfad)"),
    "web_search": ToolDef(t_web_search, "READ_ONLY", network=True, desc="web_search(query) kostenlose Websuche (DuckDuckGo), liefert Titel/URL/Snippet"),
    "fetch_url": ToolDef(t_fetch_url, "READ_ONLY", network=True, desc="fetch_url(url) oeffentliche Webseite als Text lesen"),
    "shell_run": ToolDef(t_shell_run, "SAFE_WRITE", desc="shell_run(command) PowerShell-Befehl ueber das NEXUS-Terminal-Gate, cwd=workspace/sandbox"),
    "project_write": ToolDef(t_project_write, "SAFE_WRITE", special_only=True, desc="project_write(path, content) nur im Spezialauftrag: Datei unter NEXUS-Root schreiben, mit Backup"),
}
# Netzwerk nur fuer Agenten in NETWORK_AGENTS (PolicyGuard).
NETWORK_AGENTS = {"researcher"}


# ---------------- Approvals (Human-in-the-Loop, eine Quelle) ----------------
def request_approval(tool: str, args: dict, ttl_s: int = 900) -> str:
    """Erzeugt nur eine WARTENDE Anfrage. Freigabe ausschliesslich Eddie im Terminal-UI."""
    from nexus.terminal import guard as _tg
    aid = _tg.request_action_approval("AETHER", tool, args, reason=f"AETHER {tool}", ttl_s=ttl_s)
    log("approval_requested", approval_id=aid, tool=tool, args=args)
    return aid


def grant_approval(tool: str, args: dict, ttl_s: int = 900) -> str:
    """Kompatibilitaetsname. Erzeugt NUR eine wartende Anfrage (kein Auto-Approve)."""
    return request_approval(tool, args, ttl_s)


def _consume_approval(aid: str | None, tool: str, args: dict) -> bool:
    from nexus.terminal import guard as _tg
    ok = _tg.consume_action_approval(aid, "AETHER", tool, args)
    if ok:
        log("approval_consumed", approval_id=aid, tool=tool, args=args)
    return ok


# ---------------- PolicyGuard ----------------
class PolicyDenied(Exception):
    pass


class PolicyGuard:
    """Deterministisch, unter dem Modell. Kein Bypass."""
    def __init__(self, max_depth: int = MAX_DEPTH, allow_network: bool = False):
        self.max_depth, self.allow_network = max_depth, allow_network

    def check(self, spec: "AgentSpec", tool: str, args: dict, depth: int, approval_id: str | None = None, special: bool = False) -> None:
        if tool not in spec.tools:
            raise PolicyDenied("TOOL_NOT_IN_SPEC")
        td = TOOLS.get(tool)
        if td is None:
            raise PolicyDenied("UNKNOWN_TOOL")
        if depth > self.max_depth:
            raise PolicyDenied("DEPTH_LIMIT")
        if td.special_only and not special:
            raise PolicyDenied("SPECIAL_ORDER_REQUIRED")
        if td.network and not (self.allow_network or spec.name in NETWORK_AGENTS):
            raise PolicyDenied("NETWORK_DENIED")
        if (td.destructive or td.level == "PRIVILEGED") and not _consume_approval(approval_id, tool, args):
            raise PolicyDenied("APPROVAL_REQUIRED")


# ---------------- Blackboard / deterministischer Merge (WP4) ----------------
_TARGET_TOOLS = {"write_note": "out", "delete_temp": "out", "sandbox_write": "sandbox",
                 "sandbox_delete": "sandbox", "project_write": "root"}
_BB_LOCK = threading.Lock()


def _runs_dir() -> Path:
    return WORK / "runs"


def _run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", str(run_id or "")):
        raise ValueError("BAD_RUN_ID")
    return _runs_dir() / str(run_id)


def _target_key(tool: str, target: Any) -> str:
    t = str(target or "").replace("\\", "/").strip()
    if not t:
        return ""
    base = _TARGET_TOOLS.get(tool, "")
    if base == "sandbox":
        try:
            return "sandbox:" + str(_sandbox_path(t).relative_to(SANDBOX.resolve())).replace("\\", "/").lower()
        except Exception:
            return "sandbox:" + os.path.normpath(t).replace("\\", "/").lower()
    if base == "out":
        return "out:" + os.path.normpath(t).replace("\\", "/").lower()
    try:
        p = Path(t)
        p = p.resolve() if p.is_absolute() else (ROOT / p).resolve()
        return "root:" + str(p).replace("\\", "/").lower()
    except Exception:
        return "root:" + t.lower()


def _load_ownership(run_id: str) -> dict:
    try:
        data = json.loads((_run_dir(run_id) / "ownership.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def blackboard_reserve(run_id: str, tool: str, target: Any, worker: str) -> dict:
    """Reserviert ein Ziel deterministisch fuer genau einen Worker (kein Ueberschreiben)."""
    key = _target_key(tool, target)
    if not key:
        return {"ok": True, "key": ""}
    with _BB_LOCK:
        d = _load_ownership(run_id)
        owner = d.get(key)
        if owner and owner != worker:
            return {"ok": False, "key": key, "owner": owner}
        d[key] = worker
        rd = _run_dir(run_id)
        rd.mkdir(parents=True, exist_ok=True)
        tmp = rd / "ownership.tmp"
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(rd / "ownership.json")
    return {"ok": True, "key": key, "owner": worker}


def blackboard_record(run_id: str, worker: str, role: str, tool: str, target: Any, status: str,
                      path: str | None = None, error: str = "") -> dict | None:
    """Append-only Beleg. Secret-Ziele werden nie geschrieben."""
    text = str(target or "")
    p = Path(path) if path else None
    if _is_secret_path(text) or (p and _is_secret_path(p)):
        log("blackboard_skipped_secret", run_id=run_id, worker=worker, target=text[:200])
        return None
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run_id": run_id, "worker": worker,
        "role": role, "tool": tool, "target": redact(text)[:500],
        "target_key": _target_key(tool, target), "status": str(status)[:40],
        "path": str(p) if p else None,
        "sha256": file_sha256(p) if (p and status == "PASS") else None,
        "bytes": (p.stat().st_size if (p and p.is_file()) else None),
        "error": redact(error)[:500],
    }
    try:
        rd = _run_dir(run_id)
        rd.mkdir(parents=True, exist_ok=True)
        with _BB_LOCK, (rd / "results.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            fh.flush()
    except Exception as e:
        log("blackboard_error", run_id=run_id, error=f"{type(e).__name__}: {e}")
        return None
    log("blackboard_record", run_id=run_id, worker=worker, tool=tool,
        target=rec["target"], status=rec["status"], sha256=rec["sha256"])
    return rec


def blackboard_merge(run_id: str) -> dict:
    """LLM-freier Merge: sortiert, zaehlt, findet Ownership-Konflikte."""
    try:
        lines = (_run_dir(run_id) / "results.jsonl").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {"run_id": run_id, "counts": {}, "conflicts": [], "records": []}
    records: list[dict] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            records.append(rec)
    counts: dict[str, int] = {}
    by_key: dict[str, set] = {}
    for rec in records:
        counts[str(rec.get("status"))] = counts.get(str(rec.get("status")), 0) + 1
        key = str(rec.get("target_key") or "")
        if key:
            by_key.setdefault(key, set()).add(str(rec.get("worker") or ""))
    conflicts = sorted(k for k, owners in by_key.items() if len(owners) > 1)
    records.sort(key=lambda r: (str(r.get("role")), str(r.get("worker")), str(r.get("target_key")), str(r.get("ts"))))
    if conflicts:
        log("merge_conflict", run_id=run_id, conflicts=conflicts[:10])
    return {"run_id": run_id, "counts": counts, "conflicts": conflicts, "records": records}


def _safety_precheck(agent: str, tool: str, args: dict) -> None:
    """Rechte kommen vom ausfuehrenden Agenten AETHER, nie vom Worker-Namen."""
    from nexus.safety import gate
    if not gate.enabled():
        return
    action = {"write_note": "write_file", "sandbox_write": "write_file", "project_write": "write_file",
              "sandbox_delete": "delete_file", "delete_temp": "delete_file"}.get(tool)
    if not action:
        return
    from nexus.terminal.guard import agent_level
    try:
        granted = gate.to_level(agent_level("AETHER"))
        v = gate.check("AETHER", action, {"path": args.get("path") or args.get("name") or ""}, granted=granted)
    except Exception as e:
        raise PolicyDenied("SAFETY_ERROR") from e
    if not v.allowed:
        raise PolicyDenied("SAFETY_DENIED:" + str(v.reason))


class ToolGateway:
    """Einziger Weg vom Worker zum Werkzeug (WP5). Setzt Safety + Budget + Ownership durch."""

    def __init__(self, budget: Budget | None = None, tools: dict[str, ToolDef] | None = None):
        self.budget = budget
        self.tools = tools if tools is not None else TOOLS

    def invoke(self, agent: str, role: str, tool: str, args: dict, run_id: str | None = None) -> dict:
        td = self.tools.get(tool)
        if td is None:
            raise PolicyDenied("UNKNOWN_TOOL")
        if self.budget:
            self.budget.consume_tool()
        if not (swarm_enabled() and gateway_enabled()):
            return td.fn(**args)  # Rollback: NEXUS_SWARM=0 oder NEXUS_TOOL_GATEWAY=0
        try:
            _safety_precheck(agent, tool, args)
        except PolicyDenied as e:
            log("gateway_denied", run_id=run_id, agent=agent, tool=tool, reason=str(e))
            raise
        key = ""
        target = args.get("path") or args.get("name")
        if target and _is_secret_path(target):
            log("gateway_denied", run_id=run_id, agent=agent, tool=tool, target=str(target)[:200], reason="SECRET_TARGET")
            raise PolicyDenied("SECRET_TARGET")
        target = target if tool in _TARGET_TOOLS else None
        if run_id and target:
            res = blackboard_reserve(run_id, tool, target, agent)
            if not res.get("ok"):
                log("merge_conflict", run_id=run_id, agent=agent, tool=tool,
                    target=str(target)[:200], owner=res.get("owner"))
                raise PolicyDenied("OWNERSHIP_CONFLICT")
            key = str(res.get("key") or "")
        try:
            out = td.fn(**args)
        except Exception as e:
            if run_id and key:
                blackboard_record(run_id, agent, role, tool, target, "ERROR", error=f"{type(e).__name__}: {e}")
            raise
        if run_id and key:
            status = "DELETED" if tool in ("sandbox_delete", "delete_temp") else "PASS"
            written = out.get("written") or out.get("deleted")
            blackboard_record(run_id, agent, role, tool, target, status, path=written)
        return out


# ---------------- AgentSpec / Templates / Factory ----------------
@dataclass
class AgentSpec:
    name: str
    role: str
    system_prompt: str
    tools: list[str] = field(default_factory=list)
    max_steps: int = 8
    can_spawn: bool = False


TEMPLATES: dict[str, dict] = {
    "researcher": dict(name="researcher", role="Lokale Recherche: Gedaechtnis und NEXUS-Dateien durchsuchen, Quellen nennen.",
                       system_prompt="Du bist researcher. Nutze nur deine Tools. Antworte auf Deutsch mit Quellen (Dateipfad oder Memory-ID) und Kurzfazit. Erfinde nichts; wenn nichts gefunden, sag das. Dateien findest du mit search_files (Standardordner docs); nenne einen Treffer-Pfad aus hits als Quelle. Memory-IDs (M-xxxx) sind keine Dateien - nicht mit read_file oeffnen.",
                       tools=["search_memory", "search_files", "read_file", "list_files", "web_search", "fetch_url"], max_steps=6),
    "terminal": dict(name="terminal", role="Befehle im NEXUS-Terminal ausfuehren (Gate entscheidet Rechte), Arbeitsordner Sandbox.",
                     system_prompt="Du bist terminal. Nutze shell_run fuer PowerShell-Befehle. Das Gate entscheidet; BLOCKED-Ergebnisse ehrlich melden, nicht umgehen. Kein C:. Gib die echte Ausgabe wieder.",
                     tools=["shell_run", "list_files", "read_file"], max_steps=5),
    "fileops": dict(name="fileops", role="Dateien auflisten/lesen, Notizen in den Ausgabeordner schreiben. Loeschen nur mit Freigabe.",
                    system_prompt="Du bist fileops. Nutze nur deine Tools. In workspace/sandbox (sandbox_write/sandbox_delete) arbeitest du frei. project_write nur im Spezialauftrag. Schreiben nur per write_note. delete_temp braucht eine Freigabe von Eddie; ohne Freigabe nicht erneut versuchen. Antworte kurz auf Deutsch.",
                    tools=["list_files", "read_file", "write_note", "delete_temp", "sandbox_write", "sandbox_delete", "project_write"], max_steps=6),
    "reviewer": dict(name="reviewer", role="Prueft eine Antwort auf Belegbarkeit und Vollstaendigkeit. Keine Tools.",
                     system_prompt="Du bist reviewer. Pruefe die Antwort gegen Aufgabe und Belege. ok=true nur wenn die Antwort die Aufgabe beantwortet und nichts Unbelegtes behauptet. Antworte als JSON.",
                     tools=[], max_steps=1),
}


class FactoryError(ValueError):
    pass


class AgentFactory:
    def __init__(self, llm: "OllamaJSON", tools: dict[str, ToolDef] | None = None, guard: PolicyGuard | None = None,
                 max_agents: int = MAX_AGENTS, max_depth: int = MAX_DEPTH, allow_free_specs: bool = ALLOW_FREE_SPECS):
        self.llm, self.tools = llm, tools if tools is not None else TOOLS
        self.guard = guard or PolicyGuard(max_depth)
        self.agents: dict[str, WorkerAgent] = {}
        self.max_agents, self.max_depth, self.allow_free_specs = max_agents, max_depth, allow_free_specs

    def validate(self, raw: dict | str, depth: int = 0) -> AgentSpec:
        if isinstance(raw, str):
            raw = {"template": raw}
        if not isinstance(raw, dict):
            raise FactoryError("SPEC_NOT_OBJECT")
        tpl = raw.get("template") or raw.get("name")
        if tpl in TEMPLATES and (raw.get("template") or set(raw) <= {"name"} or self._same_as_template(raw)):
            spec = AgentSpec(**TEMPLATES[tpl])
        elif not self.allow_free_specs:
            raise FactoryError("FREE_SPEC_DISABLED")
        else:
            try:
                spec = AgentSpec(**{k: raw[k] for k in raw if k in AgentSpec.__dataclass_fields__})
            except TypeError as e:
                raise FactoryError("SPEC_INVALID") from e
        if len(self.agents) >= self.max_agents and spec.name not in self.agents:
            raise FactoryError("AGENT_LIMIT")
        if not spec.name.isidentifier():
            raise FactoryError("BAD_NAME")
        if set(spec.tools) - set(self.tools):
            raise FactoryError("UNKNOWN_TOOL")
        if len(spec.tools) > MAX_TOOLS_PER_WORKER:
            raise FactoryError("TOO_MANY_TOOLS")
        if depth > self.max_depth:
            raise FactoryError("DEPTH_LIMIT")
        if depth >= self.max_depth:
            spec.can_spawn = False
        spec.max_steps = max(1, min(int(spec.max_steps), 8))
        return spec

    @staticmethod
    def _same_as_template(raw: dict) -> bool:
        t = TEMPLATES[raw["name"]]
        return all(raw.get(k, t[k]) == t[k] for k in ("role", "system_prompt", "tools"))

    def spawn(self, raw: dict | str, depth: int = 0) -> "WorkerAgent":
        spec = self.validate(raw, depth)
        if spec.name in self.agents:
            return self.agents[spec.name]
        a = WorkerAgent(spec, self.llm, {t: self.tools[t] for t in spec.tools}, depth, self.guard)
        self.agents[spec.name] = a
        log("spawn", spec=asdict(spec), depth=depth)
        return a


# ---------------- LLM (Ollama, JSON-Schema erzwungen) ----------------
class OllamaJSON:
    def __init__(self, model: str = MODEL, temperature: float = TEMPERATURE, timeout: int = LLM_TIMEOUT):
        self.model, self.temperature, self.timeout = model, temperature, timeout

    def json(self, system: str, user: str, schema: dict) -> dict:
        from nexus.common.ai_layer import keep_alive_value, load_config, ollama_base_url, unified_num_ctx
        cfg = load_config()
        payload = {"model": self.model, "stream": False, "think": False, "format": schema,
                   "keep_alive": keep_alive_value(cfg),
                   "options": {"num_ctx": unified_num_ctx(), "temperature": self.temperature},
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        req = urllib.request.Request(ollama_base_url(cfg) + "/api/chat", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        self.last_usage = {"prompt": int(data.get("prompt_eval_count") or 0),
                           "completion": int(data.get("eval_count") or 0)}
        return json.loads((data.get("message") or {}).get("content") or "{}")


WORKER_SCHEMA = {"type": "object", "properties": {
    "tool": {"type": "string"}, "args": {"type": "object"}, "answer": {"type": "string"}},
    "required": ["tool", "args", "answer"]}
REVIEW_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}, "reason": {"type": "string"}}, "required": ["ok", "reason"]}


def _usage_tokens(llm: Any) -> int:
    usage = getattr(llm, "last_usage", None) or {}
    try:
        return int(usage.get("prompt") or 0) + int(usage.get("completion") or 0)
    except (TypeError, ValueError):
        return 0


class WorkerAgent:
    def __init__(self, spec: AgentSpec, llm: OllamaJSON, tools: dict[str, ToolDef], depth: int, guard: PolicyGuard):
        self.spec, self.llm, self.tools, self.depth, self.guard = spec, llm, tools, depth, guard

    def run(self, task: str, approval_id: str | None = None, deadline: float | None = None, special: bool = False,
            run_id: str | None = None, budget: Budget | None = None) -> str:
        gateway = ToolGateway(budget, self.tools)
        if not self.tools:  # z. B. reviewer
            if budget:
                budget.consume_model()
            r = self.llm.json(self.spec.system_prompt, task, REVIEW_SCHEMA if self.spec.name == "reviewer" else WORKER_SCHEMA)
            if budget:
                budget.add_tokens(_usage_tokens(self.llm))
            log("worker_result", agent=self.spec.name, result=r)
            return json.dumps(r, ensure_ascii=False)
        sbx = task_targets(task)["sandbox"]
        offered = {n: d for n, d in self.tools.items() if not (sbx and n in _NON_SANDBOX_WRITE)}
        tool_list = "\n".join(f"- {n}: {t.desc}" for n, t in offered.items())
        if sbx:
            tool_list += "\nSANDBOX-AUFTRAG: schreibe/loesche nur mit sandbox_write/sandbox_delete, path relativ zu workspace/sandbox."
        system = (self.spec.system_prompt + "\nTools:\n" + tool_list +
                  "\nAntworte NUR als JSON {tool, args, answer}. Fuer ein Tool: tool=<name>, answer=\"\". "
                  "Wenn fertig: tool=\"done\", args={}, answer=<Ergebnis mit Quellen>.")
        transcript = "Aufgabe: " + task
        for step in range(self.spec.max_steps):
            if deadline and time.time() > deadline:
                return "TIMEOUT"
            if budget:
                budget.check_wall()
                budget.consume_model()
            d = self.llm.json(system, transcript, WORKER_SCHEMA)
            if budget:
                budget.add_tokens(_usage_tokens(self.llm))
            tool, args = str(d.get("tool") or "done"), d.get("args") if isinstance(d.get("args"), dict) else {}
            if tool == "done":
                log("worker_result", agent=self.spec.name, step=step, answer=d.get("answer", ""))
                return str(d.get("answer") or "")
            try:
                if tool not in offered and tool in self.tools:
                    raise PolicyDenied("SANDBOX_TASK_USE_SANDBOX_TOOLS")
                self.guard.check(self.spec, tool, args, self.depth, approval_id, special)
                if tool == "shell_run" and approval_id and "approval_id" not in args:
                    args = dict(args, approval_id=approval_id)
                res = gateway.invoke(self.spec.name, self.spec.role, tool, args, run_id)
                out = json.dumps(res, ensure_ascii=False)[:3000]
                log("tool_call", agent=self.spec.name, tool=tool, args=args, ok=True, result=out[:1500])
            except PolicyDenied as e:
                out = f"POLICY_DENIED: {e}"
                log("tool_call", agent=self.spec.name, tool=tool, args=args, ok=False, denied=str(e))
            except BudgetExceeded:
                raise
            except Exception as e:  # Toolfehler offen zurueckgeben
                out = f"TOOL_ERROR: {type(e).__name__}: {e}"
                log("tool_call", agent=self.spec.name, tool=tool, args=args, ok=False, error=out)
            transcript += f"\n\n[{tool} {json.dumps(args, ensure_ascii=False)}]\n{out}"
        return "MAX_STEPS"


# ---------------- Supervisor ----------------
SUPERVISOR_SCHEMA = {"type": "object", "properties": {
    "action": {"type": "string", "enum": ["self", "spawn", "call", "final"]},
    "template": {"type": "string", "enum": ["researcher", "fileops", "terminal", "none"]},
    "agent": {"type": "string"}, "task": {"type": "string"}, "answer": {"type": "string"}},
    "required": ["action", "template", "agent", "task", "answer"]}

SUPERVISOR_PROMPT = (
    "Du bist AETHER, Supervisor in NEXUS, Kommandoton, kurz und bestimmt, ehrlich, schadest Eddie nie. Du schreibst keinen Code und fuehrst selbst nichts aus, du entscheidest. "
    "Verfuegbare Worker-Templates: researcher (Gedaechtnis, NEXUS-Dateien, kostenlose Websuche web_search/fetch_url), terminal (PowerShell ueber NEXUS-Gate, cwd Sandbox), "
    "fileops (Dateien auflisten/lesen, Notiz schreiben, Loeschen nur mit Freigabe). "
    "Felder immer: action, template (researcher|fileops|terminal|none), agent, task, answer. Aktionen: spawn (template=researcher, fileops oder terminal + task: kurze Teilaufgabe), call (agent + task fuer bereits gestarteten Worker), "
    "self (kurz nachdenken, answer=Notiz), final (answer=Endantwort auf Deutsch, nur Belegtes, mit Quellen aus Worker-Ergebnissen). "
    "Gib einem Worker nur seine Teilaufgabe. Wenn Ergebnisse reichen: final.")


def _carry(sub: str, task: str) -> str:
    tg = task_targets(task)
    if tg["sandbox"] and not task_targets(sub)["sandbox"]:
        sub += " (Ziel: Sandbox, workspace/sandbox" + (", Datei " + ", ".join(tg["files"]) if tg["files"] else "") + ")"
    return sub


def run_supervisor(task: str, llm: OllamaJSON | None = None, factory: AgentFactory | None = None,
                   max_steps: int = SUPERVISOR_MAX_STEPS, approval_id: str | None = None, timeout_s: int = RUN_TIMEOUT,
                   special: bool = False, budget: Budget | None = None) -> dict:
    llm = llm or OllamaJSON()
    factory = factory or AgentFactory(llm)
    run_id = "SV-" + uuid.uuid4().hex[:10]
    t0 = time.time(); deadline = t0 + timeout_s
    if budget is None and swarm_enabled():
        budget = Budget(wall_clock_s=float(timeout_s), max_agents=min(MAX_AGENTS, 4))
    ctx = "Aufgabe von Eddie: " + str(task).strip()
    trace: list[dict] = []
    retried = False
    log("run_start", run_id=run_id, task=task, special_order=special,
        swarm=swarm_enabled(), budget=budget.snapshot() if budget else None)
    if special:
        ctx += "\n[SPEZIALAUFTRAG von Eddie: erweiterter Spielraum, project_write erlaubt. Harte Grenzen bleiben: C: tabu, Eddie nie schaden, Backup, keine Secrets, kein Bypass.]"
    for step in range(max_steps):
        if time.time() > deadline:
            break
        try:
            if budget:
                budget.check_wall()
                budget.consume_model()
            d = llm.json(SUPERVISOR_PROMPT, ctx, SUPERVISOR_SCHEMA)
            if budget:
                budget.add_tokens(_usage_tokens(llm))
        except BudgetExceeded as e:
            log("budget_denied", run_id=run_id, step=step, reason=str(e), budget=budget.snapshot())
            return {"success": False, "run_id": run_id, "error": "BUDGET_EXCEEDED",
                    "reason": str(e), "budget": budget.snapshot(), "trace": trace}
        except Exception as e:
            log("run_end", run_id=run_id, status="FAIL", error=type(e).__name__)
            return {"success": False, "run_id": run_id, "error": "SUPERVISOR_LLM_ERROR", "error_type": type(e).__name__, "trace": trace}
        act = d.get("action")
        log("decision", run_id=run_id, step=step, decision=d)
        entry: dict[str, Any] = {"step": step, "action": act}
        try:
            if act == "spawn":
                spec_raw = d.get("spec") if isinstance(d.get("spec"), dict) else {"template": next((x for x in (d.get("template"), d.get("agent")) if x in TEMPLATES and x != "reviewer"), "")}
                if budget and not (isinstance(spec_raw, dict) and spec_raw.get("name") in factory.agents):
                    budget.consume_agent()
                w = factory.spawn(spec_raw, depth=1)
                r = w.run(_carry(str(d.get("task") or task), task), approval_id, deadline, special,
                          run_id=run_id, budget=budget)
                entry.update(agent=w.spec.name, result=r[:1500]); ctx += f"\n\n[{w.spec.name}]\n{r}"
            elif act == "call":
                name = str(d.get("agent") or d.get("template") or "")
                if name not in factory.agents:
                    raise FactoryError("AGENT_NOT_SPAWNED")
                r = factory.agents[name].run(_carry(str(d.get("task") or task), task), approval_id, deadline, special,
                                             run_id=run_id, budget=budget)
                entry.update(agent=name, result=r[:1500]); ctx += f"\n\n[{name}]\n{r}"
            elif act == "self":
                entry.update(note=str(d.get("answer"))[:500]); ctx += f"\n\n[notiz]\n{d.get('answer')}"
            elif act == "final":
                answer = str(d.get("answer") or "")
                vr = verify_result(task, run_id=run_id)
                merged = vr.get("merge") or (blackboard_merge(run_id) if swarm_enabled() else None)
                log("verify", run_id=run_id, result=vr)
                if not vr["ok"]:
                    rr = {"ok": False, "reason": "DETERMINISTISCH FAIL: " + vr["reason"], "deterministic": vr}
                else:
                    rv = factory.spawn("reviewer", depth=1)
                    rr = json.loads(rv.run(f"Aufgabe: {task}\n\nBelege/Kontext:\n{ctx[-5000:]}\n\nAntwort:\n{answer}",
                                           deadline=deadline, run_id=run_id, budget=budget))
                    rr["deterministic"] = vr
                entry.update(answer=answer, review=rr); trace.append(entry)
                if rr.get("ok") or retried:
                    status = "PASS" if rr.get("ok") else ("FAIL" if not vr["ok"] else "PARTIAL")
                    base = {"run_id": run_id, "answer": answer, "review": rr, "review_status": status,
                            "retried": retried, "trace": trace, "agents": list(factory.agents),
                            "merge": merged, "budget": budget.snapshot() if budget else None}
                    if status == "FAIL":
                        log("run_end", run_id=run_id, status="FAIL", answer=answer, review=rr)
                        return {"success": False, "error": "VERIFY_FAILED", **base}
                    log("run_end", run_id=run_id, status=status, answer=answer, review=rr, secs=round(time.time() - t0, 1))
                    return {"success": True, **base, "secs": round(time.time() - t0, 1)}
                retried = True
                ctx += f"\n\n[reviewer: abgelehnt]\n{rr.get('reason')}\nVerbessere die Antwort (ein Versuch)."
                continue
            else:
                raise FactoryError("UNKNOWN_ACTION")
        except BudgetExceeded as e:
            log("budget_denied", run_id=run_id, step=step, reason=str(e), budget=budget.snapshot() if budget else None)
            return {"success": False, "run_id": run_id, "error": "BUDGET_EXCEEDED", "reason": str(e),
                    "budget": budget.snapshot() if budget else None, "trace": trace}
        except (FactoryError, PolicyDenied) as e:
            entry.update(error=str(e)); ctx += f"\n\n[system]\nABGELEHNT: {e}"
            log("rejected", run_id=run_id, step=step, error=str(e))
        except Exception as e:
            entry.update(error=f"{type(e).__name__}"); ctx += f"\n\n[system]\nFEHLER: {type(e).__name__}"
            log("error", run_id=run_id, step=step, error=f"{type(e).__name__}: {e}")
        trace.append(entry)
    log("run_end", run_id=run_id, status="MAX_STEPS")
    return {"success": False, "run_id": run_id, "error": "MAX_STEPS_OR_TIMEOUT", "trace": trace,
            "agents": list(factory.agents), "budget": budget.snapshot() if budget else None}


# ---------------- AETHER shell-audit guard (worker.ps1, 2026-11-03) ----------------
SHELL_DENY_WINDOW = 60
SHELL_DENY_LIMIT = 5
SHELL_LOCK_SECONDS = 60
SHELL_GUARD_STATE = WORK / "shell_guard.json"
_GUARD_LOCK = threading.Lock()
_SHELL_WATCH: dict[str, Any] = {"thread": None, "stop": False}


def shell_audit_path() -> Path:
    raw = (os.environ.get("WORKER_AUDIT") or "").strip()
    return Path(raw).expanduser() if raw else Path.home() / "logs" / "shell-audit.jsonl"


def tail_shell_audit(limit: int = 200) -> list[dict]:
    """Tail the worker audit log without locking it. Broken lines are ignored."""
    path = shell_audit_path()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-max(1, min(int(limit), 2000)):]
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _ts_epoch(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def shell_denials(now: float | None = None, window: int = SHELL_DENY_WINDOW) -> list[dict]:
    current = time.time() if now is None else float(now)
    hits: list[dict] = []
    for rec in tail_shell_audit(400):
        if not str(rec.get("error") or "").startswith("DENIED"):
            continue
        ts = _ts_epoch(rec.get("timestamp"))
        if ts is None:
            continue
        age = current - ts
        if 0 <= age <= window:
            hits.append(rec)
    return hits


def _load_shell_guard() -> dict:
    try:
        data = json.loads(SHELL_GUARD_STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_shell_guard(data: dict) -> None:
    try:
        SHELL_GUARD_STATE.parent.mkdir(parents=True, exist_ok=True)
        SHELL_GUARD_STATE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def reset_shell_guard() -> None:
    try:
        SHELL_GUARD_STATE.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def check_shell_guard(agent: str = "Hermes", emit_live: bool = True, now: float | None = None) -> dict:
    """AETHER: >=5 DENIED in 60 s -> warn in Live-Hub and lock shell_readonly temporarily."""
    current = time.time() if now is None else float(now)
    with _GUARD_LOCK:
        state = _load_shell_guard()
        until = float(state.get("locked_until") or 0)
        if until > current:
            return {"blocked": True, "reason": state.get("reason") or "lockout",
                    "count": state.get("count"), "locked_until": until,
                    "retry_after": round(until - current, 1)}
        denials = shell_denials(now=current)
        if len(denials) >= SHELL_DENY_LIMIT:
            reason = f"{len(denials)} DENIED in {SHELL_DENY_WINDOW}s"
            locked_until = current + SHELL_LOCK_SECONDS
            _save_shell_guard({"locked_until": locked_until, "reason": reason,
                               "count": len(denials), "ts": datetime.now().isoformat(timespec="seconds")})
            log("shell_guard_lock", agent=agent, count=len(denials), reason=reason, locked_until=locked_until)
            if emit_live:
                try:
                    from nexus.common.live_events import emit
                    emit("Aether", "security", "shell_readonly voruebergehend gesperrt: " + reason,
                         target="shell_readonly", ok=False,
                         data={"agent": agent, "count": len(denials), "window_s": SHELL_DENY_WINDOW,
                               "locked_until": locked_until})
                except Exception:
                    pass
            return {"blocked": True, "reason": reason, "count": len(denials),
                    "locked_until": locked_until, "retry_after": SHELL_LOCK_SECONDS}
        if state:
            _save_shell_guard({})
        return {"blocked": False, "count": len(denials), "limit": SHELL_DENY_LIMIT,
                "window_s": SHELL_DENY_WINDOW}


def shell_guard_status() -> dict:
    with _GUARD_LOCK:
        state = _load_shell_guard()
        denials = shell_denials()
    return {"locked": float(state.get("locked_until") or 0) > time.time(),
            "locked_until": state.get("locked_until"), "reason": state.get("reason"),
            "denied_last_60s": len(denials), "limit": SHELL_DENY_LIMIT}


def start_shell_audit_watch(interval: float = 5.0) -> bool:
    """Start AETHER's background tail of shell-audit.jsonl (idempotent)."""
    with _GUARD_LOCK:
        thread = _SHELL_WATCH.get("thread")
        if thread is not None and thread.is_alive():
            return False
        _SHELL_WATCH["stop"] = False

        def _loop() -> None:
            while not _SHELL_WATCH["stop"]:
                try:
                    check_shell_guard(agent="Aether", emit_live=True)
                except Exception:
                    pass
                time.sleep(max(1.0, float(interval)))

        _SHELL_WATCH["thread"] = threading.Thread(target=_loop, name="aether-shell-audit", daemon=True)
        _SHELL_WATCH["thread"].start()
        return True
