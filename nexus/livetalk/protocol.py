"""LT-01: WebSocket-Protokoll Live Talk (JSON-Steuer + Binaer-Audio)."""
from __future__ import annotations
from json import dumps, loads, JSONDecodeError

MSG_TYPES = ("start", "stop", "audio", "transcript", "token", "tts_chunk", "barge_in", "state", "error",
             # LT-08 (10.11.2026): Sitzung, Agent, Wiedergabe, Messwerte, Texteingabe, Keepalive
             "hello", "ready", "loaded", "agent", "tts_end", "playback", "metrics", "text", "vad", "ping", "pong",
             # App 1.1.0: Handy-Bruecke (Benachrichtigungen, Dateien, Kommandos)
             "phone_notification", "phone_file", "phone_cmd", "phone_result")


def encode(msg_type: str, **payload) -> str:
    if msg_type not in MSG_TYPES:
        raise ValueError("Unbekannter Typ")
    return dumps({"type": msg_type, **payload}, ensure_ascii=False)


def decode(raw: str) -> dict:
    try:
        obj = loads(raw)
    except JSONDecodeError:
        raise ValueError("Kein gültiges JSON")
    if not isinstance(obj, dict):
        raise ValueError("Kein Objekt")
    if "type" not in obj or obj["type"] not in MSG_TYPES:
        raise ValueError("Fehlender oder unbekannter type")
    return obj


def pack_audio(seq: int, pcm: bytes) -> bytes:
    return seq.to_bytes(4, byteorder="big", signed=False) + pcm


def unpack_audio(frame: bytes) -> tuple[int, bytes]:
    if len(frame) < 4:
        raise ValueError("Frame zu kurz")
    seq = int.from_bytes(frame[0:4], byteorder="big", signed=False)
    return seq, frame[4:]
