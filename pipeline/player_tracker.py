"""A tiny, dependency-free ByteTrack-style two-pass tracker.

ByteTrack's key idea is retained: associate high-confidence detections first,
then recover unmatched tracks with lower-confidence detections.  This version
uses greedy IoU/center-distance association because only one 1-FPS frame is in
memory and scipy is deliberately avoided.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import RuntimeSettings, TEMP_DIR
from pipeline.common import (ProgressCallback, StopCheck, atomic_write_json,
                             eta_text, read_json, report, stop_if_requested)
from utils.logger import setup_logger
from utils.memory_manager import MemoryManager

LOG = setup_logger("tracker")
DET_RE = re.compile(r"det_(\d+)\.json$")


def _xyxy(item: dict[str, Any]) -> tuple[float, float, float, float]:
    x, y, w, h = item["bbox"]
    return x, y, x + w, y + h


def _iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1, ax2, ay2 = _xyxy(a); bx1, by1, bx2, by2 = _xyxy(b)
    area = max(0, min(ax2, bx2) - max(ax1, bx1)) * max(0, min(ay2, by2) - max(ay1, by1))
    union = max(1e-6, (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - area)
    return area / union


def _center_distance(a: dict[str, Any], b: dict[str, Any], diagonal: float) -> float:
    ax, ay, aw, ah = a["bbox"]; bx, by, bw, bh = b["bbox"]
    return math.hypot(ax + aw / 2 - bx - bw / 2, ay + ah / 2 - by - bh / 2) / max(diagonal, 1)


@dataclass
class Track:
    track_id: int
    detection: dict[str, Any]
    missed: int = 0


class ByteTrackLite:
    def __init__(self, high_threshold: float = 0.5, low_threshold: float = 0.1,
                 max_missed: int = 4):
        self.high_threshold = high_threshold
        self.low_threshold = low_threshold
        self.max_missed = max_missed
        self.tracks: list[Track] = []
        self.next_id = 1

    def _associate(self, detections: list[dict[str, Any]], candidates: list[int],
                   diagonal: float, excluded_tracks: set[int] | None = None) -> tuple[set[int], set[int]]:
        pairs = []
        excluded_tracks = excluded_tracks or set()
        for ti, track in enumerate(self.tracks):
            if ti in excluded_tracks:
                continue
            for di in candidates:
                detection = detections[di]
                if track.detection["class"] != detection["class"]:
                    continue
                overlap = _iou(track.detection, detection)
                distance = _center_distance(track.detection, detection, diagonal)
                score = overlap + max(0.0, 0.25 - distance)
                if overlap >= 0.10 or distance <= 0.12:
                    pairs.append((score, ti, di))
        matched_tracks: set[int] = set(); matched_detections: set[int] = set()
        for _, ti, di in sorted(pairs, reverse=True):
            if ti in matched_tracks or di in matched_detections:
                continue
            self.tracks[ti].detection = detections[di]
            self.tracks[ti].missed = 0
            detections[di]["track_id"] = self.tracks[ti].track_id
            matched_tracks.add(ti); matched_detections.add(di)
        return matched_tracks, matched_detections

    def update(self, detections: list[dict[str, Any]], image_size: list[int]) -> list[dict[str, Any]]:
        width, height = image_size or [1, 1]
        diagonal = math.hypot(width, height)
        for track in self.tracks:
            track.missed += 1
        high = [i for i, d in enumerate(detections) if d.get("confidence", 0) >= self.high_threshold]
        low = [i for i, d in enumerate(detections)
               if self.low_threshold <= d.get("confidence", 0) < self.high_threshold]
        matched_tracks, matched_high = self._associate(detections, high, diagonal)
        # Second ByteTrack pass uses weak detections only for still-unmatched tracks.
        matched_low_tracks, matched_low = self._associate(
            detections, low, diagonal, excluded_tracks=matched_tracks
        )
        matched_tracks |= matched_low_tracks
        for di in high:
            if di not in matched_high:
                detection = detections[di]
                detection["track_id"] = self.next_id
                self.tracks.append(Track(self.next_id, detection, 0))
                self.next_id += 1
        self.tracks = [track for track in self.tracks if track.missed <= self.max_missed]
        return detections


class PlayerTracker:
    def __init__(self, settings: RuntimeSettings, memory: MemoryManager | None = None):
        self.settings = settings
        self.memory = memory or MemoryManager(settings.max_ram_usage_percent)
        self.input_dir = TEMP_DIR / "detections"
        self.output_dir = TEMP_DIR / "tracks"

    def track(self, resume: bool = False, progress: ProgressCallback | None = None,
              stop_check: StopCheck | None = None) -> dict[str, Any]:
        files = sorted(self.input_dir.glob("det_*.json"))
        if not files:
            raise RuntimeError("No detection JSON files found. Run object detection first.")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Stateful tracking must replay earlier detections after resume, but
        # completed JSON output can remain on disk and be overwritten atomically.
        if not resume:
            for old in self.output_dir.glob("track_*.json"):
                old.unlink(missing_ok=True)
        tracker = ByteTrackLite(high_threshold=self.settings.yolo_confidence,
                                low_threshold=max(0.1, self.settings.yolo_confidence * 0.5))
        started = time.monotonic(); total = len(files)
        for index, source in enumerate(files, 1):
            stop_if_requested(stop_check)
            record = read_json(source, {})
            tracked = tracker.update(record.get("detections", []), record.get("image_size", [1, 1]))
            record["detections"] = tracked
            frame_id = int(record.get("frame_id", index))
            atomic_write_json(self.output_dir / f"track_{frame_id:05d}.json", record)
            if index == 1 or index % 50 == 0 or index == total:
                message = (f"Tracking players: {index}/{total} ({int(index * 100 / total)}%) - "
                           f"{eta_text(started, index, total)}")
                report(progress, index, total, message)
            if index % self.settings.frame_batch_size == 0:
                self.memory.check_resources()
        self.memory.release("ByteTrack state released")
        return {"total_frames": total, "track_dir": str(self.output_dir),
                "tracks_created": tracker.next_id - 1}
