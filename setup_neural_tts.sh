#!/usr/bin/env bash
# Sets up the neural TTS helper used for character personas (Batman, Iron Man,
# etc.). It runs in its own Python 3.12 venv because the neural stack doesn't
# build on the app's Python 3.14. The main app stays on macOS `say` for Emmy
# and only routes neural personas through this helper (see tts.py).
#
# Idempotent: safe to re-run. Requires python3.12 (e.g. `brew install python@3.12`).
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Creating .venv-tts (Python 3.12)"
python3.12 -m venv .venv-tts
.venv-tts/bin/python -m pip install --upgrade pip
.venv-tts/bin/python -m pip install -r requirements-tts.txt

echo "==> Downloading Kokoro model files into tts_models/"
mkdir -p tts_models
base="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
[ -f tts_models/voices-v1.0.bin ]   || curl -L -o tts_models/voices-v1.0.bin   "$base/voices-v1.0.bin"
[ -f tts_models/kokoro-v1.0.onnx ]  || curl -L -o tts_models/kokoro-v1.0.onnx  "$base/kokoro-v1.0.onnx"

echo "==> Done. Neural voices ready (Batman, Einstein, Shiva, Krishna, Durga, Spider-Man, Iron Man)."
