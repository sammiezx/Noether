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

echo "==> Building the Einstein voice-conversion reference from a public recording"
mkdir -p tts_models/refs
if [ ! -f tts_models/refs/einstein.wav ]; then
  vid="https://archive.org/download/Albert-Einstein-Speech/Real+Speech+Of+Albert+Einstein_Voice+Of+Albert+Einstein_Einstein+Was+Speaking.mp4"
  curl -L -o /tmp/einstein_src.mp4 "$vid"
  # ~14s of clean speech from the opening, gently band-limited + normalised
  ffmpeg -y -loglevel error -i /tmp/einstein_src.mp4 -ss 4 -t 14 \
    -af "highpass=f=60,loudnorm" -ar 24000 -ac 1 tts_models/refs/einstein.wav
fi

echo "==> Building the Batman voice-conversion reference (isolated Bale movie lines)"
if [ ! -f tts_models/refs/batman.wav ]; then
  UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605 Safari/605"
  rm -f /tmp/bat_*.mp3 /tmp/bat_list.txt
  # calm gravelly-Batman lines from moviesoundclips.net (no music)
  for id in 1930 1941 2604 1927; do
    curl -sL -A "$UA" -e "https://www.moviesoundclips.net/" \
      -o /tmp/bat_$id.mp3 "https://www.moviesoundclips.net/download.php?id=$id&ft=mp3"
    echo "file '/tmp/bat_$id.mp3'" >> /tmp/bat_list.txt
  done
  ffmpeg -y -loglevel error -f concat -safe 0 -i /tmp/bat_list.txt \
    -af "highpass=f=70,loudnorm" -ar 24000 -ac 1 tts_models/refs/batman.wav
fi

echo "==> Done. Neural voices ready."
echo "    Note: knn-vc (voice conversion) downloads its WavLM model via torch.hub"
echo "    on first use of a converted voice (Einstein/Batman) — needs internet once."
