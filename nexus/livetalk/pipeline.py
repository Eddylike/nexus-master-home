"""LT-04: Ein Gespraechszug STT -> LLM (Stream) -> satzweise TTS, mit Abbruch und Latenzmessung.
Modelle werden injiziert (in Tests gemockt): stt(pcm)->str, llm_stream(messages)->Iterable[str],
tts(text)->bytes, send(dict)."""
from __future__ import annotations
import time
from typing import Callable, Iterable

from nexus.livetalk.sentences import split_stream


def run_turn(pcm: bytes, history: list[dict], stt: Callable[[bytes], str],
             llm_stream: Callable[[list[dict]], Iterable[str]], tts: Callable[[str], bytes],
             send: Callable[[dict], None], cancelled: Callable[[], bool] = lambda: False,
             clock: Callable[[], float] | None = None) -> dict:
    """1) text=stt(pcm); leer -> send({"type":"transcript","text":""}) und
       {"ok":False,"reason":"empty"} zurueck.
    2) send transcript; messages = history + [{"role":"user","content":text}].
    3) Tokens via nexus.livetalk.sentences.split_stream buendeln; je Token send({"type":"token","text":t});
       je Satz send({"type":"tts_chunk","text":satz,"audio":tts(satz)}).
    4) cancelled() vor jedem Token pruefen: True -> stoppen, send({"type":"barge_in"}).
    Rueckgabe {"ok":True,"user":text,"reply":gesprochener Text,"interrupted":bool,
    "first_audio_ms":int|None}. history um user + assistant (falls reply) erweitern."""
    clk = clock or time.monotonic
    t0 = clk()
    text = (stt(pcm) or "").strip()
    if not text:
        send({"type": "transcript", "text": ""})
        return {"ok": False, "reason": "empty"}
    send({"type": "transcript", "text": text})
    messages = list(history) + [{"role": "user", "content": text}]
    state = {"interrupted": False}

    def tokens():
        for tok in llm_stream(messages):
            if cancelled():
                state["interrupted"] = True
                send({"type": "barge_in"})
                return
            send({"type": "token", "text": tok})
            yield tok

    spoken: list[str] = []
    first_audio_ms = None
    for satz in split_stream(tokens()):
        if state["interrupted"]:
            break  # nach Barge-in keinen Rest mehr sprechen
        audio = tts(satz)
        if first_audio_ms is None:
            first_audio_ms = max(0, int(round((clk() - t0) * 1000)))
        send({"type": "tts_chunk", "text": satz, "audio": audio})
        spoken.append(satz)
    reply = " ".join(spoken)
    history.append({"role": "user", "content": text})
    if reply:
        history.append({"role": "assistant", "content": reply})
    return {"ok": True, "user": text, "reply": reply, "interrupted": state["interrupted"],
            "first_audio_ms": first_audio_ms}
