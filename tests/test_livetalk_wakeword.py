import pytest
from nexus.livetalk.wakeword import WakeGate

W = lambda *ws, c=0.9: [{"word": w, "conf": c} for w in ws]

def test_positive_and_cooldown():
    g = WakeGate(0.5, cooldown_s=3)
    assert g.check(W("Hey", "Nexus", "wie"), True, 10.0)
    assert not g.check(W("hey", "nexus"), True, 11.0)
    assert g.check(W("hallo", "nexus"), True, 14.0)

def test_vad_gate_and_negatives():
    g = WakeGate(0.5)
    assert not g.check(W("hey", "nexus"), False, 1.0)
    assert not g.check(W("hey", "lexus"), True, 1.0)
    assert not g.check(W("nexus"), True, 1.0)
    assert WakeGate(0.5, allow_bare=True).check(W("nexus"), True, 1.0)

def test_sensitivity():
    assert not WakeGate(0.2).check(W("hey", "nexus", c=0.6), True, 1.0)
    assert WakeGate(0.6).check(W("hey", "nexus", c=0.6), True, 1.0)
    with pytest.raises(ValueError): WakeGate(1.5)
