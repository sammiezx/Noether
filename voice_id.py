"""Speaker verification via Resemblyzer.

Stores a single voice print (`.voice_print.npy`) next to this file.
Embeddings are L2-normalized 256-d vectors; we compare with cosine
similarity (= dot product since they're already unit-length).

Same-speaker similarity is typically 0.75-0.90. Different speakers
land around 0.30-0.55. The default threshold (0.65) is permissive —
tune up if you get false positives, down if Emmy stops recognizing
you when you have a cold.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from resemblyzer import VoiceEncoder

PRINT_PATH = Path(__file__).parent / ".voice_print.npy"
DEFAULT_THRESHOLD = 0.45
MIN_ENROLL_SECONDS = 3.0  # below this, refuse to enroll
SAMPLE_RATE = 16000


class VoiceID:
    def __init__(self, threshold: float = DEFAULT_THRESHOLD):
        self.encoder = VoiceEncoder(verbose=False)
        self.threshold = threshold
        self.print: np.ndarray | None = None
        if PRINT_PATH.exists():
            self.print = np.load(PRINT_PATH)

    @property
    def enrolled(self) -> bool:
        return self.print is not None

    def enroll(self, audio: np.ndarray) -> bool:
        if len(audio) < SAMPLE_RATE * MIN_ENROLL_SECONDS:
            return False
        embed = self.encoder.embed_utterance(audio.astype(np.float32))
        np.save(PRINT_PATH, embed)
        self.print = embed
        return True

    def verify(self, audio: np.ndarray) -> tuple[bool, float]:
        """Returns (authorized, similarity_score in [-1, 1])."""
        if self.print is None:
            return False, 0.0
        if len(audio) < SAMPLE_RATE * 0.5:
            # Too short to compute a reliable embedding — reject rather
            # than admit (short clips are usually noise, not real speech).
            return False, 0.0
        embed = self.encoder.embed_utterance(audio.astype(np.float32))
        similarity = float(np.dot(embed, self.print))
        return similarity >= self.threshold, similarity

    def reset(self) -> None:
        if PRINT_PATH.exists():
            PRINT_PATH.unlink()
        self.print = None
