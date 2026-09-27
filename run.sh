#!/usr/bin/env bash
# macOS / Linux launcher
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Setting up FFMPEG-Forge (one time)..."
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip --quiet
  ./.venv/bin/python -m pip install --quiet PySide6-Essentials
fi
exec ./.venv/bin/python ffmpeg_forge.py
