"""CSRF-Schutz: fremde Browser-Origins dürfen keine POSTs auslösen, native Clients schon."""
from email.message import Message

import pytest

from nexus.chat import server


class _H:
    """Minimaler Handler-Stub: nur headers + die zu prüfende Methode."""
    _cross_site_blocked = server.Handler._cross_site_blocked

    def __init__(self, **hdrs):
        self.headers = Message()
        for k, v in hdrs.items():
            self.headers[k.replace("_", "-")] = v


@pytest.mark.parametrize("hdrs,blocked", [
    ({}, False),                                                       # curl / Handy-App
    ({"Host": "127.0.0.1:8299"}, False),
    ({"Host": "127.0.0.1:8299", "Origin": "http://127.0.0.1:8299", "Sec-Fetch-Site": "same-origin"}, False),
    ({"Host": "192.168.1.5:8299", "Origin": "http://192.168.1.5:8299"}, False),  # Handy im LAN-Browser
    ({"Host": "127.0.0.1:8299", "Origin": "https://evil.example"}, True),
    ({"Host": "127.0.0.1:8299", "Sec-Fetch-Site": "cross-site"}, True),
    ({"Host": "127.0.0.1:8299", "Origin": "null"}, True),               # file:// / sandboxed iframe
])
def test_cross_site_blocked(hdrs, blocked):
    assert _H(**hdrs)._cross_site_blocked() is blocked


def test_allowlisted_origin_passes(monkeypatch):
    monkeypatch.setattr(server, "_CORS_ORIGINS", frozenset({"http://wozipc-nexus:8299"}))
    assert _H(Host="127.0.0.1:8299", Origin="http://wozipc-nexus:8299")._cross_site_blocked() is False
