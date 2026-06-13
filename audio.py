"""Microphone capture, voice-activity detection, and barge-in detection.

A single persistent `MicStream` is opened for the whole session and shared by
every capture call. The previous design opened and closed a fresh PortAudio
stream on every utterance and every barge-in check; that constant churn
destabilised macOS CoreAudio (the `PaMacCore -50` warnings) and could segfault
mid-conversation. One long-lived stream is stable.
"""

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


class MicStream:
    """One persistent input stream for the whole session.

    Open once, read frames forever, close on shutdown — instead of opening a
    new stream per capture (which churned CoreAudio and could crash).
    """

    def __init__(self) -> None:
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=FRAME_SAMPLES
        )
        self._stream.start()

    def read(self) -> np.ndarray:
        frame, _ = self._stream.read(FRAME_SAMPLES)
        return frame.flatten()

    def flush(self) -> None:
        """Drop buffered frames so a fresh capture doesn't begin with stale
        audio that piled up while we weren't reading (e.g. during thinking)."""
        try:
            while self._stream.read_available >= FRAME_SAMPLES:
                self._stream.read(FRAME_SAMPLES)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


def calibrate_noise_floor(mic: MicStream, seconds: float = 1.0) -> float:
    n = max(1, int(seconds * 1000 / FRAME_DURATION_MS))
    frames = [mic.read() for _ in range(n)]
    return _rms(np.concatenate(frames))


def record_until_silence(
    noise_floor: float,
    mic: MicStream,
    silence_duration: float = 1.0,
    max_duration: float = 30.0,
    pre_roll: float = 0.3,
    on_frame: FrameCallback | None = None,
    should_cancel: Callable[[], bool] | None = None,
    already_speaking: bool = False,
) -> np.ndarray | None:
    """Wait for speech, then record until `silence_duration` of quiet.

    `on_frame` is called every ~30ms with the frame's RMS and whether speech
    has been detected yet. `should_cancel`, if provided, is polled each frame;
    if it returns True the recorder exits early with None.
    """
    threshold = max(noise_floor * 4.0, 0.012)
    silence_frames_needed = int(silence_duration * 1000 / FRAME_DURATION_MS)
    pre_roll_frames = int(pre_roll * 1000 / FRAME_DURATION_MS)
    # Require this many consecutive above-threshold frames to trigger —
    # filters single-frame artifacts (coughs, key clicks, AC kicks).
    trigger_frames_needed = 3

    captured: list[np.ndarray] = []
    pre_buffer: list[np.ndarray] = []
    silence_count = 0
    loud_count = 0
    triggered = already_speaking
    elapsed_since_trigger = 0.0

    if not already_speaking:
        mic.flush()  # start listening fresh

    while True:
        if should_cancel is not None and should_cancel():
            return None
        frame = mic.read()
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
    mic: MicStream,
    is_tts_active: Callable[[], bool],
    on_frame: FrameCallback | None = None,
    interrupt_threshold_mult: float = 4.0,
    interrupt_min_frames: int = 6,
    interrupt_floor: float = 0.012,
    should_cancel: Callable[[], bool] | None = None,
    diagnostic: Callable[[str], None] | None = None,
) -> bool:
    """Monitor the mic while TTS plays. Return True iff the user spoke.

    Uses a higher energy threshold than ambient capture to suppress speaker
    bleed (Emmy hearing her own voice). `interrupt_floor` is the absolute
    minimum threshold — in a quiet room the noise-floor-relative threshold
    collapses to near zero, so this floor is what keeps speaker bleed from
    reading as a barge-in.
    """
    threshold = max(noise_floor * interrupt_threshold_mult, interrupt_floor)
    consecutive_loud = 0
    peak_energy = 0.0

    if diagnostic is not None:
        diagnostic(f"barge-in armed: threshold={threshold:.4f} ({interrupt_min_frames} frames)")
    while is_tts_active():
        if should_cancel is not None and should_cancel():
            return False
        frame = mic.read()
        energy = _rms(frame)
        peak_energy = max(peak_energy, energy)
        if on_frame is not None:
            on_frame(energy, False)
        if energy > threshold:
            consecutive_loud += 1
            if consecutive_loud >= interrupt_min_frames:
                if diagnostic is not None:
                    diagnostic(
                        f"BARGE-IN fired: energy={energy:.4f} > thr={threshold:.4f} "
                        f"for {interrupt_min_frames} frames"
                    )
                return True
        else:
            consecutive_loud = 0
    if diagnostic is not None:
        diagnostic(f"TTS ended without barge-in (peak={peak_energy:.4f}, thr={threshold:.4f})")
    return False
