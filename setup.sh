#!/usr/bin/env bash
# One-time installer for Fedora, Ubuntu, Debian, and Linux Mint.
# Safe to rerun after an interrupted package or model download.
set -euo pipefail

cd "$(dirname "$0")"

echo "=============================================================="
echo " AI Sports Commentator - Linux setup"
echo "=============================================================="
echo "The first setup downloads several GB and may take 10-60 minutes."
echo

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if ! command -v sudo >/dev/null 2>&1; then
    echo "ERROR: sudo is required to install system packages." >&2
    echo "Install sudo or run the required package commands as root." >&2
    exit 1
  fi
  SUDO="sudo"
fi

# Install Python and multimedia/build prerequisites before checking Python,
# making the first-run launcher useful on a minimal Linux installation.
echo "[1/6] Installing operating-system packages..."
if command -v dnf >/dev/null 2>&1; then
  $SUDO dnf install -y python3 python3-devel ffmpeg-free curl git gcc gcc-c++ pciutils
elif command -v apt-get >/dev/null 2>&1; then
  $SUDO apt-get update
  $SUDO apt-get install -y python3 python3-venv python3-dev ffmpeg curl git build-essential pciutils
else
  echo "ERROR: Automatic setup supports Fedora (dnf), Ubuntu, Debian, and Linux Mint (apt)." >&2
  echo "For other distributions, follow 'Other x86-64 Linux distributions' in INSTALL.md." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: Python 3 was not found after package installation." >&2
  exit 1
fi
python3 - <<'PY'
import shutil
import sys
from pathlib import Path
if sys.version_info < (3, 10):
    raise SystemExit(f"Python 3.10+ is required; found {sys.version.split()[0]}")
free = shutil.disk_usage(Path.cwd()).free / 1024**3
print(f"Python {sys.version.split()[0]} OK; {free:.1f} GiB disk space free.")
if free < 15:
    raise SystemExit("At least 15 GiB free disk space is required before setup.")
PY

echo
echo "[2/6] Creating an isolated Python environment..."
python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools

echo
echo "[3/6] Installing CPU-only AI and application packages..."
# Installing this first from the dedicated index prevents pip from selecting
# multi-gigabyte CUDA wheels on systems without an NVIDIA GPU.
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt

echo
echo "[4/6] Installing the local Ollama language-model service..."
if ! command -v ollama >/dev/null 2>&1; then
  curl -fsSL https://ollama.com/install.sh | sh
else
  echo "Ollama is already installed."
fi
if ! command -v ollama >/dev/null 2>&1; then
  echo "ERROR: Ollama installation completed but the ollama command is not on PATH." >&2
  echo "Open a new terminal and run bash run.sh again." >&2
  exit 1
fi

echo
echo "[5/6] Downloading and verifying YOLO, Piper, Phi-3, and TinyLlama..."
python install_models.py

echo
echo "[6/6] Checking this computer..."
python -m config.hardware_detect

chmod +x run.sh setup.sh install_models.py
touch .venv/.setup_complete

echo
echo "=============================================================="
echo " Setup complete"
echo "=============================================================="
echo "The application will start now when setup was launched by run.sh."
echo "For later launches, run: bash run.sh"
echo "Then open: http://localhost:7860"
echo
echo "Optional voice cloning instructions are in INSTALL.md."
