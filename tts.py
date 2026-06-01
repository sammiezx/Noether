"""Text-to-speech via macOS `say`. Picks the highest-quality variant
available for the chosen voice. Supports both blocking (`speak`) and
non-blocking (`speak_async`) playback so the caller can interrupt
mid-utterance — used for barge-in detection.
"""

from __future__ import annotations

import subprocess


def _pick_voice(preferred: str) -> str:
    try:
        out = subprocess.run(
            ["say", "-v", "?"], capture_output=True, text=True, check=True
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return preferred
    if f"{preferred} (Premium)" in out:
        return f"{preferred} (Premium)"
    if f"{preferred} (Enhanced)" in out:
        return f"{preferred} (Enhanced)"
    return preferred


class TTS:
    def __init__(self, voice: str = "Zoe", rate: int = 185):
        self.voice = _pick_voice(voice)
        self.rate = rate
        self._proc: subprocess.Popen | None = None

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
