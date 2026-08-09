from pipeline.player_tracker import ByteTrackLite


def person(x, confidence=.9):
    return {"class": "person", "bbox": [x, 100, 40, 100], "confidence": confidence}


def test_track_id_survives_small_motion():
    tracker = ByteTrackLite(high_threshold=.5)
    first = tracker.update([person(100)], [1000, 700])
    second = tracker.update([person(120)], [1000, 700])
    assert first[0]["track_id"] == second[0]["track_id"]


def test_distant_player_gets_new_track():
    tracker = ByteTrackLite(high_threshold=.5)
    first = tracker.update([person(10)], [1000, 700])
    second = tracker.update([person(800)], [1000, 700])
    assert first[0]["track_id"] != second[0]["track_id"]
