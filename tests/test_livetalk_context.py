from nexus.livetalk.context import build_messages, append_turn

def test_build():
    conv = [{"role": "user", "content": "a"}, {"role": "tool", "content": "x"},
            {"role": "assistant", "text": "b"}, {"role": "user", "content": ""}]
    m = build_messages(conv, "SYS")
    assert m == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "a"},
                 {"role": "assistant", "content": "b"}]

def test_max_turns():
    conv = [{"role": "user", "content": str(i)} for i in range(30)]
    m = build_messages(conv, "S", max_turns=5)
    assert len(m) == 6 and m[-1]["content"] == "29"

def test_append():
    c = []
    assert append_turn(c, "hi", "") is c and len(c) == 1
    append_turn(c, "x", "y")
    assert c[-1] == {"role": "assistant", "content": "y", "source": "livetalk"}
