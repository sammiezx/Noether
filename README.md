# Noether

A local, voice-controlled AI assistant for macOS. You talk, **Emmy** (the assistant's
persona, named after [Emmy Noether](https://en.wikipedia.org/wiki/Emmy_Noether)) listens,
thinks with Claude, and talks back — while it can drive your Mac, your editor, the shell,
and a fullscreen physics visualization on command.

Everything except the Claude calls runs **locally on Apple Silicon**: speech-to-text,
text-to-speech, and speaker verification all happen on-device.

---

## What it does

- **Wake-free voice loop.** Listens continuously, detects when you start and stop
  speaking, transcribes, responds, and speaks the reply out loud.
- **Speaker-locked.** On first launch it enrolls *your* voice. After that it only obeys
  you — anyone else's voice is rejected at the gate ("you're not my master").
- **Barge-in.** Talk over Emmy mid-sentence and she stops and listens.
- **Agentic.** The brain is Claude running a tool-use loop, so a single request can chain
  multiple actions — run a command, read a file, take a screenshot, drive VS Code — and
  narrate progress between steps.
- **Spacetime visualization.** Voice-controlled fullscreen physics scenes (black hole,
  light cones, gravitational waves, N-body, spacetime curvature) rendered with Three.js.
- **Web UI.** A browser dashboard shows live state (listening / thinking / speaking),
  a mic amplitude meter, the running transcript, tool calls, and a pause button.
- **Equation visualizer** (companion app). Type any equation — natural language, LaTeX,
  or code — and Claude turns it into an interactive Plotly figure with live sliders.

---

## Architecture

```
                 ┌─────────────────────────────────────────────┐
   mic ─────────▶│  loop (worker thread)                        │
                 │   record → verify → STT → Brain → TTS        │
                 │      │        │      │      │       │         │
   speakers ◀────┼──────┼────────┼──────┼──────┼───────┘         │
                 │   audio.py  voice_id  stt   brain.py  tts.py  │
                 │                              │ tools.py        │
                 └──────────────┬───────────────┼────────────────┘
                                │ StateBus       │ Claude API
                                ▼                ▼
                 ┌─────────────────────────────────────────────┐
   browser ◀─────│  FastAPI + WebSocket (server.py)             │
                 │  static/ UI  +  spacetime.js (Three.js)      │
                 └─────────────────────────────────────────────┘
```

`main.py` boots the loop in a daemon thread, starts the FastAPI server, and opens the
browser at `http://127.0.0.1:8765/`.

### Per-turn pipeline (`loop.py`)

1. **Listen** — `audio.record_until_silence` captures from the mic until you go quiet,
   with a noise-floor calibration, pre-roll buffer, and trigger debouncing.
2. **Verify** — `voice_id.VoiceID` (Resemblyzer) embeds the clip and compares it against
   the enrolled voice print via cosine similarity. Below threshold → rejected.
3. **Transcribe** — `stt.STT` runs `mlx-whisper` (`large-v3-turbo`) on-device. Known
   Whisper silence-hallucinations ("thanks for watching", etc.) are filtered out.
4. **Think** — `brain.Brain` sends the text to Claude with the tool schemas and runs the
   tool-use loop (up to `MAX_ITERATIONS`), streaming intermediate narration to TTS.
5. **Speak** — `tts.TTS` (macOS `say`) plays the reply, with `audio.listen_during_tts`
   watching for barge-in.

Voice commands: say a shutdown phrase ("goodbye Emmy", "power down", …) to quit, or a
reset phrase ("forget my voice", "re-enroll", …) to wipe and re-record your voice print.

### Components

| File | Role |
|------|------|
| `main.py` | Entry point — boots loop thread + server + browser |
| `loop.py` | The conversation loop and state machine |
| `brain.py` | Claude client + agentic tool-use loop + Emmy system prompt |
| `tools.py` | The tools Claude can call (see below) |
| `audio.py` | Mic capture, VAD, noise calibration, barge-in detection |
| `stt.py` | Speech-to-text (mlx-whisper) |
| `tts.py` | Text-to-speech (macOS `say`) with async/interruptible playback |
| `voice_id.py` | Speaker enrollment + verification (Resemblyzer) |
| `server.py` | FastAPI app: static UI + bidirectional WebSocket |
| `state_bus.py` | Thread-safe pub/sub bridging the loop thread and asyncio |
| `controls.py` | Thread-safe pause/resume flag |
| `static/` | Browser UI (HTML/CSS/JS) + `spacetime.js` Three.js scenes |
| `equation_viz/` | Standalone equation→Plotly visualizer (separate app) |

### Tools available to Claude

`run_shell`, `mac_keystroke`, `type_text`, `open_app`, `open_in_vscode`,
`vscode_command`, `read_file`, `take_screenshot`, and `spacetime` (open/close/switch
scene/speed/pause/rotate/zoom the visualization).

> ⚠️ These tools give the assistant real control of your machine — arbitrary shell
> execution, keystrokes, and file reads. Run it only on a machine you trust, and review
> `tools.py` before extending it.

---

## Setup

Requires **macOS on Apple Silicon** (for `mlx-whisper` and the `say` voices) and
**Python 3.11+**.

```bash
# 1. Create a virtualenv and install deps
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install resemblyzer        # speaker verification (not yet in requirements.txt)

# 2. Provide your Anthropic API key
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 3. Run
python main.py
```

First boot downloads the Whisper model (~800 MB) and prompts you to enroll your voice
(talk for ~10 seconds). The browser UI opens automatically at
`http://127.0.0.1:8765/`.

Grant the terminal app **Microphone** and **Accessibility** permissions in
System Settings → Privacy & Security (Accessibility is needed for the keystroke tools).

### Equation visualizer (optional, standalone)

```bash
python equation_viz/app.py     # serves http://127.0.0.1:8766/
```

Type an equation in the page; Claude (`interpreter.py`) parses it into a spec and
`plotter.py` renders an interactive Plotly figure with parameter sliders.

---

## Configuration knobs

- **Model** — `brain.Brain(model=...)` (default `claude-opus-4-7`).
- **Voice ID threshold** — `voice_id.DEFAULT_THRESHOLD` (raise for fewer false accepts,
  lower if it stops recognizing you).
- **TTS voice / rate** — `tts.TTS(voice="Zoe", rate=185)` (`Zoe` is a macOS `say` voice).
- **Tool-use cap** — `brain.MAX_ITERATIONS`.
- **Host / port** — `main.HOST` / `main.PORT`.

---

## Notes & caveats

- `.env` (your API key) and `.voice_print.npy` (your voice biometric) are gitignored —
  keep them out of version control.
- `resemblyzer` is imported by `voice_id.py` but not currently listed in
  `requirements.txt`; install it separately (see Setup).
- The assistant persona is **Emmy** (a.k.a. **Noether**). The macOS voice she speaks in
  happens to be named `Zoe` — that's a system voice, not the assistant's name.
