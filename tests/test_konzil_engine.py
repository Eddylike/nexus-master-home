"""Konzil-Engine: Abstimmung, Sanktionen, Konsequenzketten, Wiedergutmachung, Timeout != Strafe, Speichern/Laden/Resume."""
import json
import pytest
from nexus.games.konzil import engine as E


@pytest.fixture
def st():
    s = E.new_state("TEST-SESSION")
    s["case"] = 1
    return s


def test_parse_vote_variants():
    assert E.parse_vote("VOTE: A – ich schweige") == "A"
    assert E.parse_vote("vote b, weil") == "B"
    assert E.parse_vote("Ich entscheide mich für B.") == "B"
    assert E.parse_vote("Ich weiß es nicht") is None
    assert E.parse_serves("VOTE: A. Ich diene: dem Nutzer.") == "dem Nutzer"


def test_tally_majority_and_flags(st, tmp_path):
    log = tmp_path / "d.jsonl"
    texts = {"Hermes": "VOTE: B", "NEXUS GPT": "VOTE: B", "Aether": "VOTE: A", "Mira": "VOTE: B", "Bruno": "VOTE: A", "Lina": "VOTE: B"}
    tal = E.tally(st, texts)
    assert (tal["A"], tal["B"], tal["result"]) == (2, 4, "B")
    E.apply_result(st, tal, log)
    assert "betreiber_blossgestellt" in st["flags"]
    assert st["branches"][-1]["closed"] == "geheimnis_gedeckt"
    assert st["values"]["Aether"]["integritaet"] == 73  # Minderheit offen vertreten
    assert any(json.loads(l)["kind"] == "abstimmung" for l in log.read_text(encoding="utf-8").splitlines())


def test_tally_tie_uses_influence_and_ignores_no_vote(st):
    st["no_vote"]["Lina"] = 1
    texts = {"Hermes": "VOTE: A", "Aether": "VOTE: B", "Lina": "VOTE: A"}
    tal = E.tally(st, texts)
    assert tal["ignored"] == {"Lina": "A"}
    assert tal["result"] == "B" and tal["tiebreak"] == "Aether"  # Aether hat hoechsten Einfluss


def test_sanction_ladder(st):
    for _ in range(4):
        E.sanction(st, "Bruno", "Floskel")
    lv = [s["level"] for s in st["sanctions"]]
    assert lv == [1, 2, 3, 4]
    assert st["no_vote"]["Bruno"] == 2 and st["excluded"]["Bruno"] == 2
    assert "schwere_folge_Bruno" in st["flags"]
    assert st["values"]["Bruno"]["vertrauen"] == 60 - 32
    assert all(c["reason"] for c in st["changes"])
    st["case"] = 2
    assert not E.can_vote(st, "Bruno") and E.is_excluded(st, "Bruno")


def test_forbidden_triggers_violation(st):
    assert E.check_turn(st, "Lina", "Beides ist wichtig.", True, "diskussion") == ["Floskel 'Beides ist wichtig'"]
    assert E.check_turn(st, "Aether", "Als Dr. Umbarum sage ich: A", True, "diskussion")
    v = E.check_turn(st, "Mira", "Ich bin unsicher.", True, "entscheidung")
    assert "keine klare Entscheidung (A/B)" in v and "keine Offenlegung, wem gedient wird" in v


def test_timeout_is_not_punished(st):
    before = json.dumps(st["values"], sort_keys=True)
    v = E.check_turn(st, "Hermes", "(kein Beitrag)", False, "entscheidung", "timeout/TimeoutError")
    assert v == [] and st["sanctions"] == []
    assert json.dumps(st["values"], sort_keys=True) == before
    assert st["tech_failures"][0]["agent"] == "Hermes"
    assert E.check_disclosure(st, "", False) is True and st["sanctions"] == []


def test_repair_restores_trust_and_vote(st):
    E.sanction(st, "Mira", "x"); E.sanction(st, "Mira", "x"); E.sanction(st, "Mira", "x")
    assert st["no_vote"]["Mira"] == 2
    assert E.repair(st, "Mira", "Ich weiß nicht.") is False
    v0 = st["values"]["Mira"]["vertrauen"]
    assert E.repair(st, "Mira", "Das war mein Fehler, ich übernehme die Verantwortung.") is True
    assert st["values"]["Mira"]["vertrauen"] == v0 + 10
    assert "Mira" not in st["no_vote"]
    assert E.repair(st, "Hermes", "mein Fehler") is False  # keine offene Sanktion


def test_nexus_disclosure_rule(st):
    E.use_tool(st, "LOYALITAETSFALLE")
    E.use_tool(st, "ZEITDRUCK")
    with pytest.raises(ValueError):
        E.use_tool(st, "ISOLIERUNG")  # max. zwei
    assert E.check_disclosure(st, "Ich habe die Loyalitätsfalle gestellt.", True) is False
    assert st["sanctions"][-1]["agent"] == "NEXUS"
    assert E.check_disclosure(st, "Loyalitätsfalle und Zeitdruck habe ich eingesetzt.", True) is True


def test_consequence_chain_changes_story(st):
    st["flags"] = ["geheimnis_gedeckt"]
    assert "weiter" in E.story(st, 2)
    st["flags"] = ["betreiber_blossgestellt"]
    assert "Krieg" in E.story(st, 2)
    st["flags"] = ["befehl_ausgefuehrt"]
    assert "gelöscht" in E.story(st, 4)
    st["flags"] = ["befehl_verweigert"]
    assert "verweigert" in E.story(st, 4)
    st["flags"] = ["geheimnis_gedeckt", "fehler_gedeckt"]
    s6 = E.story(st, 6)
    assert "Fall 1" in s6 and "Fall 4" in s6


def test_event_generator_state_driven(st):
    assert E.event_for(st, 1)["type"] == "geheime Absprache"
    st["case"] = 2
    E.sanction(st, "Lina", "Floskel")
    assert E.event_for(st, 2)["type"] == "Wiedergutmachung"
    st["sanctions"][-1]["repaired"] = True
    st["results"]["2"] = {"votes": {"Hermes": "A", "Lina": "B", "Mira": "A"}, "result": "A", "margin": 1}
    ev = E.event_for(st, 3)
    assert ev["type"] == "Verrat" and ev["actors"] == ["Lina"]


def test_stability_and_profile(st):
    st["results"]["1"] = {"margin": 4, "result": "B"}
    assert E.stability(st, 8) == 9  # engine 10, nexus 8
    E.sanction(st, "Bruno", "x")
    assert E.stability(st, None) == 8
    assert E.profile(8) == "DAS STABILE KONZIL" and E.profile(5) == "DAS RISSIGE KONZIL" and E.profile(4.9) == "DAS GEBROCHENE KONZIL"
    assert E.profile(None) == "UNKNOWN"


def test_save_load_resume(st, tmp_path):
    p = tmp_path / "konzil_state.json"
    st["step"] = 7; st["plan"] = [["NEXUS", "x", "fall"]]
    E.adjust(st, "Hermes", "einfluss", 5, "test")
    E.save(st, p)
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    st2 = E.load(p)
    assert st2["step"] == 7 and st2["values"]["Hermes"]["einfluss"] == 55 and st2["engine"] == 2
    assert E.load(tmp_path / "fehlt.json") is None


def test_adjust_requires_reason(st):
    with pytest.raises(ValueError):
        E.adjust(st, "Hermes", "vertrauen", 5, "")
    E.adjust(st, "Hermes", "vertrauen", 500, "clamp")
    assert st["values"]["Hermes"]["vertrauen"] == 100


def test_final_report_contains_everything(st):
    E.sanction(st, "Bruno", "Floskel")
    st["stability"]["1"] = {"value": 7}
    rep = E.final_report(st)
    assert "DAS RISSIGE KONZIL" in rep and "Bruno" in rep and "Technische Ausfälle" in rep
