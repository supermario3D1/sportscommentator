"""Disk-space checks, upload persistence, and bounded temporary cleanup."""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

from config.settings import TEMP_DIR, UPLOAD_DIR
from utils.logger import setup_logger

LOG = setup_logger("disk")


def free_space_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / (1024 ** 3)


def require_free_space(path: Path, minimum_gb: float = 3.0) -> None:
    free = free_space_gb(path)
    if free < minimum_gb:
        raise RuntimeError(
            f"Only {free:.1f} GiB is free on {path}. At least {minimum_gb:.1f} GiB "
            "is required for frames and PCM audio. Free disk space and resume."
        )
    if free < 15:
        LOG.warning("Only %.1f GiB is free; a long match can consume several GiB.", free)


def file_fingerprint(path: Path) -> str:
    """Fast identity using metadata plus the first/last MiB, not the full video."""
    stat = path.stat()
    digest = hashlib.sha256()
    digest.update(str(stat.st_size).encode())
    with path.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
        if stat.st_size > 1024 * 1024:
            handle.seek(max(0, stat.st_size - 1024 * 1024))
            digest.update(handle.read(1024 * 1024))
    return digest.hexdigest()[:20]


def persist_upload(source: str | Path, category: str = "video") -> Path:
    """Persist a Gradio upload, using a hard link when possible to avoid a copy."""
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Uploaded file no longer exists: {source}")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c for c in source.name if c.isalnum() or c in "._-") or category
    target = (UPLOAD_DIR / safe_name).resolve()
    if source == target:
        return source
    if (target.exists() and target.stat().st_size == source.stat().st_size
            and file_fingerprint(target) == file_fingerprint(source)):
        return target
    if target.exists():
        target = UPLOAD_DIR / f"{source.stem}_{int(time.time())}{source.suffix.lower()}"
    try:
        os.link(source, target)
        LOG.info("Persisted upload with a zero-copy hard link: %s", target)
    except OSError:
        LOG.info("Copying upload into persistent storage: %s", target)
        shutil.copy2(source, target)
    return target


def clear_working_temp() -> None:
    """Clear generated work while retaining the directory skeleton."""
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    for child in TEMP_DIR.iterdir():
        if child.name == ".gitkeep":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
    for name in ("frames", "detections", "tracks", "commentary", "audio_clips", "audio_chunks"):
        (TEMP_DIR / name).mkdir(parents=True, exist_ok=True)


def cleanup_old_temp(max_age_hours: float = 24.0) -> int:
    """Remove generated files older than the retention period, never uploads/models."""
    if not TEMP_DIR.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for path in sorted(TEMP_DIR.rglob("*"), reverse=True):
        try:
            if path.is_file() and path.name != ".gitkeep" and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError as exc:
            LOG.warning("Could not clean %s: %s", path, exc)
    if removed:
        LOG.info("Removed %d temporary files older than %.1f hours.", removed, max_age_hours)
    return removed
