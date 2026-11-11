"""NEXUS MissionEngine: Planung, parallele Ausfuehrung, Freigaben, Verifikation.

Ablauf: Ziel -> Plan (Analyse) -> Dispatch -> Ausfuehrung -> Verifikation ->
eine Reparaturrunde -> ehrliche Gesamtantwort. Jeder Schritt laeuft ueber
einen echten Agenten-Adapter; parallele Ausfuehrung nur ohne Abhaengigkeiten.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Callable

from nexus.common.live_events import emit
from nexus.mission import agents as agent_runtime
from nexus.mission import planner as planner_mod
from nexus.mission.store import MissionStore, now
from nexus.common.paths import WORKSPACE


def _new_id() -> str:
    return "MS-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


class MissionEngine:
    def __init__(self, store: MissionStore | None = None,
                 runner: Callable[[str, dict, dict, MissionStore], dict] | None = None,
                 planner: Callable[[str, str], dict] | None = None,
                 max_parallel: int = 3, step_timeout: float = 240.0) -> None:
        self.store = store or MissionStore()
        self.runner = runner or agent_runtime.run_agent
        self.planner = planner or planner_mod.plan_mission
        self.max_parallel = max(1, min(int(max_parallel), 4))
        self.step_timeout = max(0.1, float(step_timeout))
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def _lock(self, mission_id: str) -> threading.Lock:
        with self._locks_guard:
            if mission_id not in self._locks:
                self._locks[mission_id] = threading.Lock()
            return self._locks[mission_id]

    def create(self, goal: str, context: str = "", meta: dict | None = None) -> dict:
        text = str(goal or "").strip()
        if not text:
            raise ValueError("GOAL_REQUIRED")
        mission = {
            "id": _new_id(), "goal": text, "context": str(context or ""),
            "status": "queued", "stage": "received", "created": now(), "updated": now(),
            "steps": [], "analysis": "", "planner": None, "planner_error": None,
            "verification": None, "final": "", "error": None, "correction_round": 0,
            "approvals": {}, "trace": [], "meta": meta or {},
        }
        self.store.save(mission)
        self.store.event(mission, "NEXUS", "created", f"Mission angelegt: {text[:200]}")
        self.store.save(mission)
        return mission

    def submit(self, goal: str, context: str = "", background: bool = True,
               meta: dict | None = None) -> dict:
        mission = self.create(goal, context, meta)
        if background:
            thread = threading.Thread(target=self._run_guarded, args=(mission["id"],), daemon=True,
                                      name=f"mission-{mission['id'][-6:]}")
            with self._locks_guard:
                self._threads[mission["id"]] = thread
            thread.start()
            return self.store.get(mission["id"]) or mission
        self.run(mission["id"])
        return self.store.get(mission["id"]) or mission

    def _run_guarded(self, mission_id: str) -> None:
        try:
            self.run(mission_id)
        finally:
            with self._locks_guard:
                self._threads.pop(mission_id, None)

    def get(self, mission_id: str) -> dict | None:
        return self.store.get(mission_id)

    def run(self, mission_id: str) -> dict:
        lock = self._lock(mission_id)
        if not lock.acquire(blocking=False):
            return self.store.get(mission_id) or {}
        try:
            mission = self.store.get(mission_id)
            if not mission:
                raise KeyError("MISSION_NOT_FOUND")
            return self._run(mission)
        finally:
            lock.release()

    def _run(self, mission: dict) -> dict:
        mission["status"] = "running"
        mission["stage"] = "analyze"
        self.store.save(mission)

        if not mission.get("steps"):
            plan = self.planner(mission["goal"], mission.get("context", ""))
            mission["analysis"] = plan.get("analysis") or ""
            mission["planner"] = plan.get("planner") or "unknown"
            mission["planner_error"] = plan.get("error")
            mission["steps"] = plan.get("steps") or []
            for index, step in enumerate(mission["steps"]):
                step["id"] = index
                step.setdefault("status", "pending")
                step.setdefault("depends_on", [])
                step.setdefault("critical", planner_mod.detect_critical(step.get("instruction", "")))
                step.setdefault("attempts", 0)
            self.store.event(mission, "NEXUS", "planned",
                             f"Plan mit {len(mission['steps'])} Schritten ({mission['planner']})",
                             {"error": mission.get("planner_error")})
            emit("NEXUS", "mission", f"Plan erstellt: {len(mission['steps'])} Schritte")
        mission["stage"] = "dispatch"
        self.store.save(mission)

        for _ in range(40):
            outcome = self._execute(mission)
            if outcome == "approval":
                self.store.save(mission)
                return mission
            failed = [s for s in mission["steps"] if s.get("status") == "failed"]
            if failed and int(mission.get("correction_round") or 0) < 1:
                mission["correction_round"] = int(mission.get("correction_round") or 0) + 1
                mission["stage"] = "repair"
                for step in failed:
                    step["instruction"] = (str(step.get("instruction") or "") +
                                           "\nDiagnose: Der vorherige Versuch ist fehlgeschlagen: " +
                                           str((step.get("result") or {}).get("error") or "unbekannt")[:300])
                    step["status"] = "pending"
                    step["recovered"] = True
                for step in mission["steps"]:
                    if step.get("status") == "blocked":
                        step["status"] = "pending"
                self.store.event(mission, "NEXUS", "repair",
                                 f"Reparaturrunde fuer {len(failed)} Schritt(e)")
                self.store.save(mission)
                continue
            break

        self._verify(mission)
        self._finalize(mission)
        self.store.save(mission)
        emit("NEXUS", "mission", f"Mission {mission['status']}: {mission['goal'][:80]}")
        return mission

    @staticmethod
    def _ready(mission: dict) -> tuple[list[dict], dict[int, dict]]:
        steps = mission["steps"]
        by_id = {int(s["id"]): s for s in steps}
        ready: list[dict] = []
        for step in steps:
            if step.get("status") != "pending":
                continue
            deps = [int(d) for d in step.get("depends_on") or []]
            states = [by_id[d].get("status") for d in deps if d in by_id]
            if any(state in ("pending", "awaiting_approval") for state in states):
                continue
            failed_dep = any(state != "succeeded" for state in states)
            if failed_dep:
                allow_partial = (step.get("action") in ("verify", "synthesize")
                                 and int(mission.get("correction_round") or 0) >= 1)
                if not allow_partial:
                    step["status"] = "blocked"
                    continue
            ready.append(step)
        return ready, by_id

    def _execute(self, mission: dict) -> str:
        for _ in range(len(mission["steps"]) * 4 + 10):
            if any(s.get("status") == "awaiting_approval" for s in mission["steps"]):
                mission["status"] = "awaiting_approval"
                mission["stage"] = "approval"
                return "approval"
            ready, _ = self._ready(mission)
            if not ready:
                break
            approvals = mission.get("approvals") or {}
            critical = [s for s in ready if s.get("critical")
                        and not (approvals.get(str(s["id"])) or {}).get("approved")]
            if critical:
                for step in critical:
                    step["status"] = "awaiting_approval"
                    self.store.event(mission, "Aether", "approval_required",
                                     f"Freigabe benoetigt: {step.get('title')}",
                                     {"step_id": step["id"], "instruction": step.get("instruction", "")[:500]})
                mission["status"] = "awaiting_approval"
                mission["stage"] = "approval"
                emit("Aether", "mission", f"Freigabe offen: {critical[0].get('title')}")
                return "approval"

            results = self._run_parallel(mission, ready)
            for step, result in results:
                self._apply(mission, step, result)
            self.store.save(mission)
        return "done"

    def _run_parallel(self, mission: dict, ready: list[dict]) -> list[tuple[dict, dict]]:
        if not ready:
            return []
        workers = min(self.max_parallel, len(ready))
        out: list[tuple[dict, dict]] = []
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="mission-step")
        try:
            futures = {pool.submit(self._call, step, mission): step for step in ready}
            done, not_done = wait(list(futures), timeout=self.step_timeout)
            for future in done:
                step = futures[future]
                try:
                    out.append((step, future.result()))
                except Exception as exc:
                    out.append((step, {"success": False, "output": "", "error": f"{type(exc).__name__}: {exc}"[:400],
                                       "evidence": "runner_exception", "agent": step.get("agent")}))
            for future in not_done:
                step = futures[future]
                future.cancel()
                out.append((step, {"success": False, "output": "",
                                   "error": f"STEP_TIMEOUT ({int(self.step_timeout)}s)",
                                   "evidence": "timeout", "agent": step.get("agent")}))
        finally:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=False)
        return out

    def _call(self, step: dict, mission: dict) -> dict:
        step["status"] = "running"
        step["started"] = now()
        step["attempts"] = int(step.get("attempts") or 0) + 1
        return self.runner(step.get("agent"), step, mission, self.store)

    def _apply(self, mission: dict, step: dict, result: dict) -> None:
        result = result if isinstance(result, dict) else {"success": False, "output": "", "error": "BAD_RESULT"}
        output = str(result.get("output") or "").strip()
        success = bool(result.get("success")) and bool(output)
        if result.get("success") and not output:
            result["error"] = result.get("error") or "EMPTY_OUTPUT"
        step["finished"] = now()
        step["status"] = "succeeded" if success else "failed"
        step["result"] = {"success": success, "output": output, "evidence": str(result.get("evidence") or "")[:2000],
                          "error": None if success else str(result.get("error") or "FAILED")[:400],
                          "agent": result.get("agent") or step.get("agent"),
                          "latency_ms": result.get("latency_ms"),
                          "tool_calls": result.get("tool_calls") or [],
                          "execution_status": result.get("execution_status")}
        if output:
            try:
                self.store.remember(mission["id"], str(step.get("agent")), output,
                                    evidence=str(result.get("evidence") or ""))
            except Exception:
                pass
        self.store.event(mission, str(step.get("agent")), "step_" + step["status"],
                         str(step.get("title"))[:200],
                         {"step_id": step["id"], "error": step["result"]["error"]})
        emit(str(step.get("agent")), "mission",
             f"{step.get('title')}: {step['status']}")

    def _verify(self, mission: dict) -> None:
        mission["stage"] = "verify"
        steps = mission["steps"]
        checks = []
        failed = [s for s in steps if s.get("status") != "succeeded"]
        checks.append({"check": "alle_schritte_erfolgreich", "passed": not failed,
                       "detail": [f"{s['id']}:{s.get('status')}" for s in failed]})
        empty = [s["id"] for s in steps if s.get("status") == "succeeded" and
                 not str((s.get("result") or {}).get("output") or "").strip()]
        checks.append({"check": "ausgaben_nicht_leer", "passed": not empty, "detail": empty})
        tech = [s for s in steps if s.get("action") == "execute_technical"]
        unproven = [s["id"] for s in tech if s.get("status") == "succeeded" and
                    not (s.get("result") or {}).get("tool_calls")]
        checks.append({"check": "technik_durch_werkzeuge_belegt", "passed": not unproven, "detail": unproven})
        synth = [s for s in steps if s.get("action") == "synthesize" and s.get("status") == "succeeded"]
        checks.append({"check": "gesamtantwort_vorhanden", "passed": bool(synth), "detail": [s["id"] for s in synth]})
        mission["verification"] = {
            "passed": all(c["passed"] for c in checks), "checks": checks,
            "correction_rounds": int(mission.get("correction_round") or 0), "checked": now(),
        }

    def _finalize(self, mission: dict) -> None:
        steps = mission["steps"]
        synth = [s for s in steps if s.get("action") == "synthesize" and s.get("status") == "succeeded"]
        if synth:
            final = str((synth[-1].get("result") or {}).get("output") or "").strip()
        else:
            final = self._fallback_final(mission)
        failed = [s for s in steps if s.get("status") != "succeeded"]
        if failed:
            listing = "; ".join(f"{s.get('title')} [{s.get('status')}]" for s in failed)
            final += f"\n\nEhrlicher Hinweis: Nicht abgeschlossen: {listing}."
        mission["final"] = final.strip()
        good = [s for s in steps if s.get("status") == "succeeded" and s.get("action") != "synthesize"]
        if not failed and (mission.get("verification") or {}).get("passed"):
            mission["status"] = "completed"
        elif good or synth:
            mission["status"] = "partial"
        else:
            mission["status"] = "failed"
        mission["stage"] = "done"

    @staticmethod
    def _fallback_final(mission: dict) -> str:
        lines = [f"Mission: {mission.get('goal')}"]
        for step in mission.get("steps") or []:
            result = step.get("result") or {}
            if step.get("status") == "succeeded" and result.get("output"):
                lines.append(f"\n## {step.get('title')} ({step.get('agent')})\n{result['output']}")
        if len(lines) == 1:
            lines.append("\nKeine Teilaufgabe wurde erfolgreich abgeschlossen.")
        return "\n".join(lines)

    def approve(self, mission_id: str, step_id: int | str | None = None, approved: bool = True,
                by: str = "Eddie", background: bool = True) -> dict:
        mission = self.store.get(mission_id)
        if not mission:
            raise KeyError("MISSION_NOT_FOUND")
        steps = mission.get("steps") or []
        targets = [s for s in steps if s.get("status") == "awaiting_approval"
                   and (step_id is None or int(s.get("id")) == int(step_id))]
        if not targets:
            raise ValueError("NO_STEP_AWAITING_APPROVAL")
        for step in targets:
            mission.setdefault("approvals", {})[str(step["id"])] = {
                "approved": bool(approved), "by": str(by or "Eddie"), "ts": now(),
                "title": step.get("title"), "instruction": str(step.get("instruction") or "")[:500],
            }
            step["status"] = "pending" if approved else "rejected"
            self.store.event(mission, str(by or "Eddie"), "approved" if approved else "rejected",
                             f"{step.get('title')}: {'freigegeben' if approved else 'abgelehnt'}",
                             {"step_id": step["id"]})
        mission["status"] = "running"
        mission["stage"] = "dispatch"
        self.store.save(mission)
        if background:
            thread = threading.Thread(target=self._run_guarded, args=(mission_id,), daemon=True)
            with self._locks_guard:
                self._threads[mission_id] = thread
            thread.start()
            return self.store.get(mission_id) or mission
        self.run(mission_id)
        return self.store.get(mission_id) or mission

    def status(self) -> dict:
        missions = self.store.list(30)
        active = [m for m in missions if m.get("status") in ("queued", "running", "awaiting_approval")]
        with self._locks_guard:
            threads = len(self._threads)
        return {"success": True, "service": "mission", "active": active, "threads": threads,
                "agents": agent_runtime.health(), "missions": missions,
                "store": str(self.store.base)}


_ENGINE: MissionEngine | None = None
_ENGINE_LOCK = threading.Lock()


def get_engine() -> MissionEngine:
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            base = os.environ.get("NEXUS_MISSION_DIR")
            store = MissionStore(WORKSPACE / "missions" if not base else Path(base))
            timeout = float(os.environ.get("NEXUS_MISSION_STEP_TIMEOUT", "240") or 240)
            _ENGINE = MissionEngine(store=store, step_timeout=timeout)
        return _ENGINE
