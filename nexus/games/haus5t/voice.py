"""HAUS5T - automatische Sprachausgabe ueber die vorhandene NEXUS-TTS.

Keine zweite Spracharchitektur: synthetisiert wird mit ``nexus.voice.tts``
(Edge-TTS mit lokalem SAPI-Fallback). Die Wiedergabe erfolgt seriell ueber
genau einen Worker. Bereits gesprochene Beitraege werden anhand eines
stabilen Schluessels dauerhaft in ``workspace/games/haus5t_voice.json``
vermerkt, damit sie nach Neustart/Fortsetzung nicht doppelt vorgelesen werden.

Steuerung:
  - ``announce_new(state)``  reiht alle noch nicht gesprochenen Beitraege ein
  - ``announce_text(key, text)`` fuer Spielstart und Abschluss-Zusammenfassung
  - ``pause()`` / ``resume()`` / ``enable()`` / ``disable()``
  - ``reset(session_id)`` fuer eine neue Spielsitzung

Fehlt TTS, wird der Beitrag ehrlich als Fehler protokolliert und der
Spielablauf laeuft ohne Warten weiter.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from nexus.common.paths import ROOT, WORKSPACE

from . import rules

GAMES_DIR = Path(ROOT) / "workspace" / "games"
VOICE_STATE_PATH = GAMES_DIR / "haus5t_voice.json"
TTS_DIR = WORKSPACE / "media" / "tts"
HISTORY_LIMIT = 400
ATTEMPTED_LIMIT = 4000
WAIT_TIMEOUT = 120.0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SynthFn = Callable[[str, Path], dict]

START_TEXT = (
    "Das Haus der fünf Türen. Ein Rollenspiel und eine Simulation, keine Therapie. "
    "Mira, Bruno und Lina sind eigenständige persistente Agenten. Eddie, du kannst "
    "jederzeit eingreifen."
)
SUMMARY_PREFIX = "Spielleitung NEXUS fasst den Spielstand zusammen: "


# ------------------------------------------------------------------ announcements
def door_number_for_round(round_no: int) -> int | None:
    """Runde 2..6 -> Tuer 1..5, sonst None."""
    try:
        value = int(round_no)
    except (TypeError, ValueError):
        return None
    return value - 1 if 2 <= value <= 6 else None


def section_label(round_no: int) -> str:
    try:
        value = int(round_no)
    except (TypeError, ValueError):
        return ""
    number = door_number_for_round(value)
    if number is not None:
        return f"Tür {number} von 5: {rules.DOORS[number - 1]}. Runde {value}."
    if value == 1:
        return "Runde 1: Ankommen."
    if value == 7:
        return "Runde 7: Abschluss."
    try:
        title = rules.round_title(value)
    except ValueError:
        title = ""
    return f"Runde {value}: {title}." if title else f"Runde {value}."


def _display_name(speaker: str) -> str:
    value = str(speaker or "NEXUS")
    return "Spielleitung NEXUS" if value == "NEXUS" else value


def build_announcement(entry: dict) -> str:
    """Baut den Vorlesetext aus einem gespeicherten Beitrag.

    Der gespeicherte Antworttext wird unveraendert uebernommen. Zusaetzlich
    werden Runde/Tuer, Sprecher und - wenn vorhanden - Einzelbewertung bzw.
    Abstimmungsergebnis angekuendigt.
    """
    speaker = str(entry.get("speaker") or "NEXUS")
    text = str(entry.get("text") or "").strip()
    if not text:
        return ""
    round_no = entry.get("round")
    parts: list[str] = []
    label = section_label(round_no) if round_no is not None else ""
    if label:
        parts.append(label)

    number = door_number_for_round(round_no) if round_no is not None else None
    score = entry.get("score")
    average = entry.get("door_average")
    if score is not None and number is not None:
        parts.append(f"Einzelbewertung von {_display_name(speaker)} für Tür {number}: {score} von 10.")
    elif average is not None and number is not None:
        count = entry.get("rating_count")
        count_text = f" aus {count} echten Einzelbewertungen" if count else ""
        parts.append(
            f"Abstimmungsergebnis für Tür {number} {rules.DOORS[number - 1]}: "
            f"Durchschnitt {average} von 10{count_text}."
        )
    else:
        parts.append(f"{_display_name(speaker)} sagt:")

    parts.append(text)
    return " ".join(part for part in parts if part).strip()


def contribution_key(entry: dict, index: int = 0) -> str:
    """Stabiler Schluessel fuer einen Beitrag (Position|round|speaker|ts|text).

    Die Position ist noetig, weil zwei Beitraege desselben Sprechers in derselben
    Sekunde (z. B. Eroeffnung und Abschluss einer Runde) sonst kollidieren.
    """
    payload = "\0".join(
        [str(index)] + [str(entry.get(key) or "") for key in ("round", "speaker", "ts", "text")]
    )
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()


# ------------------------------------------------------------------ persistence
def _now() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_voice_state() -> dict:
    return {
        "schema": "nexus.haus5t.voice.v1",
        "session_id": "",
        "enabled": True,
        "paused": False,
        "attempted": [],
        "history": [],
        "last_error": None,
        "updated": _now(),
    }


def load_voice_state(path: Path | None = None) -> dict:
    target = Path(path or VOICE_STATE_PATH)
    if not target.is_file():
        return new_voice_state()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return new_voice_state()
    if not isinstance(value, dict):
        return new_voice_state()
    base = new_voice_state()
    base.update(value)
    if not isinstance(base.get("attempted"), list):
        base["attempted"] = []
    if not isinstance(base.get("history"), list):
        base["history"] = []
    return base


def save_voice_state(state: dict, path: Path | None = None) -> None:
    target = Path(path or VOICE_STATE_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    state["updated"] = _now()
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(state, ensure_ascii=False, indent=1))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


# ------------------------------------------------------------------ audio playback
class HostPlayer:
    """Wiedergabe einer MP3 auf den Host-Lautsprechern (MCI, sonst ffplay)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._alias: str | None = None
        self._proc: subprocess.Popen | None = None
        self._stopped = False

    def _mci(self):
        import ctypes

        return ctypes.windll.winmm

    def play(self, path: Path) -> bool:
        with self._lock:
            self._stopped = False
            self._alias = None
            self._proc = None
        if os.name == "nt":
            try:
                if self._play_mci(Path(path)):
                    return True
            except Exception:
                pass
        return self._play_ffplay(Path(path))

    def _play_mci(self, path: Path) -> bool:
        import ctypes

        winmm = self._mci()
        buf = ctypes.create_unicode_buffer(256)
        alias = "nxhaus" + uuid.uuid4().hex[:8]
        if winmm.mciSendStringW(f'open "{path}" type mpegvideo alias {alias}', buf, 255, 0) != 0:
            return False
        with self._lock:
            self._alias = alias
        try:
            if winmm.mciSendStringW(f"play {alias}", buf, 255, 0) != 0:
                return False
            while True:
                with self._lock:
                    if self._stopped:
                        return False
                status = ctypes.create_unicode_buffer(256)
                winmm.mciSendStringW(f"status {alias} mode", status, 255, 0)
                mode = str(status.value or "").strip().lower()
                if mode in ("stopped", ""):
                    return True
                time.sleep(0.05)
        finally:
            try:
                winmm.mciSendStringW(f"close {alias}", buf, 255, 0)
            except Exception:
                pass
            with self._lock:
                self._alias = None

    def _ffplay_exe(self) -> str | None:
        found = shutil.which("ffplay")
        if found:
            return found
        import glob

        pattern = os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffplay.exe"
        )
        hits = sorted(glob.glob(pattern))
        return hits[-1] if hits else None

    def _play_ffplay(self, path: Path) -> bool:
        exe = self._ffplay_exe()
        if not exe:
            return False
        proc = subprocess.Popen(
            [exe, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_CREATE_NO_WINDOW,
        )
        with self._lock:
            self._proc = proc
        try:
            code = proc.wait(timeout=180)
            return code == 0
        except subprocess.TimeoutExpired:
            proc.kill()
            return False
        finally:
            with self._lock:
                self._proc = None

    def pause(self) -> bool:
        with self._lock:
            alias = self._alias
            proc = self._proc
        if alias:
            try:
                import ctypes

                self._mci().mciSendStringW(f"pause {alias}", ctypes.create_unicode_buffer(8), 8, 0)
                return True
            except Exception:
                return False
        if proc and proc.stdin:
            try:
                proc.stdin.write(b"p")
                proc.stdin.flush()
                return True
            except Exception:
                return False
        return False

    def resume(self) -> bool:
        with self._lock:
            alias = self._alias
            proc = self._proc
        if alias:
            try:
                import ctypes

                self._mci().mciSendStringW(f"resume {alias}", ctypes.create_unicode_buffer(8), 8, 0)
                return True
            except Exception:
                return False
        if proc and proc.stdin:
            try:
                proc.stdin.write(b"p")
                proc.stdin.flush()
                return True
            except Exception:
                return False
        return False

    def stop(self) -> bool:
        with self._lock:
            self._stopped = True
            alias = self._alias
            proc = self._proc
        ok = False
        if alias:
            try:
                import ctypes

                self._mci().mciSendStringW(f"stop {alias}", ctypes.create_unicode_buffer(8), 8, 0)
                ok = True
            except Exception:
                pass
        if proc:
            try:
                proc.kill()
                ok = True
            except Exception:
                pass
        return ok


# ------------------------------------------------------------------ queue
class SpeechQueue:
    """Serielle Vorlese-Warteschlange mit Pause/Fortsetzen/Aus."""

    def __init__(
        self,
        state_path: Path | None = None,
        synth: SynthFn | None = None,
        player: HostPlayer | None = None,
        text_dir: Path | None = None,
    ) -> None:
        self.state_path = Path(state_path or VOICE_STATE_PATH)
        self.text_dir = Path(text_dir or TTS_DIR)
        self.synth: SynthFn = synth or default_synthesize
        self.player = player or HostPlayer()
        self.state = load_voice_state(self.state_path)
        self._lock = threading.RLock()
        self._cond = threading.Condition(self._lock)
        self._items: list[dict] = []
        self._queued: set[str] = set()
        self._active: dict | None = None
        self._closed = False
        self._thread: threading.Thread | None = None
        self._attempted = set(str(k) for k in self.state.get("attempted") or [])

    # -- worker ---------------------------------------------------------
    def _has_worker(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._closed:
                return
            if not self._has_worker():
                self._thread = threading.Thread(
                    target=self._worker, name="haus5t-voice", daemon=True
                )
                self._thread.start()

    def _worker(self) -> None:
        while True:
            with self._cond:
                while not self._items and not self._closed:
                    self._cond.wait(0.25)
                if self._closed:
                    return
                if not self.state.get("enabled", True) or self.state.get("paused", False):
                    self._cond.wait(0.1)
                    continue
                item = self._items.pop(0)
                self._active = item
            self._speak(item)
            with self._cond:
                self._active = None
                self._cond.notify_all()

    def _speak(self, item: dict) -> None:
        text = str(item.get("announcement") or item.get("text") or "")
        path = self.text_dir / f"haus5t_{uuid.uuid4().hex}.mp3"
        result: dict = {}
        error = None
        spoken_path = path
        try:
            result = self.synth(text, path) or {}
        except BaseException as exc:  # noqa: BLE001 - TTS darf das Spiel nie stoppen
            result = {}
            error = "synthesis/" + type(exc).__name__
        if result.get("success") and result.get("path"):
            spoken_path = Path(result["path"])
        if not result.get("success"):
            error = error or str(result.get("error") or "tts_unavailable")
        playback_ok = False
        if error is None:
            # Pause/Aus kann waehrend der Synthese eintreffen: erst danach sprechen.
            with self._cond:
                while (self.state.get("enabled", True)
                       and self.state.get("paused", False)
                       and not self._closed):
                    self._cond.wait(0.1)
                if self._closed:
                    error = "closed"
                elif not self.state.get("enabled", True):
                    error = "disabled"
            if error is None:
                try:
                    playback_ok = bool(self.player.play(spoken_path))
                except BaseException as exc:  # noqa: BLE001
                    error = "playback/" + type(exc).__name__
                if not playback_ok and error is None:
                    error = "playback_failed"
        try:
            if spoken_path.exists():
                spoken_path.unlink()
        except OSError:
            pass
        self._record_attempt(item, result, error, playback_ok)

    def _record_attempt(self, item: dict, result: dict, error: str | None, playback_ok: bool) -> None:
        with self._cond:
            key = item["key"]
            self._attempted.add(key)
            self._queued.discard(key)
            self.state["attempted"] = list(self._attempted)[-ATTEMPTED_LIMIT:]
            record = {
                "key": key,
                "kind": item.get("kind") or "contribution",
                "speaker": item.get("speaker"),
                "round": item.get("round"),
                "text": item.get("text"),
                "announcement": item.get("announcement") or item.get("text"),
                "ok": bool(error is None and playback_ok),
                "error": error,
                "voice": result.get("voice") or result.get("mode"),
                "ts": _now(),
            }
            self.state.setdefault("history", []).append(record)
            self.state["history"] = self.state["history"][-HISTORY_LIMIT:]
            if error:
                self.state["last_error"] = error
            self._save()
            self._cond.notify_all()

    def _save(self) -> None:
        try:
            save_voice_state(self.state, self.state_path)
        except OSError:
            pass

    # -- enqueue --------------------------------------------------------
    def _enqueue(self, item: dict) -> bool:
        key = item["key"]
        if key in self._attempted or key in self._queued:
            return False
        self._queued.add(key)
        self._items.append(item)
        self._ensure_worker()
        self._cond.notify_all()
        return True

    def _adopt_state_session(self, state: dict) -> bool:
        """Erster Kontakt mit einer bestehenden Sitzung: Altes nicht nachtraeglich vorlesen."""
        session_id = str(state.get("session_id") or "")
        if not session_id or session_id == self.state.get("session_id"):
            return False
        if self.state.get("session_id"):
            # Andere Sitzung ohne reset(): ebenfalls nicht rueckwirkend sprechen.
            pass
        for index, entry in enumerate(state.get("contributions") or []):
            key = contribution_key(entry, index)
            if key not in self._attempted:
                self._attempted.add(key)
                self.state.setdefault("history", []).append({
                    "key": key,
                    "kind": "contribution",
                    "speaker": entry.get("speaker"),
                    "round": entry.get("round"),
                    "text": entry.get("text"),
                    "announcement": "",
                    "ok": False,
                    "error": "skipped_existing",
                    "ts": _now(),
                })
        self._attempted_list_sync()
        self.state["session_id"] = session_id
        self.state["history"] = self.state["history"][-HISTORY_LIMIT:]
        self._save()
        return True

    def _attempted_list_sync(self) -> None:
        self.state["attempted"] = list(self._attempted)[-ATTEMPTED_LIMIT:]

    def announce_new(self, state: dict) -> int:
        """Reiht alle neuen, noch nicht gesprochenen Beitraege in korrekter Reihenfolge ein."""
        with self._lock:
            if self._adopt_state_session(state):
                return 0
            added = 0
            for index, entry in enumerate(state.get("contributions") or []):
                key = contribution_key(entry, index)
                if key in self._attempted or key in self._queued:
                    continue
                announcement = build_announcement(entry)
                if not announcement:
                    self._attempted.add(key)
                    continue
                if not self.state.get("enabled", True):
                    # Ausgeschaltet: nicht spaeter nachholen, sonst doppelte Vorlesung.
                    self._attempted.add(key)
                    self.state.setdefault("history", []).append({
                        "key": key,
                        "kind": "contribution",
                        "speaker": entry.get("speaker"),
                        "round": entry.get("round"),
                        "text": entry.get("text"),
                        "announcement": announcement,
                        "ok": False,
                        "error": "disabled",
                        "ts": _now(),
                    })
                    added += 0
                    continue
                if self._enqueue({
                    "key": key,
                    "kind": "contribution",
                    "speaker": entry.get("speaker"),
                    "round": entry.get("round"),
                    "text": str(entry.get("text") or ""),
                    "announcement": announcement,
                }):
                    added += 1
            self._attempted_list_sync()
            self.state["history"] = self.state["history"][-HISTORY_LIMIT:]
            self._save()
            return added

    def announce_text(self, key: str, text: str, kind: str = "event") -> bool:
        """Reiht einen expliziten Spielleitungs-Text ein (Start, Zusammenfassung)."""
        value = str(text or "").strip()
        if not value:
            return False
        with self._lock:
            if not self.state.get("enabled", True):
                self._attempted.add(str(key))
                self._attempted_list_sync()
                self._save()
                return False
            return self._enqueue({
                "key": str(key),
                "kind": kind,
                "speaker": "NEXUS",
                "round": None,
                "text": value,
                "announcement": value,
            })

    # -- control --------------------------------------------------------
    def pause(self) -> dict:
        with self._lock:
            self.state["paused"] = True
            self._save()
            active = self._active is not None
        paused = False
        if active:
            try:
                paused = bool(self.player.pause())
            except Exception:
                paused = False
        with self._cond:
            self._cond.notify_all()
        return {"success": True, "paused": True, "current_paused": paused}

    def resume(self) -> dict:
        with self._lock:
            self.state["enabled"] = True
            self.state["paused"] = False
            self._save()
        resumed = False
        try:
            resumed = bool(self.player.resume())
        except Exception:
            resumed = False
        self._ensure_worker()
        with self._cond:
            self._cond.notify_all()
        return {"success": True, "paused": False, "current_resumed": resumed}

    def enable(self) -> dict:
        return self.resume()

    def disable(self) -> dict:
        with self._lock:
            self.state["enabled"] = False
            self.state["paused"] = False
            for item in self._items:
                self._attempted.add(item["key"])
                self.state.setdefault("history", []).append({
                    "key": item["key"],
                    "kind": item.get("kind") or "contribution",
                    "speaker": item.get("speaker"),
                    "round": item.get("round"),
                    "text": item.get("text"),
                    "announcement": item.get("announcement"),
                    "ok": False,
                    "error": "disabled",
                    "ts": _now(),
                })
            self._items.clear()
            self._queued.clear()
            self._attempted_list_sync()
            self.state["history"] = self.state["history"][-HISTORY_LIMIT:]
            self._save()
        try:
            self.player.stop()
        except Exception:
            pass
        with self._cond:
            self._cond.notify_all()
        return {"success": True, "enabled": False}

    def reset(self, session_id: str = "") -> dict:
        """Neue Spielsitzung: Warteschlange und Dedupe leeren; Ein/Aus bleibt erhalten."""
        with self._lock:
            self._items.clear()
            self._queued.clear()
            self._attempted.clear()
            self.state["attempted"] = []
            self.state["history"] = []
            self.state["last_error"] = None
            self.state["paused"] = False
            self.state["session_id"] = str(session_id or "")
            self._save()
        try:
            self.player.stop()
        except Exception:
            pass
        with self._cond:
            self._cond.notify_all()
        return {"success": True, "session_id": self.state["session_id"]}

    def wait_idle(self, timeout: float = WAIT_TIMEOUT) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._cond:
            while self._items or self._active:
                if self.state.get("paused", False) or not self.state.get("enabled", True):
                    return False
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cond.wait(min(0.2, remaining))
            return True

    def close(self, timeout: float = 5.0) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        try:
            self.player.stop()
        except Exception:
            pass
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(max(0.1, float(timeout)))

    def status(self) -> dict:
        with self._lock:
            history = list(self.state.get("history") or [])
            return {
                "enabled": bool(self.state.get("enabled", True)),
                "paused": bool(self.state.get("paused", False)),
                "queued": len(self._items),
                "active": bool(self._active),
                "spoken": sum(1 for r in history if r.get("ok")),
                "attempted": len(self._attempted),
                "last_error": self.state.get("last_error"),
                "session_id": self.state.get("session_id") or "",
                "tts_available": tts_available(),
                "last": history[-1] if history else None,
            }

    def history(self) -> list[dict]:
        with self._lock:
            return list(self.state.get("history") or [])


# ------------------------------------------------------------------ defaults
_AVAILABLE_CACHE = {"ts": 0.0, "ok": False}


def tts_available() -> bool:
    now = time.monotonic()
    if now - _AVAILABLE_CACHE["ts"] < 30:
        return bool(_AVAILABLE_CACHE["ok"])
    ok = False
    try:
        from nexus.voice import tts as tts_mod

        ok = bool(tts_mod.available())
        if not ok and os.name == "nt":
            ok = bool(tts_mod._find_ffmpeg())
    except Exception:
        ok = False
    _AVAILABLE_CACHE.update(ts=now, ok=ok)
    return ok


def default_synthesize(text: str, out_path: Path) -> dict:
    """Vorhandene NEXUS-TTS verwenden (kein eigener Syntheseweg)."""
    from nexus.voice import tts as tts_mod

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    return tts_mod.synthesize(text, Path(out_path))


_DEFAULT: SpeechQueue | None = None
_DEFAULT_LOCK = threading.Lock()


def get_queue() -> SpeechQueue:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = SpeechQueue()
        return _DEFAULT


def set_queue(queue: SpeechQueue | None) -> None:
    """Fuer Tests/Integration: Standardwarteschlange ersetzen."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        old = _DEFAULT
        _DEFAULT = queue
    if old is not None and old is not queue:
        try:
            old.close()
        except Exception:
            pass
