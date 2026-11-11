"""MH4D Runde 3 (10.11.2026): Titel-Tooltip, 1280-Ueberlappungen, tote settingsDesktopBg-Reste, Favicon, Pruefskript."""
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "nexus" / "chat" / "static"


def _r(p):
    return (S / p).read_text(encoding="utf-8")


def test_title_tooltip_script_linked():
    j = _r("js/mh4d_fixes.js")
    assert 'getElementById("chatTitle")' in j and "t.title" in j and "MutationObserver" in j
    assert "mh4d_fixes.js?v=" in _r("index.html")


def test_1280_overlap_rules_below_1660():
    c = _r("css/nexus4d_mh4d.css")
    blk = c[c.index("MH4D-08 2026-11-10 (1280x900"):]
    assert "@media not all and (min-width:1660px)" in blk
    assert "#nxReferenceCard-library{display:none" in blk
    assert "#btnLiveHubClose{flex:0 0 24px" in blk


def test_settings_desktop_bg_dead_code_removed():
    assert '$("#settingsDesktopBg")' not in _r("js/app.js")
    assert "#settingsDesktopBg" not in _r("css/nexus4d.css")
    assert 'id="btnDesktopBg"' in _r("index.html") or "btnDesktopBg" in _r("js/app.js") or "btnDesktopBg" in _r("js/master_glass.js")


def test_layout_checker_skips_closed_details():
    assert 'p.tagName==="DETAILS"&&!p.open' in _r("js/nexus4d.js")


def test_favicon_and_name():
    h = _r("index.html")
    assert 'rel="icon" href="data:image/svg+xml' in h
    assert "<title>NEXUS" in h


def test_live_served_round3():
    try:
        html = urllib.request.urlopen("http://127.0.0.1:8299/", timeout=5).read().decode("utf-8", "replace")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Core nicht erreichbar: {e}")
    assert "mh4d_fixes.js?v=" in html and "nexus4d_mh4d.css?v=mh4d3" in html