"""LT-08: Minimaler WebSocket-Server (RFC 6455) direkt im bestehenden NEXUS-http.server.

Live Talk laeuft ueber dieselben Ports wie der Core (8299 ws, 8443 wss): kein zweiter Dienst, kein zweites
Zertifikat; der Mobile-Browser bekommt das Mikrofon im sicheren Kontext (https/wss :8443).
Alle Socket-Zugriffe (lesen UND schreiben) passieren in genau einem Thread (sicher auch fuer TLS-Sockets);
andere Threads legen ausgehende Nachrichten nur in eine Warteschlange.
"""
from __future__ import annotations

import base64
import hashlib
import queue
import select
import socket
import struct
import time

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 2 * 1024 * 1024
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class WSClosed(Exception):
    pass


class WSProtocolError(Exception):
    pass


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1((key.strip() + GUID).encode("ascii")).digest()).decode("ascii")


def is_upgrade(headers) -> bool:
    return ("websocket" in str(headers.get("Upgrade") or "").lower()
            and "upgrade" in str(headers.get("Connection") or "").lower()
            and bool(headers.get("Sec-WebSocket-Key")))


def _xor(data: bytes, mask: bytes) -> bytes:
    if not data:
        return data
    n = len(data)
    k = int.from_bytes((mask * (n // 4 + 1))[:n], "big")
    return (int.from_bytes(data, "big") ^ k).to_bytes(n, "big")


def encode_frame(opcode: int, payload: bytes, mask: bytes | None = None, fin: bool = True) -> bytes:
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    mbit = 0x80 if mask else 0
    if n < 126:
        head += bytes([mbit | n])
    elif n < 65536:
        head += bytes([mbit | 126]) + struct.pack(">H", n)
    else:
        head += bytes([mbit | 127]) + struct.pack(">Q", n)
    return head + mask + _xor(payload, mask) if mask else head + payload


def parse_frames(buf: bytes, require_mask: bool = True):
    """Zerlegt einen Puffer in vollstaendige Frames. Rueckgabe ([(fin, opcode, payload)], rest)."""
    frames, i = [], 0
    while True:
        if len(buf) - i < 2:
            break
        b1, b2 = buf[i], buf[i + 1]
        fin, op, masked, n = bool(b1 & 0x80), b1 & 0x0F, bool(b2 & 0x80), b2 & 0x7F
        j = i + 2
        if n == 126:
            if len(buf) - j < 2:
                break
            n = struct.unpack(">H", buf[j:j + 2])[0]; j += 2
        elif n == 127:
            if len(buf) - j < 8:
                break
            n = struct.unpack(">Q", buf[j:j + 8])[0]; j += 8
        if n > MAX_FRAME:
            raise WSProtocolError("frame_too_big")
        if require_mask and not masked:
            raise WSProtocolError("unmasked_client_frame")
        mask = b""
        if masked:
            if len(buf) - j < 4:
                break
            mask = buf[j:j + 4]; j += 4
        if len(buf) - j < n:
            break
        payload = buf[j:j + n]
        frames.append((fin, op, _xor(payload, mask) if masked else payload))
        i = j + n
    return frames, buf[i:]


class WSConnection:
    def __init__(self, sock):
        self.sock = sock
        self.outq: "queue.Queue[bytes]" = queue.Queue()
        self.closed = False
        self._buf = b""
        self._parts: list[bytes] = []
        self._kind = None
        self.last_rx = time.monotonic()

    @classmethod
    def handshake(cls, handler) -> "WSConnection":
        key = handler.headers.get("Sec-WebSocket-Key")
        # Roh geschrieben: der Core-Handler spricht HTTP/1.0, WebSocket verlangt "HTTP/1.1 101".
        resp = ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n").encode("ascii")
        try:
            handler.wfile.flush()
        except Exception:
            pass
        handler.connection.sendall(resp)
        try:
            handler.log_request(101)
        except Exception:
            pass
        handler.close_connection = True
        return cls(handler.connection)

    # ---- ausgehend (thread-sicher: nur Warteschlange)
    def send_text(self, text: str) -> None:
        if not self.closed:
            self.outq.put(encode_frame(OP_TEXT, text.encode("utf-8")))

    def send_binary(self, data: bytes) -> None:
        if not self.closed:
            self.outq.put(encode_frame(OP_BIN, data))

    def close(self, code: int = 1000) -> None:
        if not self.closed:
            self.outq.put(encode_frame(OP_CLOSE, struct.pack(">H", code)))
            self.outq.put(b"")  # Marker: danach schliessen
            self.closed = True

    # ---- IO-Schleife (nur im Verbindungs-Thread)
    def _flush(self) -> bool:
        while True:
            try:
                data = self.outq.get_nowait()
            except queue.Empty:
                return True
            if data == b"":
                return False
            try:
                self.sock.sendall(data)
            except OSError:
                return False

    def serve(self, on_text, on_binary, on_tick=None, idle_timeout: float = 45.0, tick: float = 0.01) -> str:
        """Blockiert bis Verbindungsende. Rueckgabe: Grund."""
        try:
            self.sock.setblocking(True)
            self.sock.settimeout(5.0)
        except Exception:
            pass
        reason = "eof"
        try:
            while True:
                if not self._flush():
                    reason = "closed_by_server"
                    break
                pending = 0
                try:
                    pending = self.sock.pending() if hasattr(self.sock, "pending") else 0
                except Exception:
                    pending = 0
                readable = bool(pending)
                if not readable:
                    r, _, _ = select.select([self.sock], [], [], tick)
                    readable = bool(r)
                if readable:
                    try:
                        data = self.sock.recv(65536)
                    except (socket.timeout, TimeoutError):
                        data = None
                    except OSError:
                        reason = "io_error"
                        break
                    if data == b"":
                        reason = "eof"
                        break
                    if data:
                        self.last_rx = time.monotonic()
                        self._buf += data
                        frames, self._buf = parse_frames(self._buf)
                        for fin, op, payload in frames:
                            if not self._handle(fin, op, payload, on_text, on_binary):
                                return "client_close"
                if on_tick:
                    on_tick()
                if time.monotonic() - self.last_rx > idle_timeout:
                    reason = "idle_timeout"
                    break
        except WSProtocolError as e:
            reason = "protocol:" + str(e)
            try:
                self.sock.sendall(encode_frame(OP_CLOSE, struct.pack(">H", 1002)))
            except OSError:
                pass
        finally:
            self.closed = True
            self._flush()
        return reason

    def _handle(self, fin, op, payload, on_text, on_binary) -> bool:
        if op == OP_PING:
            self.outq.put(encode_frame(OP_PONG, payload))
            return True
        if op == OP_PONG:
            return True
        if op == OP_CLOSE:
            try:
                self.sock.sendall(encode_frame(OP_CLOSE, payload[:2] or struct.pack(">H", 1000)))
            except OSError:
                pass
            return False
        if op in (OP_TEXT, OP_BIN):
            self._kind, self._parts = op, [payload]
        elif op == OP_CONT and self._kind is not None:
            self._parts.append(payload)
        else:
            raise WSProtocolError("bad_opcode")
        if fin:
            data, kind = b"".join(self._parts), self._kind
            self._parts, self._kind = [], None
            if kind == OP_TEXT:
                on_text(data.decode("utf-8", errors="replace"))
            else:
                on_binary(data)
        return True
