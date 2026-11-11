"""LT-08/LT-09: Live-Talk-Sitzung (WebSocket) im bestehenden NEXUS-Core.

Endpunkt: GET /ws/livetalk (ws://<PC>:8299, wss://<PC>:8443) - Upgrade im vorhandenen http.server-Handler.
Rauf: Binaerframes = 4 Byte Sequenz (BE) + PCM16 mono 16 kHz (protocol.pack_audio), JSON-Steuernachrichten.
Runter: JSON (state/transcript/token/agent/tts_chunk/tts_end/barge_in/metrics/error) + Binaerframes
        b"A" + turn(4) + index(4) + samplerate(4) + PCM16 mono (Piper).
Ablauf: Silero VAD (kontinuierlich) -> Satzende nach Stille -> faster-whisper (RAM) -> Agenten-Routing (agents.py)
        -> Ollama-Stream satzweise (sentences.split_stream) -> Piper -> Handy. Spricht Eddie dazwischen,
        bricht der Zug sofort ab (Barge-in, bargein.BargeInController) und sein neuer Satz wird verarbeitet.
Audio wird nur im RAM gehalten und nach dem Zug verworfen; der Gespraechskontext (nur Text) bleibt pro
conversation_id 60 Minuten im RAM (Reconnect ohne Kontextverlust).
"""
from __future__ import annotations

import json
import re
import struct
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable

from nexus.livetalk import auth, protocol
from nexus.livetalk.bargein import BargeInController
from nexus.livetalk.sentences import split_stream

ROOT = Path(__file__).resolve().parents[2]
METRICS_LOG = ROOT / "workspace" / "logs" / "livetalk_metrics.jsonl"
WINDOW = 512                 # Samples je VAD-Fenster (32 ms bei 16 kHz)
WIN_MS = 32
CONV_TTL = 3600
CONV_ID = re.compile(r"[A-Za-z0-9_-]{6,40}\Z")
_CONVS: dict[str, dict] = {}
_CONV_LOCK = threading.Lock()
ACTIVE: dict[str, dict] = {}


def is_allowed_client(ip: str) -> bool:
    """True nur fuer localhost, private LAN-Adressen und Tailscale."""
    return auth.is_allowed_client(ip)


def conversation(conv_id: str | None) -> tuple[str, list[dict]]:
    cid = conv_id if conv_id and CONV_ID.fullmatch(str(conv_id)) else "lt" + uuid.uuid4().hex[:12]
    now = time.time()
    with _CONV_LOCK:
        for k in [k for k, v in _CONVS.items() if now - v["ts"] > CONV_TTL]:
            _CONVS.pop(k, None)
        rec = _CONVS.setdefault(cid, {"history": [], "ts": now})
        rec["ts"] = now
        return cid, rec["history"]


def audio_frame(turn: int, idx: int, sr: int, pcm: bytes) -> bytes:
    return b"A" + struct.pack(">III", turn, idx, sr) + pcm


def first_clause_split(sentences, min_chars: int = 40):
    """Nur der ERSTE Satz: ist er lang, am ersten Komma (nach >= 18 Zeichen) teilen -> erster Ton frueher.
    Alle weiteren Saetze bleiben ganz (beste Satzmelodie)."""
    first = True
    for s in sentences:
        if first and min_chars and len(s) >= min_chars:
            i = s.find(", ", 18)
            if 0 < i < len(s) - 12:
                yield s[:i + 1]
                yield s[i + 2:]
                first = False
                continue
        first = False
        yield s


def _log_metrics(rec: dict) -> None:
    try:
        METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with METRICS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


class Session:
    """Ein Live-Talk-Client: VAD-Puffer, BargeInController, Verlauf (Text) aus der Konversation."""

    def __init__(self, conversation_id: str | None, send, send_bin: Callable[[bytes], None] | None = None,
                 engine=None, authed: bool = False, cfg: dict | None = None, vad=None,
                 stt: Callable | None = None, synth: Callable | None = None, reply: Callable | None = None,
                 route: Callable | None = None, clock: Callable[[], float] = time.monotonic, warm: bool = True,
                 ip: str = ""):
        from nexus.livetalk import engines
        self.cfg = cfg or engines.load_cfg()
        self._send_json = send
        self._send_bin = send_bin or (lambda b: None)
        self.engine = engine
        self.authed = authed
        self.ip = ip
        self.clock = clock
        self._vad = vad
        self._stt = stt or (lambda pcm: engines.transcribe(pcm, self.cfg))
        self._synth = synth or (lambda text: engines.synth_stream(text, self.cfg))
        if reply is None or route is None:
            from nexus.livetalk import agents
            def _reply_default(agent, mode, text, history, call_id, meta):
                meta = meta if meta is not None else {}
                meta.setdefault("phone", self.phone)
                meta.setdefault("from_phone", self.from_phone)
                return agents.reply_stream(self.engine, agent, mode, text, history, call_id, meta)

            def _route_default(text):
                return agents.route(text, from_phone=self.from_phone)

            reply = reply or _reply_default
            route = route or _route_default
        self._reply = reply
        self._route = route
        self._warm = warm
        self.conv_id, self.history = conversation(conversation_id)
        self.call_id = ("LT" + self.conv_id)[:40]
        self.ctrl = BargeInController(min_speech_ms=int(self.cfg["bargein_ms"]), frame_ms=WIN_MS)
        self.active = False
        self._lock = threading.RLock()
        self._pending = b""
        self._preroll: deque = deque(maxlen=10)
        self._in_utt = False
        self._utt = bytearray()
        self._speech_ms = self._silence_ms = self._run = 0
        self._utt_start = 0.0
        self.turn_id = 0
        self._cancel = threading.Event()
        self._turn_thread: threading.Thread | None = None
        self._turn_busy = False
        self._last_pcm = b""
        self._audio_sent_any = False
        self._speak_until = 0.0
        self._playback_done = True
        self.seq_last = -1
        self.frames_in = 0
        self.stats = {"turns": 0, "barge_ins": 0}
        self._merged: set[int] = set()
        self._gen = 0          # zaehlt Sprachfenster der laufenden Aeusserung (fuer spekulative STT)
        self._spec: dict | None = None
        self.from_phone = True
        self.client_name = ""
        self._pending_announce: str | None = None
        self._announced_files: set[str] = set()
        from nexus.livetalk.phone_link import PhoneLink
        self.phone = PhoneLink(send_cmd=self._phone_cmd)

    def _phone_cmd(self, **payload) -> None:
        self.send("phone_cmd", **payload)

    def on_phone_file(self, rec: dict) -> None:
        """Inbox-Upload oder WS phone_file: 'Foto erhalten' sagen, Bild merken."""
        key = str((rec or {}).get("saved_as") or (rec or {}).get("path") or (rec or {}).get("name") or "")
        if key and key in self._announced_files:
            return
        spoken = self.phone.remember_file(rec or {})
        if not spoken:
            return
        if key:
            self._announced_files.add(key)
        self.announce(spoken)

    def announce(self, text: str) -> None:
        text = (text or "").strip()
        if not text or not self.active:
            return
        if self._turn_busy or self.ctrl.state in ("thinking", "speaking"):
            self._pending_announce = text
            return
        self._start_turn(b"", text=text, announce=True)

    # ------------------------------------------------------------ senden
    def send(self, msg_type: str, **payload) -> None:
        try:
            self._send_json(protocol.encode(msg_type, **payload))
        except Exception:
            pass

    def _state(self, state: str) -> None:
        with self._lock:
            if state in ("idle", "listening", "thinking", "speaking"):
                self.ctrl.set_state(state)
        self.send("state", state=state, turn=self.turn_id)

    # ------------------------------------------------------------ eingehend
    def on_message(self, raw: str) -> None:
        """Steuernachricht (protocol.decode): start/stop/text/barge_in/playback/ping."""
        try:
            msg = protocol.decode(raw)
        except ValueError as e:
            return self.send("error", error="BAD_MESSAGE", detail=str(e))
        t = msg["type"]
        if t == "ping":
            return self.send("pong", ts=msg.get("ts"))
        if t == "start":
            if not self.authed:
                kind = auth.check_token(msg.get("token"))
                if not kind:
                    self.send("error", error="AUTH_REQUIRED", fatal=True)
                    raise PermissionError("AUTH_REQUIRED")
                self.authed = True
            if msg.get("conversation_id") and msg.get("conversation_id") != self.conv_id:
                self.conv_id, self.history = conversation(msg.get("conversation_id"))
                self.call_id = ("LT" + self.conv_id)[:40]
            self.active = True
            self.client_name = str(msg.get("client") or "")
            src = str(msg.get("source") or "").lower()
            if src in ("phone", "handy", "android", "mobile"):
                self.from_phone = True
            elif src in ("web", "pc", "desktop"):
                self.from_phone = False
            elif "mobile" in self.client_name.lower() or "android" in self.client_name.lower():
                self.from_phone = True
            ACTIVE[self.conv_id] = {"ip": self.ip, "since": time.time(), "session": self}
            try:
                from nexus.mobile_inbox import on_upload
                on_upload(self.on_phone_file)
            except Exception:
                pass
            self.send("ready", conversation_id=self.conv_id, turns=len(self.history) // 2,
                      voice=Path(self.cfg["piper_model"]).stem, stt=self.cfg["stt_model"],
                      end_silence_ms=self.cfg["end_silence_ms"], agents=["NEXUS", "Hermes", "NEXUS GPT", "Aether"])
            if self._warm:
                threading.Thread(target=self._warmup, name="livetalk-warm", daemon=True).start()
            else:
                self._state("listening")
            return
        if not self.authed:
            return self.send("error", error="AUTH_REQUIRED")
        if t == "stop":
            self.active = False
            self._cancel_turn("stop")
            self._reset_utt()
            return self._state("idle")
        if t == "barge_in":  # Tippen auf den Orb: sofort still
            return self._barge_in("manual")
        if t == "playback":
            if msg.get("state") == "ended" and int(msg.get("turn") or 0) == self.turn_id:
                self._playback_done = True
            return
        if t == "text":  # Texteingabe (ohne Mikrofon) - gleicher Ablauf ohne STT
            text = str(msg.get("text") or "").strip()[:600]
            if text:
                self._start_turn(b"", text=text)
            return
        if t == "phone_notification":
            data = msg.get("data") or msg.get("notification") or msg
            ev = msg.get("event") or "posted"
            if ev != "removed":
                self.phone.remember_notification(data if isinstance(data, dict) else {})
            return
        if t == "phone_file":
            rec = msg.get("data") or msg.get("file") or msg
            self.on_phone_file(rec if isinstance(rec, dict) else {})
            return
        if t == "phone_result":
            self.phone.on_result(msg)
            return

    def _warmup(self) -> None:
        self._state("loading")
        info = {}
        try:
            from nexus.livetalk import engines
            info = engines.warm_all(self.cfg)
            if self._vad is None:
                self._vad = engines.get_vad_factory(self.cfg)()
        except Exception as e:
            self.send("error", error="ENGINE_LOAD_FAILED", detail=f"{type(e).__name__}: {e}"[:200])
        try:  # Modell im Hintergrund vorwaermen (wie beim NEXUSPhone-Anruf), keine Antwort
            if self.engine is not None:
                from nexus.voice import call
                call.warm(self.engine, {"call_id": self.call_id, "target": "nexus"})
        except Exception:
            pass
        self.send("loaded", **info)
        if self.active:
            self._state("listening")

    def on_audio(self, frame: bytes) -> None:
        """Audioframe (protocol.unpack_audio) -> VAD -> Barge-in / Turn-Ende -> Zug."""
        if not self.authed or not self.active:
            return
        try:
            seq, pcm = protocol.unpack_audio(frame)
        except ValueError:
            return
        self.seq_last = seq
        self.frames_in += 1
        if self._vad is None:
            return  # Modelle laden noch
        self._pending += pcm
        while len(self._pending) >= WINDOW * 2:
            w, self._pending = self._pending[:WINDOW * 2], self._pending[WINDOW * 2:]
            self._window(w)

    def _window(self, w: bytes) -> None:
        import numpy as np
        prob = self._vad.process(np.frombuffer(w, dtype=np.int16).astype(np.float32) / 32768.0)
        state = self.ctrl.state
        thr = float(self.cfg["vad_threshold_speaking"] if state == "speaking" else self.cfg["vad_threshold"])
        speech = prob >= thr
        if not self._in_utt:
            self._preroll.append(w)
            self._run = self._run + 1 if speech else 0
            if self._run >= 2:
                self._in_utt = True
                self._utt = bytearray(b"".join(self._preroll))
                self._speech_ms, self._silence_ms = self._run * WIN_MS, 0
                self._utt_start = self.clock()
                if state in ("idle", "listening"):
                    self.send("vad", speech=True)
        else:
            self._utt += w
            if speech:
                self._speech_ms += WIN_MS
                self._silence_ms = 0
                self._gen += 1
            else:
                self._silence_ms += WIN_MS
                spec_ms = int(self.cfg.get("spec_stt_ms") or 0)
                if (spec_ms and self._silence_ms >= spec_ms and state in ("idle", "listening")
                        and self._speech_ms >= int(self.cfg["min_speech_ms"])
                        and (self._spec is None or self._spec["gen"] != self._gen)):
                    self._start_spec(bytes(self._utt))
        if state in ("thinking", "speaking"):
            # Waehrend STT (thinking, noch kein Ton): Echo/eigene Stimme nicht als neuen Zug werten.
            if state == "thinking" and not self._audio_sent_any:
                hit = False
            else:
                with self._lock:
                    hit = self.ctrl.on_vad(speech)  # setzt bei Treffer selbst auf "listening"
            if hit:
                self._barge_in("voice", was=state)
        if self._in_utt:
            state = self.ctrl.state
            too_long = len(self._utt) >= int(self.cfg["max_utterance_s"]) * 32000
            if state in ("idle", "listening") and (self._silence_ms >= int(self.cfg["end_silence_ms"]) or too_long):
                pcm = bytes(self._utt[: max(0, len(self._utt) - max(0, self._silence_ms - 200) * 32)])
                ok = self._speech_ms >= int(self.cfg["min_speech_ms"])
                spec = self._spec if (self._spec and self._spec["gen"] == self._gen and not too_long) else None
                self._reset_utt()
                if ok:
                    self._start_turn(pcm, spec=spec)
            elif state in ("thinking", "speaking") and self._silence_ms >= int(self.cfg["end_silence_ms"]):
                self._reset_utt()  # kurzes Geraeusch ohne Barge-in: verwerfen

    def _start_spec(self, pcm: bytes) -> None:
        """Spracherkennung schon in der Sprechpause starten; spricht Eddie weiter, wird das Ergebnis verworfen."""
        spec = {"gen": self._gen, "event": threading.Event(), "result": None, "t0": self.clock()}
        self._spec = spec

        def run():
            try:
                spec["result"] = self._stt(pcm)
            except Exception as e:
                spec["result"] = {"text": "", "error": f"{type(e).__name__}"}
            finally:
                spec["event"].set()
        threading.Thread(target=run, name="livetalk-spec-stt", daemon=True).start()

    def _reset_utt(self) -> None:
        self._spec = None
        self._in_utt = False
        self._utt = bytearray()
        self._speech_ms = self._silence_ms = self._run = 0

    def on_tick(self) -> None:
        if self.ctrl.state == "speaking" and not self._turn_busy:
            if self._playback_done or self.clock() > self._speak_until + 1.5:
                self._state("listening")

    # ------------------------------------------------------------ Zuege
    def _barge_in(self, reason: str, was: str | None = None) -> None:
        with self._lock:
            was = was or self.ctrl.state
            if was not in ("thinking", "speaking") and not self._turn_busy:
                return
            self._cancel.set()
            self.ctrl.set_state("listening")
        self.stats["barge_ins"] += 1
        # In der Denkphase weitergesprochen: vorherigen Satz mit dem neuen zusammenfuehren
        carry = self._last_pcm if (was == "thinking" and not self._audio_sent_any and reason == "voice") else b""
        if carry:
            self._utt = bytearray(carry) + self._utt
            self._merged.add(self.turn_id)
        self.send("barge_in", turn=self.turn_id, reason=reason, was=was,
                  ms_since_speech=int((self.clock() - self._utt_start) * 1000) if self._in_utt else None)
        self.send("state", state="listening", turn=self.turn_id)

    def _cancel_turn(self, reason: str) -> None:
        self._cancel.set()

    def _start_turn(self, pcm: bytes, text: str | None = None, spec: dict | None = None, announce: bool = False) -> None:
        th = self._turn_thread
        if th is not None and th.is_alive():
            self._cancel.set()  # alter Zug beendet sich selbst (nicht im IO-Thread warten)
        with self._lock:
            self.turn_id += 1
            self._cancel = threading.Event()
            self._turn_busy = True
            self._audio_sent_any = False
            self._playback_done = False
            self._last_pcm = pcm
            self.ctrl.set_state("thinking")
        tid, cancel, t_end = self.turn_id, self._cancel, self.clock()
        self.send("state", state="thinking", turn=tid)
        self._turn_thread = threading.Thread(target=self._turn, args=(tid, pcm, text, cancel, t_end, spec, announce),
                                             name=f"livetalk-turn-{tid}", daemon=True)
        self._turn_thread.start()

    def _turn(self, tid: int, pcm: bytes, text: str | None, cancel: threading.Event, t_end: float,
              spec: dict | None = None, announce: bool = False) -> None:
        m = {"turn": tid, "conv": self.conv_id, "ts": time.time(), "endpoint_ms": int(self.cfg["end_silence_ms"]),
             "audio_in_s": round(len(pcm) / 32000, 2)}
        spoken: list[str] = []
        tokens_gen = None
        try:
            if text is None:
                r = None
                if spec is not None and spec["event"].wait(30.0):
                    r = spec["result"]
                    m["stt_spec"] = True
                    m["stt_wait_ms"] = int((self.clock() - t_end) * 1000)
                if r is None:
                    r = self._stt(pcm)
                text = (r.get("text") or "").strip()
                m["stt_ms"] = r.get("stt_ms")
                m["stt_model"] = r.get("model")
                m["stt_confident"] = r.get("confident")
                m["stt_no_speech"] = r.get("no_speech")
                m["stt_logprob"] = r.get("avg_logprob")
                if r.get("low_confidence"):
                    m["low_confidence"] = True
            else:
                m["stt_ms"] = 0
                m["text_input"] = True
            pcm = b""  # Audio sofort verwerfen
            if cancel.is_set():
                m["interrupted"] = "during_stt"
                return
            self.send("transcript", text=text, turn=tid, stt_ms=m["stt_ms"])
            m["heard"] = (text or "")[:200]
            meta: dict = {}
            if not text:
                m["empty"] = True
                # Nur nachfragen, wenn wirklich gesprochen wurde (kein Rauschen-Kurzburst)
                if float(m.get("audio_in_s") or 0) < 0.7:
                    return
                clarify = "Ich habe dich nicht verstanden. Sag es bitte nochmal."
                agent, mode = "NEXUS", "clarify"
                tokens_gen = iter((clarify,))
            elif m.get("low_confidence") and not m.get("text_input"):
                heard = text if len(text) <= 80 else text[:77] + "..."
                clarify = f"Ich habe das nicht genau verstanden. Ich habe verstanden: {heard}. Sag es bitte nochmal."
                agent, mode = "NEXUS", "clarify"
                tokens_gen = iter((clarify,))
            elif announce:
                agent, mode = "NEXUS", "announce"
                tokens_gen = iter((text,))
                meta["verified"] = True
            else:
                agent, mode = self._route(text)
                tokens_gen = self._reply(agent, mode, text, list(self.history), self.call_id, meta)
            m.update(agent=agent, mode=mode)
            self.send("agent", agent=agent, mode=mode, turn=tid)

            def tokens():
                first = True
                for tok in tokens_gen:
                    if cancel.is_set():
                        return
                    if first:
                        m["first_token_ms"] = int((self.clock() - t_end) * 1000)
                        first = False
                    self.send("token", text=tok, turn=tid)
                    yield tok

            idx = 0
            total_audio = 0.0
            for sentence in first_clause_split(split_stream(tokens()), int(self.cfg.get("first_clause_chars") or 0)):
                if cancel.is_set():
                    break
                from nexus.livetalk.agents import guard_sentence
                sentence, stop = guard_sentence(sentence, meta)
                if "first_sentence_ms" not in m:
                    m["first_sentence_ms"] = int((self.clock() - t_end) * 1000)
                first_chunk = True
                t_s = self.clock()
                for sr, audio in self._synth(sentence):
                    if cancel.is_set():
                        break
                    if not self._audio_sent_any:
                        self._audio_sent_any = True
                        m["first_audio_ms"] = int((self.clock() - t_end) * 1000)
                        m["first_tts_ms"] = int((self.clock() - t_s) * 1000)
                        with self._lock:
                            if not cancel.is_set():
                                self.ctrl.set_state("speaking")
                                self._speak_until = self.clock()
                        self.send("state", state="speaking", turn=tid)
                    dur = len(audio) / 2 / sr
                    total_audio += dur
                    self._speak_until = max(self._speak_until, self.clock()) + dur
                    self.send("tts_chunk", turn=tid, index=idx, sr=sr, samples=len(audio) // 2,
                              text=sentence if first_chunk else "", agent=m.get("agent"))
                    self._send_bin(audio_frame(tid, idx, sr, audio))
                    first_chunk = False
                    idx += 1
                if cancel.is_set():
                    break
                spoken.append(sentence)
                if stop:
                    break
            m.update(model=meta.get("model"), guarded=meta.get("guarded"), audio_out_s=round(total_audio, 2),
                     sentences=len(spoken), error=meta.get("error"))
            if cancel.is_set():
                m["interrupted"] = "barge_in"
        except Exception as e:
            m["error"] = f"{type(e).__name__}: {e}"[:200]
            self.send("error", error="TURN_FAILED", detail=m["error"], turn=tid)
        finally:
            if tokens_gen is not None:
                try:
                    tokens_gen.close()  # bricht den Ollama-Stream ab (Verbindung zu)
                except Exception:
                    pass
            reply = " ".join(spoken).strip()
            if text and tid not in self._merged and not announce:  # zusammengefuehrte Zuege zaehlen nur einmal
                self.history.append({"role": "user", "content": text})
                if reply:
                    self.history.append({"role": "assistant", "content": reply, "agent": m.get("agent") or "NEXUS",
                                         "interrupted": bool(m.get("interrupted"))})
            elif announce and reply:
                self.history.append({"role": "assistant", "content": reply, "agent": "NEXUS", "announce": True})
                del self.history[:-40]
            m["reply_chars"] = len(reply)
            if reply:
                m["reply"] = reply[:200]
            m["total_ms"] = int((self.clock() - t_end) * 1000)
            self.stats["turns"] += 1
            back_to_listening = False
            with self._lock:
                if tid == self.turn_id:
                    self._turn_busy = False
                if tid == self.turn_id and self.ctrl.state == "thinking":
                    self.ctrl.set_state("listening")
                    back_to_listening = True
            self.send("tts_end", turn=tid, interrupted=bool(m.get("interrupted")), text=reply, agent=m.get("agent"))
            self.send("metrics", **{k: v for k, v in m.items() if k not in ("conv",)})
            if back_to_listening:
                self.send("state", state="listening", turn=tid)
            _log_metrics(m)
            pending = None
            with self._lock:
                if self._pending_announce and tid == self.turn_id and not self._turn_busy:
                    pending, self._pending_announce = self._pending_announce, None
            if pending:
                self.announce(pending)

    def close(self) -> None:
        self.active = False
        self._cancel.set()
        try:
            from nexus.mobile_inbox import off_upload
            off_upload(self.on_phone_file)
        except Exception:
            pass
        ACTIVE.pop(self.conv_id, None)


def handle_upgrade(handler, engine=None) -> None:
    """Im bestehenden http.server-Handler: GET /ws/livetalk -> WebSocket-Sitzung (blockiert bis Ende)."""
    from nexus.livetalk import ws
    ip = handler.client_address[0]
    if not auth.is_allowed_client(ip, handler.headers):
        body = b'{"success":false,"error":"NETWORK_NOT_ALLOWED"}'
        handler.send_response(403)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return
    if not ws.is_upgrade(handler.headers):
        body = b'{"success":false,"error":"WEBSOCKET_UPGRADE_REQUIRED"}'
        handler.send_response(426)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return
    conn = ws.WSConnection.handshake(handler)
    session = Session(None, conn.send_text, conn.send_binary, engine=engine,
                      authed=auth.is_loopback(ip, handler.headers), ip=ip)
    session.send("hello", protocol=2, auth_required=not session.authed, sample_rate_in=16000,
                 frame_samples=WINDOW)

    def on_text(raw):
        try:
            session.on_message(raw)
        except PermissionError:
            conn.close(4401)

    reason = "?"
    try:
        reason = conn.serve(on_text, session.on_audio, session.on_tick)
    finally:
        session.close()
        _log_metrics({"event": "disconnect", "reason": reason, "conv": session.conv_id, "ts": time.time(),
                      "frames_in": session.frames_in, **session.stats})


def status() -> dict:
    from nexus.livetalk import engines
    return {"success": True, "endpoint": "/ws/livetalk", "page": "/m/livetalk", "active_sessions": len(ACTIVE),
            "engines": engines.status()}
