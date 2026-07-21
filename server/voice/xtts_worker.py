#!/usr/bin/env python3
"""Local consent-gated voice cloning worker for the sports commentary app.

The worker uses Coqui XTTS v2 when installed. It is intentionally invoked only
by the API after a consent receipt has been verified. It writes a single WAV
file with generated commentary lines placed at their requested timeline times.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LANGUAGE_ALIASES = {
    "english": "en",
    "en": "en",
    "en-au": "en",
    "en-gb": "en",
    "en-us": "en",
    "australian english": "en",
    "british english": "en",
    "american english": "en",
    "spanish": "es",
    "es": "es",
    "french": "fr",
    "fr": "fr",
    "german": "de",
    "de": "de",
    "italian": "it",
    "it": "it",
    "portuguese": "pt",
    "pt": "pt",
    "polish": "pl",
    "pl": "pl",
    "turkish": "tr",
    "tr": "tr",
    "russian": "ru",
    "ru": "ru",
    "dutch": "nl",
    "nl": "nl",
    "czech": "cs",
    "cs": "cs",
    "arabic": "ar",
    "ar": "ar",
    "chinese": "zh-cn",
    "mandarin": "zh-cn",
    "zh": "zh-cn",
    "zh-cn": "zh-cn",
    "japanese": "ja",
    "ja": "ja",
    "hungarian": "hu",
    "hu": "hu",
    "korean": "ko",
    "ko": "ko",
    "hindi": "hi",
    "hi": "hi",
}


@dataclass
class ScriptLine:
    id: str
    time: float
    duration: float
    text: str
    emphasis: str


def main() -> int:
    parser = argparse.ArgumentParser(description="Run local XTTS voice cloning synthesis.")
    parser.add_argument("--request", required=True, help="Path to request JSON from the Node API.")
    parser.add_argument("--output", required=True, help="Path to output WAV file.")
    args = parser.parse_args()

    request_path = Path(args.request)
    output_path = Path(args.output)
    request = json.loads(request_path.read_text(encoding="utf-8"))

    consent = request.get("consent") or {}
    if consent.get("speakerPermissionConfirmed") is not True:
        print("Speaker permission must be confirmed before voice cloning.", file=sys.stderr)
        return 2

    voice_sample = Path(request["voiceSamplePath"])
    if not voice_sample.exists():
        print(f"Voice sample not found: {voice_sample}", file=sys.stderr)
        return 3

    lines = [parse_line(line) for line in request.get("script", []) if str(line.get("text", "")).strip()]
    if not lines:
        print("No script lines to synthesize.", file=sys.stderr)
        return 4

    if os.environ.get("VOICE_CLONE_DRY_RUN") == "1":
        synthesize_dry_run(lines, output_path)
        return 0

    try:
        from TTS.api import TTS  # type: ignore
    except Exception as exc:  # pragma: no cover - only exercised without optional dependency
        print(
            "Coqui TTS is not installed. Install the optional model dependencies with:\n"
            "  python3 -m pip install -r server/voice/requirements.txt\n"
            "Then restart npm run dev:server.",
            file=sys.stderr,
        )
        print(str(exc), file=sys.stderr)
        return 12

    language = normalize_language(str(request.get("language") or "en"))
    model_name = str(request.get("modelName") or os.environ.get("VOICE_MODEL_NAME") or "tts_models/multilingual/multi-dataset/xtts_v2")
    device = choose_device()
    print(f"Loading voice cloning model: {model_name} on {device}")

    model = TTS(model_name=model_name)
    try:
        model = model.to(device)
    except Exception:
        # Some TTS wrappers do not expose .to(); the model can still run on CPU.
        pass

    temp_dir = Path(tempfile.mkdtemp(prefix="sportscommentator-xtts-lines-"))
    try:
        segments: list[tuple[float, Path]] = []
        for index, line in enumerate(lines, start=1):
            line_path = temp_dir / f"{index:04d}-{line.id}.wav"
            print(f"Synthesizing line {index}/{len(lines)} at {line.time:.2f}s: {line.text[:90]}")
            synthesize_line(model, line.text, voice_sample, language, line_path)
            segments.append((line.time, line_path))
        place_segments_on_timeline(segments, output_path)
        print(f"Wrote cloned commentary WAV: {output_path}")
        return 0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def parse_line(raw: dict[str, Any]) -> ScriptLine:
    return ScriptLine(
        id=safe_id(raw.get("id", "line")),
        time=max(0.0, to_float(raw.get("time"), 0.0)),
        duration=max(0.5, to_float(raw.get("duration"), 3.0)),
        text=" ".join(str(raw.get("text", "")).split()),
        emphasis=str(raw.get("emphasis") or "medium"),
    )


def safe_id(value: Any) -> str:
    result = "".join(character if character.isalnum() or character in "-_" else "-" for character in str(value))
    return result[:80] or "line"


def to_float(value: Any, fallback: float) -> float:
    try:
        number = float(value)
        if number != number:  # NaN
            return fallback
        return number
    except Exception:
        return fallback


def normalize_language(language: str) -> str:
    normalized = language.strip().lower()
    if normalized in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[normalized]
    if "-" in normalized and normalized.split("-")[0] in LANGUAGE_ALIASES:
        return LANGUAGE_ALIASES[normalized.split("-")[0]]
    return "en"


def choose_device() -> str:
    if os.environ.get("VOICE_CLONE_DEVICE"):
        return os.environ["VOICE_CLONE_DEVICE"]
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def synthesize_line(model: Any, text: str, speaker_wav: Path, language: str, output_path: Path) -> None:
    kwargs = {
        "text": text,
        "speaker_wav": str(speaker_wav),
        "file_path": str(output_path),
        "split_sentences": True,
    }
    try:
        model.tts_to_file(language=language, **kwargs)
    except TypeError:
        # Fallback for single-language voice cloning models.
        model.tts_to_file(**kwargs)


def place_segments_on_timeline(segments: list[tuple[float, Path]], output_path: Path) -> None:
    segments = sorted(segments, key=lambda item: item[0])
    if not segments:
        synthesize_dry_run([], output_path)
        return

    first_params = read_wave_params(segments[0][1])
    channels, sample_width, frame_rate = first_params
    if sample_width != 2:
        raise RuntimeError(f"Expected 16-bit PCM WAV from synthesis model, got sample width {sample_width}.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    current_frame = 0
    with wave.open(str(output_path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(frame_rate)

        for start_seconds, segment_path in segments:
            params = read_wave_params(segment_path)
            if params != first_params:
                raise RuntimeError(f"Segment WAV format mismatch for {segment_path.name}: {params} != {first_params}")
            target_frame = int(max(0.0, start_seconds) * frame_rate)
            if target_frame > current_frame:
                write_silence(output, target_frame - current_frame, channels, sample_width)
                current_frame = target_frame

            with wave.open(str(segment_path), "rb") as segment:
                while True:
                    frames = segment.readframes(8192)
                    if not frames:
                        break
                    output.writeframes(frames)
                    current_frame += len(frames) // (channels * sample_width)

        write_silence(output, int(frame_rate * 0.25), channels, sample_width)


def read_wave_params(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as wav:
        return wav.getnchannels(), wav.getsampwidth(), wav.getframerate()


def write_silence(output: wave.Wave_write, frames: int, channels: int, sample_width: int) -> None:
    if frames <= 0:
        return
    chunk_frames = 8192
    silence_chunk = b"\x00" * (chunk_frames * channels * sample_width)
    remaining = frames
    while remaining > 0:
        amount = min(chunk_frames, remaining)
        output.writeframes(silence_chunk[: amount * channels * sample_width])
        remaining -= amount


def synthesize_dry_run(lines: list[ScriptLine], output_path: Path) -> None:
    """Create a silent timeline for API testing without loading a model."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame_rate = 24_000
    channels = 1
    sample_width = 2
    end_time = max((line.time + line.duration for line in lines), default=1.0) + 0.25
    with wave.open(str(output_path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(frame_rate)
        write_silence(output, int(end_time * frame_rate), channels, sample_width)


if __name__ == "__main__":
    raise SystemExit(main())
