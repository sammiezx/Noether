"""Persistent neural TTS renderer (Kokoro-82M via kokoro-onnx).

Runs under the dedicated 3.12 env (.venv-tts) because the neural stack needs
packages that don't build on the app's Python 3.14. The main app (tts.py)
spawns this once and keeps it alive, so the ~300MB model is loaded a single
time and every render after that is fast.

Protocol (line-delimited over stdin/stdout):
  stdout "READY"                  — emitted once the model is loaded
  stdin  <json>                   — one request per line:
        {"text","voice","speed","pitch","lowpass","gain","out"}
  stdout "OK"                     — wav written to `out`
  stdout "ERR <message>"          — render failed

`pitch` is in semitones (negative = deeper), `lowpass` is a cutoff in Hz to
darken the tone, `gain` scales the level (for a hushed delivery). All optional.
Everything noisy (model warnings) goes to stderr, which the parent discards.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import soundfile as sf
import librosa
from scipy.signal import butter, filtfilt
from kokoro_onnx import Kokoro


def _lowpass(x: np.ndarray, sr: int, cutoff: float) -> np.ndarray:
    b, a = butter(4, cutoff / (sr / 2), btype="low")
    return filtfilt(b, a, x).astype(np.float32)


def main() -> None:
    model_path, voices_path = sys.argv[1], sys.argv[2]
    kokoro = Kokoro(model_path, voices_path)
    print("READY", flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            samples, sr = kokoro.create(
                req["text"],
                voice=req.get("voice", "am_onyx"),
                speed=float(req.get("speed", 1.0)),
                lang="en-us",
            )
            samples = samples.astype(np.float32)

            pitch = float(req.get("pitch", 0) or 0)
            if pitch:
                samples = librosa.effects.pitch_shift(samples, sr=sr, n_steps=pitch)

            cutoff = req.get("lowpass")
            if cutoff:
                samples = _lowpass(samples, sr, float(cutoff))

            gain = float(req.get("gain", 1.0) or 1.0)
            if gain != 1.0:
                samples = samples * gain

            sf.write(req["out"], samples, sr)
            print("OK", flush=True)
        except Exception as exc:  # noqa: BLE001 — report, keep serving
            print("ERR " + str(exc).replace("\n", " "), flush=True)


if __name__ == "__main__":
    main()
