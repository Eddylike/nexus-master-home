import json, pytest
from nexus.livetalk.voice import voice_for, load_cfg, AGENTS, DEFAULT_MODEL

def test_same_voice_all_agents():
    cfg = {"model": "G:/meine-ki/models/piper/de_DE-thorsten-high.onnx"}
    vs = [voice_for(a, cfg) for a in AGENTS]
    assert all(v == vs[0] for v in vs)
    assert vs[0] == {"engine": "piper", "model": cfg["model"], "config": cfg["model"] + ".json", "language": "de"}

def test_errors():
    with pytest.raises(KeyError): voice_for("Fremder", {"model": "x.onnx"})
    with pytest.raises(ValueError): voice_for("NEXUS", {})
    with pytest.raises(ValueError): voice_for("NEXUS", {"model": ""})

def test_load_cfg(tmp_path):
    d = load_cfg(tmp_path / "no.json")
    assert d["engine"] == "piper" and d["model"] == DEFAULT_MODEL
    p = tmp_path / "c.json"; p.write_text(json.dumps({"engine": "piper", "model": "a.onnx"}))
    assert load_cfg(p)["model"] == "a.onnx"