import pytest
from nexus.games.haus5t import rules as r


def test_doors_complete():
    for i in range(5):
        d = r.door_spec(i)
        assert d["name"] == r.DOORS[i] and all(d[k] for k in ("situation", "conflict", "question"))
    with pytest.raises(IndexError):
        r.door_spec(5)


def test_round_titles():
    assert r.round_title(1) == "Ankommen" and r.round_title(7) == "Abschluss"
    with pytest.raises(ValueError):
        r.round_title(8)


def test_one_question_rule():
    assert r.check_reply("Ich hoere dich. Was spuerst du?")["ok"]
    bad = r.check_reply("Warum? Und wieso?")
    assert not bad["ok"] and "MULTI_QUESTION" in bad["violations"]


def test_parse_score():
    assert r.parse_score("Spielwert: 7/10") == 7
    assert r.parse_score("Spielwert: 12/10") is None and r.parse_score("ohne") is None


def test_parse_score_natural_wording():
    assert r.parse_score("Der Spielwert dieser Tuer erreicht 9/10, da sie traegt.") == 9
    assert r.parse_score("Die Tuer wird mit einem Spielwert von 8/10 bewertet.") == 8
    assert r.parse_score("Spielwert: Der Durchschnitt liegt bei 8,8/10.") is None
