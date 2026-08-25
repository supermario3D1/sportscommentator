"""Startup component check.

The pipeline fails deep into a run when a component is missing: FFmpeg only
matters for the last two stages, and a Piper voice is only touched after
commentary has been written.  Checking once at startup turns a one-hour
detour into a five-second message.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from config.settings import MODEL_DIR, RuntimeSettings


def check_components(settings: RuntimeSettings) -> list[str]:
    """Return one line per missing component; empty means everything is ready."""
    problems: list[str] = []

    for command, purpose in (
        ("ffmpeg", "audio mixing and the final video export"),
        ("ffprobe", "measuring the source video duration"),
    ):
        if not (shutil.which(command) or shutil.which(f"{command}.exe")):
            problems.append(
                f"FFmpeg's '{command}' was not found on PATH; it is required for {purpose}. "
                "Install FFmpeg, open a new terminal, then start again (see INSTALL.md)."
            )

    yolo = MODEL_DIR / "yolov8n.onnx"
    if not yolo.is_file():
        problems.append(
            f"Object-detection model is missing ({yolo}). "
            "Run: .venv/Scripts/python install_models.py   (or .venv/bin/python on Linux)"
        )

    voice = MODEL_DIR / "piper" / f"{settings.piper_voice}.onnx"
    voice_config = Path(str(voice) + ".json")
    if not voice.is_file() or not voice_config.is_file():
        problems.append(
            f"Piper voice '{settings.piper_voice}' is incomplete under {MODEL_DIR / 'piper'}. "
            "Run: .venv/Scripts/python install_models.py --voices"
        )

    if not (shutil.which("ollama") or shutil.which("ollama.exe")):
        problems.append(
            "Ollama was not found on PATH, so commentary will use the built-in rule-based "
            "fallback instead of a local language model. Install Ollama for full quality."
        )

    if settings.use_voice_cloning:
        converter = MODEL_DIR / "openvoice" / "checkpoints_v2" / "converter"
        if not (converter / "config.json").is_file() or not (converter / "checkpoint.pth").is_file():
            problems.append(
                "Voice cloning was requested, but the OpenVoice V2 checkpoints are missing. "
                "Run: .venv/Scripts/python install_models.py --openvoice  "
                "(the built-in Piper voice is used until then)"
            )

    return problems


def preflight_summary(settings: RuntimeSettings) -> list[str]:
    """Console lines describing the component check."""
    problems = check_components(settings)
    if not problems:
        return ["Preflight: FFmpeg, YOLOv8n, Ollama, and the Piper voice are ready."]
    lines = ["Preflight: some components need attention before processing will finish:"]
    lines.extend(f"  - {problem}" for problem in problems)
    lines.append("  Starting anyway; fix these before processing a real match.")
    return lines
