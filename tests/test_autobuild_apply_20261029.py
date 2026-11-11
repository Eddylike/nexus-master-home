import json
from pathlib import Path

import pytest

from nexus.autobuild import guard, loop

SRC = "def f(a):\n    x = a + 1\n    return x\n"


@pytest.fixture()
def sb(monkeypatch):
    root = Path(guard.ROOT) / "workspace" / "autobuild" / "_test_apply"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "mod.py"
    target.write_text(SRC, encoding="utf-8")
    for k, v in (("DIR", root), ("BACKLOG", root / "backlog.json"), ("CYCLES", root / "cycles.jsonl"),
                 ("PROGRESS", root / "progress.md"), ("STOP_FILE", root / "STOP")):
        monkeypatch.setattr(loop, k, v)
    rel = str(target.relative_to(guard.ROOT)).replace("\\", "/")
    (root / "backlog.json").write_text(json.dumps([{"id": "T1", "title": "t", "status": "offen", "file": rel}]), encoding="utf-8")
    yield root, target, rel
    import shutil
    shutil.rmtree(root, ignore_errors=True)


# (1) whitespace-/einrueckungstolerant, CRLF, Schluessel-Aliase
def test_tolerant_match_reindents(sb):
    _, target, rel = sb
    out = loop._compute_edits([{"path": rel, "search": "x = a  +  1   \r\n  return x", "replace": "y = a + 2\nreturn y"}])
    assert out[target] == "def f(a):\n    y = a + 2\n    return y\n"


def test_tolerant_match_requires_unique(sb):
    _, target, rel = sb
    target.write_text("def f():\n    pass\ndef g():\n    pass\n", encoding="utf-8")
    with pytest.raises(loop.AnchorMissing, match="PATCH_ANCHOR_AMBIGUOUS"):
        loop._compute_edits([{"file": rel, "find": "  pass", "replace": "return 1"}])


# (2) Syntaxpruefung im Speicher mit Ausschnitt + Korrekturrunde statt Rollback
def test_syntax_error_has_excerpt(sb):
    _, target, rel = sb
    with pytest.raises(ValueError) as ei:
        loop._compute_edits([{"file": rel, "find": "    return x", "replace": "      return x"}])
    msg = str(ei.value)
    assert "IndentationError" in msg and "Zeile 3" in msg and "Ausschnitt" in msg and "return x" in msg
    assert target.read_text(encoding="utf-8") == SRC


def _ab(plans, runner, seen):
    it = iter(plans)
    def planner(item):
        seen.append(dict(item))
        return {"mode": "patch", "result": {"edits": next(it)}}
    return loop.Autobuild(tests_runner=runner, planner=planner, hermes=lambda c, t: {"ok": True, "path": "p"}, busy=lambda: None)


def test_syntax_retry_then_adopted(sb):
    _, target, rel = sb
    seen = []
    ab = _ab([[{"file": rel, "find": "    return x", "replace": "      return x"}],
              [{"file": rel, "find": "    return x", "replace": "    return x * 2"}]], lambda a, t: {"ok": True, "summary": "ok"}, seen)
    r = ab.cycle()
    assert r["result"] == "UEBERNOMMEN" and len(seen) == 2
    assert "Ausschnitt" in seen[1]["_korrektur"][1]
    assert "return x * 2" in target.read_text(encoding="utf-8")


# (3) TESTS_FAILED -> 1 Korrekturrunde mit Testfehlern
def test_tests_failed_retry_then_adopted(sb):
    _, target, rel = sb
    calls, seen = [], []
    def runner(a, t):
        calls.append(a)
        return {"ok": len(calls) > 1, "summary": "s", "detail": "FAILED tests/test_x.py::test_y - AssertionError"}
    ab = _ab([[{"file": rel, "find": "x = a + 1", "replace": "x = a + 9"}],
              [{"file": rel, "find": "x = a + 1", "replace": "x = a + 2"}]], runner, seen)
    r = ab.cycle()
    assert r["result"] == "UEBERNOMMEN" and len(seen) == 2
    assert "TESTS_FAILED" in seen[1]["_korrektur"][1] and "test_y" in seen[1]["_korrektur"][1]
    assert "x = a + 2" in target.read_text(encoding="utf-8")


def test_tests_failed_twice_rolls_back(sb):
    _, target, rel = sb
    seen = []
    e = [{"file": rel, "find": "x = a + 1", "replace": "x = a + 9"}]
    r = _ab([e, e], lambda a, t: {"ok": False, "summary": "s", "detail": "FAILED t"}, seen).cycle()
    assert r["result"] == "ROLLBACK" and r["reason"] == "TESTS_FAILED" and len(seen) == 2
    assert target.read_text(encoding="utf-8") == SRC


# (4) ungueltiges Planer-JSON
def test_json_raw_newline_tolerated():
    assert loop._json_from('Antwort: {"analyse": "a\nb"}')["analyse"] == "a\nb"


def test_analysis_json_retry(monkeypatch):
    prompts = []
    answers = iter(['{"analyse": "x",, }', '{"analyse": "ok", "schritte": []}'])
    monkeypatch.setattr(loop, "_gpt", lambda p: (prompts.append(p), next(answers))[1])
    r = loop.plan_edits({"id": "A1", "title": "t", "goal": "g"})
    assert r["mode"] == "analysis" and r["result"]["analyse"] == "ok"
    assert "kein gueltiges JSON" in prompts[1]
