from __future__ import annotations

from typing import Callable

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
FRAME_DURATION_MS = 30
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_DURATION_MS / 1000)

FrameCallback = Callable[[float, bool], None]
"""Called for every captured frame with (rms_energy, is_recording)."""


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def calibrate_noise_floor(seconds: float = 1.0) -> float:
    audio = sd.rec(int(SAMPLE_RATE * seconds), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
    sd.wait()
    return _rms(audio.flatten())


def record_until_silence(
    noise_floor: float,
    silence_duration: float = 1.0,
    max_duration: float = 30.0,
    pre_roll: float = 0.3,
    on_frame: FrameCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    already_speaking: bool = False,
) -> np.ndarray | None:
    """Wait for speech, then record until `silence_duration` of quiet.

    `on_frame` is called every ~30ms with the frame's RMS and whether
    speech has been detected yet (False while waiting, True while recording).

    `should_cancel`, if provided, is polled each frame. If it ever returns
    True the recorder exits early with None — used to abort recording when
    the user hits pause mid-utterance.
    """
    threshold = max(noise_floor * 4.0, 0.012)
    silence_frames_needed = int(silence_duration * 1000 / FRAME_DURATION_MS)
    pre_roll_frames = int(pre_roll * 1000 / FRAME_DURATION_MS)
    # Require this many consecutive above-threshold frames to trigger.
    # Filters out single-frame artifacts: coughs, key clicks, AC kicks.
    trigger_frames_needed = 3

    captured: list[np.ndarray] = []
    pre_buffer: list[np.ndarray] = []
    silence_count = 0
    loud_count = 0
    triggered = already_speaking
    elapsed_since_trigger = 0.0

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=FRAME_SAMPLES
    ) as stream:
        while True:
            if should_cancel is not None and should_cancel():
                return None
            frame, _ = stream.read(FRAME_SAMPLES)
            frame = frame.flatten()
            energy = _rms(frame)
            if on_frame is not None:
                on_frame(energy, triggered)

            if not triggered:
                pre_buffer.append(frame)
                if len(pre_buffer) > pre_roll_frames:
                    pre_buffer.pop(0)
                if energy > threshold:
                    loud_count += 1
                    if loud_count >= trigger_frames_needed:
                        triggered = True
                        captured.extend(pre_buffer)
                else:
                    loud_count = 0
            else:
                captured.append(frame)
                elapsed_since_trigger += FRAME_DURATION_MS / 1000.0
                if energy < threshold:
                    silence_count += 1
                    if silence_count >= silence_frames_needed:
                        break
                else:
                    silence_count = 0
                if elapsed_since_trigger > max_duration:
                    break

    if not captured:
        return None
    return np.concatenate(captured)


def listen_during_tts(
    noise_floor: float,
    is_tts_active: Callable[[], bool],
    on_frame: FrameCallback | None = None,
    interrupt_threshold_mult: float = 4.0,
    interrupt_min_frames: int = 6,
    should_cancel: Callable[[], bool] | None = None,
    diagnostic: Callable[[str], None] | None = None,
) -> bool:
    """Monitor the mic while TTS plays. Return True iff the user spoke.

    Uses a higher energy threshold than ambient capture to suppress
    speaker bleed (Emmy hearing her own voice). Returns when TTS ends
    naturally (False) or the user has spoken above threshold for
    `interrupt_min_frames * FRAME_DURATION_MS` ms (True).
    """
    threshold = max(noise_floor * interrupt_threshold_mult, 0.012)
    consecutive_loud = 0
    peak_energy = 0.0

    with sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=FRAME_SAMPLES
    ) as stream:
        if diagnostic is not None:
            diagnostic(f"barge-in armed: threshold={threshold:.4f} ({interrupt_min_frames} frames)")
        while is_tts_active():
            if should_cancel is not None and should_cancel():
                return False
            frame, _ = stream.read(FRAME_SAMPLES)
            energy = _rms(frame.flatten())
            peak_energy = max(peak_energy, energy)
            if on_frame is not None:
                on_frame(energy, False)
            if energy > threshold:
                consecutive_loud += 1
                if consecutive_loud >= interrupt_min_frames:
                    return True
            else:
                consecutive_loud = 0
        if diagnostic is not None:
            diagnostic(f"TTS ended without barge-in (peak={peak_energy:.4f}, thr={threshold:.4f})")
    return False
