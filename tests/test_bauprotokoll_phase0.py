"""Phase 0 Abnahme: zentrale config.json, Health-Uebersicht und Start-/Rollback-Skripte."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from nexus.bauprotokoll.config import Config
from nexus.bauprotokoll.health import check_all

ROOT = Path(__file__).resolve().parents[1]


class _StatusHandler(BaseHTTPRequestHandler):
    code = 200
    body = b"ok"

    def do_GET(self):
        self.send_response(self.code)
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


def _serve(code: int = 200):
    handler = type("H", (_StatusHandler,), {"code": code})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv, f"http://127.0.0.1:{srv.server_port}/health"


def _cfg(services):
    return {"health": {"timeout_s": 1, "services": services}}


def test_config_json_exists_and_has_core_keys():
    path = ROOT / "config.json"
    assert path.is_file(), "config.json fehlt (Phase 0: Config zentralisieren)"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["ports"]["master_home"] == 8299
    assert data["ports"]["bridge"] == 8300
    assert data["models"]["generalist"]
    assert isinstance(data["whitelist"], list) and data["whitelist"]
    assert data["forbidden_paths"]


def test_config_defaults_merge_with_local_override(tmp_path):
    (tmp_path / "config.json").write_text('{"ports": {"master_home": 9999}}', encoding="utf-8")
    cfg = Config(root=tmp_path)
    assert cfg.get("ports.master_home") == 9999
    assert cfg.get("ports.bridge") == 8300  # Default bleibt erhalten


def test_health_all_services_up():
    srv, url = _serve(200)
    try:
        report = check_all(_cfg({"master": url}))
    finally:
        srv.shutdown()
    assert report["overall"] == "UP"
    assert report["success"] is True
    assert report["services"][0]["status"] == "UP"
    assert report["services"][0]["http_status"] == 200


def test_health_auth_401_counts_as_up():
    srv, url = _serve(401)
    try:
        report = check_all(_cfg({"bridge": url}))
    finally:
        srv.shutdown()
    assert report["services"][0]["status"] == "UP"
    assert report["services"][0]["http_status"] == 401


def test_health_404_is_down():
    srv, url = _serve(404)
    try:
        report = check_all(_cfg({"missing": url}))
    finally:
        srv.shutdown()
    assert report["services"][0]["status"] == "DOWN"
    assert report["services"][0]["http_status"] == 404


def test_health_500_is_down():
    srv, url = _serve(500)
    try:
        report = check_all(_cfg({"broken": url}))
    finally:
        srv.shutdown()
    assert report["overall"] == "DOWN"
    assert report["success"] is False
    assert report["services"][0]["status"] == "DOWN"


def test_health_closed_port_is_down():
    srv, url = _serve(200)
    srv.shutdown()
    srv.server_close()
    report = check_all(_cfg({"gone": url}))
    assert report["services"][0]["status"] == "DOWN"
    assert report["services"][0]["error"]


def test_health_mixed_services_overall_down():
    up, up_url = _serve(200)
    down, down_url = _serve(500)
    try:
        report = check_all(_cfg({"up": up_url, "down": down_url}))
    finally:
        up.shutdown()
        down.shutdown()
    assert report["overall"] == "DOWN"
    by_name = {s["name"]: s for s in report["services"]}
    assert by_name["up"]["status"] == "UP"
    assert by_name["down"]["status"] == "DOWN"


def test_start_all_bat_delegates_to_ordered_script():
    text = (ROOT / "start_all.bat").read_text(encoding="utf-8")
    assert "start_nexus_all.ps1" in text
    assert "ExecutionPolicy Bypass" in text


def test_rollback_bat_requires_tag_and_calls_ps1():
    text = (ROOT / "rollback.bat").read_text(encoding="utf-8")
    assert "rollback.ps1" in text
    assert "Nutzung" in text
    ps = (ROOT / "rollback.ps1").read_text(encoding="utf-8")
    assert "git" in ps and "reset --hard" in ps
    assert "pre-rollback-" in ps


def test_backup_script_zips_config_and_code():
    text = (ROOT / "backup_nexus.ps1").read_text(encoding="utf-8")
    assert "Compress-Archive" in text
    assert "nexus-backup-" in text
    assert "config.json" in text
