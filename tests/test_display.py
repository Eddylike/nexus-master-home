import pytest

from nexus.platform.display import display_available


def test_headless_environment_disables_display(monkeypatch):
    monkeypatch.setenv("NEXUS_HEADLESS", "1")
    assert not display_available()


@pytest.mark.visual
def test_visual_probe_requires_display():
    assert display_available()
