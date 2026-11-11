"""Isolated tests for the NEXUS maintenance / drain gate.

No real server, task, or process is started. These tests exercise:
  * MaintenanceController (block new work, drain, graceful-shutdown gating)
  * TaskManager integration (new tasks blocked, in-flight work tracked)
  * Bridge maintenance handlers (loopback-only + Bearer auth)

Run: python -m pytest tests/test_maintenance_drain.py -q
"""
from __future__ import annotations

import threading
import time

import pytest


def _fresh_controller(tmp_path, monkeypatch):
    from nexus import maintenance as mod

    ctl = mod.MaintenanceController(state_path=tmp_path / "maintenance.json")
    monkeypatch.setattr(mod, "_CONTROLLER", ctl, raising=False)
    return mod, ctl


def test_controller_lifecycle(tmp_path, monkeypatch):
    mod, ctl = _fresh_controller(tmp_path, monkeypatch)
    assert ctl.state() == mod.STATE_RUNNING
    assert ctl.in_flight() == 0

    with ctl.work("a"):
        assert ctl.in_flight() == 1
        assert ctl.snapshot()["active"] == {"a": 1}
    assert ctl.in_flight() == 0

    ctl.begin_drain(reason="test", actor="unit")
    assert ctl.state() == mod.STATE_DRAINED  # idle at begin time

    with pytest.raises(mod.MaintenanceActive):
        with ctl.work("blocked"):
            pass

    ctl.cancel_drain()
    assert ctl.state() == mod.STATE_RUNNING
    with ctl.work("c"):
        pass


def test_drain_waits_for_in_flight(tmp_path, monkeypatch):
    mod, ctl = _fresh_controller(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def worker():
        with ctl.work("long"):
            started.set()
            release.wait(timeout=5)

    t = threading.Thread(target=worker)
    t.start()
    assert started.wait(5)

    ctl.begin_drain(reason="drain", actor="unit")
    assert ctl.state() == mod.STATE_DRAINING
    assert ctl.in_flight() == 1

    result: dict = {}

    def waiter():
        result["drained"] = ctl.wait_drained(timeout=5, quiet_seconds=0.2)

    w = threading.Thread(target=waiter)
    w.start()
    time.sleep(0.4)
    assert "drained" not in result  # still waiting for the in-flight worker

    release.set()
    w.join(5)
    t.join(5)
    assert result.get("drained") is True
    assert ctl.state() == mod.STATE_DRAINED
    assert ctl.in_flight() == 0


def test_drain_timeout_returns_false(tmp_path, monkeypatch):
    mod, ctl = _fresh_controller(tmp_path, monkeypatch)
    started = threading.Event()
    release = threading.Event()

    def worker():
        with ctl.work("stuck"):
            started.set()
            release.wait(timeout=5)

    t = threading.Thread(target=worker)
    t.start()
    assert started.wait(5)
    ctl.begin_drain(reason="drain", actor="unit")

    assert ctl.wait_drained(timeout=0.5, quiet_seconds=0.1) is False
    assert ctl.state() == mod.STATE_DRAINING
    release.set()
    t.join(5)


def test_shutdown_gating(tmp_path, monkeypatch):
    mod, ctl = _fresh_controller(tmp_path, monkeypatch)
    fired: list = []
    ctl.set_shutdown_callback(lambda: fired.append(True))

    r = ctl.request_shutdown(confirm=True)
    assert r["ok"] is False and r["error"] == "drain_not_active"

    ctl.begin_drain(reason="drain", actor="unit")
    r = ctl.request_shutdown(confirm=False)
    assert r["ok"] is False and r["error"] == "confirmation_required"

    r = ctl.request_shutdown(confirm=True)
    assert r["ok"] is True and fired == [True]
    assert ctl.snapshot()["shutdown_requested"] is True


def test_shutdown_refused_while_work_in_flight(tmp_path, monkeypatch):
    mod, ctl = _fresh_controller(tmp_path, monkeypatch)
    fired: list = []
    ctl.set_shutdown_callback(lambda: fired.append(True))
    started = threading.Event()
    release = threading.Event()

    def worker():
        with ctl.work("busy"):
            started.set()
            release.wait(timeout=5)

    t = threading.Thread(target=worker)
    t.start()
    assert started.wait(5)
    ctl.begin_drain(reason="drain", actor="unit")

    r = ctl.request_shutdown(confirm=True)
    assert r["ok"] is False and r["error"] == "work_in_flight"
    assert fired == []

    release.set()
    t.join(5)
    r = ctl.request_shutdown(confirm=True)
    assert r["ok"] is True


def test_task_manager_blocks_new_and_tracks_inflight(tmp_path, monkeypatch):
    from nexus import maintenance as mod
    from nexus.bridge.errors import BridgeError
    from nexus.bridge.models import TaskCreateRequest
    from nexus.bridge.storage import TaskStorage
    from nexus.bridge.tasks import TaskManager

    _fresh_controller(tmp_path, monkeypatch)

    tm = TaskManager(storage=TaskStorage(persist_dir=tmp_path / "tasks"))
    started = threading.Event()
    release = threading.Event()

    def slow_ping(params, rec):
        started.set()
        release.wait(timeout=5)
        return {"ok": True, "pong": True}

    tm.register("ping", slow_ping)

    outcome: dict = {}

    def run_task():
        try:
            rec = tm.create_and_run(TaskCreateRequest(task="ping", params={}, source="test"))
            outcome["status"] = rec.status
        except Exception as e:  # pragma: no cover - defensive
            outcome["error"] = repr(e)

    t = threading.Thread(target=run_task)
    t.start()
    assert started.wait(5)
    # The running task must be counted as in-flight.
    assert mod.maintenance().in_flight() == 1

    mod.maintenance().begin_drain(reason="unit", actor="unit")
    assert mod.maintenance().state() == mod.STATE_DRAINING

    # A new task is refused with 503 while draining.
    with pytest.raises(BridgeError) as ei:
        tm.create_and_run(TaskCreateRequest(task="ping", params={}, source="test"))
    assert ei.value.code == "MAINTENANCE_ACTIVE"
    assert ei.value.http_status == 503

    release.set()
    t.join(5)
    assert outcome.get("status") == "succeeded"
    assert mod.maintenance().in_flight() == 0
    assert mod.maintenance().state() == mod.STATE_DRAINED
    mod.maintenance().cancel_drain()


class _FakeHandler:
    def __init__(self, client: str, auth: str | None):
        self.client_address = (client, 51234)
        self.headers = {"Authorization": auth} if auth else {}
        self.sent: list = []

    def _json(self, code: int, payload: dict) -> None:
        self.sent.append((code, payload))


def test_maintenance_handler_auth_and_loopback(tmp_path, monkeypatch):
    from nexus import maintenance as mod
    from nexus.bridge import api as bapi

    _fresh_controller(tmp_path, monkeypatch)
    token = "a" * 64
    monkeypatch.setenv("NEXUS_BRIDGE_TOKEN", token)
    auth = f"Bearer {token}"

    # Non-loopback caller is rejected even with a valid token.
    remote = _FakeHandler("10.0.0.7", auth)
    bapi.handle_maintenance(remote, "/api/bridge/maintenance/drain", {})
    assert remote.sent[-1][0] == 403
    assert remote.sent[-1][1]["error"] == "LOCAL_ONLY"

    # Loopback without a token is unauthorized.
    noauth = _FakeHandler("127.0.0.1", None)
    bapi.handle_maintenance(noauth, "/api/bridge/maintenance/drain", {})
    assert noauth.sent[-1][0] == 401

    # Loopback with valid token may drain.
    local = _FakeHandler("127.0.0.1", auth)
    bapi.handle_maintenance(local, "/api/bridge/maintenance/drain", {"reason": "unit"})
    assert local.sent[-1][0] == 200
    assert mod.maintenance().is_draining() is True

    # Status GET is allowed and reports DRAINED (idle).
    status = _FakeHandler("127.0.0.1", auth)
    bapi.handle_maintenance_get(status, "/api/bridge/maintenance", {})
    assert status.sent[-1][0] == 200
    assert status.sent[-1][1]["maintenance"]["state"] == mod.STATE_DRAINED

    # Shutdown requires confirm + completed drain.
    noconf = _FakeHandler("127.0.0.1", auth)
    bapi.handle_maintenance(noconf, "/api/bridge/maintenance/shutdown", {})
    assert noconf.sent[-1][0] == 400

    mod.maintenance().set_shutdown_callback(lambda: None)
    ok = _FakeHandler("127.0.0.1", auth)
    bapi.handle_maintenance(ok, "/api/bridge/maintenance/shutdown", {"confirm": True})
    assert ok.sent[-1][0] == 200 and ok.sent[-1][1]["success"] is True

    cancel = _FakeHandler("127.0.0.1", auth)
    bapi.handle_maintenance(cancel, "/api/bridge/maintenance/cancel", {})
    assert cancel.sent[-1][0] == 200
    assert mod.maintenance().state() == mod.STATE_RUNNING


def test_maintenance_path_is_local_only_by_contract():
    """The path must never be part of the Funnel/gateway allowlist."""
    from nexus.bridge_gateway import gateway_allowlist as gw

    assert gw.allowed_path("/api/bridge/maintenance") is False
    assert gw.allowed_path("/api/bridge/maintenance/drain") is False
    assert gw.allowed_path("/api/bridge/maintenance/shutdown") is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
