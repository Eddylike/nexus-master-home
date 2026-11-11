"""THINK-05/06 (2026-11-10): Denkprozess-Fenster im Master Home (statisch + live gegen :8299)."""
import json
import os
import subprocess
import sys
import tempfile
import shutil
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "nexus" / "chat" / "static"
BASE = "http://127.0.0.1:8299"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def _get(path, timeout=10):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _post(path, obj, timeout=10):
    req = urllib.request.Request(BASE + path, json.dumps(obj).encode(), {"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def test_index_loads_think_panel():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "/static/js/think_panel.js?v=" in html
    assert "/static/css/think_panel.css?v=" in html
    assert html.index("think_panel.js") > html.index("nexus4d.js")


def test_think_panel_js_contract():
    js = (STATIC / "js" / "think_panel.js").read_text(encoding="utf-8")
    for needle in ("Kein Denkprozess aktiv", "nxThinkEnabled", "/api/chat/think", "/api/chat/thinking",
                   "b.think = S.enabled", 'ev.type === "thinking"', "nx-think-msg", "res.body.tee", "role=\"switch\""):
        assert needle in js, needle
    css = (STATIC / "css" / "think_panel.css").read_text(encoding="utf-8")
    assert "#nxThinkPanel" in css and "nx-think-on" in css


def test_live_settings_endpoint_roundtrip():
    if _get("/api/health", 5) is None:
        pytest.skip("NEXUS :8299 nicht erreichbar")
    cur = json.loads(_get("/api/chat/think"))
    assert cur["success"] is True and isinstance(cur["enabled"], bool)
    try:
        assert _post("/api/chat/think", {"enabled": not cur["enabled"]})["enabled"] is (not cur["enabled"])
        assert json.loads(_get("/api/chat/think"))["enabled"] is (not cur["enabled"])
    finally:
        _post("/api/chat/think", {"enabled": cur["enabled"]})
    assert json.loads(_get("/api/chat/think"))["enabled"] is cur["enabled"]
    items = json.loads(_get("/api/chat/thinking"))
    assert items["success"] is True and isinstance(items["items"], list)


def test_live_panel_dom_headless():
    if _get("/api/health", 5) is None:
        pytest.skip("NEXUS :8299 nicht erreichbar")
    if not os.path.exists(EDGE):
        pytest.skip("Edge fehlt")
    base = ROOT / "workspace" / "tmp"; base.mkdir(parents=True, exist_ok=True)
    prof = tempfile.mkdtemp(prefix="thinkprof_", dir=str(base))
    try:
        r = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
                            "--user-data-dir=" + prof, "--window-size=1920,1080", "--timeout=12000", "--dump-dom",
                            BASE + "/?thinkcheck=1"], capture_output=True, text=True, timeout=120, errors="replace")
    finally:
        shutil.rmtree(prof, ignore_errors=True)
    dom = r.stdout or ""
    i = dom.find('id="thinkcheck"')
    assert i >= 0, "Pruefhaken fehlt (think_panel.js nicht geladen?)"
    raw = dom[dom.index(">", i) + 1: dom.index("</pre>", i)]
    raw = raw.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    chk = json.loads(raw)
    assert chk["mounted"] == "nxChat"
    assert chk["overlapComposer"] is False and chk["overlapThread"] is False
    assert chk["aria"] in ("true", "false")
    assert chk["emptyText"] in ("Kein Denkprozess aktiv", "Denkt nach \u2026")
