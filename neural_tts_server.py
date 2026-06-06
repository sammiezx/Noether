"""Persistent neural TTS renderer with optional voice conversion.

Runs under the dedicated 3.12 env (.venv-tts). The main app (tts.py) spawns
this once and keeps it alive, so models load a single time.

Two stages:
  1. Kokoro-82M renders the words *clearly* (kokoro-onnx).
  2. If the request has a `convert_ref` (a path to a sample of a target voice),
     knn-vc repaints that clean audio into the target's voice — intelligible
     words, target timbre/pitch. This is how Einstein/Batman sound like
     themselves without the gibberish you get from cloning a noisy recording.

knn-vc + its WavLM model load lazily on the first conversion request, so plain
(non-converted) voices stay light. Matching sets are cached per reference path.

Protocol (line-delimited JSON over stdin/stdout):
  stdout "READY"
  stdin  {"text","voice","speed","pitch","lowpass","gain","out","convert_ref"?}
  stdout "OK" | "ERR <message>"
"""

from __future__ import annotations

import json
import sys

import numpy as np
import soundfile as sf
import librosa
from scipy.signal import butter, filtfilt, hilbert
from kokoro_onnx import Kokoro

# Lazily-initialised voice-conversion state (torch/knn-vc loaded on first use).
_KNN = None
_MATCHING_SETS: dict = {}
_VC_SR = 16000


def _lowpass(x: np.ndarray, sr: int, cutoff: float) -> np.ndarray:
    b, a = butter(4, cutoff / (sr / 2), btype="low")
    return filtfilt(b, a, x).astype(np.float32)


def _highpass(x: np.ndarray, sr: int, cutoff: float) -> np.ndarray:
    b, a = butter(2, cutoff / (sr / 2), btype="high")
    return filtfilt(b, a, x).astype(np.float32)


def _postprocess(x: np.ndarray, sr: int, post: dict):
    """Character DSP applied AFTER voice conversion. Used for Batman's snarl:
    deepen (pitch), a breath/hiss layer that tracks the speech envelope (the
    'king-cobra' airflow), and tanh saturation for rasp.
    """
    x = x.astype(np.float32)
    if x.ndim > 1:
        x = x.mean(axis=1)

    # Strip the constant hiss the conversion/old-reference leaves behind.
    # Done first, so Batman's deliberate breath layer (added below) survives.
    if post.get("denoise"):
        import noisereduce as nr
        x = nr.reduce_noise(
            y=x, sr=sr, stationary=True,
            prop_decrease=float(post.get("denoise_amount", 0.8)),
        ).astype(np.float32)

    pitch = float(post.get("pitch", 0) or 0)
    if pitch:
        x = librosa.effects.pitch_shift(x, sr=sr, n_steps=pitch)

    breath = float(post.get("breath", 0) or 0)
    noise_layer = 0.0
    if breath:
        env = np.abs(hilbert(x))
        b, a = butter(2, 28 / (sr / 2), btype="low")
        env = filtfilt(b, a, env)
        env = env / (env.max() + 1e-9)
        lo, hi = post.get("breath_band", [1700, 6500])
        rng = np.random.default_rng(1)  # fixed seed → deterministic
        n = rng.standard_normal(len(x)).astype(np.float32)
        bb, ba = butter(4, [lo / (sr / 2), hi / (sr / 2)], btype="band")
        n = filtfilt(bb, ba, n).astype(np.float32)
        noise_layer = n * (env ** 0.6)

    drive = float(post.get("saturation", 0) or 0)
    voiced = np.tanh(x * drive) if drive else x
    base = float(post.get("voice_gain", 0.85)) if breath else 1.0
    x = base * voiced + breath * noise_layer

    # Presence: lift the consonant band so a deep/converted voice stays
    # intelligible instead of muffled.
    pres = float(post.get("presence", 0) or 0)
    if pres:
        x = x + pres * _highpass(x, sr, float(post.get("presence_hz", 2500)))

    cut = post.get("lowpass")
    if cut:
        x = _lowpass(x, sr, float(cut))
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.92
    return x.astype(np.float32), sr


def _ensure_knn():
    global _KNN
    if _KNN is None:
        import torch  # noqa: deferred — only when a persona needs conversion
        _KNN = torch.hub.load(
            "bshall/knn-vc", "knn_vc", prematched=True, trust_repo=True, device="cpu"
        )
    return _KNN


def _convert(src_wav: str, ref_path: str, out_path: str) -> None:
    """Repaint clean speech in `src_wav` into the voice from `ref_path`."""
    import torchaudio
    knn = _ensure_knn()
    if ref_path not in _MATCHING_SETS:
        _MATCHING_SETS[ref_path] = knn.get_matching_set([ref_path])
    query = knn.get_features(src_wav)
    out = knn.match(query, _MATCHING_SETS[ref_path], topk=4)
    torchaudio.save(out_path, out[None].cpu(), _VC_SR)


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

            out_path = req["out"]

            # 1. pre-conversion shaping (for plain processed voices)
            pitch = float(req.get("pitch", 0) or 0)
            if pitch:
                samples = librosa.effects.pitch_shift(samples, sr=sr, n_steps=pitch)
            cutoff = req.get("lowpass")
            if cutoff:
                samples = _lowpass(samples, sr, float(cutoff))
            gain = float(req.get("gain", 1.0) or 1.0)
            if gain != 1.0:
                samples = samples * gain

            # 2. voice conversion to a target sample (optional)
            ref = req.get("convert_ref")
            if ref:
                src = out_path + ".src.wav"
                sf.write(src, samples, sr)
                conv = out_path + ".conv.wav"
                _convert(src, ref, conv)
                samples, sr = sf.read(conv)
                samples = samples.astype(np.float32)

            # 3. character DSP after conversion (Batman's deepen/breath/rasp)
            post = req.get("post")
            if post:
                samples, sr = _postprocess(samples, sr, post)

            sf.write(out_path, samples, sr)
            print("OK", flush=True)
        except Exception as exc:  # noqa: BLE001 — report, keep serving
            print("ERR " + str(exc).replace("\n", " "), flush=True)


if __name__ == "__main__":
    main()
