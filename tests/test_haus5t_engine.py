import pytest
from nexus.games.haus5t import engine as e, state as s


def fake_ask(log):
    def ask(agent, prompt):
        log.append((agent, prompt)); return (f"{agent} antwortet.", True, "test")
    return ask


def test_plan_door_round():
    plan = e.plan_round(2)
    names = [a for a, _ in plan]
    assert names[0] == "NEXUS" and names[-1] == "NEXUS"
    assert {"NEXUS", "Hermes", "NEXUS GPT", "Aether", "Mira", "Bruno", "Lina"} <= set(names)


def test_context_and_eddie_in_prompt():
    st = s.new_state("CHAT-X"); s.add_contribution(st, "Bruno", "Ich halte fest.", True, "t")
    p = e.build_prompt(st, "NEXUS", "Weiter", ["Eddie fragt etwas"])
    assert "Ich halte fest." in p and "Eddie fragt etwas" in p and "SIMULATION" in p.upper()


def test_step_pause_resume():
    st = s.new_state("CHAT-X"); log = []
    e.step(st, fake_ask(log)); assert len(log) == 1 and st["contributions"]
    e.control(st, "pause"); e.step(st, fake_ask(log)); assert len(log) == 1
    e.control(st, "resume"); e.step(st, fake_ask(log)); assert len(log) == 2


def test_timeout_is_honest():
    st = s.new_state("CHAT-X")
    e.step(st, lambda a, p: ("", False, "timeout"))
    assert st["contributions"][-1]["ok"] is False


def test_full_game_seven_rounds():
    st = s.new_state("CHAT-X"); n = 0
    while st.get("status") != "finished" and n < 500:
        e.step(st, lambda a, p: ("Spielwert: 6/10" if a == "NEXUS" else "ok.", True, "t")); n += 1
    assert st["status"] == "finished" and len(st["scores"]) == 5
    assert e.summary(st)["rounds_done"] == 7
