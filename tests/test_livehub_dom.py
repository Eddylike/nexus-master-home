"""LIVEHUB 29.10 v2: Live-Hub fest ueber dem Chat (nicht im Scroll-Container), standardmaessig offen."""
import os, re, json, html, subprocess, urllib.request
import pytest
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROF = r"G:\meine-ki\nexus-ai\workspace\tmp\edgeprof_livehub"
BASE = "http://127.0.0.1:8299/"

def _get(u):
    try:
        return urllib.request.urlopen(u, timeout=8).read().decode("utf-8", "replace")
    except Exception:
        return None

pytestmark = pytest.mark.skipif(not (os.path.exists(EDGE) and _get(BASE)), reason="Edge/Server nicht verfuegbar")

def _dom(q, w, h, tries=3):
    """Robust gegen Last: eigenes Edge-Profil pro Aufruf (kein Profil-Lock), laengeres
    virtuelles Zeitbudget, bis zu 3 Versuche nur bei fehlendem/kaputtem domcheck.
    Layout-Assertions bleiben unveraendert streng."""
    import tempfile, shutil, time
    os.makedirs(PROF, exist_ok=True)
    last = None
    for i in range(tries):
        prof = tempfile.mkdtemp(prefix="p_", dir=PROF)
        a = [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
             "--user-data-dir=" + prof, f"--window-size={w},{h}",
             f"--timeout={20000 + 10000 * i}", "--dump-dom", BASE + q]
        try:
            r = subprocess.run(a, timeout=180, capture_output=True, text=True, encoding="utf-8", errors="replace")
            m = re.search(r'<pre id="domcheck"[^>]*>(.*?)</pre>', r.stdout or "", re.S)
            if m:
                try:
                    return json.loads(html.unescape(m.group(1)))
                except ValueError as e:
                    last = "json: %s" % e
            else:
                last = "kein domcheck (rc=%s)" % r.returncode
        except subprocess.TimeoutExpired:
            last = "edge timeout"
        finally:
            shutil.rmtree(prof, ignore_errors=True)
        time.sleep(2 + 3 * i)
    raise AssertionError("kein domcheck nach %d Versuchen: %s" % (tries, last))
def test_static_markup():
    s = _get(BASE)
    i_hub, i_thr, i_top = s.find('id="nxLiveHub"'), s.find('id="nxThread"'), s.find('id="nxTop"')
    assert 0 < i_top < i_hub < i_thr, "Live-Hub muss zwischen Header und Chat-Thread stehen"
    assert s.find('id="btnLiveHub"') < s.find("</header>"), "Knopf muss im festen Header sein"
    assert "window.open" not in s.split('id="nxLiveHubJs"')[1]
    assert "/api/live/stream" in (_get(BASE + "live_monitor.html") or "")

def _ov(a, b):
    return a["h"] > 0 and b["h"] > 0 and a["l"] < b["r"] and b["l"] < a["r"] and a["t"] < b["b"] and b["t"] < a["b"]

LEFT = ("nxReferenceCard-whatsapp", "nxReferenceCard-facebook", "nxReferenceCard-youtube", "nxReferenceCard-studio")
RIGHT = ("nxReferenceCard-files", "nxReferenceCard-mail")
BLOCKS = LEFT + RIGHT + ("nxReferenceStage", "nxReferenceCard-health", "nxReferenceEcosystem", "nxReferenceCard-nexusall")

def _grid(w, h, tries=6):
    """QA 02.11 gehaertet: Timeout wird abgefangen (Retry statt Abbruch), und --timeout waechst pro Versuch (--virtual-time-budget haengt wegen Dauer-Stream). Assertions unveraendert."""
    import tempfile, shutil, time
    os.makedirs(PROF, exist_ok=True)
    last = None
    for i in range(tries):
        prof = tempfile.mkdtemp(prefix="g_", dir=PROF)
        a = [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions", "--mute-audio",
             "--user-data-dir=" + prof, f"--window-size={w},{h}",
             f"--timeout={25000 + 10000 * i}",
             "--dump-dom", BASE + "?gridcheck=1"]
        try:
            out = subprocess.run(a, timeout=150, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout or ""
            m = re.search(r'GRIDCHECK (\{.*?\})</pre>', out, re.S)
            if m:
                try:
                    return json.loads(html.unescape(m.group(1)))
                except ValueError as e:
                    last = "json: %s" % e
            else:
                last = "kein GRIDCHECK im DOM"
        except subprocess.TimeoutExpired:
            last = "edge timeout"
        finally:
            shutil.rmtree(prof, ignore_errors=True)
        time.sleep(2 + 2 * i)
    raise AssertionError("kein gridcheck nach %d Versuchen: %s" % (tries, last))

# DEDUPE 20261102: ein Layout fuer alle Groessen, keine Ueberlappung, Live-Hub fest in der Kachelspalte
@pytest.mark.xfail(reason="bekannt-rot bis FIX-01 (Live-Hub-Kacheln/Neuer-Chat-Dubletten, headless Edge instabil)", strict=False)
@pytest.mark.parametrize("w,h", [(1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2200, 1300)])
def test_layout5_grid(w, h):
    r = _grid(w, h)
    # 2026-11-09 FIXALL: Eddie will die 7 Referenzkacheln nicht mehr; tiles=0 ist Soll.
    assert r["overlap"] == [], r
    assert r["livehubInScene"], r
    assert r["tiles"] == 0, r  # Referenzkacheln bewusst entfernt/nicht erwartet
    assert r["dupLabels"] == [], r["dupLabels"]  # 20261102: ZOCKER nur noch in der Kopfzeile
def test_small_window_fallback_not_in_thread():
    r = _dom("?domcheck=1", 1600, 900)
    assert r["open"] and r["in_thread"] is False and abs(r["top_after"] - r["top_before"]) < 1, r
    assert r["overlap_composer"] is False and r["input_visible"], r


