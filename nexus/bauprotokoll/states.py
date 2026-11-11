"""Task-Statusmaschine: PLANNED -> IN_PROGRESS -> REVIEW -> VALIDATED -> DONE.

Nur der Validator darf VALIDATED/DONE setzen. FAIL fuehrt zurueck zu IN_PROGRESS.
"""
from __future__ import annotations

from enum import Enum

VALIDATOR_ACTOR = "validator"


class TaskState(str, Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEW = "REVIEW"
    VALIDATED = "VALIDATED"
    DONE = "DONE"


TRANSITIONS = {
    TaskState.PLANNED: {"start": TaskState.IN_PROGRESS},
    TaskState.IN_PROGRESS: {"review": TaskState.REVIEW, "fail": TaskState.IN_PROGRESS},
    TaskState.REVIEW: {"pass": TaskState.VALIDATED, "fail": TaskState.IN_PROGRESS},
    TaskState.VALIDATED: {"done": TaskState.DONE},
    TaskState.DONE: {},
}


def transition(current: TaskState | str, event: str) -> TaskState:
    state = TaskState(current)
    target = TRANSITIONS[state].get(event)
    if target is None:
        raise ValueError(f"Ungueltiger Uebergang: {state.value} --{event}-->")
    return target


def apply_validation(current: TaskState | str, status: str, actor: str) -> TaskState:
    """Validator-Ergebnis anwenden. PASS -> VALIDATED -> DONE, FAIL -> IN_PROGRESS."""
    if actor != VALIDATOR_ACTOR:
        raise PermissionError("Nur der Validator darf den Task abschliessen.")
    state = TaskState(current)
    if state in (TaskState.VALIDATED, TaskState.DONE) and status == "PASS":
        return TaskState.DONE
    if status == "PASS":
        if state not in (TaskState.REVIEW, TaskState.IN_PROGRESS, TaskState.PLANNED):
            raise ValueError(f"PASS nicht erlaubt aus {state.value}")
        return TaskState.DONE
    if status == "FAIL":
        return TaskState.IN_PROGRESS
    raise ValueError(f"Unbekannter Validator-Status: {status}")


def state_from_report(report: dict) -> TaskState:
    if report.get("status") == "PASS":
        return TaskState.DONE
    if report.get("status") == "FAIL":
        return TaskState.IN_PROGRESS
    raise ValueError("Report ohne status PASS/FAIL")
