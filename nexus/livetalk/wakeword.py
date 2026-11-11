"""LT-05: Hey-Nexus-Logik (Ueberholung): Textmatch auf Erkenner-Ergebnis + VAD-Gate + Cooldown + Empfindlichkeit."""
from __future__ import annotations

WAKE_PHRASES = ("hey nexus", "hallo nexus", "hi nexus")


def _norm(word: str) -> str:
    return "".join(ch for ch in str(word or "").lower() if ch.isalnum())


class WakeGate:
    def __init__(self, sensitivity: float = 0.5, cooldown_s: float = 3.0, allow_bare: bool = False):
        """sensitivity 0..1 (ausserhalb -> ValueError). Mindestkonfidenz = 1 - sensitivity."""
        s = float(sensitivity)
        if not 0.0 <= s <= 1.0:
            raise ValueError("sensitivity muss zwischen 0 und 1 liegen")
        self.sensitivity = s
        self.min_conf = round(1.0 - s, 6)
        self.cooldown_s = float(cooldown_s)
        self.allow_bare = bool(allow_bare)
        self._last: float | None = None
        self._phrases = [tuple(p.split()) for p in WAKE_PHRASES]

    def check(self, words: list[dict], speech: bool, now: float) -> bool:
        """words: Vosk-Ergebnisse [{"word":..,"conf":..}]. False wenn speech False (VAD-Gate)
        oder innerhalb cooldown_s seit letzter Ausloesung. True wenn eine WAKE_PHRASE als
        aufeinanderfolgende Woerter vorkommt (Gross/Klein egal) und alle ihre conf >= Mindestkonfidenz;
        einzelnes "nexus" nur wenn allow_bare. Bei True Ausloesezeit merken."""
        if not speech:
            return False
        if self._last is not None and now - self._last < self.cooldown_s:
            return False
        seq = [(_norm(w.get("word")), float(w.get("conf", 1.0) or 0.0)) for w in (words or []) if isinstance(w, dict)]
        hit = False
        for phrase in self._phrases:
            n = len(phrase)
            for i in range(len(seq) - n + 1):
                win = seq[i:i + n]
                if tuple(w for w, _ in win) == phrase and all(c >= self.min_conf for _, c in win):
                    hit = True
                    break
            if hit:
                break
        if not hit and self.allow_bare:
            hit = any(w == "nexus" and c >= self.min_conf for w, c in seq)
        if hit:
            self._last = now
        return hit
