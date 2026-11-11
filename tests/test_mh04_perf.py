# -*- coding: utf-8 -*-
"""MH04: kleiner Performance-Check fuer Master-Home Health-Endpunkt (kein Fake-PASS)."""
from __future__ import annotations
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8299"

def test_health_latency_under_2s():
    t0 = time.perf_counter()
    with urllib.request.urlopen(BASE + "/api/health", timeout=5) as r:
        body = r.read()
        assert r.status == 200
    ms = (time.perf_counter() - t0) * 1000
    assert b"success" in body
    assert ms < 2000, f"health zu langsam: {ms:.0f} ms"

def test_health_score_present():
    with urllib.request.urlopen(BASE + "/api/health", timeout=5) as r:
        data = json.loads(r.read().decode("utf-8"))
    assert data.get("success") is True
    assert "score" in data
