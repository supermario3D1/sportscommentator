"""Fast final mux: copy video stream and encode only commentary audio."""
from __future__ import annotations

import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import OUTPUT_DIR, RuntimeSettings, TEMP_DIR
from pipeline.common import ProgressCallback, StopCheck, report, stop_if_requested
from utils.logger import setup_logger

LOG = setup_logger("export")


class VideoExporter:
    def __init__(self, settings: RuntimeSettings):
        self.settings = settings

    def export(self, video_path: str | Path, output_path: str | Path | None = None,
               progress: ProgressCallback | None = None,
               stop_check: StopCheck | None = None) -> dict[str, Any]:
        video = Path(video_path); audio = TEMP_DIR / "final_audio.wav"
        if not video.is_file():
            raise FileNotFoundError(f"Original video is missing: {video}")
        if not audio.is_file():
            raise FileNotFoundError(f"Mixed audio is missing: {audio}")
        ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        if not ffmpeg:
            raise RuntimeError("FFmpeg was not found on PATH.")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        if output_path:
            output = Path(output_path)
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output = OUTPUT_DIR / f"match_with_commentary_{timestamp}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        stop_if_requested(stop_check)
        report(progress, 0, 1, "Exporting video: copying the original video stream...")
        command = [
            ffmpeg, "-y", "-v", "warning", "-i", str(video), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-c:a", self.settings.audio_codec, "-b:a", self.settings.audio_bitrate,
            "-movflags", "+faststart", "-shortest", str(output),
        ]
        started = time.monotonic()
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        if process.returncode != 0 or not output.is_file():
            output.unlink(missing_ok=True)
            raise RuntimeError(
                "FFmpeg could not copy this video's codec into MP4. The source may use an "
                f"incompatible video codec. Error: {process.stderr.strip()}"
            )
        report(progress, 1, 1, "Video export: 1/1 (100%)")
        result = {
            "output_path": str(output.resolve()),
            "size_bytes": output.stat().st_size,
            "elapsed_sec": round(time.monotonic() - started, 2),
            "video_reencoded": False,
        }
        LOG.info("Final video ready: %s (%.1f MiB)", output, result["size_bytes"] / 1024 ** 2)
        return result
