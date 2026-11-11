from nexus.common import agent_guard as G


class H(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_loopback_without_proxy_allowed(monkeypatch):
    monkeypatch.delenv("NEXUS_AGENT_GUARD", raising=False)
    assert G.allow("127.0.0.1", H()) == (True, "LOOPBACK")
    assert G.allow("::1", H()) == (True, "LOOPBACK")


def test_remote_denied_by_default(monkeypatch):
    monkeypatch.delenv("NEXUS_AGENT_REMOTE", raising=False)
    ok, why = G.allow("192.168.1.50", H())
    assert not ok and why == "LOOPBACK_OR_TOKEN_REQUIRED"


def test_forwarded_header_denies_loopback(monkeypatch):
    ok, why = G.allow("127.0.0.1", H({"X-Forwarded-For": "1.2.3.4"}))
    assert not ok and why == "LOOPBACK_OR_TOKEN_REQUIRED"


def test_remote_with_token(monkeypatch):
    monkeypatch.setenv("NEXUS_AGENT_REMOTE", "1")
    monkeypatch.setenv("NEXUS_AGENT_TOKEN", "s3cret-token")
    assert G.allow("10.0.0.5", H({"Authorization": "Bearer s3cret-token"})) == (True, "REMOTE_TOKEN")
    assert not G.allow("10.0.0.5", H({"Authorization": "Bearer wrong"}))[0]
    assert not G.allow("10.0.0.5", H())[0]


def test_remote_enabled_without_token_fails_closed(monkeypatch):
    monkeypatch.setenv("NEXUS_AGENT_REMOTE", "1")
    monkeypatch.delenv("NEXUS_AGENT_TOKEN", raising=False)
    assert not G.allow("10.0.0.5", H())[0]


def test_guard_rollback_flag(monkeypatch):
    monkeypatch.setenv("NEXUS_AGENT_GUARD", "0")
    assert G.allow("10.0.0.5", H()) == (True, "GUARD_DISABLED")
