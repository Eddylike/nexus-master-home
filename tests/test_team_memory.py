from nexus.swarm import core, experience


def test_memory_brief_returns_prior_evidence(tmp_path):
    path = tmp_path / "experience.jsonl"
    experience.add(path, {"run_id": "run-1", "evidence_id": "EV-1", "task": "reports zusammenfassen", "score": 1})
    brief = experience.memory_brief(path, "reports kurz zusammenfassen")
    assert brief["evidence_ids"] == ["EV-1"]
    assert brief["adoption_target"] == 0.80


def test_learning_measures_evidence_adoption(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "SWARM_DIR", tmp_path)
    state = {"id": "run-2", "task": "reports", "mode": "pipeline", "winner": "V1", "votes": [{"score": 1}],
             "memory_brief": {"evidence_ids": ["EV-1"]}, "adopted_evidence_ids": ["EV-1"]}
    result = core.n_learning(state)
    assert result["evolution"]["adoption_rate"] == 1.0
    assert result["evolution"]["target_met"] is True
    assert result["evolution"]["tracking"]["score_delta"] == 1.0
    assert (tmp_path / "evolution.jsonl").is_file()


def test_debugger_removes_invalid_variants_before_retry():
    result = core.n_debugger({"variants": [{"id": "V1", "text": ""}, {"id": "V2", "text": "x" * 60}]})
    assert result["debug"]["retry"] is True
    assert result["variants"] == [{"id": "V2", "text": "x" * 60}]
