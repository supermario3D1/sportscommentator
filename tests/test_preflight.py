"""The startup component check must name exactly what is missing."""
from __future__ import annotations

from pathlib import Path

from config.settings import RuntimeSettings
from utils import preflight


def _fake_which(monkeypatch, available: set[str]) -> None:
    monkeypatch.setattr(preflight.shutil, "which",
                        lambda name, *a, **k: "/usr/bin/" + name if name in available else None)


def _install_models(monkeypatch, tmp_path: Path, voice: str) -> None:
    models = tmp_path / "models"
    (models / "piper").mkdir(parents=True)
    (models / "yolov8n.onnx").write_bytes(b"model")
    (models / "piper" / f"{voice}.onnx").write_bytes(b"voice")
    (models / "piper" / f"{voice}.onnx.json").write_text("{}")
    monkeypatch.setattr(preflight, "MODEL_DIR", models)


def test_reports_every_missing_component(monkeypatch, tmp_path):
    _fake_which(monkeypatch, set())
    monkeypatch.setattr(preflight, "MODEL_DIR", tmp_path / "models")
    problems = preflight.check_components(RuntimeSettings())
    joined = " ".join(problems).lower()
    assert "ffmpeg" in joined
    assert "yolov8n" in joined
    assert "piper" in joined
    assert "ollama" in joined


def test_clean_install_reports_nothing(monkeypatch, tmp_path):
    settings = RuntimeSettings()
    _fake_which(monkeypatch, {"ffmpeg", "ffprobe", "ollama"})
    _install_models(monkeypatch, tmp_path, settings.piper_voice)
    assert preflight.check_components(settings) == []


def test_voice_cloning_needs_the_openvoice_checkpoints(monkeypatch, tmp_path):
    settings = RuntimeSettings(use_voice_cloning=True)
    _fake_which(monkeypatch, {"ffmpeg", "ffprobe", "ollama"})
    _install_models(monkeypatch, tmp_path, settings.piper_voice)
    problems = preflight.check_components(settings)
    assert len(problems) == 1
    assert "openvoice" in problems[0].lower()


def test_summary_is_a_single_line_when_ready(monkeypatch, tmp_path):
    settings = RuntimeSettings()
    _fake_which(monkeypatch, {"ffmpeg", "ffprobe", "ollama"})
    _install_models(monkeypatch, tmp_path, settings.piper_voice)
    lines = preflight.preflight_summary(settings)
    assert len(lines) == 1
    assert lines[0].startswith("Preflight:")


def test_summary_lists_problems(monkeypatch, tmp_path):
    _fake_which(monkeypatch, set())
    monkeypatch.setattr(preflight, "MODEL_DIR", tmp_path / "models")
    lines = preflight.preflight_summary(RuntimeSettings())
    assert len(lines) >= 3
    assert all(line.startswith(("Preflight:", "  ")) for line in lines)
