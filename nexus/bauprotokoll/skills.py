"""Phase 3: Skill-Bibliothek.

Struktur: skills/<name>/skill.json + prompt.md + tests/
Skill-Aenderung = neue Version; die alte Version bleibt unter versions/<version>/ abrufbar.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from nexus.bauprotokoll.config import load_config
from nexus.bauprotokoll.vectors import cosine, hashed_embedding

REQUIRED = ("name", "version", "beschreibung", "benoetigte_tools", "modell", "eingabe", "ausgabe")

STARTER_SKILLS = {
    "code_review": {
        "beschreibung": "Prueft einen Diff auf Bugs, Risiken, Sicherheitsluecken und fehlende Tests.",
        "keywords": ["review", "pruefen", "prüfen", "qualitaet", "qualität", "diff", "code"],
        "benoetigte_tools": ["read_file"],
        "modell": "generalist",
        "eingabe": {"typ": "diff", "pflicht": ["aufgabe", "diff"]},
        "ausgabe": {"typ": "json", "felder": ["befund", "risiken", "empfehlung"]},
        "prompt": "Du bist Reviewer. Pruefe den Diff gegen die Aufgabe. Nenne konkrete Datei/Zeile, Risiko und Fix. Erfinde nichts.",
    },
    "bugfix": {
        "beschreibung": "Findet die Ursache eines Fehlers und liefert einen minimalen, getesteten Fix.",
        "keywords": ["fehler", "bug", "repariere", "fix", "traceback", "exception", "absturz"],
        "benoetigte_tools": ["read_file", "write_file", "run_tests"],
        "modell": "coder",
        "eingabe": {"typ": "text", "pflicht": ["fehler", "code"]},
        "ausgabe": {"typ": "json", "felder": ["ursache", "fix", "test"]},
        "prompt": "Du bist Bugfixer. Reproduziere den Fehler, finde die Ursache, aendere so wenig wie moeglich, liefere einen Test.",
    },
    "html_single_file": {
        "beschreibung": "Erstellt eine eigenstaendige HTML-Datei mit CSS und JavaScript ohne externe Abhaengigkeiten.",
        "keywords": ["html", "single", "datei", "landingpage", "seite", "css", "javascript", "webseite"],
        "benoetigte_tools": ["write_file"],
        "modell": "coder",
        "eingabe": {"typ": "text", "pflicht": ["zweck", "inhalt"]},
        "ausgabe": {"typ": "datei", "felder": ["html"]},
        "prompt": "Du baust eine einzelne HTML-Datei. Kein CDN, kein Build, alles inline. Mobiltauglich und ohne Konsolenfehler.",
    },
    "python_module": {
        "beschreibung": "Schreibt ein sauberes Python-Modul mit Typen, Doku und Tests.",
        "keywords": ["python", "modul", "klasse", "funktion", "paket", "bibliothek"],
        "benoetigte_tools": ["write_file", "run_tests"],
        "modell": "coder",
        "eingabe": {"typ": "text", "pflicht": ["zweck", "schnittstelle"]},
        "ausgabe": {"typ": "datei", "felder": ["modul", "tests"]},
        "prompt": "Du schreibst ein Python-Modul. Standardbibliothek zuerst, Typen, klare Namen, Tests mit unittest/pytest.",
    },
    "recherche": {
        "beschreibung": "Recherchiert ein Thema, sammelt Quellen und liefert ein belegtes Fazit.",
        "keywords": ["recherch", "suche", "quellen", "web", "information", "vergleich", "markt"],
        "benoetigte_tools": ["web_search", "read_file"],
        "modell": "generalist",
        "eingabe": {"typ": "text", "pflicht": ["frage"]},
        "ausgabe": {"typ": "json", "felder": ["quellen", "fazit", "offene_fragen"]},
        "prompt": "Du bist Researcher. Nur belegbare Aussagen, Quelle mit Datum. Keine Schreibrechte.",
    },
    "zusammenfassen": {
        "beschreibung": "Fasst lange Texte kurz und verlustfrei zusammen.",
        "keywords": ["zusammenfass", "summary", "tldr", "tl;dr", "kuerzen", "kürzen", "abstract"],
        "benoetigte_tools": [],
        "modell": "router",
        "eingabe": {"typ": "text", "pflicht": ["text"]},
        "ausgabe": {"typ": "json", "felder": ["kernpunkte", "laenge"]},
        "prompt": "Fasse zusammen: max. 5 Kernpunkte, keine Meinung, keine Erfindungen. Zahlen unveraendert lassen.",
    },
    "test_schreiben": {
        "beschreibung": "Schreibt Tests fuer vorhandenen oder neuen Code und fuehrt sie aus.",
        "keywords": ["test", "pytest", "unittest", "abdeckung", "testfall", "tests"],
        "benoetigte_tools": ["read_file", "write_file", "run_tests"],
        "modell": "coder",
        "eingabe": {"typ": "text", "pflicht": ["ziel", "verhalten"]},
        "ausgabe": {"typ": "datei", "felder": ["testdatei", "ergebnis"]},
        "prompt": "Schreibe Tests, die echtes Verhalten pruefen. Kein Assert auf Implementierungsdetails. Erst rot, dann gruen.",
    },
    "doku_schreiben": {
        "beschreibung": "Schreibt README, Docstrings oder Betriebsdokumentation.",
        "keywords": ["doku", "dokumentation", "readme", "docstring", "anleitung", "handbuch"],
        "benoetigte_tools": ["read_file", "write_file"],
        "modell": "generalist",
        "eingabe": {"typ": "text", "pflicht": ["zielgruppe", "inhalt"]},
        "ausgabe": {"typ": "datei", "felder": ["markdown"]},
        "prompt": "Dokumentiere knapp und korrekt. Befehle copy-paste-faehig, keine ungeprueften Behauptungen.",
    },
}


STARTER_ACTIONS = {
    "code_review": ["pruefe", "prüfe", "reviewe", "bewerte", "review"],
    "bugfix": ["repariere", "behebe", "korrigiere", "fixe", "debugge", "bug", "fehler", "absturz"],
    "html_single_file": ["baue", "erstelle eine seite", "erzeuge html", "landingpage"],
    "python_module": ["schreibe ein python", "implementiere modul", "python-modul"],
    "recherche": ["recherchiere", "suche nach", "finde quellen"],
    "zusammenfassen": ["fasse", "zusammenfassen", "kuerze", "kürze"],
    "test_schreiben": ["schreibe test", "schreibe pytest", "teste", "testschreiben"],
    "doku_schreiben": ["dokumentiere", "schreibe doku", "readme", "handbuch"],
}


def _skill_text(meta: dict) -> str:
    parts = [meta.get("beschreibung", ""), " ".join(meta.get("keywords") or [])]
    return " ".join(parts)


class SkillLibrary:
    def __init__(self, skills_dir: str | Path | None = None, config=None):
        cfg = config or load_config()
        self.dir = Path(skills_dir) if skills_dir else cfg.path_for("paths.skills")
        self.dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, dict]:
        skills = {}
        for meta_path in sorted(self.dir.glob("*/skill.json")):
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if "benötigte_tools" in data and "benoetigte_tools" not in data:
                data["benoetigte_tools"] = data.pop("benötigte_tools")
            missing = [k for k in REQUIRED if k not in data]
            if missing:
                continue
            data["_path"] = str(meta_path.parent)
            data["_prompt"] = self._read_prompt(meta_path.parent)
            skills[data["name"]] = data
        return skills

    @staticmethod
    def _read_prompt(folder: Path) -> str:
        p = folder / "prompt.md"
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""

    def get(self, name: str) -> dict | None:
        return self.load().get(name)

    def select(self, task: str, top_k: int = 3) -> list[dict]:
        qvec = hashed_embedding(task)
        low = (task or "").lower()
        ranked = []
        for name, meta in self.load().items():
            sim = cosine(qvec, hashed_embedding(_skill_text(meta)))
            keywords = meta.get("keywords") or []
            hits = sum(1 for k in keywords if k.lower() in low)
            hit_ratio = hits / max(1, len(keywords))
            actions = meta.get("aktionen") or STARTER_ACTIONS.get(name, [])
            action_hits = sum(1 for a in actions if a.lower() in low)
            name_hit = 0.2 if name.replace("_", " ") in low else 0.0
            score = round(
                0.5 * sim + 0.35 * hit_ratio + 0.3 * min(3, action_hits) + name_hit, 4
            )
            ranked.append({"name": name, "score": score, "skill": meta})
        ranked.sort(key=lambda r: r["score"], reverse=True)
        return ranked[:top_k]

    def save(self, meta: dict, prompt: str = "", test: dict | None = None) -> Path:
        missing = [k for k in REQUIRED if k not in meta]
        if missing:
            raise ValueError(f"Skill-Felder fehlen: {missing}")
        folder = self.dir / meta["name"]
        folder.mkdir(parents=True, exist_ok=True)
        clean = {k: v for k, v in meta.items() if not k.startswith("_")}
        if (folder / "skill.json").is_file():
            old = json.loads((folder / "skill.json").read_text(encoding="utf-8"))
            old_version = str(old.get("version", "0"))
            old_prompt = self._read_prompt(folder)
            if old_version == str(clean["version"]) and (old != clean or old_prompt != prompt):
                raise ValueError(
                    f"Version {old_version} existiert bereits. Fuer Aenderungen Version erhoehen."
                )
            archive = folder / "versions" / old_version
            archive.mkdir(parents=True, exist_ok=True)
            for name in ("skill.json", "prompt.md"):
                src = folder / name
                if src.is_file():
                    shutil.copy2(src, archive / name)
        (folder / "skill.json").write_text(
            json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (folder / "prompt.md").write_text(prompt or "", encoding="utf-8")
        tests = folder / "tests"
        tests.mkdir(exist_ok=True)
        if test is not None:
            (tests / "contract.json").write_text(
                json.dumps(test, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
        return folder

    def ensure_starters(self) -> list[str]:
        created = []
        for name, spec in STARTER_SKILLS.items():
            folder = self.dir / name
            if (folder / "skill.json").is_file():
                continue
            meta = {
                "name": name,
                "version": "1.0.0",
                "beschreibung": spec["beschreibung"],
                "keywords": spec["keywords"],
                "aktionen": STARTER_ACTIONS.get(name, []),
                "benoetigte_tools": spec["benoetigte_tools"],
                "modell": spec["modell"],
                "eingabe": spec["eingabe"],
                "ausgabe": spec["ausgabe"],
            }
            test = {
                "eingabe": {"typ": spec["eingabe"]["typ"]},
                "erwartete_ausgabe_felder": spec["ausgabe"]["felder"],
            }
            self.save(meta, spec["prompt"], test)
            created.append(name)
        return created

    def stats(self) -> dict:
        skills = self.load()
        return {"count": len(skills), "names": sorted(skills)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NEXUS Skill-Bibliothek")
    ap.add_argument("--init", action="store_true", help="8 Start-Skills anlegen")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--select", metavar="AUFGABE")
    ap.add_argument("--skills-dir", default=None)
    a = ap.parse_args(argv)
    lib = SkillLibrary(a.skills_dir)
    if a.init:
        print("angelegt:", ", ".join(lib.ensure_starters()) or "nichts")
    if a.select:
        for hit in lib.select(a.select, top_k=5):
            print(f"{hit['score']:.3f} {hit['name']}")
    if a.list or not (a.init or a.select):
        data = lib.stats()
        print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
