#!/usr/bin/env bash
set -euo pipefail

python3 -m PyInstaller --noconfirm OCRemixJukebox-linux.spec
