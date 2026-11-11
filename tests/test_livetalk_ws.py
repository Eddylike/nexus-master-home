import os, struct, pytest
from nexus.livetalk import ws


def test_accept_key_rfc_example():
    assert ws.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="


def test_masked_roundtrip_and_partial():
    payload = os.urandom(70000)
    f1 = ws.encode_frame(ws.OP_BIN, payload, mask=b"\x01\x02\x03\x04")
    f2 = ws.encode_frame(ws.OP_TEXT, "Hallo".encode(), mask=b"\xaa\xbb\xcc\xdd")
    frames, rest = ws.parse_frames(f1[:1000])
    assert frames == [] and rest == f1[:1000]
    frames, rest = ws.parse_frames(f1 + f2)
    assert rest == b"" and frames == [(True, ws.OP_BIN, payload), (True, ws.OP_TEXT, b"Hallo")]


def test_unmasked_and_too_big_rejected():
    with pytest.raises(ws.WSProtocolError):
        ws.parse_frames(ws.encode_frame(ws.OP_TEXT, b"x"))
    big = bytes([0x82, 0xFF]) + struct.pack(">Q", ws.MAX_FRAME + 1)
    with pytest.raises(ws.WSProtocolError):
        ws.parse_frames(big)


def test_is_upgrade():
    assert ws.is_upgrade({"Upgrade": "websocket", "Connection": "keep-alive, Upgrade", "Sec-WebSocket-Key": "k"})
    assert not ws.is_upgrade({"Connection": "Upgrade", "Sec-WebSocket-Key": "k"})
