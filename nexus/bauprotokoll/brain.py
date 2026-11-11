"""Phase 4: Master Brain.

Ablauf: verstehen -> Memory -> zerlegen (max. 7) -> Skills/Agenten zuordnen ->
ausfuehren (Pipeline) -> Validator entscheidet -> Ergebnis ins Memory.
"""
from __future__ import annotations

import json
import re
import sys
import uuid

from nexus.bauprotokoll.config import load_config
from nexus.bauprotokoll.models import ModelManager
from nexus.bauprotokoll.skills import SkillLibrary
from nexus.bauprotokoll.states import TaskState, state_from_report

SKILL_AGENT = {
    "code_review": "reviewer",
    "bugfix": "coder",
    "html_single_file": "coder",
    "python_module": "coder",
    "recherche": "researcher",
    "zusammenfassen": "generalist",
    "test_schreiben": "tester",
    "doku_schreiben": "generalist",
}

KIND_AGENT = {
    "bugfix": "coder",
    "code": "coder",
    "test": "tester",
    "review": "reviewer",
    "recherche": "researcher",
    "doku": "generalist",
    "allgemein": "generalist",
}

_SPLIT = re.compile(r"\s*(?:;\s*|\.\s+|\?+\s*|!\s+|\bund\b|\bdann\b|\banschliessend\b)\s*", re.IGNORECASE)


class MasterBrain:
    def __init__(self, config=None, memory=None, skills=None, models=None):
        self.cfg = config or load_config()
        self.memory = memory
        self.skills = skills or SkillLibrary(config=self.cfg)
        self.models = models or ModelManager(config=self.cfg)
        self.max_steps = int(self.cfg.get("budgets.max_steps", 7))

    # ------------------------------------------------------------ planning
    def understand(self, task: str) -> dict:
        low = (task or "").lower()
        if any(w in low for w in ("fehler", "bug", "fix", "repariere", "traceback")):
            kind = "bugfix"
        elif any(w in low for w in ("recherch", "quelle", "vergleich")):
            kind = "recherche"
        elif any(w in low for w in ("test", "pytest", "unittest")):
            kind = "test"
        elif any(w in low for w in ("review", "pruefe", "prüfe", "diff")):
            kind = "review"
        elif any(w in low for w in ("doku", "readme", "dokumentation")):
            kind = "doku"
        elif any(w in low for w in ("code", "python", "html", "modul", "funktion", "implementier")):
            kind = "code"
        else:
            kind = "allgemein"
        words = len(re.findall(r"\w+", low))
        conjunctions = len(re.findall(r"\b(und|dann|anschliessend)\b", low))
        return {
            "ziel": (task or "").strip(),
            "art": kind,
            "komplexitaet": min(5, 1 + words // 25 + conjunctions),
            "modell": self.models.classify(task),
        }

    def decompose(self, task: str) -> list[str]:
        parts = [p.strip(" .;:") for p in _SPLIT.split(task or "") if p and p.strip(" .;:")]
        if not parts:
            parts = [(task or "").strip()]
        if len(parts) > self.max_steps:
            head = parts[: self.max_steps - 1]
            tail = " und ".join(parts[self.max_steps - 1:])
            parts = head + [tail]
        return parts[: self.max_steps]

    def plan(self, task: str, task_id: str | None = None,
             allowed_paths: list[str] | None = None) -> dict:
        goal = self.understand(task)
        steps = []
        for nr, text in enumerate(self.decompose(task), start=1):
            hit = self.skills.select(text, top_k=1)
            skill = hit[0]["name"] if hit else ""
            agent = SKILL_AGENT.get(skill) or KIND_AGENT.get(goal["art"], "generalist")
            steps.append({"nr": nr, "text": text, "skill": skill, "agent": agent})
        memory = {}
        if self.memory is not None:
            memory = self.memory.context_for(task)
        return {
            "task_id": task_id or f"NX-{uuid.uuid4().hex[:8]}",
            "goal": goal,
            "skills": [s["skill"] for s in steps if s["skill"]],
            "steps": steps,
            "allowed_paths": allowed_paths or [],
            "memory": memory,
        }

    # ----------------------------------------------------------- execution
    def run(self, task: str, pipeline=None, task_id: str | None = None,
            allowed_paths: list[str] | None = None) -> dict:
        plan = self.plan(task, task_id, allowed_paths)
        if pipeline is None:
            return {"plan": plan, "state": TaskState.PLANNED.value}
        report = pipeline.run(plan)
        state = self.finish(report)
        return {"plan": plan, "report": report, "state": state.value}

    def finish(self, report: dict) -> TaskState:
        """Nur ein Validator-Report (PASS/FAIL) darf den Task abschliessen."""
        state = state_from_report(report)
        if self.memory is not None:
            self.memory.record_validation(report)
            offen = "" if state == TaskState.DONE else "Fehlerbericht pruefen"
            self.memory.set_project_state(
                "NEXUS",
                f"Task {report.get('task_id', '?')}: {report.get('status')}",
                offen,
            )
        return state


def main(argv=None) -> int:
    ap_args = argv if argv is not None else sys.argv[1:]
    if not ap_args:
        print("Nutzung: python -m nexus.bauprotokoll.brain \"Aufgabe\" [--json]")
        return 2
    as_json = "--json" in ap_args
    task = " ".join(a for a in ap_args if a != "--json")
    from nexus.bauprotokoll.memory import Memory

    brain = MasterBrain(memory=Memory())
    plan = brain.plan(task)
    if as_json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        print(f"Ziel: {plan['goal']['ziel']} ({plan['goal']['art']})")
        for step in plan["steps"]:
            print(f"  {step['nr']}. [{step['agent']}/{step['skill']}] {step['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
