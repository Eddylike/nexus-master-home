"""Autonomer Selbstbau-Zyklus: Backlog waehlen -> planen -> bauen -> testen -> uebernehmen/Rollback -> Bericht.

- Laeuft als Daemon-Thread im NEXUS-Server (BelowNormal-Testprozesse, 1 Zyklus gleichzeitig).
- Pausiert bei Gaming oder RAM > RAM_LIMIT.
- Stop: POST /api/autobuild/stop oder Datei workspace/autobuild/STOP.
- Nie simulierte Erfolge: Status nur aus echten Testergebnissen.
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
from nexus.autobuild.scaffold import ensure_target_scaffold as _ensure_scaffold_impl
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from nexus.autobuild.guard import CDriveBlocked, check_write, child_env
from nexus.common.paths import ROOT
from nexus.platform.display import DisplayUnavailable

DIR = ROOT / "workspace" / "autobuild"
BACKLOG = DIR / "backlog.json"
CYCLES = DIR / "cycles.jsonl"
PROGRESS = ROOT / "workspace" / "auftraege" / "SELBSTBAU_FORTSCHRITT.md"
STOP_FILE = DIR / "STOP"
INTERVAL_SEC = 900
RAM_WARN_PERCENT = 85
RAM_PAUSE_PERCENT = 92
FIRST_DELAY_SEC = 180
RAM_LIMIT = 92.0
MAX_ATTEMPTS_PER_ITEM = 3
GAMES = re.compile(r"(?i)^(EscapeFromTarkov|BsgLauncher|steam_?game|cs2|FortniteClient|RocketLeague|GTA5|eldenring|bf2042|valorant|r5apex)")
BELOW_NORMAL = 0x00004000
from nexus.common.ai_layer import unified_num_ctx as _unified_ctx


def _anchor_hit(anchor: str, line: str) -> bool:
    """Anker zuerst als Klartext, dann als Regex; ungueltige Regex (z. B. 'def search(') -> kein PLAN_FAILED (20261029)."""
    if not anchor:
        return False
    if anchor in line:
        return True
    try:
        return re.search(anchor, line) is not None
    except re.error:
        return False
GPT_CTX = _unified_ctx()  # 2026-10-28: einheitlich mit Chat (kein Reload)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _emit(agent: str, kind: str, text: str, ok=None, data=None) -> None:
    try:
        from nexus.common.live_events import emit
        emit(agent, kind, text, target="autobuild", ok=ok, data=data)
    except Exception:
        pass


def _write(path: Path, text: str, mode: str = "w") -> None:
    p = check_write(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, mode, encoding="utf-8", newline="") as fh:
        fh.write(text)


_SNAPSHOT: dict = {}  # 20261104_merge: Stand je id beim letzten Laden/Speichern


def _key(i) -> str:
    return json.dumps(i, ensure_ascii=False, sort_keys=True)


def _read_disk() -> list[dict]:
    try:
        data = json.loads(BACKLOG.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def load_backlog() -> list[dict]:
    items = _read_disk()
    _SNAPSHOT.clear()
    _SNAPSHOT.update({str(i.get("id")): _key(i) for i in items if isinstance(i, dict)})
    return items


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def merge_backlog(disk: list[dict], items: list[dict], snapshot: dict) -> list[dict]:
    """Merge statt Overwrite: nur Eintraege uebernehmen, die dieser Prozess seit dem Laden geaendert oder neu
    angelegt hat. Fremd hinzugefuegte/geaenderte Eintraege auf der Platte bleiben erhalten."""
    out = list(disk)
    pos = {str(d.get("id")): n for n, d in enumerate(out) if isinstance(d, dict)}
    for i in items:
        if not isinstance(i, dict):
            continue
        k = str(i.get("id"))
        if snapshot.get(k) == _key(i):
            continue  # unveraendert -> Plattenstand gewinnt
        if k in pos:
            out[pos[k]] = i
        else:
            out.append(i)
            pos[k] = len(out) - 1
    return out


def save_backlog(items: list[dict]) -> None:
    merged = merge_backlog(_read_disk(), items, _SNAPSHOT)
    text = json.dumps(merged, ensure_ascii=False, indent=2)
    target = check_write(BACKLOG)
    tmp = target.with_name(target.name + ".tmp")
    _write(tmp, text)
    os.replace(tmp, target)  # atomar, UTF-8 ohne BOM
    _SNAPSHOT.clear()
    _SNAPSHOT.update({str(i.get("id")): _key(i) for i in merged if isinstance(i, dict)})
    items[:] = merged


def system_busy() -> str | None:
    try:
        import psutil
        ram = psutil.virtual_memory().percent
        if ram >= RAM_PAUSE_PERCENT:
            return f"RAM-SCHUTZ: {ram:.0f}% >= {RAM_PAUSE_PERCENT}% – Autobuild pausiert"
        if ram >= RAM_WARN_PERCENT:
            _emit("NEXUS", "warning", f"RAM-WARNUNG: {ram:.0f}% – Selbstbau läuft weiter")
        for pr in psutil.process_iter(["name"]):
            n = (pr.info.get("name") or "").rsplit(".", 1)[0]
            if GAMES.match(n):
                return f"Gaming aktiv ({n})"
    except Exception as e:
        return f"busy_check_failed:{e}"
    return None


def _excerpt(item: dict) -> tuple[str, str]:
    f = item.get("file")
    if not f:
        return "", ""
    text = (ROOT / f).read_text(encoding="utf-8")
    lines = text.splitlines()
    idx = 0
    if item.get("anchor"):
        for i, ln in enumerate(lines):
            if _anchor_hit(item["anchor"], ln):
                idx = i
                break
    if f.endswith(".py"):
        ax = _ast_excerpt(text, idx)
        if ax:
            return f, ax
    a, b = max(0, idx - 15), min(len(lines), idx + 35)
    return f, "\n".join(lines[a:b])


class _TestsFailed(Exception):
    """Tests nach Patch rot -> 1 Korrekturrunde mit Testfehlern (20261029)."""


class AnchorMissing(ValueError):
    """A patch anchor is absent, so retrying the same delegate cannot help."""


def apply_patch(text: str, anchor: str, replacement: str) -> str:
    """Apply one normalized text patch without treating an existing result as failure."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    anchor = anchor.replace("\r\n", "\n").replace("\r", "\n")
    replacement = replacement.replace("\r\n", "\n").replace("\r", "\n")
    if replacement in text:
        return "already_applied"
    if anchor not in text:
        raise AnchorMissing("PATCH_ANCHOR_MISSING")
    if text.count(anchor) != 1:
        raise AnchorMissing("PATCH_ANCHOR_AMBIGUOUS")
    return text.replace(anchor, replacement, 1)


def _ws_norm(line: str) -> str:
    return " ".join(str(line).split())


def _syntax_check(text: str, name: str) -> None:
    """ast.parse im Speicher; bei Fehler exakter Dateiausschnitt fuer die Korrekturrunde (20261029)."""
    try:
        ast.parse(text)
    except SyntaxError as se:
        n = int(se.lineno or 1)
        ls = text.splitlines()
        ex = "\n".join(f"{i + 1:5d}| {ls[i]}" for i in range(max(0, n - 5), min(len(ls), n + 4)))
        raise ValueError(f"{type(se).__name__}:{se.msg} ({name}, Zeile {n})\nAusschnitt nach Patch:\n{ex}") from None


def _compute_edits(edits: list) -> dict:
    if not edits:
        raise ValueError("NO_EDITS")
    new_texts: dict[Path, str] = {}
    for e in edits:
        if isinstance(e, dict):  # 20261029: gaengige Schluessel-Varianten des Modells akzeptieren
            for alias, key in (("path", "file"), ("datei", "file"), ("search", "find"), ("old", "find"), ("suche", "find"),
                               ("new", "replace"), ("ersetze", "replace"), ("replacement", "replace")):
                if key not in e and alias in e:
                    e[key] = e.pop(alias)
        if isinstance(e, dict) and "file" in e and not ({"find", "replace"} <= set(e)) and \
                any(k in e for k in ("function", "funktion", "name", "imports")):  # Funktionsmodus (20261030)
            target = check_write(ROOT / e["file"])
            cur = new_texts.get(target) or target.read_text(encoding="utf-8")
            new_texts[target] = _apply_fn_edit(cur, e, e["file"])
            continue
        if not isinstance(e, dict) or not {"file", "find", "replace"} <= set(e):
            raise ValueError("EDIT_INCOMPLETE")
        for k in ("find", "replace"):
            e[k] = str(e[k]).replace("\r\n", "\n").replace("\r", "\n")  # Ausschnitt-Kopfzeilen ("# --- Betroffen: ...") sind kein Dateiinhalt
            e[k] = "\n".join(x for x in str(e[k]).split("\n") if not x.lstrip().startswith("# --- "))
        target = check_write(ROOT / e["file"])
        cur = (new_texts.get(target) or target.read_text(encoding="utf-8")).replace("\r\n", "\n").replace("\r", "\n")
        try:
            patched = apply_patch(cur, e["find"], e["replace"])
        except AnchorMissing:
            # Tolerant: Zeilen mit abweichender Einrueckung/Leerzeichen am Ende eindeutig zuordnen.
            fl = [_ws_norm(x) for x in e["find"].strip("\n").splitlines()]
            while fl and not fl[-1]:
                fl.pop()
            cl = cur.splitlines(keepends=True)
            hits = [i for i in range(len(cl) - len(fl) + 1) if [_ws_norm(c) for c in cl[i:i + len(fl)]] == fl]
            if len(hits) == 1 and fl:
                i = hits[0]
                ind = cl[i][:len(cl[i]) - len(cl[i].lstrip())]
                rl = e["replace"].strip("\n").splitlines()
                base = min((len(r) - len(r.lstrip()) for r in rl if r.strip()), default=0)
                rep = "".join(ind + r[base:] + "\n" if r.strip() else "\n" for r in rl)
                cur = "".join(cl[:i]) + rep + "".join(cl[i + len(fl):])
            else:
                reason = "PATCH_ANCHOR_AMBIGUOUS" if len(hits) > 1 else "PATCH_ANCHOR_MISSING"
                raise AnchorMissing(f"{reason}:{e['file']}") from None
        else:
            if patched == "already_applied":
                new_texts[target] = cur
                continue
            cur = patched
        if target.suffix == ".py":
            _syntax_check(cur, e["file"])
        new_texts[target] = cur
    return new_texts


# ---------- Funktionsmodus (20261030_fnmode) ----------
# 9B-Modell reproduziert Codestellen nicht exakt (FIND_MATCHES_0/EDIT_INCOMPLETE) -> statt Search/Replace
# liefert es eine komplette Funktion; Ersetzung per AST ueber den Funktionsnamen, neue Funktionen werden angehaengt.
FN_MAX_LINES = 80


def _strip_fence(code: str) -> str:
    code = str(code).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if code.lstrip().startswith("```"):
        code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("```"))
    return code


def _top_func(tree: ast.Module, name: str):
    return next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)


def _replace_function(text: str, name: str, code: str) -> str:
    """Top-level-Funktion `name` komplett durch `code` ersetzen (oder anhaengen, falls neu). Syntaxcheck vorher+nachher."""
    import textwrap
    code = textwrap.dedent(_strip_fence(code)).strip("\n")
    if not code:
        raise ValueError(f"FN_EMPTY:{name}")
    try:
        new_tree = ast.parse(code)
    except SyntaxError as se:
        # Sicherstellen, dass die Fehlermeldung des Parser beibehalten wird
        raise ValueError(f"FN_SYNTAX:{name}: {se.msg} (Zeile {se.lineno})") from None
    defs = [n for n in new_tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(new_tree.body) != 1 or len(defs) != 1:
        raise ValueError(f"FN_NOT_SINGLE_DEF:{name}: 'code' muss genau EINE Funktion (def ...) enthalten")
    if defs[0].name != name:
        raise ValueError(f"FN_NAME_MISMATCH:{name}!={defs[0].name}")
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    old = _top_func(tree, name)
    if old is None:  # neue Hilfsfunktion anhaengen
        out = text.rstrip("\n") + "\n\n\n" + code + "\n"
    else:
        start = min([d.lineno for d in old.decorator_list] + [old.lineno]) - 1
        out = "".join(lines[:start]) + code + "\n" + "".join(lines[old.end_lineno:])
    ast.parse(out)
    return out


def _add_imports(text: str, imports: list) -> str:
    """Fehlende Import-Zeilen nach dem letzten Top-Level-Import einfuegen (nur 'import x'/'from x import y')."""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    add = []
    for imp in imports or []:
        imp = str(imp).strip()
        if not imp:
            continue
        try:
            t = ast.parse(imp)
        except SyntaxError:
            raise ValueError(f"FN_IMPORT_SYNTAX:{imp[:80]}") from None
        if not all(isinstance(n, (ast.Import, ast.ImportFrom)) for n in t.body):
            raise ValueError(f"FN_IMPORT_INVALID:{imp[:80]}")
        if imp not in text and imp not in add:
            add.append(imp)
    if not add:
        return text
    last = max((n.end_lineno for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))), default=0)
    if last == 0 and tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(getattr(tree.body[0], "value", None), ast.Constant):
        last = tree.body[0].end_lineno
    return "".join(lines[:last]) + "".join(a + "\n" for a in add) + "".join(lines[last:])


def _apply_fn_edit(cur: str, e: dict, fname: str) -> str:
    for alias, key in (("funktion", "function"), ("name", "function"), ("new_code", "code"), ("neu", "code"), ("body", "code")):
        if key not in e and alias in e:
            e[key] = e.pop(alias)
    if e.get("imports"):
        cur = _add_imports(cur, e["imports"] if isinstance(e["imports"], list) else [e["imports"]])
    if e.get("function"):
        if "code" not in e:
            raise ValueError("EDIT_INCOMPLETE:function ohne code")
        cur = _replace_function(cur, str(e["function"]).strip().split("(")[0].replace("def ", "").strip(), e["code"])
    _syntax_check(cur, fname)
    return cur


def _function_excerpt(text: str, idx: int) -> tuple[str | None, str]:
    """Name + exakter Quelltext der Top-Level-Funktion an Zeile idx (0-basiert)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None, ""
    lines = text.splitlines()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.lineno <= idx + 1 <= n.end_lineno:
            return n.name, "\n".join(lines[n.lineno - 1:n.end_lineno])
    return None, ""


def _gpt_text(prompt: str) -> str:
    return _gpt(prompt, fmt=None)  # Funktionsmodus: Freitext mit ```python-Block (kein JSON-Escaping)


def parse_code_blocks(reply: str, file: str, default_name: str | None = None) -> dict:
    """```python-Bloecke -> Edits (je Top-Level-def ein Edit, Top-Level-Imports gesammelt). Fallback: JSON-Antwort."""
    blocks = re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", reply or "", re.S | re.I)
    if not blocks and re.match(r"\s*(async\s+)?def\s", reply or ""):
        blocks = [reply]
    edits, imports, errs = [], [], []
    for b in blocks:
        import textwrap
        b = textwrap.dedent(b.replace("\r\n", "\n")).strip("\n")
        try:
            tree = ast.parse(b)
        except SyntaxError as se:
            errs.append(f"FN_SYNTAX:{se.msg} (Block-Zeile {se.lineno})")
            continue
        ls = b.splitlines()
        for n in tree.body:
            seg = "\n".join(ls[min([d.lineno for d in getattr(n, "decorator_list", [])] + [n.lineno]) - 1:n.end_lineno])
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                imports.append(seg)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                edits.append({"file": file, "function": n.name, "code": seg})
    if edits:
        if imports:
            edits[0]["imports"] = imports
        m = re.search(r"(?im)^\s*begruendung\s*:\s*(.+)$", reply or "")
        return {"edits": edits, "begruendung": (m.group(1).strip() if m else "Funktionsmodus (Codeblock)"), "format": "codeblock"}
    try:
        res = _json_from(reply)
    except ValueError:
        if errs:
            raise ValueError(errs[0]) from None
        raise ValueError("NO_CODE_BLOCK") from None
    if not res.get("edits") and res.get("code"):
        res = {"edits": [{"file": file, "function": res.get("function") or default_name, "code": res["code"],
                          "imports": res.get("imports") or []}], "begruendung": res.get("begruendung")}
    for e in res.get("edits") or []:
        if isinstance(e, dict):
            e.setdefault("file", file)
    res["format"] = "json"
    return res


# ---------- Nachschlagen echter API-Namen (20261030_lookup) ----------
LOOKUP_MAX_CHARS = 2200


def _api_objects(code: str) -> dict:
    """Module/Objekte aus Import-Zeilen des Codes (ohne nexus.*) -> {qualifizierter Name: Objekt}."""
    import importlib
    out = {}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        tree = None
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))] if tree else []
    for n in nodes:
        try:
            if isinstance(n, ast.Import):
                for a in n.names:
                    if not a.name.startswith("nexus"):
                        out[a.asname or a.name] = importlib.import_module(a.name)
            elif n.module and not n.module.startswith("nexus") and not n.level:
                mod = importlib.import_module(n.module)
                for a in n.names:
                    if a.name == "*":
                        continue
                    obj = getattr(mod, a.name, None)
                    if obj is None:
                        try:
                            obj = importlib.import_module(n.module + "." + a.name)
                        except Exception:
                            continue
                    out[a.asname or a.name] = obj
        except Exception:
            continue
    return out


def _api_names(objs: dict) -> list[str]:
    """Echte oeffentliche Bezeichner (dir), inkl. Mitglieder verschachtelter Enums/Klassen (z.B. Image.Transpose.FLIP_LEFT_RIGHT)."""
    import enum
    import inspect
    names = []
    for q, obj in objs.items():
        for a in dir(obj):
            if a.startswith("_"):
                continue
            names.append(f"{q}.{a}")
            try:
                sub = getattr(obj, a)
            except Exception:
                continue
            if inspect.isclass(sub) and issubclass(sub, enum.Enum):
                names += [f"{q}.{a}.{m}" for m in sub.__members__]
    return names


def _sig(obj) -> str:
    import inspect
    try:
        return str(inspect.signature(obj))
    except Exception:
        return ""


def api_hints(code: str, error_text: str = "") -> str:
    """Prompt-Hinweis: verfuegbare Namen der verwendeten Module + bei Attribute-/NameError die aehnlichsten echten Namen."""
    import difflib
    objs = _api_objects(code)
    if not objs:
        return ""
    names = _api_names(objs)
    lines = []
    for q, obj in objs.items():
        pub = [a for a in dir(obj) if not a.startswith("_")]
        lines.append(f"{q}: " + ", ".join(pub[:80]))
    txt = "\nVERFUEGBARE API (echt per dir() ermittelt, nur diese Namen verwenden):\n" + "\n".join(lines)
    wrong = re.findall(r"has no attribute '(\w+)'", error_text or "") + re.findall(r"name '(\w+)' is not defined", error_text or "")
    if wrong:
        tails = {}
        for nm in names:
            tails.setdefault(nm.rsplit(".", 1)[-1], []).append(nm)
        fix = []
        for w in dict.fromkeys(wrong):
            close = difflib.get_close_matches(w, list(tails), n=3, cutoff=0.5)
            cand = [q for c in close for q in tails[c]][:5]
            if cand:
                fix.append(f"'{w}' existiert NICHT -> meintest du: " + ", ".join(
                    c + (_sig(_resolve(objs, c)) if callable(_resolve(objs, c)) else "") for c in cand))
            else:
                fix.append(f"'{w}' existiert NICHT und hat keine aehnliche Entsprechung.")
        txt = "\nFALSCHE NAMEN IM LETZTEN VERSUCH:\n" + "\n".join(fix) + txt
    if "UnboundLocalError" in (error_text or ""):
        txt = "\nHINWEIS: UnboundLocalError -> Imports nicht in if-Zweigen/innerhalb der Funktion verschatten; oben importieren." + txt
    return txt[:LOOKUP_MAX_CHARS]


def _resolve(objs: dict, qual: str):
    head, *rest = qual.split(".")
    obj = objs.get(head)
    for r in rest:
        obj = getattr(obj, r, None)
    return obj


# ---------- Hermes als Pruefer (20261030_lookup) ----------
REVIEW_TIMEOUT_SEC = 240



def _notes_text(item: dict) -> str:
    """Verbindliche Hinweise aus dem Backlog (reset_note, abnahme_text) fuer Modell-Prompt und Hermes-Review (20261031)."""
    parts = []
    for k, lbl in (("reset_note", "Reset-Hinweis"), ("abnahme_text", "Abnahme")):
        v = item.get(k)
        if v:
            parts.append(f"- {lbl}: {str(v)[:1500]}")
    if not parts:
        return ""
    return "VERBINDLICHE HINWEISE (haben Vorrang vor eigenen Annahmen):\n" + "\n".join(parts) + "\n"

def hermes_review(item: dict, plan: dict, timeout: int = REVIEW_TIMEOUT_SEC) -> dict:
    """Entwurf vor dem Testlauf von Hermes pruefen lassen. Nie blockierend: Timeout/Fehler -> status UNAVAILABLE."""
    try:
        from nexus.integrations import hermes_agent as H
        h = H.health()
        if not h.get("reachable"):
            return {"status": "UNAVAILABLE", "detail": str(h.get("error") or h.get("detail"))}
    except Exception as e:
        return {"status": "UNAVAILABLE", "detail": type(e).__name__}
    code = "\n\n".join(str(e.get("code") or "") for e in plan["result"].get("edits") or [] if isinstance(e, dict))
    acc = ""
    a = item.get("acceptance")
    if a and (ROOT / a).is_file():
        acc = (ROOT / a).read_text(encoding="utf-8")[:2000]
    task = ("Code-Review (nur lesen, nichts ausfuehren oder schreiben). Pruefe den Entwurf auf Syntax, falsche/erfundene "
            "API-Namen und Logikfehler gegenueber dem Abnahmetest. Antworte mit 'OK' wenn alles passt, sonst mit "
            "'PROBLEME:' und einer kurzen Liste konkreter Fehler mit Korrektur. Pruefe auch, ob die verbindlichen Hinweise eingehalten sind.\n" + _notes_text(item) + "Entwurf:\n```python\n" + code[:3500] +
            "\n```\n" + (api_hints(code)[:1200]) + ("\nAbnahmetest:\n```python\n" + acc + "\n```" if acc else ""))
    box: dict = {}

    def run():
        try:
            box["r"] = H.ask(task)
        except Exception as e:
            box["e"] = e
    t = threading.Thread(target=run, name="hermes-review", daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        return {"status": "UNAVAILABLE", "detail": f"TIMEOUT_{timeout}s"}
    if "e" in box:
        return {"status": "UNAVAILABLE", "detail": type(box["e"]).__name__}
    r = box.get("r") or {}
    reply = str(r.get("reply") or "").strip()
    if not reply or r.get("error") in ("HERMES_BUSY", "HERMES_UNAVAILABLE") or reply.startswith("HERMES_UNAVAILABLE"):
        return {"status": "UNAVAILABLE", "detail": str(r.get("error") or "EMPTY")}
    if re.match(r"(?is)\W*ok\b", reply) and "PROBLEM" not in reply.upper():
        return {"status": "OK", "reply": reply[:500]}
    return {"status": "ISSUES", "reply": reply[:1500]}


def fn_mode_applicable(item: dict) -> bool:
    """Funktionsmodus fuer Python-Ziele, deren Ankerfunktion klein ist (Stub/kleine Funktion)."""
    f = str(item.get("file") or "")
    if not f.endswith(".py") or item.get("auto_key") or item.get("fn_mode") is False:
        return False
    try:
        text = (ROOT / f).read_text(encoding="utf-8")
        lines = text.splitlines()
        idx = next(i for i, ln in enumerate(lines) if _anchor_hit(item.get("anchor") or "", ln))
        name, src = _function_excerpt(text, idx)
    except Exception:
        return False
    return bool(name) and len(src.splitlines()) <= FN_MAX_LINES


# ---------- Mehr-Stub-Modus (20261104_multistub) ----------
MULTI_FILE_MAX_CHARS = 12000  # volle Zieldatei als Kontext (SZ-Module ~2-3 KB, Puffer fuer groessere Stub-Dateien)
MULTI_TEST_MAX_CHARS = 6000   # regression_test-Datei; bisher 2500 fuer acceptance-Datei, Tests ueber alle Stubs sind laenger


def stub_functions(text: str) -> list[str]:
    """Top-Level-Funktionen, deren Rumpf `raise NotImplementedError` enthaelt (Reihenfolge der Datei)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for s in ast.walk(n):
                if isinstance(s, ast.Raise) and s.exc is not None:
                    e = s.exc.func if isinstance(s.exc, ast.Call) else s.exc
                    if isinstance(e, ast.Name) and e.id == "NotImplementedError":
                        out.append(n.name)
                        break
    return out


def multi_stub_prompt(item: dict, text: str, stubs: list[str], test_text: str = "") -> str:
    f = item["file"]
    return (f"Du bist NEXUS GPT. Implementiere ALLE Stub-Funktionen einer Python-Datei vollstaendig in einem Schritt.\n"
            f"Aufgabe: {item['title']}\nZiel: {item.get('goal','')}\n{_notes_text(item)}"
            f"Datei: {f}\nZu implementierende Funktionen (alle, jeweils gleiche Signatur): {', '.join(stubs)}\n"
            f"Komplette aktuelle Datei:\n```python\n{text[:MULTI_FILE_MAX_CHARS]}\n```\n"
            + (f"Abnahmetest (muss komplett bestehen, derzeit rot):\n```python\n{test_text[:MULTI_TEST_MAX_CHARS]}\n```\n" if test_text else "") +
            "Regeln: Schreibe JEDE genannte Funktion GANZ neu (def-Zeile + Rumpf), Signaturen nicht aendern, kein NotImplementedError mehr. "
            "Modul-Konstanten nicht aendern. Nur lokal, keine Pfade auf C:, keine Netzwerkzugriffe. "
            "KEINE Docstrings und keine dreifachen Anfuehrungszeichen (nur # Kommentare); Code muss mit ast.parse fehlerfrei sein. "
            "Hilfsfunktionen erlaubt. AUSGABEFORMAT (kein JSON): genau EIN Codeblock ```python mit benoetigten Import-Zeilen oben, "
            "dann alle Funktionen. Danach eine Zeile 'Begruendung: ...'.")


def _syntax_hint(reply: str, err: str) -> str:
    """Bei FN_SYNTAX die fehlerhafte Zeile des Codeblocks zurueckmelden (Mehr-Stub-Modus)."""
    import re as _re
    m = _re.search(r"Block-Zeile (\d+)", err)
    blocks = _re.findall(r"```(?:python|py)?[ \t]*\n(.*?)```", reply or "", _re.S | _re.I)
    if not m or not blocks:
        return ""
    ls = blocks[0].replace("\r\n", "\n").split("\n")
    n = int(m.group(1))
    if 1 <= n <= len(ls):
        return (f"\nSyntaxfehler in deiner Zeile {n}: `{ls[n-1].strip()}` - Bezeichner nur ASCII ohne Leerzeichen, "
                "Variablennamen konsistent verwenden.")
    return ""


def plan_multi_stub(item: dict, text: str, stubs: list[str], ask: Callable[[str], str]) -> dict:
    rt = item.get("regression_test") or ""
    test_text = (ROOT / rt).read_text(encoding="utf-8") if rt and (ROOT / rt).is_file() else ""
    prompt = multi_stub_prompt(item, text, stubs, test_text) + _korrektur_text(item)
    if item.get("last_test_output"):
        prompt += "\nLetzter Versuch scheiterte an diesen Testfehlern, vermeide sie:\n" + item["last_test_output"]
    last = None
    for _ in range(3):
        reply = ""
        try:
            reply = ask(prompt)
            res = parse_code_blocks(reply, item["file"], stubs[0])
            got = {e.get("function") for e in res.get("edits") or []}
            miss = [s for s in stubs if s not in got]
            if miss:
                raise ValueError("STUBS_MISSING:" + ",".join(miss))
            return {"mode": "patch", "fn_mode": True, "multi_stub": stubs, "result": res}
        except ValueError as e:
            last = e
            prompt += _syntax_hint(reply, str(e))
            prompt += (f"\nACHTUNG: Letzte Antwort unbrauchbar ({e}). Liefere genau einen ```python-Block mit ALLEN "
                       f"Funktionen: {', '.join(stubs)}.")
    raise last


def plan_function(item: dict, ask: Callable[[str], str] | None = None) -> dict:
    ask = ask or _gpt_text
    f = item["file"]
    text = (ROOT / f).read_text(encoding="utf-8")
    stubs = stub_functions(text)  # 20261104_multistub: mehrere Stubs -> alle in einem Zyklus
    if len(stubs) > 1:
        return plan_multi_stub(item, text, stubs, ask)
    lines = text.splitlines()
    idx = next(i for i, ln in enumerate(lines) if _anchor_hit(item.get("anchor") or "", ln))
    name, src = _function_excerpt(text, idx)
    heads = "\n".join("\n".join(lines[n.lineno - 1:n.end_lineno]) for n in ast.parse(text).body
                      if isinstance(n, (ast.Import, ast.ImportFrom)))
    others = ", ".join(n.name for n in ast.parse(text).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name != name)
    acc = ""
    a = item.get("acceptance")
    if a and (ROOT / a).is_file():
        acc = (ROOT / a).read_text(encoding="utf-8")[:2500]
    prompt = (f"Du bist NEXUS GPT. Implementiere eine Python-Funktion vollstaendig.\nAufgabe: {item['title']}\nZiel: {item.get('goal','')}\n{_notes_text(item)}"
              f"Datei: {f}\nVorhandene Imports:\n```\n{heads}\n```\nAndere Funktionen in der Datei (nicht aendern): {others or '-'}\n"
              f"Aktuelle Funktion `{name}` (wird KOMPLETT ersetzt):\n```\n{src}\n```\n"
              + (f"Der Abnahmetest (muss bestehen, derzeit rot):\n```\n{acc}\n```\n" if acc else "") +
              "Regeln: Schreibe die GANZE Funktion neu (def-Zeile, Docstring, Rumpf), gleiche Signatur. Kein Search/Replace. "
              "Nur lokal, keine Pfade auf C:, keine Netzwerkzugriffe, ehrliche Fehler (ValueError mit Code). "
              "KEINE Docstrings und keine dreifachen Anfuehrungszeichen (nur # Kommentare); Keyword-Argumente immer NACH Positionsargumenten; Code muss mit ast.parse fehlerfrei sein. "
              "Zusaetzliche Hilfsfunktionen sind als weitere Edits erlaubt (werden angehaengt). Fehlende Imports als Liste 'imports'. "
              "AUSGABEFORMAT (kein JSON): genau EIN Codeblock ```python mit ggf. benoetigten Import-Zeilen oben, dann "
              f"der kompletten Funktion `def {name}(...)` und ggf. Hilfsfunktionen. Danach eine Zeile 'Begruendung: ...'.")
    err_ctx = " ".join([str((item.get("_korrektur") or ("", ""))[1]), str(item.get("last_test_output") or "")])
    draft = str((item.get("_korrektur") or ("", ""))[0])
    try:  # 20261030_lookup: echte Bezeichner der verwendeten Module nachschlagen
        dcode = "\n".join(re.findall(r"(?m)^\s*(?:from\s+[\w.]+\s+import\s+[\w, ]+|import\s+[\w.]+)\s*$", draft.replace("\\n", "\n")))
        prompt += api_hints(text + "\n" + dcode, err_ctx)
    except Exception:
        pass
    prompt += _korrektur_text(item)
    if item.get("last_test_output"):
        prompt += "\nLetzter Versuch scheiterte an diesen Testfehlern, vermeide sie:\n" + item["last_test_output"]
    last = None
    for _ in range(3):
        try:
            res = parse_code_blocks(ask(prompt), f, name)
            if not res.get("edits"):
                raise ValueError("NO_EDITS")
            return {"mode": "patch", "fn_mode": True, "result": res}
        except ValueError as e:
            last = e
            prompt += (f"\nACHTUNG: Letzte Antwort unbrauchbar ({e}). Liefere genau einen ```python-Block mit der "
                       "vollstaendigen, syntaktisch korrekten Funktion.")
    raise last


PATCH_RULES = ("\nPflicht: Liefere IMMER einen konkreten Patch (mind. 1 Edit) - keine Verweigerung, keine Rueckfragen. "
               "Wenn unsicher: minimal-invasiv (z.B. robustere Fehlerbehandlung an der Fehlerstelle). Der Ausschnitt "
               "enthaelt die vollstaendigen betroffenen Funktionen/Klassen samt Imports - das genuegt.")


def _focus_text(item: dict) -> str:
    """Fehlerstelle + umgebende Funktion benennen: Patch soll dort ansetzen, nicht in Prompt-/Stringtexten."""
    try:
        text = (ROOT / item["file"]).read_text(encoding="utf-8")
        lines = text.splitlines()
        idx = next(i for i, ln in enumerate(lines) if _anchor_hit(item["anchor"], ln))
        fn = next((n.name for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.lineno <= idx + 1 <= n.end_lineno and n.end_lineno - n.lineno < 60), "?")
    except Exception:
        return ""
    return (f"\nFEHLERSTELLE: Zeile {idx + 1} `{lines[idx].strip()}` in Funktion `{fn}`. Aendere bevorzugt NUR diese "
            "Funktion; 'find' sollen 1-6 kurze Codezeilen sein (keine langen String-/Prompttexte). Jeder Edit braucht "
            "'file', 'find' UND 'replace'.")


def _korrektur_text(item: dict) -> str:
    k = item.get("_korrektur")
    if not k:
        return ""
    return ("\nKORREKTUR: Dein vorheriger Entwurf war fehlerhaft und wurde NICHT angewendet.\nEntwurf: " + k[0] +
            "\nFehler: " + k[1] + "\nLiefere einen korrigierten vollstaendigen Entwurf im selben JSON-Format.")


def _ast_excerpt(text: str, idx: int) -> str | None:
    """Vollstaendige umgebende Funktion/Klasse (+ Aufrufer, max 3) samt Imports, exakt aus der Datei."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    lines = text.splitlines()
    ln = idx + 1
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
             and n.lineno <= ln <= (n.end_lineno or n.lineno)]
    if not nodes:
        return None
    funcs = [n for n in nodes if not isinstance(n, ast.ClassDef)]
    inner = max(funcs or nodes, key=lambda n: n.lineno)
    outer = min(nodes, key=lambda n: n.lineno)
    main = outer if (outer.end_lineno - outer.lineno) <= 150 else inner
    def seg(n):
        start = min([d.lineno for d in getattr(n, "decorator_list", [])] + [n.lineno])
        return "\n".join(lines[start - 1:n.end_lineno])
    parts = ["# --- Imports ---"] + ["\n".join(lines[n.lineno - 1:n.end_lineno]) for n in tree.body
                                     if isinstance(n, (ast.Import, ast.ImportFrom))]
    parts += [f"# --- Betroffen: {main.name} (Zeilen {main.lineno}-{main.end_lineno}) ---", seg(main)]
    name = inner.name
    callers = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n is not inner
               and not (main.lineno <= n.lineno <= main.end_lineno)
               and any(isinstance(c, ast.Call) and getattr(c.func, "id", getattr(c.func, "attr", None)) == name for c in ast.walk(n))]
    used = sum(len(p.splitlines()) for p in parts)
    for c in callers[:3]:
        if used + (c.end_lineno - c.lineno) > 260:
            break
        parts += [f"# --- Aufrufer: {c.name} (Zeilen {c.lineno}-{c.end_lineno}) ---", seg(c)]
        used += c.end_lineno - c.lineno
    return "\n".join(parts)


def _gpt(prompt: str, fmt: str | None = "json") -> str:
    from nexus.integrations.gpt_local_agent import ask
    r = ask(prompt, fmt=fmt, num_ctx=GPT_CTX)  # JSON erzwungen, begrenzter Kontext (gegen NO_JSON)
    for wait in (20, 40):  # voruebergehende Ollama-Fehler (Modellwechsel/RAM) -> kurz warten, erneut
        if r.get("success") or r.get("error") not in ("GPT_LOCAL_HTTP_ERROR", "GPT_LOCAL_UNAVAILABLE", "GPT_LOCAL_EMPTY_RESPONSE"):
            break
        _emit("NEXUS-GPT", "retry", f"{r.get('error')} -> neuer Versuch in {wait}s", ok=None)
        time.sleep(wait)
        r = ask(prompt, fmt=fmt, num_ctx=GPT_CTX)
    if not r.get("success"):
        raise RuntimeError("NEXUS_GPT_FAILED:" + str(r.get("error")))
    return str(r.get("reply") or "")


def _json_from(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("NO_JSON")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return json.loads(m.group(0), strict=False)  # rohe Zeilenumbrueche in Strings tolerieren (20261029)


def locate_source(item: dict) -> tuple[str, str] | None:
    """Zieldatei fuer SB-AUTO selbst ermitteln: Traceback-Zeilen, sonst Fehlercode/Bezeichner im Repo suchen."""
    logs = "\n".join(item.get("logs") or [])
    for m in re.finditer(r'File "([^"]+\.py)", line (\d+)', logs):
        try:
            fp = Path(m.group(1)).resolve()
            rel = fp.relative_to(ROOT)
        except Exception:
            continue
        if fp.is_file() and rel.parts[0] == "nexus":
            ln = fp.read_text(encoding="utf-8").splitlines()[int(m.group(2)) - 1]
            return str(rel).replace("\\", "/"), re.escape(ln.strip())
    key = str(item.get("auto_key") or "")
    terms = [t for t in re.findall(r"[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+", key.split(":", 1)[-1])] + \
        re.findall(r"\b([a-z_][a-z0-9_]{3,})\(", logs)
    best = None
    for term in terms:
        for fp in sorted((ROOT / "nexus").rglob("*.py")):
            if ".bak" in fp.name:
                continue
            for ln in fp.read_text(encoding="utf-8", errors="replace").splitlines():
                if term in ln and ("raise" in ln or "return" in ln or "def " + term in ln):
                    score = (2 if f'"{term}"' in ln or f"'{term}'" in ln else 1) + (1 if "raise" in ln else 0)
                    if not best or score > best[0]:
                        best = (score, str(fp.relative_to(ROOT)).replace("\\", "/"), re.escape(ln.strip()))
        if best:
            return best[1], best[2]
    return None


def plan_edits(item: dict) -> dict:
    if fn_mode_applicable(item):  # 20261030_fnmode
        return plan_function(item)
    f, ex = _excerpt(item)
    if f and item.get("auto_key"):
        tpath = item["regression_test"]
        prompt = (f"Du bist NEXUS GPT. Behebe einen wiederkehrenden Fehler der NEXUS-Selbstbau-Engine/Hermes.\n"
                  f"Fehler: {item['title']}\nZiel: {item.get('goal','')}\n{_notes_text(item)}Log-Auszuege:\n" + "\n".join(item.get("logs") or [])[:1200] +
                  f"\nDatei: {f}\nAusschnitt (exakt aus der Datei):\n```\n{ex}\n```\n"
                  "Regeln: minimaler Patch, keine Sicherheitspruefung abschalten, nichts auf C:. 'find' = EXAKT kopierte "
                  "zusammenhaengende Zeilen aus dem Ausschnitt. Schreibe zusaetzlich einen pytest-Regressionstest, der den "
                  "Fehlerfall nachstellt (Netz/Modelle per monkeypatch ersetzen, kein tmp_path) und nach dem Patch besteht. "
                  "Antworte NUR als JSON: {\"edits\": [{\"file\": \"" + f + "\", \"find\": \"...\", \"replace\": \"...\"}], "
                  "\"test\": {\"path\": \"" + tpath + "\", \"content\": \"...\"}, \"begruendung\": \"...\"}")
        prompt += PATCH_RULES + _korrektur_text(item) + _focus_text(item)
        if item.get("last_test_output"):
            prompt += ("\nDein letzter Versuch scheiterte mit diesen Testfehlern, vermeide sie (Imports pruefen, "
                       "nur vorhandene Funktionen nutzen):\n" + item["last_test_output"])
        last = None
        for _ in range(3):
            try:
                return {"mode": "patch", "result": _json_from(_gpt(prompt))}
            except ValueError as e:
                last = e
        raise last
    if not f:
        prompt = (f"Du bist NEXUS GPT. Aufgabe aus dem NEXUS-Selbstbau-Backlog: {item['title']}\n{item.get('goal','')}\n{_notes_text(item)}"
                  "Erstelle eine knappe technische Analyse auf Deutsch: Ursache/Ansatz, betroffene Module, konkrete naechste "
                  "Bauschritte (max 6), Risiken, Testidee. Antworte NUR als JSON {\"analyse\": \"...\", \"schritte\": [\"...\"]}.")
        try:
            return {"mode": "analysis", "result": _json_from(_gpt(prompt))}
        except ValueError:  # 1 Retry mit strengerer Anweisung (20261029)
            prompt += ("\nACHTUNG: Letzte Antwort war kein gueltiges JSON. Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, "
                       "ohne Text davor/danach; Anfuehrungszeichen und Zeilenumbrueche (\\n) korrekt escapen.")
            return {"mode": "analysis", "result": _json_from(_gpt(prompt))}
    prompt = (f"Du bist NEXUS GPT und entwirfst einen minimalen, sicheren Code-Patch.\nAufgabe: {item['title']}\nZiel: {item.get('goal','')}\n{_notes_text(item)}"
              f"Datei: {f}\nAusschnitt (exakt aus der Datei):\n```\n{ex}\n```\n"
              "Regeln: 'find' muss eine EXAKT kopierte, zusammenhaengende Zeilenfolge aus dem Ausschnitt sein (gleiche Einrueckung). "
              "'replace' ersetzt sie. Kein anderer Text. Antworte NUR als JSON: "
              "{\"edits\": [{\"file\": \"" + f + "\", \"find\": \"...\", \"replace\": \"...\"}], \"begruendung\": \"...\"}")
    prompt += PATCH_RULES + _korrektur_text(item)
    acc = ""
    for t in item.get("tests") or []:
        tp = ROOT / t
        if tp.is_file() and "placeholder" in tp.name or (tp.is_file() and item.get("acceptance") == t):
            acc = tp.read_text(encoding="utf-8")[:1800]
            break
    if acc:
        prompt += "\nDer Patch MUSS diesen Abnahmetest bestehen (er schlaegt derzeit fehl):\n```\n" + acc + "\n```"
    last = None
    for _ in range(3):  # NEXUS GPT liefert gelegentlich kaputtes JSON -> neu fragen
        try:
            return {"mode": "patch", "result": _json_from(_gpt(prompt))}
        except ValueError as e:
            last = e
            prompt += "\nACHTUNG: Letzte Antwort war kein gueltiges JSON. Escape Anfuehrungszeichen und Zeilenumbrueche (\\n) korrekt."
    raise last


def _run_tests(args: list[str], timeout: int) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", *args], cwd=str(ROOT),
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout, env=child_env(),
                           creationflags=BELOW_NORMAL if sys.platform == "win32" else 0)
        try:  # NEXUS-Terminal (20261031): nur protokollieren
            from nexus.terminal.service import record as _trec
            _trec("Autobuild", ["pytest", "-q", *args], cwd=ROOT, rc=p.returncode, stdout=(p.stdout or "")[-6000:],
                  stderr=(p.stderr or "")[-3000:], sec=round(time.time() - t0, 2), kind="pytest")
        except Exception:
            pass
        tail = (p.stdout or "").strip().splitlines()[-1:] or [""]
        errs = [ln for ln in (p.stdout or "").splitlines() if ln.startswith(("E ", "ERROR", "FAILED")) or "Error" in ln]
        return {"ok": p.returncode == 0, "rc": p.returncode, "summary": tail[0][-300:], "sec": round(time.time() - t0),
                "detail": "\n".join(errs[-12:])[-1200:]}
    except subprocess.TimeoutExpired:
        try:  # NEXUS-Terminal (20261031)
            from nexus.terminal.service import record as _trec
            _trec("Autobuild", ["pytest", "-q", *args], cwd=ROOT, rc=None, stderr=f"TIMEOUT nach {timeout}s", sec=timeout, kind="pytest", ok=False)
        except Exception:
            pass
        return {"ok": False, "rc": None, "summary": "TIMEOUT", "sec": timeout}


PENDING = DIR / "hermes_pending.json"
NO_RETRY = ("CONTENT_MISMATCH_NO_OVERWRITE", "EXPLICIT_FILE_REQUIREMENTS_REQUIRED", "C_DRIVE_BLOCKED")


def _maintenance_active() -> bool:
    try:
        from nexus.maintenance import maintenance
        return bool(maintenance().is_draining())
    except Exception:
        return False


def _hermes_report(cycle_id: str, text: str, wait_sec: int = 120) -> dict:
    """Zyklusbericht ueber Hermes' vertragsgebundenen Schreibpfad (apply_requirements).

    Contract = genau diese eine Anforderung {path, content}; create_file + read_file-Rueckpruefung
    bytegenau, ohne Modell im Schreibpfad (das Modell veraenderte Escapes -> TOOL_OUTSIDE_AUTHORIZED_CONTRACT).
    Jeder Versuch ist ein neuer Bridge-Auftrag (neue task_ids, kein offener Batch -> keine Reconciliation).
    Waehrend MAINTENANCE_ACTIVE wird gewartet bzw. zurueckgestellt, nie gesendet.
    """
    path = f"workspace/projects/hermes/autobuild_{cycle_id}.md"
    t0 = time.time()
    while _maintenance_active():
        if time.time() - t0 > wait_sec:
            return {"ok": False, "path": path, "error": "DEFERRED_MAINTENANCE_ACTIVE", "deferred": True}
        time.sleep(10)
    try:
        check_write(ROOT / path)
        from nexus.integrations.hermes_brain import HermesBrain
        last: dict = {}
        for attempt in range(2):
            res = HermesBrain(None).apply_requirements([{"path": path, "content": text}])
            last = {"ok": res.get("status") == "PASS", "path": path, "status": res.get("status"),
                    "error": res.get("error"), "task_ids": res.get("task_ids"), "attempt": attempt + 1,
                    "sha256": ((res.get("verification") or [{}])[-1] or {}).get("sha256")}
            if last["ok"] or str(res.get("error")) in NO_RETRY or _maintenance_active():
                break
            time.sleep(5)
        return last
    except Exception as e:
        return {"ok": False, "path": path, "error": f"{type(e).__name__}:{str(e)[:160]}"}


AUTO_CODE = re.compile(r"[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+")


def record_issue(items: list[dict], source: str, error: str, excerpt: str) -> dict | None:
    """Selbstheilung: Fehlercode als SB-AUTO-Punkt erfassen; Duplikate zusammenfassen.
    Ab dem 2. Auftreten 'offen' (wiederkehrend) -> wird in Folgezyklen von NEXUS/GPT/Hermes bearbeitet."""
    codes = AUTO_CODE.findall(str(error or ""))  # spezifischster (letzter) Code, z.B. PLAN_FAILED:NO_JSON -> NO_JSON
    code = codes[-1] if codes else (str(error or "UNBEKANNT").split(":")[0][:60] or "UNBEKANNT")
    key = f"{source}:{code}"
    it = next((i for i in items if i.get("auto_key") == key), None)
    if it is None:
        n = 1 + max([int(i["id"].rsplit("-", 1)[1]) for i in items if str(i.get("id", "")).startswith("SB-AUTO-")] or [0])
        it = {"id": f"SB-AUTO-{n:02d}", "title": f"Selbstheilung: {source} Fehler {code}", "auto_key": key,
              "status": "beobachtet", "count": 0, "logs": [],
              "goal": (f"Wiederkehrender Fehler {code} in {source}. Ursache finden und beheben, ohne Sicherheitspruefungen "
                       "abzuschalten. C: bleibt tabu. Log-Auszuege siehe 'logs'.")}
        items.append(it)
    it["count"] = int(it.get("count") or 0) + 1
    it["last_seen"] = _now()
    it["logs"] = (it.get("logs") or [])[-2:] + [str(excerpt)[:400]]
    if it["count"] >= 2 and it.get("status") == "beobachtet":
        it["status"] = "offen"
    _emit("NEXUS", "selbstheilung", f"{it['id']} {code} ({it['count']}x, {it['status']})", ok=None)
    return it



def ensure_target_scaffold(item: dict) -> str | None:
    """Wrapper: fehlende Ziel-Dateien stubben (PLAN FileNotFound / FIND_MATCHES_0)."""
    return _ensure_scaffold_impl(root=ROOT, check_write=check_write, item=item)


def _deps_satisfied(item: dict, by_id: dict[str, dict]) -> bool:
    """depends_on/deps: alle genannten IDs muessen status==erledigt sein."""
    deps = item.get("depends_on") if item.get("depends_on") is not None else item.get("deps")
    if not deps:
        return True
    if not isinstance(deps, (list, tuple)):
        return True
    for d in deps:
        dep = by_id.get(str(d))
        if dep is None or str(dep.get("status")) != "erledigt":
            return False
    return True


def pick_next_item(items: list[dict], item_id: str | None = None) -> dict | None:
    """Return the next actionable backlog item, blocking exhausted items first.
    Respektiert depends_on/deps (nur wenn alle deps erledigt)."""
    by_id = {str(i.get("id")): i for i in items if isinstance(i, dict) and i.get("id") is not None}
    candidates = []
    for item in items:
        if not isinstance(item, dict):
            continue
        st = str(item.get("status") or "")
        if st in ("offen", "fehlgeschlagen", "freigegeben") and (not item_id or item.get("id") == item_id):
            if int(item.get("attempts") or 0) >= MAX_ATTEMPTS_PER_ITEM:
                item["status"] = "blocked"
                continue
            if not _deps_satisfied(item, by_id):
                continue
            candidates.append(item)
    candidates.sort(key=lambda item: (-int(item.get("prio") or 0),
                                      0 if str(item.get("id", "")).startswith("SB-AUTO-") else 1))
    return candidates[0] if candidates else None



# 2026-11-02 Phase 1: echte Abnahme vor "erledigt" (kein PASS nur aus gruenem pytest)
_ACC_TAGS = ("div", "section", "script", "style", "button", "main", "nav", "header", "footer", "ul", "aside")


def _html_balance(text: str) -> list[str]:
    body = re.sub(r"(?is)<!--.*?-->", "", text)
    body = re.sub(r"(?is)(<script\b[^>]*>).*?(</script>)", r"\1\2", body)
    body = re.sub(r"(?is)(<style\b[^>]*>).*?(</style>)", r"\1\2", body)
    errs = []
    for t in _ACC_TAGS:
        o = len(re.findall(rf"(?i)<{t}(?=[\s>])", body)); c = len(re.findall(rf"(?i)</{t}\s*>", body))
        if o != c:
            errs.append(f"TAG_UNBALANCED:{t}:{o}/{c}")
    return errs


def _node_check(code: str, label: str) -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    tmp = DIR / "tmp_accept"; tmp.mkdir(parents=True, exist_ok=True)
    f = tmp / (re.sub(r"[^\w.-]", "_", label) + ".js")
    f.write_text(code, encoding="utf-8")
    try:
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=30)
        return None if r.returncode == 0 else f"JS_SYNTAX:{label}:{(r.stderr or '').strip().splitlines()[-1:]}"
    finally:
        f.unlink(missing_ok=True)


def _static_check(path: Path) -> list[str]:
    if not path.exists():
        return [f"FILE_MISSING:{path.name}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() == ".js":
        e = _node_check(text, path.name)
        return [e] if e else []
    errs = _html_balance(text)
    for n, m in enumerate(re.finditer(r"(?is)<script\b([^>]*)>(.*?)</script>", text)):
        attrs, code = m.group(1).lower(), m.group(2)
        if "src=" in attrs or "json" in attrs or "module" in attrs or not code.strip():
            continue
        e = _node_check(code, f"{path.stem}_script{n}")
        if e:
            errs.append(e)
    return errs


def acceptance_check(item: dict, http_get: Callable[[str], tuple[int, str]] | None = None) -> dict:
    """Abnahme: item['accept'] = [{"http": url, "status": 200, "contains": [...]}, {"static": "pfad"}].
    Ohne accept: HTML/JS-Zieldateien werden automatisch statisch geprueft. ok nur wenn ALLE Checks bestanden."""
    def _get(url):
        with urllib.request.urlopen(url, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace")
    http_get = http_get or _get
    checks = list(item.get("accept") or [])
    files = [item.get("file")] + list(item.get("files") or [])
    for f in files:
        if f and str(f).lower().endswith((".html", ".js")) and not any(c.get("static") == f for c in checks):
            checks.append({"static": f})
    results, errs = [], []
    for c in checks:
        if c.get("http"):
            try:
                st, body = http_get(c["http"])
            except Exception as e:
                st, body = getattr(e, "code", 0), ""
            miss = [x for x in (c.get("contains") or []) if x not in body]
            e = [] if st == int(c.get("status", 200)) and not miss else [f"HTTP:{c['http']}:{st}:missing={miss}"]
        elif c.get("static"):
            e = _static_check(ROOT / c["static"])
        else:
            e = [f"ACCEPT_UNKNOWN:{c}"]
        results.append({"check": c, "ok": not e, "errors": e}); errs += e
    return {"ok": not errs, "n": len(checks), "results": results, "error": "; ".join(errs)[:300]}


class Autobuild:
    def __init__(self, tests_runner: Callable[[list[str], int], dict] | None = None,
                 planner: Callable[[dict], dict] | None = None, hermes: Callable[[str, str], dict] | None = None,
                 busy: Callable[[], str | None] | None = None,
                 reviewer: Callable[[dict, dict], dict] | None = None) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.log = logging.getLogger(__name__)
        self.tests_runner = tests_runner or _run_tests
        self.planner = planner or plan_edits
        self.hermes = hermes or _hermes_report
        self.busy = busy or system_busy
        self.reviewer = reviewer or hermes_review
        self.last: dict | None = None
        self.running_cycle = False

    # ---------- Steuerung ----------
    def status(self) -> dict:
        items = load_backlog()
        return {"success": True, "loop": bool(self._thread and self._thread.is_alive()), "stop_file": STOP_FILE.exists(),
                "cycle_running": self.running_cycle, "interval_sec": INTERVAL_SEC, "last": self.last,
                "backlog": [{k: i.get(k) for k in ("id", "title", "status", "attempts")} for i in items]}

    def start(self) -> dict:
        if STOP_FILE.exists():
            STOP_FILE.unlink()
        if not (self._thread and self._thread.is_alive()):
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="nexus-autobuild", daemon=True)
            self._thread.start()
            _emit("NEXUS", "start", "Selbstbau-Loop gestartet")
        return self.status()

    def stop(self) -> dict:
        self._stop.set()
        _write(STOP_FILE, _now())
        _emit("NEXUS", "stop", "Selbstbau-Loop gestoppt")
        return self.status()

    def _loop(self) -> None:
        if self._stop.wait(FIRST_DELAY_SEC):
            return

        _emit("NEXUS", "start",
              "Kontinuierlicher Selbstbau aktiviert", ok=True)

        while not self._stop.is_set() and not STOP_FILE.exists():

            # Alle aktuell bearbeitbaren Aufgaben nacheinander abarbeiten
            while not self._stop.is_set() and not STOP_FILE.exists():
                try:
                    result = self.cycle()

                    state = (
                        result.get("result")
                        if isinstance(result, dict)
                        else None
                    )

                    if state == "NICHTS_OFFEN":
                        _emit("NEXUS", "status",
                              "Alle aktuell bearbeitbaren Aufgaben abgearbeitet")
                        break

                    if state == "PAUSIERT":
                        _emit("NEXUS", "status",
                              "System belegt; erneuter Versuch folgt")
                        if self._stop.wait(30):
                            return
                        continue

                    if state == "CYCLE_RUNNING":
                        if self._stop.wait(2):
                            return
                        continue

                    # Fehler werden im Backlog dokumentiert.
                    # Der nächste Durchlauf übernimmt den nächsten Punkt,
                    # sobald der vorherige blockiert oder abgeschlossen ist.
                    if self._stop.wait(1):
                        return

                except Exception as e:
                    self.log.error("Traceback:\n%s", traceback.format_exc())
                    _emit("NEXUS", "error",
                          f"Selbstbau-Fehler: {type(e).__name__}: {e}",
                          ok=False)
                    if self._stop.wait(5):
                        return

            if self._stop.is_set() or STOP_FILE.exists():
                break

            # Backlog regelmäßig auf neu hinzugekommene Aufgaben prüfen
            if self._stop.wait(INTERVAL_SEC):
                break

        _emit("NEXUS", "stop", "Selbstbau beendet")
    # ---------- Zyklus ----------
    def cycle(self, item_id: str | None = None, full_suite: bool = True) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"success": False, "error": "CYCLE_RUNNING"}
        self.running_cycle = True
        try:
            result = self._cycle(item_id, full_suite)
            self.last = result
            return result
        finally:
            self.running_cycle = False
            self._lock.release()

    def _cycle(self, item_id, full_suite) -> dict:
        cid = datetime.now().strftime("%Y%m%d_%H%M%S")
        rec: dict[str, Any] = {"cycle": cid, "ts": _now()}
        try:
            self._flush_pending()
        except Exception:
            pass
        busy = self.busy()
        if busy:
            rec.update(result="PAUSIERT", reason=busy)
            _emit("NEXUS", "skip", f"Selbstbau pausiert: {busy}")
            return self._finish(rec)
        items = load_backlog()
        # 2026-10-28 chatplan: Phase-2-Items erst freigeben, wenn alle Phase-1-Items erledigt (PASS) sind
        p1 = [i for i in items if i.get("phase") == 1]
        if p1 and all(i.get("status") == "erledigt" for i in p1):
            for i in items:
                if i.get("phase") == 2 and i.get("status") == "wartet_phase1":
                    i["status"] = "offen"
            save_backlog(items)
        item = pick_next_item(items, item_id)
        if item is None:
            save_backlog(items)
            rec.update(result="NICHTS_OFFEN")
            return self._finish(rec)
        rec.update(item=item["id"], title=item["title"])
        try:
            sc = ensure_target_scaffold(item)
            if sc:
                _emit("NEXUS", "plan", f"{item['id']}: {sc}")
                rec["scaffold"] = sc
        except Exception as e:
            return self._fail(items, item, rec, f"SCAFFOLD_FAILED:{e}")
        if (item.get("auto_key") or item.get("status") == "freigegeben") and not item.get("file"):
            loc = locate_source(item)
            if loc:
                item["file"], item["anchor"] = loc
                item["regression_test"] = "tests/test_auto_" + item["id"].lower().replace("-", "_") + ".py"
                item["tests"] = [item["regression_test"], "tests/test_autobuild.py"]
                _emit("NEXUS", "plan", f"{item['id']}: Quelle ermittelt {loc[0]}", data={"anchor": loc[1][:120]})
                save_backlog(items)
        if item.get("status") == "freigegeben" and not item.get("file"):
            item["status"] = "blockiert"
            item["block_reason"] = "SOURCE_NOT_FOUND: Keine passende Quelldatei gefunden."
            rec.update(result="BLOCKIERT", reason=item["block_reason"])
            save_backlog(items)
            return self._finish(rec)
        _emit("NEXUS", "plan", f"Selbstbau {cid}: gewaehlt {item['id']} â€“ {item['title']}")
        try:
            _emit("NEXUS", "delegate", "Entwurf an NEXUS GPT", data={"item": item["id"]})
            plan = self.planner(item)
            _emit("NEXUS-GPT", "result", f"Entwurf fuer {item['id']}: {plan['mode']}", ok=True)
        except Exception as e:
            self.log.error("Traceback:\n%s", traceback.format_exc())
            return self._fail(items, item, rec, f"PLAN_FAILED:{e}")
        if plan["mode"] == "analysis":
            item["status"] = "analysiert"
            item["analyse"] = plan["result"]
            rec.update(result="ANALYSIERT", analyse=plan["result"])
            save_backlog(items)
            return self._finish(rec)
        rec["begruendung"] = plan["result"].get("begruendung")
        bdir = DIR / "backups" / cid
        correction_used = False
        tests_retry_used = False
        review_used = False
        while True:
            backups: list[tuple[Path, Path | None]] = []
            try:
                new_texts = _compute_edits(plan["result"].get("edits") or [])
                if plan.get("fn_mode") and not review_used:  # 20261030_lookup: Hermes prueft vor dem Testlauf
                    review_used = True
                    _emit("NEXUS", "delegate", f"{item['id']}: Entwurf an Hermes zur Pruefung")
                    try:
                        rv = self.reviewer(item, plan)
                    except Exception as e:
                        rv = {"status": "UNAVAILABLE", "detail": type(e).__name__}
                    rec["review"] = {k: rv.get(k) for k in ("status", "detail")} | {"reply": str(rv.get("reply") or "")[:300]}
                    _emit("Hermes", "result", f"Review {item['id']}: {rv.get('status')} {str(rv.get('detail') or '')}", ok=rv.get("status") != "ISSUES")
                    if rv.get("status") == "ISSUES":
                        try:
                            item["_korrektur"] = (json.dumps(plan["result"], ensure_ascii=False)[:2500],
                                                  "HERMES-REVIEW fand Probleme:\n" + str(rv.get("reply"))[:1200])
                            plan2 = self.planner(item)
                            _compute_edits(plan2["result"].get("edits") or [])  # nur uebernehmen, wenn anwendbar
                            plan = plan2
                            rec["review"]["nachgebessert"] = True
                            rec["begruendung"] = plan["result"].get("begruendung")
                        except Exception as e2:
                            rec["review"]["nachbesserung_fehler"] = str(e2)[:200]
                        finally:
                            item.pop("_korrektur", None)
                        new_texts = _compute_edits(plan["result"].get("edits") or [])
                tst = plan["result"].get("test") if item.get("auto_key") else None
                if tst:  # Regressionstest VOR Anwendung kompilieren
                    compile(str(tst.get("content") or ""), item["regression_test"], "exec")
                for target, text in new_texts.items():
                    b = check_write(bdir / target.relative_to(ROOT))
                    b.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, b)
                    backups.append((target, b))
                    _write(target, text)
                if tst:
                    tp = check_write(ROOT / item["regression_test"])  # Pfad fest vorgegeben, nur tests/
                    if tp.exists():
                        b = check_write(bdir / tp.relative_to(ROOT)); b.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(tp, b); backups.append((tp, b))
                    else:
                        backups.append((tp, None))
                    _write(tp, str(tst.get("content") or ""))
                    new_texts[tp] = tp.read_text(encoding="utf-8")
                    col = self.tests_runner(["--collect-only", item["regression_test"]], 120)
                    if not col.get("ok"):
                        raise ValueError("TEST_COLLECT_FAILED:" + (col.get("detail") or col.get("summary") or "")[:600])
                rec["changed"] = [str(t.relative_to(ROOT)) for t in new_texts]
                _emit("NEXUS", "action", f"Patch angewendet: {', '.join(rec['changed'])}", ok=True)
                _emit("NEXUS", "test", f"Tests: {item.get('tests') or 'tests'}")
                t1 = self.tests_runner(list(item.get("tests") or ["tests"]), 600)
                rec["test_target"] = t1
                t2 = self.tests_runner(["tests"], 900) if (t1["ok"] and full_suite) else None
                rec["test_full"] = t2
                if not (t1["ok"] and (t2 is None or t2["ok"])):
                    raise _TestsFailed(((t2 or t1) or {}).get("detail", "") or ((t2 or t1) or {}).get("summary", ""))
                break
            except CDriveBlocked as e:
                self._rollback(backups)
                return self._fail(items, item, rec, f"APPLY_FAILED:{e}")
            except _TestsFailed as e:
                self._rollback(backups)
                rec["rollback"] = True
                item["last_test_output"] = str(e)[:1200]
                if not tests_retry_used:  # 1 Korrekturrunde mit Testfehlern vor endgueltigem Rollback
                    tests_retry_used = True
                    rec["test_korrektur"] = str(e)[:300]
                    _emit("NEXUS", "korrektur", f"{item['id']}: Tests rot -> Korrektur an NEXUS GPT", ok=None)
                    try:
                        item["_korrektur"] = (json.dumps(plan["result"], ensure_ascii=False)[:2500],
                                              ("TESTS_FAILED - fehlgeschlagene Tests/Trace:\n" + str(e))[:1200])
                        plan = self.planner(item)
                    except Exception as e2:
                        return self._fail(items, item, rec, f"PLAN_FAILED:{e2}")
                    finally:
                        item.pop("_korrektur", None)
                    rec.pop("rollback", None)
                    rec["begruendung"] = plan["result"].get("begruendung")
                    continue
                return self._fail(items, item, rec, "TESTS_FAILED")
            except DisplayUnavailable as e:
                self._rollback(backups)
                item["status"] = "skipped"
                rec.update(result="SKIPPED", reason=str(e))
                _emit("NEXUS", "skip", f"{item['id']}: {e}")
                save_backlog(items)
                return self._finish(rec)
            except Exception as e:
                self.log.error("Traceback:\n%s", traceback.format_exc())
                self._rollback(backups)
                err = f"{type(e).__name__}:{e}"
                if not correction_used:  # 1 Korrekturrunde, zaehlt NICHT als Fehlversuch
                    correction_used = True
                    rec["korrektur"] = err[:300]
                    _emit("NEXUS", "korrektur", f"{item['id']}: Entwurf zur Korrektur an NEXUS GPT ({err[:120]})", ok=None)
                    try:
                        item["_korrektur"] = (json.dumps(plan["result"], ensure_ascii=False)[:2500], err[:800])
                        plan = self.planner(item)
                    except Exception as e2:
                        return self._fail(items, item, rec, f"PLAN_FAILED:{e2}")
                    finally:
                        item.pop("_korrektur", None)
                    rec["begruendung"] = plan["result"].get("begruendung")
                    continue
                return self._fail(items, item, rec, f"APPLY_FAILED:{err}")
        acc = acceptance_check(item)
        rec["accept"] = {k: acc[k] for k in ("ok", "n", "error")}
        item["last_accept"] = rec["accept"]
        if not acc["ok"]:
            self._rollback(backups)
            rec["rollback"] = True
            return self._fail(items, item, rec, "ACCEPT_FAILED:" + acc["error"])
        item["status"] = "erledigt"
        rec["result"] = "UEBERNOMMEN"
        _emit("NEXUS", "done", f"Selbstbau {item['id']} uebernommen ({(rec.get('test_full') or rec['test_target'])['summary']})", ok=True)
        save_backlog(items)
        return self._finish(rec)

    def _rollback(self, backups) -> None:
        for target, b in backups:
            if b is None:
                target.unlink(missing_ok=True)  # neu angelegte Datei (z.B. Regressionstest) entfernen
            else:
                shutil.copy2(b, target)
        if backups:
            _emit("NEXUS", "rollback", f"Rollback: {len(backups)} Datei(en) wiederhergestellt", ok=True)

    def _fail(self, items, item, rec, reason) -> dict:
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["status"] = "blocked" if item["attempts"] >= MAX_ATTEMPTS_PER_ITEM else "fehlgeschlagen"
        item["last_error"] = reason[:300]
        rec.update(result="ROLLBACK" if rec.get("rollback") or "APPLY" in reason else "FEHLGESCHLAGEN", reason=reason[:300])
        _emit("NEXUS", "error", f"Selbstbau {item['id']}: {reason[:160]}", ok=False)
        if reason.split(":")[0] in ("PLAN_FAILED", "APPLY_FAILED") and not str(item.get("id", "")).startswith("SB-AUTO-"):
            record_issue(items, "Engine " + reason.split(":")[0], reason, f"{rec.get('cycle')} {item['id']}: {reason[:300]}")
        save_backlog(items)
        return self._finish(rec)

    def _flush_pending(self) -> None:
        pend = _load_json(PENDING, [])
        if not pend or _maintenance_active():
            return
        rest = []
        for p in pend:
            h = self.hermes(p["cycle"], p["text"])
            _emit("Hermes", "result", f"Nachgelieferter Zyklusbericht {'abgelegt' if h.get('ok') else 'FEHLER'}: {h.get('path')}", ok=h.get("ok"))
            if h.get("deferred"):
                rest.append(p)
        _write(PENDING, json.dumps(rest, ensure_ascii=False))

    def _finish(self, rec: dict) -> dict:
        rec["success"] = rec.get("result") in ("UEBERNOMMEN", "ANALYSIERT", "PAUSIERT", "NICHTS_OFFEN", "SKIPPED")
        line = (f"\n## Zyklus {rec['cycle']} ({rec['ts']})\n- Punkt: {rec.get('item','-')} {rec.get('title','')}\n"
                f"- Ergebnis: **{rec.get('result')}** {rec.get('reason','')}\n- Geaendert: {rec.get('changed', [])}\n"
                f"- Tests Ziel: {(rec.get('test_target') or {}).get('summary','-')} | Suite: {(rec.get('test_full') or {}).get('summary','-')}\n")
        if rec.get("analyse"):
            line += "- Analyse NEXUS GPT: " + json.dumps(rec["analyse"], ensure_ascii=False)[:1500] + "\n"
        if rec.get("item"):
            h = self.hermes(rec["cycle"], "# Autobuild " + rec["cycle"] + "\n" + line)
            rec["hermes"] = h
            if h.get("deferred"):
                pend = _load_json(PENDING, [])
                pend.append({"cycle": rec["cycle"], "text": "# Autobuild " + rec["cycle"] + "\n" + line})
                _write(PENDING, json.dumps(pend, ensure_ascii=False))
            elif not h.get("ok"):
                items = load_backlog()
                record_issue(items, "Hermes-Bericht", str(h.get("error")), json.dumps(h, ensure_ascii=False, default=str))
                save_backlog(items)
            _emit("Hermes", "result", f"Zyklusbericht {'abgelegt' if h.get('ok') else ('zurueckgestellt (Wartung)' if h.get('deferred') else 'FEHLER ' + str(h.get('error')))}: {h.get('path')}",
                  ok=h.get("ok") if not h.get("deferred") else None, data={k: h.get(k) for k in ("status", "error", "attempt", "sha256")})
            line += f"- Hermes-Bericht: {h.get('path')} ({'OK' if h.get('ok') else 'FEHLER ' + str(h.get('error',''))})\n"
        _write(PROGRESS, line, "a")
        _write(CYCLES, json.dumps(rec, ensure_ascii=False, default=str) + "\n", "a")  # inkl. Hermes-Ergebnis
        self.last = rec
        return rec


_AB: Autobuild | None = None


def get_autobuild() -> Autobuild:
    global _AB
    if _AB is None:
        _AB = Autobuild()
    return _AB
