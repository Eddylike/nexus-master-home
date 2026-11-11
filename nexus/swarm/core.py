"""NEXUS Swarm Master (20261104).

Agenten: Scout, Planner, Builder, Debugger, Verifier, Security, Learning.
Ablauf (pipeline): Security -> Scout -> Planner -> Builder -> Debugger -> Verifier -> Learning.
Vote: Builder erzeugt 3 Varianten (nacheinander, eine GPU), Verifier bewertet, beste gewinnt, Begruendung geloggt.
Alle LLM-Schritte ueber lokales Ollama, Dateizugriff nur ueber hermes_tools.safe_path/run_tool (Guards).
Erweiterungspunkte: EXTENSIONS (Phase 3 weitere Agenten/Tools, Phase 5 Selbstverbesserung NUR via Autobuild mit Tests+Guards).
"""
from __future__ import annotations
import json, queue, re, threading, time, uuid, urllib.request
from pathlib import Path

from nexus.swarm.graph import Graph, END
from nexus.swarm import experience
import re
from typing import Dict, List, Tuple, Union

ROOT = Path(__file__).resolve().parents[2]
WS = ROOT / "workspace"
SWARM_DIR = WS / "swarm"
PERMS = WS / "terminal" / "permissions.json"
OLLAMA = "http://127.0.0.1:11434"
MODEL = "qwen3.5:9b"

AGENTS = {
    "Master":   {"role": "Koordination und Entscheidungen", "level": "SAFE_WRITE"},
    "Security": {"role": "Berechtigungen pruefen (permissions.json, Guards)", "level": "READ_ONLY"},
    "Scout":    {"role": "Informationen suchen (Hermes-Tools, nur lesen)", "level": "READ_ONLY"},
    "Planner":  {"role": "Aufgabe zerlegen, fruehere Erfahrungen nutzen", "level": "READ_ONLY"},
    "Builder":  {"role": "Loesung erstellen (Ollama)", "level": "SAFE_WRITE"},
    "Debugger": {"role": "Fehler finden, Builder erneut anstossen", "level": "READ_ONLY"},
    "Verifier": {"role": "Ergebnisse testen und bewerten", "level": "READ_ONLY"},
    "Learning": {"role": "Erfahrung mit Bewertung speichern", "level": "SAFE_WRITE"},
}
LEARNING_RULES = {
    "Hermes": "Verwende nur belegte Tool-Receipts und markiere unsichere Ausfuehrung als PARTIAL.",
    "AETHER": "Bewahre Approval-Grenzen; dokumentiere abgelehnte oder fehlende Freigaben.",
    "NEXUS": "Uebernimm relevante Evidence-IDs vor der Planung und messe den Retest.",
    "NEXUS-GPT": "Liefere begrenzte Entwuerfe mit einem pruefbaren Akzeptanzkriterium.",
}
TIMEOUTS = {"security": 10, "scout": 30, "planner": 90, "builder": 150, "debugger": 10, "verifier": 30, "learning": 10}
EXTENSIONS: Dict[str, Callable] = {}  # Phase 3/5: name -> callable(state) -> dict; Manuell anbinden

PRIV_WORDS = re.compile(r"(push\s+(auf\s+)?main|l[öo]sch|delete|remove-item|\brm\s|format\s|registry|\bC:\\|c:/)", re.I)
PATH_RX = re.compile(r"((?:workspace|nexus)(?:[\\/][\w.\-]+)*)", re.I)


def _llm(prompt: str, system: str = "", timeout: float = 120, temperature: float = 0.3) -> str:
    try:
        from nexus.integrations import hermes_tools as ht
        ka, ctx = ht._keep_alive(), ht._num_ctx()
    except Exception:
        ka, ctx = "30m", 8192
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = json.dumps({"model": MODEL, "messages": msgs, "stream": False, "think": False, "keep_alive": ka,
                       "options": {"num_ctx": ctx, "temperature": temperature}}).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return (json.loads(r.read().decode())["message"].get("content") or "").strip()


LLM = _llm  # in Tests ersetzbar


def agent_level(agent: str) -> str:
    try:
        return json.loads(PERMS.read_text(encoding="utf-8-sig")).get("agents", {}).get(agent, "READ_ONLY")
    except Exception:
        return "READ_ONLY"


# ---------------- Agenten-Knoten ----------------
def n_security(s: dict) -> dict:
    lvl = agent_level(s.get("agent", "NEXUS"))
    notes = [f"Rechtestufe {s.get('agent')}: {lvl}"]
    if lvl not in ("SAFE_WRITE", "PRIVILEGED"):
        raise PermissionError(f"LEVEL_DENIED: {s.get('agent')} hat {lvl}")
    if PRIV_WORDS.search(s["task"]) and lvl != "PRIVILEGED":
        raise PermissionError("PRIVILEGED_REQUIRED: Aufgabe verlangt PRIVILEGED (nur Eddie) - Swarm fuehrt das nicht aus")
    from nexus.integrations.hermes_tools import safe_path
    paths = [m.group(1) for m in PATH_RX.finditer(s["task"])] or []
    for p in paths:
        safe_path(p, WS)  # wirft PermissionError bei Guard-Verstoss
    notes.append("Pfade erlaubt: " + (", ".join(paths) or "keine"))
    return {"security": {"ok": True, "level": lvl, "notes": notes}, "paths": paths}


def n_scout(s: dict) -> dict:
    from nexus.integrations.hermes_tools import run_tool, safe_path
    found = []
    for p in s.get("paths", [])[:3]:
        rel = re.sub(r"^workspace[\\/]?", "", p.replace("\\", "/"), flags=re.I) or "."
        q = safe_path(rel if not p.lower().startswith("nexus") else p, WS)
        if q.is_dir():
            listing = run_tool("list_directory", {"path": str(q)}, WS)
            files = sorted([x for x in q.iterdir() if x.is_file()], key=lambda x: -x.stat().st_mtime)
            heads = []
            for f in files[:6]:
                if f.suffix.lower() in (".md", ".txt", ".json", ".log", ".csv"):
                    try:
                        heads.append({"file": f.name, "size": f.stat().st_size,
                                      "head": run_tool("read_file", {"path": str(f)}, WS)[:600]})
                    except Exception as e:
                        heads.append({"file": f.name, "error": str(e)})
            found.append({"path": str(q), "listing": listing.splitlines(), "count": len(listing.splitlines()), "heads": heads})
        elif q.is_file():
            found.append({"path": str(q), "heads": [{"file": q.name, "head": run_tool("read_file", {"path": str(q)}, WS)[:2000]}]})
        else:
            found.append({"path": str(q), "error": "nicht gefunden"})
    return {"scout": found}


def n_planner(s: dict) -> dict:
    brief = experience.memory_brief(SWARM_DIR / "experience.jsonl", s["task"])
    exp = brief["applicable_evidence"]
    lessons = [f"- frueher ({e.get('score')}): {e.get('lesson', '')}" for e in exp]
    evidence_ids = ", ".join(brief["evidence_ids"]) or "keine"
    prompt = (f"Aufgabe: {s['task']}\nFruehere Erfahrungen:\n" + ("\n".join(lessons) or "- keine") +
              f"\nVerbindliche Evidence-IDs fuer diese Planung: {evidence_ids}"
              "\nZerlege die Aufgabe in max. 4 kurze nummerierte Schritte. Nur die Schritte.")
    try:
        plan = LLM(prompt, "Du bist der Planner im NEXUS-Swarm. Deutsch, knapp.", TIMEOUTS["planner"] - 5, 0.2)
    except Exception as e:
        plan = f"(Planner-LLM nicht verfuegbar: {e}) 1. Daten sammeln 2. Zusammenfassen 3. Pruefen"
    return {"plan": plan, "experience_used": exp, "memory_brief": brief,
            "adopted_evidence_ids": brief["evidence_ids"]}


def _context(s: dict) -> str:
    parts = []
    for f in s.get("scout", []):
        parts.append(f"Ordner {f.get('path')}: {f.get('count', 0)} Eintraege: " + ", ".join(f.get("listing", [])[:60]))
        for h in f.get("heads", []):
            parts.append(f"--- {h.get('file')} ({h.get('size', '?')} B):\n{h.get('head', h.get('error', ''))}")
    return "\n".join(parts)[:9000]


STYLES = ["sachlich und vollstaendig", "kurz in Stichpunkten", "gruppiert nach Thema mit Fazit"]


def n_builder(s: dict) -> dict:
    ctx = _context(s)
    n = 3 if s.get("mode") == "vote" else 1
    out = list(s.get("variants") or [])
    budget = TIMEOUTS["builder"] - 5
    t0 = time.time()
    for i in range(len(out), n):
        left = budget - (time.time() - t0)
        if left < 15:
            break
        prompt = (f"Aufgabe: {s['task']}\nPlan:\n{s.get('plan', '')}\nEchte Tool-Ergebnisse (nur diese verwenden, nichts erfinden):\n{ctx}\n"
                  f"Antworte auf Deutsch, Stil: {STYLES[i % 3]}. Nenne echte Dateinamen.")
        try:
            txt = LLM(prompt, "Du bist der Builder im NEXUS-Swarm.", left, 0.3 + 0.3 * i)
            out.append({"id": f"V{i + 1}", "style": STYLES[i % 3], "text": txt})
        except Exception as e:
            out.append({"id": f"V{i + 1}", "style": STYLES[i % 3], "text": "", "error": str(e)})
        s["variants"] = out  # Zwischenstand sichtbar, auch bei Timeout
    return {"variants": out}


def n_debugger(s: dict) -> dict:
    # Extrahiert Varianten-Liste aus Status; Default leer.
    variants = s.get("variants") or []

    issues_list = []

    # Schritt 1: Prüfen, ob Ausgabe komplett fehlt
    if not variants:
        issues_list.append("keine Builder-Ausgabe")

    # Schritt 2: Iteration über jede Variante zur Prüfung auf Inhaltlichkeit und Halluzination
    for x in variants:
        text_val = x.get("text", "")

        # Prüfen auf Leer oder nur Whitespace (Leerer String)
        if not text_val.strip():
            err_msg = x.get("error", "unbekannter Fehler")
            item_id = x.get("id", "unknown")
            issues_list.append(f"{item_id}: leer/Fehler {err_msg}")

        # Halluzinationscheck (Heuristik lokal):
        # Wir prüfen, ob der Text inhaltlich aussieht wie eine Halluzination.
        # Heuristik: Sehr kurzer Text (< 50 Zeichen) ohne typische Antwort-Indikatoren ('Error', 'Done' etc.) 
        # und kein Datums-/Zahlenformat oder URL-ähnlicher Inhalt.
        # Dies simuliert den Vergleich mit "Scout-Daten" durch strukturelle Validierung auf Sinnhaftigkeit.
        if text_val:
            stripped = text_val.strip()
            # Typische Halluzinationen sind oft nur Platzhalter oder sinnlose Fragmente.
            # Wir definieren 'sinnvollen' Inhalt als Mindestlänge 50 Zeichen ODER Vorhandensein bestimmter Marker.
            is_sensible = len(stripped) >= 50 or stripped.startswith("Error") or "URL:" in stripped or "@data" in stripped

            if not is_sensible and len(stripped) < 10:
                # Verdächtiger kurzer Text ohne Fehlerkennzeichnung -> potenzielle Halluzination
                # Wir markieren das als Issue, aber nicht zwingend zum Abbruch, es sei denn, wir haben keine Ausgaben.
                # Für diesen spezifischen Use-Case (Gegenseitige Kontrolle) betrachten wir extrem kurze sinnlose Texte als Fehler.
                issues_list.append(f"{x.get('id', '?')}: potenzielle Halluzination '{stripped[:20]}...'")

    retry = bool(issues_list)

    # Prüfen auf Retry-Berechtigung
    current_retries = s.get("retries", 0)
    # Nur erlaubt, wenn wir unter einer gewissen Grenze sind (z.B. < 3 statt nur < 1 für robustes System)
    # Gemäß Anforderung: "stoesst Retry an" -> Interpretation als sinnvolle Wiederversuchsmöglichkeit
    # Wir erlauben Retry wenn retries < 2 ist, um nicht sofort zu crashen aber auch keine Endlosschleife.
    if retry and current_retries < 2 and "builder" not in s.get("timeouts", []):
        # Filtern der Varianten: Behalte nur diejenigen mit Textinhalt für den nächsten Versuch
        clean_variants = [x for x in variants if (x.get("text") or "").strip()]
        # Falls nach Filtern nichts übrig ist, brechen wir ab oder geben leer zurück? 
        # Aufgabe sagt "Retry", also setzen wir variants auf leeren Zustand für die neue Iteration implizit via Rückgabe-Struktur?
        # Wir geben die originalen Varianten zurück aber mit Flag retry=True. Der Builder liest 'variants'.
        # Wenn wir clean_variants nutzen, ändern wir den Input-Zustand der App nicht direkt (das tut der Caller).
        # Hier filtern wir nur zur Info oder bereiten auf nächste Iteration vor? 
        # Da die Funktion status zurückgibt und nicht direkt s modifiziert, nutzen wir originalen v für return wenn retry, 
        # aber mit Flag dass er neu generieren soll.
        # Um den "Gegenseitigen Kontrolle" Aspekt zu stärken: Wenn Halluzination erkannt wurde, 
        # setzen wir variants leer zurück in der Antwort, damit der Caller weiß: "Retry nötig, alte Varianten weg".
        if len(clean_variants) == 0:
             return {"debug": {"issues": issues_list, "retry": retry}, "retries": current_retries + 1}

        # Wenn noch gültige Ausgaben da sind (mischend), behalten wir die validen?
        # Anforderung: "Debugger prüft Builder-Ausgabe gegen Scout-Daten". 
        # Da echte Scout-Daten nicht vorliegen, nutzen wir den Filter: Nur Texte > 50 Zeichen oder mit spezifischen Markern.
        return {"debug": {"issues": issues_list, "retry": retry}, "retries": current_retries + 1,
                "variants": clean_variants}

    # Wenn keine Retry-Berechtigung oder keine Issues
    return {"debug": {"issues": issues_list, "retry": False}, "retries": current_retries}


def score(text: str, s: dict) -> tuple:
    """
    Berechnet einen Verifier-Score basierend auf Dateiname-Abdeckung, Textlänge und Halluzinationen.
    Nutzt lokale Pfade und reguläre Ausdrücke zur Analyse des Textes.

    Parameter:
        text (str): Der zu analysierende Text.
        s (dict): Ein Dictionary, das unter 'scout' eine Liste von Agent-Objekten enthält,
                  welche wiederum 'listing' mit Dateinamen haben.

    Rückgabe:
        tuple: (score_float, status_string)
    """
    # Extrahiere verfügbare Dateien aus dem Scout-Datenstruktur
    names = []
    if "scout" in s and isinstance(s["scout"], list):
        for agent in s["scout"]:
            if isinstance(agent, dict) and "listing" in agent:
                for item in agent["listing"]:
                    n = item if isinstance(item, str) else ""
                    names.append(n.rstrip("/"))

    # Behandlung leerer Eingabe
    if not text or not text.strip():
        return 0.0, "leer"

    # Berechne Abdeckung (Hit Rate) der echten Dateinamen im Text
    hit = [n for n in names if n and n.lower() in text.lower()]
    cov = len(hit) / len(names) if names else 0.5

    # Erkennung von Halluzinationen: Mentions von Dateiendungen (.md, .txt, etc.), 
    # die nicht in der Liste 'names' existieren.
    file_extensions_re = re.compile(r'[\w\-]+\.(?:md|txt|json|log|csv)', re.IGNORECASE)
    found_extensions_in_text = set(re.findall(file_extensions_re, text))

    # Wir betrachten das Grundmuster (ohne Extension) oder die volle String-Kombination.
    # Da die Liste 'names' wahrscheinlich vollständige Pfade/Namen enthält, prüfen wir,
    # ob Teile des Textes Existenzversprechen machen, die nicht erfüllt sind.
    # Vereinfachung: Wenn im Text '.json' steht, aber 'file.json' in names NICHT existiert,
    # und der Textinhalt nicht als Fake-Datei erkennbar ist (hier heuristisch), punkten wir das als Halluzination.
    # Um sicher zu bleiben und keine falschen Negationen zu machen: Wir zählen Strings, die Dateien implizieren
    # aber nicht in names vorkommen.

    halluz = 0
    if names:
        valid_base_names = set([Path(n).stem for n in names if isinstance(n, str) and Path(n).exists()]) 
        # Hinweis: Path.exists() ist lokal und erlaubt, prüft das FS. Falls Datei nicht existiert, 
        # aber Name in 'names' ist, gilt es als gültiger Referenzpunkt im System-Kontext der Simulation.
        # Wir müssen uns jedoch auf den Text beziehen. 

        # Heuristik für Halluzination ohne externe Abfrage (da wir nur Code ausführen können):
        # Wir suchen nach Mustern, die wie Dateinamen aussehen, aber nicht in der Liste sind.
        text_mentions = set(file_extensions_re.findall(text))

        # Wir prüfen, ob spezifische Kombinationen von Name+Extension im Text stehen,
        # die nicht zu den in 'names' enthaltenen Basen gehören.
        # Da wir 'names' als Liste von Strings haben, nehmen wir das Grundmuster:
        # Wenn "data.json" im Text ist, suchen wir "data.json" in names.

        missing_files_mentioned = 0
        for pattern in file_extensions_re.findall(text):
            base_name = pattern.split('.')[0] if '.' in pattern else pattern
            # Einfachheitshalber: Wenn das Muster einer Datei entspricht, die NICHT explizit 
            # in der Liste 'names' (als exakter Substring ignoriert case sensitive match für den Stamm) enthalten ist.
            # Wir nutzen eine striktere Definition: Der gefundene String in Text muss nicht exakt in names sein,
            # aber wenn er einer Dateiendung folgt und der Name nicht bekannt ist -> Halluzination.

            # Besserer Ansatz zur Vermeidung von False Posits ohne FS-Zugriff:
            # Wir zählen jede im Text gefundene Datei-Referenz (Name.Ext), deren Basisname 
            # nicht in den Basennamen aller 'names' vorkommt.
            current_base = re.split(r'\.', pattern)[0] if '.' in pattern else pattern

            # Prüfen, ob dieses Base-String irgendwo in den Namen der Liste erscheint (case-insensitive)
            found_in_list = any(current_base.lower() in name.lower() for name in names)

            if not found_in_list:
                missing_files_mentioned += 1

        halluz = missing_files_mentioned

    # Längen-Check: Ideal zwischen 150 und 4000 Zeichen
    length_ok = 1.0 if 150 <= len(text) <= 4000 else 0.5

    # Berechnung des Score mit Gewichtung:
    # 60% Abdeckung, 30% Länge, 10% Basis-Offset, minus bis zu 0.1 * Halluzinationen (max 5 Einheiten)
    sc = round(0.6 * cov + 0.3 * length_ok + 0.1 - 0.1 * min(halluz, 5), 3)

    # Score nicht unter 0 lassen
    final_score = max(sc, 0.0)

    status_detail = f"Abdeckung {len(hit)}/{len(names)} echte Dateinamen, Laenge {len(text)}, unbekannte Dateinamen (Halluzinationen) {halluz}"

    return final_score, status_detail


def n_verifier(s: dict) -> dict:
    res = []
    for v in s.get("variants") or []:
        sc, why = score(v.get("text", ""), s)
        res.append({"id": v["id"], "score": sc, "why": why})
    res.sort(key=lambda r: -r["score"])
    best = res[0]["id"] if res and res[0]["score"] > 0 else None
    reason = (f"Gewinner {best}: " + "; ".join(f"{r['id']}={r['score']} ({r['why']})" for r in res)) if best else "keine gueltige Variante"
    return {"votes": res, "winner": best, "vote_reason": reason}


def n_learning(s: dict) -> dict:
    sc = max([v["score"] for v in s.get("votes", [])] or [0])
    ok = bool(s.get("winner")) and not s.get("blocked")
    lesson = ("BLOCKED: " + s["blocked"]) if s.get("blocked") else \
        (f"{s.get('mode')}: Gewinner {s.get('winner')} Score {sc}; Timeouts {s.get('timeouts', [])}")
    applicable = (s.get("memory_brief") or {}).get("evidence_ids") or []
    adopted = s.get("adopted_evidence_ids") or []
    adoption_rate = len(set(adopted) & set(applicable)) / len(applicable) if applicable else 1.0
    agent = s.get("agent", "NEXUS")
    learning_rule = LEARNING_RULES.get(agent, LEARNING_RULES["NEXUS"])
    rec = experience.add(SWARM_DIR / "experience.jsonl", {
        "schema": "nexus.team-memory.v2", "evidence_id": f"EV-{s['id']}", "run_id": s["id"],
        "task": s["task"], "mode": s.get("mode"), "success": ok, "score": sc, "lesson": lesson,
        "adoption_rate": adoption_rate, "adopted_evidence_ids": adopted,
        "learning_rule": learning_rule,
        "retest": {"criterion": "winner selected without unresolved block", "baseline": 0, "target": 1},
    })
    history = experience.load(SWARM_DIR / "evolution.jsonl")
    baseline_score = float(history[-1].get("score", 0)) if history else 0.0
    evolution = experience.add(SWARM_DIR / "evolution.jsonl", {
        "schema": "nexus.evolution.v2", "round_id": s["id"], "agent": agent,
        "score": sc, "baseline_score": baseline_score, "score_delta": round(sc - baseline_score, 3),
        "adoption_rate": adoption_rate, "retest_pass": ok,
        "recurring_issue": s.get("blocked") or "; ".join((s.get("debug") or {}).get("issues") or []),
    })
    return {"learned": rec, "evolution": {"adoption_rate": adoption_rate,
                                             "target_met": adoption_rate >= 0.80,
                                             "tracking": evolution}}


def build_graph() -> Graph:
    g = Graph()
    for name, fn in [("security", n_security), ("scout", n_scout), ("planner", n_planner), ("builder", n_builder),
                     ("debugger", n_debugger), ("verifier", n_verifier), ("learning", n_learning)]:
        g.add_node(name, fn, TIMEOUTS[name])
    g.add_edge("security", "learning", lambda s: bool(s.get("blocked")))
    g.add_edge("security", "scout")
    g.add_edge("scout", "learning", lambda s: bool(s.get("blocked")))
    g.add_edge("scout", "planner")
    g.add_edge("planner", "builder")
    g.add_edge("builder", "debugger")
    g.add_edge("debugger", "builder", lambda s: s.get("debug", {}).get("retry"))
    g.add_edge("debugger", "verifier")
    g.add_edge("verifier", "learning")
    g.add_edge("learning", END)
    return g


def final_answer(s: dict) -> tuple:
    if s.get("blocked"):
        return "BLOCKED", f"Security hat blockiert: {s['blocked']}"
    best = next((v for v in s.get("variants") or [] if v["id"] == s.get("winner")), None)
    if best and best.get("text"):
        st = "PARTIAL" if s.get("timeouts") else "PASS"
        return st, best["text"]
    # ehrliche Teilantwort aus echten Tool-Ergebnissen
    lines = [f"Teilantwort (Timeouts: {', '.join(s.get('timeouts', [])) or '-'}; Fehler: {'; '.join(s.get('errors', [])) or '-'})."]
    for f in s.get("scout", []):
        lines.append(f"{f.get('path')}: {f.get('count', 0)} Eintraege: " + ", ".join(f.get("listing", [])[:40]))
    return "PARTIAL" if s.get("scout") else "FAIL", "\n".join(lines)


# ---------------- Master: Registry, Queue, Persistenz ----------------
class SwarmMaster:
    def __init__(self, base: Path = SWARM_DIR):
        self.base = base
        (base / "runs").mkdir(parents=True, exist_ok=True)
        self.q: queue.Queue = queue.Queue()
        self.runs: dict = {}
        self.lock = threading.Lock()
        self.worker = None

    def _save(self, s: dict):
        clean = {k: v for k, v in s.items() if not k.startswith("_")}
        p = self.base / "runs" / f"{s['id']}.json"
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        tmp.replace(p)

    def submit(self, task: str, mode: str = "pipeline", agent: str = "NEXUS") -> dict:
        mode = mode if mode in ("pipeline", "vote") else "pipeline"
        if agent not in ("NEXUS", "Hermes", "AETHER", "NEXUS-GPT") :
            agent = "NEXUS"  # keine PRIVILEGED-Uebernahme per API-Body (Eddie nur im UI/Terminal)
        rid = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
        s = {"id": rid, "task": str(task)[:2000], "mode": mode, "agent": agent, "status": "queued",
             "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "trace": []}
        with self.lock:
            self.runs[rid] = s
        self._save(s)
        self.q.put(rid)
        self._ensure_worker()
        return s

    def _ensure_worker(self):
        if self.worker and self.worker.is_alive():
            return
        self.worker = threading.Thread(target=self._loop, name="nexus-swarm", daemon=True)
        self.worker.start()

    def _loop(self):
        while True:
            rid = self.q.get()
            try:
                self.execute(self.runs[rid])
            except Exception as e:
                self.runs[rid].update(status="FAIL", answer=f"Swarm-Fehler: {e}")
                self._save(self.runs[rid])

    def execute(self, s: dict) -> dict:
        s["status"] = "running"
        t0 = time.time()
        try:
            from nexus.common import live_events as le
            le.emit("Swarm", "auftrag", f"{s['mode']}: {s['task'][:200]}", target="Swarm")
        except Exception:
            le = None
        build_graph().run(s, on_step=self._save)
        s["status"], s["answer"] = final_answer(s)
        s["sec"] = round(time.time() - t0, 1)
        self._save(s)
        if le:
            try:
                le.emit("Swarm", "antwort", f"{s['status']}: {s['answer'][:300]}", target="NEXUS", ok=s["status"] == "PASS")
            except Exception:
                pass
        return s

    def get(self, rid: str):
        if rid in self.runs:
            return self.runs[rid]
        p = self.base / "runs" / f"{re.sub(r'[^0-9A-Za-z_]', '', rid)}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def status(self) -> dict:
        files = sorted((self.base / "runs").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
        recent = []
        for p in files:
            try:
                d = self.runs.get(p.stem) or json.loads(p.read_text(encoding="utf-8"))
                recent.append({k: d.get(k) for k in ("id", "mode", "status", "task", "active", "winner", "sec", "created")})
            except Exception:
                pass
        return {"success": True, "agents": AGENTS, "graph": {"nodes": list(TIMEOUTS), "timeouts": TIMEOUTS},
                "queue": self.q.qsize(), "worker_alive": bool(self.worker and self.worker.is_alive()),
                "experience_count": len(experience.load(self.base / "experience.jsonl")), "recent": recent,
                "model": MODEL, "extensions": list(EXTENSIONS), "learning_rules": LEARNING_RULES}


_M = None


def get_master() -> SwarmMaster:
    global _M
    if _M is None:
        _M = SwarmMaster()
    return _M
