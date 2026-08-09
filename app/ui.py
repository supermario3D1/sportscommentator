"""Single-page Gradio interface for the offline batch pipeline."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any, Generator

from config.settings import RuntimeSettings, VOICE_MODELS
from pipeline.pipeline_manager import PipelineManager
from utils.disk_manager import persist_upload
from utils.logger import setup_logger

LOG = setup_logger("ui")


class UIController:
    def __init__(self, base_settings: RuntimeSettings):
        self.base_settings = base_settings
        self.manager = PipelineManager(base_settings)
        self._lock = threading.Lock()

    @staticmethod
    def _stage_markdown(state: dict[str, Any] | None) -> str:
        state = state or {}
        current = state.get("current_stage")
        rows = ["### Pipeline stages"]
        for stage in PipelineManager.STAGES:
            record = state.get("stages", {}).get(stage, {})
            status = record.get("status")
            if status == "complete":
                icon = "☑"
                suffix = "complete"
            elif status == "failed":
                icon = "✖"
                suffix = record.get("message", "failed")
            elif stage == current:
                icon = "▶"
                suffix = f"{record.get('progress', 0)}%"
                if state.get("status") == "awaiting_review" and stage == "voice_synthesis":
                    suffix = "waiting for commentary review"
            else:
                icon = "☐"; suffix = "waiting"
            rows.append(f"{icon} **{PipelineManager.STAGE_LABELS[stage]}** — {suffix}")
        return "\n\n".join(rows)

    @staticmethod
    def _progress_html(percent: float, label: str) -> str:
        percent = max(0, min(100, float(percent)))
        return (f'<div class="progress-wrap"><div class="progress-label">{label}</div>'
                f'<progress value="{percent}" max="100"></progress>'
                f'<span>{percent:.1f}% overall</span></div>')

    @staticmethod
    def _summary(result: dict[str, Any]) -> str:
        state = result.get("state", {})
        elapsed = float(state.get("processing_seconds", 0))
        output = result.get("output_path")
        lines = [f"**Status:** {result.get('message', result.get('status', ''))}",
                 f"**Accumulated processing time:** {elapsed / 60:.1f} minutes"]
        if output and Path(output).is_file():
            lines.append(f"**Final file size:** {Path(output).stat().st_size / 1024 ** 2:.1f} MiB")
        return "  \n".join(lines)

    def _settings(self, sport: str, style: str, voice: str, frequency: int,
                  duck_percent: int, review: bool, sampling: str, llm: str,
                  key_events_only: bool, clone_priority: str, has_sample: bool) -> RuntimeSettings:
        overrides: dict[str, Any] = {
            "sport_type": sport,
            "commentary_style": style,
            "piper_voice": VOICE_MODELS[voice],
            "commentary_frequency": int(frequency),
            "original_audio_duck_level": float(duck_percent) / 100.0,
            "review_commentary": bool(review),
            "frame_extraction_fps": 0.5 if sampling.startswith("1 frame / 2") else 1.0,
            "key_events_only": bool(key_events_only),
            "use_voice_cloning": bool(has_sample and clone_priority.startswith("Quality")),
        }
        if llm.startswith("TinyLlama"):
            overrides["ollama_model"] = "tinyllama"
        elif llm.startswith("Phi-3"):
            overrides["ollama_model"] = "phi3:mini"
        settings = self.base_settings.with_overrides(**overrides)
        if settings.low_ram_mode:
            settings = settings.with_overrides(use_voice_cloning=False, yolo_input_size=320,
                                               frame_batch_size=50, ollama_model="tinyllama")
        return settings

    def stream(self, video: str | None, voice_sample: str | None, sport: str,
               style: str, voice: str, frequency: int, duck_percent: int,
               review: bool, sampling: str, llm: str, key_events_only: bool,
               clone_priority: str, resume: bool = False) -> Generator[tuple, None, None]:
        messages: queue.Queue = queue.Queue()
        logs: list[str] = []
        result_holder: dict[str, Any] = {}

        def progress(payload: dict[str, Any]) -> None:
            messages.put(("progress", payload))

        def work() -> None:
            try:
                if resume:
                    manager = PipelineManager(self.base_settings)
                    self.manager = manager
                    result = manager.run(resume=True, progress=progress)
                else:
                    if not video:
                        raise ValueError("Upload a match video before starting.")
                    saved_video = persist_upload(video, "video")
                    saved_voice = persist_upload(voice_sample, "voice") if voice_sample else None
                    settings = self._settings(sport, style, voice, frequency, duck_percent,
                                              review, sampling, llm, key_events_only,
                                              clone_priority, bool(saved_voice))
                    manager = PipelineManager(settings)
                    self.manager = manager
                    result = manager.run(saved_video, saved_voice, resume=False, progress=progress)
                result_holder.update(result)
            except Exception as exc:
                LOG.exception("UI pipeline error: %s", exc)
                result_holder.update({"status": "failed", "message": str(exc),
                                      "state": self.manager.load_checkpoint() or {}})
            finally:
                messages.put(("done", None))

        worker = threading.Thread(target=work, name="offline-pipeline", daemon=True)
        worker.start()
        done = False; latest_state: dict[str, Any] = {}
        yield (self._progress_html(0, "Preparing job..."), self._stage_markdown({}),
               "Preparing disk-backed pipeline...", [], None, "**Status:** Starting")
        while not done:
            try:
                kind, payload = messages.get(timeout=.5)
            except queue.Empty:
                continue
            if kind == "done":
                done = True
                continue
            latest_state = payload.get("state", latest_state)
            message = str(payload.get("message", ""))
            logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")
            logs = logs[-300:]
            rows = self.manager.get_event_rows()
            yield (
                self._progress_html(payload.get("overall_progress", 0),
                                    f"{payload.get('stage_label')}: {payload.get('stage_progress', 0)}%"),
                self._stage_markdown(latest_state), "\n".join(logs), rows,
                None, f"**Status:** {message}",
            )
        worker.join()
        result = result_holder
        state = result.get("state", self.manager.load_checkpoint() or latest_state)
        output = result.get("output_path") if result.get("status") == "complete" else None
        overall = 100 if result.get("status") == "complete" else sum(
            1 for value in state.get("stages", {}).values() if value.get("status") == "complete"
        ) / len(PipelineManager.STAGES) * 100
        logs.append(f"[{time.strftime('%H:%M:%S')}] {result.get('message', result.get('status'))}")
        yield (
            self._progress_html(overall, result.get("status", "finished").replace("_", " ").title()),
            self._stage_markdown(state), "\n".join(logs[-300:]),
            result.get("events") or self.manager.get_event_rows(), output,
            self._summary(result),
        )

    def pause(self) -> str:
        return self.manager.request_pause()

    def save_edits(self, rows: Any) -> tuple[str, str]:
        manager = PipelineManager(self.base_settings)
        count = manager.save_commentary_edits(rows)
        self.manager = manager
        return (f"Saved {count} commentary lines. Press RESUME to synthesize voice and export.",
                self._stage_markdown(manager.load_checkpoint()))

    def regenerate(self, row_number: int) -> tuple[list[list[Any]], str]:
        state = self.manager.load_checkpoint() or {}
        settings = RuntimeSettings.from_dict(state.get("settings", self.base_settings.to_dict()))
        from pipeline.commentary_generator import CommentaryGenerator
        generator = CommentaryGenerator(settings)
        item = generator.regenerate(int(row_number) - 1)
        manager = PipelineManager(settings)
        rows = manager.get_event_rows()
        manager.save_commentary_edits(rows)
        self.manager = manager
        return rows, f"Regenerated row {int(row_number)}: {item['text']}"

    def cleanup(self) -> str:
        return self.manager.cleanup_temp()


def create_ui(settings: RuntimeSettings):
    try:
        import gradio as gr
    except ImportError as exc:
        raise RuntimeError("Gradio is not installed. Run setup.sh first.") from exc

    controller = UIController(settings)
    prior = controller.manager.resumable_summary()
    css = """
    .gradio-container {max-width: 1180px !important; margin: auto;}
    .hero {text-align:center; padding: 8px 0 2px;}
    .progress-wrap {display:flex; gap:12px; align-items:center; padding:14px; border:1px solid #374151; border-radius:10px;}
    .progress-wrap progress {width:100%; height:20px; accent-color:#16a34a;}
    .progress-label {min-width:240px; font-weight:700;}
    #start-button {font-size:1.15rem; font-weight:800;}
    """
    with gr.Blocks(title="AI Sports Commentator", css=css) as demo:
        gr.Markdown("# ⚽ AI Sports Commentator\n### CPU-first, private, offline batch processing", elem_classes="hero")
        if prior:
            gr.Markdown(f"> **Resume available:** {prior}")

        with gr.Group():
            gr.Markdown("## 1 · Upload")
            with gr.Row():
                video = gr.File(label="Upload Match Video", file_types=[".mp4", ".mkv", ".avi", ".mov"], type="filepath")
                sample = gr.File(label="Upload Voice Sample (optional)", file_types=[".wav", ".mp3", ".flac"], type="filepath")

        with gr.Group():
            gr.Markdown("## 2 · Settings")
            with gr.Row():
                sport = gr.Dropdown(["Football", "Basketball", "Tennis", "Generic"], value="Football", label="Sport Type")
                style = gr.Dropdown(["Excited", "Professional", "Casual"], value="Excited", label="Commentary Style")
                voice = gr.Dropdown(list(VOICE_MODELS), value="Female American", label="Built-in Voice")
            with gr.Row():
                frequency = gr.Slider(1, 10, 5, step=1, label="Commentary Frequency (1 fewer · 10 more)")
                duck = gr.Slider(0, 100, 30, step=5, label="Original Audio Level During Commentary (%)")
            with gr.Accordion("Low-resource and workflow options", open=False):
                with gr.Row():
                    sampling = gr.Radio(["1 frame / second", "1 frame / 2 seconds (faster)"], value="1 frame / second", label="Frame sampling")
                    llm = gr.Radio(["Auto (hardware based)", "Phi-3 Mini (quality)", "TinyLlama (low RAM)"], value="Auto (hardware based)", label="Commentary model")
                with gr.Row():
                    clone_priority = gr.Radio(["Speed — use built-in Piper", "Quality — clone uploaded sample with OpenVoice"], value="Speed — use built-in Piper", label="Voice priority")
                    key_only = gr.Checkbox(False, label="Key events only (goals, shots, corners)")
                    review = gr.Checkbox(True, label="Pause for commentary review before speech")

        with gr.Group():
            gr.Markdown("## 3 · Process")
            with gr.Row():
                start = gr.Button("START PROCESSING", variant="primary", elem_id="start-button")
                pause = gr.Button("PAUSE (safe checkpoint)", variant="stop")
                resume_button = gr.Button("RESUME", variant="secondary")
            progress_html = gr.HTML(controller._progress_html(0, "Waiting"))
            stage_status = gr.Markdown(controller._stage_markdown(controller.manager.load_checkpoint()))
            console = gr.Textbox(label="Console log", lines=12, interactive=False, autoscroll=True)

        with gr.Group():
            gr.Markdown("## 4 · Review detected events and commentary")
            gr.Markdown("Edit the **Commentary** column, save, then resume. Timestamps/events are reference data.")
            events = gr.Dataframe(headers=["Timestamp", "Event", "Confidence", "Description", "Commentary"],
                                  datatype=["str", "str", "number", "str", "str"],
                                  col_count=(5, "fixed"), type="array", interactive=True, wrap=True)
            with gr.Row():
                save = gr.Button("SAVE COMMENTARY EDITS", variant="primary")
                row_number = gr.Number(value=1, minimum=1, precision=0, label="Row to re-generate")
                regenerate = gr.Button("RE-GENERATE THIS LINE")
            review_message = gr.Markdown()

        with gr.Group():
            gr.Markdown("## 5 · Output")
            result_file = gr.File(label="Download Final MP4", interactive=False)
            summary = gr.Markdown("**Status:** Waiting for a job")
            cleanup = gr.Button("Delete temporary files after successful export")
            cleanup_message = gr.Markdown()

        shared_inputs = [video, sample, sport, style, voice, frequency, duck, review,
                         sampling, llm, key_only, clone_priority]
        outputs = [progress_html, stage_status, console, events, result_file, summary]
        # These wrappers are generator functions (rather than lambdas returning
        # generators), allowing Gradio to stream progress updates immediately.
        def start_stream(*args):
            yield from controller.stream(*args, resume=False)

        def resume_stream(*args):
            yield from controller.stream(*args, resume=True)

        start.click(start_stream, shared_inputs, outputs,
                    concurrency_limit=1, concurrency_id="pipeline")
        resume_button.click(resume_stream, shared_inputs, outputs,
                            concurrency_limit=1, concurrency_id="pipeline")
        pause.click(controller.pause, outputs=review_message, queue=False)
        save.click(controller.save_edits, inputs=events, outputs=[review_message, stage_status], queue=False)
        # Re-generation shares the pipeline concurrency group so Ollama can
        # never load while YOLO/Piper/OpenVoice is active.
        regenerate.click(controller.regenerate, inputs=row_number, outputs=[events, review_message],
                         concurrency_limit=1, concurrency_id="pipeline")
        cleanup.click(controller.cleanup, outputs=cleanup_message, queue=False)
    return demo
