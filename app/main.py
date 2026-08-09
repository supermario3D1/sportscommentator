"""CLI and Gradio launcher for the local AI sports commentator."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow ``python app/main.py`` in addition to ``python -m app.main``.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.hardware_detect import detect_hardware
from config.settings import VOICE_MODELS, build_runtime_settings, ensure_directories
from pipeline.pipeline_manager import PipelineManager
from utils.disk_manager import persist_upload
from utils.logger import setup_logger

LOG = setup_logger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CPU-first offline AI sports commentator")
    parser.add_argument("--process", metavar="VIDEO", help="Run in terminal instead of launching Gradio")
    parser.add_argument("--voice-sample", help="Optional OpenVoice reference audio")
    parser.add_argument("--resume", action="store_true", help="Resume the current checkpoint")
    parser.add_argument("--no-resume", action="store_true", help="Discard prior progress for --process")
    parser.add_argument("--sport", choices=["Football", "Basketball", "Tennis", "Generic"], default="Football")
    parser.add_argument("--style", choices=["Excited", "Professional", "Casual"], default="Excited")
    parser.add_argument("--voice", choices=list(VOICE_MODELS), default="Female American")
    parser.add_argument("--frequency", type=int, choices=range(1, 11), default=5)
    parser.add_argument("--half-fps", action="store_true", help="Sample one frame every two seconds")
    parser.add_argument("--tinyllama", action="store_true", help="Force the 1.1B fallback model")
    parser.add_argument("--key-events-only", action="store_true")
    parser.add_argument("--clone-voice", action="store_true", help="Use optional OpenVoice V2")
    parser.add_argument("--no-review", action="store_true", help="Do not pause before voice synthesis")
    parser.add_argument("--host", default="0.0.0.0", help="UI bind address")
    parser.add_argument("--port", type=int, default=7860, help="UI port")
    parser.add_argument("--share", action="store_true", help="Ask Gradio for a temporary public URL")
    return parser.parse_args()


def cli_progress(payload: dict) -> None:
    print(f"[{payload['overall_progress']:5.1f}%] {payload['message']}", flush=True)


def run_cli(args: argparse.Namespace, settings) -> int:
    manager = PipelineManager(settings)
    resume = args.resume
    previous = manager.resumable_summary()
    if previous and not args.no_resume and not args.resume:
        print(previous)
        try:
            answer = input("Resume previous progress? [Y/n]: ").strip().lower()
        except EOFError:
            answer = "y"
        resume = answer in {"", "y", "yes"}
    try:
        if resume:
            state = manager.load_checkpoint() or {}
            if state.get("status") == "awaiting_review":
                print("Commentary review is pending in temp/commentary/commentary.json.")
                try:
                    approved = input("Have you finished editing and want to synthesize it? [y/N]: ").strip().lower()
                except EOFError:
                    approved = "n"
                if approved not in {"y", "yes"}:
                    print("Review retained. Run with --resume when ready.")
                    return 0
                manager.save_commentary_edits(manager.get_event_rows())
            result = manager.run(resume=True, progress=cli_progress)
        else:
            if not args.process:
                raise ValueError("--process VIDEO is required for terminal processing.")
            # CLI source paths already persist independently of Gradio, so do not
            # duplicate a multi-gigabyte video under uploads.
            result = manager.run(Path(args.process), args.voice_sample, False, cli_progress)
        print(json.dumps({k: v for k, v in result.items() if k != "state"}, indent=2))
        if result["status"] == "awaiting_review":
            print("Edit temp/commentary/commentary.json, then run ./run.sh --resume")
        return 0 if result["status"] in {"complete", "paused", "awaiting_review"} else 1
    except KeyboardInterrupt:
        manager.request_pause()
        print("\nPause requested. Run with --resume after the active item exits.")
        return 130
    except Exception as exc:
        LOG.error("Processing failed: %s", exc)
        print("Fix the reported issue, then use --resume. See checkpoints/pipeline.log.", file=sys.stderr)
        return 1


def main() -> int:
    args = parse_args(); ensure_directories()
    hardware = detect_hardware(print_summary=True)
    user = {
        "sport_type": args.sport,
        "commentary_style": args.style,
        "piper_voice": VOICE_MODELS[args.voice],
        "commentary_frequency": args.frequency,
        "frame_extraction_fps": .5 if args.half_fps else 1.0,
        "key_events_only": args.key_events_only,
        "review_commentary": not args.no_review,
        "use_voice_cloning": bool(args.clone_voice and args.voice_sample),
    }
    if args.tinyllama:
        user["ollama_model"] = "tinyllama"
    settings = build_runtime_settings(hardware["settings_overrides"], user)
    if args.process or args.resume:
        return run_cli(args, settings)

    from app.ui import create_ui
    demo = create_ui(settings)
    print(f"Opening local interface at http://localhost:{args.port}")
    demo.queue(default_concurrency_limit=2).launch(
        server_name=args.host, server_port=args.port, share=args.share,
        show_error=True, inbrowser=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
