import json, pytest
from nexus.livetalk import protocol as p

def test_encode_decode():
    s = p.encode("token", text="Hallo")
    assert json.loads(s) == {"type": "token", "text": "Hallo"}
    assert p.decode(s)["text"] == "Hallo"

def test_unknown_type():
    with pytest.raises(ValueError): p.encode("evil")
    with pytest.raises(ValueError): p.decode('{"type":"evil"}')
    with pytest.raises(ValueError): p.decode('[1]')
    with pytest.raises(ValueError): p.decode('{"x":1}')

def test_audio_roundtrip():
    f = p.pack_audio(7, b"\x01\x02")
    assert f[:4] == (7).to_bytes(4, "big")
    assert p.unpack_audio(f) == (7, b"\x01\x02")
    with pytest.raises(ValueError): p.unpack_audio(b"\x00")


def test_phone_bridge_types():
    s = p.encode("phone_cmd", cmd="get_notifications")
    assert p.decode(s)["cmd"] == "get_notifications"
    n = p.encode("phone_notification", event="posted", data={"title": "Anna"})
    assert p.decode(n)["data"]["title"] == "Anna"
    f = p.encode("phone_file", kind="image", name="foto.png")
    assert p.decode(f)["kind"] == "image"
    r = p.encode("phone_result", cmd="confirm_send", ok=True)
    assert p.decode(r)["ok"] is True
