#!/bin/sh
# fPKG2Zar launcher for macOS and Linux: sets up a local virtual environment
# on first run, builds the Kraken decoder if needed, then opens the GUI.
# Usage: ./start.sh [game.pkg ...]
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 was not found. Install Python 3.10 or newer first." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "Creating .venv and installing PyQt6 + zstandard ..."
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi

if [ ! -f fpkg2zar/libkraken.so ] && [ ! -f fpkg2zar/libkraken.dylib ]; then
  echo "Building the Kraken decoder ..."
  sh native/build.sh
fi

exec .venv/bin/python -m fpkg2zar.gui "$@"
