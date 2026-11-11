import json
from pathlib import Path
import pytest
from nexus.livetalk import engines


def test_speakable():
    t = engines.speakable("**Hallo** Eddie 😀, z. B. die KI am PC läuft zu 90 % – siehe https://x.de/a")
    assert "**" not in t and "😀" not in t and "zum Beispiel" in t and "Ka-I" in t and "Pe-Zeh" in t
    assert "90 Prozent" in t and "der Link" in t


def test_hallucination_filter():
    assert engines.clean_transcript(" Untertitel im Auftrag des ZDF ") == ""
    assert engines.clean_transcript("Vielen Dank.") == ""
    assert engines.clean_transcript("Wie spät ist es?") == "Wie spät ist es?"


def test_cfg_override(tmp_path):
    p = tmp_path / "lt.json"
    p.write_text(json.dumps({"stt_model": "base", "fremd": 1}))
    c = engines.load_cfg(p)
    assert c["stt_model"] == "base" and "fremd" not in c and c["end_silence_ms"] == engines.DEFAULTS["end_silence_ms"]


@pytest.mark.skipif(not Path(engines.DEFAULTS["vad_model"]).is_file(), reason="Silero-Modell fehlt")
def test_silero_vad_silence():
    import numpy as np
    v = engines.SileroVAD(engines.DEFAULTS["vad_model"])
    probs = [v.process(np.zeros(512, dtype=np.float32)) for _ in range(10)]
    assert max(probs) < 0.3
    with pytest.raises(ValueError):
        v.process(np.zeros(100, dtype=np.float32))


class _Seg:
    def __init__(self, text, no_speech_prob=0.1, avg_logprob=-0.3):
        self.text = text
        self.no_speech_prob = no_speech_prob
        self.avg_logprob = avg_logprob


def test_stt_confidence():
    ok = engines._stt_confidence([_Seg("Hallo Eddie"), _Seg("Wie geht es dir?")])
    assert ok["segments"] == 2 and ok["no_speech"] < 0.3
    empty = engines._stt_confidence([])
    assert empty["no_speech"] == 1.0
    assert engines.DEFAULTS["stt_model"] == "small"
    assert int(engines.DEFAULTS["stt_beam"]) >= 5
    assert engines.DEFAULTS["stt_vad_filter"] is True
    # WhatsApp steht im Prompt
    assert "WhatsApp" in engines.DEFAULTS["stt_hint"]
    assert "Erfurt" not in engines.DEFAULTS["stt_hint"]
    assert int(engines.DEFAULTS["stt_beam_short"]) <= 2

