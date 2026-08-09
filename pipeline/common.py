"""Shared pipeline primitives with no heavyweight dependencies."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

ProgressCallback = Callable[[int, int, str], None]
StopCheck = Callable[[], bool]


class PipelinePaused(RuntimeError):
    """Raised only at a safe disk checkpoint when the user requests pause."""


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def eta_text(started: float, completed: int, total: int) -> str:
    if completed <= 0 or total <= completed:
        return "~0 min remaining" if total <= completed else "estimating time"
    remaining = (time.monotonic() - started) / completed * (total - completed)
    if remaining < 90:
        return f"~{int(remaining)} sec remaining"
    return f"~{int(round(remaining / 60))} min remaining"


def report(callback: ProgressCallback | None, current: int, total: int, message: str) -> None:
    print(message, flush=True)
    if callback:
        callback(current, total, message)


def stop_if_requested(stop_check: StopCheck | None) -> None:
    if stop_check and stop_check():
        raise PipelinePaused("Processing paused at a safe checkpoint.")
