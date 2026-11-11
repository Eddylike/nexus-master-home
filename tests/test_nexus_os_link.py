"""Unit tests for nexus_os_link — temp state must live on G: (guard stays strict)."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest

from nexus import nexus_os_link as L

_G_TMP_ROOT = Path(r"G:\meine-ki\nexus-ai\workspace\tmp")


@pytest.fixture
def g_tmp(monkeypatch):
    """Dedicated temp dir on G: so LINK_STATE_MUST_BE_ON_G stays intact."""
    _G_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    d = _G_TMP_ROOT / f"pytest_{uuid.uuid4().hex[:12]}"
    d.mkdir(parents=True, exist_ok=False)
    monkeypatch.setattr(L, "DIR", d)
    monkeypatch.setattr(L, "link_status", lambda: {"server": "ONLINE"})
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_network_filter():
    assert L.allowed("192.0.2.10", {})
    assert L.allowed("192.0.2.56", {})
    assert L.allowed("127.0.0.1", {})
    assert not L.allowed("8.8.8.8", {})
    assert not L.allowed("127.0.0.1", {"X-Forwarded-For": "1.2.3.4"})
    assert L.handle("GET", "/api/nexus-os/device", "8.8.8.8", {})[0] == 403
    assert L.handle("GET", "/api/other", "8.8.8.8", {}) is None


def test_device_roundtrip(g_tmp):
    assert L.device_get()[1]["state"] == "UNKNOWN"
    c, r = L.handle(
        "POST",
        "/api/nexus-os/device",
        "192.0.2.10",
        {},
        body={"battery": "77", "net": "wifi", "bogus": 1},
    )
    assert c == 200 and "bogus" not in r["stored"]
    d = L.device_get()[1]
    assert d["state"] == "ONLINE" and d["device"]["battery"] == 77 and "charging" not in d["device"]


def test_sync_idempotent_and_push(g_tmp):
    items = [{"id": "a1", "kind": "chat", "text": "hi"}, {"id": "x", "kind": "bad"}]
    r1 = L.handle("POST", "/api/nexus-os/sync", "192.0.2.10", {}, body={"items": items})[1]
    r2 = L.handle("POST", "/api/nexus-os/sync", "192.0.2.10", {}, body={"items": items})[1]
    assert r1["acked"] == ["a1"] == r2["acked"] and r1["rejected"] == ["x"]
    assert len((g_tmp / "inbox.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert L.handle("POST", "/api/nexus-os/push", "192.0.2.10", {}, body={"text": "x"})[0] == 403
    L.handle("POST", "/api/nexus-os/push", "127.0.0.1", {}, body={"text": "vom PC"})
    g = L.sync_get({"since": ["0"]})[1]
    assert g["cursor"] == 1 and g["items"][0]["text"] == "vom PC"
    assert L.sync_get({"since": ["1"]})[1]["items"] == []
