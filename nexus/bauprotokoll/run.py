"""End-to-End-Orchestrierung: Master Brain + Agenten + Validator + Memory.

Beispiel:
  python -m nexus.bauprotokoll.run "Repariere den Fehler" --allowed-paths "skills/*" --dry-run
  python -m nexus.bauprotokoll.run "..." --allowed-paths "skills/*" --execute
"""
from __future__ import annotations

import argparse
import json
import sys

from nexus.bauprotokoll.agents import (TeamPipeline, build_default, llm_implementer,
                                      run_validator)
from nexus.bauprotokoll.brain import MasterBrain
from nexus.bauprotokoll.config import load_config
from nexus.bauprotokoll.memory import Memory
from nexus.bauprotokoll.models import ModelManager
from nexus.bauprotokoll.skills import SkillLibrary


def build(root=None, config=None, memory=None):
    cfg = config or load_config()
    memory = memory or Memory(config=cfg)
    skills = SkillLibrary(config=cfg)
    models = ModelManager(config=cfg)
    agents = build_default(root or cfg.root, cfg)
    validator = lambda plan, files: run_validator(
        {"task_id": plan["task_id"], "base_ref": plan.get("base_ref", "HEAD"),
         "allowed_paths": plan.get("allowed_paths") or [], "require_tests": True},
        cfg.root,
    )
    pipeline = TeamPipeline(
        agents["coder"], agents["tester"], agents["reviewer"], validator,
        memory=memory,
        max_attempts=cfg.get("budgets.max_attempts", 3),
        task_timeout_s=cfg.get("budgets.task_timeout_s", 900),
    )
    brain = MasterBrain(config=cfg, memory=memory, skills=skills, models=models)
    return brain, pipeline, models, memory


def run_task(task: str, allowed_paths, execute: bool = False, task_id=None,
             base_ref: str = "HEAD", brain_pipeline=None):
    brain, pipeline, models, memory = brain_pipeline or build()
    plan = brain.plan(task, task_id, allowed_paths)
    plan["base_ref"] = base_ref
    if not execute:
        return {"plan": plan, "state": "PLANNED"}
    implement = llm_implementer(models)
    report = pipeline.run(plan, implement=implement)
    state = brain.finish(report["report"])
    return {"plan": plan, "report": report, "state": state.value}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NEXUS Bauprotokoll End-to-End")
    ap.add_argument("aufgabe")
    ap.add_argument("--allowed-paths", nargs="+", required=True)
    ap.add_argument("--base-ref", default="HEAD")
    ap.add_argument("--task-id", default=None)
    ap.add_argument("--execute", action="store_true", help="ohne dieses Flag nur planen")
    a = ap.parse_args(argv)
    out = run_task(a.aufgabe, a.allowed_paths, execute=a.execute,
                   task_id=a.task_id, base_ref=a.base_ref)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0 if out.get("state") in ("PLANNED", "DONE") else 1


if __name__ == "__main__":
    sys.exit(main())
