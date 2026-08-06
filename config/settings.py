"""Central configuration for the disk-backed sports commentary pipeline.

Constants are intentionally conservative.  ``build_runtime_settings`` creates a
per-run copy, so hardware detection and UI choices never mutate module globals.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Processing
FRAME_EXTRACTION_FPS = 1.0
FRAME_BATCH_SIZE = 200
MAX_RAM_USAGE_PERCENT = 70.0
TEMP_DIR = PROJECT_ROOT / "temp"
OUTPUT_DIR = PROJECT_ROOT / "outputs"
CHECKPOINT_DIR = PROJECT_ROOT / "checkpoints"
UPLOAD_DIR = PROJECT_ROOT / "uploads"
MODEL_DIR = PROJECT_ROOT / "models"
FRAME_JPEG_QUALITY = 80
AUDIO_CHUNK_SECONDS = 60

# Detection
YOLO_MODEL = "yolov8n"
YOLO_CONFIDENCE = 0.50
DETECTION_CLASSES = ("person", "sports ball")
YOLO_INPUT_SIZE = 640

# Event detection
GOAL_AREA_THRESHOLD = 0.15
CLUSTER_DISTANCE = 100.0
BALL_SPEED_THRESHOLD = 50.0

# LLM
OLLAMA_MODEL = "phi3:mini"
OLLAMA_FALLBACK = "tinyllama"
LLM_TEMPERATURE = 0.8
LLM_MAX_TOKENS = 150

# Voice
PIPER_VOICE = "en_US-lessac-medium"
USE_VOICE_CLONING = False
VOICE_CLONE_MODEL = "openvoice_v2"

# Audio/output
COMMENTARY_VOLUME = 1.0
ORIGINAL_AUDIO_DUCK_LEVEL = 0.3
DUCK_FADE_MS = 500
OUTPUT_FORMAT = "mp4"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"

VOICE_MODELS = {
    "Male British": "en_GB-alan-medium",
    "Male American": "en_US-ryan-medium",
    "Female British": "en_GB-alba-medium",
    "Female American": "en_US-lessac-medium",
}


@dataclass(frozen=True)
class RuntimeSettings:
    """Immutable settings passed explicitly between pipeline stages."""

    frame_extraction_fps: float = FRAME_EXTRACTION_FPS
    frame_batch_size: int = FRAME_BATCH_SIZE
    max_ram_usage_percent: float = MAX_RAM_USAGE_PERCENT
    jpeg_quality: int = FRAME_JPEG_QUALITY
    yolo_confidence: float = YOLO_CONFIDENCE
    yolo_input_size: int = YOLO_INPUT_SIZE
    detection_classes: tuple[str, ...] = DETECTION_CLASSES
    goal_area_threshold: float = GOAL_AREA_THRESHOLD
    cluster_distance: float = CLUSTER_DISTANCE
    ball_speed_threshold: float = BALL_SPEED_THRESHOLD
    ollama_model: str = OLLAMA_MODEL
    ollama_fallback: str = OLLAMA_FALLBACK
    llm_temperature: float = LLM_TEMPERATURE
    llm_max_tokens: int = LLM_MAX_TOKENS
    piper_voice: str = PIPER_VOICE
    use_voice_cloning: bool = USE_VOICE_CLONING
    commentary_volume: float = COMMENTARY_VOLUME
    original_audio_duck_level: float = ORIGINAL_AUDIO_DUCK_LEVEL
    duck_fade_ms: int = DUCK_FADE_MS
    audio_chunk_seconds: int = AUDIO_CHUNK_SECONDS
    audio_codec: str = AUDIO_CODEC
    audio_bitrate: str = AUDIO_BITRATE
    cpu_threads: int = 1
    compute_backend: str = "CPU"
    execution_provider: str = "CPUExecutionProvider"
    low_ram_mode: bool = False
    key_events_only: bool = False
    sport_type: str = "Football"
    commentary_style: str = "Excited"
    commentary_frequency: int = 5
    review_commentary: bool = True

    def with_overrides(self, **values: Any) -> "RuntimeSettings":
        valid = {k: v for k, v in values.items() if hasattr(self, k)}
        return replace(self, **valid)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["detection_classes"] = list(self.detection_classes)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeSettings":
        values = dict(data)
        if "detection_classes" in values:
            values["detection_classes"] = tuple(values["detection_classes"])
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in values.items() if k in allowed})


def build_runtime_settings(hardware_overrides: dict[str, Any] | None = None,
                           user_overrides: dict[str, Any] | None = None) -> RuntimeSettings:
    settings = RuntimeSettings()
    if hardware_overrides:
        settings = settings.with_overrides(**hardware_overrides)
    if user_overrides:
        settings = settings.with_overrides(**user_overrides)
    return settings


def ensure_directories() -> None:
    """Create every mutable directory before the first pipeline stage."""
    for path in (
        TEMP_DIR, OUTPUT_DIR, CHECKPOINT_DIR, UPLOAD_DIR, MODEL_DIR,
        TEMP_DIR / "frames", TEMP_DIR / "detections", TEMP_DIR / "tracks",
        TEMP_DIR / "commentary", TEMP_DIR / "audio_clips",
        TEMP_DIR / "audio_chunks", MODEL_DIR / "piper",
    ):
        path.mkdir(parents=True, exist_ok=True)
