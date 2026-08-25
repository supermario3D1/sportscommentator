"""Keep console output alive on Windows code pages.

Python already uses UTF-8 when stdout is attached to a Windows console, but a
*redirected* stream (``run.bat > install.log``, a scheduled task, CI) falls back
to the ANSI code page such as cp1252.  Characters like ✓, →, and … do not exist
there, so a plain ``print`` aborts the installer with ``UnicodeEncodeError``
half-way through a multi-gigabyte download.  Reconfiguring both streams to
UTF-8 with ``errors="replace"`` makes that impossible.
"""
from __future__ import annotations

import sys

_CONFIGURED = False


def enable_utf8_console() -> None:
    """Switch stdout/stderr to UTF-8 where the stream allows it. No-op otherwise."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # A captured or closed stream (pytest, pythonw, a detached service)
            # is not worth failing startup over.
            continue
