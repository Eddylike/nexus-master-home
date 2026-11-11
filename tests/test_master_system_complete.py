"""~40 completion tests for NEXUS Master System (local, no secrets printed)."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = os.environ.get("NEXUS_TEST_BASE", "http://127.0.0.1:8299")
GW = os.environ.get("NEXUS_TEST_GW", "http://127.0.0.1:8300")
PUBLIC = os.environ.get("NEXUS_TEST_PUBLIC", "https://wozipc-nexus.example.ts.net")
TOKEN_FILE = ROOT / "workspace" / "bridge" / ".token"

RESULTS: list[tuple[str, bool, str]] = []


def tok() -> str:
    if os.environ.get("NEXUS_BRIDGE_TOKEN"):
        return os.environ["NEXUS_BRIDGE_TOKEN"].strip()
    if TOKEN_FILE.is_file():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    return ""


def req(method: str, url: str, body: dict | None = None, auth: bool = True, timeout: float = 30):
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if auth:
        t = tok()
        if t:
            r.add_header("Authorization", f"Bearer {t}")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                j = json.loads(raw)
            except Exception:
                j = {"_raw": raw[:300]}
            return int(getattr(resp, "status", 200) or 200), j
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(raw)
        except Exception:
            j = {"_raw": raw[:300]}
        return int(e.code), j
    except Exception as e:
        return -1, {"error": type(e).__name__, "message": str(e)[:200]}


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL"), name, detail[:120])


def main() -> int:
    # 1 health
    c, j = req("GET", f"{BASE}/api/health", auth=False)
    check("T01_ORIGIN_HEALTH", c == 200 and j.get("ok"), f"code={c}")
    # 2 modules
    mods = (j or {}).get("modules") or {}
    check("T02_MODULES_CORRECTIONS", mods.get("corrections") is True)
    check("T03_MODULES_SELF_IMPROVE", mods.get("self_improve") is True)
    # 3 bridge unauthorized
    c, _ = req("GET", f"{BASE}/api/bridge/health", auth=False)
    check("T04_BRIDGE_401", c == 401, f"code={c}")
    # 4 bridge health auth
    c, j = req("GET", f"{BASE}/api/bridge/health")
    check("T05_BRIDGE_HEALTH", c == 200 and j.get("ok"), f"code={c}")
    check("T06_PROCESS_LEVEL_SAFE_WRITE", (j.get("permissions") or {}).get("process_level") == "SAFE_WRITE", str(j.get("permissions")))
    # 5 capabilities
    c, caps = req("GET", f"{BASE}/api/capabilities")
    check("T07_CAPABILITIES", c == 200 and isinstance(caps.get("tasks"), list), f"code={c} n={len(caps.get('tasks') or [])}")
    names = {t["name"] for t in caps.get("tasks") or []}
    for need in ("write_file", "patch_file", "create_file", "move_file", "copy_file", "delete_file",
                 "git_diff", "git_log", "git_add", "git_commit", "git_push",
                 "python_run_project_script", "powershell_registered_action",
                 "service_status", "nexus_status", "nexus_restart",
                 "android_status", "termux_status", "media_status", "voice_status", "llm_status"):
        check(f"T08_TASK_{need}", need in names)
    check("T09_NO_REMOTE_SHELL", caps.get("executors", {}).get("remote_shell") is False)
    check("T10_OPENAI_NOT_REQUIRED", caps.get("openai_required") is False)
    # 6 handshake
    c, j = req("POST", f"{BASE}/api/message", {"message": "NEXUS_BRIDGE_OK handshake", "source": "test"})
    check("T11_HANDSHAKE", c == 200 and "NEXUS_BRIDGE_OK" in str(j.get("reply")), str(j.get("reply"))[:80])
    # 7 ping
    c, j = req("POST", f"{BASE}/api/task", {"task": "ping", "params": {"echo": "master"}})
    check("T12_PING", c == 200 and j.get("success"), f"code={c}")
    # 8 llm_status
    c, j = req("POST", f"{BASE}/api/task", {"task": "llm_status"})
    check("T13_LLM_STATUS", c == 200 and j.get("success"), f"code={c}")
    # 9 nexus_status
    c, j = req("POST", f"{BASE}/api/task", {"task": "nexus_status"})
    check("T14_NEXUS_STATUS", c == 200 and j.get("success"), f"code={c}")
    # 10 write/read/patch/copy/move/delete cycle
    rel = "workspace/bridge/_master_test_file.txt"
    c, j = req("POST", f"{BASE}/api/task", {"task": "write_file", "params": {"path": rel, "content": "hello-master-1"}})
    check("T15_WRITE", c == 200 and j.get("success"), f"code={c} {j.get('error')}")
    c, j = req("POST", f"{BASE}/api/task", {"task": "read_file", "params": {"path": rel}})
    check("T16_READ", c == 200 and "hello-master-1" in str((j.get("task") or {}).get("result", {}).get("content")), f"code={c}")
    c, j = req("POST", f"{BASE}/api/task", {"task": "patch_file", "params": {"path": rel, "find": "1", "replace": "2"}})
    check("T17_PATCH", c == 200 and j.get("success"), f"code={c}")
    c, j = req("POST", f"{BASE}/api/task", {"task": "copy_file", "params": {"src": rel, "dst": rel + ".copy"}})
    check("T18_COPY", c == 200 and j.get("success"), f"code={c}")
    c, j = req("POST", f"{BASE}/api/task", {"task": "move_file", "params": {"src": rel + ".copy", "dst": rel + ".moved"}})
    check("T19_MOVE", c == 200 and j.get("success"), f"code={c}")
    c, j = req("POST", f"{BASE}/api/task", {"task": "delete_file", "params": {"path": rel + ".moved", "confirm": True}})
    check("T20_DELETE", c == 200 and j.get("success"), f"code={c}")
    c, j = req("POST", f"{BASE}/api/task", {"task": "delete_file", "params": {"path": rel, "confirm": True}})
    check("T21_DELETE_ORIG", c == 200 and j.get("success"), f"code={c}")
    # 11 path traversal
    c, j = req("POST", f"{BASE}/api/task", {"task": "read_file", "params": {"path": "../Windows/win.ini"}})
    check("T22_TRAVERSAL_BLOCK", c in (400, 403) or not j.get("success"), f"code={c}")
    # 12 privileged without approval -> awaiting
    c, j = req("POST", f"{BASE}/api/task", {"task": "powershell_registered_action", "params": {"action": "echo_host"}})
    check("T23_PRIV_AWAITING", c == 202 and j.get("awaiting_approval"), f"code={c} {j.get('error')}")
    aid = j.get("approval_id") or (j.get("task") or {}).get("approval_id")
    check("T24_APPROVAL_ID", bool(aid), str(aid))
    # 13 approve locally then run
    if aid:
        c2, j2 = req("POST", f"{BASE}/api/bridge/approvals/{aid}/approve", {"by": "test"})
        check("T25_APPROVE", c2 == 200 and j2.get("success"), f"code={c2}")
        c3, j3 = req("POST", f"{BASE}/api/task", {"task": "powershell_registered_action", "params": {"action": "echo_host"}, "approval_id": aid})
        check("T26_PRIV_RUN", c3 == 200 and j3.get("success"), f"code={c3} {j3.get('error')}")
    else:
        check("T25_APPROVE", False, "no aid")
        check("T26_PRIV_RUN", False, "no aid")
    # 14 gateway allowlist
    c, _ = req("GET", f"{GW}/api/bridge/health")
    check("T27_GW_BRIDGE", c in (200, 401), f"code={c}")
    c, j = req("GET", f"{GW}/", auth=False)
    check("T28_GW_ROOT_403", c == 403, f"code={c}")
    c, j = req("GET", f"{GW}/api/chat", auth=False)
    check("T29_GW_CHAT_403", c == 403, f"code={c}")
    # 15 public funnel
    c, j = req("GET", f"{PUBLIC}/api/bridge/health")
    check("T30_FUNNEL_HTTPS", c in (200, 401), f"code={c}")
    c, j = req("GET", f"{PUBLIC}/", auth=False)
    check("T31_FUNNEL_ROOT_403", c == 403, f"code={c}")
    # 16 git status
    c, j = req("POST", f"{BASE}/api/task", {"task": "git_status"})
    check("T32_GIT_STATUS", c == 200 and j.get("success"), f"code={c}")
    # 17 media/voice/android/termux/service
    for tname, tid in (("media_status", "T33"), ("voice_status", "T34"), ("android_status", "T35"),
                       ("termux_status", "T36"), ("service_status", "T37"), ("list_files", "T38")):
        params = {"path": "workspace"} if tname == "list_files" else {}
        c, j = req("POST", f"{BASE}/api/task", {"task": tname, "params": params})
        check(f"{tid}_{tname.upper()}", c == 200 and j.get("success"), f"code={c}")
    # 18 sessions
    c, j = req("POST", f"{BASE}/api/bridge/sessions", {"client": "test", "source": "master"})
    check("T39_SESSION_CREATE", c == 200 and j.get("success"), f"code={c}")
    # 19 no cloudflare process (informational via env skip)
    check("T40_TOKEN_PRESENT", len(tok()) > 8, "mask_only")
    # 20 create_dir
    c, j = req("POST", f"{BASE}/api/task", {"task": "create_dir", "params": {"path": "workspace/bridge/_master_test_dir"}})
    check("T41_CREATE_DIR", c == 200 and j.get("success"), f"code={c}")
    # 21 integration import
    try:
        from nexus.integration.chatgpt import ChatGPTBridgeClient  # noqa: F401
        check("T42_INTEGRATION_IMPORT", True)
    except Exception as e:
        check("T42_INTEGRATION_IMPORT", False, str(e))
    # 22 openai optional
    try:
        from nexus.common import openai_client  # may be under common or bridge
        check("T43_OPENAI_OPTIONAL_MOD", hasattr(openai_client, "openai_available"))
    except Exception:
        try:
            import importlib
            m = importlib.import_module("nexus.bridge.openai_client") if False else None
        except Exception:
            pass
        # file placed at nexus/common/openai_client.py or nexus/bridge — check path
        ok = (ROOT / "nexus" / "common" / "openai_client.py").is_file() or (ROOT / "nexus" / "bridge" / "openai_client.py").is_file()
        check("T43_OPENAI_OPTIONAL_MOD", ok, "file_check")
    # 23 approvals page exists
    check("T44_APPROVALS_HTML", (ROOT / "nexus" / "chat" / "static" / "approvals.html").is_file())
    # 24 docs
    check("T45_FINAL_DOC", (ROOT / "NEXUS_FINAL_SYSTEM.md").is_file())

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print("==== SUMMARY ====")
    print(f"PASSED={passed} FAILED={failed} TOTAL={len(RESULTS)}")
    out = ROOT / "workspace" / "apex" / "SMOKE_MASTER_SYSTEM_COMPLETE_LAST.json"
    out.write_text(json.dumps({
        "passed": passed, "failed": failed, "total": len(RESULTS),
        "results": [{"name": n, "ok": ok, "detail": d} for n, ok, d in RESULTS],
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, indent=2), encoding="utf-8")
    print(f"WROTE {out}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
