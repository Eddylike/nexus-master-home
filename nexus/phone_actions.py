"""NEXUS Handy-Aktionen (heynexus-20261006).

Sicherheitsregel (Eddie / Stabschef):
- KEINE generische Remote-Shell. Nur feste, erlaubte Aktionen mit validierten Parametern.
- adb wird immer mit Argument-Liste (shell=False) und festem Serial aufgerufen.
- Primaer drahtlos (Tailscale, NEXUS_PHONE_TAILSCALE_IP) 192.0.2.10:5555, Fallback USB 249ac5d7 (konfigurierbar).
- Erlaubt: `adb connect <tcp-endpoint>` (idempotent). VERBOTEN: tcpip, kill-server,
  forward --remove(-all), pair, disconnect. Der Forward tcp:18766 -> 8766 bleibt unangetastet.
- Keine Nachrichtentexte, Nummern oder Tokens im Log (nur Laenge + Empfaenger-Maske).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import threading
import time
import unicodedata
from datetime import datetime
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]  # nexus/phone_actions.py -> nexus-ai/
CONFIG_PATH = ROOT / "workspace" / "voice" / "phone_actions.json"
CONTACTS_PATH = ROOT / "workspace" / "apex" / "contacts.json"
LOG_PATH = ROOT / "logs" / "voice_commands.jsonl"

# Tailscale-IP des Handys; per Env ueberschreibbar, falls Tailscale neu vergibt.
PHONE_TAILSCALE_IP = os.environ.get("NEXUS_PHONE_TAILSCALE_IP", "192.0.2.10")

DEFAULT_CONFIG: dict[str, Any] = {
    "adb_path": r"C:\Users\gustl\AppData\Local\Android\Sdk\platform-tools\adb.exe",
    # Reihenfolge = Prioritaet. Erster Eintrag mit ':' ist der TCP-Endpunkt fuer `adb connect`.
    "serials": [f"{PHONE_TAILSCALE_IP}:5555", "249ac5d7"],
    "tcp_endpoint": f"{PHONE_TAILSCALE_IP}:5555",
    "default_country_code": "49",
    "read_phone_contacts": True,
    "send_button_wait_s": 10,
}

# Name (gesprochen, normalisiert) -> Liste moeglicher Pakete (erstes installiertes gewinnt)
PHONE_APPS: dict[str, dict[str, Any]] = {
    "whatsapp": {"label": "WhatsApp", "packages": ["com.whatsapp", "com.whatsapp.w4b"],
                  "aliases": ["whats app", "whatsapp business", "watsapp", "whatsup", "whats up"]},
    "spotify": {"label": "Spotify", "packages": ["com.spotify.music"], "aliases": []},
    "youtube": {"label": "YouTube", "packages": ["com.google.android.youtube"], "aliases": ["you tube"]},
    "youtube music": {"label": "YouTube Music", "packages": ["com.google.android.apps.youtube.music"],
                       "aliases": ["youtube musik"]},
    "kamera": {"label": "Kamera", "packages": ["com.android.camera"], "aliases": ["camera", "die kamera", "foto"]},
    "galerie": {"label": "Galerie", "packages": ["com.miui.gallery", "com.google.android.apps.photos"],
                 "aliases": ["fotos", "bilder", "gallery"]},
    "chrome": {"label": "Chrome", "packages": ["com.android.chrome"], "aliases": ["browser", "google chrome"]},
    "telegram": {"label": "Telegram", "packages": ["org.telegram.messenger"], "aliases": []},
    "instagram": {"label": "Instagram", "packages": ["com.instagram.android"], "aliases": ["insta"]},
    "tiktok": {"label": "TikTok", "packages": ["com.zhiliaoapp.musically"], "aliases": ["tik tok"]},
    "maps": {"label": "Google Maps", "packages": ["com.google.android.apps.maps"],
              "aliases": ["google maps", "karten", "navigation"]},
    "einstellungen": {"label": "Einstellungen", "packages": ["com.android.settings"], "aliases": ["settings"]},
    "uhr": {"label": "Uhr", "packages": ["com.android.deskclock", "com.google.android.deskclock"],
             "aliases": ["wecker", "die uhr", "clock", "timer"]},
    "gmail": {"label": "Gmail", "packages": ["com.google.android.gm"], "aliases": ["mail", "e-mail", "email"]},
    "discord": {"label": "Discord", "packages": ["com.discord"], "aliases": []},
    "messenger": {"label": "Messenger", "packages": ["com.facebook.orca"], "aliases": ["facebook messenger"]},
    "facebook": {"label": "Facebook", "packages": ["com.facebook.katana"], "aliases": []},
    "telefon": {"label": "Telefon", "packages": ["com.google.android.dialer", "com.android.dialer"],
                 "aliases": ["anrufe", "dialer"]},
    "kontakte": {"label": "Kontakte", "packages": ["com.google.android.contacts", "com.android.contacts"],
                  "aliases": ["telefonbuch"]},
    "nachrichten": {"label": "Nachrichten", "packages": ["com.google.android.apps.messaging"],
                     "aliases": ["sms", "messages"]},
    "rechner": {"label": "Rechner", "packages": ["com.miui.calculator", "com.google.android.calculator"],
                 "aliases": ["taschenrechner", "calculator"]},
    "nexus": {"label": "NEXUS OS", "packages": ["ai.nexus.os.mobile"], "aliases": ["nexus os", "nexus app"]},
    "google home": {"label": "Google Home", "packages": ["com.google.android.apps.chromecast.app"],
                     "aliases": ["home"]},
}

WHATSAPP_PACKAGES = ("com.whatsapp", "com.whatsapp.w4b")

_RE_SERIAL = re.compile(r"^[A-Za-z0-9.:_-]{4,64}$")
_RE_PKG = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_RE_URL_SAFE = re.compile(r"^[A-Za-z0-9:/?=&%._~-]+$")
_RE_DIGITS = re.compile(r"^[0-9]{7,15}$")

_lock = threading.RLock()
_pkg_cache: dict[str, tuple[float, set[str]]] = {}
_contacts_cache: dict[str, Any] = {"ts": 0.0, "items": []}


# ---------------------------------------------------------------- config/log
def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    try:
        if CONFIG_PATH.is_file():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    except Exception:
        pass
    env = (os.environ.get("NEXUS_ADB_SERIALS") or "").strip()
    if env:
        cfg["serials"] = [s.strip() for s in env.split(",") if s.strip()]
    cfg["serials"] = [s for s in cfg.get("serials") or [] if isinstance(s, str) and _RE_SERIAL.match(s)]
    if not (isinstance(cfg.get("tcp_endpoint"), str) and _RE_SERIAL.match(cfg["tcp_endpoint"]) and ":" in cfg["tcp_endpoint"]):
        cfg["tcp_endpoint"] = ""
    return cfg


def ensure_config_file() -> None:
    try:
        if not CONFIG_PATH.exists():
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def mask_recipient(name: str | None, number: str | None = None) -> str:
    n = (name or "").strip()
    m = (n[:2] + "***") if n else "***"
    if number:
        m += f"(..{str(number)[-2:]})"
    return m


def log_action(entry: dict[str, Any]) -> None:
    """JSONL-Protokoll. Niemals Nachrichtentext oder volle Nummer hineinschreiben."""
    safe = {k: v for k, v in entry.items() if k not in ("text", "message", "number", "phone", "token")}
    safe["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(safe, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- adb core
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _adb_exe(cfg: dict[str, Any]) -> str:
    p = str(cfg.get("adb_path") or "")
    return p if p and Path(p).is_file() else "adb"


def _run(cmd: list[str], timeout: float) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, shell=False, check=False,
                              creationflags=_NOWIN)
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        return {"ok": proc.returncode == 0, "rc": proc.returncode, "out": out, "err": err}
    except FileNotFoundError:
        return {"ok": False, "rc": -1, "out": "", "err": "adb_not_found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": -2, "out": "", "err": "timeout"}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "rc": -3, "out": "", "err": type(e).__name__}


def _adb(serial: str, args: list[str], timeout: float = 15) -> dict[str, Any]:
    """Interner Aufruf: nur mit festen Argumentlisten aus diesem Modul benutzen."""
    cfg = load_config()
    if not _RE_SERIAL.match(serial or ""):
        return {"ok": False, "rc": -4, "out": "", "err": "bad_serial"}
    if args and args[0] in ("tcpip", "kill-server", "disconnect", "pair", "usb", "root", "reboot"):
        return {"ok": False, "rc": -5, "out": "", "err": "forbidden"}
    if args[:1] == ["forward"]:
        return {"ok": False, "rc": -5, "out": "", "err": "forbidden"}
    return _run([_adb_exe(cfg), "-s", serial] + args, timeout)


def adb_devices() -> dict[str, str]:
    cfg = load_config()
    r = _run([_adb_exe(cfg), "devices"], 8)
    res: dict[str, str] = {}
    for line in (r.get("out") or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            res[parts[0]] = parts[1]
    return res


def select_device(connect: bool = True) -> dict[str, Any]:
    """Waehlt das Geraet: primaer TCP, Fallback USB. Ruft `adb connect` idempotent auf."""
    cfg = load_config()
    if cfg.get("adb_path") and not Path(str(cfg["adb_path"])).is_file():
        # adb im PATH versuchen
        pass
    connect_info = None
    tcp = cfg.get("tcp_endpoint") or ""
    if connect and tcp:
        r = _run([_adb_exe(cfg), "connect", tcp], 6)
        connect_info = (r.get("out") or r.get("err") or "").strip()[:120]
    devs = adb_devices()
    for s in cfg["serials"]:
        if devs.get(s) == "device":
            _WD["last_seen"] = time.time()
            return {"status": "OK", "serial": s, "transport": "tcp" if ":" in s else "usb",
                    "connect": connect_info}
    # Wireless-Debugging (mDNS/TLS, z. B. ueber Tailscale): Seriennummer wechselt, daher an der
    # gespeicherten Identitaet (ro.serialno, sonst device+model) erkennen; 'sapphiren' nur Fallback.
    for s, st in devs.items():
        if st == "device" and s not in cfg["serials"]:
            ident = read_identity(s)
            if identity_matches(ident):
                remember_identity(ident, s)
                return {"status": "OK", "serial": s, "transport": "wifi", "connect": connect_info,
                        "identity_by": ident.get("_matched_by")}
    reason = "ADB_TCP_NOT_ENABLED"
    states = {s: devs.get(s, "missing") for s in cfg["serials"]}
    if any(v == "unauthorized" for v in states.values()):
        reason = "ADB_UNAUTHORIZED"
    return {"status": "UNAVAILABLE", "reason": reason, "states": states, "connect": connect_info,
            "reply": "Das Handy ist gerade nicht erreichbar. Bitte schliess es einmal per USB an, "
                     "damit ich es wieder drahtlos steuern kann."}


# ---------------------------------------------------------------- identity (005D)
IDENTITY_PATH = ROOT / "workspace" / "phone_data" / "phone_identity.json"
RECONNECT_LOG = ROOT / "workspace" / "phone_data" / "adb_reconnect.log"
FALLBACK_DEVICE = "sapphiren"
TAILSCALE_IP = PHONE_TAILSCALE_IP
_RE_PROP = re.compile(r"^[A-Za-z0-9._ -]{1,64}$")
_RE_HOSTPORT = re.compile(r"^(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})$")
_WD: dict[str, Any] = {"last_seen": None, "reconnect_attempts": 0, "last_error": None, "last_attempt": None,
                       "last_endpoint": None, "running": False, "blocked": None}
_id_cache: dict[str, Any] = {}


def load_identity() -> dict[str, Any]:
    try:
        d = json.loads(IDENTITY_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_identity(serial: str) -> dict[str, Any]:
    """Liest ro.serialno / ro.product.device / ro.product.model (read-only getprop)."""
    hit = _id_cache.get(serial)
    if hit and time.time() - hit[0] < 600:
        return dict(hit[1])
    out: dict[str, Any] = {}
    for key, prop in (("serialno", "ro.serialno"), ("device", "ro.product.device"), ("model", "ro.product.model")):
        r = _adb(serial, ["shell", "getprop", prop], 6)
        v = (r.get("out") or "").strip()
        if r.get("ok") and v and _RE_PROP.match(v):
            out[key] = v
    if out:
        _id_cache[serial] = (time.time(), dict(out))
    return out


def identity_matches(ident: dict[str, Any], known: dict[str, Any] | None = None) -> bool:
    known = load_identity() if known is None else known
    if not ident:
        return False
    if known.get("serialno") and ident.get("serialno"):
        ok = ident["serialno"] == known["serialno"]
        ident["_matched_by"] = "serialno" if ok else None
        return ok
    if known.get("device") and known.get("model"):
        if ident.get("device") == known["device"] and ident.get("model") == known["model"]:
            ident["_matched_by"] = "device+model"
            return True
        return False
    if ident.get("device") == FALLBACK_DEVICE:
        ident["_matched_by"] = "fallback_device"
        return True
    return False


def remember_identity(ident: dict[str, Any], serial: str) -> None:
    now = time.time()
    _WD["last_seen"] = now
    _WD["blocked"] = None
    known = load_identity()
    new = {k: ident[k] for k in ("serialno", "device", "model") if ident.get(k)}
    changed = any(known.get(k) != v for k, v in new.items()) or known.get("last_serial") != serial
    if not changed and now - float(known.get("last_seen_ts") or 0) < 300:
        return
    known.update(new)
    known["last_serial"] = serial
    known["last_seen_ts"] = now
    known["last_seen"] = datetime.fromtimestamp(now).astimezone().isoformat(timespec="seconds")
    known.setdefault("first_seen", known["last_seen"])
    try:
        _write_json_atomic(IDENTITY_PATH, known)
    except Exception:
        pass


# ---------------------------------------------------------------- reconnect watchdog (005D)
def _rlog(msg: str) -> None:
    try:
        RECONNECT_LOG.parent.mkdir(parents=True, exist_ok=True)
        if RECONNECT_LOG.is_file() and RECONNECT_LOG.stat().st_size > 256 * 1024:
            old = RECONNECT_LOG.with_suffix(".log.1")
            os.replace(RECONNECT_LOG, old)
        with RECONNECT_LOG.open("a", encoding="utf-8") as f:
            f.write(datetime.now().astimezone().isoformat(timespec="seconds") + " " + msg[:400] + "\n")
    except Exception:
        pass


def parse_mdns(out: str) -> list[dict[str, Any]]:
    res = []
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1].startswith("_adb-tls-connect"):
            m = _RE_HOSTPORT.match(parts[2])
            if m:
                res.append({"name": parts[0], "host": m.group(1), "port": int(m.group(2))})
    return res


def reconnect_candidates(mdns: list[dict[str, Any]], known: dict[str, Any]) -> list[str]:
    sn = known.get("serialno") or ""
    mine = [m for m in mdns if sn and m["name"].startswith("adb-" + sn + "-")]
    if not mine and not sn:
        mine = mdns[:1] if len(mdns) == 1 else []
    eps: list[str] = []
    for m in mine:
        eps.append(f'{m["host"]}:{m["port"]}')
        eps.append(f'{TAILSCALE_IP}:{m["port"]}')
    if not mine and known.get("last_port"):
        eps.append(f'{TAILSCALE_IP}:{int(known["last_port"])}')
    out = []
    for e in eps:
        if e not in out and _RE_SERIAL.match(e):
            out.append(e)
    return out


def reconnect_once() -> dict[str, Any]:
    """Ein Reconnect-Versuch. Nur `adb mdns services` + `adb connect <ip:port>` (kein tcpip, kein pair)."""
    cfg = load_config()
    adb = _adb_exe(cfg)
    known = load_identity()
    _WD["last_attempt"] = time.time()
    _WD["reconnect_attempts"] += 1
    md = _run([adb, "mdns", "services"], 8)
    cands = reconnect_candidates(parse_mdns(md.get("out") or ""), known)
    if not cands:
        _WD["last_error"] = "NO_MDNS_SERVICE_FOR_DEVICE"
        _WD["blocked"] = ("Handy nicht per mDNS sichtbar (WLAN-Debugging aus oder anderes Netz). "
                          "Nach Neustart/Umschalten am Handy WLAN-Debugging wieder einschalten; "
                          "neu koppeln nur, falls 'adb connect' 'failed to authenticate' meldet.")
        _rlog("attempt=%d no_candidate mdns_rc=%s" % (_WD["reconnect_attempts"], md.get("rc")))
        return {"success": False, "error": _WD["last_error"]}
    for ep in cands:
        r = _run([adb, "connect", ep], 8)
        txt = ((r.get("out") or "") + (r.get("err") or "")).strip().replace("\n", " ")[:160]
        _rlog("attempt=%d connect %s -> %s" % (_WD["reconnect_attempts"], ep, txt))
        low = txt.lower()
        if "connected to" in low and "cannot" not in low and "failed" not in low:
            _WD["last_endpoint"] = ep
            _WD["last_error"] = None
            _WD["blocked"] = None
            try:
                known["last_port"] = int(ep.rsplit(":", 1)[1])
                known["last_endpoint"] = ep
                _write_json_atomic(IDENTITY_PATH, known)
            except Exception:
                pass
            return {"success": True, "endpoint": ep}
        if "authenticat" in low or "unauthorized" in low:
            _WD["last_error"] = "NEEDS_PAIRING"
            _WD["blocked"] = "PC ist am Handy nicht (mehr) gekoppelt: WLAN-Debugging > Geraet koppeln noetig."
        else:
            _WD["last_error"] = "CONNECT_FAILED: " + txt[:80]
    return {"success": False, "error": _WD["last_error"]}


def transport_path(serial: str, mdns_out: str | None = None) -> dict[str, Any]:
    """005D: lan|tailscale|usb|unknown fuer eine ADB-Seriennummer (mDNS-Name oder ip:port)."""
    host = None
    m = _RE_HOSTPORT.match(serial or "")
    if m:
        host = m.group(1)
    elif (serial or "").startswith("adb-") and "._adb-tls" in serial:
        if mdns_out is None:
            mdns_out = _run([_adb_exe(load_config()), "mdns", "services"], 8).get("out") or ""
        name = serial.split("._adb-tls")[0]
        for e in parse_mdns(mdns_out):
            if e["name"] == name:
                host = e["host"]
                break
    elif serial and ":" not in serial and "._" not in serial:
        return {"transport_path": "usb", "transport_host": None}
    if not host:
        return {"transport_path": "unknown", "transport_host": None}
    a, b = (int(x) for x in host.split(".")[:2])
    tsnet = a == 100 and 64 <= b <= 127
    return {"transport_path": "tailscale" if tsnet else "lan", "transport_host": host}


_HB = {"ts": 0.0}


def watchdog_tick() -> dict[str, Any]:
    dev = select_device(connect=False)
    if dev.get("status") == "OK":
        _WD["last_seen"] = time.time()
        if time.time() - _HB["ts"] >= 29:
            _HB["ts"] = time.time()
            try:
                tp = transport_path(dev.get("serial") or "").get("transport_path")
            except Exception:
                tp = "unknown"
            _rlog("heartbeat ok serial=%s path=%s by=%s" % (dev.get("serial"), tp, dev.get("identity_by") or "cfg"))
        return {"action": "none", "status": "OK"}
    return {"action": "reconnect", **reconnect_once()}


def watchdog_state() -> dict[str, Any]:
    def iso(ts):
        return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds") if ts else None
    return {"last_seen": iso(_WD["last_seen"]), "reconnect_attempts": _WD["reconnect_attempts"],
            "last_error": _WD["last_error"], "last_attempt": iso(_WD["last_attempt"]),
            "last_endpoint": _WD["last_endpoint"], "watchdog_running": _WD["running"],
            "blocked": _WD["blocked"]}


def start_watchdog(interval: float = 30.0) -> bool:
    with _lock:
        if _WD["running"]:
            return False
        _WD["running"] = True

    def _loop():
        _rlog("watchdog start interval=%ss" % int(interval))
        while True:
            try:
                watchdog_tick()
            except Exception as e:  # pragma: no cover
                _WD["last_error"] = "WATCHDOG_EXC: " + type(e).__name__
            time.sleep(interval)

    threading.Thread(target=_loop, name="nexus-adb-watchdog", daemon=True).start()
    return True


def screen_state(serial: str) -> dict[str, Any]:
    r = _adb(serial, ["shell", "dumpsys", "window"], 12)
    out = r.get("out") or ""
    kg = re.search(r"isKeyguardShowing=(true|false)", out)
    scr_on = re.search(r"mScreenOn\s*=\s*(true|false)", out) or re.search(r"mScreenOnFully=(true|false)", out)
    awake = re.search(r"mAwake=(true|false)", out)
    keyguard = (kg.group(1) == "true") if kg else None
    screen_on = (scr_on.group(1) == "true") if scr_on else ((awake.group(1) == "true") if awake else None)
    return {"ok": r.get("ok", False), "keyguard": keyguard, "screen_on": screen_on,
            "unlocked": bool(keyguard is False and screen_on is True)}


def installed_packages(serial: str, max_age: float = 300) -> set[str]:
    with _lock:
        hit = _pkg_cache.get(serial)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
    r = _adb(serial, ["shell", "pm", "list", "packages"], 20)
    pk = {ln.split(":", 1)[1].strip() for ln in (r.get("out") or "").splitlines() if ln.startswith("package:")}
    if pk:
        with _lock:
            _pkg_cache[serial] = (time.time(), pk)
    return pk


# ---------------------------------------------------------------- text helpers
def _norm(s: str) -> str:
    s = (s or "").lower().strip()
    s = s.replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9 +\-]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def find_phone_app(name: str) -> tuple[str, dict[str, Any]] | None:
    n = _norm(name)
    n = re.sub(r"^(die|den|das|meine?n?|app)\s+", "", n)
    n = re.sub(r"\s+app$", "", n).strip()
    if not n:
        return None
    for key, spec in PHONE_APPS.items():
        if n == key or n in [_norm(a) for a in spec.get("aliases", [])] or n == _norm(spec["label"]):
            return key, spec
    names = {}
    for key, spec in PHONE_APPS.items():
        for a in [key, _norm(spec["label"])] + [_norm(x) for x in spec.get("aliases", [])]:
            names[a] = key
    m = difflib.get_close_matches(n, list(names.keys()), n=1, cutoff=0.78)
    if m:
        k = names[m[0]]
        return k, PHONE_APPS[k]
    return None


def phone_app_names() -> list[str]:
    return [v["label"] for v in PHONE_APPS.values()]


# ---------------------------------------------------------------- actions
def status() -> dict[str, Any]:
    dev = select_device(connect=False)
    out = {"success": True, "device": dev, "config": {k: v for k, v in load_config().items() if k != "adb_path"},
           "contacts_file": str(CONTACTS_PATH), "contacts_file_exists": CONTACTS_PATH.is_file()}
    return out


def open_phone_app(name: str, source: str = "voice") -> dict[str, Any]:
    found = find_phone_app(name)
    if not found:
        return {"success": False, "error": "unknown_app",
                "reply": f"Die App {name} kenne ich auf dem Handy nicht."}
    key, spec = found
    dev = select_device()
    if dev["status"] != "OK":
        log_action({"action": "open_phone_app", "app": key, "result": dev["status"], "reason": dev.get("reason"), "source": source})
        return {"success": False, "status": "UNAVAILABLE", "error": dev.get("reason"), "reply": dev["reply"], "device": dev}
    serial = dev["serial"]
    pkgs = installed_packages(serial)
    pkg = next((p for p in spec["packages"] if p in pkgs), None)
    if not pkg:
        log_action({"action": "open_phone_app", "app": key, "result": "not_installed", "source": source})
        return {"success": False, "error": "not_installed",
                "reply": f"{spec['label']} ist auf dem Handy nicht installiert."}
    if not _RE_PKG.match(pkg):
        return {"success": False, "error": "bad_package", "reply": "Ungueltiges Paket."}
    t0 = time.time()
    r = _adb(serial, ["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"], 15)
    ok = r.get("ok") and "Events injected: 1" in (r.get("out") or "")
    scr = screen_state(serial) if ok else {}
    log_action({"action": "open_phone_app", "app": key, "package": pkg, "transport": dev["transport"],
                "result": "ok" if ok else "fail", "ms": int((time.time() - t0) * 1000), "source": source})
    if not ok:
        return {"success": False, "error": "launch_failed", "reply": f"Ich konnte {spec['label']} auf dem Handy nicht oeffnen."}
    reply = f"{spec['label']} ist auf dem Handy offen."
    if scr.get("keyguard") or scr.get("screen_on") is False:
        reply += " Das Handy ist aber gesperrt, bitte entsperre es."
    return {"success": True, "package": pkg, "transport": dev["transport"], "screen": scr, "reply": reply}


# -------------------------------------------------------------- contacts
def normalize_number(raw: str, cc: str | None = None) -> str | None:
    cc = cc or str(load_config().get("default_country_code") or "49")
    s = str(raw or "").strip()
    plus = s.startswith("+")
    d = re.sub(r"\D", "", s)
    if not d:
        return None
    if plus:
        pass
    elif d.startswith("00"):
        d = d[2:]
    elif d.startswith("0"):
        d = cc + d[1:]
    return d if _RE_DIGITS.match(d) else None


def _load_local_contacts() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    try:
        data = json.loads(CONTACTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return items
    for c in (data.get("contacts") if isinstance(data, dict) else data) or []:
        if not isinstance(c, dict) or c.get("example"):
            continue
        num = normalize_number(c.get("whatsapp") or c.get("phone") or "")
        name = str(c.get("name") or "").strip()
        if not (num and name):
            continue
        items.append({"name": name, "aliases": [str(a) for a in c.get("aliases") or []], "number": num, "src": "local"})
    return items


def _load_phone_contacts(serial: str) -> list[dict[str, Any]]:
    with _lock:
        if time.time() - _contacts_cache["ts"] < 300 and _contacts_cache["items"]:
            return list(_contacts_cache["items"])
    r = _adb(serial, ["shell", "content", "query", "--uri", "content://com.android.contacts/data/phones",
                      "--projection", "display_name:data1"], 20)
    items = []
    for ln in (r.get("out") or "").splitlines():
        m = re.match(r"^Row:\s*\d+\s+display_name=(.*?),\s*data1=(.*)$", ln.strip())
        if not m:
            continue
        num = normalize_number(m.group(2))
        name = m.group(1).strip()
        if num and name and name != "NULL":
            items.append({"name": name, "aliases": [], "number": num, "src": "phone"})
    with _lock:
        _contacts_cache.update({"ts": time.time(), "items": items})
    return items


def resolve_contact(spoken: str, serial: str | None = None) -> dict[str, Any]:
    """Unscharfe Namenssuche. Ergebnis: {matches:[{name, number, score, src}], unique: bool}."""
    q = _norm(spoken)
    q = re.sub(r"^(an|zu|der|die|dem|den|meiner?|meinem|meinen)\s+", "", q).strip()
    pool = _load_local_contacts()
    if serial and load_config().get("read_phone_contacts"):
        try:
            pool += _load_phone_contacts(serial)
        except Exception:
            pass
    scored: dict[str, dict[str, Any]] = {}
    for c in pool:
        best = 0.0
        for cand in [c["name"]] + c["aliases"]:
            cn = _norm(cand)
            if not cn:
                continue
            if cn == q:
                s = 1.0
            else:
                s = difflib.SequenceMatcher(None, q, cn).ratio()
                toks = cn.split()
                if q in toks:
                    s = max(s, 0.9)
                elif toks and difflib.get_close_matches(q, toks, n=1, cutoff=0.85):
                    s = max(s, 0.82)
            best = max(best, s)
        if c["src"] == "local":
            best += 0.02  # lokale Datei bevorzugen
        if best >= 0.72:
            prev = scored.get(c["number"])
            if not prev or prev["score"] < best:
                scored[c["number"]] = {"name": c["name"], "number": c["number"], "score": round(best, 3), "src": c["src"]}
    matches = sorted(scored.values(), key=lambda x: -x["score"])
    unique = bool(matches) and (len(matches) == 1 or matches[0]["score"] - matches[1]["score"] >= 0.1)
    return {"matches": matches[:5], "unique": unique}


# -------------------------------------------------------------- whatsapp
def build_whatsapp_url(number: str, text: str) -> str:
    if not _RE_DIGITS.match(number or ""):
        raise ValueError("bad_number")
    url = "https://api.whatsapp.com/send?phone=" + number + "&text=" + quote(text or "", safe="")
    if not _RE_URL_SAFE.match(url):
        raise ValueError("bad_url")
    return url


def _ui_dump(serial: str) -> str:
    r = _adb(serial, ["exec-out", "uiautomator", "dump", "/dev/tty"], 20)
    out = r.get("out") or ""
    i = out.find("<?xml")
    j = out.rfind("</hierarchy>")
    return out[i:j + len("</hierarchy>")] if i >= 0 and j > i else ""


def _find_send_button(xml_text: str) -> dict[str, Any] | None:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    for node in root.iter("node"):
        rid = node.get("resource-id") or ""
        pkg = node.get("package") or ""
        if pkg in WHATSAPP_PACKAGES and rid in (pkg + ":id/send", "com.whatsapp:id/send"):
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.get("bounds") or "")
            if not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            if x2 <= x1 or y2 <= y1:
                continue
            return {"x": (x1 + x2) // 2, "y": (y1 + y2) // 2, "package": pkg, "resource_id": rid,
                    "enabled": node.get("enabled") == "true"}
    return None


def _entry_text_len(xml_text: str) -> int | None:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    for node in root.iter("node"):
        rid = node.get("resource-id") or ""
        if rid.endswith(":id/entry") and (node.get("package") or "") in WHATSAPP_PACKAGES:
            return len(node.get("text") or "")
    return None


def whatsapp_open_chat(number: str, text: str, source: str = "voice", recipient: str = "") -> dict[str, Any]:
    """Oeffnet den Chat mit vorausgefuelltem Text und sucht den Senden-Knopf. Tippt NICHT."""
    if not _RE_DIGITS.match(number or ""):
        return {"success": False, "error": "bad_number", "reply": "Die Nummer ist ungueltig."}
    if not (text or "").strip() or len(text) > 2000:
        return {"success": False, "error": "bad_text", "reply": "Der Nachrichtentext fehlt oder ist zu lang."}
    dev = select_device()
    if dev["status"] != "OK":
        log_action({"action": "whatsapp_open", "to": mask_recipient(recipient, number), "text_len": len(text),
                    "result": dev["status"], "reason": dev.get("reason"), "source": source})
        return {"success": False, "status": "UNAVAILABLE", "error": dev.get("reason"), "reply": dev["reply"]}
    serial = dev["serial"]
    scr = screen_state(serial)
    if not scr.get("unlocked"):
        log_action({"action": "whatsapp_open", "to": mask_recipient(recipient, number), "text_len": len(text),
                    "result": "locked", "source": source})
        return {"success": False, "error": "phone_locked", "screen": scr,
                "reply": "Dein Handy ist gesperrt oder der Bildschirm ist aus. Bitte entsperre es, dann sag es nochmal."}
    pkgs = installed_packages(serial)
    pkg = next((p for p in WHATSAPP_PACKAGES if p in pkgs), None)
    if not pkg:
        return {"success": False, "error": "whatsapp_missing", "reply": "WhatsApp ist auf dem Handy nicht installiert."}
    try:
        url = build_whatsapp_url(number, text)
    except ValueError as e:
        return {"success": False, "error": str(e), "reply": "Die Nachricht konnte nicht vorbereitet werden."}
    # `adb shell` fuegt Argumente zu EINER Remote-Shell-Zeile zusammen -> URL in einfache
    # Anfuehrungszeichen. Sicher, weil die URL per Regex nur [A-Za-z0-9:/?=&%._~-] enthaelt.
    t0 = time.time()
    r = _adb(serial, ["shell", "am", "start", "-W", "-a", "android.intent.action.VIEW", "-p", pkg,
                      "-d", "'" + url + "'"], 20)
    if not r.get("ok") or "Error" in (r.get("out") or ""):
        log_action({"action": "whatsapp_open", "to": mask_recipient(recipient, number), "text_len": len(text),
                    "result": "am_start_failed", "source": source})
        return {"success": False, "error": "am_start_failed", "reply": "WhatsApp liess sich nicht oeffnen."}
    btn = None
    wait_s = float(load_config().get("send_button_wait_s") or 10)
    deadline = time.time() + wait_s
    xml_text = ""
    while time.time() < deadline:
        time.sleep(0.8)
        xml_text = _ui_dump(serial)
        btn = _find_send_button(xml_text) if xml_text else None
        if btn:
            break
        if xml_text and re.search(r"nicht bei WhatsApp registriert|isn.t on WhatsApp|not on WhatsApp", xml_text):
            log_action({"action": "whatsapp_open", "to": mask_recipient(recipient, number), "text_len": len(text),
                        "package": pkg, "result": "not_on_whatsapp", "source": source})
            return {"success": False, "error": "not_on_whatsapp",
                    "reply": "Diese Nummer ist nicht bei WhatsApp registriert. Nichts gesendet."}
    log_action({"action": "whatsapp_open", "to": mask_recipient(recipient, number), "text_len": len(text),
                "package": pkg, "transport": dev["transport"], "send_button": bool(btn),
                "result": "ready" if btn else "no_send_button", "ms": int((time.time() - t0) * 1000), "source": source})
    if not btn:
        return {"success": False, "error": "no_send_button", "serial": serial,
                "reply": "Der Chat ist offen, aber ich finde den Senden-Knopf nicht. Ist die Nummer bei WhatsApp?"}
    return {"success": True, "serial": serial, "button": btn, "package": pkg, "transport": dev["transport"],
            "reply": "Chat ist vorbereitet."}


def whatsapp_send(number: str, text: str, source: str = "voice", recipient: str = "",
                  confirmed: bool = False) -> dict[str, Any]:
    """Sendet NUR mit confirmed=True. Genau ein Tap auf den Senden-Knopf (uiautomator-Position)."""
    if not confirmed:
        return {"success": False, "error": "not_confirmed", "reply": "Nicht bestaetigt, nichts gesendet."}
    prep = whatsapp_open_chat(number, text, source=source, recipient=recipient)
    if not prep.get("success"):
        return prep
    serial, btn = prep["serial"], prep["button"]
    scr = screen_state(serial)
    if not scr.get("unlocked"):
        return {"success": False, "error": "phone_locked",
                "reply": "Dein Handy wurde gesperrt. Bitte entsperre es, dann sag es nochmal."}
    x, y = int(btn["x"]), int(btn["y"])
    if not (0 < x < 5000 and 0 < y < 5000):
        return {"success": False, "error": "bad_coords", "reply": "Senden-Knopf hat ungueltige Position."}
    r = _adb(serial, ["shell", "input", "tap", str(x), str(y)], 10)
    time.sleep(1.0)
    after = _ui_dump(serial)
    entry_len = _entry_text_len(after) if after else None
    sent = bool(r.get("ok")) and (entry_len == 0 or entry_len is None)
    log_action({"action": "whatsapp_send", "to": mask_recipient(recipient, number), "text_len": len(text),
                "transport": prep.get("transport"), "tap": r.get("ok"), "entry_empty_after": entry_len == 0,
                "result": "sent" if sent else "unclear", "source": source})
    if not sent:
        return {"success": False, "error": "send_unclear",
                "reply": "Ich habe auf Senden getippt, bin aber nicht sicher, ob die Nachricht raus ist. Schau bitte kurz aufs Handy."}
    return {"success": True, "reply": "Nachricht gesendet."}


ensure_config_file()
