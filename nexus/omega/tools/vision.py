"""Vision-Tools: HTML headless rendern (Chrome/Edge/Chromium), ohne Playwright-Zwang."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from nexus.omega import config

_MAX_HTML = 200_000


def _browsers() -> list[str]:
    found: list[str] = []
    for name in ("msedge", "chrome", "chromium", "chromium-browser", "brave", "google-chrome"):
        exe = shutil.which(name)
        if exe:
            found.append(exe)
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) /
            "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) /
            "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) /
            "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) /
            "BraveSoftware/Brave-Browser/Application/brave.exe",
        ]
        for path in candidates:
            if path.is_file():
                found.append(str(path))
    elif os.name == "posix":
        for path in ("/usr/bin/chromium", "/usr/bin/google-chrome", "/snap/bin/chromium"):
            if Path(path).is_file():
                found.append(path)
    seen = set()
    return [b for b in found if not (b.lower() in seen or seen.add(b.lower()))]


def _safe_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", str(name or "render"))[:60] or "render"
    return base if base.lower().endswith(".png") else base + ".png"


def render_html(params: dict, ctx: dict) -> dict:
    html = str(params.get("html") or "")
    if not html.strip():
        return {"success": False, "status": "INVALID_PARAMS", "error": "html_required"}
    if len(html.encode("utf-8")) > _MAX_HTML:
        return {"success": False, "status": "TOO_LARGE", "error": "html_too_large"}
    width = max(200, min(int(params.get("width") or 1280), 4000))
    height = max(200, min(int(params.get("height") or 900), 4000))
    browsers = _browsers()
    if not browsers:
        return {"success": False, "status": "NOT_AVAILABLE",
                "error": "Kein Chrome/Edge/Chromium gefunden (headless Rendering nicht verfuegbar)"}
    outdir = config.sandbox_dir() / "renders"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    name = _safe_name(str(params.get("name") or f"render_{stamp}"))
    out = (outdir / name).resolve()
    src = (outdir / (name + ".html")).resolve()
    src.write_text(html, encoding="utf-8")
    last_error = ""
    for browser in browsers:
        cmd = [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
               "--no-first-run", "--no-default-browser-check", "--no-sandbox",
               f"--window-size={width},{height}", f"--screenshot={out}", src.as_uri()]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=40,
                                  creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            last_error = f"{type(exc).__name__}: {exc}"[:300]
            continue
        if out.is_file() and out.stat().st_size > 0:
            return {"success": True, "status": "OK", "path": str(out),
                    "relative": str(out.relative_to(config.home())), "bytes": out.stat().st_size,
                    "width": width, "height": height, "browser": Path(browser).name}
        last_error = proc.stderr.decode("utf-8", "replace")[-800:]
    return {"success": False, "status": "RENDER_FAILED",
            "error": "Headless-Rendering fehlgeschlagen: " + (last_error or "unbekannt")}


HANDLERS = {"render_html": render_html}
