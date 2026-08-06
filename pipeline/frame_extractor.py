"""OpenCV frame sampling that never retains more than one decoded frame."""
from __future__ import annotations

import math
import re
import time
from pathlib import Path
from typing import Any

from config.settings import RuntimeSettings, TEMP_DIR
from pipeline.common import (ProgressCallback, StopCheck, atomic_write_json,
                             eta_text, report, stop_if_requested)
from utils.logger import setup_logger
from utils.memory_manager import MemoryManager

LOG = setup_logger("frames")
FRAME_RE = re.compile(r"frame_(\d+)\.jpg$")


class FrameExtractor:
    def __init__(self, settings: RuntimeSettings, memory: MemoryManager | None = None):
        self.settings = settings
        self.memory = memory or MemoryManager(settings.max_ram_usage_percent)
        self.output_dir = TEMP_DIR / "frames"
        self.manifest_path = TEMP_DIR / "frames_manifest.json"

    def extract(self, video_path: str | Path, resume: bool = False,
                progress: ProgressCallback | None = None,
                stop_check: StopCheck | None = None) -> dict[str, Any]:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV is not installed. Run setup.sh or pip install opencv-python-headless.") from exc

        video_path = Path(video_path)
        if not video_path.is_file():
            raise FileNotFoundError(f"Match video does not exist: {video_path}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not resume:
            for old in self.output_dir.glob("frame_*.jpg"):
                old.unlink(missing_ok=True)

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            raise RuntimeError(f"OpenCV could not open {video_path}. Check the codec and FFmpeg installation.")
        try:
            source_fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
            source_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            duration = source_frames / source_fps if source_fps > 0 and source_frames > 0 else 0
            if duration <= 0:
                duration_ms = float(capture.get(cv2.CAP_PROP_POS_MSEC) or 0)
                duration = duration_ms / 1000
            if duration <= 0:
                raise RuntimeError("Video duration is unavailable; remux the file with FFmpeg and try again.")
            sample_fps = max(0.1, self.settings.frame_extraction_fps)
            interval = 1.0 / sample_fps
            # Sample at 1s, 2s, ... exactly as timestamps are reported downstream.
            total = max(1, int(math.floor(duration * sample_fps)))
            existing_ids = []
            if resume:
                for frame_path in self.output_dir.glob("frame_*.jpg"):
                    match = FRAME_RE.match(frame_path.name)
                    if match and frame_path.stat().st_size > 0:
                        existing_ids.append(int(match.group(1)))
            start_id = max(existing_ids, default=0) + 1
            if start_id > total:
                start_id = total + 1
            started = time.monotonic()

            for frame_id in range(start_id, total + 1):
                stop_if_requested(stop_check)
                timestamp = (frame_id - 1) * interval
                # Seeking prevents decoding all 162k source frames for a 90-minute
                # match. Only the requested sample is ever held in memory.
                capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
                ok, frame = capture.read()
                if not ok or frame is None:
                    LOG.warning("Could not decode sample %d at %.2fs; continuing.", frame_id, timestamp)
                    continue
                output = self.output_dir / f"frame_{frame_id:05d}.jpg"
                ok = cv2.imwrite(str(output), frame, [cv2.IMWRITE_JPEG_QUALITY, self.settings.jpeg_quality])
                del frame
                if not ok:
                    raise RuntimeError(f"OpenCV failed to write {output}. Check free disk space.")

                completed_this_run = frame_id - start_id + 1
                if frame_id == 1 or frame_id % 25 == 0 or frame_id == total:
                    pct = int(frame_id * 100 / total)
                    message = (f"Extracting frames: {frame_id}/{total} ({pct}%) - "
                               f"{eta_text(started, completed_this_run, total - start_id + 1)}")
                    report(progress, frame_id, total, message)
                if frame_id % self.settings.frame_batch_size == 0:
                    self.memory.check_resources()
                    self.memory.release("frame extraction batch")

            width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            actual = len(list(self.output_dir.glob("frame_*.jpg")))
            manifest = {
                "video_path": str(video_path.resolve()),
                "duration_sec": duration,
                "source_fps": source_fps,
                "sample_fps": sample_fps,
                "sample_interval_sec": interval,
                "expected_frames": total,
                "total_frames": actual,
                "width": width,
                "height": height,
                "frame_dir": str(self.output_dir),
            }
            atomic_write_json(self.manifest_path, manifest)
            LOG.info("Frame extraction complete: %d compressed JPEGs in %s", actual, self.output_dir)
            return manifest
        finally:
            capture.release()
            self.memory.release("OpenCV capture closed")
