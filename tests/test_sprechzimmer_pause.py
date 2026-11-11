import threading
import time

from nexus.sprechzimmer import engine


def test_pause_stops_running_autonomous_loop():
    session = {"id": "SZ-pause-test", "participants": ["NEXUS", "Aether"], "messages": []}
    seen = []

    def ask(agent, s):
        seen.append(agent)
        time.sleep(0.15)
        return "beitrag"

    box = {}
    thread = threading.Thread(target=lambda: box.update(produced=engine.run_autonomous(
        session, ask, max_rounds=10, timeout=2)))
    thread.start()
    time.sleep(0.05)
    engine.pause(session)
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert len(box["produced"]) <= 1
    assert session["paused"] is True
    assert len(seen) <= 2
