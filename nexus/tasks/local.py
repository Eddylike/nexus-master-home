"""Deterministic preflight plans for local AI/device work, without a shell.

Registration is not availability. These plans inspect the requested host through
the existing Bridge; they never turn an integration goal into a deployment grant.
"""
from __future__ import annotations

import re

from nexus.bridge.permissions import TASK_PERMISSIONS, PermissionLevel

LOCAL_SIGNAL = re.compile(r"\b(?:qwen[\w.:-]*|ollama|adb|termux)\b", re.I)
MOBILE_SIGNAL = re.compile(r"\b(?:adb|termux|redmi|handy|smartphone|android|mobile)\b", re.I)
WORK_VERB = re.compile(
    r"\b(?:verbinde|integrier\w*|analysier\w*|untersuch\w*|prüf\w*|pruef\w*|"
    r"beheb\w*|reparier\w*|entwick\w*|implementier\w*|bau\w*|erstell\w*|"
    r"connect|integrate|inspect|check|implement)\b", re.I)
INTEGRATION = re.compile(r"\b(?:verbinde|integrier\w*|anbind\w*|entwick\w*|implement\w*|bau\w*|reparier\w*)\b", re.I)


def is_local_work(text: str) -> bool:
    value = str(text or "")
    if not LOCAL_SIGNAL.search(value) or not WORK_VERB.search(value):
        return False
    # Explicit file/software artifacts retain their existing development contract.
    if (re.search(r"\b(?:inhalt|content)\s*:", value, re.I)
            or re.search(r"\b[\w-]+\.(?:py|md|txt|json|html|js|css)\b|\bpython[-\s]+(?:skript|script|programm)\b", value, re.I)
            or re.search(r"\b(?:erstell|schreib|speicher|entwick|bau)\w*\b[^.!?\n]{0,100}\b(?:datei|bericht|markdown|textdatei|webseite|website|browserprojekt|spiel|game|programm|html|css|javascript)\b", value, re.I)):
        return False
    if re.match(r"\s*(?:wie|was|warum|wieso|erklär\w*|erklaer\w*)\b", value, re.I):
        return False
    # Do not treat a negated inspection/integration as execution authorization.
    verbs = list(WORK_VERB.finditer(value))
    return any(not re.search(r"\b(?:nicht|nie|niemals|kein\w*)\b",
                            re.split(r"[.!?\n;]", value[max(0, m.start()-45):m.start()])[-1], re.I)
               and not re.search(r"\b(?:nicht|nie|niemals)\b",
                                 re.split(r"[!?\n;,]|\.(?:\s|$)", value[m.end():])[0], re.I)
               for m in verbs)


def plan_local_work(text: str) -> dict:
    """Use only fixed diagnostic actions; never accept model-supplied params."""
    endpoints = re.findall(r"\b(?:https?://)?(?:\d{1,3}\.){3}\d{1,3}:\d+\b|https?://[\w.-]+(?::\d+)?", text, re.I)
    if any(e.lower() not in ("127.0.0.1:11434", "http://127.0.0.1:11434", "http://localhost:11434") for e in endpoints):
        raise ValueError("LOCAL_AI_ENDPOINT_NOT_SUPPORTED:registered probes support only http://127.0.0.1:11434 on the requested host")
    mobile = bool(MOBILE_SIGNAL.search(text))
    device_match = re.search(r"\b(?:adb[-\s]*(?:gerät|geraet|device)|device)\s*[:=]?\s*[`\"']?([\w.:-]+)", text, re.I)
    device = device_match.group(1).rstrip(".,;:") if device_match else None
    steps = []
    if mobile:
        steps.append({"action": "android_devices", "params": {}, "title": "ADB-Geräte prüfen", "depends_on": []})
        # Phone AI is not probed through the retired Phase-A control service.
    else:
        steps.append({"action": "llm_status", "params": {}, "title": "Lokale Ollama-Modelle auf dem PC prüfen", "depends_on": []})
    for step in steps:
        action = step["action"]
        if action not in TASK_PERMISSIONS:
            raise ValueError("LOCAL_ACTION_NOT_REGISTERED:" + action)
        if TASK_PERMISSIONS[action] == PermissionLevel.PRIVILEGED:
            raise ValueError("LOCAL_ACTION_REQUIRES_APPROVAL:" + action)
    return {"title": "Lokaler KI-/ADB-Auftrag", "steps": steps, "mobile": mobile,
            "device": device, "integration": bool(INTEGRATION.search(text)), "requires_web": False,
            "request": text}


def capability_gaps(plan: dict, records: list[dict]) -> list[dict]:
    """Infer prerequisites only from receipts from the requested host."""
    def gap(code, detail):
        return [{"code": code, "detail": detail}]

    if plan["mobile"]:
        adb = next((r.get("result") or {} for r in records if r.get("task") == "android_devices"), {})
        devices = dict(re.findall(r"(?m)^([^\s]+)\s+(device|unauthorized|offline)(?:\s|$)", str(adb.get("stdout") or "")))
        if not plan["device"]:
            return gap("ADB_TARGET_REQUIRED", "Der lokale Auftrag benötigt eine explizite ADB-Geräte-ID; kein Gerät wurde automatisch ausgewählt.")
        state = devices.get(plan["device"])
        if state != "device":
            return gap("ADB_TARGET_" + (state.upper() if state else "NOT_CONNECTED"),
                       "ADB-Ziel " + plan["device"] + ": " + (state or "nicht verbunden") + ".")
        return gap("PHONE_LOCAL_AI_UNVERIFIED",
                   "ADB-Gerät ist erreichbar. Ein unabhängiger, aktiver Nachweis der lokalen Handy-KI fehlt, solange kein frischer App-Heartbeat vorliegt.")
    else:
        status = next((r.get("result") or {} for r in records if r.get("task") == "llm_status"), {})
        ai = status.get("ollama") or {}
        if not ai.get("reachable"):
            return gap("PC_OLLAMA_UNREACHABLE", "Ollama auf dem PC unter 127.0.0.1:11434 ist nicht erreichbar.")
    models = ai.get("models") or []
    names = [str(m.get("name") or "") if isinstance(m, dict) else str(m) for m in models]
    request = plan.get("request", "").lower()
    requested_models = list(re.finditer(r"\bqwen(\d+(?:\.\d+)?)(?:[-: /]+(\d+(?:\.\d+)?b)\b)?", request))
    requested = next((m for m in requested_models if m.group(2)), requested_models[0] if requested_models else None)
    family = "qwen" + requested.group(1) if requested else ""
    size = requested.group(2) if requested else None
    matches = [n for n in names if re.search(r"(?:^|/)" + re.escape(family) + r"(?=[:\-/]|$)", n.lower())] if requested else []
    if size:
        matches = [n for n in matches if re.search(r"(?<![\w.])" + re.escape(size) + r"(?![\w.])", n.lower())]
    if requested and not matches:
        return gap("QWEN3_MODEL_NOT_FOUND", "Die Modellliste des angefragten Hosts enthält das angeforderte " + family +
                   ("-" + size if size else "") + "-Modell nicht: " + ", ".join(names) + ".")
    if plan["integration"]:
        return gap("LOCAL_INTEGRATION_CONTRACT_REQUIRED",
                   "Die Vorprüfung ist belegt. Für die Integration fehlt ein validierter Änderungsplan mit konkreten Quelldateien, Backups und Offline-Abnahmetest. "
                   "Hermes-Datei-/Prüfwerkzeuge, python_run_project_script und ADB-Transfers bleiben an ihre vorhandenen Verträge gebunden.")
    if re.search(r"\b(?:offlinebetrieb|chat|gespräch\w*|gespraech\w*|antwort\w*|inferenz|generate|generation)\b", request):
        return gap("LOCAL_FUNCTIONAL_TEST_REQUIRED",
                   "Die Statusabfrage belegt Erreichbarkeit und Modellliste. Der ausdrücklich verlangte Chat-/Inferenz-/Offline-Test "
                   "benötigt einen eigenen registrierten Prüfvertrag und wurde nicht durch die Statusabfrage nachgewiesen.")
    return []
