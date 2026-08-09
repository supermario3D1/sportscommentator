"""Small logging setup shared by CLI, UI, and pipeline stages."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_CONFIGURED = False


def setup_logger(name: str = "sports_commentator", log_file: Path | None = None,
                 level: int = logging.INFO) -> logging.Logger:
    global _CONFIGURED
    root = logging.getLogger("sports_commentator")
    root.setLevel(level)
    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        ))
        root.addHandler(handler)
        _CONFIGURED = True
    if log_file and not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        ))
        root.addHandler(file_handler)
    return logging.getLogger(f"sports_commentator.{name}")


class CallbackLogHandler(logging.Handler):
    """Forward formatted log records to a Gradio-safe callback."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(self.format(record))
        except Exception:
            self.handleError(record)
