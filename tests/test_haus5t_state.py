import pytest
from nexus.games.haus5t import state as s


def test_roundtrip_resume(tmp_path):
    st = s.new_state("CHAT-X")
    assert st["round"] == 1 and len(st["roles"]) == 7
    s.add_contribution(st, "Mira", "Hallo", True, "ollama-persona")
    s.set_score(st, "Leiblichkeit", 6)
    p = tmp_path / "st.json"; s.save_state(st, p)
    assert not p.read_bytes().startswith(b"\xef\xbb\xbf")
    back = s.load_state(p)
    assert back["contributions"][0]["speaker"] == "Mira" and back["scores"]["Leiblichkeit"] == 6
    assert s.load_state(tmp_path / "fehlt.json") is None


def test_validation():
    st = s.new_state("CHAT-X")
    with pytest.raises(ValueError):
        s.add_contribution(st, "Ita", "x", True, "x")
    with pytest.raises(ValueError):
        s.set_score(st, "Leiblichkeit", 11)
