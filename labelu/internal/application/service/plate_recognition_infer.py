from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


class PlateDetectionPredictor:
    """Minimal Pose-only predictor used by the detection-only profile."""

    def __init__(self, weights: Path):
        from ultralytics import YOLO

        self._lock = threading.Lock()
        self.model = YOLO(str(weights))

    @staticmethod
    def _xyxy(value) -> list[float]:
        data = value.tolist() if hasattr(value, "tolist") else value
        if data and isinstance(data[0], (list, tuple)):
            data = data[0]
        return [float(item) for item in data[:4]]

    @staticmethod
    def _scalar(value) -> float:
        if hasattr(value, "item"):
            return float(value.item())
        data = value.tolist() if hasattr(value, "tolist") else value
        if isinstance(data, list):
            data = data[0]
        return float(data)

    def predict(self, image, *, confidence: float, imgsz: int) -> list[dict[str, Any]]:
        with self._lock:
            result = self.model.predict(
                source=image,
                conf=float(confidence),
                imgsz=int(imgsz),
                verbose=False,
            )[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or not len(boxes):
            return []
        keypoints = getattr(result, "keypoints", None)
        keypoint_xy = getattr(keypoints, "xy", None) if keypoints is not None else None
        keypoint_conf = (
            getattr(keypoints, "conf", None) if keypoints is not None else None
        )
        detections: list[dict[str, Any]] = []
        for index in range(len(boxes)):
            box = self._xyxy(boxes.xyxy[index])
            corners: list[dict[str, float]] = []
            if keypoint_xy is not None and index < len(keypoint_xy):
                points = keypoint_xy[index].tolist()
                scores = (
                    keypoint_conf[index].tolist()
                    if keypoint_conf is not None and index < len(keypoint_conf)
                    else [None] * len(points)
                )
                corners = [
                    {
                        "x": float(point[0]),
                        "y": float(point[1]),
                        "confidence": (
                            float(scores[point_index])
                            if scores[point_index] is not None
                            else None
                        ),
                    }
                    for point_index, point in enumerate(points[:4])
                ]
            detections.append(
                {
                    "confidence": round(self._scalar(boxes.conf[index]), 4),
                    "x1": box[0],
                    "y1": box[1],
                    "x2": box[2],
                    "y2": box[3],
                    "corners": corners,
                }
            )
        return detections


def _checkpoint(path: Path, *, component: str) -> dict[str, Any]:
    import torch

    try:
        checkpoint = torch.load(str(path), map_location="cpu", weights_only=True)
    except TypeError:
        checkpoint = torch.load(str(path), map_location="cpu")
    if not isinstance(checkpoint, dict) or checkpoint.get("component") != component:
        raise RuntimeError(f"Invalid plate {component} checkpoint: {path.name}")
    if not isinstance(checkpoint.get("state_dict"), dict):
        raise RuntimeError(f"Plate {component} checkpoint has no state_dict")
    return checkpoint


def _tensor_from_bgr(image, *, width: int, height: int):
    import cv2
    import torch

    resized = cv2.resize(
        image, (int(width), int(height)), interpolation=cv2.INTER_LINEAR
    )
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return torch.from_numpy(rgb.transpose(2, 0, 1).copy()).float().unsqueeze(0) / 255.0


def _perspective_crop(image, corners):
    import cv2
    import numpy as np

    points = np.asarray(corners, dtype=np.float32).reshape(4, 2)
    top = np.linalg.norm(points[1] - points[0])
    bottom = np.linalg.norm(points[2] - points[3])
    left = np.linalg.norm(points[3] - points[0])
    right = np.linalg.norm(points[2] - points[1])
    width = max(8, int(round(max(top, bottom))))
    height = max(8, int(round(max(left, right))))
    target = np.asarray(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype=np.float32,
    )
    matrix = cv2.getPerspectiveTransform(points, target)
    return cv2.warpPerspective(image, matrix, (width, height))


def _rect_crop(image, box):
    image_h, image_w = image.shape[:2]
    x1 = max(0, min(image_w - 1, int(round(box[0]))))
    y1 = max(0, min(image_h - 1, int(round(box[1]))))
    x2 = max(x1 + 1, min(image_w, int(round(box[2]))))
    y2 = max(y1 + 1, min(image_h, int(round(box[3]))))
    return image[y1:y2, x1:x2]


def _ocr_crop(plate, ratio: float):
    import cv2

    split = max(1, int(round(plate.shape[0] * float(ratio))))
    split = min(plate.shape[0] - 1, split)
    lower = plate[split:]
    if lower.size == 0:
        lower = plate[:split]
    lower = cv2.resize(lower, (192, 48), interpolation=cv2.INTER_LINEAR)
    gray = cv2.cvtColor(lower, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 4)).apply(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _ctc_decode(logits, characters: list[str], blank_index: int = 0) -> dict[str, Any]:
    import numpy as np

    values = np.asarray(logits, dtype=np.float32).reshape((-1, len(characters) + 1))
    values = values - np.max(values, axis=1, keepdims=True)
    probabilities = np.exp(values)
    probabilities /= np.sum(probabilities, axis=1, keepdims=True)
    sequence = np.argmax(probabilities, axis=1)
    decoded: list[str] = []
    emitted: list[float] = []
    previous = None
    for timestep, raw_index in enumerate(sequence):
        index = int(raw_index)
        if index != blank_index and index != previous:
            char_index = index - 1 if index > blank_index else index
            if 0 <= char_index < len(characters):
                decoded.append(characters[char_index])
                emitted.append(float(probabilities[timestep, index]))
        previous = index
    return {
        "text": "".join(decoded),
        "confidence": float(sum(emitted) / len(emitted)) if emitted else 0.0,
    }


def _select_ocr_candidate(
    candidates: list[dict[str, Any]],
    *,
    preferred_length: int | None,
    tolerance: float,
    anchor_ratio: float,
    preserve_confidence: float,
) -> tuple[dict[str, Any], bool]:
    if not candidates:
        return {"text": "", "confidence": 0.0}, False
    anchor = min(
        candidates, key=lambda item: abs(float(item["crop_ratio"]) - anchor_ratio)
    )
    if (
        preferred_length
        and len(anchor["text"]) != preferred_length
        and anchor["confidence"] >= preserve_confidence
    ):
        return anchor, False
    selected = max(candidates, key=lambda item: item["confidence"])
    preferred = [
        item
        for item in candidates
        if preferred_length and len(item["text"]) == preferred_length
    ]
    if preferred:
        best_preferred = max(preferred, key=lambda item: item["confidence"])
        if best_preferred["confidence"] >= selected["confidence"] - tolerance:
            selected = best_preferred
    return selected, bool(
        preferred_length and len(selected["text"]) != preferred_length
    )


def _apply_ocr_fallback(
    selected: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    preferred_length: int | None,
    tolerance: float,
) -> dict[str, Any]:
    preferred = [
        item
        for item in candidates
        if preferred_length and len(item["text"]) == preferred_length
    ]
    if not preferred:
        return selected
    best = max(preferred, key=lambda item: item["confidence"])
    return (
        best if best["confidence"] >= selected["confidence"] - tolerance else selected
    )


def _apply_guarded_consensus(
    selected: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    preferred_length: int | None,
    anchor_ratio: float,
    preserve_confidence: float,
    anchor_group_min_confidence: float,
    non_anchor_group_min_confidence: float,
) -> dict[str, Any]:
    if not candidates or not preferred_length:
        return selected
    anchor = min(
        candidates, key=lambda item: abs(float(item["crop_ratio"]) - anchor_ratio)
    )
    if (
        len(anchor["text"]) != preferred_length
        and anchor["confidence"] >= preserve_confidence
    ):
        return selected
    preferred = [item for item in candidates if len(item["text"]) == preferred_length]
    if not preferred:
        return selected

    consensus_characters = []
    for position in range(preferred_length):
        scores: dict[str, float] = {}
        for item in preferred:
            character = item["text"][position]
            scores[character] = scores.get(character, 0.0) + float(item["confidence"])
        consensus_characters.append(max(scores, key=scores.get))
    consensus = "".join(consensus_characters)
    if consensus == selected["text"]:
        return selected
    matches = [item for item in preferred if item["text"] == consensus]
    if len(selected["text"]) != preferred_length:
        return {
            "text": consensus,
            "confidence": max(float(item["confidence"]) for item in preferred),
            "crop_ratio": "guarded_consensus",
        }
    if len(matches) < 2:
        return selected
    group_confidence = max(float(item["confidence"]) for item in matches)
    minimum_confidence = (
        anchor_group_min_confidence
        if anchor["text"] == consensus
        else non_anchor_group_min_confidence
    )
    if group_confidence < minimum_confidence:
        return selected
    return {
        "text": consensus,
        "confidence": group_confidence,
        "crop_ratio": "guarded_consensus",
    }


class PlateRecognitionPredictor:
    def __init__(self, *, artifacts: dict[str, Path], metadata: dict[str, Any]):
        import torch
        from ultralytics import YOLO

        from labelu.internal.common.plate_recognition_models import (
            build_plate_classifier,
            build_plate_ocr_model,
        )

        self._lock = threading.Lock()
        self.pose = YOLO(str(artifacts["pose"]))
        self.metadata = metadata

        ocr_checkpoint = _checkpoint(artifacts["ocr"], component="ocr")
        self.characters = [str(value) for value in ocr_checkpoint.get("labels", [])[1:]]
        self.ocr = build_plate_ocr_model(len(self.characters) + 1)
        self.ocr.load_state_dict(ocr_checkpoint["state_dict"])
        self.ocr.eval()

        self.classifiers: dict[str, tuple[Any, list[str], tuple[int, int]]] = {}
        for component in ("color", "plate_type"):
            checkpoint = _checkpoint(artifacts[component], component=component)
            labels = [str(value) for value in checkpoint.get("labels", [])]
            model = build_plate_classifier(len(labels))
            model.load_state_dict(checkpoint["state_dict"])
            model.eval()
            input_size = checkpoint.get("input_size") or [128, 64]
            self.classifiers[component] = (
                model,
                labels,
                (int(input_size[0]), int(input_size[1])),
            )
        self.torch = torch

    @staticmethod
    def _scalar(value) -> float:
        if hasattr(value, "item"):
            return float(value.item())
        data = value.tolist() if hasattr(value, "tolist") else value
        if isinstance(data, list):
            data = data[0]
        return float(data)

    @staticmethod
    def _xyxy(value) -> list[float]:
        data = value.tolist() if hasattr(value, "tolist") else value
        if data and isinstance(data[0], (list, tuple)):
            data = data[0]
        return [float(item) for item in data[:4]]

    def _ocr_prediction(self, plate) -> dict[str, Any]:
        ocr_meta = self.metadata.get("ocr") or {}
        crop_ratios = [float(value) for value in (ocr_meta.get("crop_ratios") or [0.5])]
        preferred_length = ocr_meta.get("preferred_length")
        preferred_length = int(preferred_length) if preferred_length else None
        tolerance = float(ocr_meta.get("length_preference_tolerance", 0.1))
        anchor_ratio = float(ocr_meta.get("length_anchor_crop_ratio", 0.5))
        preserve_confidence = float(
            ocr_meta.get("non_preferred_length_preserve_confidence", 0.99)
        )

        def predict_ratio(ratio: float) -> dict[str, Any]:
            tensor = _tensor_from_bgr(_ocr_crop(plate, ratio), width=192, height=48)
            with self.torch.no_grad():
                logits = self.ocr(tensor).cpu().numpy()[0]
            return {**_ctc_decode(logits, self.characters), "crop_ratio": ratio}

        candidates = [predict_ratio(ratio) for ratio in crop_ratios]
        selected, needs_fallback = _select_ocr_candidate(
            candidates,
            preferred_length=preferred_length,
            tolerance=tolerance,
            anchor_ratio=anchor_ratio,
            preserve_confidence=preserve_confidence,
        )
        if needs_fallback:
            fallback = [
                predict_ratio(float(ratio))
                for ratio in (ocr_meta.get("fallback_crop_ratios") or [])
            ]
            selected = _apply_ocr_fallback(
                selected,
                fallback,
                preferred_length=preferred_length,
                tolerance=tolerance,
            )
        if bool(ocr_meta.get("guarded_consensus_enabled", True)):
            selected = _apply_guarded_consensus(
                selected,
                candidates,
                preferred_length=preferred_length,
                anchor_ratio=anchor_ratio,
                preserve_confidence=preserve_confidence,
                anchor_group_min_confidence=float(
                    ocr_meta.get("anchor_group_min_confidence", 0.9)
                ),
                non_anchor_group_min_confidence=float(
                    ocr_meta.get("non_anchor_group_min_confidence", 0.94)
                ),
            )
        return selected

    def _classification(self, component: str, crop) -> tuple[str, float]:
        model, labels, input_size = self.classifiers[component]
        tensor = _tensor_from_bgr(crop, width=input_size[0], height=input_size[1])
        with self.torch.no_grad():
            probabilities = model(tensor).softmax(1)[0]
        index = int(probabilities.argmax().item())
        return labels[index], float(probabilities[index].item())

    def predict(self, image, *, confidence: float, imgsz: int) -> list[dict[str, Any]]:
        import cv2
        import numpy as np

        bgr = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        with self._lock:
            result = self.pose.predict(
                source=image,
                conf=float(confidence),
                imgsz=int(imgsz),
                verbose=False,
            )[0]
            boxes = getattr(result, "boxes", None)
            keypoints = getattr(result, "keypoints", None)
            if boxes is None or not len(boxes):
                return []
            keypoint_xy = (
                getattr(keypoints, "xy", None) if keypoints is not None else None
            )
            keypoint_conf = (
                getattr(keypoints, "conf", None) if keypoints is not None else None
            )
            detections: list[dict[str, Any]] = []
            for index in range(len(boxes)):
                box = self._xyxy(boxes.xyxy[index])
                corners: list[dict[str, float]] = []
                if keypoint_xy is not None and index < len(keypoint_xy):
                    points = keypoint_xy[index].tolist()
                    scores = (
                        keypoint_conf[index].tolist()
                        if keypoint_conf is not None and index < len(keypoint_conf)
                        else [None] * len(points)
                    )
                    corners = [
                        {
                            "x": float(point[0]),
                            "y": float(point[1]),
                            "confidence": (
                                float(scores[point_index])
                                if scores[point_index] is not None
                                else None
                            ),
                        }
                        for point_index, point in enumerate(points[:4])
                    ]
                plate_number = {"text": "", "confidence": 0.0}
                if len(corners) == 4:
                    plate = _perspective_crop(
                        bgr, [[point["x"], point["y"]] for point in corners]
                    )
                    plate_number = self._ocr_prediction(plate)
                classifier_crop = _rect_crop(bgr, box)
                color, color_confidence = self._classification("color", classifier_crop)
                plate_type, type_confidence = self._classification(
                    "plate_type", classifier_crop
                )
                detections.append(
                    {
                        "confidence": round(self._scalar(boxes.conf[index]), 4),
                        "x1": box[0],
                        "y1": box[1],
                        "x2": box[2],
                        "y2": box[3],
                        "corners": corners,
                        "plate_number": str(plate_number["text"]),
                        "plate_number_confidence": round(
                            float(plate_number["confidence"]), 4
                        ),
                        "color": color,
                        "color_confidence": round(color_confidence, 4),
                        "plate_type": plate_type,
                        "plate_type_confidence": round(type_confidence, 4),
                    }
                )
            return detections


_CACHE_LOCK = threading.Lock()
_PREDICTOR_CACHE: dict[tuple[tuple[str, int], ...], PlateRecognitionPredictor] = {}
_DETECTION_CACHE: dict[tuple[str, int], PlateDetectionPredictor] = {}


def infer_plate_detection(
    image,
    *,
    weights: Path,
    confidence: float,
    imgsz: int,
) -> list[dict[str, Any]]:
    path = Path(weights)
    cache_key = (str(path), path.stat().st_mtime_ns)
    with _CACHE_LOCK:
        predictor = _DETECTION_CACHE.get(cache_key)
        if predictor is None:
            predictor = PlateDetectionPredictor(path)
            _DETECTION_CACHE.clear()
            _DETECTION_CACHE[cache_key] = predictor
    return predictor.predict(image, confidence=confidence, imgsz=imgsz)


def infer_plate_recognition(
    image,
    *,
    artifacts: dict[str, Path],
    metadata: dict[str, Any],
    confidence: float,
    imgsz: int,
) -> list[dict[str, Any]]:
    cache_key = tuple(
        (str(artifacts[name]), artifacts[name].stat().st_mtime_ns)
        for name in ("pose", "ocr", "color", "plate_type")
    )
    with _CACHE_LOCK:
        predictor = _PREDICTOR_CACHE.get(cache_key)
        if predictor is None:
            predictor = PlateRecognitionPredictor(
                artifacts=artifacts, metadata=metadata
            )
            _PREDICTOR_CACHE.clear()
            _PREDICTOR_CACHE[cache_key] = predictor
    return predictor.predict(image, confidence=confidence, imgsz=imgsz)
