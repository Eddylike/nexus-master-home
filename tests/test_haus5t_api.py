import pytest
from nexus.games.haus5t import api


def test_unknown_path_404():
    code, body = api.handle("GET", "/api/games/haus5t/nix", {})
    assert code == 404 and body["success"] is False


def test_control_bad_action():
    code, body = api.handle("POST", "/api/games/haus5t/control", {"action": "explode"})
    assert code in (400, 404)
