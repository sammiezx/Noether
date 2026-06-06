"""Text-to-speech via macOS `say`. Picks the highest-quality variant
available for the chosen voice. Supports both blocking (`speak`) and
non-blocking (`speak_async`) playback so the caller can interrupt
mid-utterance — used for barge-in detection.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache


@lru_cache(maxsize=1)
def _voice_catalog() -> str:
    try:
        return subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _resolve(preferred: str) -> str | None:
    """Best installed variant of `preferred` (Premium/Enhanced > base), or None."""
    catalog = _voice_catalog()
    for variant in (f"{preferred} (Premium)", f"{preferred} (Enhanced)"):
        if variant in catalog:
            return variant
    for line in catalog.splitlines():
        # Each line: 'Name                 en_US    # Sample sentence.'
        name = line.split("  ")[0].strip()
        if name == preferred:
            return preferred
    return None


def _pick_voice(preferred: str) -> str:
    return _resolve(preferred) or preferred


class TTS:
    def __init__(self, voice: str = "Zoe", rate: int = 185):
        self.voice = _pick_voice(voice)
        self._default = self.voice
        self.rate = rate
        self._proc: subprocess.Popen | None = None

    def set_voice(self, candidates: str | list[str]) -> str:
        """Switch to the first installed voice in `candidates`.

        Accepts a single name or an ordered preference list. If none are
        installed, keeps the default voice. Returns the voice now in use.
        """
        if isinstance(candidates, str):
            candidates = [candidates]
        for name in candidates:
            resolved = _resolve(name)
            if resolved:
                self.voice = resolved
                return self.voice
        self.voice = self._default
        return self.voice

    def _cmd(self, text: str) -> list[str]:
        return ["say", "-v", self.voice, "-r", str(self.rate), text]

    def speak(self, text: str) -> None:
        """Blocking. Plays the full utterance, then returns."""
        text = text.strip()
        if not text:
            return
        self._proc = subprocess.Popen(self._cmd(text))
        try:
            self._proc.wait()
        finally:
            self._proc = None

    def speak_async(self, text: str) -> None:
        """Non-blocking. Caller polls `is_active()` and may call `stop()`."""
        text = text.strip()
        if not text:
            return
        self.stop()  # clear any prior process
        self._proc = subprocess.Popen(self._cmd(text))

    def is_active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        self._proc = None

    def wait(self) -> None:
        if self._proc is not None:
            self._proc.wait()
            self._proc = None
