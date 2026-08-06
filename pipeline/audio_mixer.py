"""Chunked PyDub mixer: PCM stays on disk and only 60 seconds enters RAM."""
from __future__ import annotations

import math
import shutil
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

from config.settings import RuntimeSettings, TEMP_DIR
from pipeline.common import (ProgressCallback, StopCheck, atomic_write_json,
                             eta_text, read_json, report, stop_if_requested)
from utils.logger import setup_logger
from utils.memory_manager import MemoryManager

LOG = setup_logger("mixer")


class AudioMixer:
    def __init__(self, settings: RuntimeSettings, memory: MemoryManager | None = None):
        self.settings = settings
        self.memory = memory or MemoryManager(settings.max_ram_usage_percent)
        self.original_wav = TEMP_DIR / "original_audio.wav"
        self.manifest_path = TEMP_DIR / "audio_clips" / "manifest.json"
        self.chunk_dir = TEMP_DIR / "audio_chunks"
        self.output_path = TEMP_DIR / "final_audio.wav"

    @staticmethod
    def _ffmpeg() -> str:
        executable = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        if not executable:
            raise RuntimeError("FFmpeg was not found. Install it and ensure ffmpeg is on PATH.")
        return executable

    @staticmethod
    def _duration(video: Path) -> float:
        ffprobe = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
        if not ffprobe:
            raise RuntimeError("ffprobe was not found beside FFmpeg.")
        process = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                                  "-of", "default=noprint_wrappers=1:nokey=1", str(video)],
                                 capture_output=True, text=True, check=False)
        try:
            return float(process.stdout.strip())
        except ValueError as exc:
            raise RuntimeError(f"Could not determine video duration: {process.stderr.strip()}") from exc

    def _extract_audio(self, video: Path) -> None:
        command = [self._ffmpeg(), "-y", "-v", "warning", "-i", str(video),
                   "-map", "0:a:0?", "-vn", "-ac", "2", "-ar", "48000",
                   "-c:a", "pcm_s16le", str(self.original_wav)]
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        if process.returncode == 0 and self.original_wav.is_file() and self.original_wav.stat().st_size > 44:
            return
        # Silent source videos are valid input. Generate matching stereo silence.
        duration = self._duration(video)
        LOG.warning("No usable source audio; generating %.1f seconds of silence.", duration)
        command = [self._ffmpeg(), "-y", "-v", "warning", "-f", "lavfi", "-i",
                   "anullsrc=r=48000:cl=stereo", "-t", f"{duration:.3f}",
                   "-c:a", "pcm_s16le", str(self.original_wav)]
        process = subprocess.run(command, capture_output=True, text=True, check=False)
        if process.returncode != 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {process.stderr.strip()}")

    @staticmethod
    def _duck(segment, start_ms: int, end_ms: int, level: float, fade_ms: int):
        """Apply gain to a subrange with short boundary fades."""
        if end_ms <= start_ms or level >= .999:
            return segment
        gain_db = 20 * math.log10(max(level, .001))
        start_ms = max(0, start_ms); end_ms = min(len(segment), end_ms)
        middle = segment[start_ms:end_ms].apply_gain(gain_db)
        fade = min(fade_ms, len(middle) // 2)
        if fade > 0:
            # Fade from unity to duck gain, then back to unity.
            head = segment[start_ms:start_ms + fade].fade(to_gain=gain_db, start=0, duration=fade)
            tail = segment[end_ms - fade:end_ms].apply_gain(gain_db).fade(
                from_gain=0, to_gain=-gain_db, start=0, duration=fade)
            middle = head + middle[fade:max(fade, len(middle) - fade)] + tail
        return segment[:start_ms] + middle + segment[end_ms:]

    def _mix_chunk(self, raw: bytes, frame_rate: int, channels: int, sample_width: int,
                   chunk_start_ms: int, clips: list[dict[str, Any]]):
        try:
            from pydub import AudioSegment
        except ImportError as exc:
            raise RuntimeError("PyDub is missing. Run setup.sh or pip install pydub.") from exc
        segment = AudioSegment(data=raw, sample_width=sample_width,
                               frame_rate=frame_rate, channels=channels)
        chunk_end_ms = chunk_start_ms + len(segment)
        # First create all duck regions, then overlay speech. Clips overlapping a
        # chunk boundary are sliced, so no full-match AudioSegment is allocated.
        loaded: list[tuple[dict[str, Any], Any, int]] = []
        for clip in clips:
            clip_start = int(float(clip["timestamp"]) * 1000)
            clip_duration = int(float(clip.get("duration_sec", 0)) * 1000)
            clip_end = clip_start + clip_duration
            if clip_end <= chunk_start_ms or clip_start >= chunk_end_ms:
                continue
            audio = AudioSegment.from_file(clip["audio_file"])
            audio = audio.set_frame_rate(frame_rate).set_channels(channels).set_sample_width(sample_width)
            loaded.append((clip, audio, clip_start))
            local_duck_start = max(0, clip_start - chunk_start_ms)
            local_duck_end = min(len(segment), clip_end + 1000 - chunk_start_ms)
            segment = self._duck(segment, local_duck_start, local_duck_end,
                                 self.settings.original_audio_duck_level,
                                 self.settings.duck_fade_ms)
        volume_db = 20 * math.log10(max(self.settings.commentary_volume, .001))
        for _, audio, clip_start in loaded:
            overlap_start = max(chunk_start_ms, clip_start)
            overlap_end = min(chunk_end_ms, clip_start + len(audio))
            source_start = overlap_start - clip_start
            piece = audio[source_start:source_start + (overlap_end - overlap_start)].apply_gain(volume_db)
            segment = segment.overlay(piece, position=overlap_start - chunk_start_ms)
        return segment

    @staticmethod
    def _concatenate_wav(chunks: list[Path], target: Path) -> None:
        if not chunks:
            raise RuntimeError("No mixed audio chunks were produced.")
        with wave.open(str(chunks[0]), "rb") as first:
            params = first.getparams()
        with wave.open(str(target), "wb") as output:
            output.setparams(params)
            for chunk in chunks:
                with wave.open(str(chunk), "rb") as source:
                    compatible = (source.getnchannels(), source.getsampwidth(), source.getframerate()) == (
                        params.nchannels, params.sampwidth, params.framerate)
                    if not compatible:
                        raise RuntimeError(f"Audio chunk format mismatch: {chunk}")
                    while True:
                        data = source.readframes(48000)
                        if not data:
                            break
                        output.writeframesraw(data)
            output.writeframes(b"")

    def mix(self, video_path: str | Path, resume: bool = False,
            progress: ProgressCallback | None = None,
            stop_check: StopCheck | None = None) -> dict[str, Any]:
        video = Path(video_path)
        clips = read_json(self.manifest_path, {}).get("clips", [])
        self.chunk_dir.mkdir(parents=True, exist_ok=True)
        if not resume:
            for old in self.chunk_dir.glob("chunk_*.wav"):
                old.unlink(missing_ok=True)
            self.output_path.unlink(missing_ok=True)
            self.original_wav.unlink(missing_ok=True)
        if not self.original_wav.is_file():
            LOG.info("Extracting original audio to disk as 48 kHz PCM...")
            self._extract_audio(video)

        chunks: list[Path] = []
        with wave.open(str(self.original_wav), "rb") as source:
            channels = source.getnchannels(); sample_width = source.getsampwidth()
            frame_rate = source.getframerate(); total_frames = source.getnframes()
            frames_per_chunk = frame_rate * self.settings.audio_chunk_seconds
            total = max(1, math.ceil(total_frames / frames_per_chunk))
            started = time.monotonic()
            for index in range(total):
                stop_if_requested(stop_check)
                output = self.chunk_dir / f"chunk_{index + 1:05d}.wav"
                chunk_frames = min(frames_per_chunk, total_frames - index * frames_per_chunk)
                if resume and output.is_file() and output.stat().st_size > 44:
                    source.setpos(min(total_frames, (index + 1) * frames_per_chunk))
                else:
                    raw = source.readframes(chunk_frames)
                    mixed = self._mix_chunk(raw, frame_rate, channels, sample_width,
                                            index * self.settings.audio_chunk_seconds * 1000, clips)
                    mixed.export(str(output), format="wav")
                    del raw, mixed
                chunks.append(output)
                report(progress, index + 1, total,
                       f"Mixing audio: {index + 1}/{total} chunks ({int((index + 1) * 100 / total)}%) - "
                       f"{eta_text(started, index + 1, total)}")
                self.memory.check_resources()
                self.memory.release("audio chunk")
        self._concatenate_wav(chunks, self.output_path)
        result = {"audio_path": str(self.output_path), "chunks": len(chunks),
                  "commentary_clips": len(clips)}
        atomic_write_json(TEMP_DIR / "audio_mix_manifest.json", result)
        LOG.info("Mixed audio written to %s", self.output_path)
        return result
