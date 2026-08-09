import numpy as np

from config.settings import RuntimeSettings
from pipeline.object_detector import ObjectDetector


def test_yolov8_raw_output_to_person_and_ball():
    output = np.zeros((1, 84, 2), dtype=np.float32)
    output[0, :4, 0] = [320, 320, 100, 200]
    output[0, 4, 0] = .90  # COCO person
    output[0, :4, 1] = [100, 100, 20, 20]
    output[0, 4 + 32, 1] = .80  # COCO sports ball
    detector = ObjectDetector(RuntimeSettings())
    result = detector._postprocess(output, scale=1, pad_x=0, pad_y=0,
                                   width=640, height=640)
    assert {item["class"] for item in result} == {"person", "sports ball"}
    assert result[0]["bbox"] == [270.0, 220.0, 100.0, 200.0]
