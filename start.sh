#!/usr/bin/env bash
#
# Noether — one command to set up (if needed) and run everything.
#
#   ./start.sh
#
# First run creates both Python environments, installs deps, downloads the
# voice models, and builds the cloned-voice references. Every run after that
# skips straight to launching. Idempotent and safe to re-run.
#
# Requirements: macOS on Apple Silicon, Homebrew, Python 3.11+ (for the app)
# and Python 3.12 (for the neural voices; `brew install python@3.12`).
set -euo pipefail
cd "$(dirname "$0")"

say() { printf "\033[1;36m==>\033[0m %s\n" "$1"; }

# --- pick a Python 3.11+ for the main app -----------------------------------
pick_python() {
  for p in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$p" >/dev/null 2>&1 && \
       "$p" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,11) else 1)'; then
      echo "$p"; return 0
    fi
  done
  echo "ERROR: need Python 3.11+ for the app." >&2; exit 1
}

# --- 0. ffmpeg (neural voice conversion + building voice refs) ---------------
if ! command -v ffmpeg >/dev/null 2>&1; then
  say "Installing ffmpeg (Homebrew)"
  brew install ffmpeg
fi

# --- 1. main app env (.venv) -------------------------------------------------
if [ ! -x .venv/bin/python ]; then
  PY="$(pick_python)"
  say "Creating main app env (.venv) with $PY"
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

# --- 2. API key --------------------------------------------------------------
if [ ! -f .env ]; then
  cp .env.example .env
  say "Created .env — edit it and set ANTHROPIC_API_KEY before talking to Emmy."
fi
if ! grep -q "sk-ant" .env 2>/dev/null; then
  printf "\033[1;33m!!  .env has no ANTHROPIC_API_KEY yet — Emmy can't think without it.\033[0m\n"
fi

# --- 3. neural voices env (.venv-tts) + models + refs (recommended) ----------
if [ ! -x .venv-tts/bin/python ] || [ ! -f tts_models/kokoro-v1.0.onnx ]; then
  say "Setting up neural voices — downloads models (~1 GB), one time"
  ./setup_neural_tts.sh
fi

# --- 4. launch ---------------------------------------------------------------
say "Starting Noether — opening http://127.0.0.1:8765/"
exec .venv/bin/python main.py
