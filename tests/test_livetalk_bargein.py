import pytest
from nexus.livetalk.bargein import BargeInController

def test_barge_in_while_speaking():
    b = BargeInController(min_speech_ms=90, frame_ms=30); b.set_state("speaking")
    assert [b.on_vad(True) for _ in range(3)] == [False, False, True]
    assert b.state == "listening"

def test_noise_resets():
    b = BargeInController(90, 30); b.set_state("speaking")
    for v in (True, True, False, True, True): assert b.on_vad(v) is False

def test_no_barge_when_idle_or_listening():
    b = BargeInController(30, 30)
    assert b.on_vad(True) is False
    b.set_state("listening"); assert b.on_vad(True) is False

def test_bad_state():
    with pytest.raises(ValueError): BargeInController().set_state("x")
