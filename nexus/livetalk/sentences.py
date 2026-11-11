"""LT-02: LLM-Token-Stream satzweise fuer TTS buendeln (geringe Latenz)."""
from __future__ import annotations
from typing import Iterable, Iterator

_ENDS = ".!?:;"
_ABBREV = {"z", "b", "dr", "ca", "usw", "bzw", "etc", "nr", "vgl", "evtl", "ggf", "inkl", "max", "min",
           "mio", "mrd", "prof", "hr", "fr", "st", "str", "d", "h", "u", "a", "o", "s", "zb", "bsp", "tel"}


def _is_abbrev(buf: str, i: int) -> bool:
    """Punkt an Position i gehoert zu einer Abkuerzung (z. B., Dr., ca., einzelner Buchstabe)."""
    j = i
    while j > 0 and buf[j - 1].isalpha():
        j -= 1
    word = buf[j:i].lower()
    return bool(word) and (len(word) == 1 or word in _ABBREV)


def _split_point(buf: str, i: int, final: bool) -> bool:
    ch = buf[i]
    if ch == "\n":
        return True
    if ch not in _ENDS:
        return False
    nxt = buf[i + 1] if i + 1 < len(buf) else None
    if nxt is None:
        if not final:
            return False  # erst entscheiden, wenn das naechste Zeichen da ist (z. B. "3." + "5")
    elif not nxt.isspace():
        return False  # "3.5", "a.b", "?!" -> kein Satzende hier
    if ch == "." and _is_abbrev(buf, i):
        return False
    return True


def split_stream(tokens: Iterable[str], min_chars: int = 12) -> Iterator[str]:
    """Tokens sammeln, bei . ! ? : ; oder Zeilenumbruch einen gestrippten Satz liefern,
    sobald dieser >= min_chars ist (kurze Saetze mit naechstem zusammenfassen).
    Abkuerzungen wie "z. B.", "Dr.", "ca." und Zahlen wie "3.5" NICHT trennen. Rest am Ende liefern.
    Leere Saetze nie liefern."""
    buf = ""
    pos = 0  # ab hier noch nicht geprueft

    def drain(final: bool) -> Iterator[str]:
        nonlocal buf, pos
        i = pos
        while i < len(buf):
            if _split_point(buf, i, final):
                cand = buf[: i + 1].strip()
                if len(cand) >= min_chars:
                    yield cand
                    buf = buf[i + 1:]
                    i = 0
                    continue
            elif buf[i] in _ENDS and i + 1 >= len(buf) and not final:
                break  # Satzzeichen am Pufferende: auf naechstes Token warten
            i += 1
        pos = i

    for tok in tokens:
        if not tok:
            continue
        buf += tok
        yield from drain(False)
    yield from drain(True)
    rest = buf.strip()
    if rest:
        yield rest
