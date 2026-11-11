"""Reparaturpfad fuer bestehende Projektdateien (Auftrag 002, 2026-10-15).

Ablauf: Pfad pruefen (nur innerhalb des Projektroots, Datei muss existieren) -> Backup + SHA-256
-> Fix ermitteln (deterministisch, sonst lokales Modell via master.llm) -> anwenden -> py_compile
+ passender Test (test_<name>.py daneben, sonst Import/Start der Datei) -> bei Fehler Backup
zurueckspielen und Hash pruefen. Arbeitsnachweis als JSON unter workspace/repair_receipts/.
"""
from __future__ import annotations

import difflib, hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECEIPT_DIR = ROOT / "workspace" / "repair_receipts"
BACKUP_DIR = ROOT / "workspace" / "repair_backups"
SCHEMA = "nexus.repair-order.v1"
MAX_ROUNDS = 2
TEST_TIMEOUT = 60

REPAIR_INTENT = re.compile(
    r"\b(reparier\w*|repair\w*|beheb\w*|behob\w*|korrigier\w*|(?:ä|ae)nder\w*|fix\w*|bugfix\w*|beseitig\w*)\b", re.I)
PATH_RE = re.compile(r"(?<![\w.\-])((?:[A-Za-z]:)?[\w./\\:-]*[\w-]+\.[A-Za-z][A-Za-z0-9]{0,5})\b", re.I)
ALLOWED_SUFFIX = {".py"}
# Schreibbare Reparaturbereiche. Die Projektgrenze selbst bleibt ROOT; alles ausserhalb
# wird in resolve() weiterhin als OUTSIDE_PROJECT abgelehnt. Pfade innerhalb des Projekts,
# die hier nicht genannt sind, werden als PATH_NOT_WRITABLE (nicht als "ausserhalb")
# gemeldet. nexus/games/ ist fuer die Spielfixierung freigegeben.
WRITE_ALLOWLIST = ("workspace/", "nexus/integrations/", "nexus/games/")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_allowed(rel: str) -> bool:
    """True, wenn der projektrelative Pfad in einem freigegebenen Schreibbereich liegt."""
    posix = str(rel).replace("\\", "/").lstrip("/")
    return any(posix == prefix.rstrip("/") or posix.startswith(prefix) for prefix in WRITE_ALLOWLIST)


def named_paths(text: str) -> list[str]:
    return [m.group(1) for m in PATH_RE.finditer(text or "")]


def resolve(raw: str):
    """-> (Path|None, fehlercode|None). Nur Dateien innerhalb ROOT, keine Traversal.

    Normalisierung: Backslashes werden vereinheitlicht, leere und '.'-Segmente entfernt,
    Laufwerkspfade bleiben absolut. Die Projektgrenze ist ROOT.resolve(); ein gueltiger
    projektinterner Pfad darf hier niemals als OUTSIDE_PROJECT enden.
    """
    s = (raw or "").strip().strip('"\'')
    norm = s.replace("\\", "/")
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None, "PATH_TRAVERSAL"
    is_abs = norm.startswith("/") or bool(re.match(r"^[A-Za-z]:", norm))
    p = Path(norm) if is_abs else ROOT.joinpath(*parts)
    try:
        rp = p.resolve()
    except Exception:
        return None, "PATH_INVALID"
    try:
        rp.relative_to(ROOT)
    except ValueError:
        return None, "OUTSIDE_PROJECT"
    if not rp.is_file():
        return None, "FILE_NOT_FOUND"
    if rp.suffix.lower() not in ALLOWED_SUFFIX:
        return None, "TYPE_NOT_SUPPORTED"
    return rp, None


def is_repair_order(text: str, paths=()) -> bool:
    """Reparaturverb + mindestens ein genannter Dateipfad. Ob der Pfad zulaessig ist,
    entscheidet run() (dort saubere Ablehnung ohne Schreibzugriff)."""
    t = text or ""
    if not REPAIR_INTENT.search(t):
        return False
    return bool(named_paths(t) or paths)


def _refuse(code: str, raw: str) -> dict:
    texts = {
        "OUTSIDE_PROJECT": "liegt ausserhalb des freigegebenen Projekts G:\\meine-ki\\nexus-ai",
        "PATH_TRAVERSAL": "enthaelt '..' (Pfad-Traversal)",
        "FILE_NOT_FOUND": "existiert nicht",
        "TYPE_NOT_SUPPORTED": "ist kein unterstuetzter Dateityp (nur .py)",
        "PATH_INVALID": "ist kein gueltiger Pfad",
        "PATH_NOT_WRITABLE": "liegt im Projekt, aber nicht in einem freigegebenen Schreibbereich "
                             "(workspace/, nexus/integrations/, nexus/games/)",
    }
    detail = f"Reparatur abgelehnt: '{raw}' {texts.get(code, code)}. Es wurde nichts geschrieben."
    return {"success": False, "intent": "repair", "status": "REFUSED", "error": "REPAIR_" + code,
            "reply": detail, "data": {"execution_status": "REFUSED", "path": raw, "code": code,
                                       "writes": 0, "task_ids": []}}


def _find_test(target: Path):
    for cand in (target.with_name("test_" + target.name), target.parent / "tests" / ("test_" + target.name)):
        if cand.is_file() and cand != target:
            return cand
    return None


def _verify(target: Path, test: Path | None) -> dict:
    py = sys.executable
    steps = []
    cmd = [py, "-m", "py_compile", str(target)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=TEST_TIMEOUT, cwd=str(target.parent))
    steps.append({"cmd": " ".join(cmd), "rc": r.returncode, "out": (r.stdout + r.stderr)[-2000:]})
    if r.returncode == 0:
        if test is not None:
            cmd = [py, str(test)]
        else:
            cmd = [py, "-c", f"import importlib.util,sys;s=importlib.util.spec_from_file_location('m',r'{target}');"
                             "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)"]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TEST_TIMEOUT,
                               cwd=str(target.parent), env=env)
            steps.append({"cmd": " ".join(cmd), "rc": r.returncode, "out": (r.stdout + r.stderr)[-2000:]})
        except subprocess.TimeoutExpired:
            steps.append({"cmd": " ".join(cmd), "rc": -9, "out": "TIMEOUT"})
    ok = all(s["rc"] == 0 for s in steps)
    return {"ok": ok, "steps": steps}


def _llm_fix(master, goal: str, target: Path, current: str, test_src: str, output: str):
    from nexus.integrations.hermes_brain import _strip_code_fence
    llm = getattr(master, "llm", None)
    if llm is None:
        return None, "MODEL_UNAVAILABLE"
    messages = [
        {"role": "system", "content":
            "You repair ONE existing Python file. Fix the root cause of the failing check shown below. "
            "Keep the public API (function names/signatures). Only the standard library. Output ONLY the "
            "complete corrected file content, no markdown fence, no explanation."},
        {"role": "user", "content": f"Task: {goal[:1500]}\n\nFailing check output:\n{output[-3000:]}\n\n"
                                    f"File to fix: {target.name}\n{current}\n\n"
                                    + (f"Test file (do not change, must pass):\n{test_src[:6000]}" if test_src else "")}]
    reply = llm.chat(messages, temperature=0, num_predict=4096)
    if not reply.get("success"):
        return None, "MODEL_UNAVAILABLE"
    fixed = _strip_code_fence(str(reply.get("reply") or "")).rstrip("\n") + "\n"
    if not fixed.strip() or fixed == current:
        return None, "FIX_NOT_GENERATED"
    return fixed, "local_model"


def run(master, text: str, session: dict | None = None) -> dict:
    raws = named_paths(text)
    if not raws:
        return _refuse("PATH_INVALID", "")
    raw = raws[0]
    target, err = resolve(raw)
    if err:
        return _refuse(err, raw)
    rel = target.relative_to(ROOT).as_posix()
    if not write_allowed(rel):
        return _refuse("PATH_NOT_WRITABLE", raw)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    original = target.read_bytes()
    hash_before = hashlib.sha256(original).hexdigest()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"{stamp}_{target.name}.bak"
    shutil.copy2(target, backup)
    if _sha(backup) != hash_before:
        return {"success": False, "intent": "repair", "status": "BLOCKED", "error": "BACKUP_FAILED",
                "reply": "Backup konnte nicht verifiziert werden; nichts geaendert.", "data": {}}
    test = _find_test(target)
    test_src = test.read_text(encoding="utf-8") if test else ""
    before = _verify(target, test)
    rounds, method, error = [], None, None
    current = original.decode("utf-8")
    final = before
    if not before["ok"]:
        for _ in range(MAX_ROUNDS):
            out = "\n".join(s["out"] for s in final["steps"])
            fixed, method = _llm_fix(master, text, target, current, test_src, out)
            if fixed is None:
                error = method
                break
            target.write_text(fixed, encoding="utf-8", newline="\n")
            final = _verify(target, test)
            rounds.append({"method": method, "ok": final["ok"], "steps": final["steps"]})
            current = fixed
            if final["ok"]:
                break
    after_text = target.read_text(encoding="utf-8")
    diff = "".join(difflib.unified_diff(original.decode("utf-8").splitlines(True), after_text.splitlines(True),
                                        f"a/{rel}", f"b/{rel}"))
    rolled_back, restored_ok = False, None
    if not final["ok"]:
        shutil.copy2(backup, target)
        rolled_back = True
        restored_ok = _sha(target) == hash_before
    hash_after = _sha(target)
    ok = final["ok"] and not rolled_back
    status = "VERIFIED" if ok else ("ROLLED_BACK" if rolled_back else "BLOCKED")
    if before["ok"]:
        status, ok = "NO_CHANGE_NEEDED", True
    # Provenienz (Auftrag 003): Modellausgabe gilt NIE automatisch als verifiziert.
    model_used = bool(rounds)
    last_rc = (final["steps"][-1]["rc"] if final["steps"] else None)
    if rolled_back:
        vstatus = "rolled_back"
    elif final["ok"] and last_rc == 0:
        vstatus = "test_verified"
    else:
        vstatus = "failed"
    model_provenance = "model_response_unverified" if model_used else None
    if not model_used:
        provenance = "no_model_patch"
    elif vstatus == "test_verified":
        provenance = "model_patch_test_verified"
    elif vstatus == "rolled_back":
        provenance = "model_patch_rolled_back"
    else:
        provenance = "model_patch_unverified"
    receipt = {
        "schema": SCHEMA, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "order": text[:2000], "path": rel,
        "backup": backup.relative_to(ROOT).as_posix(), "hash_before": hash_before, "hash_after": hash_after,
        "diff": diff, "test": (test.relative_to(ROOT).as_posix() if test else None),
        "check_before": before, "rounds": rounds, "final_check": final, "status": status,
        "rolled_back": rolled_back, "restore_hash_matches_original": restored_ok, "error": error,
        "model_provenance": model_provenance, "provenance": provenance,
    }
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    rpath = RECEIPT_DIR / f"{stamp}_{target.stem}.json"
    rpath.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    rrel = rpath.relative_to(ROOT).as_posix()
    verification = {"status": vstatus, "evidence": rrel, "hash_before": hash_before, "hash_after": hash_after,
                    "test_cmd": (final["steps"][-1]["cmd"] if final["steps"] else None), "test_rc": last_rc}
    receipt["verification"] = verification
    rpath.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    last = (final["steps"][-1] if final["steps"] else {})
    if status == "VERIFIED":
        reply = (f"Reparatur von {rel} geprueft abgeschlossen ({len(rounds)} Runde(n), {method}). "
                 f"Test: `{last.get('cmd')}` rc={last.get('rc')}. SHA-256 vorher {hash_before[:12]}..., "
                 f"nachher {hash_after[:12]}.... Nachweis: {rrel}")
    elif status == "NO_CHANGE_NEEDED":
        reply = f"{rel} besteht die Pruefung bereits (`{last.get('cmd')}` rc=0); nichts geaendert. Nachweis: {rrel}"
    elif rolled_back:
        reply = (f"Reparatur von {rel} fehlgeschlagen ({error or 'Test weiter rot'}). Automatisch zurueckgesetzt, "
                 f"Hash identisch mit Original: {restored_ok}. Nachweis: {rrel}")
    else:
        reply = f"Reparatur von {rel} blockiert: {error}. Nachweis: {rrel}"
    return {"success": ok, "intent": "repair", "status": status, "reply": reply,
            "error": None if ok else ("REPAIR_" + (error or status)),
            "data": {"execution_status": status, "receipt": rrel, "hash_before": hash_before,
                     "hash_after": hash_after, "rolled_back": rolled_back, "task_ids": [],
                     "model_provenance": model_provenance, "provenance": provenance,
                     "verification": verification}}
