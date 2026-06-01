"""Speech-to-text via mlx-whisper. Runs natively on Apple Silicon
(Neural Engine + GPU) — much faster than CPU faster-whisper while using
the larger, more accurate `large-v3-turbo` model.

First boot downloads the model from Hugging Face (~800 MB).
"""

from __future__ import annotations

import mlx_whisper
import numpy as np


class STT:
    def __init__(self, model: str = "mlx-community/whisper-large-v3-turbo"):
        self._model = model
        # Warm-up pass on silence: forces model load + JIT compile now,
        # so the first real utterance returns quickly.
        warmup = np.zeros(16000, dtype=np.float32)  # 1s of silence
        mlx_whisper.transcribe(warmup, path_or_hf_repo=self._model, language="en")

    def transcribe(self, audio: np.ndarray) -> str:
        result = mlx_whisper.transcribe(
            audio.astype(np.float32),
            path_or_hf_repo=self._model,
            language="en",
            condition_on_previous_text=False,
        )
        return result["text"].strip()
