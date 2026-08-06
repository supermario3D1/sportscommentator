"""CPU-first Piper synthesis with optional sequential OpenVoice V2 conversion."""
from __future__ import annotations

import shutil
import subprocess
import time
import wave
from pathlib import Path
from typing import Any

from config.settings import MODEL_DIR, RuntimeSettings, TEMP_DIR, VOICE_MODELS
from pipeline.common import (PipelinePaused, ProgressCallback, StopCheck,
                             atomic_write_json, eta_text, read_json, report,
                             stop_if_requested)
from utils.logger import setup_logger
from utils.memory_manager import MemoryManager

LOG = setup_logger("voice")


class VoiceSynthesizer:
    def __init__(self, settings: RuntimeSettings, memory: MemoryManager | None = None):
        self.settings = settings
        self.memory = memory or MemoryManager(settings.max_ram_usage_percent)
        self.commentary_path = TEMP_DIR / "commentary" / "commentary.json"
        self.output_dir = TEMP_DIR / "audio_clips"
        self.manifest_path = self.output_dir / "manifest.json"

    def _model_paths(self) -> tuple[Path, Path]:
        model_name = self.settings.piper_voice
        model = MODEL_DIR / "piper" / f"{model_name}.onnx"
        config = Path(str(model) + ".json")
        if not model.is_file() or not config.is_file():
            raise FileNotFoundError(
                f"Piper voice '{model_name}' is missing under {MODEL_DIR / 'piper'}. "
                "Run python install_models.py --voices."
            )
        return model, config

    def _synthesize_cli(self, text: str, output: Path, model: Path, config: Path) -> None:
        executable = shutil.which("piper") or shutil.which("piper.exe")
        if not executable:
            raise FileNotFoundError("The Piper CLI executable was not found in this environment.")
        command = [executable, "--model", str(model), "--config", str(config),
                   "--output_file", str(output)]
        process = subprocess.run(command, input=(text + "\n").encode("utf-8"),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if process.returncode != 0 or not output.is_file():
            raise RuntimeError(f"Piper failed: {process.stderr.decode(errors='replace').strip()}")

    @staticmethod
    def _duration(path: Path) -> float:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() / max(1, handle.getframerate())

    def _openvoice_convert(self, source_paths: list[Path], reference: Path,
                           progress: ProgressCallback | None, stop_check: StopCheck | None) -> bool:
        """Convert all Piper clips after Piper has been unloaded.

        OpenVoice is optional because its torch/checkpoint footprint is much
        larger. Any setup/runtime failure leaves valid Piper audio in place.
        """
        checkpoint_root = MODEL_DIR / "openvoice" / "checkpoints_v2" / "converter"
        config = checkpoint_root / "config.json"
        checkpoint = checkpoint_root / "checkpoint.pth"
        if not config.is_file() or not checkpoint.is_file():
            LOG.warning("Voice sample supplied, but OpenVoice V2 checkpoints are absent; keeping Piper voice.")
            return False
        try:
            import torch
            from openvoice import se_extractor
            from openvoice.api import ToneColorConverter
        except ImportError as exc:
            LOG.warning("OpenVoice dependencies are not installed (%s); keeping Piper voice.", exc)
            return False
        converter = None
        try:
            LOG.info("Loading OpenVoice V2 on CPU after Piper has been released.")
            converter = ToneColorConverter(str(config), device="cpu")
            converter.load_ckpt(str(checkpoint))
            target_se, _ = se_extractor.get_se(str(reference), converter, vad=True)
            total = len(source_paths); started = time.monotonic()
            converted_paths: list[Path] = []
            for index, source in enumerate(source_paths, 1):
                stop_if_requested(stop_check)
                source_se, _ = se_extractor.get_se(str(source), converter, vad=False)
                converted = source.with_suffix(".converted.wav")
                converter.convert(audio_src_path=str(source), src_se=source_se,
                                  tgt_se=target_se, output_path=str(converted),
                                  message="AI Sports Commentator")
                if not converted.is_file():
                    raise RuntimeError(f"OpenVoice did not create {converted}")
                converted_paths.append(converted)
                report(progress, index, total,
                       f"Cloning voice: {index}/{total} ({int(index * 100 / max(total, 1))}%) - "
                       f"{eta_text(started, index, total)}")
            # Commit conversion only after every clip succeeded, ensuring a
            # failure leaves a consistent all-Piper fallback rather than a mix.
            for converted, source in zip(converted_paths, source_paths):
                converted.replace(source)
            return True
        except PipelinePaused:
            for path in source_paths:
                path.with_suffix(".converted.wav").unlink(missing_ok=True)
            raise
        except Exception as exc:
            for path in source_paths:
                path.with_suffix(".converted.wav").unlink(missing_ok=True)
            LOG.exception("OpenVoice conversion failed; valid Piper clips will be used: %s", exc)
            return False
        finally:
            del converter
            self.memory.release("OpenVoice V2 unloaded")

    def synthesize(self, voice_sample: str | Path | None = None, resume: bool = False,
                   progress: ProgressCallback | None = None,
                   stop_check: StopCheck | None = None) -> dict[str, Any]:
        lines = read_json(self.commentary_path, {}).get("commentary", [])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not resume:
            for old in self.output_dir.glob("clip_*.wav"):
                old.unlink(missing_ok=True)
        total = len(lines)
        if total == 0:
            manifest = {"clips": [], "voice": self.settings.piper_voice,
                        "voice_cloned": False, "voice_sample": None}
            atomic_write_json(self.manifest_path, manifest)
            LOG.warning("There are no commentary lines; voice synthesis is a no-op.")
            return manifest
        model, config = self._model_paths()
        started = time.monotonic(); clips: list[dict[str, Any]] = []
        source_paths: list[Path] = []
        piper_voice = None
        use_cli = bool(shutil.which("piper") or shutil.which("piper.exe"))
        try:
            if not use_cli:
                try:
                    from piper.voice import PiperVoice
                    LOG.info("Piper CLI absent; loading the lightweight Piper Python API.")
                    piper_voice = PiperVoice.load(str(model), config_path=str(config), use_cuda=False)
                except (ImportError, TypeError) as exc:
                    raise RuntimeError(
                        "Piper TTS is not available. Activate .venv and run pip install piper-tts."
                    ) from exc
            for index, line in enumerate(lines, 1):
                stop_if_requested(stop_check)
                output = self.output_dir / f"clip_{index:03d}.wav"
                if not (resume and output.is_file() and output.stat().st_size > 44):
                    if use_cli:
                        self._synthesize_cli(str(line.get("text", "")), output, model, config)
                    else:
                        with wave.open(str(output), "wb") as wav_file:
                            piper_voice.synthesize_wav(str(line.get("text", "")), wav_file)
                source_paths.append(output)
                clips.append({
                    "index": index,
                    "timestamp": float(line.get("timestamp", 0)),
                    "event": line.get("event", "event"),
                    "text": line.get("text", ""),
                    "audio_file": str(output),
                    "duration_sec": round(self._duration(output), 3),
                })
                atomic_write_json(self.manifest_path, {"clips": clips, "voice": self.settings.piper_voice})
                report(progress, index, max(total, 1),
                       f"Synthesizing voice: {index}/{total} ({int(index * 100 / max(total, 1))}%) - "
                       f"{eta_text(started, index, total)}")
                self.memory.check_resources()
        finally:
            piper_voice = None
            self.memory.release("Piper voice unloaded")

        cloned = False
        if voice_sample and self.settings.use_voice_cloning and not self.settings.low_ram_mode and source_paths:
            reference = Path(voice_sample)
            if reference.is_file():
                cloned = self._openvoice_convert(source_paths, reference, progress, stop_check)
                # Converted durations can differ slightly.
                for clip, path in zip(clips, source_paths):
                    clip["duration_sec"] = round(self._duration(path), 3)
        manifest = {
            "clips": clips,
            "voice": self.settings.piper_voice,
            "voice_cloned": cloned,
            "voice_sample": str(voice_sample) if voice_sample else None,
        }
        atomic_write_json(self.manifest_path, manifest)
        LOG.info("Voice synthesis complete: %d clips, cloned=%s", total, cloned)
        return manifest
