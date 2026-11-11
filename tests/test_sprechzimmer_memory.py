"""SZ-01: Gedaechtnis getrennt pro Agent, fremder Agent darf nicht lesen."""
import pytest


def _m():
    from nexus.sprechzimmer import memory
    return memory


def test_append_and_read_own(tmp_path):
    m = _m()
    m.append_memory("Hermes", {"text": "Ich mache mir Sorgen"}, tmp_path)
    m.append_memory("Hermes", {"text": "zweiter Eintrag"}, tmp_path)
    got = m.read_memory("Hermes", "Hermes", tmp_path)
    assert [e["text"] for e in got] == ["Ich mache mir Sorgen", "zweiter Eintrag"]


def test_separate_files_per_agent(tmp_path):
    m = _m()
    m.append_memory("Hermes", {"text": "h"}, tmp_path)
    m.append_memory("NEXUS GPT", {"text": "g"}, tmp_path)
    assert m.memory_path("Hermes", tmp_path) != m.memory_path("NEXUS GPT", tmp_path)
    assert m.memory_path("Hermes", tmp_path).exists()
    assert " " not in m.memory_path("NEXUS GPT", tmp_path).name
    assert [e["text"] for e in m.read_memory("NEXUS GPT", "NEXUS GPT", tmp_path)] == ["g"]


def test_foreign_agent_cannot_read(tmp_path):
    m = _m()
    m.append_memory("Aether", {"text": "vertraulich"}, tmp_path)
    with pytest.raises(PermissionError):
        m.read_memory("Aether", "Hermes", tmp_path)


def test_missing_memory_is_empty(tmp_path):
    assert _m().read_memory("NEXUS", "NEXUS", tmp_path) == []


def test_stored_under_base_dir(tmp_path):
    m = _m()
    m.append_memory("NEXUS", {"text": "x"}, tmp_path)
    p = m.memory_path("NEXUS", tmp_path)
    assert tmp_path in p.parents and p.suffix == ".jsonl"