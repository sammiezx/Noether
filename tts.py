"""Text-to-speech with two engines, chosen per persona:

  - "say"    — macOS `say` (instant, used for Emmy and most personas)
  - "kokoro" — neural Kokoro-82M via a persistent helper process (neural_tts_server.py)
               running in the 3.12 .venv-tts, for voices that need to sound
               genuinely human (e.g. a deep, hushed Batman).

Either way, playback is a killable subprocess (`say` or `afplay`), so the
barge-in detector can stop speech mid-utterance. `configure(cfg)` selects the
engine + voice before each utterance; the neural helper is spawned lazily on
first use and kept warm so the model only loads once.
"""

from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

_REPO = Path(__file__).resolve().parent
_VENV_TTS_PY = _REPO / ".venv-tts" / "bin" / "python"
_NEURAL_SERVER = _REPO / "neural_tts_server.py"
_KOKORO_MODEL = _REPO / "tts_models" / "kokoro-v1.0.onnx"
_KOKORO_VOICES = _REPO / "tts_models" / "voices-v1.0.bin"
_NEURAL_WAV = Path(tempfile.gettempdir()) / "noether_neural.wav"

DEFAULT_CFG = {"engine": "say", "voices": ["Zoe", "Samantha"], "rate": 185}


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
        name = line.split("  ")[0].strip()
        if name == preferred:
            return preferred
    return None


def _pick_voice(preferred: str) -> str:
    return _resolve(preferred) or preferred


def neural_available() -> bool:
    """True if the 3.12 helper env and Kokoro model files are present."""
    return _VENV_TTS_PY.exists() and _KOKORO_MODEL.exists() and _KOKORO_VOICES.exists()


# Live TTS instances, so a UI "shutdown" can tear down their child processes
# (the neural helper holds torch + a ~1.5GB model — it must not be orphaned).
_INSTANCES: list["TTS"] = []


def shutdown_all() -> None:
    """Kill every TTS instance's playback + neural helper. Safe to call twice."""
    for t in list(_INSTANCES):
        try:
            t._hard_close()
        except Exception:
            pass


atexit.register(shutdown_all)


class TTS:
    def __init__(self, voice: str = "Zoe", rate: int = 185):
        self.voice = _pick_voice(voice)
        self._default = self.voice
        self.rate = rate
        self._cfg = dict(DEFAULT_CFG)
        self._proc: subprocess.Popen | None = None       # current playback (say/afplay)
        self._neural: subprocess.Popen | None = None      # persistent Kokoro server
        _INSTANCES.append(self)

    def _hard_close(self) -> None:
        """Kill current playback and the neural helper subprocess. For shutdown."""
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.kill()
            except Exception:
                pass
        if self._neural is not None and self._neural.poll() is None:
            try:
                self._neural.stdin.close()
            except Exception:
                pass
            try:
                self._neural.terminate()
                self._neural.wait(timeout=1)
            except Exception:
                try:
                    self._neural.kill()
                except Exception:
                    pass
        self._neural = None

    # ---- configuration -----------------------------------------------------

    def configure(self, cfg: dict) -> None:
        """Select engine + voice for subsequent utterances.

        `cfg` for "say":    {"engine":"say", "voices":[...], "rate":int}
        `cfg` for "kokoro": {"engine":"kokoro", "voice":str, "speed":float,
                             "pitch":semitones, "lowpass":hz, "gain":float}
        """
        if not cfg:
            cfg = DEFAULT_CFG
        self._cfg = cfg
        if cfg.get("engine", "say") == "say":
            self.set_voice(cfg.get("voices", [self._default]))
            self.rate = int(cfg.get("rate", self.rate))

    def set_voice(self, candidates: str | list[str]) -> str:
        if isinstance(candidates, str):
            candidates = [candidates]
        for name in candidates:
            resolved = _resolve(name)
            if resolved:
                self.voice = resolved
                return self.voice
        self.voice = self._default
        return self.voice

    # ---- neural helper -----------------------------------------------------

    def _ensure_neural(self) -> bool:
        """Spawn the Kokoro helper if needed; return True if it's ready."""
        if self._neural is not None and self._neural.poll() is None:
            return True
        if not neural_available():
            return False
        try:
            self._neural = subprocess.Popen(
                [str(_VENV_TTS_PY), str(_NEURAL_SERVER),
                 str(_KOKORO_MODEL), str(_KOKORO_VOICES)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
            )
        except Exception:
            self._neural = None
            return False
        # Wait for the model to load (READY), bounded so we never hang forever.
        for _ in range(600):  # ~60s budget for first model load
            line = self._neural.stdout.readline()
            if not line:
                break
            if line.strip() == "READY":
                return True
        self._neural = None
        return False

    def _render_neural(self, text: str) -> str | None:
        """Render `text` to a wav via the helper. Returns path or None on failure."""
        if not self._ensure_neural():
            return None
        cfg = self._cfg
        req = {
            "text": text,
            "voice": cfg.get("voice", "am_onyx"),
            "speed": cfg.get("speed", 1.0),
            "pitch": cfg.get("pitch", 0),
            "lowpass": cfg.get("lowpass"),
            "gain": cfg.get("gain", 1.0),
            "out": str(_NEURAL_WAV),
        }
        ref = cfg.get("convert_ref")
        if ref:
            # Resolve relative to the repo so the helper (any cwd) finds it.
            ref_path = Path(ref)
            if not ref_path.is_absolute():
                ref_path = _REPO / ref_path
            req["convert_ref"] = str(ref_path)
        if cfg.get("post"):
            req["post"] = cfg["post"]
        try:
            self._neural.stdin.write(json.dumps(req) + "\n")
            self._neural.stdin.flush()
            while True:
                line = self._neural.stdout.readline()
                if not line:
                    return None
                line = line.strip()
                if line == "OK":
                    return str(_NEURAL_WAV)
                if line.startswith("ERR"):
                    print(f"[neural-tts] {line}", file=sys.stderr, flush=True)
                    return None
        except Exception:
            return None

    # ---- playback ----------------------------------------------------------

    def _say_cmd(self, text: str) -> list[str]:
        return ["say", "-v", self.voice, "-r", str(self.rate), text]

    def speak(self, text: str) -> None:
        """Blocking. Plays the full utterance, then returns."""
        text = text.strip()
        if not text:
            return
        if self._cfg.get("engine") == "kokoro":
            wav = self._render_neural(text)
            if wav and os.path.exists(wav):
                self._proc = subprocess.Popen(["afplay", wav])
                try:
                    self._proc.wait()
                finally:
                    self._proc = None
                return
            # fall through to `say` if neural failed
        self._proc = subprocess.Popen(self._say_cmd(text))
        try:
            self._proc.wait()
        finally:
            self._proc = None

    def speak_async(self, text: str) -> None:
        """Non-blocking. Caller polls `is_active()` and may call `stop()`."""
        text = text.strip()
        if not text:
            return
        self.stop()
        if self._cfg.get("engine") == "kokoro":
            wav = self._render_neural(text)
            if wav and os.path.exists(wav):
                self._proc = subprocess.Popen(["afplay", wav])
                return
            # fall through to `say` if neural failed
        self._proc = subprocess.Popen(self._say_cmd(text))

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
