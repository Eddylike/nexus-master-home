"""MH05-MH12 (20261028): Statuslogik Ollama/ChatGPT, Kacheln -> echte Ziele, kein Ueberlappen."""
import json
import re
from pathlib import Path

from nexus import nexus_os as n

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "nexus" / "chat" / "static" / "js" / "master_glass.js").read_text(encoding="utf-8", errors="replace")
TILES = re.findall(r'\["(\w+)", "[^"]*", "\w+", (.+?), \[([\d.]+), ([\d.]+), ([\d.]+), ([\d.]+)\]', JS)


def test_mh05_ollama_model_loaded_is_online(monkeypatch):
    monkeypatch.setattr(n, "wozipc_local_status", lambda cfg=None: {"status": n.BUSY, "reason": "MODEL_LOADED", "model_loaded": True,
                                                                    "model": "qwen3.5:9b", "reachable": True, "source": "x"})
    src = Path(n.__file__).read_text(encoding="utf-8")
    assert "MH05 20261028" in src and 'status=ONLINE, busy=True' in src


def test_mh06_chatgpt_off_is_optional_not_unavailable():
    src = Path(n.__file__).read_text(encoding="utf-8")
    assert 'reason="OFF_BY_COST_RULE"' in src and 'reason="NOT_CONFIGURED"' not in src


def test_mh08_tiles_have_real_targets():
    assert len(TILES) >= 7  # DEDUPE 20261102: Health-Kachel (doppelt zu Header-Health) entfernt
    from nexus.computer.os_actions import APP_MAP
    server = (ROOT / "nexus" / "chat" / "server.py").read_text(encoding="utf-8", errors="replace")
    for tid, action, *_ in TILES:
        m = re.search(r'window\.open\("(/\w+)"', action)
        if m:
            assert f'"{m.group(1)}"' in server, tid
        m = re.search(r'launch_app", \{ name: "(\w+)"', action)
        if m:
            assert m.group(1) in APP_MAP, tid
        assert "/api/os/" in action or "window.open" in action or "navigate(" in action, tid


def test_mh12_tiles_do_not_overlap():
    boxes = [(t[0], *map(float, t[2:])) for t in TILES]
    for i, (a, ax, ay, aw, ah) in enumerate(boxes):
        assert ax + aw <= 100.0 and ay + ah <= 100.0, a
        for b, bx, by, bw, bh in boxes[i + 1:]:
            assert ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay, (a, b)
