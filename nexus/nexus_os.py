"""NEXUS OS - Identity, Provider-Slots and layered, source-backed system status.

Status model v2 (2026-10-06). Single source for /api/nexus-os/status, the
NEXUS SHELL / ORBITAL panel (index.html) and the PWA (/m). No secrets are read
or written here; the Bridge token is never touched.

Layers
  * devices : WOZIPC, NEXUSPHONE (+ TERMUX, PHONE_AGENT as phone sub-layers)
  * ai      : WOZIPC_LOCAL_AI, PHONE_LOCAL_AI, GLOBAL_LOCAL_AI, ACTIVE_AI_PROVIDER
  * overall : CORE, OPERATING_MODE, SYSTEM_HEALTH, CRITICAL_FAILURE, CORE_OPERATIONAL

Component states
  ONLINE                    real probe succeeded
  OFFLINE                   probe target not running / not answering
  DISCONNECTED              device link missing (adb/tailscale)
  UNAVAILABLE_TEMPORARILY   blocked by a dependency (reason + dependency set)
  FAILED                    service was reached and returned a real error
  UNKNOWN                   cannot be determined (or source stale) - never a PASS
  OPTIONAL                  ChatGPT only - never affects health

Rules
  * every field carries source + checked_at; stale snapshots -> UNKNOWN
  * GLOBAL_LOCAL_AI = ONLINE if >= 1 local AI backend is really reachable
  * a disconnected phone never sets CORE offline (-> PC_FALLBACK / DEGRADED)
  * CHATGPT is OPTIONAL and never a reason for DEGRADED/red
  * phone path is read-only: adb devices / adb forward / GET /health,
    tailscale status --json. Phase-A control port is 8766 (legacy 8765 retired).
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import re
import uuid
import urllib.error
import urllib.request
from copy import deepcopy
from typing import Any

from nexus.common.paths import APEX, WORKSPACE

CONFIG_PATH = APEX / "nexus_os.json"
STATUS_LOG = WORKSPACE / "logs" / "nexus_os_status.log"
SCHEMA = "nexus-os-status/v2"

SYSTEM_NAME = "NEXUS OS"
DEVICE_NAME = "NEXUS PHONE"
SYSTEM_ID = "NEXUS-OS-MOBILE"
LEAD_AGENT = "NEXUS Master Home"

# ---- component states
ONLINE = "ONLINE"
OFFLINE = "OFFLINE"
DISCONNECTED = "DISCONNECTED"
UNAVAILABLE_TEMPORARILY = "UNAVAILABLE_TEMPORARILY"
FAILED = "FAILED"
UNKNOWN = "UNKNOWN"
BUSY = "BUSY"  # Ollama erreichbar, Modell geladen/Generierung laeuft -> kein zweites Modell laden
OPTIONAL = "OPTIONAL"
# aggregate / legacy states
DEGRADED = "DEGRADED"
HEALTHY = "HEALTHY"
CRITICAL = "CRITICAL"
UNAVAILABLE = "UNAVAILABLE"  # legacy alias, kept for old callers

COMPONENT_STATES = (ONLINE, OFFLINE, DISCONNECTED, UNAVAILABLE_TEMPORARILY, FAILED, UNKNOWN, OPTIONAL)
AVAILABLE = "AVAILABLE"
# Display vocabulary (field "state"): ONLINE, OFFLINE, AVAILABLE, UNAVAILABLE, DISCONNECTED, UNKNOWN, DEGRADED, CRITICAL.
# "status" keeps the internal v2 value for compatibility; "state" is derived via STATE_VOCAB.
STATES = (ONLINE, OFFLINE, AVAILABLE, UNAVAILABLE, DISCONNECTED, UNKNOWN, DEGRADED, CRITICAL)
STATE_VOCAB = {
    ONLINE: ONLINE, HEALTHY: ONLINE, OFFLINE: OFFLINE, DISCONNECTED: DISCONNECTED, UNKNOWN: UNKNOWN,
    DEGRADED: DEGRADED, CRITICAL: CRITICAL,
    FAILED: UNAVAILABLE,                   # answered, but not usable (reason/http_status in the field)
    UNAVAILABLE_TEMPORARILY: UNAVAILABLE,  # blocked by a dependency (dependency in the field)
    OPTIONAL: AVAILABLE,                   # optional provider configured (no paid test call); UNAVAILABLE if not configured
    AVAILABLE: AVAILABLE, UNAVAILABLE: UNAVAILABLE,
}
# Phone control chain is strictly ONLINE / OFFLINE / UNKNOWN (Eddie, 06.10.)
_TRI_STATE_KEYS = ("TERMUX", "PHONE_AGENT", "PHONE_CONTROL")
VOCABULARY_DOC = {
    "states": list(STATES),
    "mapping_from_status": {k: v for k, v in STATE_VOCAB.items()},
    "tri_state_components": list(_TRI_STATE_KEYS),
    "notes": "TERMUX/PHONE_AGENT/PHONE_CONTROL: FAILED/UNAVAILABLE_TEMPORARILY/DISCONNECTED -> OFFLINE (reason/dependency bleiben). "
             "CHATGPT: AVAILABLE wenn konfiguriert, sonst UNAVAILABLE; nie ONLINE ohne echten (kostenpflichtigen) Call.",
}


def to_state(key: str, comp: dict[str, Any]) -> str:
    st = str(comp.get("status") or UNKNOWN)
    if key == "CHATGPT" or st == OPTIONAL:
        return AVAILABLE if comp.get("configured") else UNAVAILABLE
    if key in _TRI_STATE_KEYS:
        return st if st in (ONLINE, UNKNOWN) else OFFLINE
    return STATE_VOCAB.get(st, st)

# Mapping to the ORBITAL vocabulary (documented in the repair report).
ORBITAL_MAP = {
    ONLINE: "PASS",
    FAILED: "FAIL",
    OFFLINE: "FAIL",
    DISCONNECTED: "BLOCKED",
    UNAVAILABLE_TEMPORARILY: "BLOCKED",
    UNKNOWN: "UNKNOWN",
    OPTIONAL: "OPTIONAL",
}

PROVIDER_PHONE_LOCAL = "PHONE_LOCAL_AI"
PROVIDER_WOZIPC_LOCAL = "WOZIPC_LOCAL_AI"
PROVIDER_CHATGPT_OPTIONAL = "CHATGPT_OPTIONAL"

# Phone side: Phase-A control service (Termux, binds 127.0.0.1:8766 on the phone),
# reached read-only through a host-side adb forward.
PHASE_A_REMOTE_PORT = 8766
PHASE_A_FORWARD_PORT = 18766
LEGACY_AGENT_REMOTE_PORTS = (8765,)  # retired NEXUS_TERMUX_AGENT - forwards get cleaned up
# legacy names (old callers import these)
TERMUX_AGENT_LOCAL_PORT = PHASE_A_REMOTE_PORT
TERMUX_AGENT_FORWARD_PORT = PHASE_A_FORWARD_PORT
TERMUX_STAGE_REMOTE = "/sdcard/Download/NEXUS_TRANSFER"

_TAILSCALE_EXE = r"C:\Program Files\Tailscale\tailscale.exe"
_ADB_FALLBACK = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "platform-tools", "adb.exe")
_BRIDGE_STATE_DIR = WORKSPACE / "bridge"

RECHECK_ORDER = [
    "NEXUSPHONE (adb devices; else tailscale ping --c 1 --timeout 3s <peer>, cached)",
    "TERMUX (adb: pidof com.termux)",
    "PHONE_AGENT (adb forward tcp:18766->tcp:8766, GET /health)",
    "WOZIPC_LOCAL_AI (Ollama /api/tags)",
    "MODEL (configured model present; POST /api/generate num_predict=1 keep_alive=0, cached)",
    "PHONE_CONTROL (POST /action file_status, read-only)",
    "SHARED_ROOM (POST /api/shared-room/message + GET /api/shared-room/status roundtrip on NEXUS-OS-HEALTH, cached)",
    "VOICE (POST /api/voice/tts -> audio/mpeg, cached)",
]

DISPLAY_ORDER = [
    "CORE", "OPERATING_MODE", "WOZIPC", "WOZIPC_LOCAL_AI", "PHONE_DEVICE", "PHONE_CONTROL", "TERMUX",
    "PHONE_AGENT", "PHONE_LOCAL_AI", "GLOBAL_LOCAL_AI", "ACTIVE_AI_PROVIDER", "CHATGPT",
    "SHARED_ROOM", "VOICE", "NETWORK", "BRIDGE", "SYSTEM_HEALTH", "CRITICAL_FAILURE",
]
LINK_ORDER = ["PHONE_CORE", "CORE_WOZIPC", "CORE_CHATGPT"]

DEFAULT_CONFIG: dict[str, Any] = {
    "system_name": SYSTEM_NAME,
    "device_name": DEVICE_NAME,
    "system_id": SYSTEM_ID,
    "lead_agent": LEAD_AGENT,
    "providers": {
        "phone_local": PROVIDER_PHONE_LOCAL,
        "wozipc_local": PROVIDER_WOZIPC_LOCAL,
        "chatgpt_optional": PROVIDER_CHATGPT_OPTIONAL,
    },
    "default_provider": PROVIDER_WOZIPC_LOCAL,
    "fallback_order": [PROVIDER_PHONE_LOCAL, PROVIDER_WOZIPC_LOCAL, PROVIDER_CHATGPT_OPTIONAL],
    "phone_enabled": True,
    "phone_adb_serial": "249ac5d7",
    "phone_tailscale_ip": os.environ.get("NEXUS_PHONE_TAILSCALE_IP", "192.0.2.10"),
    "phone_tailscale_host": "redmi-note-13",
    "phone_peer_match": "ip",              # ip (default) | host  - no name guessing when an IP is configured
    "phone_expected_model": "23124RA7EO",  # Redmi Note 13 (sapphiren), from adb devices -l of serial 249ac5d7
    "phone_ping_ttl_sec": 90,
    "room_check_ttl_sec": 600,
    "ai_inference_ttl_sec": 600,           # real minimal inference (num_predict=1, keep_alive=0), cached
    "phone_link_window_sec": 90,           # PHONE_CORE ONLINE only with an inbound phone request in this window
    "phase_a_port": PHASE_A_REMOTE_PORT,
    "phase_a_forward_port": PHASE_A_FORWARD_PORT,
    "bridge_gateway_port": 8300,
    "bridge_origin_port": 8299,
    "recheck_interval_sec": 45,
    "stale_after_sec": 150,
    "voice_check_ttl_sec": 600,
    "public_probe_ttl_sec": 300,
    "phone_ai_required_for_healthy": False,
    "cache_ttl_sec": 15,
    "notes": (
        "NEXUS OS identity + provider slots. Keine Geheimnisse in dieser Datei. "
        "Phone-Pfad nur lesend: adb forward tcp:18766->8766 (Phase-A /health), tailscale status. "
        "CHATGPT_OPTIONAL bleibt optional und blockiert nie Health."
    ),
}

_CONFIG_KEYS = tuple(DEFAULT_CONFIG.keys())

_LOCK = threading.RLock()          # protects _STATE
_CYCLE_LOCK = threading.Lock()     # only one probe cycle at a time
_STATE: dict[str, Any] = {
    "snapshot": None, "ts": 0.0, "cycles": 0, "last_cycle_ms": None,
    "monitor": None, "monitor_started_at": None, "last_error": None,
    "voice": None, "public": None, "chat_e2e": None, "pkg": None, "prev": {},
    "ts_ping": None, "room_check": None, "peer_identity": None, "ai_infer": None, "clients": {},
}


# --------------------------------------------------------------------------- config
def _env_overrides(cfg: dict[str, Any]) -> dict[str, Any]:
    out = dict(cfg)
    name = (os.environ.get("NEXUS_SYSTEM_NAME") or "").strip()
    if name:
        out["system_name"] = name
    lead = (os.environ.get("NEXUS_LEAD_AGENT") or "").strip()
    if lead:
        out["lead_agent"] = lead
    phone = (os.environ.get("NEXUS_PHONE_ENABLED") or "").strip().lower()
    if phone in ("1", "true", "yes", "on"):
        out["phone_enabled"] = True
    elif phone in ("0", "false", "no", "off"):
        out["phone_enabled"] = False
    return out


def load_config() -> dict[str, Any]:
    base = deepcopy(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.is_file():
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k in _CONFIG_KEYS:
                    if k in raw:
                        base[k] = raw[k]
    except Exception:
        pass
    base = _env_overrides(base)
    if not isinstance(base.get("providers"), dict):
        base["providers"] = deepcopy(DEFAULT_CONFIG["providers"])
    if not isinstance(base.get("fallback_order"), list) or not base["fallback_order"]:
        base["fallback_order"] = list(DEFAULT_CONFIG["fallback_order"])
    return base


def save_config(updates: dict[str, Any] | None = None) -> dict[str, Any]:
    """Persist identity/provider metadata only. Secrets are rejected, never stored."""
    cfg = load_config()
    for k, v in (updates or {}).items():
        if k not in DEFAULT_CONFIG:
            continue
        low = k.lower()
        if any(x in low for x in ("token", "secret", "password", "api_key", "apikey")):
            continue
        if isinstance(v, (str, int, float, bool, list, dict)):
            cfg[k] = v
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)
    except Exception:
        pass
    return cfg


def identity() -> dict[str, Any]:
    cfg = load_config()
    return {
        "system_name": cfg.get("system_name") or SYSTEM_NAME,
        "device_name": cfg.get("device_name") or DEVICE_NAME,
        "system_id": cfg.get("system_id") or SYSTEM_ID,
        "lead_agent": cfg.get("lead_agent") or LEAD_AGENT,
    }


# --------------------------------------------------------------------------- helpers
def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts if ts is not None else time.time()))


def _field(status: str, source: str, detail: str = "", *, reason: str | None = None,
           dependency: str | None = None, checked_at: str | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status, "source": source, "checked_at": checked_at or _iso(), "detail": detail}
    if reason:
        out["reason"] = reason
    if dependency:
        out["dependency"] = dependency
    out.update(extra)
    return out


PHONE_AI_HEARTBEAT_MAX_AGE_S = 180.0   # gleiche Grenze wie nexus_os_link.device_get (ONLINE < 180 s)
PHONE_AI_CHECK_MAX_AGE_S = 300.0       # Ollama-Pruefung der App darf hoechstens 5 min vor dem Heartbeat liegen


def _phone_ai_from_heartbeat(dev_get=None) -> dict[str, Any] | None:
    """F-03: PHONE_LOCAL_AI aus dem App-Heartbeat (ollama_up/ollama_version/ollama_model/ollama_checked_ts).

    None  = Heartbeat fehlt oder alte App ohne KI-Feld -> Aufrufer nutzt den bisherigen Weg.
    UNKNOWN bei veralteten Daten, ONLINE/OFFLINE nur aus einem frischen, echten Heartbeat-Wert.
    """
    try:
        if dev_get is None:
            from nexus import nexus_os_link as _link
            dev_get = _link.device_get
        _code, d = dev_get()
    except Exception:
        return None
    dev = d.get("device") if isinstance(d, dict) else None
    if not isinstance(dev, dict) or not isinstance(dev.get("ollama_up"), bool):
        return None
    src = "App-Heartbeat /api/nexus-os/device (ollama_up)"
    age = d.get("age_s")
    extra = {"model": dev.get("ollama_model"), "version": dev.get("ollama_version"),
             "heartbeat_age_s": age, "ollama_checked_ts": dev.get("ollama_checked_ts")}
    try:
        age_f = float(age)
    except (TypeError, ValueError):
        age_f = None
    if age_f is None or age_f > PHONE_AI_HEARTBEAT_MAX_AGE_S:
        return _field(UNKNOWN, src, "Heartbeat veraltet", reason="HEARTBEAT_STALE", **extra)
    chk, rts = dev.get("ollama_checked_ts"), dev.get("received_ts")
    try:
        if chk is None or rts is None or abs(float(rts) - float(chk)) > PHONE_AI_CHECK_MAX_AGE_S:
            return _field(UNKNOWN, src, "Ollama-Pruefung der App veraltet oder ohne Zeit", reason="AI_CHECK_STALE", **extra)
    except (TypeError, ValueError):
        return _field(UNKNOWN, src, "Ollama-Pruefzeit ungueltig", reason="AI_CHECK_STALE", **extra)
    if dev["ollama_up"]:
        return _field(ONLINE, src, "%s (Ollama %s)" % (dev.get("ollama_model") or "?", dev.get("ollama_version") or "?"), **extra)
    return _field(OFFLINE, src, "Ollama am Handy antwortet nicht", reason="PHONE_AI_NOT_RUNNING", **extra)


def _tcp_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _run(args: list[str], timeout: float = 8.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False, check=False,  # MH05
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except FileNotFoundError:
        return 127, "", "not_found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:  # pragma: no cover - defensive
        return 1, "", "%s: %s" % (type(e).__name__, str(e)[:160])  # PH01


def _http(url: str, *, method: str = "GET", body: bytes | None = None, timeout: float = 3.0,
          headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Tiny HTTP probe. Returns code/content_type/body or a classified error."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return {"ok": True, "code": int(getattr(r, "status", 200) or 200),
                    "content_type": r.headers.get("Content-Type", ""), "body": data}
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:
            data = b""
        return {"ok": False, "code": int(e.code), "content_type": e.headers.get("Content-Type", "") if e.headers else "",
                "body": data, "error": "HTTP_%s" % e.code}
    except Exception as e:  # connection level
        name = type(e).__name__
        msg = str(e)
        reason = getattr(e, "reason", None)
        if reason is not None:
            name = type(reason).__name__
            msg = str(reason)
        low = (name + " " + msg).lower()
        if "timed out" in low or "timeout" in low:
            kind = "TIMEOUT"
        elif "refused" in low or "10061" in low:
            kind = "CONNECTION_REFUSED"
        elif "remotedisconnected" in low or "reset" in low or "closed" in low or "10054" in low or "badstatusline" in low:
            kind = "REMOTE_CLOSED"
        else:
            kind = name.upper()
        return {"ok": False, "code": None, "error": kind, "error_detail": msg[:160]}


def _json_body(res: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads((res.get("body") or b"{}").decode("utf-8", "replace") or "{}")
    except Exception:
        return {}


def _origin() -> str:
    cfg = load_config()
    return "http://127.0.0.1:%s" % int(cfg.get("bridge_origin_port") or 8299)


# --------------------------------------------------------------------------- adb (read-only + forward)
def _adb_exe() -> str:
    found = shutil.which("adb")
    if found:
        return found
    if _ADB_FALLBACK and os.path.isfile(_ADB_FALLBACK):
        return _ADB_FALLBACK
    return "adb"


def adb_devices() -> dict[str, Any]:
    rc, out, err = _run([_adb_exe(), "devices", "-l"], timeout=10.0)
    if rc == 127:
        return {"adb": False, "devices": [], "ready": [], "state": UNKNOWN, "error": "ADB_NOT_FOUND"}
    if rc != 0:
        return {"adb": True, "devices": [], "ready": [], "state": UNKNOWN, "error": "ADB_ERROR", "stderr": (err or "")[:200]}
    devices = []
    for line in (out or "").splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            d = {"serial": parts[0], "state": parts[1]}
            for p in parts[2:]:
                if p.startswith("model:"):
                    d["model"] = p[6:]
            devices.append(d)
    ready = [d for d in devices if d["state"] == "device"]
    if ready:
        err_code = None
    elif any(d["state"] == "unauthorized" for d in devices):
        err_code = "DEVICE_UNAUTHORIZED"
    elif devices:
        err_code = "DEVICE_OFFLINE"
    else:
        err_code = "NO_DEVICE"
    return {
        "adb": True,
        "devices": devices,
        "ready": ready,
        # Kein Geraet am Kabel = temporaer nicht verfuegbar, nicht defekt.
        "state": ONLINE if ready else UNAVAILABLE_TEMPORARILY,
        "error": err_code,
    }


_ADB_MESSAGES = {
    "ADB_NOT_FOUND": "adb nicht gefunden (Android SDK platform-tools fehlen im PATH)",
    "NO_DEVICE": "Kein Android-Geraet per ADB verbunden: USB-Kabel anstecken, USB-Debugging aktiv lassen",
    "DEVICE_UNAUTHORIZED": "Geraet nicht autorisiert: USB-Debugging-Dialog auf dem Handy bestaetigen",
    "DEVICE_OFFLINE": "Geraet im ADB-Zustand offline: Kabel neu stecken oder 'adb reconnect'",
    "SERIAL_NOT_FOUND": "Gewuenschtes Geraet (Serial) nicht verbunden",
    "FORWARD_FAILED": "adb forward konnte nicht eingerichtet werden",
    "ADB_ERROR": "adb devices lieferte einen Fehler",
}


def _pick_serial(info: dict[str, Any], preferred: str = "") -> tuple[str, str | None]:
    ready = info.get("ready") or []
    if preferred:
        for d in info.get("devices") or []:
            if d["serial"] == preferred:
                if d["state"] == "device":
                    return preferred, None
                return "", ("DEVICE_UNAUTHORIZED" if d["state"] == "unauthorized" else "DEVICE_OFFLINE")
        if ready:
            # configured serial missing but another ready device exists -> use it, but say so
            return ready[0]["serial"], None
        return "", info.get("error") or "NO_DEVICE"
    if ready:
        return ready[0]["serial"], None
    return "", info.get("error") or "NO_DEVICE"


def _adb_ready_serial(preferred: str = "") -> str:
    serial, _ = _pick_serial(adb_devices(), preferred or load_config().get("phone_adb_serial") or "")
    return serial


def _forward_list() -> list[tuple[str, str, str]]:
    rc, out, _ = _run([_adb_exe(), "forward", "--list"], timeout=8.0)
    entries = []
    if rc == 0:
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) == 3:
                entries.append((parts[0], parts[1], parts[2]))
    return entries


def ensure_adb_forward(local_port: int = PHASE_A_FORWARD_PORT, remote_port: int = PHASE_A_REMOTE_PORT,
                       serial: str = "", *, cleanup_legacy: bool = True,
                       devices: dict[str, Any] | None = None) -> dict[str, Any]:
    """Idempotent adb forward setup with conflict + legacy cleanup.

    Never runs a shell on the device. Returns {ok, code, message, serial, actions}.
    Called on every status cycle, so a re-plugged phone gets its forward back
    automatically (reconnect recovery)."""
    info = devices if devices is not None else adb_devices()
    if not info.get("adb"):
        return {"ok": False, "code": "ADB_NOT_FOUND", "message": _ADB_MESSAGES["ADB_NOT_FOUND"], "serial": None, "actions": []}
    preferred = serial or str(load_config().get("phone_adb_serial") or "")
    sel, err = _pick_serial(info, preferred)
    if not sel:
        code = err or "NO_DEVICE"
        return {"ok": False, "code": code, "message": "ADB forward to Termux agent failed: " + _ADB_MESSAGES.get(code, code),
                "serial": None, "actions": [], "devices": info.get("devices")}
    adb = _adb_exe()
    want_l, want_r = "tcp:%d" % int(local_port), "tcp:%d" % int(remote_port)
    actions: list[str] = []
    entries = _forward_list()
    if cleanup_legacy:
        for s, l, r in entries:
            if any(r == "tcp:%d" % p for p in LEGACY_AGENT_REMOTE_PORTS):
                rc, _, _ = _run([adb, "-s", s, "forward", "--remove", l], timeout=8.0)
                actions.append("removed legacy forward %s %s->%s rc=%s" % (s, l, r, rc))
        if actions:
            entries = _forward_list()
    if (sel, want_l, want_r) in entries:
        return {"ok": True, "code": "FORWARD_PRESENT", "message": "forward vorhanden", "serial": sel,
                "forward": "%s->%s" % (want_l, want_r), "actions": actions}
    for s, l, r in entries:
        if l == want_l:
            rc, _, _ = _run([adb, "-s", s, "forward", "--remove", l], timeout=8.0)
            actions.append("removed conflicting forward %s %s->%s rc=%s" % (s, l, r, rc))
    rc, _, err_txt = _run([adb, "-s", sel, "forward", want_l, want_r], timeout=10.0)
    if rc != 0:
        return {"ok": False, "code": "FORWARD_FAILED",
                "message": "ADB forward to Termux agent failed: %s (%s)" % (_ADB_MESSAGES["FORWARD_FAILED"], (err_txt or "").strip()[:160]),
                "serial": sel, "actions": actions}
    ok = (sel, want_l, want_r) in _forward_list()
    actions.append("created forward %s %s->%s" % (sel, want_l, want_r))
    return {"ok": ok, "code": "FORWARD_CREATED" if ok else "FORWARD_FAILED",
            "message": "forward eingerichtet" if ok else "ADB forward to Termux agent failed: forward nicht in --list",
            "serial": sel, "forward": "%s->%s" % (want_l, want_r), "actions": actions}


def _termux_packages(serial: str) -> list[str]:
    cache = _STATE.get("pkg") or {}
    if cache.get("serial") == serial and time.time() - float(cache.get("ts") or 0) < 600:
        return list(cache.get("pkgs") or [])
    rc, so, _ = _run([_adb_exe(), "-s", serial, "shell", "pm", "list", "packages", "com.termux"], timeout=15.0)
    pkgs = [ln.strip().replace("package:", "") for ln in (so or "").splitlines() if ln.strip().startswith("package:")]
    if rc == 0:
        _STATE["pkg"] = {"serial": serial, "ts": time.time(), "pkgs": pkgs}
    return pkgs


def phone_status(preferred: str = "") -> dict[str, Any]:
    """Legacy-compatible phone/adb view (used by tests and older callers)."""
    info = adb_devices()
    serial, err = _pick_serial(info, preferred or str(load_config().get("phone_adb_serial") or ""))
    out: dict[str, Any] = {
        "device": serial or None,
        "adb": info.get("adb", False),
        "adb_state": info.get("state", UNKNOWN),
        "adb_devices": info.get("devices", []),
    }
    if not serial:
        out.update({"state": UNAVAILABLE_TEMPORARILY if info.get("adb") else UNKNOWN,
                    "termux": UNAVAILABLE_TEMPORARILY, "termux_packages": [], "error": err})
        return out
    out["state"] = ONLINE
    pkgs = _termux_packages(serial)
    out["termux_packages"] = pkgs
    out["termux"] = ONLINE if "com.termux" in pkgs else OFFLINE
    out["termux_api"] = "com.termux.api" in pkgs
    out["termux_boot"] = "com.termux.boot" in pkgs
    if "com.termux" not in pkgs:
        out["error"] = "termux_not_installed"
    return out


def phase_a_health(serial: str = "", timeout: float = 3.0, devices: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-only Phase-A control probe through adb forward (GET /health only)."""
    cfg = load_config()
    lp = int(cfg.get("phase_a_forward_port") or PHASE_A_FORWARD_PORT)
    rp = int(cfg.get("phase_a_port") or PHASE_A_REMOTE_PORT)
    fw = ensure_adb_forward(lp, rp, serial, devices=devices)
    base = {"agent_port": rp, "forward_port": lp, "forward": fw, "chat_capable": False}
    if not fw.get("ok"):
        return {**base, "reachable": False, "status": UNAVAILABLE_TEMPORARILY, "reason": fw.get("code"),
                "error": fw.get("message"), "device": fw.get("serial")}
    res = _http("http://127.0.0.1:%d/health" % lp, timeout=timeout)
    base["device"] = fw.get("serial")
    if res.get("ok"):
        data = _json_body(res)
        if str(data.get("status", "")).upper() == "PASS" and data.get("service") == "NEXUS_PHASE_A_CONTROL":
            return {**base, "reachable": True, "status": ONLINE, "agent": data.get("service"), "bind": data.get("bind"),
                    "version": data.get("version"), "code_sha256": data.get("code_sha256"), "pid": data.get("pid"),
                    "local_ai": data.get("local_ai"), "health": {k: v for k, v in data.items() if k != "local_ai"}}
        return {**base, "reachable": False, "status": FAILED, "reason": "UNEXPECTED_HEALTH_PAYLOAD",
                "error": json.dumps(data)[:160]}
    if res.get("code"):
        return {**base, "reachable": False, "status": FAILED, "reason": res.get("error"), "error": res.get("error")}
    kind = res.get("error") or "UNREACHABLE"
    reason = "AGENT_NOT_LISTENING" if kind in ("REMOTE_CLOSED", "CONNECTION_REFUSED") else "AGENT_" + kind
    return {**base, "reachable": False, "status": OFFLINE, "reason": reason, "error": res.get("error_detail") or kind}


def termux_agent_status(preferred: str = "", timeout: float = 3.0) -> dict[str, Any]:
    """Legacy entry point (PhoneLocalProvider): phone agent = Phase-A control 8766.

    Uses the monitor snapshot when it is fresh, otherwise probes directly."""
    with _LOCK:
        snap = _STATE.get("snapshot")
        age = time.time() - float(_STATE.get("ts") or 0.0)
    if snap and age < 90 and not preferred:
        pa = deepcopy((snap.get("phone") or {}).get("phase_a") or {})
        if pa:
            pa["from_snapshot_age_sec"] = round(age, 1)
            return pa
    return phase_a_health(preferred, timeout=min(float(timeout or 3.0), 5.0))


# --------------------------------------------------------------------------- inbound clients (PHONE_CORE link)
def note_client(ip: str, path: str) -> None:
    """Called by the HTTP server for every request: remembers the last request per client IP (memory only)."""
    try:
        if not ip or ip.startswith("127.") or ip == "::1":
            return
        with _LOCK:
            cl = _STATE.setdefault("clients", {})
            cl[ip] = {"ts": time.time(), "path": str(path)[:80]}
            if len(cl) > 64:
                for k in sorted(cl, key=lambda x: cl[x]["ts"])[:16]:
                    cl.pop(k, None)
    except Exception:
        pass


def _phone_ips(cfg: dict[str, Any], peer: dict[str, Any] | None, ident_chk: dict[str, Any] | None) -> list[str]:
    ips = {str(cfg.get("phone_tailscale_ip") or "")}
    if peer and peer.get("ip"):
        ips.add(str(peer["ip"]))
    for ip in (ident_chk or {}).get("phone_ipv4") or []:
        if ip and not ip.startswith("127."):
            ips.add(ip)
    return sorted(i for i in ips if i)


def _last_phone_request(ips: list[str]) -> dict[str, Any] | None:
    with _LOCK:
        cl = dict(_STATE.get("clients") or {})
    best = None
    for ip in ips:
        c = cl.get(ip)
        if c and (best is None or c["ts"] > best["ts"]):
            best = dict(c, ip=ip)
    return best


# --------------------------------------------------------------------------- tailscale / network
def _tailscale_status() -> dict[str, Any]:
    if not os.path.isfile(_TAILSCALE_EXE):
        return {"available": False, "error": "TAILSCALE_NOT_INSTALLED"}
    rc, out, err = _run([_TAILSCALE_EXE, "status", "--json"], timeout=8.0)
    if rc != 0 or not out.strip():  # MH05: einmal wiederholen (Lastspitzen), Fehler sichtbar machen
        rc, out, err = _run([_TAILSCALE_EXE, "status", "--json"], timeout=12.0)
    if rc != 0 or not out.strip():
        return {"available": True, "running": False, "error": "TAILSCALE_STATUS_RC_%s" % rc}
    try:
        data = json.loads(out)
    except Exception:
        return {"available": True, "running": False, "error": "TAILSCALE_JSON"}
    return {"available": True, "running": str(data.get("BackendState", "")) == "Running", "data": data}


def _tailscale_peer(ts: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Find the configured phone peer. Default match is the configured Tailscale IP;
    the host name is only used when phone_peer_match == "host" or no IP is configured."""
    data = ts.get("data") or {}
    ip = str(cfg.get("phone_tailscale_ip") or "").strip()
    host = str(cfg.get("phone_tailscale_host") or "").strip().lower()
    mode = str(cfg.get("phone_peer_match") or "ip").strip().lower()
    by_host = mode == "host" or not ip
    for peer in (data.get("Peer") or {}).values():
        ips = peer.get("TailscaleIPs") or []
        hn = str(peer.get("HostName") or "").lower()
        dns = str(peer.get("DNSName") or "").lower()
        hit = None
        if ip and not by_host and ip in ips:
            hit = "ip"
        elif by_host and host and (hn == host or dns.split(".")[0] == host):
            hit = "host"
        if hit:
            return {"host": peer.get("HostName"), "dns": peer.get("DNSName"), "ip": (ips or [None])[0],
                    "online": bool(peer.get("Online")), "active": bool(peer.get("Active")),
                    "last_seen": peer.get("LastSeen"), "last_handshake": peer.get("LastHandshake"),
                    "os": peer.get("OS"), "relay": peer.get("Relay"), "cur_addr": peer.get("CurAddr"),
                    "matched_by": hit, "configured_ip": ip or None, "configured_host": host or None}
    return None


def tailscale_ping(target: str, cfg: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    """Real reachability probe: tailscale ping --c 1 --timeout 3s <target>. Cached."""
    cfg = cfg or load_config()
    ttl_ok = float(cfg.get("phone_ping_ttl_sec") or 90)
    cached = _STATE.get("ts_ping")
    if cached and not force and cached.get("target") == target:
        ttl = ttl_ok if cached.get("ok") else min(45.0, ttl_ok)
        if time.time() - cached.get("ts", 0) < ttl:
            return dict(cached, cached=True)
    if not os.path.isfile(_TAILSCALE_EXE):
        out = {"ok": None, "error": "TAILSCALE_NOT_INSTALLED"}
    else:
        t0 = time.time()
        rc, so, se = _run([_TAILSCALE_EXE, "ping", "--c", "1", "--timeout", "3s", target], timeout=10.0)
        text = ((so or "") + " " + (se or "")).strip()
        m = re.search(r"pong from (\S+) \(([^)]+)\) via (.+?) in ([0-9.]+)\s*ms", text)
        if m:
            out = {"ok": True, "pong_from": m.group(1), "ip": m.group(2), "via": m.group(3).strip(),
                   "latency_ms": float(m.group(4))}
        elif rc == 124 and (se or "").strip() == "timeout" and not (so or "").strip():
            out = {"ok": None, "error": "TAILSCALE_PING_TIMEOUT_LOCAL"}
        else:
            out = {"ok": False, "error": "NO_PONG", "rc": rc, "output": text[:160]}
        out["ms"] = int((time.time() - t0) * 1000)
    out.update({"target": target, "ts": time.time(), "checked_at": _iso(),
                "method": "tailscale_ping", "source": "tailscale ping --c 1 --timeout 3s %s (cache %ds)" % (target, int(ttl_ok))})
    _STATE["ts_ping"] = out
    return dict(out, cached=False)


def _peer_identity(serial: str, peer: dict[str, Any] | None, cfg: dict[str, Any]) -> dict[str, Any]:
    """ADB-side cross check (read-only getprop / ip addr), only when ADB is present. Cached for the process."""
    prev = _STATE.get("peer_identity")
    if not serial:
        return prev or {"status": "UNVERIFIED", "reason": "NO_ADB", "checked_at": None}
    if prev and prev.get("serial") == serial and time.time() - prev.get("ts", 0) < 3600:
        return prev
    adb = _adb_exe()
    _, model, _ = _run([adb, "-s", serial, "shell", "getprop", "ro.product.model"], timeout=6.0)
    _, dev, _ = _run([adb, "-s", serial, "shell", "getprop", "ro.product.device"], timeout=6.0)
    _, ipout, _ = _run([adb, "-s", serial, "shell", "ip", "-4", "addr"], timeout=6.0)
    _, sn, _ = _run([adb, "-s", serial, "shell", "getprop", "ro.serialno"], timeout=6.0)
    model, dev, sn = (model or "").strip(), (dev or "").strip(), (sn or "").strip()
    phone_ips = re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", ipout or "")
    exp_model = str(cfg.get("phone_expected_model") or "")
    # 005D: Identitaet ueber Seriennummer; Tailscale-IP ueber die Peer-Liste von `tailscale status`
    # (Android-Tailscale ist ein VPN-tun und taucht in `adb shell ip -4 addr` oft nicht auf).
    exp_sn = ""
    try:
        exp_sn = str(json.loads(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "workspace", "phone_data", "phone_identity.json"), encoding="utf-8").read()).get("serialno") or "")
    except Exception:
        pass
    cfg_ip = str(cfg.get("phone_tailscale_ip") or "")
    peer_ip = (peer or {}).get("ip") or cfg_ip
    ip_match = bool(peer and peer_ip and (not cfg_ip or peer_ip == cfg_ip))
    serial_match = bool(sn and exp_sn and sn == exp_sn)
    model_match = (not exp_model) or model == exp_model
    if (sn and exp_sn and not serial_match) or (model and not model_match):
        status = "MISMATCH"
    elif (serial_match or not exp_sn) and model_match and ip_match:
        status = "VERIFIED"
    else:
        status = "UNVERIFIED"
    out = {"status": status, "serial": serial, "model": model, "device": dev, "expected_model": exp_model,
           "serialno": sn, "expected_serialno": exp_sn, "serial_match": serial_match,
           "phone_ipv4": phone_ips, "tailscale_ip": peer_ip, "ip_match": ip_match, "model_match": model_match,
           "tailscale_peer_online": (peer or {}).get("online"),
           "source": "adb getprop ro.serialno/model/device + tailscale status peer list (read-only)",
           "ts": time.time(), "checked_at": _iso()}
    _STATE["peer_identity"] = out
    return out


def _funnel_on() -> bool | None:
    if not os.path.isfile(_TAILSCALE_EXE):
        return None
    rc, fout, _ = _run([_TAILSCALE_EXE, "funnel", "status"], timeout=8.0)
    if rc != 0:
        return None
    return "(Funnel on)" in (fout or "")


def _public_probe(url: str, ttl: float) -> dict[str, Any]:
    cached = _STATE.get("public")
    if cached and cached.get("url") == url and time.time() - cached.get("ts", 0) < ttl:
        return cached
    res = _http(url.rstrip("/") + "/api/bridge/health", timeout=8.0)
    out = {"url": url, "ts": time.time(), "checked_at": _iso(), "code": res.get("code"), "error": res.get("error")}
    _STATE["public"] = out
    return out


def network_status() -> dict[str, Any]:
    """Legacy-compatible network block (tailscale + funnel + gateway)."""
    cfg = load_config()
    ts = _tailscale_status()
    return _network_from(ts, cfg)


def _network_from(ts: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    if not ts.get("available"):
        return {"status": UNKNOWN, "tailscale": UNKNOWN, "funnel": None, "error": ts.get("error"),
                "gateway_port": int(cfg.get("bridge_gateway_port") or 8300)}
    running = bool(ts.get("running"))
    dns = ((ts.get("data") or {}).get("Self") or {}).get("DNSName") or ""
    funnel = _funnel_on() if running else None
    gw_port = int(cfg.get("bridge_gateway_port") or 8300)
    gw = _http("http://127.0.0.1:%d/api/bridge/health" % gw_port, timeout=3.0)
    gateway_ok = gw.get("code") in (200, 401)
    url_file = _BRIDGE_STATE_DIR / "tunnel.url"
    public_url = url_file.read_text(encoding="utf-8").strip() if url_file.is_file() else ""
    pub = _public_probe(public_url, float(cfg.get("public_probe_ttl_sec") or 300)) if (public_url and running and funnel) else None
    public_ok = bool(pub and pub.get("code") in (200, 401))
    if not running:
        status = OFFLINE
    elif gateway_ok and funnel and public_ok:
        status = ONLINE
    else:
        status = DEGRADED
    return {
        "status": status,
        "tailscale": ONLINE if running else OFFLINE,
        "tailscale_dns": dns,
        "funnel": funnel,
        "public_url": public_url,
        "public_probe": pub,
        "gateway_local": ONLINE if gateway_ok else OFFLINE,
        "gateway_code": gw.get("code"),
        "gateway_port": gw_port,
        "error": None if running else (ts.get("error") or "BACKEND_NOT_RUNNING"),
    }


# --------------------------------------------------------------------------- local AI
def _configured_model() -> str:
    try:
        raw = json.loads((APEX / "ai_models.json").read_text(encoding="utf-8"))
        m = str(raw.get("chat_model") or "").strip()
        if m:
            return m
    except Exception:
        pass
    env = (os.environ.get("NEXUS_MODEL") or "").strip()
    return env or "qwen3.5:9b"


def _ollama_url() -> str:
    return (os.environ.get("NEXUS_OLLAMA_URL") or "http://127.0.0.1:11434").rstrip("/")


def _ollama_process() -> dict[str, Any]:
    if os.name != "nt":
        rc, so, _ = _run(["pgrep", "-x", "ollama"], timeout=4.0)
        return {"running": rc == 0 and bool(so.strip()), "pids": so.split()[:4], "source": "pgrep -x ollama"}
    rc, so, _ = _run(["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/FO", "CSV", "/NH"], timeout=6.0)
    pids = re.findall(r'"ollama\.exe","(\d+)"', so or "", flags=re.I)
    return {"running": bool(pids), "pids": pids[:4], "source": "tasklist ollama.exe"}


def _infer_keep_alive(loaded: bool) -> Any:
    """2026-10-29: Pruefung entlaedt das Chatmodell nicht mehr (vorher keep_alive 0 -> Kaltstart je Zyklus)."""
    try:
        from nexus.common.ai_layer import keep_alive_value
        ka = keep_alive_value()
    except Exception:
        ka = "30m"
    return ka if ka not in (0, "0", None, "") else ("10m" if loaded else 0)


def _ollama_infer(base: str, model: str, cfg: dict[str, Any], force: bool = False) -> dict[str, Any]:
    """Minimal real inference: /api/generate num_predict=1, keep_alive=0 (unloads right after). Cached."""
    ttl_ok = float(cfg.get("ai_inference_ttl_sec") or 600)
    cached = _STATE.get("ai_infer")
    if cached and not force and cached.get("model") == model and cached.get("base") == base:
        ttl = ttl_ok if cached.get("ok") else 120.0
        if time.time() - cached.get("ts", 0) < ttl:
            return dict(cached, cached=True)
    # Ist das Modell gerade geladen (z. B. aktiver KI-Anruf hält es warm), entlädt die Prüfung es nicht
    # (keep_alive 0 hätte den laufenden Anruf auf ~5 s Neuladen je Zug zurückgeworfen). Sonst unverändert 0.
    loaded = False
    try:
        ps = _json_body(_http(base + "/api/ps", timeout=3.0))
        loaded = any(str(m.get("name") or m.get("model")) == model for m in (ps.get("models") or []))
    except Exception:
        loaded = False
    body = json.dumps({"model": model, "prompt": "OK", "stream": False, "keep_alive": _infer_keep_alive(loaded),
                       "options": {"num_predict": 1, "temperature": 0, "num_ctx": __import__("nexus.common.ai_layer", fromlist=["x"]).unified_num_ctx()}}).encode("utf-8")
    t0 = time.time()
    res = _http(base + "/api/generate", method="POST", body=body, timeout=120.0, headers={"Content-Type": "application/json"})
    data = _json_body(res)
    ok = bool(res.get("ok")) and ("response" in data) and bool(data.get("done", True))
    out = {"ok": ok, "model": model, "base": base, "ms": int((time.time() - t0) * 1000),
           "eval_count": data.get("eval_count"), "http_status": res.get("code"),
           "error": None if ok else (data.get("error") or res.get("error") or "NO_RESPONSE"),
           "ts": time.time(), "checked_at": _iso(),
           "source": "POST %s/api/generate num_predict=1 keep_alive=0 (cache %ds, never_pull)" % (base, int(ttl_ok))}
    _STATE["ai_infer"] = out
    return dict(out, cached=False)


def wozipc_local_status(client: Any = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Real Ollama check: process, port 11434, /api/tags, model present, minimal inference. Never pulls models.
    ONLINE only after a successful inference."""
    cfg = cfg or load_config()
    model = _configured_model()
    base = _ollama_url()
    proc = _ollama_process()
    tags = _http(base + "/api/tags", timeout=3.0)
    if not tags.get("ok"):
        if tags.get("code"):
            # service answered (HTTP error) -> reachable, but not usable
            return {"provider": PROVIDER_WOZIPC_LOCAL, "reachable": True, "status": FAILED,
                    "reason": "HTTP_%s" % tags.get("code"), "http_status": tags.get("code"),
                    "model": model, "source": "GET %s/api/tags" % base}
        return {"provider": PROVIDER_WOZIPC_LOCAL, "reachable": False, "status": OFFLINE,
                "reason": "OLLAMA_NOT_RUNNING" if not proc.get("running") else "OLLAMA_PORT_UNREACHABLE",
                "error": tags.get("error"), "model": model, "process": proc, "port_open": False,
                "source": "tasklist ollama + GET %s/api/tags" % base}
    names = [m.get("name") for m in (_json_body(tags).get("models") or [])]
    present = model in names or (model + ":latest") in names
    ps = _http(base + "/api/ps", timeout=3.0)
    loaded = [m.get("name") for m in (_json_body(ps).get("models") or [])] if ps.get("ok") else None
    if not present:
        return {"provider": PROVIDER_WOZIPC_LOCAL, "reachable": True, "status": FAILED, "reason": "MODEL_MISSING",
                "model": model, "models": names, "loaded": loaded, "process": proc, "source": "GET %s/api/tags" % base}
    if loaded:
        # Nexus-UI 2026-10-15: belegtes Ollama ist BUSY, nicht FAILED. Kein Inferenztest, der ein zweites Modell laedt.
        return {"provider": PROVIDER_WOZIPC_LOCAL, "reachable": True, "status": BUSY, "reason": "MODEL_LOADED",
                "error": "geladen: " + ",".join(str(x) for x in loaded), "model": model, "model_present": True,
                "models": names, "loaded": loaded, "process": proc, "port_open": True, "never_pull": True,
                "model_loaded": model in loaded, "checked_at": _iso(),
                "source": "tasklist ollama + GET %s/api/tags + GET /api/ps (belegt -> kein Inferenztest)" % base}
    inf = _ollama_infer(base, model, cfg)
    common = {"provider": PROVIDER_WOZIPC_LOCAL, "reachable": True, "model": model, "model_present": True,
              "models": names, "loaded": loaded, "process": proc, "port_open": True, "inference": inf,
              "keep_alive": 0, "never_pull": True,
              "source": "tasklist ollama + GET %s/api/tags + POST /api/generate (num_predict=1, keep_alive=0)" % base}
    if inf.get("ok"):
        return dict(common, status=ONLINE, model_loaded=bool(loaded and model in loaded))
    return dict(common, status=FAILED, reason="INFERENCE_FAILED", error=inf.get("error"), http_status=inf.get("http_status"))


def _last_real_chat(max_age_sec: float = 1800.0) -> dict[str, Any] | None:
    """2026-10-28 MH02: letzte echte, erfolgreiche Chat-Antwort aus dem Verlauf als Beleg (kein Testcall)."""
    try:
        from datetime import datetime as _d
        from nexus.chat import engine as _eng
        data = json.loads(_eng.HISTORY_FILE.read_text(encoding="utf-8")).get("sessions", {})
        best = None
        for s in data.values():
            for m in s.get("messages") or []:
                if m.get("role") == "assistant" and m.get("ts") and (m.get("success") is not False):
                    if best is None or str(m["ts"]) > str(best["ts"]):
                        best = m
        if not best:
            return None
        ts = _d.fromisoformat(str(best["ts"]).replace("Z", "+00:00"))
        age = (_d.now(ts.tzinfo) - ts).total_seconds() if ts.tzinfo else (_d.now() - ts).total_seconds()
        if age > max_age_sec:
            return None
        return _field(ONLINE, "chat_history.json (letzte echte Antwort)", "letzte Antwort vor %ds" % int(age),
                      reason="RECENT_REAL_CHAT", checked_at=_iso())
    except Exception:
        return None


def chatgpt_optional_status() -> dict[str, Any]:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    enabled = (os.environ.get("NEXUS_CHATGPT_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")
    model = (os.environ.get("NEXUS_OPENAI_MODEL") or "gpt-5.6").strip()
    return {
        "provider": PROVIDER_CHATGPT_OPTIONAL,
        "configured": bool(key),
        "explicitly_enabled": enabled,
        "status": OPTIONAL,
        "model": model,
        "blocks_health": False,
        "blocks_startup": False,
    }


# --------------------------------------------------------------------------- voice / room / chat
def _voice_check(cfg: dict[str, Any], force: bool = False) -> dict[str, Any]:
    """Real TTS check via POST /api/voice/tts -> audio/mpeg. Cached (no synth per poll)."""
    cached = _STATE.get("voice")
    ttl_ok = float(cfg.get("voice_check_ttl_sec") or 600)
    if cached and not force:
        ttl = ttl_ok if cached.get("status") == ONLINE else 120.0
        if time.time() - cached.get("ts", 0) < ttl:
            return cached
    voice_name = "de-DE-AmalaNeural"
    try:
        vc = json.loads((APEX / "voice_config.json").read_text(encoding="utf-8"))
        voice_name = str(vc.get("tts_voice") or voice_name)
    except Exception:
        pass
    body = json.dumps({"text": "NEXUS Statuscheck."}).encode("utf-8")
    t0 = time.time()
    res = _http(_origin() + "/api/voice/tts", method="POST", body=body, timeout=25.0,
                headers={"Content-Type": "application/json"})
    ms = int((time.time() - t0) * 1000)
    ctype = str(res.get("content_type") or "")
    size = len(res.get("body") or b"")
    if res.get("ok") and ctype.startswith("audio/mpeg") and size >= 1000:
        out = {"status": ONLINE, "detail": "TTS %s -> audio/mpeg %d B in %d ms" % (voice_name, size, ms)}
    elif res.get("ok") or res.get("code"):
        err = _json_body(res).get("error") if not ctype.startswith("audio") else "too_small"
        out = {"status": FAILED, "reason": "TTS_" + str(err or res.get("error") or "NO_AUDIO").upper()[:40],
               "detail": "content_type=%s bytes=%d" % (ctype, size)}
    else:
        out = {"status": UNKNOWN, "reason": "ORIGIN_UNREACHABLE", "detail": str(res.get("error"))}
    out.update({"ts": time.time(), "checked_at": _iso(), "voice": voice_name, "ms": ms,
                "source": "POST %s/api/voice/tts (cache %ds)" % (_origin(), int(ttl_ok))})
    _STATE["voice"] = out
    return out


def voice_status(status_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Legacy helper: module flags only (kept for callers); real check is _voice_check."""
    if status_payload is None:
        try:
            from nexus.voice.voice_manager import VoiceManager

            status_payload = VoiceManager().status()
        except Exception as e:
            return {"status": UNKNOWN, "error": type(e).__name__}
    tts = bool((status_payload.get("tts") or {}).get("edge_tts"))
    stt = bool((status_payload.get("stt") or {}).get("available"))
    speak = bool(status_payload.get("speak_pc"))
    return {"status": ONLINE if tts else OFFLINE, "tts_edge": tts, "stt_whisper": stt, "speak_pc": speak,
            "pipeline": "STT -> NEXUS -> Local Provider -> TTS", "echo_loop_guard": True,
            "note": "Modul-Flags; SYSTEM-Status nutzt echten TTS-Check"}


ROOM_HEALTH_SESSION = "NEXUS-OS-HEALTH"
ROOM_HEALTH_KEEP = 10


def room_check(cfg: dict[str, Any] | None = None, force: bool = False) -> dict[str, Any]:
    """Real Shared-Room roundtrip over the HTTP API on a dedicated health session
    (never a chat session): POST marker -> GET status -> marker id must come back.
    The health session is trimmed to the last ROOM_HEALTH_KEEP markers. Cached."""
    cfg = cfg or load_config()
    ttl_ok = float(cfg.get("room_check_ttl_sec") or 600)
    cached = _STATE.get("room_check")
    if cached and not force:
        ttl = ttl_ok if cached.get("status") == ONLINE else 120.0
        if time.time() - cached.get("ts", 0) < ttl:
            return cached
    # Nexus-UI 2026-10-15: interner Roundtrip ueber SharedRoom.append/status statt HTTP (Identity-Lock bleibt unveraendert).
    marker = "nexus-os-health %s %s" % (_iso(), uuid.uuid4().hex[:8])
    t0 = time.time()
    out: dict[str, Any]
    try:
        from nexus.chat.shared_room import SharedRoom

        sr = SharedRoom()
        wd = sr.append(session_id=ROOM_HEALTH_SESSION, sender_type="system", sender_name="NEXUS OS",
                       content=marker, source="nexus_os_health", role="system")
        mid = ((wd.get("message") or {}).get("message_id")) if wd.get("success") else None
        if not mid:
            out = {"status": FAILED, "reason": "ROOM_WRITE_" + str(wd.get("blocked") or "UNKNOWN").upper(),
                   "detail": "SharedRoom.append -> %s" % wd.get("blocked")}
        else:
            rd = sr.status(ROOM_HEALTH_SESSION)
            back = ((rd.get("last_message") or {}).get("message_id"))
            if back == mid:
                out = {"status": ONLINE, "detail": "Roundtrip write+read ok (%d ms), max_rounds=%s" % (int((time.time() - t0) * 1000), rd.get("max_rounds")),
                       "max_rounds": rd.get("max_rounds")}
            else:
                out = {"status": FAILED, "reason": "ROOM_READBACK_MISMATCH", "detail": "marker %s nicht zurueckgelesen" % mid}
    except Exception as e:
        out = {"status": FAILED, "reason": "ROOM_" + type(e).__name__.upper(), "detail": str(e)[:200]}
        mid = None
    if mid:
        try:  # bounded health session, never touches chat sessions
            from nexus.chat.shared_room import SharedRoom

            sr = SharedRoom()
            room = sr.load(ROOM_HEALTH_SESSION)
            msgs = room.get("messages") or []
            if len(msgs) > ROOM_HEALTH_KEEP:
                room["messages"] = msgs[-ROOM_HEALTH_KEEP:]
                sr._save(room)
        except Exception:
            pass
    out.update({"ts": time.time(), "checked_at": _iso(), "session": ROOM_HEALTH_SESSION,
                "source": "intern nexus.chat.shared_room.SharedRoom.append + .status (session %s, cache %ds)" % (ROOM_HEALTH_SESSION, int(ttl_ok))})
    _STATE["room_check"] = out
    return out


def room_status(status_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        from nexus.chat.shared_room import SharedRoom

        st = SharedRoom().status("NEXUS-OS-PROBE")
        ok = bool(st.get("success"))
        return {"status": ONLINE if ok else FAILED, "max_rounds": st.get("max_rounds"),
                "guards": ["duplicate", "same_sender", "max_rounds", "self_routing"],
                "source": "nexus.chat.shared_room.SharedRoom.status (store read)"}
    except Exception as e:
        return {"status": FAILED, "reason": type(e).__name__, "source": "nexus.chat.shared_room"}


def run_chat_e2e(timeout: float = 90.0) -> dict[str, Any]:
    """On-demand chat E2E (local model). Not part of the periodic cycle."""
    marker = "NEXUS_E2E_%d" % int(time.time())
    body = json.dumps({"message": "Antworte nur mit dem Wort OK. " + marker, "session_id": "NEXUS-OS-E2E"}).encode("utf-8")
    t0 = time.time()
    res = _http(_origin() + "/api/chat", method="POST", body=body, timeout=timeout,
                headers={"Content-Type": "application/json"})
    data = _json_body(res)
    reply = str(data.get("reply") or "").strip()
    out = {"status": ONLINE if (res.get("ok") and reply) else (FAILED if res.get("code") else UNKNOWN),
           "reply_preview": reply[:80], "ms": int((time.time() - t0) * 1000), "checked_at": _iso(),
           "ts": time.time(), "source": "POST %s/api/chat (on demand)" % _origin(), "error": res.get("error")}
    _STATE["chat_e2e"] = out
    return out


# --------------------------------------------------------------------------- cycle
def _bridge_level() -> str:
    try:
        from nexus.bridge.permissions import process_level

        return str(process_level().value)
    except Exception:
        return "UNKNOWN"


def _probe_cycle(provider: Any = None) -> dict[str, Any]:
    cfg = load_config()
    ident = identity()
    t0 = time.time()
    origin = _origin()

    # ---- 1) device layer: NEXUSPHONE via adb + tailscale peer
    adb = adb_devices()
    ts = _tailscale_status()
    peer = _tailscale_peer(ts, cfg) if ts.get("running") else None
    serial, adb_err = _pick_serial(adb, str(cfg.get("phone_adb_serial") or ""))
    adb_src = "adb devices -l"
    ts_src = "tailscale status --json"
    ping = None
    if peer and peer.get("ip"):
        peer_on = bool(peer.get("online"))
        cached = _STATE.get("ts_ping")
        # stale-cache guard: a cached pong must never outlive a peer that went offline,
        # and a flag change (offline<->online) always forces a fresh live ping.
        force_ping = False
        if _STATE.get("peer_online_prev") is not None and _STATE.get("peer_online_prev") != peer_on:
            force_ping = True
        if not peer_on and cached and cached.get("ok"):
            force_ping = True
        if force_ping:
            _STATE["ts_ping"] = None
        _STATE["peer_online_prev"] = peer_on
        ping = tailscale_ping(peer["ip"], cfg, force=force_ping)
    elif _STATE.get("ts_ping") and _STATE["ts_ping"].get("ok"):
        _STATE["ts_ping"] = None  # peer vanished from tailscale status -> drop cached pong
    ident_chk = _peer_identity(serial, peer, cfg)
    ping_txt = ""
    if ping is not None:
        when = str(ping.get("checked_at") or "")[11:19]
        age = int(time.time() - float(ping.get("ts") or time.time()))
        stamp = " @%s%s" % (when, (" (cache %ds)" % age) if ping.get("cached") else " (live)")
        ping_txt = (("pong %s ms via %s" % (int(ping.get("latency_ms") or 0), ping.get("via"))) if ping.get("ok")
                    else ("kein pong (%s)" % ping.get("error"))) + stamp
    common = dict(tailscale_peer=peer, tailscale_ping=ping, peer_identity={k: ident_chk.get(k) for k in ("status", "model", "ip_match", "checked_at")})
    if serial:
        phone = _field(ONLINE, adb_src, "adb %s%s" % (serial, (" · tailscale %s: %s" % (peer.get("ip"), ping_txt)) if peer else ""),
                       method="adb", transport="adb", serial=serial, **common)
    elif ping is not None and ping.get("ok"):
        phone = _field(ONLINE, ping.get("source"), "tailscale %s %s (kein ADB: %s)" % (peer.get("ip"), ping_txt, adb_err),
                       method="tailscale_ping", transport="tailscale", adb_error=adb_err, checked_at=ping.get("checked_at"), **common)
    elif peer and peer.get("online"):
        # only the coordination-server flag, no reachability proof
        st = DISCONNECTED if (ping is not None and ping.get("ok") is False) else UNKNOWN
        phone = _field(st, (ping or {}).get("source") or ts_src,
                       "nur Tailscale-Peer-Flag online, %s; kein ADB (%s)" % (ping_txt or "kein Ping moeglich", adb_err),
                       reason="TAILSCALE_ONLY", method="none", adb_error=adb_err,
                       checked_at=(ping or {}).get("checked_at") or _iso(), **common)
    elif not adb.get("adb") and not ts.get("available"):
        phone = _field(UNKNOWN, adb_src + " + " + ts_src, "weder adb noch tailscale verfuegbar", reason="NO_STATUS_SOURCE", method="none")
    else:
        reason = "DEVICE_DISCONNECTED" if adb_err in (None, "NO_DEVICE") else adb_err
        phone = _field(DISCONNECTED, adb_src + " + " + ts_src,
                       (_ADB_MESSAGES.get(adb_err or "", "") or "kein Geraet")
                       + ((" · Tailscale-Peer offline · %s" % (ping_txt or "kein Ping")) if peer
                          else " · Tailscale-Peer %s nicht gefunden" % (cfg.get("phone_tailscale_ip") or cfg.get("phone_tailscale_host"))),
                       reason=reason, method="none", adb_error=adb_err,
                       checked_at=(ping or {}).get("checked_at") or _iso(), **common)

    # ---- 2) TERMUX (needs adb)
    if serial:
        pkgs = _termux_packages(serial)
        rc, so, _ = _run([_adb_exe(), "-s", serial, "shell", "pidof", "com.termux"], timeout=8.0)
        pid = (so or "").strip()
        if "com.termux" not in pkgs:
            termux = _field(OFFLINE, "adb shell pm list packages", "Termux nicht installiert", reason="TERMUX_NOT_INSTALLED")
        elif rc == 0 and pid:
            termux = _field(ONLINE, "adb shell pidof com.termux", "Termux-Prozess pid %s" % pid, pid=pid,
                            termux_boot="com.termux.boot" in pkgs, termux_api="com.termux.api" in pkgs)
        else:
            termux = _field(OFFLINE, "adb shell pidof com.termux", "Termux installiert, Prozess laeuft nicht",
                            reason="TERMUX_NOT_RUNNING", termux_boot="com.termux.boot" in pkgs, termux_api="com.termux.api" in pkgs)
    elif phone["status"] == ONLINE:
        termux = _field(UNKNOWN, "adb (nicht verfuegbar)", "Termux nur per ADB pruefbar; Handy nur ueber Tailscale sichtbar",
                        reason="NO_ADB_PATH", dependency="ADB")
    elif phone["status"] == UNKNOWN:
        termux = _field(UNKNOWN, "dependency", "Geraetestatus unbekannt", reason="DEVICE_STATE_UNKNOWN", dependency="NEXUSPHONE")
    else:
        termux = _field(UNAVAILABLE_TEMPORARILY, "dependency", "Handy nicht erreichbar", reason=phone.get("reason") or "DEVICE_DISCONNECTED", dependency="NEXUSPHONE")

    # ---- 3) PHONE_AGENT (Phase-A control /health via adb forward)
    if serial:
        pa = phase_a_health(serial, timeout=3.0, devices=adb)
        src = "adb forward tcp:%s->tcp:%s + GET /health" % (pa.get("forward_port"), pa.get("agent_port"))
        if pa.get("status") == ONLINE:
            agent = _field(ONLINE, src, "NEXUS_PHASE_A_CONTROL %s" % (pa.get("version") or "(alte Version ohne version-Feld)"),
                           version=pa.get("version"), code_sha256=pa.get("code_sha256"), forward=pa["forward"].get("code"))
        elif pa.get("status") == OFFLINE and termux["status"] == OFFLINE:
            agent = _field(UNAVAILABLE_TEMPORARILY, src, "Phase-A antwortet nicht (%s)" % pa.get("reason"),
                           reason=pa.get("reason"), dependency="TERMUX", forward=pa["forward"].get("code"))
        else:
            agent = _field(pa.get("status") or UNKNOWN, src, str(pa.get("error") or ""), reason=pa.get("reason"),
                           forward=(pa.get("forward") or {}).get("code"))
    elif phone["status"] == ONLINE:
        pa = {"reachable": False, "status": UNKNOWN, "reason": "NO_ADB_PATH"}
        agent = _field(UNKNOWN, "adb forward (nicht verfuegbar)", "Phase-A bindet nur 127.0.0.1 am Handy; ohne ADB nicht pruefbar",
                       reason="NO_ADB_PATH", dependency="ADB")
    elif phone["status"] == UNKNOWN:
        pa = {"reachable": False, "status": UNKNOWN, "reason": "DEVICE_STATE_UNKNOWN"}
        agent = _field(UNKNOWN, "dependency", "Geraetestatus unbekannt", reason="DEVICE_STATE_UNKNOWN", dependency="NEXUSPHONE")
    else:
        pa = {"reachable": False, "status": UNAVAILABLE_TEMPORARILY, "reason": phone.get("reason")}
        agent = _field(UNAVAILABLE_TEMPORARILY, "dependency", "Handy nicht erreichbar",
                       reason=phone.get("reason") or "DEVICE_DISCONNECTED", dependency="NEXUSPHONE")

    # ---- PHONE_LOCAL_AI (F-03: primaer aus dem App-Heartbeat, Phase-A-Agent nur noch Fallback)
    phone_ai = _phone_ai_from_heartbeat()
    if phone_ai is not None:
        pass
    elif agent["status"] == ONLINE:
        la = pa.get("local_ai")
        if isinstance(la, dict) and la.get("status") == ONLINE:
            phone_ai = _field(ONLINE, "Phase-A /health.local_ai", str(la.get("detail") or ""), models=la.get("models"))
        elif isinstance(la, dict) and la.get("status"):
            phone_ai = _field(OFFLINE if la.get("status") in (OFFLINE, "OFFLINE") else UNKNOWN, "Phase-A /health.local_ai",
                              str(la.get("detail") or ""), reason=la.get("reason") or "PHONE_AI_NOT_RUNNING")
        else:
            phone_ai = _field(UNKNOWN, "Phase-A /health", "laufende Phase-A-Version meldet keinen local_ai-Check",
                              reason="AGENT_HAS_NO_AI_PROBE")
    else:
        dep = "PHONE_AGENT" if phone["status"] == ONLINE else "NEXUSPHONE"
        rsn = phone.get("reason") if dep == "NEXUSPHONE" else ("AGENT_NOT_REACHABLE" if agent["status"] != UNKNOWN else "NO_ADB_PATH")
        # F-03: kein Heartbeat-KI-Feld und kein Agent -> Zustand nicht bekannt (UNKNOWN), nicht OFFLINE/UNAVAILABLE
        phone_ai = _field(UNKNOWN, "dependency", "kein KI-Status im Heartbeat, Agent nicht erreichbar",
                          reason="NO_AI_STATUS_IN_HEARTBEAT" if phone["status"] == ONLINE else (rsn or "DEVICE_DISCONNECTED"), dependency=dep)

    # ---- PHONE_CONTROL: NEXUS can really control the phone = Phase-A health OK + harmless read-only action
    if agent["status"] == ONLINE:
        fwp = int(cfg.get("phase_a_forward_port") or PHASE_A_FORWARD_PORT)
        act = _http("http://127.0.0.1:%d/action" % fwp, method="POST", body=json.dumps({"action": "file_status"}).encode("utf-8"),
                    timeout=5.0, headers={"Content-Type": "application/json"})
        ad = _json_body(act)
        if act.get("ok") and ad.get("status") == "PASS":
            control = _field(ONLINE, "adb forward tcp:%d + POST /action file_status (read-only)" % fwp, "Phase-A steuerbar (file_status PASS)")
        else:
            control = _field(OFFLINE, "adb forward tcp:%d + POST /action file_status (read-only)" % fwp,
                             "Read-only-Task fehlgeschlagen", reason="CONTROL_TASK_FAILED_%s" % (act.get("code") or act.get("error")))
    elif agent["status"] == UNKNOWN:
        control = _field(UNKNOWN, "dependency", "Phase-A nicht pruefbar", reason=agent.get("reason") or "NO_ADB_PATH", dependency="PHONE_AGENT")
    else:
        control = _field(OFFLINE, "dependency", "Phase-A nicht erreichbar", reason=agent.get("reason") or "AGENT_NOT_REACHABLE", dependency="PHONE_AGENT")

    # ---- 4/5) WOZIPC + local AI
    wozi = wozipc_local_status(cfg=cfg)
    wozipc_dev = _field(ONLINE, "local process (nexus_os im NEXUS-Server/Testprozess auf WoZiPC)", socket.gethostname())
    if wozi.get("status") == BUSY and wozi.get("reason") == "MODEL_LOADED" and wozi.get("model_loaded"):
        # MH05 20261028: Ollama laeuft und das konfigurierte Modell ist geladen -> ONLINE (arbeitsbereit), nicht BUSY
        wozi = dict(wozi, status=ONLINE, busy=True, error=None,
                    inference={"ms": None, "cached": False, "checked_at": _iso(), "basis": "GET /api/ps: Modell geladen"})
    wozi_ai = _field(wozi.get("status", UNKNOWN), wozi.get("source", "ollama"),
                     ("%s · Inferenz ok %s ms%s · %d Modelle · ollama pid %s" % (wozi.get("model"), (wozi.get("inference") or {}).get("ms"),
                       " (cache)" if (wozi.get("inference") or {}).get("cached") else "", len(wozi.get("models") or []),
                       ",".join((wozi.get("process") or {}).get("pids") or []) or "?"))
                     if wozi.get("status") == ONLINE else "%s · %s" % (wozi.get("model"), wozi.get("error") or wozi.get("reason") or ""),
                     reason=wozi.get("reason"), model=wozi.get("model"), model_loaded=wozi.get("model_loaded"),
                     reachable=wozi.get("reachable"), http_status=wozi.get("http_status"),
                     inference_ms=(wozi.get("inference") or {}).get("ms"), inference_checked_at=(wozi.get("inference") or {}).get("checked_at"),
                     models=wozi.get("models"), ollama_process=(wozi.get("process") or {}).get("running"))

    ai_states = (wozi_ai["status"], phone_ai["status"])
    if ONLINE in ai_states or BUSY in ai_states:
        glob = _field(ONLINE, "rule: >=1 lokales KI-Backend ONLINE oder BUSY (erreichbar, arbeitet)",
                      " + ".join("%s(%s)" % (n, s) for n, s in ((PROVIDER_WOZIPC_LOCAL, ai_states[0]), (PROVIDER_PHONE_LOCAL, ai_states[1])) if s in (ONLINE, BUSY)))
    elif UNKNOWN in ai_states:
        glob = _field(UNKNOWN, "rule", "kein Backend ONLINE, mindestens eines UNKNOWN", reason="AI_STATE_UNKNOWN")
    else:
        glob = _field(OFFLINE, "rule", "kein lokales KI-Backend erreichbar", reason="NO_LOCAL_AI")

    # ---- core / bridge
    core_res = _http(origin + "/api/health", timeout=6.0)
    core_ok = bool(core_res.get("ok")) and bool(_json_body(core_res).get("success", True))
    core = _field(ONLINE if core_ok else (FAILED if core_res.get("code") else OFFLINE), "GET %s/api/health" % origin,
                  "%s %s" % (ident["system_name"], cfg.get("system_id")) if core_ok else str(core_res.get("error")),
                  reason=None if core_ok else (core_res.get("error") or "ORIGIN_UNREACHABLE"))
    br = _http(origin + "/api/bridge/health", timeout=4.0)
    level = _bridge_level()
    if br.get("code") == 401:
        bridge = _field(ONLINE, "GET %s/api/bridge/health (unauth)" % origin, "erreichbar · Auth erzwungen (401) · Level %s" % level,
                        auth_enforced=True, process_level=level)
    elif br.get("code") == 200:
        bridge = _field(ONLINE, "GET %s/api/bridge/health" % origin, "erreichbar (200) · Level %s" % level, process_level=level)
    elif br.get("code"):
        bridge = _field(FAILED, "GET %s/api/bridge/health" % origin, "HTTP %s" % br.get("code"), reason=br.get("error"))
    else:
        bridge = _field(OFFLINE, "GET %s/api/bridge/health" % origin, str(br.get("error")), reason="BRIDGE_UNREACHABLE")

    # ---- network
    net = _network_from(ts, cfg)
    pub = net.get("public_probe") or {}
    net_detail = "tailscale=%s funnel=%s gateway=%s public=%s" % (
        net.get("tailscale"), {True: "on", False: "off", None: "?"}.get(net.get("funnel")), net.get("gateway_code"), pub.get("code"))
    network = _field(net.get("status", UNKNOWN), "tailscale status --json + tailscale funnel status + GET :%s/api/bridge/health + GET <funnel>/api/bridge/health" % net.get("gateway_port"),
                     net_detail, reason=None if net.get("status") == ONLINE else ("TAILSCALE_DOWN" if net.get("tailscale") == OFFLINE else (net.get("error") or "PARTIAL")),
                     tailscale=net.get("tailscale"), funnel=net.get("funnel"), public_url=net.get("public_url"),
                     gateway_code=net.get("gateway_code"), public_code=pub.get("code"), public_checked_at=pub.get("checked_at"))

    # ---- 6) shared room, 7) voice
    room = room_check(cfg) if core_ok else {"status": UNKNOWN, "reason": "ORIGIN_UNREACHABLE", "checked_at": _iso(),
                                            "source": "POST /api/shared-room/message (Core down)", "detail": ""}
    shared = _field(room.get("status", UNKNOWN), room.get("source", "shared_room"), room.get("detail", ""),
                    reason=room.get("reason"), max_rounds=room.get("max_rounds"), checked_at=room.get("checked_at"),
                    session=room.get("session"))
    v = _voice_check(cfg) if core_ok else {"status": UNKNOWN, "reason": "ORIGIN_UNREACHABLE", "checked_at": _iso(), "source": "POST /api/voice/tts", "detail": ""}
    voice = _field(v.get("status", UNKNOWN), v.get("source", "POST /api/voice/tts"), v.get("detail", ""),
                   reason=v.get("reason"), checked_at=v.get("checked_at"), voice=v.get("voice"))

    cg = chatgpt_optional_status()
    chatgpt = _field(OPTIONAL, "env OPENAI_API_KEY present?", "optional · %s" % ("konfiguriert" if cg.get("configured") else "nicht konfiguriert"),
                     configured=cg.get("configured"), blocks_health=False)

    # ---- overall
    phone_link_ok = phone["status"] == ONLINE and termux["status"] == ONLINE and agent["status"] == ONLINE
    if not core_ok:
        mode, health, critical = "OFFLINE", CRITICAL, True
    elif glob["status"] == OFFLINE:
        mode, health, critical = "CORE_ONLY", CRITICAL, True
    else:
        critical = False
        # zipfeglatscha 2026-10-26: BUSY = erreichbar + Modell geladen -> zaehlt als lokale KI verfuegbar
        if wozi_ai["status"] in (ONLINE, BUSY) and phone_link_ok:
            mode = "FULL"
        elif wozi_ai["status"] in (ONLINE, BUSY):
            mode = "PC_FALLBACK"
        elif phone_ai["status"] == ONLINE:
            mode = "PHONE_FALLBACK"
        else:
            mode = "CORE_ONLY"
        health = HEALTHY
    required = ["CORE", "BRIDGE", "WOZIPC", "WOZIPC_LOCAL_AI", "NEXUSPHONE", "TERMUX", "PHONE_AGENT",
                "GLOBAL_LOCAL_AI", "SHARED_ROOM", "VOICE", "NETWORK"]
    if cfg.get("phone_ai_required_for_healthy"):
        required.append("PHONE_LOCAL_AI")
    comp_now = {"CORE": core, "BRIDGE": bridge, "WOZIPC": wozipc_dev, "WOZIPC_LOCAL_AI": wozi_ai, "NEXUSPHONE": phone,
                "PHONE_DEVICE": dict(phone), "PHONE_CONTROL": control,
                "TERMUX": termux, "PHONE_AGENT": agent, "PHONE_LOCAL_AI": phone_ai, "GLOBAL_LOCAL_AI": glob,
                "SHARED_ROOM": shared, "VOICE": voice, "NETWORK": network, "CHATGPT": chatgpt}
    not_ok = [k for k in required
              if comp_now[k]["status"] != ONLINE and not (k == "WOZIPC_LOCAL_AI" and comp_now[k]["status"] == BUSY)]
    if health == HEALTHY and not_ok:
        health = DEGRADED

    active = "NONE"
    active_src = "rule"
    if wozi_ai["status"] == ONLINE:
        active = PROVIDER_WOZIPC_LOCAL
        active_src = "rule: WoZiPC-Ollama ONLINE (einziger chat-faehiger lokaler Provider)"
    elif phone_ai["status"] == ONLINE:
        active = PROVIDER_PHONE_LOCAL
    if provider is not None:
        try:
            r = provider.route("status")
            if r.get("usable") and r.get("logical"):
                active = r.get("logical")
                active_src = "AIProviderManager.route()"
        except Exception:
            pass

    now_iso = _iso()
    components = dict(comp_now)
    components["OPERATING_MODE"] = _field(mode, "rule: CORE + WOZIPC_LOCAL_AI + Phone-Link", {
        "FULL": "PC + Handy verbunden", "PC_FALLBACK": "Handy nicht voll erreichbar - WoZiPC uebernimmt",
        "PHONE_FALLBACK": "nur Handy-KI", "CORE_ONLY": "keine lokale KI", "OFFLINE": "Core nicht arbeitsfaehig"}[mode], checked_at=now_iso)
    components["ACTIVE_AI_PROVIDER"] = _field(active, active_src, active if active != "NONE" else "kein lokaler Provider aktiv", value=active, checked_at=now_iso)
    components["SYSTEM_HEALTH"] = _field(health, "rule: alle Kernkomponenten ONLINE -> HEALTHY", 
                                         "nicht ONLINE: " + ", ".join(not_ok) if not_ok else "alle Kernkomponenten ONLINE",
                                         degraded_by=not_ok, checked_at=now_iso)
    components["CRITICAL_FAILURE"] = _field("TRUE" if critical else "FALSE", "rule: Core down oder keine lokale KI",
                                            "kritisch" if critical else "nicht kritisch", value=critical, checked_at=now_iso)

    # ---- LINKS (separate from component availability). Core OFFLINE -> every core link OFFLINE.
    links: dict[str, Any] = {}
    if not core_ok:
        for lk in LINK_ORDER:
            links[lk] = _field(OFFLINE, "rule: CORE nicht ONLINE", "Core nicht erreichbar", reason="CORE_OFFLINE", dependency="CORE", checked_at=now_iso)
    else:
        win = float(cfg.get("phone_link_window_sec") or 90)
        last = _last_phone_request(_phone_ips(cfg, peer, ident_chk))
        if last and time.time() - last["ts"] <= win:
            links["PHONE_CORE"] = _field(ONLINE, "eingehende HTTP-Anfrage vom Handy am Core (Live, Prozessspeicher)",
                                         "%s -> %s vor %ds" % (last["ip"], last["path"], int(time.time() - last["ts"])),
                                         checked_at=_iso(last["ts"]), client_ip=last["ip"], path=last["path"])
        elif phone["status"] in (DISCONNECTED,):
            links["PHONE_CORE"] = _field(OFFLINE, "rule: Handy getrennt", "Handy nicht erreichbar", reason=phone.get("reason"), dependency="PHONE_DEVICE", checked_at=now_iso)
        else:
            links["PHONE_CORE"] = _field(UNKNOWN, "eingehende HTTP-Anfragen vom Handy (Live, Prozessspeicher)",
                                         "keine Anfrage vom Handy in den letzten %ds" % int(win) + (" (zuletzt vor %ds)" % int(time.time() - last["ts"]) if last else ""),
                                         reason="NO_RECENT_PHONE_REQUEST", checked_at=now_iso)
        if wozi_ai["status"] == ONLINE:
            links["CORE_WOZIPC"] = _field(ONLINE, "Core-Prozess -> Ollama: POST /api/generate (num_predict=1)",
                                          "Core erreicht WoZiPC-KI (echte Inferenz)", checked_at=wozi_ai.get("inference_checked_at") or now_iso)
        elif wozi_ai["status"] == BUSY and wozi_ai.get("reason") == "MODEL_LOADED":
            # 2026-10-28 MH02: geladenes Modell laut GET /api/ps ist ein echter Beleg, kein UNKNOWN
            links["CORE_WOZIPC"] = _field(ONLINE, "Core-Prozess -> Ollama: GET /api/ps (Modell geladen)",
                                          "Core erreicht WoZiPC-KI (Modell im VRAM)", reason="MODEL_LOADED", checked_at=now_iso)
        else:
            links["CORE_WOZIPC"] = _field(OFFLINE if wozi_ai["status"] in (OFFLINE, FAILED) else UNKNOWN, wozi_ai["source"],
                                          "Core erreicht WoZiPC-KI nicht", reason=wozi_ai.get("reason"), dependency="WOZIPC_LOCAL_AI", checked_at=now_iso)
        if chatgpt.get("configured"):
            links["CORE_CHATGPT"] = _field(AVAILABLE, "env OPENAI_API_KEY vorhanden (kein bezahlter Testcall)",
                                           "optional · konfiguriert, nicht live getestet", reason="NO_PAID_TEST_CALL", checked_at=now_iso)
        else:
            links["CORE_CHATGPT"] = _field(OPTIONAL, "env OPENAI_API_KEY (nicht gesetzt)", "optional · aus (Kostenregel 0 €, keine bezahlte API)",
                                           reason="OFF_BY_COST_RULE", checked_at=now_iso)
    for k, v in links.items():
        v["state"] = STATE_VOCAB.get(v["status"], v["status"])
    for k, v in components.items():
        v["state"] = to_state(k, v)
    components["SYSTEM_HEALTH"]["state"] = health if health != HEALTHY else "HEALTHY"
    components["CRITICAL_FAILURE"]["state"] = components["CRITICAL_FAILURE"]["status"]
    components["OPERATING_MODE"]["state"] = mode
    components["ACTIVE_AI_PROVIDER"]["state"] = active

    orbital_src = {
        "PHASE_A": components["PHONE_AGENT"], "BRIDGE": bridge,
        "TAILSCALE": _field(net.get("tailscale", UNKNOWN), ts_src, net.get("tailscale_dns", "")),
        "TERMUX_AGENT": _field(agent["status"] if agent["status"] != ONLINE else ONLINE, "adb forward --list + /health",
                               str((pa.get("forward") or {}).get("code") or agent.get("reason") or "")),
        "OLLAMA": wozi_ai, "SHARED_ROOM": shared, "VOICE": voice,
        "ANDROID": _field(ONLINE if serial else (DISCONNECTED if adb.get("adb") else UNKNOWN), adb_src,
                          serial or (adb_err or "")),
    }
    ce = _STATE.get("chat_e2e")
    orbital_src["CHAT"] = _field(ce["status"], ce["source"], ce.get("reply_preview", ""), checked_at=ce["checked_at"]) if ce else \
        (_last_real_chat() or _field(UNKNOWN, "POST /api/chat (on demand: ?e2e=chat)", "kein E2E-Lauf", reason="NO_E2E_RUN"))
    orbital = {k: {"state": ORBITAL_MAP.get(v["status"], "UNKNOWN"), "status": v["status"], "source": v["source"],
                   "checked_at": v["checked_at"], "reason": v.get("reason")} for k, v in orbital_src.items()}

    payload = {
        "success": True,
        "schema": SCHEMA,
        "identity": ident,
        "SYSTEM_HEALTH": health,
        "CRITICAL_FAILURE": critical,
        "CORE_OPERATIONAL": core_ok,
        "NEXUS_OPERATIONAL": bool(core_ok and not critical),
        "OPERATING_MODE": mode,
        "ACTIVE_AI_PROVIDER": active,
        "GLOBAL_LOCAL_AI": glob["status"],
        "layers": {
            "devices": {"WOZIPC": wozipc_dev["status"], "NEXUSPHONE": phone["status"], "PHONE_DEVICE": phone["status"],
                        "PHONE_CONTROL": control["status"], "TERMUX": termux["status"], "PHONE_AGENT": agent["status"]},
            "ai": {"WOZIPC_LOCAL_AI": wozi_ai["status"], "PHONE_LOCAL_AI": phone_ai["status"],
                   "GLOBAL_LOCAL_AI": glob["status"], "ACTIVE_AI_PROVIDER": active},
            "overall": {"CORE": core["status"], "SYSTEM_HEALTH": health, "CRITICAL_FAILURE": critical,
                        "CORE_OPERATIONAL": core_ok, "OPERATING_MODE": mode},
        },
        "components": components,
        "LINKS": links,
        "link_order": LINK_ORDER,
        "vocabulary": VOCABULARY_DOC,
        "display_order": DISPLAY_ORDER,
        "orbital": orbital,
        "orbital_map": ORBITAL_MAP,
        "phone": {"adb": {k: adb.get(k) for k in ("adb", "devices", "error")}, "tailscale_peer": peer,
                  "tailscale_ping": ping, "identity": {k: v for k, v in ident_chk.items() if k != "ts"},
                  "phase_a": pa},
        "network": net,
        "default_provider": cfg.get("default_provider") or PROVIDER_WOZIPC_LOCAL,
        "fallback_order": cfg.get("fallback_order") or list(DEFAULT_CONFIG["fallback_order"]),
        # legacy fields
        "ok": bool(core_ok and not critical),   # true also when DEGRADED; see health/SYSTEM_HEALTH
        "health": health,
        "critical_unavailable": [k for k in ("CORE", "GLOBAL_LOCAL_AI") if critical and comp_now[k]["status"] != ONLINE],
        "health_rule": ("Core down -> SYSTEM_HEALTH CRITICAL, NEXUS_OPERATIONAL false. HEALTHY nur wenn CORE, BRIDGE, WOZIPC, WOZIPC_LOCAL_AI, NEXUSPHONE, TERMUX, PHONE_AGENT, GLOBAL_LOCAL_AI, "
                        "SHARED_ROOM, VOICE, NETWORK ONLINE; PHONE_LOCAL_AI zaehlt nur wenn phone_ai_required_for_healthy=true; "
                        "CHATGPT ist OPTIONAL und zaehlt nie; Core down -> OFFLINE/critical; keine lokale KI -> CRITICAL/critical"),
        "recheck": {"interval_sec": int(cfg.get("recheck_interval_sec") or 45), "order": RECHECK_ORDER,
                    "stale_after_sec": int(cfg.get("stale_after_sec") or 150)},
        "cycle_ms": int((time.time() - t0) * 1000),
        "timestamp": now_iso,
        "cached": False,
    }
    return payload


def _log_transitions(payload: dict[str, Any]) -> None:
    prev = _STATE.get("prev") or {}
    cur = {k: v.get("status") for k, v in (payload.get("components") or {}).items()}
    changes = [(k, prev.get(k), s) for k, s in cur.items() if prev and prev.get(k) != s]
    _STATE["prev"] = cur
    if not prev:
        changes = [("INIT", None, "%s/%s" % (payload.get("SYSTEM_HEALTH"), payload.get("OPERATING_MODE")))]
    if not changes:
        return
    try:
        STATUS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with STATUS_LOG.open("a", encoding="utf-8") as f:
            for k, a, b in changes:
                f.write("%s %s %s -> %s\n" % (payload.get("timestamp"), k, a, b))
    except Exception:
        pass


def run_cycle(provider: Any = None, *, wait: bool = True) -> dict[str, Any] | None:
    got = _CYCLE_LOCK.acquire(blocking=wait, timeout=90 if wait else -1)
    if not got:
        return None
    try:
        payload = _probe_cycle(provider)
        with _LOCK:
            _STATE["snapshot"] = payload
            _STATE["ts"] = time.time()
            _STATE["cycles"] = int(_STATE.get("cycles") or 0) + 1
            _STATE["last_cycle_ms"] = payload.get("cycle_ms")
            _STATE["last_error"] = None
        _log_transitions(payload)
        return payload
    except Exception as e:  # never kill the monitor
        with _LOCK:
            _STATE["last_error"] = "%s: %s" % (type(e).__name__, str(e)[:200])
        return None
    finally:
        _CYCLE_LOCK.release()


def _monitor_loop(provider_getter: Any = None) -> None:
    while True:
        interval = 45
        try:
            interval = max(20, int(load_config().get("recheck_interval_sec") or 45))
            prov = provider_getter() if callable(provider_getter) else None
            run_cycle(prov, wait=False)
        except Exception:
            pass
        time.sleep(interval)


def start_monitor(provider_getter: Any = None) -> bool:
    """Start the periodic re-check thread once per process (auto re-integration)."""
    with _LOCK:
        th = _STATE.get("monitor")
        if th is not None and th.is_alive():
            return False
        th = threading.Thread(target=_monitor_loop, args=(provider_getter,), name="nexus-os-monitor", daemon=True)
        _STATE["monitor"] = th
        _STATE["monitor_started_at"] = _iso()
        th.start()
        return True


def _apply_staleness(snap: dict[str, Any], age: float, stale_after: float) -> dict[str, Any]:
    out = deepcopy(snap)
    out["cached"] = True
    out["age_sec"] = round(age, 1)
    if age <= stale_after:
        return out
    out["stale"] = True
    for k, v in (out.get("components") or {}).items():
        if k in ("CHATGPT",):
            continue
        v["last_known"] = v.get("status")
        v["status"] = UNKNOWN
        v["state"] = UNKNOWN
        v["reason"] = "STALE_SNAPSHOT"
    for k in ("SYSTEM_HEALTH", "OPERATING_MODE", "ACTIVE_AI_PROVIDER", "GLOBAL_LOCAL_AI"):
        out[k] = UNKNOWN
    out["CRITICAL_FAILURE"] = None
    out["CORE_OPERATIONAL"] = None
    out["NEXUS_OPERATIONAL"] = None
    for v in (out.get("LINKS") or {}).values():
        v["last_known"] = v.get("status")
        v["status"] = v["state"] = UNKNOWN
        v["reason"] = "STALE_SNAPSHOT"
    out["ok"] = False
    out["health"] = UNKNOWN
    for v in (out.get("orbital") or {}).values():
        v["state"] = "UNKNOWN"
    return out


def collect(
    *,
    voice_payload: dict[str, Any] | None = None,
    room_payload: dict[str, Any] | None = None,
    force: bool = False,
    provider: Any = None,
) -> dict[str, Any]:
    """Return the status snapshot (monitor-fed). force=True runs a real cycle now."""
    cfg = load_config()
    with _LOCK:
        snap = _STATE.get("snapshot")
        ts = float(_STATE.get("ts") or 0.0)
    if force or snap is None:
        fresh = run_cycle(provider, wait=True)
        if fresh is not None:
            out = deepcopy(fresh)
            out["cached"] = False
            out["age_sec"] = 0.0
            snap = fresh
        elif snap is None:
            return {"success": False, "schema": SCHEMA, "identity": identity(), "SYSTEM_HEALTH": UNKNOWN,
                    "components": {}, "error": _STATE.get("last_error") or "status_cycle_failed",
                    "timestamp": _iso()}
        else:
            out = _apply_staleness(snap, time.time() - ts, float(cfg.get("stale_after_sec") or 150))
    else:
        out = _apply_staleness(snap, time.time() - ts, float(cfg.get("stale_after_sec") or 150))
    th = _STATE.get("monitor")
    out.setdefault("recheck", {})
    out["recheck"].update({"monitor_running": bool(th is not None and th.is_alive()),
                           "monitor_started_at": _STATE.get("monitor_started_at"),
                           "cycles": _STATE.get("cycles"), "last_cycle_ms": _STATE.get("last_cycle_ms"),
                           "last_error": _STATE.get("last_error")})
    return out


_PUBLIC_COMPONENT_KEYS = ("state", "status", "source", "checked_at", "detail", "reason", "dependency", "method", "value", "last_known", "configured")


def public_view(payload: dict[str, Any]) -> dict[str, Any]:
    """Read-only reduced status for clients like the NEXUS phone app (tailnet/LAN :8299 only;
    the Funnel gateway does not expose it). Same schema/labels as the desktop view, but without
    device inventory, peer/identity evidence, public URLs or any credentials."""
    comps = {}
    for k, v in (payload.get("components") or {}).items():
        if isinstance(v, dict):
            comps[k] = {kk: v.get(kk) for kk in _PUBLIC_COMPONENT_KEYS if v.get(kk) is not None}
    return {
        "success": True,
        "schema": payload.get("schema"),
        "view": "lite",
        "identity": payload.get("identity"),
        "ok": payload.get("ok"),
        "health": payload.get("health"),
        "SYSTEM_HEALTH": payload.get("SYSTEM_HEALTH"),
        "CRITICAL_FAILURE": payload.get("CRITICAL_FAILURE"),
        "CORE_OPERATIONAL": payload.get("CORE_OPERATIONAL"),
        "OPERATING_MODE": payload.get("OPERATING_MODE"),
        "ACTIVE_AI_PROVIDER": payload.get("ACTIVE_AI_PROVIDER"),
        "GLOBAL_LOCAL_AI": payload.get("GLOBAL_LOCAL_AI"),
        "NEXUS_OPERATIONAL": payload.get("NEXUS_OPERATIONAL"),
        "components": comps,
        "LINKS": {k: {kk: v.get(kk) for kk in _PUBLIC_COMPONENT_KEYS if v.get(kk) is not None}
                  for k, v in (payload.get("LINKS") or {}).items() if isinstance(v, dict)},
        "link_order": payload.get("link_order") or LINK_ORDER,
        "vocabulary": payload.get("vocabulary") or VOCABULARY_DOC,
        "display_order": payload.get("display_order") or DISPLAY_ORDER,
        "orbital": {k: {"state": v.get("state"), "status": v.get("status"), "reason": v.get("reason")}
                    for k, v in (payload.get("orbital") or {}).items() if isinstance(v, dict)},
        "timestamp": payload.get("timestamp"),
        "age_sec": payload.get("age_sec"),
        "stale": bool(payload.get("stale")),
        "cached": payload.get("cached"),
    }


def _provider_status(provider: Any = None) -> dict[str, Any]:
    try:
        if provider is not None:
            st = provider.status()
        else:
            from nexus.providers.manager import AIProviderManager

            st = AIProviderManager(default="local").status()
        logical = st.get("providers") or {}
    except Exception as e:
        return {"error": type(e).__name__}
    local = logical.get("local") or {}
    openai = logical.get("openai") or {}
    phone = logical.get("phone_local") or {}
    return {
        "default": st.get("default"),
        "fallback": st.get("fallback", "local"),
        PROVIDER_PHONE_LOCAL: {"status": phone.get("status") or UNKNOWN, "reachable": phone.get("reachable"),
                               "chat_capable": phone.get("chat_capable")},
        PROVIDER_WOZIPC_LOCAL: {"status": (ONLINE if local.get("ok", True) else FAILED) if local.get("reachable") else OFFLINE, "reachable": local.get("reachable"),
                                "model": local.get("model")},
        PROVIDER_CHATGPT_OPTIONAL: {"status": OPTIONAL, "configured": bool(openai.get("configured")),
                                    "model": openai.get("model"), "blocks_health": False},
        "max_rounds": st.get("max_rounds"),
    }


def invalidate_cache() -> None:
    with _LOCK:
        _STATE["ts"] = 0.0
        _STATE["snapshot"] = None
