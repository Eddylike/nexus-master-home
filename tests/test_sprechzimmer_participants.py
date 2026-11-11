"""SZ-02: Nur NEXUS darf einladen, unbekannte Agenten werden abgelehnt."""
import pytest


def _p():
    from nexus.sprechzimmer import participants
    return participants


def test_known_agents_and_moderator():
    p = _p()
    assert set(p.KNOWN_AGENTS) >= {"NEXUS", "NEXUS GPT", "Hermes", "Aether"}
    assert p.MODERATOR == "NEXUS"
    assert "Ita" not in p.KNOWN_AGENTS


def test_nexus_can_invite():
    p = _p()
    start = ["NEXUS", "Hermes"]
    out = p.invite(start, "Aether", by="NEXUS")
    assert out == ["NEXUS", "Hermes", "Aether"]
    assert start == ["NEXUS", "Hermes"]


def test_double_invite_no_duplicate():
    assert _p().invite(["NEXUS", "Hermes"], "Hermes", by="NEXUS") == ["NEXUS", "Hermes"]


@pytest.mark.parametrize("by", ["Hermes", "Aether", "NEXUS GPT", "Eddie"])
def test_only_nexus_may_invite(by):
    with pytest.raises(PermissionError):
        _p().invite(["NEXUS"], "Hermes", by=by)


def test_unknown_agent_rejected():
    p = _p()
    with pytest.raises(ValueError):
        p.invite(["NEXUS"], "Ita", by="NEXUS")
    with pytest.raises(ValueError):
        p.validate_participants(["NEXUS", "Fremder"])


def test_validate_dedup():
    assert _p().validate_participants(["Hermes", "NEXUS", "Hermes"]) == ["Hermes", "NEXUS"]