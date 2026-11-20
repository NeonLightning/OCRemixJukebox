#!/usr/bin/env bash
set -euo pipefail

python3 -m PyInstaller \
  --onefile \
  --windowed \
  --name "OCRemixJukebox" \
  --collect-all yt_dlp \
  --hidden-import yt_dlp.extractor \
  --hidden-import yt_dlp.downloader \
  --hidden-import vlc \
  --exclude-module tkinter \
  --exclude-module unittest \
  --exclude-module test \
  --exclude-module pydoc \
  ocremix.py
