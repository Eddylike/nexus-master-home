"""Phase 1/2 Abnahme: Statusmaschine, Memory, Embeddings, Fehler-Lessons."""
from __future__ import annotations

import pytest

from nexus.bauprotokoll.memory import Memory
from nexus.bauprotokoll.states import TaskState, apply_validation, transition


def mem(tmp_path):
    return Memory(db_path=tmp_path / "memory.db", use_ollama=False)


def test_state_machine_happy_path():
    s = TaskState.PLANNED
    s = transition(s, "start")
    assert s == TaskState.IN_PROGRESS
    s = transition(s, "review")
    assert s == TaskState.REVIEW
    assert apply_validation(s, "PASS", actor="validator") == TaskState.DONE


def test_state_machine_fail_returns_to_progress():
    assert apply_validation(TaskState.REVIEW, "FAIL", actor="validator") == TaskState.IN_PROGRESS


def test_only_validator_can_finish():
    with pytest.raises(PermissionError):
        apply_validation(TaskState.REVIEW, "PASS", actor="coder")


def test_invalid_transition_raises():
    with pytest.raises(ValueError):
        transition(TaskState.DONE, "start")


def test_memory_decision_roundtrip_and_search(tmp_path):
    m = mem(tmp_path)
    m.add_decision("SQLite statt Chroma verwenden", "keine Zusatzdienste, portabel", projekt="NEXUS")
    m.add_decision("Chroma fuer Vektoren", "bessere Suche", projekt="Anderes")
    hits = m.search("decisions", "SQLite", top_k=1)
    assert hits and hits[0]["entscheidung"].startswith("SQLite")


def test_memory_context_has_all_sections(tmp_path):
    m = mem(tmp_path)
    m.add_decision("Validator zuerst", "Sicherheit")
    m.add_result("T-1", "FAIL", "syntax: kaputt")
    m.add_lesson("Immer py_compile laufen lassen", tags="python")
    ctx = m.context_for("Validator Syntax")
    assert set(ctx) == {"decisions", "results", "lessons", "project_state"}
    assert ctx["lessons"]


def test_persistence_across_instances(tmp_path):
    db = tmp_path / "memory.db"
    Memory(db_path=db, use_ollama=False).add_lesson("bleibt erhalten")
    again = Memory(db_path=db, use_ollama=False)
    assert any("bleibt" in l["text"] for l in again.search("lessons", "erhalten"))


def test_failure_twice_creates_lesson_once(tmp_path):
    m = mem(tmp_path)
    report = {
        "task_id": "T-9", "status": "FAIL",
        "steps": [{"step": "syntax", "result": "FAIL", "detail": "Syntaxfehler: x.py"}],
    }
    first = m.record_validation(report)
    assert first["lesson_id"] is None
    second = m.record_validation(report)
    assert second["lesson_id"] is not None
    third = m.record_validation(report)
    assert third["lesson_id"] == second["lesson_id"]
    lessons = m.search("lessons", "Syntaxfehler x.py", top_k=10)
    assert sum(1 for l in lessons if "Wiederkehrender Fehler" in l["text"]) == 1


def test_only_pass_fail_is_stored(tmp_path):
    m = mem(tmp_path)
    with pytest.raises(ValueError):
        m.record_validation({"task_id": "T", "status": "MAYBE"})


def test_project_state_upsert(tmp_path):
    m = mem(tmp_path)
    m.set_project_state("NEXUS", "Phase 2 laeuft", "Memory-API")
    m.set_project_state("NEXUS", "Phase 2 fertig", "")
    state = m.get_project_state("NEXUS")
    assert state["stand"] == "Phase 2 fertig"
    assert m.stats()["project_state"] == 1
