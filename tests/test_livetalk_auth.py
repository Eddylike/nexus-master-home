import pytest
from nexus.livetalk import auth


def test_network_rules():
    assert auth.is_allowed_client("127.0.0.1", {})
    assert auth.is_allowed_client("192.0.2.40", {})
    assert auth.is_allowed_client("192.0.2.15", {})
    assert not auth.is_allowed_client("8.8.8.8", {})
    assert not auth.is_allowed_client("192.0.2.40", {"X-Forwarded-For": "1.2.3.4"})
    assert not auth.is_allowed_client("127.0.0.1", {"Tailscale-User-Login": "x"})
    assert not auth.is_allowed_client("kaputt", {})
    assert auth.is_loopback("127.0.0.1", {}) and not auth.is_loopback("192.0.2.40", {})


def test_pairing_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "TOKENS", tmp_path / "t.json")
    monkeypatch.setattr(auth, "PAIRING", tmp_path / "p.json")
    monkeypatch.setattr(auth.os, "name", "posix")
    assert auth.exchange("123456", "192.0.2.40", {})[0] == 409
    code = auth.start_pairing()["code"]
    bad = "000000" if code != "000000" else "111111"
    st, body = auth.exchange(bad, "192.0.2.40", {})
    assert st == 401 and body["attempts_left"] == 4
    assert auth.exchange(code, "8.8.8.8", {})[0] == 403
    st, body = auth.exchange(code, "192.0.2.40", {}, "Redmi")
    assert st == 200 and len(body["token"]) > 30
    assert auth.check_token(body["token"]) == "device"
    assert auth.check_token("x" * 40) is None
    assert body["token"] not in (tmp_path / "t.json").read_text()  # nur Hash gespeichert
    assert auth.exchange(code, "192.0.2.40", {})[0] == 409  # Code nur einmal


def test_pairing_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "TOKENS", tmp_path / "t.json")
    monkeypatch.setattr(auth, "PAIRING", tmp_path / "p.json")
    monkeypatch.setattr(auth.os, "name", "posix")
    code = auth.start_pairing()["code"]
    bad = "000000" if code != "000000" else "111111"
    for _ in range(auth.MAX_ATTEMPTS):
        auth.exchange(bad, "127.0.0.1", {})
    assert auth.exchange(code, "127.0.0.1", {})[0] == 429
