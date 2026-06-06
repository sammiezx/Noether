"""The Emmy agent loop. Runs in a worker thread and publishes
state-change events to a StateBus so the web UI can react.

Pipeline per turn:
  1. listen → record until silence
  2. (gate) speaker verification — reject if not the enrolled user
  3. STT → text
  4. brain.respond → text (may chain tool calls)
  5. speak the reply, but allow the user to barge in mid-speech

On first launch, the user is asked to enroll their voice.
"""

from __future__ import annotations

import time
import traceback

import numpy as np

from audio import (
    SAMPLE_RATE,
    calibrate_noise_floor,
    listen_during_tts,
    record_until_silence,
)
import persona
from brain import Brain
from controls import Controls
from state_bus import StateBus
from stt import STT
from tools import init_tools
from tts import TTS
from voice_id import VoiceID

SHUTDOWN_PHRASES = {
    "exit",
    "quit",
    "goodbye",
    "goodbye emmy",
    "goodbye noether",
    "shut down",
    "shutdown",
    "stop emmy",
    "stop noether",
    "power down",
}

RESET_VOICE_PHRASES = {
    "forget my voice",
    "reset my voice",
    "re-enroll",
    "reenroll",
    "learn my voice again",
    "reset voice",
}

# Whisper is well-known for hallucinating these on silence or pure noise.
# Discard them silently — they're never real user speech.
WHISPER_HALLUCINATIONS = {
    "you",
    "you.",
    "thank you",
    "thank you.",
    "thanks",
    "thanks.",
    "thanks for watching",
    "thanks for watching.",
    "thank you for watching",
    "thank you for watching.",
    "bye",
    "bye.",
    "bye-bye",
    "bye-bye.",
    "music",
    "music.",
    "(music)",
    "[music]",
    "subscribe",
    "subscribe.",
    "please subscribe.",
    "okay.",
    "ok.",
    ".",
    "...",
}


def _is_hallucination(text: str) -> bool:
    cleaned = text.lower().strip()
    if cleaned in WHISPER_HALLUCINATIONS:
        return True
    # Anything under 3 characters of letters is junk.
    letters = "".join(c for c in cleaned if c.isalpha())
    if len(letters) < 3:
        return True
    return False


def _is_shutdown(text: str) -> bool:
    return text.lower().strip(" .!?,") in SHUTDOWN_PHRASES


def _is_reset_voice(text: str) -> bool:
    return text.lower().strip(" .!?,") in RESET_VOICE_PHRASES


def run_loop(bus: StateBus, controls: Controls) -> None:
    """Initialize the pipeline and run the conversation loop forever."""
    try:
        init_tools(bus)
        bus.publish({"type": "state", "value": "booting"})

        bus.publish({"type": "log", "text": "Loading speech recognition..."})
        stt = STT()

        bus.publish({"type": "log", "text": "Loading speaker verification..."})
        voice_id = VoiceID()

        bus.publish({"type": "log", "text": "Loading TTS..."})
        tts = TTS()

        bus.publish({"type": "log", "text": "Connecting to Claude..."})
        brain = Brain()

        bus.publish({"type": "log", "text": "Calibrating microphone..."})
        noise_floor = calibrate_noise_floor(1.0)
        bus.publish({"type": "log", "text": f"Noise floor: {noise_floor:.5f}"})

        def publish_amplitude(energy: float, _recording: bool) -> None:
            scaled = min(1.0, energy / 0.15)
            bus.publish({"type": "amplitude", "value": scaled})

        def speak_blocking(text: str, role: str = "assistant") -> None:
            """Speak the text without allowing barge-in. Used for enrollment."""
            if not text.strip():
                return
            bus.publish({"type": role, "text": text})
            bus.publish({"type": "state", "value": "speaking"})
            tts.set_voice(persona.voice())
            tts.rate = persona.rate()
            tts.speak(text)

        def speak_with_interrupt(text: str) -> bool:
            """Speak the text; return True if the user spoke over it.

            On interrupt, the TTS process is killed immediately and we
            return — the caller's next `record_until_silence` call should
            be passed `already_speaking=True` to pick up the utterance
            without waiting for the trigger.
            """
            if not text.strip():
                return False
            bus.publish({"type": "assistant", "text": text})
            bus.publish({"type": "state", "value": "speaking"})
            tts.set_voice(persona.voice())
            tts.rate = persona.rate()
            tts.speak_async(text)
            # Brief warm-up so the first phoneme attack doesn't false-trigger.
            time.sleep(0.15)
            interrupted = listen_during_tts(
                noise_floor,
                is_tts_active=tts.is_active,
                on_frame=publish_amplitude,
                should_cancel=lambda: controls.paused,
            )
            if interrupted:
                tts.stop()
                # Let speaker echo dissipate
                time.sleep(0.2)
                return True
            tts.wait()
            return False

        # ------------------------------------------------------------------
        # Enrollment flow — only on first launch (no voice print on disk)
        # ------------------------------------------------------------------
        if not voice_id.enrolled:
            speak_blocking(
                "Hi, I'm Emmy — the voice of Noether. I haven't learned your voice yet. "
                "Talk to me at your normal speaking volume for about ten seconds — "
                "count from one to twenty, or describe what you did today. "
                "The more varied the better."
            )
            while not voice_id.enrolled:
                bus.publish({"type": "state", "value": "listening"})
                audio = record_until_silence(
                    noise_floor,
                    silence_duration=2.0,
                    max_duration=20.0,
                    on_frame=publish_amplitude,
                    should_cancel=lambda: controls.paused,
                )
                if audio is None or len(audio) < SAMPLE_RATE * 4:
                    speak_blocking(
                        "That was a bit short. Try talking for a few more seconds."
                    )
                    continue
                if voice_id.enroll(audio):
                    speak_blocking(
                        "Got it. I'll only respond to your voice from now on."
                    )
                else:
                    speak_blocking("Hmm, that didn't take. Let's try once more.")

        speak_blocking("Emmy online. Standing by.")

        already_speaking = False

        # ------------------------------------------------------------------
        # Main conversation loop
        # ------------------------------------------------------------------
        while True:
            if controls.paused:
                bus.publish({"type": "state", "value": "paused"})
                while controls.paused:
                    time.sleep(0.1)
                continue

            bus.publish({"type": "state", "value": "listening"})
            audio = record_until_silence(
                noise_floor,
                on_frame=publish_amplitude,
                should_cancel=lambda: controls.paused,
                already_speaking=already_speaking,
            )
            already_speaking = False  # consumed

            if audio is None or len(audio) < SAMPLE_RATE * 0.6:
                continue

            # --- Speaker verification gate ---
            authorized, similarity = voice_id.verify(audio)
            if not authorized:
                bus.publish({
                    "type": "log",
                    "text": f"voice rejected ({similarity:.2f} < {voice_id.threshold:.2f})",
                })
                bus.publish({
                    "type": "user",
                    "text": "[unrecognized voice — rejected]",
                })
                already_speaking = speak_with_interrupt(
                    "Sorry — you're not my master. I only answer to one person, "
                    "and it isn't you."
                )
                continue

            # --- STT ---
            bus.publish({"type": "state", "value": "transcribing"})
            text = stt.transcribe(audio)
            if not text:
                continue

            if _is_hallucination(text):
                # Whisper hallucination on silence/noise — drop silently.
                continue

            bus.publish({"type": "user", "text": text})

            if _is_shutdown(text):
                bus.publish({"type": "state", "value": "speaking"})
                tts.speak("Catch you later. Powering down.")
                bus.publish({"type": "assistant", "text": "Catch you later. Powering down."})
                bus.publish({"type": "state", "value": "idle"})
                return

            if _is_reset_voice(text):
                voice_id.reset()
                speak_blocking(
                    "Voice print cleared. Let me hear you for about ten seconds "
                    "to re-enroll."
                )
                while not voice_id.enrolled:
                    bus.publish({"type": "state", "value": "listening"})
                    re_audio = record_until_silence(
                        noise_floor,
                        silence_duration=2.0,
                        max_duration=20.0,
                        on_frame=publish_amplitude,
                        should_cancel=lambda: controls.paused,
                    )
                    if re_audio is None or len(re_audio) < SAMPLE_RATE * 4:
                        speak_blocking("Not quite enough. Once more.")
                        continue
                    if voice_id.enroll(re_audio):
                        speak_blocking("Got it. We're good.")
                continue

            # --- Brain (tool-use loop) ---
            bus.publish({"type": "state", "value": "thinking"})

            def on_tool_use(tool_id: str, name: str, args: dict) -> None:
                bus.publish({
                    "type": "tool_use",
                    "id": tool_id,
                    "name": name,
                    "input": args,
                })

            def on_tool_result(tool_id: str, raw_result, is_error: bool) -> None:
                if isinstance(raw_result, str):
                    summary = raw_result.splitlines()[0][:140] if raw_result else "(empty)"
                else:
                    summary = "(image returned)"
                bus.publish({
                    "type": "tool_result",
                    "id": tool_id,
                    "summary": summary,
                    "is_error": is_error,
                })

            def on_intermediate_text(intermediate: str) -> None:
                nonlocal already_speaking
                if already_speaking:
                    return  # already interrupted upstream
                if speak_with_interrupt(intermediate):
                    already_speaking = True
                bus.publish({"type": "state", "value": "thinking"})

            reply = brain.respond(
                text,
                on_tool_use=on_tool_use,
                on_tool_result=on_tool_result,
                on_text=on_intermediate_text,
            )

            if reply and not already_speaking:
                if speak_with_interrupt(reply):
                    already_speaking = True

    except Exception as exc:
        bus.publish({"type": "log", "text": f"FATAL: {exc}"})
        bus.publish({"type": "log", "text": traceback.format_exc()})
        bus.publish({"type": "state", "value": "error"})
        time.sleep(5)
        raise
