"""MH4D 2026-11-10: 4D-Daemmerungs-Redesign Master Home (statische Pruefungen + Live-Abruf)."""
import json
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "nexus" / "chat" / "static"


def _read(rel):
    return (STATIC / rel).read_text(encoding="utf-8")


def test_mh4d_css_loaded_last_with_cache_bust():
    html = _read("index.html")
    i_dark = html.index("nexus4d_dark.css?v=")
    i_mh = html.index("/static/css/nexus4d_mh4d.css?v=mh4d")
    assert i_mh > i_dark
    css = _read("css/nexus4d_mh4d.css")
    assert "#nxTop#nxTop{height:56px" in css
    assert "nxGlassTelemetry" not in css  # MH4D-02: Statusleiste bleibt entfernt (Eddie 02.11.)


def test_livehub_dark_and_cache_busted():
    html = _read("index.html")
    assert 'f.setAttribute("src","/live_monitor.html?v=mh4d1")' in html
    assert "#fbfdff" not in html  # kein helles Live-Hub-Theme mehr
    assert '/live_monitor.html?v=mh4d1' in _read("js/nexus4d.js")
    lm = _read("live_monitor.html")
    assert "MH4D-01" in lm and 'html.emb body header{display:none!important}' in lm
    assert "html,html body{background:#0a0d18!important" in lm
    assert 'classList.toggle("open")' in lm  # Roh-JSON aufklappbar
    assert "/api/live/stream" in lm


def test_dock_labels_short():
    js = _read("js/master_glass.js")
    assert '[1.3, 15.6, 17.5, 15.4], "WhatsApp"]' in js
    assert '[1.3, 84, 97.8, 9.5], "NEXUS All"]' in js


def test_ping_testmessage_removed_from_history():
    p = ROOT / "workspace" / "chat" / "conversations.json"
    if not p.exists():
        pytest.skip("kein Verlauf")
    assert "Ping 4D-Test" not in p.read_text(encoding="utf-8")


def test_live_served_files_current():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8299/live_monitor.html?v=mh4d1", timeout=8) as r:
            lm = r.read().decode("utf-8", errors="replace")
        with urllib.request.urlopen("http://127.0.0.1:8299/", timeout=8) as r:
            idx = r.read().decode("utf-8", errors="replace")
    except Exception:
        pytest.skip("NEXUS :8299 nicht erreichbar")
    assert "MH4D-01" in lm
    assert "nexus4d_mh4d.css?v=mh4d" in idx and "think_panel.js?v=think1" in idx
