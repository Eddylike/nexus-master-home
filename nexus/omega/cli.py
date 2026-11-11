"""OMEGA CLI.

Beispiele:
    python -m nexus.omega.cli run "Recherchiere AI-News und poste in Slack"
    python -m nexus.omega.cli resume RUN-... --approval APR-...
    python -m nexus.omega.cli approve APR-...
    python -m nexus.omega.cli tools
    python -m nexus.omega.cli status
    python -m nexus.omega.cli dashboard
"""
from __future__ import annotations

import argparse
import json
import sys

from nexus.omega import config, events, registry, sandbox
from nexus.omega.agent import OmegaAgent


def _print(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _approval_decision(approval_id: str, approve: bool, by: str = "OMEGA-CLI") -> dict:
    from nexus.terminal import guard

    res = guard.decide(approval_id, approve, by=by)
    if res.get("success"):
        events.set_approval_status(approval_id, "approved" if approve else "denied", by)
    return res


def cmd_run(args) -> int:
    result = OmegaAgent().run(args.goal, context=args.context or "", approval_id=args.approval)
    _print(result)
    if result.get("status") == "awaiting_approval":
        print("\nFreigabe noetig. Danach:", file=sys.stderr)
        print(f"  python -m nexus.omega.cli approve {result.get('approval_id')}", file=sys.stderr)
        print(f"  python -m nexus.omega.cli resume {result.get('run_id')} "
              f"--approval {result.get('approval_id')}", file=sys.stderr)
        return 2
    return 0 if result.get("success") else 1


def cmd_resume(args) -> int:
    result = OmegaAgent().resume(args.run_id, approval_id=args.approval)
    _print(result)
    return 0 if result.get("success") else 1


def cmd_approve(args) -> int:
    res = _approval_decision(args.approval_id, not args.deny)
    _print(res)
    return 0 if res.get("success") else 1


def cmd_tools(args) -> int:
    tools = registry.list_tools(tags=args.tag or None, include_schema=args.schema)
    if args.json:
        _print({"count": len(tools), "tools": tools})
    else:
        for tool in tools:
            guard = "approval" if (tool["destructive"] or tool["dynamic_guard"]) else "free"
            print(f"{tool['name']:<18} {tool['permission']:<10} {guard:<9} "
                  f"{tool['description'][:90]}")
    return 0


def cmd_status(_args) -> int:
    data = {
        "agent": config.AGENT_NAME,
        "version": config.OMEGA_VERSION,
        "home": str(config.home()),
        "db": str(config.db_path()),
        "hitl": config.hitl_mode(),
        "tools": len(registry.list_tools()),
        "sandbox": sandbox.status(),
        "secrets_present": sorted(k for k, v in config.secrets().items() if v),
    }
    _print(data)
    return 0


def cmd_runs(args) -> int:
    _print({"runs": events.list_runs(args.limit)})
    return 0


def cmd_dashboard(_args) -> int:
    import subprocess

    from pathlib import Path

    script = Path(__file__).with_name("dashboard.py")
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(script)])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omega", description="OMEGA Agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="Ziel ausfuehren")
    p.add_argument("goal")
    p.add_argument("--context", default="")
    p.add_argument("--approval", default=None, help="bereits erteilte approval_id")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("resume", help="pausierten Run fortsetzen")
    p.add_argument("run_id")
    p.add_argument("--approval", default=None)
    p.set_defaults(func=cmd_resume)

    p = sub.add_parser("approve", help="Freigabe erteilen oder verweigern")
    p.add_argument("approval_id")
    p.add_argument("--deny", action="store_true")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("tools", help="Tools auflisten")
    p.add_argument("--tag", action="append")
    p.add_argument("--schema", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("status", help="Status anzeigen")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("runs", help="letzte Runs anzeigen")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("dashboard", help="Streamlit-Dashboard starten")
    p.set_defaults(func=cmd_dashboard)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
