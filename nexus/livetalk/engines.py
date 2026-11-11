"""LT-10: Lokale Sprach-Engines fuer Live Talk (alles auf dem PC, CPU, kein Cloud-Dienst).

- Silero VAD (ONNX via onnxruntime, ohne torch): 512-Sample-Fenster bei 16 kHz (32 ms).
- faster-whisper (CPU int8) fuer deutsche Spracherkennung direkt aus dem RAM (keine Audiodatei).
- Piper (piper-tts) mit einer einheitlichen deutschen Stimme fuer alle Agenten (voice.py).
Modelle liegen auf G:, Caches werden auf G: gelenkt. Audio wird nie auf Platte geschrieben.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[2]
MODELS = Path(os.environ.get("NEXUS_MODELS_DIR", r"G:\meine-ki\models"))
CFG_PATH = ROOT / "workspace" / "apex" / "livetalk.json"
CACHE = Path(r"G:\meine-ki\cache")

DEFAULTS = {
    # Messung 10.11.: base ~0,6 s / WER 0,12; small ~2,0 s / WER 0,08; medium ~6,7 s (zu langsam).
    # 10.11. Nachmittag: small + beam 5, weil base am Handy zu viele Wortfehler lieferte.
    "stt_model": "small",
    "stt_beam": 5,
    "stt_beam_short": 2,       # kurze Aeusserungen: weniger Beam, weniger Latenz
    "stt_vad_filter": True,
    "stt_threads": 0,               # 0 = automatisch (logische Kerne - 2)
    "piper_model": str(MODELS / "piper" / "de_DE-thorsten-high.onnx"),
    "piper_length_scale": 0.97,     # minimal zuegiger als Standard, klingt natuerlicher im Gespraech
    "piper_noise_scale": 0.6,
    "piper_noise_w": 0.8,
    "sentence_pause_ms": 90,
    "tts_gain": 0.9,                # thorsten-high erreicht sonst Vollaussteuerung (Clipping)
    "vad_model": str(MODELS / "silero" / "silero_vad.onnx"),
    "vad_threshold": 0.5,
    "vad_threshold_speaking": 0.8,  # waehrend NEXUS spricht: hoeher (Echo-Reste nicht als Barge-in werten)
    "end_silence_ms": 500,
    "spec_stt_ms": 200,
    "first_clause_chars": 40,       # langer erster Satz: am Komma teilen (erster Ton ~0,5-1 s frueher)             # ab 200 ms Stille Spracherkennung schon spekulativ starten
    "min_speech_ms": 250,
    "bargein_ms": 300,
    "max_utterance_s": 25,
    "stt_hint": "Eddie, NEXUS, Hermes, Aether, NEXUS GPT, YouTube, WhatsApp, Chrome, Spotify, Systemstatus, CPU, Gelsenkirchen.",
}


def _cache_env() -> None:
    """Modell-Caches auf G: (C: bleibt tabu)."""
    if os.name == "nt":
        os.environ.setdefault("HF_HOME", str(CACHE / "hf"))
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")


def load_cfg(path: Path | None = None) -> dict:
    cfg = dict(DEFAULTS)
    p = path or CFG_PATH
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except Exception:
        pass
    return cfg


# ---------------------------------------------------------------- Silero VAD
class SileroVAD:
    """Silero VAD v5 (ONNX). process(window512 float32) -> Sprachwahrscheinlichkeit 0..1."""
    WINDOW = 512
    CONTEXT = 64
    SR = 16000

    def __init__(self, model_path: str):
        import numpy as np
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.inter_op_num_threads = 1
        so.intra_op_num_threads = 1
        self._np = np
        self.sess = ort.InferenceSession(model_path, sess_options=so, providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        np = self._np
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._ctx = np.zeros((1, self.CONTEXT), dtype=np.float32)

    def process(self, window) -> float:
        np = self._np
        x = np.asarray(window, dtype=np.float32).reshape(1, -1)
        if x.shape[1] != self.WINDOW:
            raise ValueError("VAD-Fenster muss 512 Samples haben")
        inp = np.concatenate([self._ctx, x], axis=1)
        out, self._state = self.sess.run(None, {"input": inp, "state": self._state,
                                                "sr": np.array(self.SR, dtype=np.int64)})
        self._ctx = inp[:, -self.CONTEXT:]
        return float(out[0][0])


# ---------------------------------------------------------------- STT
_STT_LOCK = threading.Lock()
_STT: dict = {}


def stt_model(name: str | None = None, cfg: dict | None = None):
    _cache_env()
    cfg = cfg or load_cfg()
    name = name or cfg["stt_model"]
    with _STT_LOCK:
        if name not in _STT:
            from faster_whisper import WhisperModel
            threads = int(cfg.get("stt_threads") or 0) or max(4, (os.cpu_count() or 8) - 2)
            _STT[name] = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads)
        return _STT[name]


def _stt_confidence(segments) -> dict:
    """Aus Whisper-Segmenten: Mittelwerte und Flag, ob der Text vertrauenswuerdig ist."""
    nsp, lp = [], []
    for s in segments:
        if not (getattr(s, "text", None) or "").strip():
            continue
        nsp.append(float(getattr(s, "no_speech_prob", 0.0) or 0.0))
        lp.append(float(getattr(s, "avg_logprob", 0.0) or 0.0))
    avg_nsp = sum(nsp) / len(nsp) if nsp else 1.0
    avg_lp = sum(lp) / len(lp) if lp else -2.0
    return {"no_speech": round(avg_nsp, 3), "avg_logprob": round(avg_lp, 3),
            "segments": len(nsp)}


def transcribe(pcm16: bytes, cfg: dict | None = None) -> dict:
    """PCM16 mono 16 kHz (RAM) -> {text, stt_ms, audio_s, model, confident, low_confidence, no_speech, avg_logprob}."""
    import numpy as np
    cfg = cfg or load_cfg()
    t0 = time.monotonic()
    audio = np.frombuffer(pcm16, dtype=np.int16).astype(np.float32) / 32768.0
    empty = {"text": "", "stt_ms": 0, "audio_s": round(audio.size / 16000, 2), "model": cfg["stt_model"],
             "confident": False, "low_confidence": True, "no_speech": 1.0, "avg_logprob": -2.0}
    if audio.size < 1600:
        empty["stt_ms"] = int((time.monotonic() - t0) * 1000)
        return empty
    model = stt_model(cfg=cfg)
    audio_s = audio.size / 16000.0
    beam_long = max(1, int(cfg.get("stt_beam") or 1))
    beam_short = max(1, int(cfg.get("stt_beam_short") or 2))
    # Kurze Saetze (typisch 1-1,5 s): kleiner Beam; laengere behalten beam 5.
    beam = beam_short if audio_s < 2.2 else beam_long
    use_vad = bool(cfg.get("stt_vad_filter")) and audio_s >= 2.0
    kept = []
    with _STT_LOCK:
        segs, _info = model.transcribe(audio, language="de", beam_size=beam,
                                       vad_filter=use_vad, condition_on_previous_text=False,
                                       without_timestamps=True, initial_prompt=cfg.get("stt_hint") or None,
                                       no_speech_threshold=0.6)
        segs = list(segs)
        for s in segs:
            tx = (s.text or "").strip()
            if tx and float(getattr(s, "no_speech_prob", 0) or 0) < 0.85:
                kept.append(s)
    conf = _stt_confidence(kept)
    text = clean_transcript(" ".join((s.text or "").strip() for s in kept))
    # undeutlich: hohe no_speech-Wahrscheinlichkeit oder sehr schlechter logprob bei kurzem Text
    low = (not text) or conf["no_speech"] >= 0.5 or (conf["avg_logprob"] < -1.0 and len(text) < 24)
    return {"text": text, "stt_ms": int((time.monotonic() - t0) * 1000), "audio_s": round(audio.size / 16000, 2),
            "model": cfg["stt_model"], "beam": beam, "confident": bool(text) and not low, "low_confidence": bool(low),
            "no_speech": conf["no_speech"], "avg_logprob": conf["avg_logprob"]}


# Whisper-Halluzinationen bei Stille/Rauschen (bekannte Untertitel-Floskeln)
_HALLU = re.compile(r"^\W*(untertitel(ung)?\b.*|vielen dank( f(ü|ue)r( ihre| eure)? aufmerksamkeit)?\W*|"
                    r"tsch(ü|ue)ss\W*|danke( sch(ö|oe)n)?\W*|copyright.*|www\.\S+|amara\.org.*|\.+)$", re.I)


def clean_transcript(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t or _HALLU.match(t):
        return ""
    return t


# ---------------------------------------------------------------- TTS (Piper)
_TTS_LOCK = threading.Lock()
_TTS: dict = {}

_SPEAK_MAP = [
    (re.compile(r"\bz\.\s?B\."), "zum Beispiel"), (re.compile(r"\bca\."), "circa"), (re.compile(r"\bbzw\."), "beziehungsweise"),
    (re.compile(r"\busw\."), "und so weiter"), (re.compile(r"\bggf\."), "gegebenenfalls"), (re.compile(r"\bd\.\s?h\."), "das heißt"),
    (re.compile(r"\bKI\b"), "Ka-I"), (re.compile(r"\bPC\b"), "Pe-Zeh"), (re.compile(r"\bNEXUS\b"), "Nexus"),
    (re.compile(r"\bGPT\b"), "Dschi-Pi-Ti"), (re.compile(r"\bAPI\b"), "A-P-I"), (re.compile(r"\bUSB\b"), "U-S-B"),
    (re.compile(r"(\d)\s?%"), r"\1 Prozent"), (re.compile(r"\bWLAN\b"), "W-Lan"), (re.compile(r"&"), " und "),
]
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]")


def speakable(text: str) -> str:
    """Markdown/Emojis/URLs raus, Abkuerzungen ausschreiben -> fluessig sprechbarer Text."""
    t = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)
    t = re.sub(r"https?://\S+", "der Link", t)
    t = re.sub(r"[*_#>`|~]+", " ", t)
    t = _EMOJI.sub("", t)
    for rx, rep in _SPEAK_MAP:
        t = rx.sub(rep, t)
    t = re.sub(r"^\s*[-•]\s*", "", t, flags=re.M)
    return re.sub(r"\s+", " ", t).strip()


def tts_voice(cfg: dict | None = None):
    cfg = cfg or load_cfg()
    path = cfg["piper_model"]
    with _TTS_LOCK:
        if path not in _TTS:
            from piper import PiperVoice
            _TTS[path] = PiperVoice.load(path)
        return _TTS[path]


def synth_stream(text: str, cfg: dict | None = None) -> Iterator[tuple[int, bytes]]:
    """Text -> (sample_rate, PCM16-Bytes) je Piper-Abschnitt (Streaming, nur RAM)."""
    from piper import SynthesisConfig
    cfg = cfg or load_cfg()
    t = speakable(text)
    if not t:
        return
    voice = tts_voice(cfg)
    sc = SynthesisConfig(length_scale=float(cfg["piper_length_scale"]), noise_scale=float(cfg["piper_noise_scale"]),
                         noise_w_scale=float(cfg["piper_noise_w"]))
    with _TTS_LOCK:
        chunks = list(voice.synthesize(t, sc))
    gain = float(cfg.get("tts_gain") or 1.0)
    for ch in chunks:
        pcm = ch.audio_int16_bytes
        if gain != 1.0:
            import numpy as np
            pcm = (np.frombuffer(pcm, dtype=np.int16).astype(np.float32) * gain).clip(-32768, 32767).astype(np.int16).tobytes()
        yield ch.sample_rate, pcm


def warm_all(cfg: dict | None = None) -> dict:
    """Alle Modelle laden (einmal pro Core-Prozess) und Ladezeiten melden."""
    cfg = cfg or load_cfg()
    out = {}
    t = time.monotonic(); get_vad_factory(cfg); out["vad_ms"] = int((time.monotonic() - t) * 1000)
    t = time.monotonic(); stt_model(cfg=cfg); out["stt_ms"] = int((time.monotonic() - t) * 1000)
    t = time.monotonic(); list(synth_stream("Hallo.", cfg)); out["tts_ms"] = int((time.monotonic() - t) * 1000)
    return out


_VAD_OK: dict = {}


def get_vad_factory(cfg: dict | None = None):
    """Prueft das VAD-Modell einmal; jede Sitzung bekommt eine eigene VAD-Instanz (eigener Zustand)."""
    cfg = cfg or load_cfg()
    p = cfg["vad_model"]
    if p not in _VAD_OK:
        SileroVAD(p)
        _VAD_OK[p] = True
    return lambda: SileroVAD(p)


def status() -> dict:
    cfg = load_cfg()
    def has(mod):
        import importlib.util
        try:
            return importlib.util.find_spec(mod) is not None
        except Exception:
            return False
    return {"faster_whisper": has("faster_whisper"), "piper": has("piper"), "onnxruntime": has("onnxruntime"),
            "vad_model": Path(cfg["vad_model"]).is_file(), "piper_model": Path(cfg["piper_model"]).is_file(),
            "stt_model": cfg["stt_model"], "voice": Path(cfg["piper_model"]).stem,
            "loaded": {"stt": sorted(_STT), "tts": [Path(p).stem for p in _TTS]}}
