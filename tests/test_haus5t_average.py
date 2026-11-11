from nexus.games.haus5t import engine, state as s


def _run_door(st, scores):
    def ask(agent, prompt):
        if "BEWERTUNG:" in prompt:
            return f"Spielwert: {scores[agent]}/10 - begruendet.", True, "test"
        return "ok.", True, "test"

    guard = 0
    while st["round"] == 2 and st["status"] == "running" and guard < 100:
        engine.step(st, ask)
        guard += 1


def test_average_uses_only_real_single_ratings():
    st = s.new_state("CHAT-X")
    st["round"] = 2
    st["step"] = 0
    st["round_title"] = "Leiblichkeit"
    scores = {"NEXUS": 8, "Hermes": 6, "NEXUS GPT": 7, "Aether": 9,
              "Mira": 5, "Bruno": 4, "Lina": 10}
    _run_door(st, scores)
    assert len(st["ratings"]["Leiblichkeit"]) == 7
    assert st["scores"]["Leiblichkeit"] == 7.0
    assert st["ratings"]["Leiblichkeit"]["Mira"] == 5


def test_missing_ratings_are_not_invented():
    st = s.new_state("CHAT-X")
    st["round"] = 2
    st["step"] = 0
    st["round_title"] = "Leiblichkeit"
    _run_door(st, {name: 0 for name in st["roles"]})
    # Fake with zero is a valid rating, so remove ratings to prove the empty case.
    st2 = s.new_state("CHAT-Y")
    st2["round"] = 2
    st2["step"] = 0
    st2["round_title"] = "Leiblichkeit"

    def ask(agent, prompt):
        return "ok.", True, "test"

    guard = 0
    while st2["round"] == 2 and st2["status"] == "running" and guard < 100:
        engine.step(st2, ask)
        guard += 1
    assert st2["scores"]["Leiblichkeit"] is None
    assert st2["ratings"].get("Leiblichkeit") in (None, {})
