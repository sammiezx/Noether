"""Tools Emmy can call. In-process Python functions, dispatched by name.

Each tool:
  - has a JSON schema in TOOL_SCHEMAS (sent to Claude)
  - has a Python callable in TOOL_FUNCTIONS (executed locally)
  - returns either a str (becomes a text tool_result) or a list of content
    blocks (used as the tool_result `content` field directly — for images)
"""

from __future__ import annotations

import base64
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING
from urllib.parse import quote

import persona

if TYPE_CHECKING:
    from state_bus import StateBus

# Module-level reference to the bus, set by init_tools() at startup.
# Tools that push events to the UI (like `spacetime`) use this.
_BUS: "StateBus | None" = None


def init_tools(bus: "StateBus") -> None:
    """Wire tool side-effects into the running app's event bus."""
    global _BUS
    _BUS = bus

# --- max bytes returned to Claude for any single tool result ---
MAX_RESULT_CHARS = 8000


def _truncate(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n... [truncated, original was {len(text)} chars]"


def _expand(path: str) -> str:
    return os.path.expanduser(os.path.expandvars(path))


# ---------------------------------------------------------------------------
# 1. run_shell
# ---------------------------------------------------------------------------

# A small safety net: the agent runs shell commands autonomously, so refuse the
# handful of patterns that are catastrophic and almost never intended by voice.
# This is a guardrail, not a sandbox — only run Emmy on a machine you trust.
import re as _re

_DANGEROUS_SHELL = [
    r"\brm\s+-[a-z]*r[a-z]*f\b.*\s(/|~|\$HOME|\.)\s*$",  # rm -rf / ~ . etc.
    r"\brm\s+-[a-z]*r[a-z]*f\s+/(\s|$)",                  # rm -rf /
    r"\bmkfs\b", r"\bdd\b.*\bof=/dev/", r">\s*/dev/sd",   # disk wipes
    r":\(\)\s*\{.*\}\s*;:",                                # fork bomb
    r"\b(shutdown|reboot|halt)\b", r"\bdiskutil\s+erase",
    r"\bsudo\b.*\brm\b",
]


def run_shell(command: str, timeout: int = 30) -> str:
    low = command.strip().lower()
    for pat in _DANGEROUS_SHELL:
        if _re.search(pat, low):
            return (
                "Refused: that command matches a destructive pattern "
                "(disk wipe / mass delete / power-off) and is blocked for safety. "
                "Run it manually if you really mean it."
            )
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Timed out after {timeout}s."

    parts = []
    if proc.stdout:
        parts.append(f"STDOUT:\n{proc.stdout.rstrip()}")
    if proc.stderr:
        parts.append(f"STDERR:\n{proc.stderr.rstrip()}")
    parts.append(f"EXIT: {proc.returncode}")
    return _truncate("\n".join(parts))


# ---------------------------------------------------------------------------
# 2. mac_keystroke
# ---------------------------------------------------------------------------

_KEY_CODES = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51,
    "escape": 53, "esc": 53, "left": 123, "right": 124, "down": 125,
    "up": 126, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}

_MODIFIER_MAP = {
    "cmd": "command down", "command": "command down",
    "shift": "shift down",
    "ctrl": "control down", "control": "control down",
    "opt": "option down", "option": "option down", "alt": "option down",
}


def _applescript(script: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=10
    )
    return proc.returncode, (proc.stderr or proc.stdout).strip()


def mac_keystroke(
    key: str,
    modifiers: list[str] | None = None,
    app: str | None = None,
) -> str:
    modifiers = modifiers or []
    try:
        mods = [_MODIFIER_MAP[m.lower()] for m in modifiers]
    except KeyError as e:
        return f"Unknown modifier: {e}. Use cmd/shift/ctrl/option."
    using = f" using {{{', '.join(mods)}}}" if mods else ""

    activate = ""
    if app:
        # AppleScript string escape: escape backslashes then double quotes
        safe_app = app.replace("\\", "\\\\").replace('"', '\\"')
        activate = f'tell application "{safe_app}" to activate\ndelay 0.15\n'

    code = _KEY_CODES.get(key.lower())
    if code is not None:
        script = f'{activate}tell application "System Events" to key code {code}{using}'
    else:
        safe_key = key.replace("\\", "\\\\").replace('"', '\\"')
        script = f'{activate}tell application "System Events" to keystroke "{safe_key}"{using}'

    rc, msg = _applescript(script)
    if rc != 0:
        return f"AppleScript error: {msg}"
    return f"Sent '{key}'{' + ' + '+'.join(modifiers) if modifiers else ''}{' to ' + app if app else ''}"


# ---------------------------------------------------------------------------
# 3. type_text
# ---------------------------------------------------------------------------

def type_text(text: str, app: str | None = None) -> str:
    activate = ""
    if app:
        safe_app = app.replace("\\", "\\\\").replace('"', '\\"')
        activate = f'tell application "{safe_app}" to activate\ndelay 0.15\n'
    safe_text = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f'{activate}tell application "System Events" to keystroke "{safe_text}"'
    rc, msg = _applescript(script)
    if rc != 0:
        return f"AppleScript error: {msg}"
    return f"Typed {len(text)} characters{' into ' + app if app else ''}."


# ---------------------------------------------------------------------------
# 4. open_app
# ---------------------------------------------------------------------------

def open_app(name: str) -> str:
    proc = subprocess.run(
        ["open", "-a", name], capture_output=True, text=True, timeout=10
    )
    if proc.returncode != 0:
        return f"Failed: {proc.stderr.strip() or 'unknown error'}"
    return f"Opened {name}."


# ---------------------------------------------------------------------------
# 5. open_in_vscode
# ---------------------------------------------------------------------------

def open_in_vscode(path: str, line: int | None = None) -> str:
    full = _expand(path)
    if not Path(full).exists():
        return f"Path does not exist: {full}"
    target = f"{full}:{line}" if line else full
    proc = subprocess.run(
        ["code", "-g", target], capture_output=True, text=True, timeout=10
    )
    if proc.returncode != 0:
        return f"Failed: {proc.stderr.strip() or 'code CLI not installed?'}"
    where = f" at line {line}" if line else ""
    return f"Opened {full}{where} in VS Code."


# ---------------------------------------------------------------------------
# 6. vscode_command
# ---------------------------------------------------------------------------

_VSCODE_ACTIONS: dict[str, tuple[str, list[str]]] = {
    "save":             ("s", ["command"]),
    "save_all":         ("s", ["command", "option"]),
    "quick_open":       ("p", ["command"]),
    "command_palette":  ("p", ["command", "shift"]),
    "find_in_file":     ("f", ["command"]),
    "find_in_files":    ("f", ["command", "shift"]),
    "replace_in_file":  ("h", ["command", "option"]),
    "go_to_line":       ("g", ["control"]),
    "open_terminal":    ("`", ["control"]),
    "toggle_sidebar":   ("b", ["command"]),
    "toggle_explorer":  ("e", ["command", "shift"]),
    "format_document":  ("f", ["option", "shift"]),
    "new_file":         ("n", ["command"]),
    "close_tab":        ("w", ["command"]),
    "next_tab":         ("]", ["command", "option"]),
    "prev_tab":         ("[", ["command", "option"]),
    "split_right":      ("\\", ["command"]),
    "comment_line":     ("/", ["command"]),
    "undo":             ("z", ["command"]),
    "redo":             ("z", ["command", "shift"]),
}


def vscode_command(action: str) -> str:
    if action not in _VSCODE_ACTIONS:
        valid = ", ".join(sorted(_VSCODE_ACTIONS))
        return f"Unknown action '{action}'. Valid actions: {valid}"
    key, mods = _VSCODE_ACTIONS[action]
    return mac_keystroke(key, modifiers=mods, app="Visual Studio Code")


# ---------------------------------------------------------------------------
# 7. read_file
# ---------------------------------------------------------------------------

def read_file(
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    full = _expand(path)
    p = Path(full)
    if not p.exists():
        return f"File not found: {full}"
    if not p.is_file():
        return f"Not a file: {full}"
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Binary file (not text): {full}"
    lines = text.splitlines()
    total = len(lines)
    if start_line is not None or end_line is not None:
        s = max(1, start_line or 1) - 1
        e = end_line if end_line is not None else total
        lines = lines[s:e]
        header = f"{full} (lines {s + 1}-{min(e, total)} of {total})"
    else:
        header = f"{full} ({total} lines)"
    body = "\n".join(lines)
    return _truncate(f"{header}\n\n{body}")


# ---------------------------------------------------------------------------
# 8. take_screenshot
# ---------------------------------------------------------------------------

def take_screenshot() -> list[dict[str, Any]]:
    """Return a tool_result content list containing a screenshot image."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        subprocess.run(
            ["screencapture", "-x", path], check=True, timeout=10
        )
        data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    finally:
        Path(path).unlink(missing_ok=True)
    return [
        {"type": "text", "text": "Screenshot captured."},
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": data,
            },
        },
    ]


# ---------------------------------------------------------------------------
# 9. spacetime  (UI-driven physics visualization)
# ---------------------------------------------------------------------------

_SPACETIME_SCENES = {"blackhole", "lightcone", "gravwave", "nbody", "curvature"}
_SPACETIME_ACTIONS = {
    "open", "close", "set_scene", "speed", "pause", "resume",
    "rotate", "zoom", "reset",
}


def spacetime(
    action: str,
    scene: str | None = None,
    value: float | None = None,
) -> str:
    if _BUS is None:
        return "Spacetime UI is not initialized yet."
    if action not in _SPACETIME_ACTIONS:
        return f"Unknown action '{action}'. Valid: {', '.join(sorted(_SPACETIME_ACTIONS))}"
    if action in {"open", "set_scene"} and scene is not None and scene not in _SPACETIME_SCENES:
        return f"Unknown scene '{scene}'. Valid: {', '.join(sorted(_SPACETIME_SCENES))}"
    payload: dict[str, Any] = {"type": "spacetime", "action": action}
    if scene is not None:
        payload["scene"] = scene
    if value is not None:
        payload["value"] = value
    _BUS.publish(payload)
    return _spacetime_ack(action, scene, value)


def _spacetime_ack(action: str, scene: str | None, value: float | None) -> str:
    if action == "open":
        return f"Spacetime open ({scene or 'default scene'})."
    if action == "close":
        return "Spacetime closed."
    if action == "set_scene":
        return f"Switched to {scene}."
    if action == "speed":
        return f"Time speed set to {value}x."
    if action == "pause":
        return "Time paused."
    if action == "resume":
        return "Time resumed."
    if action == "rotate":
        return f"Rotated by {value} degrees."
    if action == "zoom":
        return f"Zoomed by factor {value}."
    if action == "reset":
        return "View reset."
    return "OK."


# ---------------------------------------------------------------------------
# 10. graph_equation  (open the equation visualizer)
# ---------------------------------------------------------------------------

# The equation visualizer is mounted into the main server at /equations
# (see server.py). Keep the port in sync with main.PORT.
_EQUATIONS_URL = "http://127.0.0.1:8765/equations/"


def graph_equation(equation: str | None = None) -> str:
    """Open the equation visualizer in the browser, optionally pre-loaded.

    If `equation` is given it's passed via ?q=… so the page auto-interprets
    and plots it on load.
    """
    url = _EQUATIONS_URL
    if equation and equation.strip():
        url += f"?q={quote(equation.strip())}"
    proc = subprocess.run(["open", url], capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        return f"Failed to open the equation visualizer: {proc.stderr.strip() or 'unknown error'}"
    if equation and equation.strip():
        return f"Opened the equation visualizer with: {equation.strip()}"
    return "Opened the equation visualizer."


# ---------------------------------------------------------------------------
# 11. set_persona  (switch Emmy's speaking voice/style)
# ---------------------------------------------------------------------------

def set_persona(persona_key: str) -> str:
    key = persona_key.strip().lower()
    if not persona.is_valid(key):
        return (
            f"Unknown persona '{persona_key}'. Valid: {', '.join(persona.keys())}"
        )
    persona.set_current(key)
    label = persona.label(key)
    if _BUS is not None:
        _BUS.publish({"type": "persona", "key": key, "label": label})
    if key == persona.DEFAULT:
        return "Back to myself — Emmy."
    return f"Now speaking as {label}."


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "run_shell": run_shell,
    "mac_keystroke": mac_keystroke,
    "type_text": type_text,
    "open_app": open_app,
    "open_in_vscode": open_in_vscode,
    "vscode_command": vscode_command,
    "read_file": read_file,
    "take_screenshot": take_screenshot,
    "spacetime": spacetime,
    "graph_equation": graph_equation,
    "set_persona": set_persona,
}


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "run_shell",
        "description": (
            "Run any bash/zsh command on the user's Mac and return its "
            "stdout, stderr, and exit code. Use for filesystem operations, "
            "git, package management, system queries, etc. The shell is "
            "non-interactive — don't run commands that need user input."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute."},
                "timeout": {
                    "type": "integer",
                    "description": "Seconds before the command is killed. Default 30.",
                    "default": 30,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "mac_keystroke",
        "description": (
            "Send a single keystroke (optionally with modifiers) to a macOS "
            "app. Use for keyboard shortcuts. For typing strings, use type_text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": (
                        "Single character ('a', '/', '`') or special key name: "
                        "return, tab, space, delete, escape, left, right, up, "
                        "down, home, end, pageup, pagedown, f1-f12."
                    ),
                },
                "modifiers": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["cmd", "shift", "ctrl", "option"],
                    },
                    "description": "Modifier keys held while pressing `key`.",
                },
                "app": {
                    "type": "string",
                    "description": (
                        "Optional app to activate first (e.g. 'Visual Studio "
                        "Code', 'Safari'). If omitted, sends to whatever's focused."
                    ),
                },
            },
            "required": ["key"],
        },
    },
    {
        "name": "type_text",
        "description": "Type a string of text into the focused window (or a specific app).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type."},
                "app": {"type": "string", "description": "Optional app to activate first."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "open_app",
        "description": "Launch or focus a macOS application by name.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "App name as it appears in /Applications, e.g. 'Slack', 'Google Chrome'.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "open_in_vscode",
        "description": (
            "Open a file or folder in VS Code. Optionally jump to a specific line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File or directory path (supports ~).",
                },
                "line": {
                    "type": "integer",
                    "description": "Optional line number to jump to.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "vscode_command",
        "description": (
            "Trigger a VS Code action by name. Activates VS Code first, then "
            "sends the appropriate keyboard shortcut. Valid actions: "
            "save, save_all, quick_open, command_palette, find_in_file, "
            "find_in_files, replace_in_file, go_to_line, open_terminal, "
            "toggle_sidebar, toggle_explorer, format_document, new_file, "
            "close_tab, next_tab, prev_tab, split_right, comment_line, "
            "undo, redo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": sorted(_VSCODE_ACTIONS.keys()),
                    "description": "Named VS Code action to perform.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a text file from disk. Optionally just a line range.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (supports ~)."},
                "start_line": {"type": "integer", "description": "1-indexed first line."},
                "end_line": {"type": "integer", "description": "1-indexed last line (inclusive)."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "take_screenshot",
        "description": (
            "Capture the user's screen. Returns the image so you can see "
            "what's currently on it (visible windows, app states, errors, etc.)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "spacetime",
        "description": (
            "Control the fullscreen physics-flavored 'spacetime' visualization "
            "in the user's UI. Use this whenever the user asks to see, open, "
            "close, switch, speed up, slow down, pause, resume, rotate, zoom, "
            "or reset the spacetime view.\n\n"
            "Scenes: blackhole (Schwarzschild horizon + warped grid), "
            "lightcone (Minkowski diagram with worldlines), "
            "gravwave (binary inspiral with strain ripples), "
            "nbody (N-body gravitational simulation), "
            "curvature (rubber-sheet diagram with orbits).\n\n"
            "Actions:\n"
            "- open: open the overlay (optionally specify `scene`, default blackhole)\n"
            "- close: close the overlay\n"
            "- set_scene: switch to a different scene (requires `scene`)\n"
            "- speed: set time multiplier (`value`: e.g. 0.5, 1.0, 2.0, 5.0)\n"
            "- pause / resume: freeze or unfreeze time\n"
            "- rotate: rotate camera by `value` degrees around the up axis\n"
            "- zoom: multiply camera distance by 1/`value` (value>1 zooms in)\n"
            "- reset: reset camera, speed, and pause state"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "open", "close", "set_scene", "speed",
                        "pause", "resume", "rotate", "zoom", "reset",
                    ],
                },
                "scene": {
                    "type": "string",
                    "enum": ["blackhole", "lightcone", "gravwave", "nbody", "curvature"],
                    "description": "Scene to load. Used with `open` or `set_scene`.",
                },
                "value": {
                    "type": "number",
                    "description": "Numeric argument for speed, rotate, zoom.",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "graph_equation",
        "description": (
            "Open the interactive equation visualizer in the user's browser. "
            "Use this whenever the user wants to see, plot, graph, or visualize "
            "an equation or function (e.g. 'graph the Hawking spectrum', 'plot "
            "sin(x)/x', 'show me the Planck distribution'). Pass the equation in "
            "`equation` (natural language, LaTeX, or a numpy-style expression are "
            "all fine) and it loads pre-filled and auto-plotted, with sliders for "
            "any free parameters. Omit `equation` to just open a blank visualizer. "
            "This is for plotting math/functions — for the 3D physics scenes "
            "(black holes, light cones, etc.) use the `spacetime` tool instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "equation": {
                    "type": "string",
                    "description": (
                        "The equation or function to plot. Natural language, "
                        "LaTeX, or a numpy expression. Optional."
                    ),
                },
            },
        },
    },
    {
        "name": "set_persona",
        "description": (
            "Change the voice/personality Emmy speaks in. Call this when the user "
            "asks you to talk like, be, become, or switch to a character, or to go "
            "back to your normal self. Every reply afterward takes on that "
            "character's manner (and a matching voice) until changed again. "
            "Personas: emmy (your normal self), einstein, shiva, krishna, durga, "
            "batman, spiderman, ironman."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "persona_key": {
                    "type": "string",
                    "enum": [
                        "emmy", "einstein", "shiva", "krishna",
                        "durga", "batman", "spiderman", "ironman",
                    ],
                    "description": "Which persona to switch to.",
                },
            },
            "required": ["persona_key"],
        },
    },
]


def dispatch(name: str, args: dict[str, Any]) -> str | list[dict[str, Any]]:
    """Look up and call a tool by name. Returns whatever the tool returned."""
    if name not in TOOL_FUNCTIONS:
        return f"Unknown tool: {name}"
    try:
        return TOOL_FUNCTIONS[name](**args)
    except Exception as exc:
        return f"Tool '{name}' raised {type(exc).__name__}: {exc}"
