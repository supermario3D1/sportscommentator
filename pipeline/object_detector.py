"""YOLOv8n ONNX inference with provider auto-selection and explicit NMS."""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any

from config.settings import MODEL_DIR, RuntimeSettings, TEMP_DIR
from pipeline.common import (ProgressCallback, StopCheck, atomic_write_json,
                             eta_text, read_json, report, stop_if_requested)
from utils.logger import setup_logger
from utils.memory_manager import MemoryManager

LOG = setup_logger("detector")
COCO_NAMES = {0: "person", 32: "sports ball"}
FRAME_RE = re.compile(r"frame_(\d+)\.jpg$")


def _iou_xyxy(box: list[float], boxes) -> Any:
    import numpy as np
    x1 = np.maximum(box[0], boxes[:, 0]); y1 = np.maximum(box[1], boxes[:, 1])
    x2 = np.minimum(box[2], boxes[:, 2]); y2 = np.minimum(box[3], boxes[:, 3])
    inter = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    area1 = max(0, box[2] - box[0]) * max(0, box[3] - box[1])
    area2 = np.maximum(0, boxes[:, 2] - boxes[:, 0]) * np.maximum(0, boxes[:, 3] - boxes[:, 1])
    return inter / np.maximum(area1 + area2 - inter, 1e-6)


def _nms(boxes, scores, threshold: float = 0.45) -> list[int]:
    import numpy as np
    if len(boxes) == 0:
        return []
    order = np.argsort(scores)[::-1]
    keep: list[int] = []
    while order.size:
        index = int(order[0]); keep.append(index)
        if order.size == 1:
            break
        overlaps = _iou_xyxy(boxes[index], boxes[order[1:]])
        order = order[1:][overlaps <= threshold]
    return keep


class ObjectDetector:
    def __init__(self, settings: RuntimeSettings, memory: MemoryManager | None = None,
                 model_path: str | Path | None = None):
        self.settings = settings
        self.memory = memory or MemoryManager(settings.max_ram_usage_percent)
        self.model_path = Path(model_path or MODEL_DIR / "yolov8n.onnx")
        self.frame_dir = TEMP_DIR / "frames"
        self.output_dir = TEMP_DIR / "detections"
        self.manifest = read_json(TEMP_DIR / "frames_manifest.json", {})
        self.session = None
        self.input_size = settings.yolo_input_size

    def _load(self):
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("ONNX Runtime is not installed. Run setup.sh first.") from exc
        if not self.model_path.is_file():
            raise FileNotFoundError(
                f"YOLOv8n ONNX model is missing: {self.model_path}. Run python install_models.py."
            )
        available = ort.get_available_providers()
        requested = self.settings.execution_provider
        providers = [requested] if requested in available else []
        if "CPUExecutionProvider" not in providers:
            providers.append("CPUExecutionProvider")
        options = ort.SessionOptions()
        options.intra_op_num_threads = self.settings.cpu_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        LOG.info("Loading YOLOv8n only, ONNX providers=%s, input=%dpx", providers,
                 self.settings.yolo_input_size)
        self.session = ort.InferenceSession(str(self.model_path), sess_options=options,
                                            providers=providers)
        input_shape = self.session.get_inputs()[0].shape
        # The installer exports dynamic spatial axes, enabling low-RAM 320px
        # inference. A user-supplied static ONNX graph is honored automatically.
        if len(input_shape) >= 4 and isinstance(input_shape[2], int) and input_shape[2] > 0:
            self.input_size = int(input_shape[2])
            if self.input_size != self.settings.yolo_input_size:
                LOG.warning("ONNX graph has a fixed %dpx input; requested %dpx cannot be used.",
                            self.input_size, self.settings.yolo_input_size)
        else:
            self.input_size = self.settings.yolo_input_size
        return self.session

    def _preprocess(self, image):
        import cv2
        import numpy as np
        size = self.input_size
        height, width = image.shape[:2]
        scale = min(size / width, size / height)
        new_w, new_h = max(1, int(round(width * scale))), max(1, int(round(height * scale)))
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((size, size, 3), 114, dtype=np.uint8)
        pad_x, pad_y = (size - new_w) // 2, (size - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized
        tensor = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        return np.ascontiguousarray(tensor[None]), scale, pad_x, pad_y

    def _postprocess(self, output, scale: float, pad_x: int, pad_y: int,
                     width: int, height: int) -> list[dict[str, Any]]:
        import numpy as np
        prediction = np.asarray(output)
        prediction = np.squeeze(prediction)
        if prediction.ndim != 2:
            raise RuntimeError(f"Unexpected YOLO output shape: {np.asarray(output).shape}")
        # Raw Ultralytics YOLOv8 is [84, 8400]. Exporters may transpose it.
        if ((prediction.shape[0] in {6, 84, 85} and prediction.shape[1] != prediction.shape[0])
                or (prediction.shape[0] < prediction.shape[1] and prediction.shape[0] <= 100)):
            prediction = prediction.T

        boxes: list[list[float]] = []
        scores: list[float] = []
        class_ids: list[int] = []
        for row in prediction:
            if row.shape[0] == 6:  # Some exports include NMS: x1,y1,x2,y2,score,class.
                x1, y1, x2, y2, score, class_id = row.tolist()
                class_id = int(class_id)
            else:
                if row.shape[0] < 5:
                    continue
                class_scores = row[4:]
                class_id = int(np.argmax(class_scores))
                score = float(class_scores[class_id])
                cx, cy, bw, bh = map(float, row[:4])
                x1, y1, x2, y2 = cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2
            if class_id not in COCO_NAMES or score < self.settings.yolo_confidence:
                continue
            x1 = max(0.0, min(width, (x1 - pad_x) / scale))
            y1 = max(0.0, min(height, (y1 - pad_y) / scale))
            x2 = max(0.0, min(width, (x2 - pad_x) / scale))
            y2 = max(0.0, min(height, (y2 - pad_y) / scale))
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append([x1, y1, x2, y2]); scores.append(float(score)); class_ids.append(class_id)

        if not boxes:
            return []
        keep_all: list[int] = []
        boxes_np = np.asarray(boxes, dtype=np.float32)
        scores_np = np.asarray(scores, dtype=np.float32)
        # NMS independently per class so a ball overlapping a person is retained.
        for class_id in set(class_ids):
            indexes = np.asarray([i for i, value in enumerate(class_ids) if value == class_id])
            local = _nms(boxes_np[indexes], scores_np[indexes])
            keep_all.extend(int(indexes[i]) for i in local)
        detections = []
        for index in sorted(keep_all, key=lambda i: scores[i], reverse=True):
            x1, y1, x2, y2 = boxes[index]
            detections.append({
                "class": COCO_NAMES[class_ids[index]],
                "bbox": [round(x1, 2), round(y1, 2), round(x2 - x1, 2), round(y2 - y1, 2)],
                "confidence": round(scores[index], 4),
            })
        return detections

    def detect(self, resume: bool = False, progress: ProgressCallback | None = None,
               stop_check: StopCheck | None = None) -> dict[str, Any]:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV is required for image decoding.") from exc
        frames = sorted(self.frame_dir.glob("frame_*.jpg"))
        if not frames:
            raise RuntimeError("No extracted frames found. Run frame extraction first.")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not resume:
            for old in self.output_dir.glob("det_*.json"):
                old.unlink(missing_ok=True)
        pending = []
        for frame_path in frames:
            match = FRAME_RE.match(frame_path.name)
            if not match:
                continue
            frame_id = int(match.group(1))
            target = self.output_dir / f"det_{frame_id:05d}.json"
            if not (resume and target.is_file() and target.stat().st_size > 20):
                pending.append((frame_path, frame_id, target))
        total = len(frames)
        if not pending:
            return {"total_frames": total, "detection_dir": str(self.output_dir)}

        session = self._load()
        input_name = session.get_inputs()[0].name
        interval = float(self.manifest.get("sample_interval_sec", 1.0))
        started = time.monotonic()
        initial_complete = total - len(pending)
        try:
            for run_index, (frame_path, frame_id, target) in enumerate(pending, 1):
                stop_if_requested(stop_check)
                image = cv2.imread(str(frame_path))
                if image is None:
                    LOG.warning("Unreadable JPEG %s; saving an empty detection record.", frame_path)
                    height = int(self.manifest.get("height", 0)); width = int(self.manifest.get("width", 0))
                    detections = []
                else:
                    height, width = image.shape[:2]
                    tensor, scale, pad_x, pad_y = self._preprocess(image)
                    outputs = session.run(None, {input_name: tensor})
                    detections = self._postprocess(outputs[0], scale, pad_x, pad_y, width, height)
                    del tensor, outputs, image
                atomic_write_json(target, {
                    "frame_id": frame_id,
                    "timestamp_sec": round((frame_id - 1) * interval, 3),
                    "image_size": [width, height],
                    "detections": detections,
                })
                current = initial_complete + run_index
                if current == 1 or current % 10 == 0 or current == total:
                    pct = int(current * 100 / total)
                    message = (f"Detecting objects: {current}/{total} ({pct}%) - "
                               f"{eta_text(started, run_index, len(pending))}")
                    report(progress, current, total, message)
                if run_index % self.settings.frame_batch_size == 0:
                    self.memory.check_resources()
            return {"total_frames": total, "detection_dir": str(self.output_dir),
                    "backend": self.settings.compute_backend}
        finally:
            # ONNX Runtime is the only model in this stage. Explicitly drop it
            # before tracking or Ollama is started.
            self.session = None
            del session
            self.memory.release("YOLOv8n unloaded")
            LOG.info("YOLOv8n session unloaded from RAM.")
