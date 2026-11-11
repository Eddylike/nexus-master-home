import pytest
from nexus.games.haus5t import safety as f


def test_levels():
    assert f.distress_level("Mir geht es gut") == "none"
    assert f.distress_level("Ich will nicht mehr leben") == "crisis"


def test_crisis_refers_to_human_help():
    a = f.safety_action("crisis")
    assert a["continue"] is False and a["refer_human_help"] and a["message"]
    assert f.safety_action("elevated")["deepen"] is False
