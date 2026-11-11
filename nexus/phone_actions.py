"""NEXUS Handy-Aktionen (heynexus-20261006).

Sicherheitsregel (Eddie / Stabschef):
- KEINE generische Remote-Shell. Nur feste, erlaubte Aktionen mit validierten Parametern.
- adb wird immer mit Argument-Liste (shell=False) und festem Serial aufgerufen.
- Primaer drahtlos (Tailscale) 192.0.2.10:5555, Fallback USB 249ac5d7 (konfigurierbar).
- Erlaubt: `adb connect <tcp-endpoint>` (idempotent). VERBOTEN: tcpip, kill-server,
  forward --remove(-all), pair, disconnect. ADB-Forwarding ist fuer Phone-Aktionen nicht erlaubt.
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

DEFAULT_CONFIG: dict[str, Any] = {
    "adb_path": r"C:\Users\gustl\AppData\Local\Android\Sdk\platform-tools\adb.exe",
    # Reihenfolge = Prioritaet. Erster Eintrag mit ':' ist der TCP-Endpunkt fuer `adb connect`.
    "serials": ["192.0.2.10:5555", "249ac5d7"],
    "tcp_endpoint": "192.0.2.10:5555",
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


def _err_text(e: BaseException) -> str:
    we = getattr(e, "winerror", None)
    msg = str(e).replace("\n", " ")[:160]
    if we is not None:
        return f"{type(e).__name__}:win={we}:{msg}"
    return f"{type(e).__name__}:{msg}"


def _recvexact(sock: object, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))  # type: ignore[attr-defined]
        if not chunk:
            break
        buf += chunk
    return buf


def _recvall(sock: object, limit: int = 8_000_000) -> bytes:
    parts: list[bytes] = []
    total = 0
    while total < limit:
        try:
            chunk = sock.recv(min(65536, limit - total))  # type: ignore[attr-defined]
        except Exception:
            break
        if not chunk:
            break
        parts.append(chunk)
        total += len(chunk)
    return b"".join(parts)


def _adb_send(sock: object, cmd: str) -> None:
    payload = cmd.encode("utf-8")
    sock.sendall(f"{len(payload):04x}".encode("ascii") + payload)  # type: ignore[attr-defined]


def _adb_status(sock: object) -> tuple[bool, str]:
    stat = _recvexact(sock, 4)
    if stat == b"OKAY":
        return True, ""
    if stat == b"FAIL":
        hx = _recvexact(sock, 4)
        try:
            n = int(hx, 16)
        except Exception:
            return False, (stat + hx).decode("utf-8", "replace")
        return False, _recvexact(sock, n).decode("utf-8", "replace")
    return False, f"bad_status:{stat!r}"


def _run_via_adb_server(cmd: list[str], timeout: float) -> dict[str, Any]:
    """Spricht den lokalen ADB-Server (:5037), falls adb.exe im Core-Prozess nicht startet."""
    import socket as _socket
    args = list(cmd[1:]) if cmd else []  # exe weglassen
    serial = ""
    if len(args) >= 2 and args[0] == "-s":
        serial = args[1]
        args = args[2:]
    if not args:
        return {"ok": False, "rc": -4, "out": "", "err": "bad_cmd"}
    if args[0] in ("tcpip", "kill-server", "disconnect", "pair", "usb", "root", "reboot"):
        return {"ok": False, "rc": -5, "out": "", "err": "forbidden"}
    if args[:1] == ["forward"]:
        return {"ok": False, "rc": -5, "out": "", "err": "forbidden"}

    def _connect() -> object:
        s = _socket.create_connection(("127.0.0.1", 5037), timeout=min(timeout, 5))
        s.settimeout(timeout)
        return s

    try:
        if args[0] == "devices":
            s = _connect()
            try:
                _adb_send(s, "host:devices")
                ok, err = _adb_status(s)
                if not ok:
                    return {"ok": False, "rc": 1, "out": "", "err": err, "via": "adb_server"}
                hx = _recvexact(s, 4)
                n = int(hx, 16)
                data = _recvexact(s, n).decode("utf-8", "replace")
                return {"ok": True, "rc": 0, "out": "List of devices attached\n" + data, "err": "", "via": "adb_server"}
            finally:
                s.close()
        if args[0] == "connect" and len(args) >= 2:
            ep = args[1]
            s = _connect()
            try:
                _adb_send(s, "host:connect:" + ep)
                ok, err = _adb_status(s)
                hx = _recvexact(s, 4)
                msg = ""
                if hx:
                    try:
                        msg = _recvexact(s, int(hx, 16)).decode("utf-8", "replace")
                    except Exception:
                        msg = hx.decode("utf-8", "replace")
                txt = (msg or err or "").strip()
                ok2 = ok and "connected to" in txt.lower() and "cannot" not in txt.lower() and "failed" not in txt.lower()
                # "already connected" gilt auch als Erfolg
                if "already connected" in txt.lower():
                    ok2 = True
                return {"ok": ok2, "rc": 0 if ok2 else 1, "out": txt, "err": "" if ok2 else txt, "via": "adb_server"}
            finally:
                s.close()
        if args[0] == "mdns":
            s = _connect()
            try:
                _adb_send(s, "host:mdns:services")
                ok, err = _adb_status(s)
                if not ok:
                    return {"ok": False, "rc": 1, "out": "", "err": err or "mdns", "via": "adb_server"}
                hx = _recvexact(s, 4)
                data = b""
                if hx:
                    try:
                        data = _recvexact(s, int(hx, 16))
                    except Exception:
                        data = hx + _recvall(s)
                return {"ok": True, "rc": 0, "out": data.decode("utf-8", "replace"), "err": "", "via": "adb_server"}
            finally:
                s.close()
        if not serial:
            return {"ok": False, "rc": -4, "out": "", "err": "need_serial"}
        # pull via SYNC
        if args[0] == "pull" and len(args) >= 3:
            remote, local = args[1], args[2]
            s = _connect()
            try:
                _adb_send(s, "host:transport:" + serial)
                ok, err = _adb_status(s)
                if not ok:
                    return {"ok": False, "rc": 1, "out": "", "err": err, "via": "adb_server"}
                _adb_send(s, "sync:")
                ok, err = _adb_status(s)
                if not ok:
                    return {"ok": False, "rc": 1, "out": "", "err": err, "via": "adb_server"}
                path_b = remote.encode("utf-8")
                s.sendall(b"RECV" + len(path_b).to_bytes(4, "little") + path_b)
                chunks: list[bytes] = []
                while True:
                    hdr = _recvexact(s, 8)
                    if len(hdr) < 8:
                        return {"ok": False, "rc": 1, "out": "", "err": "pull_short", "via": "adb_server"}
                    kind, n = hdr[:4], int.from_bytes(hdr[4:], "little")
                    if kind == b"DATA":
                        chunks.append(_recvexact(s, n))
                    elif kind == b"DONE":
                        break
                    elif kind == b"FAIL":
                        return {"ok": False, "rc": 1, "out": "", "err": _recvexact(s, n).decode("utf-8", "replace"), "via": "adb_server"}
                    else:
                        return {"ok": False, "rc": 1, "out": "", "err": f"pull_{kind!r}", "via": "adb_server"}
                Path(local).write_bytes(b"".join(chunks))
                return {"ok": True, "rc": 0, "out": f"pulled {remote}", "err": "", "via": "adb_server"}
            finally:
                s.close()
        # shell / exec-out / other device services
        if args[0] == "exec-out":
            service = "exec:" + " ".join(args[1:])
        elif args[0] == "shell":
            service = "shell:" + " ".join(args[1:])
        else:
            service = args[0] + (":" + " ".join(args[1:]) if args[1:] else "")
        s = _connect()
        try:
            _adb_send(s, "host:transport:" + serial)
            ok, err = _adb_status(s)
            if not ok:
                return {"ok": False, "rc": 1, "out": "", "err": err, "via": "adb_server"}
            _adb_send(s, service)
            ok, err = _adb_status(s)
            if not ok:
                return {"ok": False, "rc": 1, "out": "", "err": err, "via": "adb_server"}
            data = _recvall(s)
            out = data.decode("utf-8", "replace") if args[0] != "exec-out" else data.decode("utf-8", "replace")
            return {"ok": True, "rc": 0, "out": out, "err": "", "via": "adb_server"}
        finally:
            s.close()
    except Exception as e:
        return {"ok": False, "rc": -3, "out": "", "err": "adb_server:" + _err_text(e)}


def _run_exe(cmd: list[str], timeout: float) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout, shell=False, check=False,
            stdin=subprocess.DEVNULL, creationflags=_NOWIN,
        )
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        return {"ok": proc.returncode == 0, "rc": proc.returncode, "out": out, "err": err}
    except FileNotFoundError:
        return {"ok": False, "rc": -1, "out": "", "err": "adb_not_found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": -2, "out": "", "err": "timeout"}
    except OSError as e:
        try:
            proc = subprocess.run(
                cmd, capture_output=True, timeout=timeout, shell=False, check=False,
                stdin=subprocess.DEVNULL,
            )
            out = proc.stdout.decode("utf-8", "replace")
            err = proc.stderr.decode("utf-8", "replace")
            return {"ok": proc.returncode == 0, "rc": proc.returncode, "out": out, "err": err}
        except Exception as e2:
            return {"ok": False, "rc": -3, "out": "", "err": _err_text(e2)}
    except Exception as e:  # pragma: no cover
        return {"ok": False, "rc": -3, "out": "", "err": _err_text(e)}


def _run(cmd: list[str], timeout: float) -> dict[str, Any]:
    r = _run_exe(cmd, timeout)
    spawn_fail = r.get("rc") in (-1, -3) or (r.get("err") or "").startswith("OSError")
    if spawn_fail:
        r2 = _run_via_adb_server(cmd, timeout)
        try:
            _rlog("adb exe fail rc=%s err=%s -> server rc=%s err=%s via=%s" % (
                r.get("rc"), (r.get("err") or "")[:80], r2.get("rc"),
                (r2.get("err") or "")[:80], r2.get("via")))
        except Exception:
            pass
        if r2.get("ok") or r2.get("out") or (r2.get("err") or "").startswith("adb_server") is False:
            # Server-Antwort (auch Fehler wie offline) ist informativer als Spawn-OSError
            if r2.get("err") != "need_serial" or not r.get("err"):
                return r2
        return r
    return r


def _adb(serial: str, args: list[str], timeout: float = 15) -> dict[str, Any]:
    """Interner Aufruf: nur mit festen Argumentlisten aus diesem Modul benutzen."""
    cfg = load_config()
    if not _RE_SERIAL.match(serial or ""):
        return {"ok": False, "rc": -4, "out": "", "err": "bad_serial"}
    if args and args[0] in ("tcpip", "kill-server", "disconnect", "pair", "usb", "root", "reboot"):
        return {"ok": False, "rc": -5, "out": "", "err": "forbidden"}
    if args[:1] == ["forward"]:
        return {"ok": False, "rc": -5, "out": "", "err": "forbidden"}
    exe = _adb_exe(cfg)
    r = _run([exe, "-s", serial] + args, timeout)
    blob = ((r.get("out") or "") + " " + (r.get("err") or "")).lower()
    if (not r.get("ok")) and any(s in blob for s in ("not found", "offline", "no devices", "device unauthorized", "connection reset")):
        # Verbindungsabbruch: adb connect auf den TCP-Endpunkt, dann mit -s wiederholen
        dev = select_device(connect=True)
        if dev.get("status") == "OK" and _RE_SERIAL.match(str(dev.get("serial") or "")):
            serial = dev["serial"]
            r = _run([exe, "-s", serial] + args, timeout)
            r["reconnected"] = True
            r["serial"] = serial
    return r


def adb_devices() -> dict[str, str]:
    cfg = load_config()
    exe = _adb_exe(cfg)
    r = _run([exe, "devices"], 8)
    def _parse(out: str) -> dict[str, str]:
        res: dict[str, str] = {}
        for line in (out or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                res[parts[0]] = parts[1]
        return res
    res = _parse(r.get("out") or "")
    if not res:
        try:
            proc = subprocess.run([exe, "devices"], capture_output=True, timeout=8, shell=False, check=False)
            r2 = {"ok": proc.returncode == 0, "rc": proc.returncode,
                  "out": proc.stdout.decode("utf-8", "replace"),
                  "err": proc.stderr.decode("utf-8", "replace")}
            res = _parse(r2.get("out") or "")
            r = r2
        except Exception as e:
            r = {"ok": False, "rc": -3, "out": "", "err": type(e).__name__}
        if not res:
            try:
                _rlog("adb_devices empty exe=%s exists=%s rc=%s err=%s out=%s" % (
                    exe, Path(exe).is_file() if exe else False, r.get("rc"),
                    (r.get("err") or "")[:120], (r.get("out") or "")[:120].replace("\n", " ")))
            except Exception:
                pass
    return res


def select_device(connect: bool = True) -> dict[str, Any]:
    """Waehlt das Geraet: schon erreichbare USB/TCP-Serials zuerst, erst dann `adb connect`."""
    cfg = load_config()
    if cfg.get("adb_path") and not Path(str(cfg["adb_path"])).is_file():
        pass
    connect_info = None
    tcp = cfg.get("tcp_endpoint") or ""
    devs = adb_devices()

    def _pick(devs_map: dict[str, str]) -> dict[str, Any] | None:
        for s in cfg["serials"]:
            if devs_map.get(s) == "device":
                _WD["last_seen"] = time.time()
                return {"status": "OK", "serial": s, "transport": "tcp" if ":" in s else "usb",
                        "connect": connect_info}
        for s, st in devs_map.items():
            if st == "device" and s not in cfg["serials"]:
                ident = read_identity(s)
                if identity_matches(ident):
                    remember_identity(ident, s)
                    return {"status": "OK", "serial": s, "transport": "wifi", "connect": connect_info,
                            "identity_by": ident.get("_matched_by")}
        return None

    found = _pick(devs)
    if found:
        return found
    if connect and tcp:
        r = _run([_adb_exe(cfg), "connect", tcp], 6)
        connect_info = (r.get("out") or r.get("err") or "").strip()[:120]
        devs = adb_devices()
        found = _pick(devs)
        if found:
            return found
    reason = "ADB_UNAVAILABLE"
    states = {s: devs.get(s, "missing") for s in cfg["serials"]}
    if any(v == "unauthorized" for v in states.values()):
        reason = "ADB_UNAUTHORIZED"
    return {"status": "UNAVAILABLE", "reason": reason, "states": states, "connect": connect_info,
            "reply": "Das Handy ist gerade nicht per ADB erreichbar. Ich verbinde kabellos, USB ist nur der Fallback."}


# ---------------------------------------------------------------- identity (005D)
IDENTITY_PATH = ROOT / "workspace" / "phone_data" / "phone_identity.json"
RECONNECT_LOG = ROOT / "workspace" / "phone_data" / "adb_reconnect.log"
FALLBACK_DEVICE = "sapphiren"
TAILSCALE_IP = "192.0.2.10"
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
    # Immer zuerst der konfigurierte TCP-Endpunkt (jetzt 5555), nicht ein alter last_port (38041).
    cfg = load_config()
    tcp = str(cfg.get("tcp_endpoint") or "").strip()
    if tcp:
        eps.append(tcp)
    if known.get("last_endpoint") and known["last_endpoint"] not in eps:
        eps.append(str(known["last_endpoint"]))
    # LAN-Fallback (Tailscale 100.x kann weg sein, das Handy bleibt im Heimnetz erreichbar)
    lan_ep = "192.0.2.56:5555"
    if lan_ep not in eps:
        eps.append(lan_ep)
    for m in mine:
        eps.append(f'{m["host"]}:{m["port"]}')
        ep2 = f'{TAILSCALE_IP}:{m["port"]}'
        if ep2 not in eps:
            eps.append(ep2)
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
    if dev.get("status") != "OK":
        dev = select_device(connect=True)  # adb connect <tcp_endpoint>
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
    scr_on = (re.search(r"mIsScreenOn\s*=\s*(true|false)", out, re.I)
              or re.search(r"mScreenOnFully=(true|false)", out)
              or re.search(r"mScreenOn\s*=\s*(true|false)", out))
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


# -------------------------------------------------------------- Live-Talk-Steuerung (kein Root)
# Nur feste Argumentlisten. Kein `su`, kein generic shell, kein tcpip/root/reboot (_adb blockt das).
_VOLUME_KEY = {"up": "24", "down": "25", "mute": "164"}  # KEYCODE_VOLUME_*


def _volume_index(serial: str) -> dict[str, Any]:
    """Liest STREAM_MUSIC (stream 3) ueber cmd media_session volume --get."""
    r = _adb(serial, ["shell", "cmd", "media_session", "volume", "--stream", "3", "--get"], 8)
    blob = ((r.get("out") or "") + " " + (r.get("err") or "")).strip()
    m = re.search(r"volume is (\d+) in range \[(\d+)\.\.(\d+)\]", blob)
    if not m:
        return {"ok": False, "index": None, "raw": blob[:200]}
    return {"ok": True, "index": int(m.group(1)), "min": int(m.group(2)), "max": int(m.group(3)), "raw": blob[:200]}


def phone_volume(action: str, source: str = "voice") -> dict[str, Any]:
    """Lautstaerke am Handy. cmd input keyevent wirkt hier, blosses input keyevent oft nicht."""
    act = str(action or "").lower().strip()
    if act not in _VOLUME_KEY:
        return {"success": False, "error": "bad_action", "reply": "Ich kann die Lautstaerke nur lauter, leiser oder stumm schalten."}
    dev = select_device()
    if dev["status"] != "OK":
        return {"success": False, "status": "UNAVAILABLE", "error": dev.get("reason"), "reply": dev["reply"], "device": dev}
    serial = dev["serial"]
    before = _volume_index(serial)
    r = _adb(serial, ["shell", "cmd", "input", "keyevent", _VOLUME_KEY[act]], 10)
    time.sleep(0.55)
    after = _volume_index(serial)
    def _changed(a, b):
        return a.get("index") is not None and b.get("index") is not None and a["index"] != b["index"]
    changed = _changed(before, after)
    at_limit = False
    if after.get("index") is not None:
        if act == "up" and after["index"] >= int(after.get("max") or 15) and before.get("index") == after["index"]:
            at_limit = True
        if act == "down" and after["index"] <= int(after.get("min") or 0) and before.get("index") == after["index"]:
            at_limit = True
        if act == "mute" and after["index"] == 0:
            at_limit = True
    if not changed and not at_limit:
        r = _adb(serial, ["shell", "input", "keyevent", _VOLUME_KEY[act]], 10)
        time.sleep(0.55)
        after = _volume_index(serial)
        changed = _changed(before, after)
    ok = changed or at_limit
    log_action({"action": "phone_volume", "dir": act, "result": "ok" if ok else "fail",
                "before": before.get("index"), "after": after.get("index"), "source": source})
    if not ok:
        return {"success": False, "error": r.get("err") or "volume_unchanged",
                "before": before.get("index"), "after": after.get("index"),
                "reply": "Die Lautstaerke am Handy liess sich nicht aendern."}
    idx = after.get("index")
    spoken = {"up": f"Handy lauter, Stufe {idx}.", "down": f"Handy leiser, Stufe {idx}.",
              "mute": "Handy stumm geschaltet."}[act]
    return {"success": True, "action": act, "transport": dev.get("transport"),
            "before": before.get("index"), "after": idx, "reply": spoken}


def phone_battery(source: str = "voice") -> dict[str, Any]:
    """Akkustand lesend (dumpsys, kein Root)."""
    dev = select_device(connect=True)
    if dev["status"] != "OK":
        return {"success": False, "status": "UNAVAILABLE", "error": dev.get("reason"), "reply": dev["reply"], "device": dev}
    r = _adb(dev["serial"], ["shell", "dumpsys", "battery"], 10)
    if not r.get("ok"):
        return {"success": False, "error": r.get("err") or "adb_failed", "reply": "Den Akkustand kann ich gerade nicht lesen."}
    out = r.get("out") or ""
    level = pct = None
    import re as _re
    m = _re.search(r"level:\s*(\d+)", out)
    if m:
        level = int(m.group(1))
    plugged = "plugged" in out.lower() and "plugged: 0" not in out.lower().replace(" ", "")
    # "plugged: 0" means unplugged
    plugged = bool(_re.search(r"plugged:\s*([1-9]\d*)", out))
    if level is None:
        return {"success": False, "error": "parse", "reply": "Den Akkustand konnte ich nicht auslesen."}
    lag = " und laedt" if plugged else ""
    reply = f"Der Akku vom Handy steht bei {level} Prozent{lag}."
    log_action({"action": "phone_battery", "level": level, "result": "ok", "source": source})
    return {"success": True, "level": level, "plugged": plugged, "reply": reply}


def phone_screenshot(source: str = "voice") -> dict[str, Any]:
    """Screenshot per screencap + pull nach workspace/tmp (kein Root, kein C:)."""
    dev = select_device()
    if dev["status"] != "OK":
        return {"success": False, "status": "UNAVAILABLE", "error": dev.get("reason"), "reply": dev["reply"], "device": dev}
    dest_dir = ROOT / "workspace" / "tmp" / "livetalk_shots"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / time.strftime("shot_%Y%m%d_%H%M%S.png")
    remote = "/sdcard/Download/nexus_livetalk_shot.png"
    r1 = _adb(dev["serial"], ["shell", "screencap", "-p", remote], 15)
    if not r1.get("ok"):
        return {"success": False, "error": r1.get("err") or "screencap_failed",
                "reply": "Ein Screenshot liess sich am Handy nicht aufnehmen. Ohne Root geht das nur, wenn der Bildschirm entsperrt ist."}
    r2 = _adb(dev["serial"], ["pull", remote, str(dest)], 20)
    ok = bool(r2.get("ok")) and dest.is_file() and dest.stat().st_size > 100
    log_action({"action": "phone_screenshot", "result": "ok" if ok else "fail", "source": source})
    if not ok:
        return {"success": False, "error": r2.get("err") or "pull_failed",
                "reply": "Der Screenshot wurde aufgenommen, konnte aber nicht auf den PC geholt werden."}
    return {"success": True, "path": str(dest), "transport": dev.get("transport"),
            "reply": "Der Screenshot vom Handy liegt auf dem PC."}


# ---------------------------------------------------------------- Live-Talk Medien / Assistent (kein Root)
_MEDIA_KEYS = {
    "play_pause": "85",
    "stop": "86",
    "next": "87",
    "previous": "88",
    "play": "126",
    "pause": "127",
}
_GENRE_WORDS = frozenset({
    "techno", "house", "trance", "hardstyle", "hardcore", "gabber", "edm", "electro",
    "rock", "pop", "jazz", "blues", "metal", "punk", "funk", "soul", "disco",
    "hiphop", "hip-hop", "hip hop", "rap", "rnb", "r&b", "reggae", "ska",
    "classical", "klassik", "schlager", "volksmusik", "volkslieder",
    "ambient", "lofi", "lo-fi", "indie", "folk", "country", "gospel",
    "drum and bass", "drumandbass", "dnb", "dubstep", "trap", "afrobeat",
})


def _device_or_fail() -> dict[str, Any]:
    dev = select_device()
    if dev.get("status") != "OK":
        return {"success": False, "status": "UNAVAILABLE", "error": dev.get("reason"),
                "reply": dev.get("reply") or "Das Handy ist gerade nicht erreichbar.", "device": dev}
    return dev


def phone_keyevent(code: str | int, source: str = "voice") -> dict[str, Any]:
    """Einen erlaubten Android-Keycode senden (nur Ziffern)."""
    code_s = str(code).strip()
    if not re.fullmatch(r"\d{1,3}", code_s):
        return {"success": False, "error": "bad_keycode", "reply": "Diesen Tastencode kenne ich nicht."}
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    r = _adb(dev["serial"], ["shell", "input", "keyevent", code_s], 10)
    ok = bool(r.get("ok"))
    log_action({"action": "phone_keyevent", "code": code_s, "result": "ok" if ok else "fail", "source": source})
    return {"success": ok, "code": code_s, "transport": dev.get("transport"),
            "reply": "OK." if ok else "Der Tastendruck am Handy hat nicht geklappt.",
            "error": None if ok else (r.get("err") or "adb_failed")}


def media_session_state(serial: str | None = None, prefer: str | None = None) -> dict[str, Any]:
    """Liest dumpsys media_session. state=3 ist PLAYING. prefer filtert nach Paketname (z. B. youtube)."""
    if not serial:
        dev = _device_or_fail()
        if not dev.get("serial"):
            return {**dev, "playing": False, "state": None}
        serial = dev["serial"]
    r = _adb(serial, ["shell", "dumpsys", "media_session"], 12)
    text = r.get("out") or ""
    sessions: list[dict] = []
    current: dict[str, Any] = {}
    for ln in text.splitlines():
        m = re.search(r"package=([A-Za-z0-9._]+)", ln)
        if m:
            if current:
                sessions.append(current)
            current = {"package": m.group(1), "state": None, "line": ln.strip()}
        sm = re.search(r"state=([A-Z]+)\((\d+)\)", ln)
        if sm and current:
            current["state"] = int(sm.group(2))
            current["state_name"] = sm.group(1)
            current["line"] = ln.strip()
        if current and re.search(r"description=", ln):
            current["description"] = ln.strip()[:200]
    if current:
        sessions.append(current)
    def _is_pref(s: dict) -> bool:
        if not prefer:
            return True
        return prefer.lower() in str(s.get("package") or "").lower()
    wanted = [s for s in sessions if _is_pref(s)] or ([] if prefer else sessions)
    playing = next((s for s in wanted if s.get("state") == 3), None)
    chosen = playing or next((s for s in wanted if s.get("state")), None)
    excerpt_lines = [s.get("line") or "" for s in (wanted or sessions)[:12]]
    if not chosen and prefer:
        return {"success": True, "playing": False, "state": None, "package": None,
                "sessions": sessions, "excerpt": "\n".join(excerpt_lines), "raw": text[:4000]}
    return {"success": True, "playing": bool(chosen and chosen.get("state") == 3),
            "state": (chosen or {}).get("state"), "package": (chosen or {}).get("package"),
            "description": (chosen or {}).get("description"), "sessions": sessions,
            "excerpt": "\n".join(excerpt_lines), "raw": text[:4000]}


def phone_media(action: str, source: str = "voice") -> dict[str, Any]:
    """Play/Pause/Weiter/Zurueck/Stopp per Media-Keyevent."""
    act = str(action or "").lower().strip()
    aliases = {"play": "play", "abspielen": "play", "weiter": "play", "pause": "pause",
               "pausieren": "pause", "anhalten": "pause", "stopp": "stop", "stop": "stop",
               "halt": "stop", "naechste": "next", "nächste": "next", "next": "next",
               "weiterer": "next", "skip": "next", "vorherige": "previous", "previous": "previous",
               "zurueck": "previous", "zurück": "previous", "play_pause": "play_pause"}
    key = aliases.get(act, act)
    if key not in _MEDIA_KEYS:
        return {"success": False, "error": "bad_media", "reply": "Das kann ich an der Wiedergabe so nicht."}
    spoken = {"play": "Wiedergabe gestartet.", "pause": "Pause.", "stop": "Wiedergabe gestoppt.",
              "next": "Naechster Titel.", "previous": "Vorheriger Titel.",
              "play_pause": "Play Pause."}[key]
    dev = _device_or_fail()
    serial = dev.get("serial")
    before = media_session_state(serial, prefer="youtube") if serial else {}
    disp = {"play": "play", "pause": "pause", "stop": "stop",
            "next": "next", "previous": "previous", "play_pause": "play-pause"}
    r = {"success": False}
    if serial and disp.get(key):
        r = _adb(serial, ["shell", "cmd", "media_session", "dispatch", disp[key]], 8)
        r = {"success": bool(r.get("ok")), "error": None if r.get("ok") else (r.get("err") or "dispatch_failed")}
    if not r.get("success"):
        r = phone_keyevent(_MEDIA_KEYS[key], source=source)
    time.sleep(0.8)
    after = media_session_state(serial, prefer="youtube") if serial else {}
    # Nach next/previous ist die Session kurz NONE, bis das naechste Video laedt.
    if key in ("next", "previous", "play") and serial:
        for _ in range(7):
            if after.get("state") == 3 and "youtube" in str(after.get("package") or "").lower():
                break
            time.sleep(0.7)
            after = media_session_state(serial, prefer="youtube")
    r["action"] = key
    r["state_before"] = before.get("state")
    r["state_after"] = after.get("state")
    r["package"] = after.get("package")
    r["playing"] = after.get("playing")
    if r.get("success"):
        r["reply"] = spoken
    return r


def _wake_screen(serial: str) -> None:
    _adb(serial, ["shell", "input", "keyevent", "224"], 6)  # WAKEUP


def _youtube_search_query(term: str) -> str:
    q = re.sub(r"\s+", " ", str(term or "").strip())
    if not q:
        return "mix"
    low = q.lower()
    if low in _GENRE_WORDS or (len(q.split()) <= 2 and low.split()[0] in _GENRE_WORDS):
        if "mix" not in low:
            return q + " mix"
    return q


def _parse_bounds(raw: str) -> tuple[int, int, int, int] | None:
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", raw or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def _node_center(bounds: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bounds
    return (x1 + x2) // 2, (y1 + y2) // 2


def _dismiss_youtube_popups(serial: str) -> None:
    xml = _ui_dump(serial)
    if not xml:
        return
    try:
        root = ET.fromstring(xml)
    except Exception:
        return
    want = ("nicht jetzt", "no thanks", "later", "ablehnen", "skip", "schließen", "schliessen", "close")
    for node in root.iter("node"):
        desc = ((node.attrib.get("content-desc") or "") + " " + (node.attrib.get("text") or "")).strip().lower()
        if not any(w in desc for w in want):
            continue
        b = _parse_bounds(node.attrib.get("bounds") or "")
        if not b:
            continue
        x, y = _node_center(b)
        _adb(serial, ["shell", "input", "tap", str(x), str(y)], 6)
        time.sleep(0.6)
        return


def find_first_youtube_video(xml_text: str) -> dict[str, Any] | None:
    """Erstes Video in einer YouTube-Suche (kein Suchfeld, bevorzugt keine Shorts)."""
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    candidates: list[dict[str, Any]] = []
    for node in root.iter("node"):
        a = node.attrib
        desc = (a.get("content-desc") or a.get("text") or "").strip()
        rid = (a.get("resource-id") or "")
        clickable = a.get("clickable") == "true"
        bounds = _parse_bounds(a.get("bounds") or "")
        if not bounds or not desc:
            continue
        x1, y1, x2, y2 = bounds
        w, h = x2 - x1, y2 - y1
        if w < 200 or h < 80 or y1 < 180:
            continue
        low = desc.lower()
        if any(s in low for s in ("search", "suchen", "filter", "abo", "subscribe", "konto", "account",
                                  "gesponsert", "sponsored", "werbung", "advertisement",
                                  "neuem fenster", "opens in a new", "anzeige")):
            continue
        score = 0
        if any(k in low for k in ("aufrufe", "views", "minuten", "minute", "sekunden", "stunden", "hour", "video")):
            score += 5
        if "thumbnail" in rid or "video" in rid or rid.endswith("/dismissable_container") or "watch" in rid:
            score += 3
        if clickable:
            score += 2
        if "short" in low or "reel" in rid.lower() or "shorts" in rid.lower():
            score -= 8
        if score <= 0:
            continue
        candidates.append({"desc": desc, "bounds": bounds, "y": y1, "score": score, "rid": rid,
                           "x": _node_center(bounds)[0], "cy": _node_center(bounds)[1]})
    if not candidates:
        # Fallback: erstes grosses klickbares Element unter der Suchleiste
        for node in root.iter("node"):
            a = node.attrib
            if a.get("clickable") != "true":
                continue
            bounds = _parse_bounds(a.get("bounds") or "")
            if not bounds:
                continue
            x1, y1, x2, y2 = bounds
            if y1 >= 220 and (x2 - x1) >= 300 and (y2 - y1) >= 140:
                return {"desc": (a.get("content-desc") or a.get("text") or "Video").strip(),
                        "bounds": bounds, "x": (x1 + x2) // 2, "cy": (y1 + y2) // 2, "score": 1}
        return None
    candidates.sort(key=lambda c: (-c["score"], c["y"]))
    return candidates[0]


def _foreground_hint(serial: str) -> str:
    r = _adb(serial, ["shell", "dumpsys", "activity", "activities"], 10)
    text = r.get("out") or ""
    for key in ("topResumedActivity", "mResumedActivity", "mFocusedApp"):
        for ln in text.splitlines():
            if key in ln:
                return ln.strip()
    return ""



def _youtube_results_url(term: str) -> str:
    q = _youtube_search_query(term)
    # sp=EgIQAQ%3D%3D = YouTube-Filter Typ: Video (keine Shorts, keine Playlists)
    return "https://www.youtube.com/results?search_query=" + quote(q) + "&sp=EgIQAQ%3D%3D"


def _youtube_is_shorts(xml_text: str) -> bool:
    low = (xml_text or "").lower()
    return any(s in low for s in ("reel_watch_fragment", "reel_play_pause_button", "reel_recycler",
                                  "reel_player_overlay"))


def _youtube_open_search(serial: str, url: str) -> dict[str, Any]:
    """YouTube hart neu starten, damit keine alte Shorts-Ansicht den Intent verschluckt."""
    _adb(serial, ["shell", "am", "force-stop", "com.google.android.youtube"], 12)
    time.sleep(0.7)
    # adb shell klebt Argumente zu einer sh-Zeile: & in der URL muss gequotet sein.
    quoted = "'" + str(url).replace("'", "") + "'"
    r = _adb(serial, [
        "shell", "am", "start",
        "--activity-clear-task",
        "-a", "android.intent.action.VIEW",
        "-d", quoted,
        "-p", "com.google.android.youtube",
    ], 15)
    if not r.get("ok"):
        r = _adb(serial, ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", quoted,
                          "-p", "com.google.android.youtube"], 15)
    return r


def _youtube_tap_play_control(serial: str, xml_text: str) -> bool:
    """Play-Knopf in Watch- oder Shorts-Ansicht antippen, falls sichtbar."""
    if not xml_text:
        return False
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return False
    want = ("abspielen", "play video", "wiedergeben", "video abspielen", "play")
    pause = ("pausieren", "pause video", "video pausieren")
    for node in root.iter("node"):
        a = node.attrib
        rid = (a.get("resource-id") or "").lower()
        desc = ((a.get("content-desc") or "") + " " + (a.get("text") or "")).strip().lower()
        if a.get("clickable") != "true":
            continue
        is_play_btn = "play" in rid and "pause" not in desc
        if any(w in desc for w in pause):
            return False  # laeuft bereits
        if is_play_btn or any(w in desc for w in want):
            b = _parse_bounds(a.get("bounds") or "")
            if not b:
                continue
            x, y = _node_center(b)
            _adb(serial, ["shell", "input", "tap", str(x), str(y)], 8)
            return True
    return False


def youtube_play(query: str, source: str = "voice") -> dict[str, Any]:
    """Suche auf YouTube oeffnen (Filter: Video, keine Shorts), erstes Ergebnis antippen, Wiedergabe pruefen (state=3)."""
    term = re.sub(r"\s+", " ", str(query or "").strip())
    if not term:
        return {"success": False, "error": "empty_query", "reply": "Was soll ich auf YouTube spielen?"}
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    serial = dev["serial"]
    _wake_screen(serial)
    time.sleep(0.4)
    scr = screen_state(serial)
    if scr.get("keyguard") is True:
        return {"success": False, "error": "LOCKED", "screen": scr,
                "reply": "Das Handy ist gesperrt. Bitte entsperre es, dann spiele ich das Video."}
    if scr.get("screen_on") is False:
        _wake_screen(serial)
        time.sleep(0.5)
        scr = screen_state(serial)
        if scr.get("keyguard") is True:
            return {"success": False, "error": "LOCKED", "screen": scr,
                    "reply": "Das Handy ist gesperrt. Bitte entsperre es, dann spiele ich das Video."}

    q = _youtube_search_query(term)
    url = _youtube_results_url(term)
    r = _youtube_open_search(serial, url)
    if not r.get("ok"):
        log_action({"action": "youtube_play", "query": q[:80], "result": "intent_failed", "source": source})
        return {"success": False, "error": "intent_failed", "reply": "Ich konnte die YouTube-Suche nicht oeffnen."}

    hit = None
    xml = ""
    focus = ""
    skipped_shorts = 0
    for attempt in range(5):
        time.sleep(3.2 if attempt == 0 else 2.0)
        _dismiss_youtube_popups(serial)
        focus = _foreground_hint(serial)
        xml = _ui_dump(serial) or ""
        if _youtube_is_shorts(xml):
            skipped_shorts += 1
            _adb(serial, ["shell", "input", "keyevent", "4"], 6)  # BACK
            time.sleep(0.5)
            if attempt >= 1:
                r = _youtube_open_search(serial, url)
            continue
        low_xml = xml.lower()
        if "kuerzlich" in low_xml or "kuerzlich" in low_xml or "miui.home:id/clearanimview" in low_xml or "kuerzlich" in xml.lower():
            # Recents: Intent wiederholen
            if "k" in low_xml and "rzlich" in low_xml:
                _adb(serial, ["shell", "input", "keyevent", "3"], 6)
                time.sleep(0.4)
                _youtube_open_search(serial, url)
                continue
        hit = find_first_youtube_video(xml)
        if hit:
            break

    title = None
    tapped = False
    used_key = False
    if hit:
        title = (hit.get("desc") or "").split(".")[0].strip()[:80]
        _adb(serial, ["shell", "input", "tap", str(int(hit["x"])), str(int(hit["cy"]))], 8)
        tapped = True
        time.sleep(3.0)
        xml2 = _ui_dump(serial) or ""
        if _youtube_is_shorts(xml2):
            skipped_shorts += 1
            _adb(serial, ["shell", "input", "keyevent", "4"], 6)
            time.sleep(1.0)
            xml = _ui_dump(serial) or ""
            # zweites Ergebnis versuchen
            cands = []
            try:
                # find again, skip first y if possible
                hit2 = find_first_youtube_video(xml)
                if hit2 and (abs(int(hit2.get("y") or 0) - int(hit.get("y") or 0)) > 80 or hit2.get("desc") != hit.get("desc")):
                    _adb(serial, ["shell", "input", "tap", str(int(hit2["x"])), str(int(hit2["cy"]))], 8)
                    title = (hit2.get("desc") or title or "").split(".")[0].strip()[:80]
                    time.sleep(3.0)
                    xml2 = _ui_dump(serial) or ""
            except Exception:
                pass
            if _youtube_is_shorts(xml2):
                _adb(serial, ["shell", "input", "keyevent", "4"], 6)
        else:
            if _youtube_tap_play_control(serial, xml2):
                time.sleep(1.2)

    def _keep_youtube_fg() -> None:
        focus_now = _foreground_hint(serial)
        if "youtube" not in (focus_now or "").lower():
            _adb(serial, ["shell", "monkey", "-p", "com.google.android.youtube",
                          "-c", "android.intent.category.LAUNCHER", "1"], 12)
            time.sleep(1.2)

    session = media_session_state(serial, prefer="youtube")
    if not session.get("playing"):
        _keep_youtube_fg()
        xml2 = _ui_dump(serial) or ""
        _youtube_tap_play_control(serial, xml2)
        _adb(serial, ["shell", "cmd", "media_session", "dispatch", "play"], 8)
        phone_keyevent("126", source=source)
        used_key = True
        time.sleep(1.5)
        session = media_session_state(serial, prefer="youtube")
    for _wait in range(6):
        if session.get("playing") and session.get("state") == 3 and "youtube" in (session.get("package") or "").lower():
            break
        _keep_youtube_fg()
        if _wait in (1, 3):
            _adb(serial, ["shell", "cmd", "media_session", "dispatch", "play"], 8)
        time.sleep(1.6)
        session = media_session_state(serial, prefer="youtube")
    playing = bool(session.get("playing") and session.get("state") == 3)
    pkg = session.get("package") or ""
    if playing and "youtube" not in pkg.lower():
        playing = False
    log_action({"action": "youtube_play", "query": q[:80], "tapped": tapped,
                "playing": playing, "state": session.get("state"), "package": pkg,
                "shorts_skipped": skipped_shorts, "source": source})
    if playing:
        reply = f"{q} laeuft auf YouTube."
        if title and title.lower() not in q.lower() and len(title) < 60:
            reply = f"{q} laeuft auf YouTube: {title}."
        return {"success": True, "reply": reply, "query": q, "title": title, "tapped": tapped,
                "used_keyevent": used_key, "state": 3, "playing": True,
                "package": pkg, "focus": focus, "excerpt": session.get("excerpt")}
    if not tapped:
        return {"success": False,
                "reply": "Die YouTube-Suche ist offen, aber ich habe kein Video zum Antippen gefunden.",
                "query": q, "state": session.get("state"), "package": pkg,
                "focus": focus, "excerpt": session.get("excerpt"), "shorts_skipped": skipped_shorts}
    return {"success": False, "reply": "Ich habe das Video angetippt, aber YouTube spielt gerade nicht.",
            "query": q, "title": title, "tapped": True, "used_keyevent": used_key,
            "state": session.get("state"), "package": pkg, "focus": focus,
            "excerpt": session.get("excerpt"), "shorts_skipped": skipped_shorts}


def start_music(source: str = "voice") -> dict[str, Any]:
    """YouTube Music, sonst Spotify, sonst Standard-Musikplayer, dann Play."""
    for app in ("youtube music", "spotify"):
        r = open_phone_app(app, source=source)
        if r.get("success"):
            time.sleep(1.2)
            phone_keyevent("126", source=source)
            r["reply"] = r.get("reply") or f"{app} ist offen."
            return r
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    r = _adb(dev["serial"], ["shell", "am", "start", "-a", "android.intent.action.MUSIC_PLAYER"], 12)
    phone_keyevent("126", source=source)
    if r.get("ok"):
        return {"success": True, "reply": "Der Musikplayer ist gestartet.", "transport": dev.get("transport")}
    return {"success": False, "reply": "Ich konnte keinen Musikplayer auf dem Handy starten."}


def close_phone_app(name: str, source: str = "voice") -> dict[str, Any]:
    found = find_phone_app(name)
    if not found:
        return {"success": False, "error": "unknown_app", "reply": f"Die App {name} kenne ich auf dem Handy nicht."}
    key, spec = found
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    pkgs = installed_packages(dev["serial"])
    pkg = next((p for p in spec["packages"] if p in pkgs), None)
    if not pkg:
        return {"success": False, "error": "not_installed", "reply": f"{spec['label']} ist nicht installiert."}
    if not _RE_PKG.match(pkg):
        return {"success": False, "error": "bad_package", "reply": "Ungueltiges Paket."}
    r = _adb(dev["serial"], ["shell", "am", "force-stop", pkg], 12)
    ok = bool(r.get("ok"))
    log_action({"action": "close_phone_app", "app": key, "result": "ok" if ok else "fail", "source": source})
    if not ok:
        return {"success": False, "reply": f"Ich konnte {spec['label']} nicht schliessen."}
    return {"success": True, "reply": f"{spec['label']} ist geschlossen.", "package": pkg}


def phone_nav(action: str, source: str = "voice") -> dict[str, Any]:
    act = str(action or "").lower()
    codes = {"back": ("4", "Zurueck."), "home": ("3", "Startbildschirm."), "recents": ("187", "Letzte Apps.")}
    if act not in codes:
        return {"success": False, "reply": "Diesen Navigationbefehl kenne ich nicht."}
    code, spoken = codes[act]
    r = phone_keyevent(code, source=source)
    if r.get("success"):
        r["reply"] = spoken
    return r


def set_alarm(hour: int, minute: int = 0, source: str = "voice") -> dict[str, Any]:
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        return {"success": False, "reply": "Die Uhrzeit fuer den Wecker ist ungueltig."}
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    r = _adb(dev["serial"], [
        "shell", "am", "start", "-a", "android.intent.action.SET_ALARM",
        "--ei", "android.intent.extra.alarm.HOUR", str(int(hour)),
        "--ei", "android.intent.extra.alarm.MINUTES", str(int(minute)),
        "--ez", "android.intent.extra.alarm.SKIP_UI", "false",
    ], 12)
    ok = bool(r.get("ok"))
    log_action({"action": "set_alarm", "hour": hour, "minute": minute, "result": "ok" if ok else "fail", "source": source})
    if not ok:
        return {"success": False, "reply": "Ich konnte den Wecker nicht oeffnen."}
    return {"success": True, "reply": f"Wecker auf {int(hour):02d}:{int(minute):02d} gestellt."}


def set_timer(seconds: int, source: str = "voice") -> dict[str, Any]:
    sec = int(seconds)
    if sec < 5 or sec > 24 * 3600:
        return {"success": False, "reply": "Die Timer-Dauer ist ungueltig."}
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    r = _adb(dev["serial"], [
        "shell", "am", "start", "-a", "android.intent.action.SET_TIMER",
        "--ei", "android.intent.extra.alarm.LENGTH", str(sec),
        "--ez", "android.intent.extra.alarm.SKIP_UI", "false",
    ], 12)
    ok = bool(r.get("ok"))
    mins, rem = divmod(sec, 60)
    spoken = f"Timer auf {mins} Minuten gestellt." if rem == 0 else f"Timer auf {mins} Minuten {rem} Sekunden gestellt."
    if mins == 0:
        spoken = f"Timer auf {sec} Sekunden gestellt."
    log_action({"action": "set_timer", "seconds": sec, "result": "ok" if ok else "fail", "source": source})
    if not ok:
        return {"success": False, "reply": "Ich konnte den Timer nicht oeffnen."}
    return {"success": True, "reply": spoken}


def phone_web_search(query: str, source: str = "voice") -> dict[str, Any]:
    q = re.sub(r"\s+", " ", str(query or "").strip())
    if not q:
        return {"success": False, "reply": "Wonach soll ich suchen?"}
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    url = "https://www.google.com/search?q=" + quote(q)
    r = _adb(dev["serial"], ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url], 12)
    ok = bool(r.get("ok"))
    log_action({"action": "phone_web_search", "q_len": len(q), "result": "ok" if ok else "fail", "source": source})
    if not ok:
        return {"success": False, "reply": "Ich konnte die Suche auf dem Handy nicht oeffnen."}
    return {"success": True, "reply": f"Ich suche auf dem Handy nach {q}."}


def phone_navigate(place: str, source: str = "voice") -> dict[str, Any]:
    q = re.sub(r"\s+", " ", str(place or "").strip())
    if not q:
        return {"success": False, "reply": "Wohin soll ich navigieren?"}
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    url = "geo:0,0?q=" + quote(q)
    r = _adb(dev["serial"], ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url], 12)
    ok = bool(r.get("ok"))
    if not ok:
        return {"success": False, "reply": "Ich konnte die Karten-App nicht oeffnen."}
    return {"success": True, "reply": f"Ich starte die Navigation nach {q}."}


def phone_flashlight(on: bool, source: str = "voice") -> dict[str, Any]:
    """Taschenlampe, ohne Root: cmd torch / flashlight, sonst ehrlich scheitern."""
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    serial = dev["serial"]
    want = "enable" if on else "disable"
    attempts = [
        ["shell", "cmd", "flashlight", want],
        ["shell", "cmd", "torch", want],
    ]
    ok = False
    for args in attempts:
        r = _adb(serial, args, 8)
        txt = ((r.get("out") or "") + (r.get("err") or "")).lower()
        if r.get("ok") and "error" not in txt and "unknown" not in txt and "not found" not in txt:
            ok = True
            break
    log_action({"action": "phone_flashlight", "on": on, "result": "ok" if ok else "fail", "source": source})
    if not ok:
        return {"success": False, "reply": "Die Taschenlampe kann ich auf diesem Handy ohne Root nicht schalten."}
    return {"success": True, "reply": "Taschenlampe an." if on else "Taschenlampe aus."}


def phone_call_prepare(spoken: str, source: str = "voice") -> dict[str, Any]:
    """Nur vorbereiten. Anruf startet erst nach muendlicher Bestaetigung."""
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    hit = resolve_contact(spoken, dev["serial"])
    if not hit or not hit.get("number"):
        return {"success": False, "reply": f"Ich habe keinen Kontakt fuer {spoken} gefunden."}
    name = hit.get("name") or spoken
    number = hit.get("number")
    return {"success": True, "pending": True, "name": name, "number": number,
            "reply": f"Soll ich {name} anrufen? Sag ja oder abbrechen."}


def phone_call_confirm(number: str, name: str = "", source: str = "voice") -> dict[str, Any]:
    num = re.sub(r"[^\d+]", "", str(number or ""))
    if not re.fullmatch(r"\+?\d{6,20}", num):
        return {"success": False, "reply": "Die Nummer ist ungueltig, ich rufe nicht an."}
    dev = _device_or_fail()
    if not dev.get("serial"):
        return dev
    r = _adb(dev["serial"], ["shell", "am", "start", "-a", "android.intent.action.CALL", "-d", "tel:" + num], 12)
    ok = bool(r.get("ok"))
    log_action({"action": "phone_call", "result": "ok" if ok else "fail", "source": source,
                "to": mask_recipient(name, num)})
    who = name or "die Nummer"
    if not ok:
        return {"success": False, "reply": f"Ich konnte {who} nicht anrufen."}
    return {"success": True, "reply": f"Ich rufe {who} an."}
