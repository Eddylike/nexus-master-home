"""LT-03: Barge-in - KI-Ausgabe abbrechen, sobald VAD Nutzersprache erkennt."""
from __future__ import annotations

_STATES = ("idle", "listening", "thinking", "speaking")


class BargeInController:
    """Zustaende: "idle", "listening", "thinking", "speaking"."""

    def __init__(self, min_speech_ms: int = 250, frame_ms: int = 30):
        if frame_ms <= 0 or min_speech_ms < 0:
            raise ValueError("frame_ms > 0 und min_speech_ms >= 0 erforderlich")
        self.min_speech_ms = int(min_speech_ms)
        self.frame_ms = int(frame_ms)
        self._state = "idle"
        self._speech_ms = 0

    @property
    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        """Unbekannter Zustand -> ValueError. Setzt Sprachzaehler zurueck."""
        if state not in _STATES:
            raise ValueError(f"unbekannter Zustand: {state!r}")
        self._state = state
        self._speech_ms = 0

    def on_vad(self, is_speech: bool) -> bool:
        """Pro Frame aufrufen. Nur im Zustand "speaking" oder "thinking": ununterbrochene Sprache
        >= min_speech_ms -> True (Barge-in), Zustand -> "listening", Zaehler reset.
        Nicht-Sprache setzt Zaehler zurueck. Sonst False."""
        if not is_speech:
            self._speech_ms = 0
            return False
        if self._state not in ("speaking", "thinking"):
            self._speech_ms = 0
            return False
        self._speech_ms += self.frame_ms
        if self._speech_ms >= self.min_speech_ms:
            self._state = "listening"
            self._speech_ms = 0
            return True
        return False
