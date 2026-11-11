"""LangGraph-artige, persistierte Pipeline fuer OMEGA.

Die Engine ist bewusst klein: lineare Schritte, Trace, Pause/Resume bei
Freigaben, Zustand in SQLite. Die bestehende `nexus.swarm.graph.Graph` bleibt
fuer parallele/verzweigte Subgraphen verfuegbar.
"""
from __future__ import annotations

import time
from typing import Callable

from nexus.omega import events

StepFn = Callable[[dict], dict]


class Pipeline:
    def __init__(self, run_id: str, steps: list[dict], initial: dict | None = None,
                 save: Callable[[dict], None] | None = None) -> None:
        self.run_id = run_id
        self.steps = steps
        self.state = dict(initial or {})
        self.state.setdefault("trace", [])
        self._save = save

    def _persist(self, status: str | None = None) -> None:
        if not self._save:
            return
        try:
            self._save(self.state, status)
        except TypeError:
            self._save(self.state)  # type: ignore[misc]

    def run(self, start_index: int = 0, *, approval_id: str | None = None) -> dict:
        if approval_id:
            self.state["approval_id"] = approval_id
        self.state["paused"] = False
        self.state["pending_step"] = None
        for index in range(max(0, start_index), len(self.steps)):
            step = self.steps[index]
            name = str(step.get("name") or f"step_{index}")
            fn: StepFn = step["fn"]
            rec = {"step": name, "index": index, "start": time.time(), "status": "running"}
            self.state["trace"].append(rec)
            self.state["active"] = name
            self.state["step_index"] = index
            self._persist()
            try:
                out = fn(self.state)
                if isinstance(out, dict):
                    self.state.update(out)
            except Exception as exc:
                rec["status"] = "error"
                rec["error"] = f"{type(exc).__name__}: {exc}"[:1_000]
                self.state["status"] = "failed"
                self.state["error"] = rec["error"]
                self.state["active"] = None
                self.state["pending_step"] = index
                self._persist("failed")
                return self.state
            rec["sec"] = round(time.time() - rec["start"], 2)
            status = str(self.state.get("status") or "")
            rec["status"] = status or "ok"
            if status == "awaiting_approval":
                self.state["paused"] = True
                self.state["pending_step"] = index
                self.state["active"] = None
                self._persist("awaiting_approval")
                return self.state
            if status in ("failed", "denied", "rate_limited", "invalid") and step.get("critical", True):
                self.state["active"] = None
                self.state["pending_step"] = index
                self._persist("failed")
                return self.state
            self.state["status"] = "running"
        self.state["active"] = None
        if str(self.state.get("status") or "running") == "running":
            self.state["status"] = "succeeded"
        self._persist(self.state.get("status"))
        return self.state


def resume_state(run: dict, approval_id: str | None = None) -> dict:
    state = dict(run.get("state") or {})
    if approval_id:
        state["approval_id"] = approval_id
    return state


def default_save(run_id: str):
    def _save(state: dict, status: str | None = None) -> None:
        events.save_run(run_id, status=status or str(state.get("status") or "running"),
                        state=state)
    return _save
