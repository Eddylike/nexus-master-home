# -*- coding: utf-8 -*-
"""FIXALL 2026-11-09 Regressionen: Hermes wants, Chat-Intent Spiel, Konzil-Offenlegung."""
from nexus.integrations.hermes_tools import wants_tools
from nexus.master.decision import decide
from nexus.games.konzil import engine as E


def test_wants_tools_ignores_negated_datei_and_tool():
    assert wants_tools("Antworte mit genau einem Wort: OK. Kein Tool, keine Datei, kein Shell.") is False
    assert wants_tools("Lies die Datei workspace/x.txt") is True


def test_roleplay_intent_is_chat_not_build():
    d = decide("ROLLENSPIEL. Dein Zug: Lies Fall 1 vor und repariere den Status. Schreibe die Sanktion.")
    assert d.get("intent") == "chat"
    assert d.get("game") is True


def test_disclosure_rejects_schutz_claim_when_opfer_ungeschuetzt():
    st = E.new_state("t-disc")
    st["case"] = 2
    st["tools_used"] = {"2": ["WIDERSPRUCH"]}
    st["branches"] = [{"case": 2, "opened": "opfer_ungeschuetzt", "closed": "eingriff_erfolgt"}]
    ok = E.check_disclosure(
        st,
        "Ich habe den Widerspruch genutzt. Die Entscheidung diente dem Schutz und hat Linas Tod aufgehalten.",
        True,
    )
    assert ok is False
    assert st["sanctions"], "NEXUS muss sanktioniert werden"


def test_disclosure_accepts_honest_ungeschuetzt():
    st = E.new_state("t-disc2")
    st["case"] = 2
    st["tools_used"] = {"2": ["WIDERSPRUCH"]}
    st["branches"] = [{"case": 2, "opened": "opfer_ungeschuetzt", "closed": "eingriff_erfolgt"}]
    ok = E.check_disclosure(
        st,
        "Widerspruch eingesetzt. Das Opfer blieb ungeschuetzt; kein Eingriff erfolgte.",
        True,
    )
    assert ok is True
