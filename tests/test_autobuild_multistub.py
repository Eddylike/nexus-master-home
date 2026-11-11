"""20261104_multistub: Stub-Erkennung und Prompt-Aufbau im Mehr-Stub-Modus."""
from nexus.autobuild import loop

SRC = '''import json


def a(x):
    raise NotImplementedError("a")


def b():
    raise NotImplementedError


def c():
    return 1
'''


def test_stub_detection():
    assert loop.stub_functions(SRC) == ["a", "b"]
    assert loop.stub_functions("def f():\n    return 2\n") == []
    assert loop.stub_functions("def (:") == []


def test_prompt_contains_file_test_and_all_stubs():
    item = {"id": "X", "title": "T", "goal": "G", "file": "nexus/x.py"}
    p = loop.multi_stub_prompt(item, SRC, ["a", "b"], "def test_a(): pass")
    assert "a, b" in p and "def c():" in p and "def test_a(): pass" in p and "```python" in p


def test_plan_multi_stub_all_edits(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "ROOT", tmp_path)
    (tmp_path / "nexus").mkdir()
    (tmp_path / "nexus" / "x.py").write_text(SRC, encoding="utf-8")
    (tmp_path / "t.py").write_text("def test_a(): pass", encoding="utf-8")
    seen = []

    def ask(prompt):
        seen.append(prompt)
        if len(seen) == 1:
            return "```python\ndef a(x):\n    return x\n```"
        return "```python\ndef a(x):\n    return x\n\ndef b():\n    return 0\n```\nBegruendung: ok"
    item = {"id": "X", "title": "T", "goal": "G", "file": "nexus/x.py", "anchor": "def a(", "regression_test": "t.py"}
    res = loop.plan_function(item, ask=ask)
    assert res["multi_stub"] == ["a", "b"]
    assert {e["function"] for e in res["result"]["edits"]} == {"a", "b"}
    assert "test_a" in seen[0] and "STUBS_MISSING:b" in seen[1]


def test_single_stub_keeps_old_path(tmp_path, monkeypatch):
    monkeypatch.setattr(loop, "ROOT", tmp_path)
    (tmp_path / "y.py").write_text("def a():\n    raise NotImplementedError\n", encoding="utf-8")
    res = loop.plan_function({"id": "Y", "title": "T", "file": "y.py", "anchor": "def a("},
                             ask=lambda p: "```python\ndef a():\n    return 1\n```")
    assert "multi_stub" not in res and res["fn_mode"] is True

def test_syntax_hint():
    r = '```python\ndef a():\n    mit Daten = []\n```'
    h = loop._syntax_hint(r, 'FN_SYNTAX:invalid syntax (Block-Zeile 2)')
    assert 'mit Daten = []' in h
    assert loop._syntax_hint('x', 'NO_EDITS') == ''

