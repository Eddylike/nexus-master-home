"""Health-Uebersicht fuer alle Dienste (Master Home, Bridge, Ollama).

Aufruf:
  python -m nexus.bauprotokoll.health [--json] [--timeout 2]

Exit-Code: 0 = alle Dienste UP, 1 = mindestens ein Dienst DOWN.
HTTP 401/403 zaehlt als UP (Dienst antwortet, nur Auth fehlt).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from nexus.bauprotokoll.config import Config, load_config


def _get(cfg, dotted: str, default=None):
    if isinstance(cfg, dict):
        node = cfg
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node
    return cfg.get(dotted, default)


def _is_up(code: int) -> bool:
    # 2xx = gesund. 401/403 = Dienst laeuft, nur Auth fehlt. 404/5xx = nicht gesund.
    return 200 <= code < 300 or code in (401, 403)


def probe(url: str, timeout: float = 2.0) -> dict:
    started = time.perf_counter()
    status, code, err = "DOWN", None, None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            code = resp.status
        status = "UP" if _is_up(code) else "DOWN"
    except urllib.error.HTTPError as e:
        code = e.code
        status = "UP" if _is_up(code) else "DOWN"
    except Exception as e:  # Verbindung, Timeout, DNS usw.
        err = f"{type(e).__name__}: {e}"
    return {
        "url": url,
        "status": status,
        "http_status": code,
        "error": err,
        "ms": round((time.perf_counter() - started) * 1000, 1),
    }


def check_all(config=None, timeout: float | None = None) -> dict:
    cfg = config if config is not None else load_config()
    services = _get(cfg, "health.services", {}) or {}
    limit = float(timeout if timeout is not None else _get(cfg, "health.timeout_s", 2))
    results = []
    if services:
        with ThreadPoolExecutor(max_workers=len(services)) as pool:
            futures = [(name, pool.submit(probe, str(url), limit)) for name, url in services.items()]
            for name, fut in futures:
                item = fut.result()
                item["name"] = name
                results.append(item)
    overall = "UP" if results and all(r["status"] == "UP" for r in results) else "DOWN"
    return {
        "success": overall == "UP",
        "overall": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "services": results,
    }


def _print_human(report: dict) -> None:
    print(f"HEALTH {report['overall']}  ({report['checked_at']})")
    for s in report["services"]:
        code = s["http_status"] if s["http_status"] is not None else "-"
        extra = f" {s['error']}" if s["error"] else ""
        print(f"  {s['status']:4} {s['name']:12} {s['url']} -> {code} ({s['ms']} ms){extra}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="NEXUS Health-Uebersicht")
    ap.add_argument("--json", action="store_true", help="JSON statt Tabelle")
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--root", default=None, help="Projektroot (sonst automatisch)")
    a = ap.parse_args(argv)

    cfg = Config(root=a.root) if a.root else load_config()
    report = check_all(cfg, a.timeout)
    if a.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_human(report)
    return 0 if report["overall"] == "UP" else 1


if __name__ == "__main__":
    sys.exit(main())
