"""Phase 6: Self-Build-Loop.

Ablauf: Backlog -> Tag/Branch -> Coder (nur Erlaubnisliste) -> Validator ->
PASS: staging (Beobachtung) / FAIL: Rollback + Memory + zurueck ins Backlog.
Not-Aus: Datei STOP im Root stoppt sofort.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from nexus.bauprotokoll.agents import Coder, run_validator
from nexus.bauprotokoll.config import load_config

RISK = {"niedrig": 0, "low": 0, "mittel": 1, "medium": 1, "hoch": 2, "high": 2}


class SelfBuild:
    def __init__(self, root=None, config=None, memory=None, coder=None,
                 validator=None, git_runner=None, implement=None, state_path=None):
        self.cfg = config or load_config()
        self.root = Path(root or self.cfg.root).resolve()
        self.memory = memory
        self.coder = coder or Coder(self.root, self.cfg.get("forbidden_paths"))
        self.validator = validator or (lambda task, files: run_validator(task, self.root))
        self.implement = implement
        self._git_runner = git_runner or self._default_git
        self.stop_file = self.cfg.get("selfbuild.stop_file", "STOP")
        self.stages = self.cfg.get("selfbuild.stage_dirs", [["docs/*", "tests/*"]])
        self.observe_runs = int(self.cfg.get("selfbuild.observe_runs", 3))
        self.successes_per_stage = int(self.cfg.get("selfbuild.successes_per_stage", 20))
        self.state_file = Path(state_path or (self.cfg.state_dir / "selfbuild.json"))

    # ----------------------------------------------------------------- infra
    def _default_git(self, args):
        p = subprocess.run(["git", *args], cwd=str(self.root), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        return p.returncode, (p.stdout + p.stderr).strip()

    def git(self, *args):
        return self._git_runner(list(args))

    def stop_requested(self) -> bool:
        return (self.root / self.stop_file).exists()

    def load_state(self) -> dict:
        default = {"stage": 0, "successes": 0, "successes_since_incident": 0,
                   "staged_successes": 0, "items": {}}
        if self.state_file.is_file():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                default.update(data)
            except (OSError, ValueError):
                pass
        return default

    def save_state(self, state: dict) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")

    # -------------------------------------------------------------- backlog
    def load_backlog(self) -> list[dict]:
        path = self.root / "backlog.json"
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig") or "[]")
        except ValueError:
            return []
        if isinstance(data, dict):
            data = data.get("items", [])
        return [i for i in data if isinstance(i, dict) and i.get("id")]

    def pick_backlog(self, state=None) -> dict | None:
        state = state or self.load_state()
        open_items = [i for i in self.load_backlog()
                      if state["items"].get(i["id"], {}).get("status") not in ("done", "rejected")]
        if not open_items:
            return None
        open_items.sort(key=lambda i: (RISK.get(str(i.get("risiko", "hoch")).lower(), 2),
                                       -int(i.get("prioritaet", 0))))
        return open_items[0]

    # --------------------------------------------------------------- stages
    def stage_paths(self, stage: int | None = None) -> list[str]:
        stage = int(stage if stage is not None else self.load_state()["stage"])
        stage = max(0, min(stage, len(self.stages) - 1))
        return list(self.stages[stage])

    def stage_allows(self, allowed_paths, stage=None) -> bool:
        from nexus.bauprotokoll.agents import _matches

        patterns = self.stage_paths(stage)
        return all(_matches(p, patterns) for p in (allowed_paths or []))

    # ---------------------------------------------------------------- abort
    def _abort_report(self, task_id, reason):
        return {"task_id": task_id, "status": "FAIL",
                "steps": [{"step": "selfbuild", "result": "FAIL", "detail": reason}]}

    # ----------------------------------------------------------------- run
    def run_once(self, dry_run: bool = True) -> dict:
        if self.stop_requested():
            return {"status": "STOPPED", "reason": f"{self.stop_file} vorhanden"}

        state = self.load_state()
        item = self.pick_backlog(state)
        if item is None:
            return {"status": "EMPTY", "reason": "Kein offener Backlog-Eintrag"}

        stage = int(state["stage"])
        allowed = list(item.get("allowed_paths") or self.stage_paths(stage))
        if not self.stage_allows(allowed, stage):
            return {"status": "REJECTED", "item": item["id"],
                    "reason": f"Pfade ausserhalb von Stufe {stage}: {allowed}"}

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        tag = f"pre-selfbuild-{stamp}"
        branch = f"selfbuild/{item['id']}-{stamp}"
        rc, base = self.git("rev-parse", "--abbrev-ref", "HEAD")
        base = base if rc == 0 and base and base != "HEAD" else "HEAD"

        if dry_run:
            return {"status": "DRY_RUN", "item": item, "stage": stage, "allowed": allowed,
                    "branch": branch, "tag": tag,
                    "allowed_ok": True}

        if self.implement is None:
            return {"status": "FAIL", "item": item["id"],
                    "reason": "Keine Implementierung (implement-Callback) gesetzt"}

        files = self.implement(item)
        if not files:
            return {"status": "FAIL", "item": item["id"], "reason": "Keine Dateien geliefert"}

        self.git("tag", "-f", tag, "HEAD")
        rc, out = self.git("checkout", "-B", branch, base)
        if rc != 0:
            return {"status": "FAIL", "item": item["id"], "reason": f"git checkout: {out}"}

        written = self.coder.apply(files, allowed)
        self.git("add", "-A")
        self.git("commit", "-m", f"selfbuild: {item['id']} {item.get('titel', '')}".strip())

        task = {
            "task_id": item["id"], "base_ref": tag, "allowed_paths": allowed,
            "require_tests": True,
        }
        report = self.validator(task, files)

        if report.get("status") == "PASS":
            return self._on_pass(state, item, report, base, branch, written)
        return self._on_fail(state, item, report, base, branch, tag)

    def _on_pass(self, state, item, report, base, branch, written):
        self.git("checkout", "-B", "staging", base)
        rc, out = self.git("merge", "--no-ff", branch, "-m", f"selfbuild: merge {item['id']}")
        if rc != 0:
            self.git("checkout", "-f", base)
            return {"status": "FAIL", "item": item["id"], "reason": f"merge staging: {out}"}
        for target in (base,):
            self.git("checkout", target)
        self.git("branch", "-D", branch)

        state["items"][item["id"]] = {"status": "staging", "branch": branch,
                                      "report": report.get("status")}
        state["successes"] = int(state.get("successes", 0)) + 1
        state["successes_since_incident"] = int(state.get("successes_since_incident", 0)) + 1
        state["staged_successes"] = int(state.get("staged_successes", 0)) + 1
        promoted = False
        if state["staged_successes"] >= self.observe_runs:
            promoted = self._promote_main(base)
            if promoted:
                state["staged_successes"] = 0
                state["items"][item["id"]]["status"] = "done"
        if state["successes_since_incident"] >= self.successes_per_stage:
            state["stage"] = min(int(state["stage"]) + 1, len(self.stages) - 1)
            state["successes_since_incident"] = 0
        self.save_state(state)
        self._remember(report)
        return {"status": "PASS", "item": item["id"], "report": report,
                "written": written, "promoted_main": promoted, "stage": state["stage"]}

    def _on_fail(self, state, item, report, base, branch, tag):
        self.git("checkout", "-f", base)
        self.git("reset", "--hard", tag)
        self.git("branch", "-D", branch)
        state["items"][item["id"]] = {"status": "backlog",
                                      "error": (report.get("steps") or [{}])[-1].get("detail", "")}
        state["successes_since_incident"] = 0
        state["staged_successes"] = 0
        self.save_state(state)
        self._remember(report)
        return {"status": "FAIL", "item": item["id"], "report": report,
                "rolled_back": True}

    def _promote_main(self, base):
        rc, main = self.git("rev-parse", "--verify", "main")
        if rc != 0:
            return False
        rc, _ = self.git("checkout", "main")
        if rc != 0:
            return False
        rc, _ = self.git("merge", "--ff-only", "staging")
        self.git("checkout", base)
        return rc == 0

    def _remember(self, report):
        if self.memory is not None and report.get("status") in ("PASS", "FAIL"):
            try:
                self.memory.record_validation(report)
            except Exception:
                pass

    def run_loop(self, iterations: int | None = None, dry_run: bool = True,
                 sleep: float | None = None):
        results = []
        n = 0
        while iterations is None or n < iterations:
            if self.stop_requested():
                results.append({"status": "STOPPED"})
                break
            res = self.run_once(dry_run=dry_run)
            results.append(res)
            n += 1
            if res["status"] in ("EMPTY", "STOPPED"):
                break
            time.sleep(self.cfg.get("selfbuild.interval_s", 900) if sleep is None else sleep)
        return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NEXUS Self-Build")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--run", action="store_true", help="wirklich ausfuehren (sonst dry-run)")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args(argv)
    sb = SelfBuild()
    if a.status:
        print(json.dumps({"stop": sb.stop_requested(), "state": sb.load_state(),
                          "backlog": [i["id"] for i in sb.load_backlog()]},
                         indent=2, ensure_ascii=False))
        return 0
    result = sb.run_once(dry_run=not a.run)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result["status"] in ("DRY_RUN", "PASS", "STOPPED", "EMPTY") else 1


if __name__ == "__main__":
    sys.exit(main())
