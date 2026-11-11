"""MH4D-GLAS: Glasoptik Master Home (Blau/Mint/Neon-Lila), abschaltbar, wenig Blur."""
import re
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "nexus" / "chat" / "static"
CSS = STATIC / "css" / "nexus4d_glass.css"
JS = STATIC / "js" / "glass_theme.js"
INDEX = STATIC / "index.html"


def _css():
    return CSS.read_text(encoding="utf-8")


def test_glass_variables_colors():
    s = _css()
    for v in ("--gl-blue:#3b82f6", "--gl-mint:#5eead4", "--gl-violet:#c084fc", "--gl-blur:14px", "--gl-glass:"):
        assert v in s, v


def test_glass_switchable_scope():
    s = _css()
    rules = [r for r in re.findall(r"([^{}]+)\{", s) if not r.strip().startswith("@media") and not r.strip().startswith("/*")]
    rules = [r.strip() for r in rules if r.strip()]
    assert rules, "keine Regeln"
    for r in rules:
        assert r.lstrip().split("*/")[-1].strip().startswith("html:not(.nx-glass-off)"), r


def test_backdrop_blur_limited_and_not_on_fixed_ancestors():
    s = _css()
    blur_rules = [m.group(1) for m in re.finditer(r"([^{}]+)\{[^{}]*backdrop-filter:blur", s)]
    assert 1 <= len(blur_rules) <= 3, blur_rules
    for r in blur_rules:
        for bad in ("#nxReferenceScene", "#nxApp", "#nxSide", "body#nxBody{", ".n4-bg"):
            assert bad not in r.split("*/")[-1], (bad, r)


def test_reduced_motion_respected_by_base():
    base = (STATIC / "css" / "nexus4d.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in base
    assert "animation" not in _css()


def test_toggle_js():
    j = JS.read_text(encoding="utf-8")
    assert '"nxGlass"' in j and "nx-glass-off" in j and "nxGlassToggle" in j
    assert "glass=([01])" in j and "window.nxGlass" in j


def test_index_links_after_think_panel():
    h = INDEX.read_text(encoding="utf-8")
    c = h.index("nexus4d_glass.css?v=glass")
    assert h.index("think_panel.css?v=") < c
    assert h.index("think_panel.js?v=") < h.index("glass_theme.js?v=glass")


def test_live_served():
    try:
        html = urllib.request.urlopen("http://127.0.0.1:8299/", timeout=5).read().decode("utf-8", "replace")
    except Exception as e:  # pragma: no cover
        pytest.skip(f"Core nicht erreichbar: {e}")
    assert "nexus4d_glass.css?v=glass" in html
    css = urllib.request.urlopen("http://127.0.0.1:8299/static/css/nexus4d_glass.css?v=glass1", timeout=5).read().decode("utf-8")
    assert "--gl-mint" in css