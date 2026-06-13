"""Core logic tests — import-light so they run anywhere (no audio/ML/mic).

These cover the agent's tool registry, the persona system, the TTS voice
router's fallback, the run_shell safety guardrail, and prompt construction.
The audio/STT/voice-id/loop modules need mics + on-device models, so they're
exercised manually rather than in CI; `compileall` (in the CI workflow) still
syntax-checks them.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import persona  # noqa: E402
import tts  # noqa: E402
import tools  # noqa: E402
import brain  # noqa: E402


def test_tool_registry_matches_schemas():
    names = [t["name"] for t in tools.TOOL_SCHEMAS]
    assert set(names) == set(tools.TOOL_FUNCTIONS)
    assert len(names) == len(tools.TOOL_FUNCTIONS)
    for required in ("run_shell", "set_persona", "graph_equation", "spacetime"):
        assert required in names


def test_set_persona_enum_matches_persona_keys():
    schema = next(t for t in tools.TOOL_SCHEMAS if t["name"] == "set_persona")
    enum = schema["input_schema"]["properties"]["persona_key"]["enum"]
    assert set(enum) == set(persona.keys())


def test_persona_tts_configs_and_immersion():
    for k in persona.keys():
        cfg = persona.tts_config(k)
        assert cfg.get("engine") in ("say", "kokoro")
    assert persona.immersive("emmy") is False
    assert persona.immersive("einstein") is True


def test_set_persona_switches_and_rejects():
    assert "Unknown persona" in tools.set_persona("nobody-here")
    assert tools.set_persona("batman") and persona.current() == "batman"
    tools.set_persona("emmy")
    assert persona.current() == "emmy"


def test_run_shell_guardrail():
    for bad in ("rm -rf /", "sudo rm -rf /var", "mkfs.ext4 /dev/sda1", "shutdown -h now"):
        assert "Refused" in tools.run_shell(bad)
    assert "hello" in tools.run_shell("echo hello")


def test_tts_voice_fallback_never_crashes():
    t = tts.TTS()
    assert isinstance(t.set_voice(["NoSuchVoice__zzz"]), str)


def test_brain_prompt_immersion_path():
    b = brain.Brain.__new__(brain.Brain)  # skip __init__ (no API key needed)
    persona.set_current("einstein")
    sp = b._system_prompt()
    assert "Einstein" in sp and "FULL CHARACTER IMMERSION" in sp
    persona.set_current("emmy")
    assert "ACTIVE PERSONA" not in b._system_prompt()
