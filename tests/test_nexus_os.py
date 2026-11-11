"""NEXUS OS tests: identity, provider routing, shared room (ohne OpenAI),
Termux connectivity semantics, system status. Offline-freie Logik + optional
live-Endpunkte. Keine Vollsuite, keine Fake-PASS.
"""
from __future__ import annotations

import json
import os
import time
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE = os.environ.get("NEXUS_TEST_BASE", "http://127.0.0.1:8299")
TOKEN_FILE = ROOT / "workspace" / "bridge" / ".token"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS" if ok else "FAIL"), name, detail[:160])
    return bool(ok)


def get(path: str, timeout: float = 40.0):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200) or 200), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return int(e.code), json.loads(raw)
        except Exception:
            return int(e.code), {"_raw": raw[:200]}
    except Exception as e:
        return -1, {"error": type(e).__name__, "message": str(e)[:160]}


# ---------------------------------------------------------------- identity
def test_identity() -> None:
    from nexus import nexus_os

    ident = nexus_os.identity()
    check("identity.system_name", ident["system_name"] == "NEXUS OS", ident["system_name"])
    check("identity.device_name", ident["device_name"] == "NEXUS PHONE", ident["device_name"])
    check("identity.system_id", ident["system_id"] == "NEXUS-OS-MOBILE", ident["system_id"])
    check("identity.lead_agent", ident["lead_agent"] == "NEXUS Master Home", ident["lead_agent"])
    cfg = nexus_os.load_config()
    prov = cfg.get("providers") or {}
    check("config.providers.slots", prov.get("phone_local") == "PHONE_LOCAL_AI" and prov.get("wozipc_local") == "WOZIPC_LOCAL_AI" and prov.get("chatgpt_optional") == "CHATGPT_OPTIONAL", json.dumps(prov))
    raw = json.dumps(cfg).lower()
    check("config.no_secrets", not any(x in raw for x in ("api_key", "token", "secret", "password")))


# ---------------------------------------------------------------- provider routing
def test_provider_routing() -> None:
    from nexus.providers.manager import AIProviderManager, LOGICAL_PROVIDERS

    mgr = AIProviderManager(default="local")
    check("routing.logical_slots", set(LOGICAL_PROVIDERS.values()) == {"local", "claude", "openai"}, str(LOGICAL_PROVIDERS))
    check("routing.resolve_logical", mgr.resolve_key("WOZIPC_LOCAL_AI") == "local" and mgr.resolve_key("CHATGPT_OPTIONAL") == "openai")

    # Phase-A/phone-local provider control is retired; no synthetic phone provider is registered.
    check("routing.no_retired_phone_provider", "phone_local" not in mgr.providers and "PHONE_LOCAL_AI" not in LOGICAL_PROVIDERS)

    route = mgr.route("guten morgen")
    wozipc = mgr.providers["local"].status()
    if wozipc.get("reachable"):
        check("routing.local_first", route["logical"] == "WOZIPC_LOCAL_AI" and route["usable"] is True, json.dumps(route))
    else:
        check("routing.no_fake_local", route.get("usable") is False and route.get("error") == "local_ai_unavailable", json.dumps(route))

    # A spoken "chatgpt" hint must not pull traffic to the cloud while local works
    if wozipc.get("reachable"):
        r2 = mgr.route("frag chatgpt")
        check("routing.chatgpt_not_forced", r2["logical"] == "WOZIPC_LOCAL_AI", json.dumps(r2))

    stat = mgr.provider_status()
    cg = stat.get("CHATGPT_OPTIONAL") or {}
    check("routing.chatgpt_optional_flag", cg.get("status") == "OPTIONAL" and cg.get("blocks_health") is False, json.dumps(cg))
    check("routing.no_cloud_dependency", stat.get("cloud_dependency") is False)


# ---------------------------------------------------------------- shared room
def test_shared_room_without_openai() -> None:
    from nexus.chat.shared_room import SharedRoom, normalize_sender, participants

    names = [p["name"] for p in participants()]
    check("room.participants", names == ["EDDY", "NEXUS", "HERMES", "NEXUS OS", "PHONE LOCAL AI", "WOZIPC LOCAL AI", "CHATGPT OPTIONAL"], str(names))
    check("room.normalize_alias", normalize_sender("PHONE_LOCAL_AI") == "local_ai" and normalize_sender("wozipc local ai") == "local_ai" and normalize_sender("chatgpt_optional") == "chatgpt")
    check("room.normalize_rejects_unknown", normalize_sender("drop-table") == "drop-table")

    room = SharedRoom()
    import time as _t
    sid = f"NEXUS-OS-TEST-ROOM-{int(_t.time() * 1000)}"  # frischer Raum je Lauf (Store ist persistent)
    check("room.empty_ok", room.status(sid).get("message_count") == 0)

    a = room.append(session_id=sid, sender_type="EDDY", sender_name="EDDY", content="Status?", source="test", mode="user")
    check("room.append_human", bool(a.get("success")), json.dumps(a)[:140])
    check("room.append_canonical_type", (a.get("message") or {}).get("sender_type") == "human")

    dup = room.append(session_id=sid, sender_type="human", sender_name="EDDY", content="Status?", source="test")
    check("room.duplicate_guard", dup.get("blocked") == "duplicate", json.dumps(dup)[:140])

    ai = room.append(session_id=sid, sender_type="WOZIPC_LOCAL_AI", sender_name="NEXUS", content="Alles lokal ONLINE.", source="test", target="EDDY", mode="collaboration")
    check("room.append_local_provider", bool(ai.get("success")), json.dumps(ai)[:140])

    loop = room.append(session_id=sid, sender_type="WOZIPC_LOCAL_AI", sender_name="NEXUS", content="Selbst.", source="test", target="WOZIPC_LOCAL_AI", mode="collaboration")
    check("room.self_routing_guard", loop.get("blocked") in ("same_sender_loop", "invalid_route"), json.dumps(loop)[:140])

    over = room.append(session_id=sid, sender_type="WOZIPC_LOCAL_AI", sender_name="NEXUS", content="Runde 9.", source="test", target="chatgpt", round_no=9, mode="collaboration")
    check("room.max_rounds_guard", over.get("blocked") == "max_rounds", json.dumps(over)[:140])
    check("room.max_rounds_default_4", room.max_rounds == 4, str(room.max_rounds))

    st = room.status(sid)
    check("room.status_reports_participants", isinstance(st.get("participants"), list) and st.get("chatgpt_optional_missing_is_ok") is True)


# ---------------------------------------------------------------- Phase-A retired / phone inventory only
def test_phase_a_retired() -> None:
    from nexus import nexus_os

    # Phase-A control server, ADB forwarding and legacy phone-agent actions are retired.
    # The handset/Termux package inventory remains read-only and must not control NEXUS.
    check("phase_a.no_agent_status", not hasattr(nexus_os, "termux_agent_status"))
    check("phase_a.no_adb_forward", not hasattr(nexus_os, "ensure_adb_forward") and not hasattr(nexus_os, "_forward_list"))
    source = Path(nexus_os.__file__).read_text(encoding="utf-8")
    check("phase_a.no_control_ports", "8766" not in source and "18766" not in source and "8765" not in source)
    check("phase_a.no_legacy_control", "termux_phase_a_control" not in source and "install_phase_a" not in source)
    dev = nexus_os.phone_status()
    state = dev.get("state")
    check("phone.state_known", state in (nexus_os.ONLINE, nexus_os.UNAVAILABLE_TEMPORARILY, nexus_os.UNKNOWN), str(state))
    check("phone.read_only_inventory", "termux" in dev and "termux_packages" in dev)


# ---------------------------------------------------------------- system status (v2 rules)
def _cycle_with(nexus_os, **patches):
    # MOBILE 20261109: rule tests simulate "phone gone" - a paired-app contact recorded by another
    # test in the same process must not leak in, unless a test injects one explicitly.
    patches.setdefault("_paired_app_link", lambda getter=None: None)
    saved = {k: getattr(nexus_os, k) for k in patches}
    try:
        for k, v in patches.items():
            setattr(nexus_os, k, v)
        return nexus_os._probe_cycle()
    finally:
        for k, v in saved.items():
            setattr(nexus_os, k, v)


def test_system_status() -> None:
    from nexus import nexus_os

    data = nexus_os.collect(force=True)
    comps = data.get("components") or {}
    check("status.schema_v2", data.get("schema") == "nexus-os-status/v2", str(data.get("schema")))
    check("status.all_display_rows", all(k in comps for k in nexus_os.DISPLAY_ORDER), ",".join(sorted(comps)))
    check("status.source_and_checked_at", all(c.get("source") and c.get("checked_at") for c in comps.values()),
          json.dumps({k: [bool(v.get("source")), bool(v.get("checked_at"))] for k, v in comps.items()})[:200])
    layer_keys = ["CORE", "WOZIPC", "WOZIPC_LOCAL_AI", "NEXUSPHONE", "TERMUX", "PHONE_LOCAL_AI", "GLOBAL_LOCAL_AI", "SHARED_ROOM", "VOICE", "BRIDGE", "CHATGPT"]
    check("status.component_vocab", all(comps[k]["status"] in nexus_os.COMPONENT_STATES for k in layer_keys),
          json.dumps({k: comps[k]["status"] for k in layer_keys}))
    check("status.health_vocab", data.get("SYSTEM_HEALTH") in (nexus_os.HEALTHY, nexus_os.DEGRADED, nexus_os.CRITICAL, nexus_os.OFFLINE), str(data.get("SYSTEM_HEALTH")))
    check("status.chatgpt_optional", comps["CHATGPT"]["status"] == nexus_os.OPTIONAL and "CHATGPT" not in (comps["SYSTEM_HEALTH"].get("degraded_by") or []))
    check("status.no_system_ok_flag", "SYSTEM_OK" not in json.dumps(data))
    check("status.ok_semantics", data.get("ok") == bool(data.get("CORE_OPERATIONAL") and not data.get("CRITICAL_FAILURE"))
          and data.get("health") == data.get("SYSTEM_HEALTH"), json.dumps({k: data.get(k) for k in ("ok", "health", "SYSTEM_HEALTH", "CORE_OPERATIONAL", "CRITICAL_FAILURE")}))
    links = data.get("LINKS") or {}
    check("status.links_present", set(links) == set(nexus_os.LINK_ORDER) and all(v.get("state") in nexus_os.STATES and v.get("source") and v.get("checked_at") for v in links.values()),
          json.dumps({k: v.get("state") for k, v in links.items()}))
    check("status.chatgpt_never_online", links.get("CORE_CHATGPT", {}).get("state") in (nexus_os.AVAILABLE, nexus_os.UNAVAILABLE, nexus_os.OFFLINE)
          and comps["CHATGPT"].get("state") in (nexus_os.AVAILABLE, nexus_os.UNAVAILABLE), json.dumps([links.get("CORE_CHATGPT", {}).get("state"), comps["CHATGPT"].get("state")]))
    special = {"OPERATING_MODE", "ACTIVE_AI_PROVIDER", "SYSTEM_HEALTH", "CRITICAL_FAILURE"}
    check("status.state_vocab", all(v.get("state") in nexus_os.STATES for k, v in comps.items() if k not in special),
          json.dumps({k: v.get("state") for k, v in comps.items() if k not in special}))
    check("status.termux_inventory_tri_state", comps["TERMUX"]["state"] in (nexus_os.ONLINE, nexus_os.OFFLINE, nexus_os.UNKNOWN),
          json.dumps({"TERMUX": comps["TERMUX"]["state"]}))
    check("status.device_not_unavailable_if_online", comps["PHONE_DEVICE"]["state"] != nexus_os.ONLINE or comps["PHONE_DEVICE"]["state"] == comps["NEXUSPHONE"]["state"])
    check("status.nexus_operational", data.get("NEXUS_OPERATIONAL") == bool(data.get("CORE_OPERATIONAL") and not data.get("CRITICAL_FAILURE")))
    wa = comps["WOZIPC_LOCAL_AI"]
    check("status.wozipc_online_has_model_evidence", wa["status"] != nexus_os.ONLINE or (wa.get("model_loaded") is True and bool(wa.get("inference_checked_at"))), json.dumps({k: wa.get(k) for k in ("status", "model_loaded", "inference_ms", "inference_checked_at", "reason")}))
    if wa["status"] == nexus_os.ONLINE and data.get("CORE_OPERATIONAL"):
        check("status.core_wozipc_link_online", links.get("CORE_WOZIPC", {}).get("state") == nexus_os.ONLINE)
    ph = comps["NEXUSPHONE"]
    check("status.phone_method", ph.get("method") in ("adb", "tailscale_ping", "app_link", "none")
          and (ph["status"] != nexus_os.ONLINE or ph.get("method") in ("adb", "tailscale_ping", "app_link"))
          and (ph.get("method") != "tailscale_ping" or (ph.get("tailscale_ping") or {}).get("ok") is True),
          json.dumps({k: ph.get(k) for k in ("status", "method", "reason", "source")}))
    if data.get("CORE_OPERATIONAL"):
        sr = comps["SHARED_ROOM"]
        check("status.room_roundtrip", "SharedRoom.append" in sr.get("source", "") and ".status" in sr.get("source", "") and sr["status"] == nexus_os.ONLINE, json.dumps(sr)[:200])
    if comps["WOZIPC_LOCAL_AI"]["status"] == nexus_os.ONLINE:
        check("status.global_ai_online_with_pc_ai", comps["GLOBAL_LOCAL_AI"]["status"] == nexus_os.ONLINE)
    if comps["NEXUSPHONE"]["status"] != nexus_os.ONLINE:
        pla = comps["PHONE_LOCAL_AI"]
        check("status.phone_ai_unknown_when_disconnected", pla["status"] == nexus_os.UNKNOWN and pla.get("reason") and pla.get("dependency"), json.dumps(pla)[:160])
    blob = json.dumps(data)
    secrets = [v for v in (os.environ.get("OPENAI_API_KEY"), os.environ.get("NEXUS_BRIDGE_TOKEN")) if v and len(v) >= 8]
    check("status.no_secret_leak", not any(v in blob for v in secrets) and "sk-" not in blob, f"checked {len(secrets)} env secrets (values not printed)")

    # Rule: phone gone, core + PC AI ok -> PC_FALLBACK / DEGRADED / not critical
    no_dev = lambda: {"adb": True, "devices": [], "ready": [], "state": nexus_os.UNAVAILABLE_TEMPORARILY, "error": "NO_DEVICE"}
    d1 = _cycle_with(nexus_os, adb_devices=no_dev, _tailscale_peer=lambda ts, cfg: None)
    c1 = d1["components"]
    if c1["WOZIPC_LOCAL_AI"]["status"] == nexus_os.ONLINE and d1["CORE_OPERATIONAL"]:
        check("rule.pc_fallback", d1["OPERATING_MODE"] == "PC_FALLBACK" and d1["SYSTEM_HEALTH"] == nexus_os.DEGRADED and d1["CRITICAL_FAILURE"] is False
              and d1["ACTIVE_AI_PROVIDER"] == "WOZIPC_LOCAL_AI" and d1["GLOBAL_LOCAL_AI"] == nexus_os.ONLINE,
              json.dumps({k: d1[k] for k in ("OPERATING_MODE", "SYSTEM_HEALTH", "CRITICAL_FAILURE", "ACTIVE_AI_PROVIDER", "GLOBAL_LOCAL_AI")}))
        check("rule.phone_ai_stale_is_unknown", c1["NEXUSPHONE"]["status"] == nexus_os.DISCONNECTED
              and c1["PHONE_LOCAL_AI"]["status"] == nexus_os.UNKNOWN
              and c1["PHONE_LOCAL_AI"].get("reason") == "HEARTBEAT_STALE",
              json.dumps({"phone": c1["NEXUSPHONE"], "phone_ai": c1["PHONE_LOCAL_AI"]})[:220])
        check("rule.pc_fallback_ok_true", d1.get("ok") is True and d1.get("health") == nexus_os.DEGRADED, json.dumps({k: d1.get(k) for k in ("ok", "health")}))
    # Rule: Tailscale peer flag online but no pong -> not ONLINE (TAILSCALE_ONLY)
    fake_ts = {"available": True, "running": True, "data": {"Peer": {"x": {"HostName": "fake", "DNSName": "fake.ts.net.",
               "TailscaleIPs": [nexus_os.load_config().get("phone_tailscale_ip") or "192.0.2.11"], "Online": True, "Active": False, "OS": "android"}}}}
    no_pong = lambda target, cfg=None, force=False: {"ok": False, "error": "NO_PONG", "source": "test tailscale ping", "checked_at": nexus_os._iso(), "method": "tailscale_ping"}
    pong = lambda target, cfg=None, force=False: {"ok": True, "latency_ms": 12.0, "via": "test", "source": "test tailscale ping", "checked_at": nexus_os._iso(), "method": "tailscale_ping"}
    d4 = _cycle_with(nexus_os, adb_devices=no_dev, _tailscale_status=lambda: fake_ts, tailscale_ping=no_pong)
    p4 = d4["components"]["NEXUSPHONE"]
    check("rule.tailscale_flag_only_not_online", p4["status"] == nexus_os.DISCONNECTED and p4.get("reason") == "TAILSCALE_ONLY" and p4.get("method") == "none", json.dumps({k: p4.get(k) for k in ("status", "reason", "method")}))
    d5 = _cycle_with(nexus_os, adb_devices=no_dev, _tailscale_status=lambda: fake_ts, tailscale_ping=pong)
    p5 = d5["components"]["NEXUSPHONE"]
    check("rule.tailscale_ping_online", p5["status"] == nexus_os.ONLINE and p5.get("method") == "tailscale_ping" and d5["components"]["TERMUX"]["status"] == nexus_os.UNKNOWN,
          json.dumps({k: p5.get(k) for k in ("status", "reason", "method")}))
    # Rule: cached pong must be dropped as soon as the peer flag goes offline (live result + checked_at shown)
    ip_cfg = nexus_os.load_config().get("phone_tailscale_ip") or "192.0.2.11"
    fake_off = {"available": True, "running": True, "data": {"Peer": {"x": {"HostName": "fake", "DNSName": "fake.ts.net.",
                "TailscaleIPs": [ip_cfg], "Online": False, "Active": False, "OS": "android"}}}}
    saved_ping, saved_prev = nexus_os._STATE.get("ts_ping"), nexus_os._STATE.get("peer_online_prev")
    seen = {}
    def spy_ping(target, cfg=None, force=False):
        seen["force"], seen["cache_at_call"] = force, nexus_os._STATE.get("ts_ping")
        return {"ok": False, "error": "NO_PONG", "source": "test tailscale ping", "checked_at": nexus_os._iso(), "ts": time.time(), "method": "tailscale_ping", "cached": False}
    try:
        nexus_os._STATE["ts_ping"] = {"ok": True, "latency_ms": 71.0, "via": "stale", "target": ip_cfg, "ts": time.time(), "checked_at": nexus_os._iso(), "method": "tailscale_ping"}
        nexus_os._STATE["peer_online_prev"] = False
        d6 = _cycle_with(nexus_os, adb_devices=no_dev, _tailscale_status=lambda: fake_off, tailscale_ping=spy_ping)
        p6 = d6["components"]["NEXUSPHONE"]
        check("rule.ping_cache_dropped_peer_offline", seen.get("force") is True and seen.get("cache_at_call") is None and p6["status"] != nexus_os.ONLINE
              and "pong 71" not in str(p6.get("detail")) and "kein pong" in str(p6.get("detail")) and bool(p6.get("checked_at")),
              json.dumps({"force": seen.get("force"), "status": p6.get("status"), "detail": p6.get("detail"), "checked_at": p6.get("checked_at")})[:220])
        # real function: force=True bypasses a cached pong
        nexus_os._STATE["ts_ping"] = {"ok": True, "latency_ms": 71.0, "via": "stale", "target": "192.0.2.16", "ts": time.time(), "checked_at": nexus_os._iso()}
        old_exe = nexus_os._TAILSCALE_EXE
        nexus_os._TAILSCALE_EXE = r"C:\nexus-os-test\missing-tailscale.exe"
        try:
            r_cached = nexus_os.tailscale_ping("192.0.2.16")
            r_live = nexus_os.tailscale_ping("192.0.2.16", force=True)
        finally:
            nexus_os._TAILSCALE_EXE = old_exe
        check("rule.ping_force_bypasses_cache", r_cached.get("cached") is True and r_live.get("cached") is False and r_live.get("ok") is not True,
              json.dumps({"cached": r_cached.get("ok"), "live": r_live.get("ok"), "err": r_live.get("error")}))
    finally:
        nexus_os._STATE["ts_ping"], nexus_os._STATE["peer_online_prev"] = saved_ping, saved_prev
    # WOZIPC_LOCAL_AI: service answers with HTTP error -> reachable=True, FAILED, http_status
    if data.get("CORE_OPERATIONAL"):
        old = os.environ.get("NEXUS_OLLAMA_URL")
        os.environ["NEXUS_OLLAMA_URL"] = nexus_os._origin() + "/nexus-os-test-404"
        try:
            w = nexus_os.wozipc_local_status()
        finally:
            if old is None:
                os.environ.pop("NEXUS_OLLAMA_URL", None)
            else:
                os.environ["NEXUS_OLLAMA_URL"] = old
        check("rule.wozipc_http_error_reachable", w.get("reachable") is True and w.get("status") == nexus_os.FAILED and isinstance(w.get("http_status"), int), json.dumps(w)[:200])
    old = os.environ.get("NEXUS_OLLAMA_URL")
    os.environ["NEXUS_OLLAMA_URL"] = "http://127.0.0.1:1"
    try:
        w2 = nexus_os.wozipc_local_status()
    finally:
        if old is None:
            os.environ.pop("NEXUS_OLLAMA_URL", None)
        else:
            os.environ["NEXUS_OLLAMA_URL"] = old
    check("rule.wozipc_refused_unreachable", w2.get("reachable") is False and w2.get("status") == nexus_os.OFFLINE, json.dumps(w2)[:200])
    # Rule: no local AI at all -> GLOBAL OFFLINE -> CRITICAL
    ai_down = lambda client=None, **kw: {"provider": "WOZIPC_LOCAL_AI", "reachable": False, "status": nexus_os.OFFLINE, "reason": "OLLAMA_UNREACHABLE", "source": "test"}
    d2 = _cycle_with(nexus_os, adb_devices=no_dev, _tailscale_peer=lambda ts, cfg: None, wozipc_local_status=ai_down)
    check("rule.no_local_ai_critical", d2["GLOBAL_LOCAL_AI"] == nexus_os.OFFLINE and d2["CRITICAL_FAILURE"] is True and d2["SYSTEM_HEALTH"] == nexus_os.CRITICAL,
          json.dumps({k: d2[k] for k in ("GLOBAL_LOCAL_AI", "CRITICAL_FAILURE", "SYSTEM_HEALTH")}))
    # Rule: core down -> OFFLINE / critical
    d3 = _cycle_with(nexus_os, _origin=lambda: "http://127.0.0.1:1", adb_devices=no_dev, _tailscale_peer=lambda ts, cfg: None)
    check("rule.core_down_links_offline", all(v["state"] == nexus_os.OFFLINE for v in d3["LINKS"].values()) and set(d3["LINKS"]) == set(nexus_os.LINK_ORDER), json.dumps({k: v["state"] for k, v in d3["LINKS"].items()}))
    check("rule.core_down_offline", d3["SYSTEM_HEALTH"] == nexus_os.CRITICAL and d3["CRITICAL_FAILURE"] is True and d3["CORE_OPERATIONAL"] is False and d3["ok"] is False and d3["NEXUS_OPERATIONAL"] is False,
          json.dumps({k: d3[k] for k in ("SYSTEM_HEALTH", "CRITICAL_FAILURE", "CORE_OPERATIONAL")}))
    # Rule: stale snapshot -> UNKNOWN instead of old value
    st = nexus_os._apply_staleness(data, 999.0, 150.0)
    check("rule.stale_unknown", st["SYSTEM_HEALTH"] == nexus_os.UNKNOWN and all(v["status"] == nexus_os.UNKNOWN for k, v in st["components"].items() if k != "CHATGPT"))


# ---------------------------------------------------------------- live endpoints
def test_live_endpoints() -> None:
    code, ident = get("/api/nexus-os/identity")
    check("live.identity_endpoint", code == 200 and ident.get("system_id") == "NEXUS-OS-MOBILE", f"{code} {json.dumps(ident)[:120]}")
    code, st = get("/api/nexus-os/status")
    check("live.status_endpoint", code == 200 and st.get("schema") == "nexus-os-status/v2" and "components" in st, f"{code} {json.dumps(st)[:120]}")
    check("live.monitor_running", bool((st.get("recheck") or {}).get("monitor_running")), json.dumps(st.get("recheck"))[:160])
    code, prov = get("/api/nexus-os/providers")
    check("live.providers_endpoint", code == 200 and prov.get("CHATGPT_OPTIONAL", {}).get("status") == "OPTIONAL", f"{code} {json.dumps(prov)[:140]}")
    code, voice = get("/api/voice/status")
    check("live.voice_has_route", code == 200 and "provider_route" in voice, f"{code} {json.dumps(voice)[:140]}")
    check("live.shell_static", (ROOT / "nexus" / "chat" / "static" / "js" / "bridge_status_tiles.js").is_file())
    check("live.shell_chip_in_ui", 'id="nx-shell-panel"' in (ROOT / "nexus" / "chat" / "static" / "index.html").read_text(encoding="utf-8"))


def main() -> int:
    test_identity()
    test_provider_routing()
    test_shared_room_without_openai()
    test_phase_a_retired()
    test_system_status()
    test_live_endpoints()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = len(RESULTS) - passed
    print(f"\nNEXUS_OS_TESTS PASSED={passed} FAILED={failed} TOTAL={len(RESULTS)}")
    for name, ok, detail in RESULTS:
        if not ok:
            print(f"  FAILED: {name} :: {detail[:200]}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())