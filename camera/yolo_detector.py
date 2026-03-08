"""
camera/yolo_detector.py — YOLOv11s ONNX inference for 3D print failure detection.

Model: ApatheticWithoutTheA/YoloV11s-3D-Print-Failure-Detection (HuggingFace)
  ~6 MB ONNX, ~10 ms CPU, mAP@50-95 = 0.82
  Classes: spaghetti, stringing, zits

This module is purely additive — if the model is unavailable (missing file, import
error, load failure) it returns empty detections and yolo_available=False.  No
exception is ever raised to the caller.

Score boost formula (sourced from Obico multi-frame weighting design):
  score += confidence × 0.3  when class == "spaghetti" AND confidence > 0.5
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

# Model cache location.
_MODEL_DIR  = Path.home() / ".bambu-mcp" / "models"
_MODEL_FILE = _MODEL_DIR / "yolov11s_3dprint.onnx"

# HuggingFace direct download URL for the ONNX model.
_MODEL_URL = (
    "https://huggingface.co/ApatheticWithoutTheA/"
    "YoloV11s-3D-Print-Failure-Detection/resolve/main/best.onnx"
)

# YOLO output class labels (in model-output order).
_CLASS_LABELS = ["spaghetti", "stringing", "zits"]

# Minimum confidence to include a detection.
_CONF_THRESHOLD = 0.25
# IoU threshold for NMS.
_IOU_THRESHOLD  = 0.45
# Input image size expected by the model.
_INPUT_SIZE = 640

# Boost applied to spaghetti detections above this confidence.
# Sourced from Obico multi-frame weighting design.
BOOST_MIN_CONFIDENCE = 0.5
BOOST_WEIGHT         = 0.3

# Module-level session state.
_session       = None   # onnxruntime.InferenceSession or None
_session_lock  = threading.Lock()
_load_attempted = False


def _ensure_model() -> bool:
    """Download the model file if it doesn't exist.  Returns True on success."""
    if _MODEL_FILE.exists():
        return True
    try:
        import urllib.request
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        log.info("yolo_detector: downloading model to %s …", _MODEL_FILE)
        urllib.request.urlretrieve(_MODEL_URL, _MODEL_FILE)
        log.info("yolo_detector: model downloaded (%d bytes)", _MODEL_FILE.stat().st_size)
        return True
    except Exception as e:
        log.warning("yolo_detector: model download failed: %s", e)
        return False


def _get_session():
    """Return the cached onnxruntime session, loading once on first call."""
    global _session, _load_attempted
    with _session_lock:
        if _load_attempted:
            return _session
        _load_attempted = True
        try:
            if not _ensure_model():
                return None
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 2
            opts.intra_op_num_threads = 2
            _session = ort.InferenceSession(str(_MODEL_FILE), sess_options=opts,
                                            providers=["CPUExecutionProvider"])
            log.info("yolo_detector: model loaded from %s", _MODEL_FILE)
        except Exception as e:
            log.warning("yolo_detector: model load failed (YOLO disabled): %s", e)
            _session = None
        return _session


def _preprocess(jpeg_bytes: bytes):
    """Decode JPEG, resize to _INPUT_SIZE×_INPUT_SIZE, return NCHW float32 array."""
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
    orig_w, orig_h = img.size
    img_resized = img.resize((_INPUT_SIZE, _INPUT_SIZE), Image.BILINEAR)
    arr = np.array(img_resized, dtype=np.float32) / 255.0
    # HWC → NCHW
    arr = arr.transpose(2, 0, 1)[np.newaxis, ...]
    return arr, orig_w, orig_h


def _nms(boxes, scores, iou_threshold: float):
    """Simple NMS returning kept indices (sorted by score descending)."""
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    kept = []
    while order.size > 0:
        i = order[0]
        kept.append(i)
        ix1 = np.maximum(x1[i], x1[order[1:]])
        iy1 = np.maximum(y1[i], y1[order[1:]])
        ix2 = np.minimum(x2[i], x2[order[1:]])
        iy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, ix2 - ix1 + 1) * np.maximum(0, iy2 - iy1 + 1)
        iou   = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][iou <= iou_threshold]
    return kept


def detect(jpeg_bytes: bytes) -> tuple[list[dict], float, bool]:
    """
    Run YOLOv11s inference on a JPEG frame.

    Returns:
        detections  — list of {class, confidence, bbox: [x1,y1,x2,y2]} in pixel coords
        yolo_boost  — score addend from spaghetti detections
        yolo_available — True if model was loaded and inference ran
    """
    sess = _get_session()
    if sess is None:
        return [], 0.0, False

    try:
        inp, orig_w, orig_h = _preprocess(jpeg_bytes)
        input_name = sess.get_inputs()[0].name
        raw = sess.run(None, {input_name: inp})[0]  # shape: (1, num_classes+4, num_anchors)

        # YOLOv11 output: (1, 7, 8400) — 4 box coords + 3 class scores per anchor.
        out = raw[0]  # (7, 8400)
        cx, cy, w, h = out[0], out[1], out[2], out[3]
        class_scores  = out[4:]  # (3, 8400)

        # Best class per anchor.
        class_ids  = class_scores.argmax(axis=0)
        confidences = class_scores.max(axis=0)

        # Filter by confidence.
        mask = confidences >= _CONF_THRESHOLD
        if not mask.any():
            return [], 0.0, True

        cx = cx[mask]; cy = cy[mask]; w = w[mask]; h = h[mask]
        class_ids = class_ids[mask]; confidences = confidences[mask]

        # Scale from model coords to original image coords.
        scale_x = orig_w / _INPUT_SIZE
        scale_y = orig_h / _INPUT_SIZE
        x1 = (cx - w / 2) * scale_x
        y1 = (cy - h / 2) * scale_y
        x2 = (cx + w / 2) * scale_x
        y2 = (cy + h / 2) * scale_y

        boxes_np = np.stack([x1, y1, x2, y2], axis=1)

        # Per-class NMS.
        detections = []
        for cls_idx in range(len(_CLASS_LABELS)):
            cls_mask = class_ids == cls_idx
            if not cls_mask.any():
                continue
            kept = _nms(boxes_np[cls_mask], confidences[cls_mask], _IOU_THRESHOLD)
            cls_boxes = boxes_np[cls_mask][kept]
            cls_confs = confidences[cls_mask][kept]
            for box, conf in zip(cls_boxes, cls_confs):
                detections.append({
                    "class":      _CLASS_LABELS[cls_idx],
                    "confidence": float(round(conf, 4)),
                    "bbox":       [int(box[0]), int(box[1]), int(box[2]), int(box[3])],
                })

        # Score boost: spaghetti detections above confidence threshold.
        # Sourced from Obico multi-frame weighting design (see BOOST_WEIGHT).
        boost = sum(
            d["confidence"] * BOOST_WEIGHT
            for d in detections
            if d["class"] == "spaghetti" and d["confidence"] > BOOST_MIN_CONFIDENCE
        )

        return detections, float(round(boost, 4)), True

    except Exception as e:
        log.warning("yolo_detector: inference failed: %s", e)
        return [], 0.0, False
