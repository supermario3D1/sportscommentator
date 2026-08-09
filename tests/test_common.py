import json
from pathlib import Path

from pipeline.common import atomic_write_json, eta_text, read_json


def test_atomic_json_round_trip(tmp_path: Path):
    path = tmp_path / "checkpoint.json"
    atomic_write_json(path, {"stage": "frame_extraction", "progress": 100})
    assert read_json(path)["progress"] == 100
    assert not path.with_suffix(".json.tmp").exists()


def test_eta_text_handles_zero():
    import time
    assert "estimating" in eta_text(time.monotonic(), 0, 100)
