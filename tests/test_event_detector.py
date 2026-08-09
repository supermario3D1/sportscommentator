import json
from pathlib import Path

from config.settings import RuntimeSettings
from pipeline.event_detector import EventDetector


def record(frame_id, timestamp, ball=True):
    players = [
        {"class": "person", "bbox": [850 + i * 18, 520 + (i % 2) * 8, 35, 95], "confidence": .9}
        for i in range(5)
    ]
    detections = players
    if ball:
        detections.append({"class": "sports ball", "bbox": [930, 580, 14, 14], "confidence": .9})
    return {"frame_id": frame_id, "timestamp_sec": timestamp,
            "image_size": [1000, 700], "detections": detections}


def test_two_of_three_goal_rules(tmp_path: Path):
    tracks = tmp_path / "tracks"; tracks.mkdir()
    (tracks / "track_00001.json").write_text(json.dumps(record(1, 10, True)))
    (tracks / "track_00002.json").write_text(json.dumps(record(2, 11, False)))
    detector = EventDetector(RuntimeSettings(commentary_frequency=10))
    detector.input_dir = tracks
    detector.output_path = tmp_path / "events.json"
    result = detector.detect()
    goals = [event for event in result["events"] if event["type"] == "goal"]
    assert len(goals) == 1
    assert goals[0]["confidence"] >= .8
    assert "ball entered goal area" in goals[0]["signals"]
    assert "ball disappeared inside goal area" in goals[0]["signals"]
