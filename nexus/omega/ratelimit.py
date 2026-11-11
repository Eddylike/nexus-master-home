"""Rate-Limiter und Cache fuer OMEGA (SQLite-Fallback, Redis optional).

Redis wird nur genutzt, wenn explizit konfiguriert und importierbar. Standard
ist SQLite, damit der Agent ohne Zusatzdienste laeuft.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from nexus.omega import events


def _key(value: str) -> str:
    raw = str(value)
    if len(raw) <= 120:
        return raw
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_get(key: str) -> Any | None:
    return events.cache_get(_key(key))


def cache_set(key: str, value: Any, ttl_s: float = 300) -> None:
    events.cache_set(_key(key), value, ttl_s)


def allow(key: str, limit: int = 60, window_s: float = 60) -> bool:
    redis_url = (os.environ.get("OMEGA_REDIS_URL") or "").strip()
    if redis_url:
        try:
            import redis  # type: ignore

            client = redis.Redis.from_url(redis_url, decode_responses=True)
            slot = _key(key)
            count = client.incr(slot)
            if count == 1:
                client.expire(slot, max(1, int(window_s)))
            return int(count) <= max(1, int(limit))
        except Exception:
            pass
    return events.rate_allow(_key(key), limit, window_s)


def cached(key: str, ttl_s: float, producer, *, refresh: bool = False) -> Any:
    """Einfacher Read-Through-Cache. producer() muss JSON-serialisierbar liefern."""
    if not refresh:
        hit = cache_get(key)
        if hit is not None:
            return hit
    value = producer()
    try:
        json.dumps(value, default=str)
        cache_set(key, value, ttl_s)
    except (TypeError, ValueError):
        pass
    return value


def monotonic() -> float:
    return time.monotonic()
