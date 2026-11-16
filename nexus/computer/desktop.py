"""Real desktop control for the Claude agent (Windows, Linux/X11, Linux/Wayland).

Input actions are delivered through pyautogui. Irreversible input (click, type,
hotkey) requires a local terminal approval by default. Screenshots and pointer
movement are audited but do not need an approval. Every result states what was
actually observed; no effect is claimed without evidence.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from nexus.common.paths import IS_LINUX, IS_WINDOWS, SCREENS_DIR, ensure_dirs

AGENT = "Claude"
_MOD = None
_MOD_ERROR: str | None = None


def _approval_required() -> bool:
    return (os.environ.get("NEXUS_CLAUDE_DESKTOP_APPROVAL", "1") or "1").strip() != "0"


def _display_ready() -> tuple[bool, str]:
    if IS_WINDOWS or os.name == "nt":
        return True, "windows_desktop"
    if IS_LINUX:
        if os.environ.get("WAYLAND_DISPLAY"):
            return True, "wayland"
        if os.environ.get("DISPLAY"):
            return True, "x11"
        return False, "display_unavailable"
    if os.name == "posix":
        return True, "posix_desktop"
    return False, "unsupported_platform"


def _pyautogui():
    global _MOD, _MOD_ERROR
    if _MOD is not None or _MOD_ERROR is not None:
        return _MOD, _MOD_ERROR
    ok, why = _display_ready()
    if not ok:
        _MOD_ERROR = why
        return None, why
    try:
        import pyautogui  # type: ignore
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.03
        _MOD = pyautogui
        return _MOD, None
    except Exception as exc:
        _MOD_ERROR = "pyautogui_unavailable:" + type(exc).__name__
        return None, _MOD_ERROR


def capabilities() -> dict[str, Any]:
    ok, display = _display_ready()
    mod, why = _pyautogui()
    clip = None
    try:
        import pyperclip  # type: ignore
        clip = True
    except Exception:
        clip = False
    screenshot_backend = None
    if mod is not None:
        screenshot_backend = "pyautogui"
    elif ok:
        screenshot_backend = next((name for name in ("grim", "scrot", "gnome-screenshot",
                                                     "spectacle", "import") if shutil.which(name)), None)
    return {
        "success": True,
        "platform": "windows" if IS_WINDOWS else ("linux" if IS_LINUX else os.name),
        "display": display,
        "display_ready": ok,
        "input": mod is not None,
        "input_error": why,
        "screenshot_backend": screenshot_backend,
        "clipboard": clip,
        "approval_required": _approval_required(),
        "screens_dir": str(SCREENS_DIR),
    }


def _audit(action: str, **fields: Any) -> None:
    try:
        from nexus.terminal import guard
        guard.audit("desktop_" + action, agent=AGENT, **fields)
    except Exception:
        pass


def _gate(action: str, params: dict[str, Any], approval_id: str | None,
          *, sensitive: bool) -> dict[str, Any] | None:
    """Return a blocking result when the action needs an approval, else None."""
    try:
        from nexus.terminal import guard
    except Exception as exc:
        return {"success": False, "error": "desktop_guard_unavailable",
                "message": type(exc).__name__, "action": action, "verified": False}
    label = "desktop." + action + "(" + ",".join(f"{k}={params[k]}" for k in sorted(params)) + ")"
    force = bool(sensitive and _approval_required())
    verdict = guard.authorize(AGENT, label, shell="session", approval_id=approval_id,
                              force_approval=force)
    if verdict.get("allowed"):
        return None
    _audit("blocked", action=action, status=verdict.get("status"),
           approval_id=verdict.get("approval_id"), level=verdict.get("level_required"))
    return {
        "success": False,
        "status": verdict.get("status"),
        "error": verdict.get("error") or verdict.get("status") or "desktop_action_blocked",
        "approval_id": verdict.get("approval_id"),
        "level_required": verdict.get("level_required"),
        "action": action,
        "verified": False,
    }


def _done(action: str, backend: str, **fields: Any) -> dict[str, Any]:
    _audit("done", step=action, backend=backend, **{k: fields[k] for k in fields
                                                    if k in ("path", "x", "y", "button", "keys", "chars")})
    return {"success": True, "action": action, "backend": backend, **fields}


def screenshot(path: str | None = None, region: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
    ensure_dirs()
    SCREENS_DIR.mkdir(parents=True, exist_ok=True)
    target = Path(path).expanduser() if path else (
        SCREENS_DIR / ("claude_" + time.strftime("%Y%m%d_%H%M%S") + "_" + hashlib.sha1(
            str(time.time_ns()).encode()).hexdigest()[:6] + ".png"))
    if not target.is_absolute():
        target = SCREENS_DIR / target
    target.parent.mkdir(parents=True, exist_ok=True)

    mod, why = _pyautogui()
    if mod is not None:
        try:
            img = mod.screenshot(region=region) if region else mod.screenshot()
            img.save(str(target))
            if target.is_file() and target.stat().st_size > 0:
                return _done("screenshot", "pyautogui", path=str(target),
                             bytes=target.stat().st_size, width=img.width, height=img.height,
                             verified=True, note="screenshot_file_verified")
        except Exception as exc:
            _audit("screenshot_error", backend="pyautogui", error=type(exc).__name__)
            if not IS_LINUX:
                return {"success": False, "error": "screenshot_failed",
                        "message": type(exc).__name__, "action": "screenshot", "verified": False}

    ok, display = _display_ready()
    if not ok:
        return {"success": False, "error": display, "action": "screenshot", "verified": False}
    commands: list[list[str]] = []
    if shutil.which("grim"):
        commands.append(["grim", str(target)])
    if shutil.which("scrot"):
        commands.append(["scrot", "-o", str(target)])
    if shutil.which("gnome-screenshot"):
        commands.append(["gnome-screenshot", "-f", str(target)])
    if shutil.which("spectacle"):
        commands.append(["spectacle", "-b", "-n", "-o", str(target)])
    if shutil.which("import"):
        commands.append(["import", "-window", "root", str(target)])
    errors: list[str] = []
    for argv in commands:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        except Exception as exc:
            errors.append(type(exc).__name__)
            continue
        if proc.returncode == 0 and target.is_file() and target.stat().st_size > 0:
            size = (0, 0)
            try:
                from PIL import Image  # type: ignore
                with Image.open(target) as img:
                    size = img.size
            except Exception:
                pass
            return _done("screenshot", Path(argv[0]).name, path=str(target),
                         bytes=target.stat().st_size, width=size[0], height=size[1],
                         verified=True, note="screenshot_file_verified")
        errors.append((proc.stderr or "").strip()[:120] or ("exit_" + str(proc.returncode)))
    return {"success": False, "error": "screenshot_backend_failed", "action": "screenshot",
            "details": errors[:4], "display": display, "verified": False}


def screen_size() -> dict[str, Any]:
    mod, why = _pyautogui()
    if mod is None:
        return {"success": False, "error": why or "input_unavailable", "action": "screen_size"}
    try:
        width, height = mod.size()
        return _done("screen_size", "pyautogui", width=int(width), height=int(height), verified=True)
    except Exception as exc:
        return {"success": False, "error": "screen_size_failed", "message": type(exc).__name__,
                "action": "screen_size", "verified": False}


def pointer_position() -> dict[str, Any]:
    mod, why = _pyautogui()
    if mod is None:
        return {"success": False, "error": why or "input_unavailable", "action": "pointer_position"}
    try:
        pos = mod.position()
        return _done("pointer_position", "pyautogui", x=int(pos.x), y=int(pos.y), verified=True)
    except Exception as exc:
        return {"success": False, "error": "pointer_position_failed",
                "message": type(exc).__name__, "action": "pointer_position", "verified": False}


def mouse_move(x: int, y: int, duration: float = 0.2, *, approval_id: str | None = None) -> dict[str, Any]:
    try:
        x, y = int(x), int(y)
    except (TypeError, ValueError):
        return {"success": False, "error": "invalid_coordinates", "action": "mouse_move"}
    blocked = _gate("mouse_move", {"x": x, "y": y}, approval_id, sensitive=False)
    if blocked:
        return blocked
    mod, why = _pyautogui()
    if mod is None:
        return {"success": False, "error": why or "input_unavailable", "action": "mouse_move"}
    try:
        mod.moveTo(x, y, duration=max(0.0, min(float(duration or 0.2), 5.0)))
        pos = mod.position()
        verified = abs(int(pos.x) - x) <= 3 and abs(int(pos.y) - y) <= 3
        return _done("mouse_move", "pyautogui", x=int(pos.x), y=int(pos.y),
                     target_x=x, target_y=y, verified=verified,
                     note=("pointer_position_verified" if verified else "pointer_position_mismatch"))
    except Exception as exc:
        return {"success": False, "error": "mouse_move_failed", "message": type(exc).__name__,
                "action": "mouse_move", "verified": False}


def mouse_click(x: int, y: int, button: str = "left", clicks: int = 1,
                interval: float = 0.1, *, approval_id: str | None = None) -> dict[str, Any]:
    try:
        x, y = int(x), int(y)
        clicks = max(1, min(int(clicks or 1), 5))
    except (TypeError, ValueError):
        return {"success": False, "error": "invalid_coordinates", "action": "mouse_click"}
    button = (button or "left").lower()
    if button not in ("left", "right", "middle"):
        return {"success": False, "error": "invalid_button", "action": "mouse_click"}
    blocked = _gate("mouse_click", {"x": x, "y": y, "button": button, "clicks": clicks},
                    approval_id, sensitive=True)
    if blocked:
        return blocked
    mod, why = _pyautogui()
    if mod is None:
        return {"success": False, "error": why or "input_unavailable", "action": "mouse_click"}
    try:
        mod.moveTo(x, y, duration=0.12)
        mod.click(x=x, y=y, clicks=clicks, interval=max(0.0, min(float(interval or 0.1), 1.0)),
                  button=button)
        return _done("mouse_click", "pyautogui", x=x, y=y, button=button, clicks=clicks,
                     verified=False, note="input_delivered_effect_not_observed")
    except Exception as exc:
        return {"success": False, "error": "mouse_click_failed", "message": type(exc).__name__,
                "action": "mouse_click", "verified": False}


def mouse_scroll(amount: int, x: int | None = None, y: int | None = None,
                 *, approval_id: str | None = None) -> dict[str, Any]:
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        return {"success": False, "error": "invalid_amount", "action": "mouse_scroll"}
    amount = max(-50, min(amount, 50))
    params = {"amount": amount, "x": x, "y": y}
    blocked = _gate("mouse_scroll", params, approval_id, sensitive=False)
    if blocked:
        return blocked
    mod, why = _pyautogui()
    if mod is None:
        return {"success": False, "error": why or "input_unavailable", "action": "mouse_scroll"}
    try:
        if x is not None and y is not None:
            mod.moveTo(int(x), int(y), duration=0.1)
        mod.scroll(amount)
        return _done("mouse_scroll", "pyautogui", amount=amount, verified=False,
                     note="input_delivered_effect_not_observed")
    except Exception as exc:
        return {"success": False, "error": "mouse_scroll_failed", "message": type(exc).__name__,
                "action": "mouse_scroll", "verified": False}


def _type_via_clipboard(mod, text: str) -> str:
    import pyperclip  # type: ignore
    previous = None
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None
    pyperclip.copy(text)
    try:
        mod.hotkey("ctrl", "v")
    finally:
        if previous is not None:
            try:
                pyperclip.copy(previous)
            except Exception:
                pass
    return "clipboard_paste"


def keyboard_type(text: str, interval: float = 0.02,
                  *, approval_id: str | None = None) -> dict[str, Any]:
    text = str(text or "")
    if not text:
        return {"success": False, "error": "empty_text", "action": "keyboard_type"}
    if len(text) > 20000:
        return {"success": False, "error": "text_too_long", "action": "keyboard_type"}
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    blocked = _gate("keyboard_type", {"chars": len(text), "sha256": digest},
                    approval_id, sensitive=True)
    if blocked:
        return blocked
    mod, why = _pyautogui()
    if mod is None:
        return {"success": False, "error": why or "input_unavailable", "action": "keyboard_type"}
    try:
        if text.isascii():
            mod.write(text, interval=max(0.0, min(float(interval or 0.02), 0.2)))
            backend = "pyautogui_write"
        else:
            backend = _type_via_clipboard(mod, text)
        return _done("keyboard_type", backend, chars=len(text), sha256=digest,
                     verified=False, note="input_delivered_effect_not_observed")
    except Exception as exc:
        return {"success": False, "error": "keyboard_type_failed",
                "message": type(exc).__name__, "action": "keyboard_type", "verified": False}


def keyboard_hotkey(keys: list[str] | str, *, approval_id: str | None = None) -> dict[str, Any]:
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.replace("+", " ").split() if k.strip()]
    keys = [str(k).strip().lower() for k in (keys or []) if str(k).strip()]
    if not keys or len(keys) > 5:
        return {"success": False, "error": "invalid_keys", "action": "keyboard_hotkey"}
    blocked = _gate("keyboard_hotkey", {"keys": "+".join(keys)}, approval_id, sensitive=True)
    if blocked:
        return blocked
    mod, why = _pyautogui()
    if mod is None:
        return {"success": False, "error": why or "input_unavailable", "action": "keyboard_hotkey"}
    try:
        mod.hotkey(*keys)
        return _done("keyboard_hotkey", "pyautogui", keys="+".join(keys),
                     verified=False, note="input_delivered_effect_not_observed")
    except Exception as exc:
        return {"success": False, "error": "keyboard_hotkey_failed",
                "message": type(exc).__name__, "action": "keyboard_hotkey", "verified": False}


def media_key(action: str = "playpause", *, approval_id: str | None = None) -> dict[str, Any]:
    mapping = {"playpause": "playpause", "play": "playpause", "pause": "playpause",
               "next": "nexttrack", "prev": "prevtrack", "previous": "prevtrack",
               "stop": "stop", "mute": "volumemute"}
    key = mapping.get((action or "").lower())
    if not key:
        return {"success": False, "error": "invalid_media_action", "action": "media_key"}
    blocked = _gate("media_key", {"action": action}, approval_id, sensitive=False)
    if blocked:
        return blocked
    mod, why = _pyautogui()
    if mod is None:
        return {"success": False, "error": why or "input_unavailable", "action": "media_key"}
    try:
        mod.press(key)
        return _done("media_key", "pyautogui", keys=key, verified=False,
                     note="input_delivered_effect_not_observed")
    except Exception as exc:
        return {"success": False, "error": "media_key_failed", "message": type(exc).__name__,
                "action": "media_key", "verified": False}


def clipboard(action: str = "get", text: str | None = None) -> dict[str, Any]:
    act = (action or "get").lower()
    if act not in ("get", "set"):
        return {"success": False, "error": "invalid_clipboard_action", "action": "clipboard"}
    try:
        import pyperclip  # type: ignore
    except Exception:
        return {"success": False, "error": "pyperclip_unavailable", "action": "clipboard"}
    try:
        if act == "get":
            value = pyperclip.paste()
            return _done("clipboard_get", "pyperclip", text=value, verified=True)
        if act == "set":
            if text is None:
                return {"success": False, "error": "text_required", "action": "clipboard"}
            pyperclip.copy(str(text))
            verified = pyperclip.paste() == str(text)
            return _done("clipboard_set", "pyperclip", chars=len(str(text)),
                         verified=verified, note=("clipboard_readback_verified" if verified
                                                  else "clipboard_readback_mismatch"))
    except Exception as exc:
        return {"success": False, "error": "clipboard_failed", "message": type(exc).__name__,
                "action": "clipboard", "verified": False}
    return {"success": False, "error": "invalid_clipboard_action", "action": "clipboard"}
