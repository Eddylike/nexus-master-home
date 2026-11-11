"""Erfahrungsgedaechtnis: workspace/swarm/experience.jsonl mit Bewertung + Aehnlichkeitssuche."""
from __future__ import annotations
import json, re, time, threading
from pathlib import Path

_lock = threading.Lock()


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-zA-Z0-9äöüß_]{3,}", (s or "").lower())}


def add(path: Path, rec: dict) -> dict:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load(path: Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def similar(path: Path, task: str, k: int = 3) -> list:
    """Vergleicht die aktuelle Aufgabe mit Erfahrungen aus experience.jsonl, um relevante Lessons als Backlog-Vorschlag zu generieren.

    Untersucht die Eingabe auf existierende Erfahrungen, extrahiert Aufgabenbeschreibungen und Punktzahlen, berechnet eine Übereinstimmungsrate
    basierend auf gemeinsamen Wörtern (Jaccard-Similität) und sortiert Ergebnisse nach Relevanz und Score.
    Gibt bis zu k Vorschläge zurück, die als Backlog für die Phase 5 Selbstverbesserung dienen können.
    """
    # Validierung der Eingabepfade
    if not path or not isinstance(path, Path):
        raise ValueError("path must be a valid Path object or None")

    if path and not path.exists():
        return []

    # Initialisierung von Ergebniszusammenstellungen
    scored = []

    # Laden der Erfahrungendaten, falls verfügbar
    experience_data = []
    if path and path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        try:
                            entry = json.loads(line)
                            experience_data.append(entry)
                        except json.JSONDecodeError as e:
                            # Ignorieren ungültiger Zeilen, um den Prozess fortzusetzen
                            continue
        except Exception as e:
            # Bei Lese-Fehlern die Erfahrung als leer behandeln
            pass

    # Extraktion von Wörtern für die aktuelle Aufgabe (vereinfachte Tokenisierung ohne externe Bibliotheken)
    def get_words(text: str) -> set:
        if not text or not isinstance(text, str):
            return set()
        # Normalisierung und Aufteilung in Wörter
        normalized = re.sub(r'[^\w\s]', '', text).lower()
        words = re.split(r'\s+', normalized.strip())
        return set(words)

    q_words = get_words(task)

    if not q_words:
        return []

    # Iteration über die geladenen Erfahrungseinträge
    for entry in experience_data:
        # Sicherstellen, dass der 'task'-Key existiert, um KeyError zu vermeiden
        task_entry_text = entry.get('task', "")
        r_words = get_words(task_entry_text)

        if not r_words:
            continue

        # Berechnung der Jaccard-Ähnlichkeit
        intersection = q_words & r_words
        union = q_words | r_words

        if not union:
            continue

        sim = len(intersection) / len(union)

        # Abruf des Scores mit einem Default-Wert, um Fehlersicherheit zu gewährleisten
        score_value = entry.get('score', 0)
        if not isinstance(score_value, (int, float)):
            try:
                score_value = float(score_value)
            except (TypeError, ValueError):
                score_value = 0.0

        # Kombination von Ähnlichkeit und Score für die Sortierung
        combined_score = sim + 0.1 * float(score_value)

        if combined_score > 0:
            scored.append((combined_score, sim, entry))

    # Sortieren nach der kombinierten Punktzahl (absteigend)
    scored.sort(key=lambda x: -x[0])

    # Auswahl der Top-k Ergebnisse und Formung der Ausgabe
    if not scored or k <= 0:
        return []

    results = []
    for combined_score, similarity, r in scored[:k]:
        # Sicherstellen, dass alle erforderlichen Keys vorhanden sind
        entry_copy = {**r}
        entry_copy["similarity"] = round(similarity, 2)
        results.append(entry_copy)

    return results


def memory_brief(path: Path, task: str, k: int = 3) -> dict:
    """Build a bounded, traceable brief that must enter the next planning round."""
    evidence = similar(path, task, k)
    evidence_ids = [str(item.get("evidence_id") or item.get("run_id"))
                    for item in evidence if item.get("evidence_id") or item.get("run_id")]
    return {
        "schema": "nexus.team-memory-brief.v2",
        "applicable_evidence": evidence,
        "evidence_ids": evidence_ids,
        "adoption_target": 0.80,
    }
