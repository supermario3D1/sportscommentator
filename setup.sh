#!/usr/bin/env bash
# One-click setup for Fedora and Ubuntu. Safe to rerun after interruption.
set -euo pipefail

cd "$(dirname "$0")"
echo "=== AI Sports Commentator Setup ==="

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install Python 3.10 or newer." >&2
  exit 1
fi
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"Python 3.10+ is required; found {sys.version.split()[0]}")
print("Python", sys.version.split()[0], "OK")
PY

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is needed to install FFmpeg/system packages." >&2; exit 1
  fi
  SUDO="sudo"
fi

if command -v dnf >/dev/null 2>&1; then
  echo "Installing Fedora system dependencies..."
  $SUDO dnf install -y python3-devel ffmpeg-free curl git gcc gcc-c++ pciutils
elif command -v apt-get >/dev/null 2>&1; then
  echo "Installing Ubuntu system dependencies..."
  $SUDO apt-get update
  $SUDO apt-get install -y python3-venv python3-dev ffmpeg curl git build-essential pciutils
else
  echo "Unsupported package manager. Install FFmpeg, Python headers, curl, and git manually." >&2
fi

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools

echo "Installing CPU-only PyTorch first (no CUDA libraries)..."
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt

if ! command -v ollama >/dev/null 2>&1; then
  echo "Installing Ollama from the official installer..."
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "Ollama already installed."
fi

python install_models.py
python -m config.hardware_detect

chmod +x run.sh setup.sh install_models.py
echo
echo "Setup complete! Run: ./run.sh"
echo "Optional voice cloning: source .venv/bin/activate && python install_models.py --skip-yolo --skip-llm --skip-voices --openvoice"
