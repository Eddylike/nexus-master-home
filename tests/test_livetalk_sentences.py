from nexus.livetalk.sentences import split_stream

def test_basic():
    toks = ["Hallo Eddie", ", wie geht", " es dir? ", "Mir geht es gut.", " Rest"]
    assert list(split_stream(toks)) == ["Hallo Eddie, wie geht es dir?", "Mir geht es gut.", "Rest"]

def test_abbrev_and_numbers():
    out = list(split_stream(["Das ist z. B. Version 3.5 von Qwen. Fertig ist es jetzt!"]))
    assert out == ["Das ist z. B. Version 3.5 von Qwen.", "Fertig ist es jetzt!"]

def test_short_merged_and_no_empty():
    out = list(split_stream(["Ja. ", "Das klappt sehr gut.", "  "]))
    assert out == ["Ja. Das klappt sehr gut."]
