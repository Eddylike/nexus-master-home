"""Local work dispatcher backed by persisted Orchestrator and Bridge receipts."""
from __future__ import annotations

from dataclasses import asdict
import hashlib

from nexus.common.atomic import atomic_write_json
from nexus.common.paths import ROOT
from nexus.tasks.local import capability_gaps, plan_local_work
from nexus.tasks.orchestrator import Orchestrator


def run(master, text: str, session: dict) -> dict:
    from nexus.master.evidence import persisted_task

    try:
        plan = plan_local_work(text)
    except ValueError as exc:
        return _response(None, {}, [], [{"code": str(exc).split(":", 1)[0], "detail": str(exc)}])
    plan["request"] = text
    orchestrator = Orchestrator()
    previous = session.get("local_order") or {}
    if previous.get("task") == text:
        order = orchestrator.get(previous.get("order_id", ""))
        if order is None:
            return _response(None, plan, [], [{"code": "LOCAL_ORDER_MISSING", "detail": "Gespeicherter lokaler Auftragsbeleg fehlt; keine automatische Wiederholung."}])
    else:
        order = orchestrator.create(plan["title"], plan["steps"])
        session.pop("hermes_order", None)
        session["local_order"] = {"task": text, "order_id": order.id}
        persist = getattr(master, "_persist_chat_order", None)
        if persist:
            persist()
    if (len(order.steps) != len(plan["steps"])
            or any(s.action != p["action"] or s.params != p["params"] or s.depends_on != p["depends_on"] or s.repair
                   for s, p in zip(order.steps, plan["steps"]))):
        return _response(order, plan, [], [{"code": "LOCAL_PLAN_MISMATCH", "detail": "Gespeicherte Schritte entsprechen nicht dem begrenzten lokalen Plan; keine Wiederholung."}])
    # Interrupted or failed steps require reconciliation, not a blind retry.
    order = orchestrator.resume(order.id)
    records = []
    gaps = []
    for step in order.steps:
        if not step.task_id:
            continue
        record = persisted_task(step.task_id)
        if (not record or record.get("task") != step.action or record.get("params") != step.params
                or record.get("status") != step.status):
            gaps = [{"code": "LOCAL_RECEIPT_MISMATCH", "detail": "Werkzeugbeleg fehlt oder passt nicht zum lokalen Plan: " + step.action}]
            break
        records.append(record)
    if not gaps and order.status == "succeeded" and (len(records) != len(order.steps) or any(r.get("status") != "succeeded" for r in records)):
        gaps = [{"code": "LOCAL_RECEIPTS_INCOMPLETE", "detail": "Nicht alle geplanten Schritte haben einen erfolgreichen gespeicherten Werkzeugbeleg."}]
    if not gaps and order.status != "succeeded":
        failed = next((s for s in order.steps if s.status not in ("pending", "succeeded")), None)
        rec = next((r for r in records if failed and r.get("id") == failed.task_id), {})
        gaps = [{"code": str(rec.get("error_code") or "LOCAL_STEP_FAILED"),
                 "detail": (failed.action + ": " if failed else "") + str(rec.get("error") or order.result)}]
    if not gaps:
        gaps = capability_gaps(plan, records)
    result = _response(order, plan, records, gaps)
    path = ROOT / "workspace" / "state" / ("local-work-" + hashlib.sha256((str(session.get("id")) + text).encode()).hexdigest()[:20] + ".json")
    atomic_write_json(path, {"schema": "nexus.local-work.v1", "request": text, "plan": plan,
                             "order": asdict(order), "response": result})
    return result


def _response(order, plan, records, gaps):
    ids = [r["id"] for r in records]
    status = "PARTIAL" if gaps and records else ("BLOCKED" if gaps else "PASS")
    lines = [status + ": Lokaler KI-/ADB-Auftrag" + ("; Vorprüfung ausgeführt." if records else ".")]
    if order:
        lines.append("Auftrags-ID: " + order.id)
    lines.extend("- " + r["task"] + ": " + r["id"] + " – " + r["status"] for r in records)
    lines.extend("- " + g["code"] + ": " + g["detail"] for g in gaps)
    return {"success": not gaps, "intent": "hermes_work", "status": status,
            "error": gaps[0]["code"] if gaps else None, "reply": "\n".join(lines),
            "data": {"execution_status": status, "order_type": "local_order",
                     "order_id": order.id if order else None, "task_ids": ids,
                     "requires_web": False, "plan": plan, "capability_gaps": gaps,
                     "tools": [{"name": r["task"], "task_id": r["id"], "status": r["status"], "result": r.get("result")} for r in records],
                     "provenance": "persisted_bridge_tasks"}}
