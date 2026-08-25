"""The Gradio controller must stream progress and finish cleanly."""
from __future__ import annotations

import pytest

from config.settings import RuntimeSettings
from pipeline.pipeline_manager import PipelineManager

app_ui = pytest.importorskip("app.ui", reason="app.ui requires gradio")


class FakeManager:
    """Stands in for PipelineManager so no model or FFmpeg is touched."""

    STAGES = PipelineManager.STAGES
    STAGE_LABELS = PipelineManager.STAGE_LABELS

    def __init__(self, settings: RuntimeSettings):
        self.settings = settings

    def run(self, video=None, voice_sample=None, resume=False, progress=None):
        if progress:
            progress({"overall_progress": 50.0, "stage_label": "Frame Extraction",
                      "stage_progress": 50, "message": "halfway through frames",
                      "state": {"stages": {}, "processing_seconds": 12.5}})
        return {"status": "complete", "message": "Processing complete.", "output_path": None,
                "events": [["1:02", "goal", 0.91, "Likely goal", "What a finish!"]],
                "state": {"stages": {}, "processing_seconds": 30.0}}

    def get_event_rows(self):
        return [["1:02", "goal", 0.91, "Likely goal", "What a finish!"]]

    def load_checkpoint(self):
        return None

    def resumable_summary(self):
        return None

    def request_pause(self):
        return "Pause requested."

    def cleanup_temp(self):
        return "Temporary files deleted."

    def save_commentary_edits(self, rows):
        return 1


@pytest.fixture()
def controller(monkeypatch):
    monkeypatch.setattr(app_ui, "PipelineManager", FakeManager)
    return app_ui.UIController(RuntimeSettings())


def test_stream_yields_progress_then_a_final_summary(controller):
    updates = list(controller.stream(
        "match.mp4", None, "Football", "Excited", "Female American", 5, 30,
        True, "1 frame / second", "Auto (hardware based)", False,
        "Speed — use built-in Piper", resume=True,
    ))
    assert len(updates) >= 3  # initial, one progress update, final
    assert all(len(update) == 6 for update in updates)
    progress_html, stages, console, events, output, summary = updates[-1]
    assert "100.0" in progress_html
    assert "Voice Synthesis" in stages
    assert "Processing complete" in console
    assert events[0][1] == "goal"
    assert output is None
    assert "complete" in summary.lower()


def test_stream_reports_a_pipeline_failure_without_raising(controller, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("FFmpeg was not found.")

    monkeypatch.setattr(FakeManager, "run", explode)
    updates = list(controller.stream(
        "match.mp4", None, "Football", "Excited", "Female American", 5, 30,
        True, "1 frame / second", "Auto (hardware based)", False,
        "Speed — use built-in Piper", resume=True,
    ))
    assert "ffmpeg was not found" in updates[-1][5].lower()


def test_stage_markdown_covers_every_stage(controller):
    markdown = controller._stage_markdown({"stages": {"frame_extraction": {"status": "complete"}},
                                           "current_stage": "object_detection"})
    for stage in PipelineManager.STAGES:
        assert PipelineManager.STAGE_LABELS[stage] in markdown


def test_progress_html_is_clamped(controller):
    assert "0.0%" in controller._progress_html(-50, "Start")
    assert "100.0%" in controller._progress_html(500, "End")


def test_pause_and_cleanup_return_text(controller):
    assert "Pause requested." == controller.pause()
    assert controller.cleanup() == "Temporary files deleted."


def test_create_ui_builds_a_blocks_app(monkeypatch):
    gradio = pytest.importorskip("gradio")
    monkeypatch.setattr(app_ui, "PipelineManager", FakeManager)
    demo = app_ui.create_ui(RuntimeSettings())
    assert isinstance(demo, gradio.Blocks)
    assert demo.title == "AI Sports Commentator"
