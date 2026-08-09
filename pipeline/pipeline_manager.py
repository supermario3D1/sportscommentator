"""Sequential orchestration, atomic checkpoints, pause/resume, and review gate."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from config.settings import (CHECKPOINT_DIR, TEMP_DIR, RuntimeSettings,
                             ensure_directories)
from pipeline.common import PipelinePaused, atomic_write_json, read_json
from utils.disk_manager import (cleanup_old_temp, clear_working_temp,
                                file_fingerprint, require_free_space)
from utils.logger import setup_logger
from utils.memory_manager import MemoryManager

LOG = setup_logger("pipeline", CHECKPOINT_DIR / "pipeline.log")
PipelineProgress = Callable[[dict[str, Any]], None]


class PipelineManager:
    STAGES = (
        "frame_extraction", "object_detection", "player_tracking",
        "event_detection", "commentary_generation", "voice_synthesis",
        "audio_mixing", "video_export",
    )
    STAGE_LABELS = {
        "frame_extraction": "Frame Extraction",
        "object_detection": "Object Detection",
        "player_tracking": "Player Tracking",
        "event_detection": "Event Detection",
        "commentary_generation": "Commentary Generation",
        "voice_synthesis": "Voice Synthesis",
        "audio_mixing": "Audio Mixing",
        "video_export": "Video Export",
    }
    ESTIMATES = {
        "frame_extraction": "3-5 min for a 90-minute match",
        "object_detection": "15-30 min on a Ryzen 7 CPU",
        "player_tracking": "2-5 min",
        "event_detection": "1-2 min",
        "commentary_generation": "3-10 min depending on event count",
        "voice_synthesis": "2-5 min with Piper; longer with OpenVoice",
        "audio_mixing": "2-3 min",
        "video_export": "1-2 min",
    }

    def __init__(self, settings: RuntimeSettings):
        ensure_directories()
        self.settings = settings
        self.checkpoint_path = CHECKPOINT_DIR / "pipeline_checkpoint.json"
        self.control_path = CHECKPOINT_DIR / "control.json"
        self.state: dict[str, Any] = {}
        self.memory = MemoryManager(settings.max_ram_usage_percent)
        # Never age out a paused/running job: resume reliability takes priority.
        prior = read_json(self.checkpoint_path, {})
        if not prior or prior.get("status") == "complete":
            cleanup_old_temp(24)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def load_checkpoint(self) -> dict[str, Any] | None:
        state = read_json(self.checkpoint_path)
        return state if isinstance(state, dict) and state.get("video_path") else None

    def resumable_summary(self) -> str | None:
        state = self.load_checkpoint()
        if not state or state.get("status") == "complete":
            return None
        current = state.get("current_stage") or "unknown stage"
        completed = [self.STAGE_LABELS.get(name, name) for name, value in state.get("stages", {}).items()
                     if value.get("status") == "complete"]
        return (f"Previous progress found for {Path(state['video_path']).name}. "
                f"Resume from {self.STAGE_LABELS.get(current, current)}? "
                f"Completed: {', '.join(completed) or 'none'}")

    def _save_state(self) -> None:
        self.state["updated_at"] = self._now()
        atomic_write_json(self.checkpoint_path, self.state)

    def _save_stage(self, stage: str, status: str, progress: int,
                    data: dict[str, Any] | None = None, message: str = "") -> None:
        record = {
            "stage": stage,
            "status": status,
            "progress": int(max(0, min(100, progress))),
            "timestamp": self._now(),
            "data_path": self._data_path(stage, data or {}),
            "message": message,
            "data": data or {},
        }
        self.state.setdefault("stages", {})[stage] = record
        self.state["current_stage"] = stage
        self.state["status"] = status if status in {"paused", "failed"} else "processing"
        self._save_state()
        atomic_write_json(CHECKPOINT_DIR / f"{stage}.json", record)

    @staticmethod
    def _data_path(stage: str, data: dict[str, Any]) -> str:
        preferred = {
            "frame_extraction": "frame_dir", "object_detection": "detection_dir",
            "player_tracking": "track_dir", "event_detection": "events_path",
            "commentary_generation": "commentary_path", "voice_synthesis": "manifest_path",
            "audio_mixing": "audio_path", "video_export": "output_path",
        }
        if preferred.get(stage) in data:
            return str(data[preferred[stage]])
        defaults = {
            "frame_extraction": TEMP_DIR / "frames",
            "object_detection": TEMP_DIR / "detections",
            "player_tracking": TEMP_DIR / "tracks",
            "event_detection": TEMP_DIR / "events.json",
            "commentary_generation": TEMP_DIR / "commentary" / "commentary.json",
            "voice_synthesis": TEMP_DIR / "audio_clips" / "manifest.json",
            "audio_mixing": TEMP_DIR / "final_audio.wav",
            "video_export": "",
        }
        return str(defaults[stage])

    def request_pause(self) -> str:
        atomic_write_json(self.control_path, {"pause_requested": True, "timestamp": self._now()})
        LOG.info("Pause requested. The active stage will stop after its current item.")
        return "Pause requested. Waiting for the current frame/event/chunk to finish safely."

    def clear_pause(self) -> None:
        atomic_write_json(self.control_path, {"pause_requested": False, "timestamp": self._now()})

    def is_pause_requested(self) -> bool:
        return bool(read_json(self.control_path, {}).get("pause_requested", False))

    def _new_state(self, video: Path, voice_sample: Path | None) -> None:
        self.state = {
            "version": 1,
            "status": "ready",
            "video_path": str(video.resolve()),
            "video_fingerprint": file_fingerprint(video),
            "voice_sample": str(voice_sample.resolve()) if voice_sample else None,
            "settings": self.settings.to_dict(),
            "current_stage": self.STAGES[0],
            "stages": {},
            "review_completed": not self.settings.review_commentary,
            "started_at": self._now(),
            "processing_seconds": 0.0,
        }
        self._save_state()

    def _progress_wrapper(self, stage: str, stage_index: int,
                          callback: PipelineProgress | None):
        def update(current: int, total: int, message: str) -> None:
            percent = int(current * 100 / max(total, 1))
            global_percent = ((stage_index + percent / 100) / len(self.STAGES)) * 100
            self._save_stage(stage, "running", percent, message=message)
            if callback:
                callback({
                    "stage": stage,
                    "stage_label": self.STAGE_LABELS[stage],
                    "stage_progress": percent,
                    "overall_progress": round(global_percent, 1),
                    "message": message,
                    "state": self.state,
                })
        return update

    def _notify(self, callback: PipelineProgress | None, stage: str, message: str,
                stage_progress: int = 0) -> None:
        if callback:
            stage_index = self.STAGES.index(stage)
            callback({
                "stage": stage, "stage_label": self.STAGE_LABELS[stage],
                "stage_progress": stage_progress,
                "overall_progress": round((stage_index + stage_progress / 100) / len(self.STAGES) * 100, 1),
                "message": message, "state": self.state,
            })

    def _run_stage(self, stage: str, video: Path, voice_sample: Path | None,
                   resume_partial: bool, progress: PipelineProgress | None) -> dict[str, Any]:
        index = self.STAGES.index(stage)
        callback = self._progress_wrapper(stage, index, progress)
        LOG.info("Starting %s (estimated %s).", self.STAGE_LABELS[stage], self.ESTIMATES[stage])
        self._notify(progress, stage, f"Starting {self.STAGE_LABELS[stage]} — estimated {self.ESTIMATES[stage]}.")

        # Imports and stage objects are intentionally created one at a time. A
        # stage object is dropped before the next model can be loaded.
        if stage == "frame_extraction":
            from pipeline.frame_extractor import FrameExtractor
            worker = FrameExtractor(self.settings, self.memory)
            result = worker.extract(video, resume_partial, callback, self.is_pause_requested)
            result["frame_dir"] = str(TEMP_DIR / "frames")
        elif stage == "object_detection":
            from pipeline.object_detector import ObjectDetector
            worker = ObjectDetector(self.settings, self.memory)
            result = worker.detect(resume_partial, callback, self.is_pause_requested)
        elif stage == "player_tracking":
            from pipeline.player_tracker import PlayerTracker
            worker = PlayerTracker(self.settings, self.memory)
            result = worker.track(resume_partial, callback, self.is_pause_requested)
        elif stage == "event_detection":
            from pipeline.event_detector import EventDetector
            worker = EventDetector(self.settings)
            event_result = worker.detect(callback, self.is_pause_requested)
            result = {"event_count": len(event_result["events"]),
                      "events_path": str(TEMP_DIR / "events.json")}
        elif stage == "commentary_generation":
            from pipeline.commentary_generator import CommentaryGenerator
            worker = CommentaryGenerator(self.settings, self.memory)
            commentary = worker.generate(resume_partial, callback, self.is_pause_requested)
            result = {"line_count": len(commentary["commentary"]),
                      "commentary_path": str(TEMP_DIR / "commentary" / "commentary.json")}
        elif stage == "voice_synthesis":
            from pipeline.voice_cloner import VoiceSynthesizer
            worker = VoiceSynthesizer(self.settings, self.memory)
            voice = worker.synthesize(voice_sample, resume_partial, callback, self.is_pause_requested)
            result = {"clip_count": len(voice["clips"]), "voice_cloned": voice["voice_cloned"],
                      "manifest_path": str(TEMP_DIR / "audio_clips" / "manifest.json")}
        elif stage == "audio_mixing":
            from pipeline.audio_mixer import AudioMixer
            worker = AudioMixer(self.settings, self.memory)
            result = worker.mix(video, resume_partial, callback, self.is_pause_requested)
        elif stage == "video_export":
            from pipeline.video_exporter import VideoExporter
            worker = VideoExporter(self.settings)
            result = worker.export(video, progress=callback, stop_check=self.is_pause_requested)
        else:
            raise ValueError(f"Unknown pipeline stage: {stage}")
        del worker
        self.memory.release(f"{stage} stage boundary")
        self.memory.log_usage(stage)
        return result

    def run(self, video_path: str | Path | None = None,
            voice_sample: str | Path | None = None, resume: bool = False,
            progress: PipelineProgress | None = None) -> dict[str, Any]:
        """Run or resume all stages; returns a serializable status dictionary."""
        ensure_directories(); require_free_space(TEMP_DIR, 3.0); self.clear_pause()
        started = time.monotonic()
        if resume:
            state = self.load_checkpoint()
            if not state:
                raise RuntimeError("No previous checkpoint was found. Start a new job first.")
            self.state = state
            video = Path(state["video_path"])
            sample_value = state.get("voice_sample")
            sample = Path(sample_value) if sample_value else None
            if not video.is_file():
                raise FileNotFoundError(f"The checkpoint's video is missing: {video}")
            if file_fingerprint(video) != state.get("video_fingerprint"):
                raise RuntimeError("The source video changed since the checkpoint; start a new job.")
            self.settings = RuntimeSettings.from_dict(state.get("settings", {}))
            self.memory = MemoryManager(self.settings.max_ram_usage_percent)
            LOG.info("Resuming checkpoint at %s.", state.get("current_stage"))
        else:
            if not video_path:
                raise ValueError("Select a match video before starting processing.")
            video = Path(video_path).resolve()
            if not video.is_file():
                raise FileNotFoundError(f"Match video not found: {video}")
            sample = Path(voice_sample).resolve() if voice_sample else None
            clear_working_temp()
            for old in CHECKPOINT_DIR.glob("*.json"):
                old.unlink(missing_ok=True)
            self._new_state(video, sample)

        self.memory.warn_if_on_battery()
        try:
            for stage_index, stage in enumerate(self.STAGES):
                prior = self.state.get("stages", {}).get(stage, {})
                if prior.get("status") == "complete":
                    continue

                # Commentary can be inspected and edited while all model/audio
                # stages are unloaded. Saving edits marks this review complete.
                if stage == "voice_synthesis" and self.settings.review_commentary \
                        and not self.state.get("review_completed", False):
                    self.state["status"] = "awaiting_review"
                    self.state["current_stage"] = stage
                    self.state["processing_seconds"] = (
                        self.state.get("processing_seconds", 0) + time.monotonic() - started
                    )
                    self._save_state()
                    message = "Commentary is ready for review. Edit/save lines, then press RESUME."
                    self._notify(progress, stage, message)
                    LOG.info(message)
                    return {"status": "awaiting_review", "message": message,
                            "events": self.get_event_rows(), "state": self.state}

                resume_partial = resume and prior.get("status") in {"running", "paused", "failed"}
                self._save_stage(stage, "running", int(prior.get("progress", 0)) if resume_partial else 0,
                                 message=f"Starting {self.STAGE_LABELS[stage]}")
                try:
                    result = self._run_stage(stage, video, sample, resume_partial, progress)
                except PipelinePaused as exc:
                    previous_progress = self.state.get("stages", {}).get(stage, {}).get("progress", 0)
                    self._save_stage(stage, "paused", previous_progress, message=str(exc))
                    self.state["processing_seconds"] = self.state.get("processing_seconds", 0) + time.monotonic() - started
                    self._save_state()
                    return {"status": "paused", "message": str(exc),
                            "events": self.get_event_rows(), "state": self.state}
                self._save_stage(stage, "complete", 100, result,
                                 f"{self.STAGE_LABELS[stage]} complete")
                self._notify(progress, stage, f"{self.STAGE_LABELS[stage]} complete.", 100)

            self.state["status"] = "complete"
            self.state["current_stage"] = None
            self.state["completed_at"] = self._now()
            self.state["processing_seconds"] = self.state.get("processing_seconds", 0) + time.monotonic() - started
            self._save_state()
            export = self.state["stages"]["video_export"]["data"]
            return {"status": "complete", "message": "Processing complete.",
                    "output_path": export.get("output_path"),
                    "events": self.get_event_rows(), "state": self.state}
        except Exception as exc:
            stage = self.state.get("current_stage") or self.STAGES[0]
            previous_progress = self.state.get("stages", {}).get(stage, {}).get("progress", 0)
            self._save_stage(stage, "failed", previous_progress, message=str(exc))
            self.state["error"] = str(exc)
            self.state["processing_seconds"] = self.state.get("processing_seconds", 0) + time.monotonic() - started
            self._save_state()
            LOG.exception("Pipeline failed in %s: %s", stage, exc)
            raise

    def get_event_rows(self) -> list[list[Any]]:
        events = read_json(TEMP_DIR / "events.json", {}).get("events", [])
        commentary = read_json(TEMP_DIR / "commentary" / "commentary.json", {}).get("commentary", [])
        texts = {float(item.get("timestamp", -1)): item.get("text", "") for item in commentary}
        rows = []
        for event in events:
            timestamp = float(event.get("timestamp", 0)); whole = int(timestamp)
            rows.append([
                f"{whole // 60}:{whole % 60:02d}", event.get("type", ""),
                event.get("confidence", 0), event.get("description", ""),
                texts.get(timestamp, ""),
            ])
        return rows

    def save_commentary_edits(self, rows: Any) -> int:
        if hasattr(rows, "values"):
            rows = rows.values.tolist()
        if not isinstance(rows, list):
            raise ValueError("Commentary table data was not recognized.")
        data_path = TEMP_DIR / "commentary" / "commentary.json"
        data = read_json(data_path, {"commentary": []})
        lines = data.get("commentary", [])
        for index, row in enumerate(rows):
            if index < len(lines) and isinstance(row, (list, tuple)) and len(row) >= 5:
                text = str(row[4]).strip()
                if text:
                    lines[index]["text"] = text
        data["commentary"] = lines
        atomic_write_json(data_path, data)
        state = self.load_checkpoint() or self.state
        self.state = state
        self.state["review_completed"] = True
        # If edits are made after synthesis, invalidate every dependent stage.
        stages = self.state.setdefault("stages", {})
        for stage in self.STAGES[self.STAGES.index("voice_synthesis"):]:
            stages.pop(stage, None)
        self.state["current_stage"] = "voice_synthesis"
        self.state["status"] = "ready"
        self._save_state()
        return len(lines)

    def cleanup_temp(self) -> str:
        state = self.load_checkpoint()
        if not state or state.get("status") != "complete":
            return "Temporary data is retained because the pipeline is not complete."
        clear_working_temp()
        return "Temporary frames, detections, PCM audio, and clips were deleted. The final video is retained."
