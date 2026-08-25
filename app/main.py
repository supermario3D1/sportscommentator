"""CLI and Gradio launcher for the local AI sports commentator."""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

# Allow ``python app/main.py`` in addition to ``python -m app.main``.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.hardware_detect import detect_hardware
from config.settings import VOICE_MODELS, build_runtime_settings, ensure_directories
from pipeline.pipeline_manager import PipelineManager
from utils.console import enable_utf8_console
from utils.logger import setup_logger
from utils.networking import (DEFAULT_PORT, PORT_SCAN_LIMIT, PortChoice,
                              pick_port, public_url)
from utils.preflight import preflight_summary

LOG = setup_logger("main")
BANNER = "=" * 62


def _frequency(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{value}' is not a number.") from exc
    if not 1 <= number <= 10:
        raise argparse.ArgumentTypeError("commentary frequency must be between 1 and 10.")
    return number


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CPU-first offline AI sports commentator")
    parser.add_argument("--process", metavar="VIDEO", help="Run in terminal instead of launching Gradio")
    parser.add_argument("--voice-sample", help="Optional OpenVoice reference audio")
    parser.add_argument("--resume", action="store_true", help="Resume the current checkpoint")
    parser.add_argument("--no-resume", action="store_true", help="Discard prior progress for --process")
    parser.add_argument("--sport", choices=["Football", "Basketball", "Tennis", "Generic"], default="Football")
    parser.add_argument("--style", choices=["Excited", "Professional", "Casual"], default="Excited")
    parser.add_argument("--voice", choices=list(VOICE_MODELS), default="Female American")
    parser.add_argument("--frequency", type=_frequency, default=5,
                        metavar="1-10", help="Commentary frequency (1 fewer, 10 more)")
    parser.add_argument("--half-fps", action="store_true", help="Sample one frame every two seconds")
    parser.add_argument("--tinyllama", action="store_true", help="Force the 1.1B fallback model")
    parser.add_argument("--key-events-only", action="store_true")
    parser.add_argument("--clone-voice", action="store_true", help="Use optional OpenVoice V2")
    parser.add_argument("--no-review", action="store_true", help="Do not pause before voice synthesis")
    parser.add_argument("--host", default="0.0.0.0", help="UI bind address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"UI port; 0 picks a free port automatically (default {DEFAULT_PORT})")
    parser.add_argument("--strict-port", action="store_true",
                        help="Fail instead of moving to a free port when --port is busy")
    parser.add_argument("--browser", action=argparse.BooleanOptionalAction, default=True,
                        help="Open the interface in the default browser (default: on)")
    parser.add_argument("--share", action="store_true", help="Ask Gradio for a temporary public URL")
    return parser.parse_args(argv)


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
        print("Fix the reported issue, then start again; add --resume to keep finished stages.",
              file=sys.stderr)
        print("Details: checkpoints/pipeline.log", file=sys.stderr)
        return 1


def _open_browser_later(url: str, delay: float = 1.25) -> None:
    """Open ``url`` once the server is listening; never break startup over it."""
    def open_now() -> None:
        try:
            import webbrowser
            if not webbrowser.open(url):
                LOG.info("No default browser was found; open %s manually.", url)
        except Exception as exc:  # headless box, no DISPLAY, no default browser
            LOG.info("Could not open a browser automatically (%s); open %s manually.", exc, url)

    timer = threading.Timer(delay, open_now)
    timer.daemon = True
    timer.start()


def report_port_choice(choice: PortChoice) -> int | None:
    """Print the outcome of port resolution; return an exit code when it failed."""
    for message in choice.messages:
        LOG.info(message)
    if choice.running_url:
        print(f"\n{BANNER}\nAI SPORTS COMMENTATOR IS ALREADY RUNNING\n{BANNER}")
        print(f"URL:     {choice.running_url}")
        print("Another copy of this app holds the port, so nothing new was started.")
        print("Close that window first if you wanted a fresh instance.")
        print(BANNER, flush=True)
        return 0
    if choice.port is None:
        print(f"\nERROR: {choice.error}", file=sys.stderr)
        print("Nothing was started. Fix the message above and try again.", file=sys.stderr)
        return 1
    return None


def launch_ui(settings, args: argparse.Namespace) -> int:
    """Build the interface, resolve a usable port, and serve until interrupted."""
    try:
        import gradio  # noqa: F401
    except ImportError:
        print("\nERROR: Gradio is not installed, so the web interface cannot start.", file=sys.stderr)
        print("Run setup.bat (Windows) or bash setup.sh (Linux), then start again.", file=sys.stderr)
        return 1
    try:
        from app.ui import create_ui
    except ImportError as exc:
        missing = getattr(exc, "name", None) or "a dependency"
        print(f"\nERROR: the web interface cannot start because '{missing}' is not installed.",
              file=sys.stderr)
        print("Run setup.bat (Windows) or bash setup.sh (Linux), then start again.", file=sys.stderr)
        return 1

    host = (args.host or "0.0.0.0").strip()
    choice = pick_port(host, args.port, strict=args.strict_port, limit=PORT_SCAN_LIMIT)
    exit_code = report_port_choice(choice)
    if exit_code is not None:
        if exit_code == 0 and args.browser and choice.running_url:
            _open_browser_later(choice.running_url, delay=0.25)
        return exit_code

    port = choice.port
    url = public_url(host, port)
    demo = create_ui(settings)
    print(f"\n{BANNER}\nAI SPORTS COMMENTATOR - LOCAL INTERFACE\n{BANNER}")
    print(f"URL:     {url}")
    print(f"Binding: {host}:{port}" + ("  (reachable from this computer only)"
                                       if host in {"127.0.0.1", "localhost"} else ""))
    if args.share:
        print("Share:   a temporary public URL is requested from Gradio.")
    print("Stop:    press Ctrl+C in this window.")
    print(BANNER, flush=True)
    if args.browser:
        _open_browser_later(url)

    try:
        demo.queue(default_concurrency_limit=2).launch(
            server_name=host, server_port=port, share=args.share,
            show_error=True, inbrowser=False,
        )
    except OSError as exc:
        # Gradio re-raised its own bind failure between the probe and launch.
        print(f"\nERROR: the interface could not bind {host}:{port} ({exc}).", file=sys.stderr)
        print("Start again with --port 0 to pick any free port.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterface stopped.")
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    enable_utf8_console()
    args = parse_args(argv)
    ensure_directories()
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
    if args.clone_voice and not args.voice_sample:
        LOG.warning("--clone-voice needs --voice-sample AUDIO; the built-in Piper voice is used.")
    if args.tinyllama:
        user["ollama_model"] = "tinyllama"
    settings = build_runtime_settings(hardware["settings_overrides"], user)
    for line in preflight_summary(settings):
        print(line, flush=True)
    if args.process or args.resume:
        return run_cli(args, settings)
    return launch_ui(settings, args)


if __name__ == "__main__":
    raise SystemExit(main())
