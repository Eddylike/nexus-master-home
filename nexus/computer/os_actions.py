"""Echte Windows-OS-Aktionen: Dateien, Browser, Apps, Audio oeffnen.

Kein Stub: webbrowser / os.startfile / Start-Process / explorer.
Auf Linux-Box: graceful Fallback (xdg-open / webbrowser), damit Smokes laufen.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

VERSION = "OS.1.3"

# Bekannte Apps: cmds zuerst, sonst urls / URI-Schemes
APP_MAP: dict[str, dict[str, list[str]]] = {
    "chrome": {
        "cmds": [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
        ],
        "urls": [],
    },
    "edge": {
        "cmds": [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ],
        "urls": [],
    },
    "firefox": {
        "cmds": [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"%LOCALAPPDATA%\Mozilla Firefox\firefox.exe",
        ],
        "urls": [],
    },
    "notepad": {"cmds": ["notepad.exe"], "urls": []},
    "rechner": {"cmds": ["calc.exe"], "urls": []},
    "calc": {"cmds": ["calc.exe"], "urls": []},
    "explorer": {"cmds": ["explorer.exe"], "urls": []},
    "cmd": {"cmds": ["cmd.exe"], "urls": []},
    "powershell": {"cmds": ["powershell.exe"], "urls": []},
    "spotify": {
        "cmds": [
            r"%APPDATA%\Spotify\Spotify.exe",
            r"%LOCALAPPDATA%\Spotify\Spotify.exe",
            r"%LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe",
        ],
        "urls": ["spotify:"],
    },
    "teams": {
        "cmds": [r"%LOCALAPPDATA%\Microsoft\WindowsApps\ms-teams.exe"],
        "urls": ["msteams:"],
    },
    "telegram": {
        "cmds": [r"%APPDATA%\Telegram Desktop\Telegram.exe"],
        "urls": ["https://web.telegram.org/"],
    },
    "vlc": {
        "cmds": [
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        ],
        "urls": [],
    },
    "wmplayer": {"cmds": ["wmplayer.exe"], "urls": []},
    "groove": {"cmds": [], "urls": ["mswindowsmusic:"]},
    "musik": {"cmds": [], "urls": ["mswindowsmusic:"]},
    "einstellungen": {"cmds": [], "urls": ["ms-settings:"]},
    "settings": {"cmds": [], "urls": ["ms-settings:"]},
    "fotos": {"cmds": [], "urls": ["ms-photos:"]},
    "kamera": {"cmds": [], "urls": ["microsoft.windows.camera:"]},
    "brave": {
        "cmds": [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
        ],
        "urls": [],
    },
    "discord": {
        "cmds": [
            r"%LOCALAPPDATA%\Discord\Update.exe --processStart Discord.exe",
        ],
        "urls": ["discord://", "https://discord.com/app"],
    },
    "steam": {
        "cmds": [
            r"C:\Program Files (x86)\Steam\steam.exe",
            r"C:\Program Files\Steam\steam.exe",
        ],
        "urls": ["steam://"],
    },
    "code": {
        "cmds": [
            r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ],
        "urls": [],
    },
    "vscode": {
        "cmds": [
            r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
            r"C:\Program Files\Microsoft VS Code\Code.exe",
        ],
        "urls": [],
    },
    "cursor": {
        "cmds": [
            r"%LOCALAPPDATA%\Programs\cursor\Cursor.exe",
            r"%LOCALAPPDATA%\Programs\Cursor\Cursor.exe",
        ],
        "urls": [],
    },
    "youtube": {"cmds": [], "urls": ["https://www.youtube.com/"]},
    "gmail": {"cmds": [], "urls": ["https://mail.google.com/"]},
    "kalender": {"cmds": [], "urls": ["https://calendar.google.com/"]},
    "calendar": {"cmds": [], "urls": ["https://calendar.google.com/"]},
    "drive": {"cmds": [], "urls": ["https://drive.google.com/"]},
    "maps": {"cmds": [], "urls": ["https://maps.google.com/"]},
    "facebook": {"cmds": [], "urls": ["https://www.facebook.com/"]},
    "whatsapp": {
        "cmds": [
            r"shell:AppsFolder\5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
            r"%LOCALAPPDATA%\WhatsApp\WhatsApp.exe",
            r"%LOCALAPPDATA%\Programs\WhatsApp\WhatsApp.exe",
            r"%LOCALAPPDATA%\Microsoft\WindowsApps\WhatsApp.exe",
        ],
        "urls": ["whatsapp:", "https://web.whatsapp.com/"],
        "verify_procs": ["WhatsApp.Root", "WhatsApp", "WhatsAppDesktop"],
        "aumid": "5319275A.WhatsAppDesktop_cv1g1gvanyjgm!App",
    },
    "tiktok": {"cmds": [], "urls": ["https://www.tiktok.com/"]},
    "outlook": {
        "cmds": [r"shell:AppsFolder\Microsoft.OutlookForWindows_8wekyb3d8bbwe!Microsoft.OutlookforWindows"],
        "urls": ["https://mail.google.com/"],
        "verify_procs": ["olk"],
    },
}

ALIASES = {
    "yt": "youtube",
    "you tube": "youtube",
    "google chrome": "chrome",
    "vs code": "code",
    "visual studio code": "code",
    "brave browser": "brave",

    "google-chrome": "chrome",
    "msedge": "edge",
    "microsoft edge": "edge",
    "notizblock": "notepad",
    "editor": "notepad",
    "taschenrechner": "rechner",
    "dateien": "explorer",
    "ordner": "explorer",
    "windows explorer": "explorer",
    "media player": "wmplayer",
    "windows media player": "wmplayer",
    "musik player": "musik",
    "music": "musik",
    "mail": "gmail",
    "email": "gmail",
    "e-mail": "gmail",
    "termine": "kalender",
    "google maps": "maps",
    "karten": "maps",
    "fb": "facebook",
    "wa": "whatsapp",
    "whats app": "whatsapp",
    "whats-app": "whatsapp",
    "whatsapp desktop": "whatsapp",
    "watsapp": "whatsapp",
    "whatapp": "whatsapp",
    "tg": "telegram",
}

_AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".opus"}
_BLOCK_SCHEME = re.compile(r"^(javascript|data|vbscript|file):", re.I)

_BROWSER_PROCS = ("chrome", "msedge", "brave", "firefox", "chromium")
_MAX_OPENED = 30


def _host_of(url: str) -> str:
    """Hostname aus URL (ohne www.)."""
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", u):
        u = "https://" + u
    try:
        host = (urlparse(u).hostname or "").lower()
    except Exception:
        host = ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_win() -> bool:
    return platform.system() == "Windows" or os.name == "nt"


def _expand(p: str) -> str:
    return os.path.expandvars(os.path.expanduser(p or ""))


def _ok(action: str, **extra: Any) -> dict:
    return {"success": True, "action": action, "version": VERSION, **extra}


def _fail(action: str, error: str, **extra: Any) -> dict:
    return {"success": False, "action": action, "error": error, "version": VERSION, **extra}


def _suggest_apps(name: str, n: int = 3) -> list[str]:
    import difflib
    keys = sorted(set(APP_MAP) | set(ALIASES))
    return difflib.get_close_matches((name or "").strip().lower(), keys, n=n, cutoff=0.6)


def _not_found_reply(name: str) -> str:
    s = _suggest_apps(name)
    return f"App {name} nicht gefunden." + (" Meintest du: " + ", ".join(s) + "?" if s else "")


class OSActions:
    """Oeffnet URLs, Pfade und Apps auf dem Host-OS."""

    def __init__(self) -> None:
        # Von NEXUS geoeffnete URLs/Hosts (Reihenfolge = zuletzt zuerst ans Ende)
        self._opened_urls: list[str] = []

    def _track_opened(self, url: str) -> None:
        u = (url or "").strip()
        if not u:
            return
        self._opened_urls = [x for x in self._opened_urls if x != u]
        self._opened_urls.append(u)
        if len(self._opened_urls) > _MAX_OPENED:
            self._opened_urls = self._opened_urls[-_MAX_OPENED:]

    def open_url(self, url: str) -> dict:
        url = (url or "").strip()
        if not url:
            return _fail("open_url", "keine_url", reply="Keine URL angegeben.")
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
            url = "https://" + url
        if _BLOCK_SCHEME.match(url) and not url.lower().startswith("file:"):
            # file: erlauben wir nur ueber open_path
            if not url.lower().startswith(("http://", "https://", "spotify:", "ms-", "mailto:")):
                return _fail("open_url", "scheme_blocked", reply=f"URL-Schema nicht erlaubt: {url}")
        try:
            if _is_win():
                # Ohne Konsole: "cmd /c start" würde "&", "|" usw. in der URL als weitere Befehle ausführen
                # (Befehlsinjektion). os.startfile übergibt die URL direkt an die Windows-Shell-Zuordnung.
                os.startfile(url)  # type: ignore[attr-defined]
                method = "shell-startfile"
            else:
                webbrowser.open(url, new=2)
                method = "webbrowser"
            self._track_opened(url)
            return _ok(
                "open_url",
                target=url,
                method=method,
                reply=f"Website geoeffnet: {url}",
            )
        except Exception as e:
            try:
                webbrowser.open(url, new=2)
                self._track_opened(url)
                return _ok("open_url", target=url, method="webbrowser-fallback", reply=f"Website geoeffnet: {url}")
            except Exception as e2:
                return _fail("open_url", f"{e}; {e2}", target=url, reply=f"Konnte URL nicht oeffnen: {e2}")

    def open_path(self, path: str, *, reveal: bool = False) -> dict:
        """Datei oder Ordner mit Standard-App / Explorer oeffnen."""
        raw = (path or "").strip().strip('"')
        if not raw:
            return _fail("open_path", "kein_pfad", reply="Kein Pfad angegeben.")
        p = Path(_expand(raw))
        try:
            p = p.resolve(strict=False)
        except Exception:
            p = Path(_expand(raw))

        if not p.exists():
            return _fail(
                "open_path",
                "not_found",
                target=str(p),
                reply=f"Pfad nicht gefunden: {p}",
            )

        try:
            if _is_win():
                if reveal or p.is_dir():
                    # Ordner im Explorer
                    subprocess.Popen(
                        ["explorer.exe", str(p)],
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    method = "explorer"
                else:
                    os.startfile(str(p))  # type: ignore[attr-defined]
                    method = "startfile"
            else:
                opener = shutil.which("xdg-open") or shutil.which("open")
                if opener:
                    subprocess.Popen([opener, str(p)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    method = opener
                else:
                    webbrowser.open(p.as_uri())
                    method = "webbrowser"
            kind = "ordner" if p.is_dir() else "datei"
            return _ok(
                "open_path",
                target=str(p),
                kind=kind,
                method=method,
                reply=f"{kind.capitalize()} geoeffnet: {p}",
            )
        except Exception as e:
            return _fail("open_path", str(e), target=str(p), reply=f"Konnte Pfad nicht oeffnen: {e}")

    def launch_app(self, name: str) -> dict:
        # 2026-10-28 voiceyt: Ortszusatz aus Sprachbefehl entfernen ("youtube am pc" -> "youtube")
        for _ in range(2): name = re.sub(r"\s+(?:(?:am|auf\s+(?:dem|meinem)?|im)\s*(?:pc|rechner|computer|laptop|desktop)|bitte|mal)\s*[.!?]*$", "", (name or "").strip(), flags=re.I).strip()
        key = (name or "").strip().lower()
        key = re.sub(r"\s+", " ", key)
        key = key.strip(" .!?,;:")
        key = re.sub(r"\bwhats\s*[-_]?\s*app\b", "whatsapp", key)
        key = key.replace("whats-app", "whatsapp")
        if not key:
            return _fail("launch_app", "kein_name", reply="Keine App angegeben.")
        key = ALIASES.get(key, key)
        key = ALIASES.get(key.replace(" ", ""), key)  # "whats app" already normalized
        spec = APP_MAP.get(key)
        if not spec and key.replace(" ", "") in APP_MAP:
            key = key.replace(" ", "")
            spec = APP_MAP.get(key)

        opened: list[dict] = []
        if not spec:
            # Direkter Executable-Pfad oder Name
            expanded = _expand(name)
            if Path(expanded).exists() or expanded.lower().endswith((".exe", ".bat", ".cmd", ".lnk")):
                r = self._start_cmd(expanded)
                opened.append(r)
            else:
                # Windows: Start-Process -Name / shell open
                r = self._start_by_name(name)
                opened.append(r)
            ok = any(x.get("ok") for x in opened)
            return {
                "success": ok,
                "action": "launch_app",
                "app": name,
                "opened": opened,
                "version": VERSION,
                "reply": (f"App gestartet: {name}" if ok else _not_found_reply(name)),
                **({} if ok else {"error": "APP_NOT_FOUND", "suggestions": _suggest_apps(name)}),
            }

        for c in spec.get("cmds") or []:
            # shell:AppsFolder\… AUMID — via explorer.exe
            if str(c).lower().startswith("shell:appsfolder"):
                r = self._start_aumid(c.split("\\", 1)[-1] if "\\" in c else c.replace("shell:AppsFolder\\", "").replace("shell:AppsFolder/", ""))
                # c may be full shell:AppsFolder\AUMID
                if "shell:AppsFolder" in c:
                    aumid = c.split("shell:AppsFolder\\")[-1].split("shell:AppsFolder/")[-1]
                    r = self._start_aumid(aumid)
                opened.append(r)
            else:
                r = self._start_cmd(_expand(c))
                opened.append(r)
            if r.get("ok"):
                break
        else:
            # Store-AUMID fallback if declared
            aumid = spec.get("aumid")
            if aumid:
                r = self._start_aumid(str(aumid))
                opened.append(r)
            if not any(x.get("ok") for x in opened):
                for u in spec.get("urls") or []:
                    r = self.open_url(u)
                    opened.append({"ok": r.get("success"), **r})
                    if r.get("success"):
                        break

        ok = any(x.get("ok") or x.get("success") for x in opened)
        verified = None
        if ok and spec.get("verify_procs"):
            verified = self._verify_process(list(spec.get("verify_procs") or []), wait_s=5.0)
            if not verified.get("running") and not any(
                (x.get("target") or "").startswith("http") and x.get("ok") for x in opened
            ):
                # Native failed verify — web fallback
                r = self.open_url("https://web.whatsapp.com/" if key == "whatsapp" else (spec.get("urls") or ["about:blank"])[0])
                opened.append({"ok": r.get("success"), **r, "fallback": "web"})
                ok = ok or bool(r.get("success"))
        return {
            "success": ok,
            "action": "launch_app",
            "app": key,
            "opened": opened,
            "verified": verified,
            "version": VERSION,
            "reply": (f"App geoeffnet: {key}" if ok else f"Konnte '{key}' nicht oeffnen."),
        }

    def play_media(self, path: str | None = None) -> dict:
        """Audio/Video-Datei mit Standardplayer oeffnen, oder Groove/Spotify starten."""
        if path:
            p = Path(_expand(path.strip().strip('"')))
            if not p.exists():
                return _fail("play_media", "not_found", target=str(p), reply=f"Mediendatei fehlt: {p}")
            r = self.open_path(str(p))
            r["action"] = "play_media"
            if r.get("success"):
                r["reply"] = f"Wiedergabe gestartet: {p.name}"
            return r

        # Ohne Pfad: Windows Media / Groove versuchen, sonst Spotify
        for cand in ("wmplayer", "musik", "spotify"):
            r = self.launch_app(cand)
            if r.get("success"):
                r["action"] = "play_media"
                r["reply"] = f"Musik-App gestartet: {cand}"
                return r
        return _fail(
            "play_media",
            "no_player",
            reply="Kein Mediaplayer gefunden. Lege MP3 unter workspace/media oder sag 'oeffne spotify'.",
        )

    def list_dir_os(self, path: str) -> dict:
        """Verzeichnis auflisten (auch ausserhalb NEXUS-Sandbox, nur Lesen)."""
        raw = (path or "").strip().strip('"') or ("." if not _is_win() else str(Path.home()))
        p = Path(_expand(raw))
        try:
            p = p.resolve(strict=False)
        except Exception:
            pass
        if not p.exists() or not p.is_dir():
            return _fail("list_dir_os", "not_dir", target=str(p), reply=f"Kein Ordner: {p}", items=[])
        items = []
        try:
            for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                try:
                    items.append(
                        {
                            "name": child.name,
                            "is_dir": child.is_dir(),
                            "size": child.stat().st_size if child.is_file() else 0,
                        }
                    )
                except OSError:
                    items.append({"name": child.name, "is_dir": child.is_dir(), "size": 0})
                if len(items) >= 200:
                    break
        except Exception as e:
            return _fail("list_dir_os", str(e), target=str(p), items=[], reply=str(e))
        return _ok(
            "list_dir_os",
            target=str(p),
            items=items,
            count=len(items),
            reply=f"{len(items)} Eintraege in {p}",
        )


    def close_app(self, name: str) -> dict:
        """App-Prozesse beenden (nicht Explorer-Shell, nicht Systemkritisch)."""
        key = (name or "").strip().lower()
        if not key:
            return _fail("close_app", "kein_name", reply="Keine App angegeben.")
        key = ALIASES.get(key, key) if "ALIASES" in globals() else key
        # Alias-Map lokal falls ALIASES fehlt
        aliases = {
            "google chrome": "chrome",
            "chromium": "chrome",
            "msedge": "edge",
            "microsoft edge": "edge",
            "calculator": "calc",
            "rechner": "calc",
            "notizblock": "notepad",
            "dateien": "explorer",
        }
        key = aliases.get(key, key)

        proc_map = {
            "chrome": ["chrome.exe"],
            "edge": ["msedge.exe"],
            "brave": ["brave.exe"],
            "firefox": ["firefox.exe"],
            "notepad": ["notepad.exe"],
            "calc": ["CalculatorApp.exe", "calc.exe"],
            "spotify": ["Spotify.exe"],
            "vlc": ["vlc.exe"],
            "telegram": ["Telegram.exe"],
            "discord": ["Discord.exe"],
            "steam": ["steam.exe"],
            "code": ["Code.exe"],
            "teams": ["ms-teams.exe", "Teams.exe"],
            "wmplayer": ["wmplayer.exe"],
        }
        # Nie die Explorer-Shell killen (Desktop stirbt)
        if key in ("explorer", "cmd", "powershell"):
            return _fail(
                "close_app",
                "protected",
                app=key,
                reply=f"'{key}' wird nicht per Kill geschlossen (System/Shell geschuetzt).",
            )

        names = proc_map.get(key)
        if not names:
            # Fallback: Name.exe
            cand = key if key.endswith(".exe") else f"{key}.exe"
            names = [cand]

        if not _is_win():
            killed = []
            for n in names:
                base = n[:-4] if n.lower().endswith(".exe") else n
                try:
                    subprocess.run(["pkill", "-f", base], check=False, capture_output=True, stdin=subprocess.DEVNULL)
                    killed.append(base)
                except Exception:
                    pass
            return _ok("close_app", app=key, killed=killed, method="pkill", reply=f"Close versucht: {key}")

        closed = []
        errors = []
        for image in names:
            try:
                # taskkill nur Image-Name — sicherheitshalber /FI
                r = subprocess.run(
                    ["taskkill", "/IM", image, "/F"],
                    capture_output=True,
                    text=True,
                    timeout=15, stdin=subprocess.DEVNULL,
                )
                out = (r.stdout or "") + (r.stderr or "")
                if r.returncode == 0 or "ERFOLG" in out.upper() or "SUCCESS" in out.upper():
                    closed.append({"image": image, "ok": True, "out": out.strip()[:200]})
                else:
                    # 128 = process not found — kein harter Fehler
                    if "nicht gefunden" in out.lower() or "not found" in out.lower() or r.returncode == 128:
                        closed.append({"image": image, "ok": False, "missing": True, "out": out.strip()[:200]})
                    else:
                        errors.append({"image": image, "code": r.returncode, "out": out.strip()[:200]})
            except Exception as e:
                errors.append({"image": image, "error": str(e)})

        ok = any(c.get("ok") for c in closed)
        if ok:
            return _ok(
                "close_app",
                app=key,
                closed=closed,
                errors=errors,
                version=VERSION,
                reply=f"App geschlossen: {key}",
            )
        if all(c.get("missing") for c in closed) and closed and not errors:
            return _ok(
                "close_app",
                app=key,
                closed=closed,
                already_closed=True,
                reply=f"App lief nicht: {key}",
            )
        return _fail(
            "close_app",
            "close_failed",
            app=key,
            closed=closed,
            errors=errors,
            reply=f"Konnte '{key}' nicht schliessen.",
        )

    def close_pid(self, pid: int | str) -> dict:
        """Einzelnen Prozess per PID beenden."""
        try:
            pid_i = int(pid)
        except (TypeError, ValueError):
            return _fail("close_pid", "bad_pid", reply="Ungueltige PID.")
        if pid_i <= 0:
            return _fail("close_pid", "bad_pid", reply="Ungueltige PID.")
        # Schutz: eigenes Nexus nicht killen
        if pid_i == os.getpid():
            return _fail("close_pid", "protected", pid=pid_i, reply="Eigenen Nexus-Prozess nicht beenden.")
        if not _is_win():
            try:
                os.kill(pid_i, 15)
                return _ok("close_pid", pid=pid_i, method="SIGTERM", reply=f"PID {pid_i} beendet.")
            except Exception as e:
                return _fail("close_pid", str(e), pid=pid_i, reply=f"PID {pid_i} fehlgeschlagen: {e}")
        try:
            r = subprocess.run(
                ["taskkill", "/PID", str(pid_i), "/F"],
                capture_output=True,
                text=True,
                timeout=15, stdin=subprocess.DEVNULL)
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            if r.returncode == 0:
                return _ok("close_pid", pid=pid_i, out=out[:200], reply=f"PID {pid_i} beendet.")
            return _fail("close_pid", f"code_{r.returncode}", pid=pid_i, out=out[:200], reply=f"PID {pid_i} nicht beendet.")
        except Exception as e:
            return _fail("close_pid", str(e), pid=pid_i, reply=str(e))


    def close_opened(self, url: str | None = None) -> dict:
        """Schliesst Browser-Fenster, die NEXUS zuvor geoeffnet hat (WM_CLOSE, kein Kill).

        url gesetzt → nur dieser Host. Sonst alle getrackten Hosts.
        Linux: graceful no-op.
        """
        import json as _json

        action = "close_opened"
        target_url = (url or "").strip() or None
        hosts: list[str] = []
        if target_url:
            h = _host_of(target_url)
            if h:
                hosts = [h]
            else:
                return _fail(
                    action,
                    "kein_host",
                    reply="Keine gueltige URL/Host zum Schliessen.",
                    url=target_url,
                )
        else:
            seen: set[str] = set()
            for u in reversed(self._opened_urls):
                h = _host_of(u)
                if h and h not in seen:
                    seen.add(h)
                    hosts.append(h)
            if not hosts:
                return _fail(
                    action,
                    "keine_tracked",
                    reply="Keine von NEXUS geoeffneten Seiten bekannt.",
                    tracked=list(self._opened_urls),
                )

        if not _is_win():
            return _fail(
                action,
                "os_unsupported",
                reply="Seiten-Schliessen (OS) nur unter Windows; UI-Embed ggf. separat zu.",
                hosts=hosts,
                tracked=list(self._opened_urls),
            )

        host_literals = ", ".join("'" + h.replace("'", "''") + "'" for h in hosts)
        browser_literals = ", ".join("'" + b + "'" for b in _BROWSER_PROCS)
        ps = (
            "$ErrorActionPreference = 'SilentlyContinue'\n"
            "Add-Type @\"\n"
            "using System;\n"
            "using System.Text;\n"
            "using System.Runtime.InteropServices;\n"
            "public class NexusWinClose {\n"
            "  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);\n"
            "  [DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumProc cb, IntPtr lp);\n"
            "  [DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr hWnd);\n"
            "  [DllImport(\"user32.dll\", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder sb, int max);\n"
            "  [DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);\n"
            "  [DllImport(\"user32.dll\")] public static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);\n"
            "  public const uint WM_CLOSE = 0x0010;\n"
            "}\n"
            "\"@\n"
            f"$needles = @({host_literals})\n"
            f"$browsers = @({browser_literals})\n"
            "$closed = New-Object System.Collections.Generic.List[string]\n"
            "$cb = [NexusWinClose+EnumProc]{\n"
            "  param([IntPtr]$h, [IntPtr]$l)\n"
            "  if (-not [NexusWinClose]::IsWindowVisible($h)) { return $true }\n"
            "  $sb = New-Object System.Text.StringBuilder 512\n"
            "  [void][NexusWinClose]::GetWindowText($h, $sb, $sb.Capacity)\n"
            "  $title = $sb.ToString()\n"
            "  if ([string]::IsNullOrWhiteSpace($title)) { return $true }\n"
            "  $procId = [uint32]0\n"
            "  [void][NexusWinClose]::GetWindowThreadProcessId($h, [ref]$procId)\n"
            "  try { $pname = (Get-Process -Id $procId -ErrorAction Stop).ProcessName.ToLower() } catch { return $true }\n"
            "  if ($browsers -notcontains $pname) { return $true }\n"
            "  $titleLow = $title.ToLower()\n"
            "  foreach ($n in $needles) {\n"
            "    if ($titleLow.Contains($n.ToLower())) {\n"
            "      [void][NexusWinClose]::PostMessage($h, [NexusWinClose]::WM_CLOSE, [IntPtr]::Zero, [IntPtr]::Zero)\n"
            "      $closed.Add(\"$pname :: $title\")\n"
            "      break\n"
            "    }\n"
            "  }\n"
            "  return $true\n"
            "}\n"
            "[void][NexusWinClose]::EnumWindows($cb, [IntPtr]::Zero)\n"
            "$closed | ConvertTo-Json -Compress\n"
        )
        try:
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    ps,
                ],
                capture_output=True,
                text=True,
                timeout=20, stdin=subprocess.DEVNULL)
            out = (proc.stdout or "").strip()
            closed_titles: list[str] = []
            if out:
                try:
                    parsed = _json.loads(out)
                    if isinstance(parsed, list):
                        closed_titles = [str(x) for x in parsed]
                    elif isinstance(parsed, str):
                        closed_titles = [parsed]
                except Exception:
                    closed_titles = [ln.strip() for ln in out.splitlines() if ln.strip()]
            host_set = set(hosts)
            self._opened_urls = [u for u in self._opened_urls if _host_of(u) not in host_set]
            if closed_titles:
                return _ok(
                    action,
                    hosts=hosts,
                    closed=closed_titles,
                    count=len(closed_titles),
                    reply=f"{len(closed_titles)} Browser-Fenster geschlossen ({', '.join(hosts)}).",
                )
            return _fail(
                action,
                "kein_fenster",
                hosts=hosts,
                closed=[],
                reply=f"Kein passendes Browser-Fenster gefunden fuer: {', '.join(hosts)}.",
            )
        except Exception as e:
            return _fail(action, str(e), hosts=hosts, reply=f"Schliessen fehlgeschlagen: {e}")



    def _start_aumid(self, aumid: str) -> dict:
        """Start Windows Store / AppsFolder app by AUMID."""
        aumid = (aumid or "").strip()
        if aumid.lower().startswith("shell:appsfolder\\"):
            aumid = aumid.split("\\", 1)[-1]
        if aumid.lower().startswith("shell:appsfolder/"):
            aumid = aumid.split("/", 1)[-1]
        if not aumid:
            return {"ok": False, "error": "empty_aumid"}
        if not _is_win():
            return {"ok": False, "error": "aumid_windows_only"}
        try:
            subprocess.Popen(
                ["explorer.exe", "shell:AppsFolder\\" + aumid],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return {"ok": True, "method": "AppsFolder-AUMID", "target": aumid}
        except Exception as e:
            try:
                safe = aumid.replace("'", "''")
                subprocess.Popen(
                    [
                        "powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "Start-Process 'explorer.exe' 'shell:AppsFolder\\" + safe + "'",
                    ],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"ok": True, "method": "AppsFolder-PS", "target": aumid}
            except Exception as e2:
                return {"ok": False, "error": f"{e}; {e2}", "target": aumid}

    def _verify_process(self, names: list[str], wait_s: float = 2.5) -> dict:
        """Check whether any process name is running (best-effort)."""
        import time
        time.sleep(max(0.2, min(wait_s, 8.0)))
        found = []
        if _is_win():
            try:
                r = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
                )
                blob = (r.stdout or "").lower()
                for n in names:
                    nl = n.lower()
                    if nl in blob or (nl + ".exe") in blob:
                        found.append(n)
            except Exception as e:
                return {"running": False, "error": str(e), "found": []}
        else:
            try:
                r = subprocess.run(["ps", "ax", "-o", "comm"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
                blob = (r.stdout or "").lower()
                for n in names:
                    if n.lower() in blob:
                        found.append(n)
            except Exception as e:
                return {"running": False, "error": str(e), "found": []}
        return {"running": bool(found), "found": found}

    def volume(self, action: str = "up", steps: int = 4) -> dict:
        """Windows media keys: up / down / mute / unmute via keybd_event."""
        act = (action or "up").strip().lower()
        try:
            steps_i = max(1, min(int(steps or 4), 20))
        except (TypeError, ValueError):
            steps_i = 4
        if not _is_win():
            return _fail("volume", "os_unsupported", reply="BLOCKED: Lautstaerke-Keys nur unter Windows.")
        vk = {"up": 0xAF, "down": 0xAE, "mute": 0xAD, "unmute": 0xAD}.get(act)
        if vk is None:
            return _fail("volume", "bad_action", reply=f"Unbekannte Volume-Aktion: {act}")
        try:
            import ctypes
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            KEYEVENTF_EXTENDEDKEY = 0x0001
            KEYEVENTF_KEYUP = 0x0002
            n = 1 if act in ("mute", "unmute") else steps_i
            for _ in range(n):
                user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
                user32.keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)
            return _ok(
                "volume",
                volume_action=act,
                steps=n,
                reply=f"Lautstaerke: {act}" + (f" x{n}" if n > 1 else ""),
            )
        except Exception as e:
            return _fail("volume", str(e), reply=f"Lautstaerke fehlgeschlagen: {e}")

    def _start_cmd(self, cmd: str) -> dict:
        raw = (cmd or "").strip()
        if raw.lower().startswith("shell:appsfolder"):
            aumid = raw.split("\\", 1)[-1] if "\\" in raw else raw.split("/", 1)[-1]
            return self._start_aumid(aumid)
        # "path\exe --args"
        if " --" in raw and not raw.lower().startswith("http"):
            exe_part, _, arg_part = raw.partition(" --")
            exe = _expand(exe_part.strip().strip('"'))
            args = ("--" + arg_part).split()
            if Path(exe).exists():
                try:
                    subprocess.Popen([exe, *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return {"ok": True, "method": "popen-args", "target": exe}
                except Exception as e:
                    return {"ok": False, "error": str(e), "target": exe}
        path = _expand(raw)
        p = Path(path)
        try:
            if p.exists():
                if _is_win():
                    os.startfile(str(p))  # type: ignore[attr-defined]
                else:
                    subprocess.Popen([str(p)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"ok": True, "method": "startfile", "target": str(p)}
            # Pfad mit Separator/Drive aber fehlt -> kein Fake-OK
            if ("\\" in path) or ("/" in path) or re.match(r"^[A-Za-z]:", path):
                return {"ok": False, "error": "not_found", "target": path}
            which = shutil.which(path)
            if not which and _is_win() and not path.lower().endswith(".exe"):
                which = shutil.which(path + ".exe")
            if which:
                if _is_win():
                    subprocess.Popen(
                        ["cmd", "/c", "start", "", which],
                        shell=False,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen([which], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"ok": True, "method": "which", "target": which}
            if _is_win() and not any(sep in path for sep in ("\\", "/", ":")):
                subprocess.Popen(
                    ["cmd", "/c", "start", "", path],
                    shell=False,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return {"ok": True, "method": "cmd-start", "target": path}
            return {"ok": False, "error": "not_found", "target": path}
        except Exception as e:
            return {"ok": False, "error": str(e), "target": path}

    def _start_by_name(self, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            return {"ok": False, "error": "empty"}
        try:
            if _is_win():
                # PowerShell Start-Process — sicherer als shell=True
                safe = name.replace("'", "''")
                # 2026-10-28 nofake: Start-Process synchron pruefen (kehrt nach Start sofort zurueck);
                # Exit!=0 = Windows kennt das Programm nicht -> ehrlicher Fehler statt "gestartet".
                cp = subprocess.run(
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        f"try {{ Start-Process -FilePath '{safe}' -ErrorAction Stop; exit 0 }} catch {{ exit 3 }}",
                    ],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, timeout=20,
                )
                if cp.returncode != 0:
                    return {"ok": False, "error": "not_found", "method": "Start-Process", "target": name}
                return {"ok": True, "method": "Start-Process", "target": name}
            which = shutil.which(name)
            if which:
                subprocess.Popen([which], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"ok": True, "method": "which", "target": which}
            return {"ok": False, "error": "not_found", "target": name}
        except Exception as e:
            return {"ok": False, "error": str(e), "target": name}


_singleton: OSActions | None = None


def get_os_actions() -> OSActions:
    global _singleton
    if _singleton is None:
        _singleton = OSActions()
    return _singleton
