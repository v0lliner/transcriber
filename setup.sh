#!/usr/bin/env bash
# One-time setup for the transcriber pipeline.
# Safe to re-run — it skips anything that's already done.
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf "\n\033[1m%s\033[0m\n" "$1"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$1"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$1"; }
die()  { printf "  \033[31m✗ %s\033[0m\n" "$1" >&2; exit 1; }

say "1/4  Checking your Mac"
if [[ "$(uname)" != "Darwin" ]]; then
  warn "Not a Mac — the transcribe step needs an Apple Silicon Mac."
  warn "See the 'Not on an Apple Silicon Mac?' section in README.md."
elif [[ "$(uname -m)" != "arm64" ]]; then
  warn "Intel Mac detected — the transcribe step (mlx-whisper) won't run."
  warn "See the 'Not on an Apple Silicon Mac?' section in README.md."
else
  ok "Apple Silicon Mac detected."
fi

say "2/4  Checking ffmpeg (the video tool)"
if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg already installed."
elif command -v brew >/dev/null 2>&1; then
  warn "ffmpeg missing — installing it with Homebrew (may take a minute)..."
  brew install ffmpeg
  ok "ffmpeg installed."
else
  die "ffmpeg is missing and Homebrew isn't installed.
     Install Homebrew first by pasting this in Terminal:
       /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\"
     Then run ./setup.sh again."
fi

say "3/4  Setting up Python"
PY=python3
if command -v python3.12 >/dev/null 2>&1; then PY=python3.12; fi
command -v "$PY" >/dev/null 2>&1 || die "python3 not found. Install Python 3.12 from https://python.org, then re-run."
if [[ ! -d venv ]]; then
  "$PY" -m venv venv
  ok "Created virtual environment (venv/)."
else
  ok "venv/ already exists — reusing it."
fi
# shellcheck disable=SC1091
source venv/bin/activate
python -m pip install --quiet --upgrade pip
warn "Installing dependencies (first time can take a few minutes)..."
python -m pip install --quiet -r requirements.txt
ok "Dependencies installed."

say "4/4  Checking models"
if [[ -f models/face_detection_yunet_2023mar.onnx && -f models/face_landmarker.task ]]; then
  ok "Face-tracking models present."
else
  warn "Model files missing from models/ — try re-downloading the project."
fi

say "✅ Setup complete!"
cat <<'EOF'

  Next — make your reels:
    • In Claude Code:   /clip ~/Desktop/your-podcast.mp4
    • Or by hand:       source venv/bin/activate && python pipeline.py my_podcast

  Finished videos land in:   projects/<name>/6_reels/
  (The first run also downloads the ~1.5 GB speech model — one time only.)
EOF
