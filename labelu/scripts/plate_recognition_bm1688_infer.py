#!/usr/bin/env python3
"""Run the four-network plate-recognition pipeline on a BM1688 device."""

from __future__ import print_function

import argparse
import io
import json
import os
import time

import cv2
import numpy as np


def _load_json(path):
    with io.open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path, payload):
    with io.open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def letterbox(image, width, height, color=(114, 114, 114)):
    src_h, src_w = image.shape[:2]
    ratio = min(float(width) / src_w, float(height) / src_h)
    resized_w = int(round(src_w * ratio))
    resized_h = int(round(src_h * ratio))
    resized = cv2.resize(image, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
    pad_w = width - resized_w
    pad_h = height - resized_h
    left = int(round(pad_w / 2.0 - 0.1))
    right = int(round(pad_w / 2.0 + 0.1))
    top = int(round(pad_h / 2.0 - 0.1))
    bottom = int(round(pad_h / 2.0 + 0.1))
    output = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=color
    )
    return output, ratio, (pad_w / 2.0, pad_h / 2.0)


def preprocess(image, width, height, use_letterbox=False):
    if use_letterbox:
        resized, ratio, pad = letterbox(image, width, height)
    else:
        resized = cv2.resize(image, (width, height), interpolation=cv2.INTER_LINEAR)
        ratio = (float(width) / image.shape[1], float(height) / image.shape[0])
        pad = (0.0, 0.0)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    tensor = rgb.transpose(2, 0, 1).astype(np.float32) / 255.0
    return np.expand_dims(tensor, 0), ratio, pad


def _iou(left, right):
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    return intersection / (left_area + right_area - intersection + 1e-9)


def _nms(predictions, threshold):
    ordered = sorted(predictions, key=lambda item: item["score"], reverse=True)
    kept = []
    for candidate in ordered:
        if all(_iou(candidate["box"], item["box"]) <= threshold for item in kept):
            kept.append(candidate)
    return kept


def _restore_xy(x, y, ratio, pad, image_width, image_height):
    ratio_x = ratio[0] if isinstance(ratio, tuple) else ratio
    ratio_y = ratio[1] if isinstance(ratio, tuple) else ratio
    restored_x = (float(x) - pad[0]) / ratio_x
    restored_y = (float(y) - pad[1]) / ratio_y
    return (
        max(0.0, min(float(image_width), restored_x)),
        max(0.0, min(float(image_height), restored_y)),
    )


def order_corners(points):
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    ordered = np.zeros((4, 2), dtype=np.float32)
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[np.argmin(sums)]
    ordered[2] = points[np.argmax(sums)]
    ordered[1] = points[np.argmin(differences)]
    ordered[3] = points[np.argmax(differences)]
    if len({(float(x), float(y)) for x, y in ordered}) != 4:
        raise ValueError("predicted plate corners are not unique")
    return ordered


def contract_ordered_corners(points):
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    if len({(float(x), float(y)) for x, y in points}) != 4:
        raise ValueError("predicted plate corners are not unique")
    contour = points.reshape((-1, 1, 2))
    if abs(float(cv2.contourArea(contour))) < 1.0:
        raise ValueError("predicted plate corners are degenerate")
    return points


def decode_pose(raw, image_shape, ratio, pad, confidence, iou_threshold):
    output = np.squeeze(np.asarray(raw))
    if output.ndim == 1 and output.size == 17:
        output = output.reshape(1, 17)
    if output.ndim != 2:
        raise ValueError("unexpected pose output shape: {}".format(output.shape))
    if output.shape[0] == 17:
        output = output.T
    if output.shape[1] != 17:
        raise ValueError("unexpected pose feature count: {}".format(output.shape))
    image_h, image_w = image_shape[:2]
    predictions = []
    for row in output:
        score = float(row[4])
        if score < confidence:
            continue
        center_x, center_y, width, height = [float(value) for value in row[:4]]
        top_left = _restore_xy(
            center_x - width / 2.0,
            center_y - height / 2.0,
            ratio,
            pad,
            image_w,
            image_h,
        )
        bottom_right = _restore_xy(
            center_x + width / 2.0,
            center_y + height / 2.0,
            ratio,
            pad,
            image_w,
            image_h,
        )
        corners = []
        corner_scores = []
        for index in range(4):
            offset = 5 + index * 3
            corners.append(
                _restore_xy(row[offset], row[offset + 1], ratio, pad, image_w, image_h)
            )
            corner_scores.append(float(row[offset + 2]))
        predictions.append(
            {
                "score": score,
                "box": [top_left[0], top_left[1], bottom_right[0], bottom_right[1]],
                "corners": corners,
                "corner_scores": corner_scores,
            }
        )
    return _nms(predictions, iou_threshold)


def perspective_crop(image, corners):
    # Pose output already follows the contract order: TL, TR, BR, BL. Coordinate
    # sum/difference sorting is ambiguous for steeply rotated plates.
    points = contract_ordered_corners(corners)
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


def double_row_image(plate, start_ratio=0.5):
    split = max(1, int(round(plate.shape[0] * float(start_ratio))))
    split = min(plate.shape[0] - 1, split)
    lower = plate[split:]
    if lower.size == 0:
        lower = plate[:split]
    return cv2.resize(lower, (192, 48), interpolation=cv2.INTER_LINEAR)


def enhance_ocr_image(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 4)).apply(gray)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def rect_crop(image, box):
    image_h, image_w = image.shape[:2]
    x1 = max(0, min(image_w - 1, int(round(box[0]))))
    y1 = max(0, min(image_h - 1, int(round(box[1]))))
    x2 = max(x1 + 1, min(image_w, int(round(box[2]))))
    y2 = max(y1 + 1, min(image_h, int(round(box[3]))))
    return image[y1:y2, x1:x2]


def ctc_decode(logits, characters, blank_index=0):
    sequence = np.argmax(np.asarray(logits), axis=-1).reshape(-1)
    decoded = []
    previous = None
    for raw_index in sequence:
        index = int(raw_index)
        if index != blank_index and index != previous:
            char_index = index - 1 if index > blank_index else index
            if 0 <= char_index < len(characters):
                decoded.append(characters[char_index])
        previous = index
    return "".join(decoded)


def ctc_decode_with_confidence(logits, characters, blank_index=0):
    values = np.asarray(logits, dtype=np.float32)
    values = values.reshape((-1, values.shape[-1]))
    values = values - np.max(values, axis=1, keepdims=True)
    probabilities = np.exp(values)
    probabilities /= np.sum(probabilities, axis=1, keepdims=True)
    sequence = np.argmax(probabilities, axis=1)
    emitted = []
    previous = None
    for timestep, raw_index in enumerate(sequence):
        index = int(raw_index)
        if index != blank_index and index != previous:
            emitted.append(float(probabilities[timestep, index]))
        previous = index
    return {
        "text": ctc_decode(logits, characters, blank_index=blank_index),
        "confidence": float(np.mean(emitted)) if emitted else 0.0,
    }


def select_ocr_candidate(candidates, preferred_length=None, tolerance=0.1):
    if not candidates:
        return {"text": "", "confidence": 0.0}
    best = max(candidates, key=lambda item: item["confidence"])
    if not preferred_length:
        return best
    preferred = [
        item for item in candidates if len(item["text"]) == int(preferred_length)
    ]
    if not preferred:
        return best
    best_preferred = max(preferred, key=lambda item: item["confidence"])
    if best_preferred["confidence"] >= best["confidence"] - float(tolerance):
        return best_preferred
    return best


def select_primary_ocr_candidate(
    candidates,
    preferred_length=None,
    tolerance=0.1,
    anchor_ratio=0.5,
    preserve_confidence=0.99,
):
    if not candidates:
        return {"text": "", "confidence": 0.0}, False
    anchor = min(
        candidates,
        key=lambda item: abs(
            float(item.get("crop_ratio", anchor_ratio)) - anchor_ratio
        ),
    )
    if (
        preferred_length
        and len(anchor["text"]) != int(preferred_length)
        and anchor["confidence"] >= float(preserve_confidence)
    ):
        return anchor, False
    selected = select_ocr_candidate(
        candidates,
        preferred_length=preferred_length,
        tolerance=tolerance,
    )
    needs_fallback = bool(
        preferred_length and len(selected["text"]) != int(preferred_length)
    )
    return selected, needs_fallback


def select_ocr_fallback_candidate(
    selected,
    fallback_candidates,
    preferred_length=None,
    tolerance=0.1,
):
    if not preferred_length:
        return selected
    preferred = [
        item
        for item in fallback_candidates
        if len(item["text"]) == int(preferred_length)
    ]
    if not preferred:
        return selected
    best_fallback = max(preferred, key=lambda item: item["confidence"])
    if best_fallback["confidence"] >= selected["confidence"] - float(tolerance):
        return best_fallback
    return selected


def select_guarded_consensus_candidate(
    selected,
    candidates,
    preferred_length=None,
    anchor_ratio=0.5,
    preserve_confidence=0.99,
    anchor_group_min_confidence=0.90,
    non_anchor_group_min_confidence=0.94,
):
    if not candidates or not preferred_length:
        return selected
    preferred_length = int(preferred_length)
    anchor = min(
        candidates,
        key=lambda item: abs(
            float(item.get("crop_ratio", anchor_ratio)) - anchor_ratio
        ),
    )
    if (
        len(anchor["text"]) != preferred_length
        and anchor["confidence"] >= float(preserve_confidence)
    ):
        return selected
    preferred = [
        item for item in candidates if len(item["text"]) == preferred_length
    ]
    if not preferred:
        return selected

    character_scores = []
    for position in range(preferred_length):
        scores = {}
        for item in preferred:
            character = item["text"][position]
            scores[character] = scores.get(character, 0.0) + float(
                item["confidence"]
            )
        character_scores.append(max(scores, key=scores.get))
    consensus_text = "".join(character_scores)
    if consensus_text == selected["text"]:
        return selected
    consensus_matches = [
        item for item in preferred if item["text"] == consensus_text
    ]
    if len(selected["text"]) != preferred_length:
        return {
            "text": consensus_text,
            "confidence": max(item["confidence"] for item in preferred),
            "crop_ratio": "guarded_consensus",
        }
    if len(consensus_matches) < 2:
        return selected
    group_confidence = max(item["confidence"] for item in consensus_matches)
    anchor_in_group = anchor["text"] == consensus_text
    minimum_confidence = (
        anchor_group_min_confidence
        if anchor_in_group
        else non_anchor_group_min_confidence
    )
    if group_confidence < float(minimum_confidence):
        return selected
    return {
        "text": consensus_text,
        "confidence": group_confidence,
        "crop_ratio": "guarded_consensus",
    }


def softmax(values):
    values = np.asarray(values, dtype=np.float32).reshape(-1)
    exp = np.exp(values - np.max(values))
    return exp / np.sum(exp)


class PlateRecognitionEngine(object):
    def __init__(self, model_path, contract, device_id=0):
        import sophon.sail as sail

        self.engine = sail.Engine(model_path, device_id, sail.IOMode.SYSIO)
        self.contract = contract
        self.graphs = set(self.engine.get_graph_names())
        required = {"plate_pose", "plate_ocr", "plate_color", "plate_type"}
        missing = sorted(required - self.graphs)
        if missing:
            raise RuntimeError("BModel missing networks: {}".format(", ".join(missing)))

    def _run(self, graph, image, letterbox_input=False):
        input_name = self.engine.get_input_names(graph)[0]
        shape = self.engine.get_input_shape(graph, input_name)
        tensor, ratio, pad = preprocess(
            image, int(shape[3]), int(shape[2]), use_letterbox=letterbox_input
        )
        started = time.time()
        outputs = self.engine.process(graph, {input_name: tensor})
        elapsed_ms = (time.time() - started) * 1000.0
        raw = list(outputs.values())[0] if isinstance(outputs, dict) else outputs
        return raw, ratio, pad, elapsed_ms

    def infer(self, image, confidence=0.25, iou_threshold=0.5):
        pose_raw, ratio, pad, pose_ms = self._run(
            "plate_pose", image, letterbox_input=True
        )
        pose_predictions = decode_pose(
            pose_raw, image.shape, ratio, pad, confidence, iou_threshold
        )
        characters = self.contract["networks"]["plate_ocr"]["characters"]
        ocr_contract = self.contract["networks"]["plate_ocr"]
        crop_ratios = ocr_contract.get("crop_ratios") or [0.5]
        fallback_crop_ratios = ocr_contract.get("fallback_crop_ratios") or []
        preferred_length = ocr_contract.get("preferred_length")
        anchor_crop_ratio = float(ocr_contract.get("length_anchor_crop_ratio", 0.5))
        length_tolerance = ocr_contract.get("length_preference_tolerance", 0.1)
        preserve_confidence = ocr_contract.get(
            "non_preferred_length_preserve_confidence", 0.99
        )
        guarded_consensus_enabled = bool(
            ocr_contract.get("guarded_consensus_enabled", False)
        )
        anchor_group_min_confidence = ocr_contract.get(
            "anchor_group_min_confidence", 0.90
        )
        non_anchor_group_min_confidence = ocr_contract.get(
            "non_anchor_group_min_confidence", 0.94
        )
        color_classes = self.contract["networks"]["plate_color"]["output"]["classes"]
        type_classes = self.contract["networks"]["plate_type"]["output"]["classes"]
        results = []
        rejected_predictions = []
        for pose in pose_predictions:
            try:
                plate = perspective_crop(image, pose["corners"])
            except ValueError as exc:
                rejected = dict(pose)
                rejected["rejected_reason"] = str(exc)
                rejected_predictions.append(rejected)
                continue
            classifier_input = rect_crop(image, pose["box"])
            ocr_candidates = []
            ocr_ms = 0.0
            for crop_ratio in crop_ratios:
                ocr_input = enhance_ocr_image(
                    double_row_image(plate, start_ratio=float(crop_ratio))
                )
                ocr_raw, _ratio, _pad, candidate_ms = self._run("plate_ocr", ocr_input)
                ocr_ms += candidate_ms
                candidate = ctc_decode_with_confidence(ocr_raw, characters)
                candidate["crop_ratio"] = float(crop_ratio)
                ocr_candidates.append(candidate)
            selected_ocr, needs_fallback = select_primary_ocr_candidate(
                ocr_candidates,
                preferred_length=preferred_length,
                tolerance=length_tolerance,
                anchor_ratio=anchor_crop_ratio,
                preserve_confidence=preserve_confidence,
            )
            if needs_fallback:
                fallback_candidates = []
                for crop_ratio in fallback_crop_ratios:
                    ocr_input = enhance_ocr_image(
                        double_row_image(plate, start_ratio=float(crop_ratio))
                    )
                    ocr_raw, _ratio, _pad, candidate_ms = self._run(
                        "plate_ocr", ocr_input
                    )
                    ocr_ms += candidate_ms
                    candidate = ctc_decode_with_confidence(ocr_raw, characters)
                    candidate["crop_ratio"] = float(crop_ratio)
                    fallback_candidates.append(candidate)
                ocr_candidates.extend(fallback_candidates)
                selected_ocr = select_ocr_fallback_candidate(
                    selected_ocr,
                    fallback_candidates,
                    preferred_length=preferred_length,
                    tolerance=length_tolerance,
                )
            if guarded_consensus_enabled:
                selected_ocr = select_guarded_consensus_candidate(
                    selected_ocr,
                    ocr_candidates[: len(crop_ratios)],
                    preferred_length=preferred_length,
                    anchor_ratio=anchor_crop_ratio,
                    preserve_confidence=preserve_confidence,
                    anchor_group_min_confidence=anchor_group_min_confidence,
                    non_anchor_group_min_confidence=(
                        non_anchor_group_min_confidence
                    ),
                )
            color_raw, _ratio, _pad, color_ms = self._run(
                "plate_color", classifier_input
            )
            type_raw, _ratio, _pad, type_ms = self._run("plate_type", classifier_input)
            color_probabilities = softmax(color_raw)
            type_probabilities = softmax(type_raw)
            color_index = int(np.argmax(color_probabilities))
            type_index = int(np.argmax(type_probabilities))
            result = dict(pose)
            result.update(
                {
                    "plate_number": selected_ocr["text"],
                    "plate_number_score": selected_ocr["confidence"],
                    "ocr_candidates": ocr_candidates,
                    "color": color_classes[color_index],
                    "color_score": float(color_probabilities[color_index]),
                    "plate_type": type_classes[type_index],
                    "plate_type_score": float(type_probabilities[type_index]),
                    "timing_ms": {
                        "pose": pose_ms,
                        "ocr": ocr_ms,
                        "color": color_ms,
                        "plate_type": type_ms,
                    },
                }
            )
            results.append(result)
        raw_pose = np.asarray(pose_raw)
        return {
            "detections": results,
            "rejected_predictions": rejected_predictions,
            "pose_max_score": (
                float(np.max(raw_pose[:, 4:5, :]))
                if raw_pose.ndim == 3 and raw_pose.shape[1] >= 5
                else None
            ),
            "pose_inference_ms": pose_ms,
        }


def _annotate(image, detections):
    output = image.copy()
    for item in detections:
        box = [int(round(value)) for value in item["box"]]
        cv2.rectangle(output, (box[0], box[1]), (box[2], box[3]), (0, 255, 0), 2)
        corners = np.asarray(item["corners"], dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(output, [corners], True, (0, 165, 255), 2)
        label = "{} {} {}".format(
            item["plate_number"], item["color"], item["plate_type"]
        )
        cv2.putText(
            output,
            label,
            (box[0], max(20, box[1] - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 0),
            2,
        )
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.5)
    args = parser.parse_args()

    contract = _load_json(args.contract)
    engine = PlateRecognitionEngine(args.model, contract, args.device_id)
    os.makedirs(args.output, exist_ok=True)
    input_paths = []
    if os.path.isdir(args.input):
        for name in sorted(os.listdir(args.input)):
            if os.path.splitext(name)[1].lower() in (".jpg", ".jpeg", ".png", ".bmp"):
                input_paths.append(os.path.join(args.input, name))
    else:
        input_paths.append(args.input)

    results = []
    for path in input_paths:
        image = cv2.imread(path)
        if image is None:
            results.append({"image": path, "error": "cannot read image"})
            continue
        inference = engine.infer(image, args.confidence, args.iou)
        inference["image"] = path
        results.append(inference)
        annotated = _annotate(image, inference["detections"])
        cv2.imwrite(os.path.join(args.output, os.path.basename(path)), annotated)
    payload = {
        "format": "labelu_plate_recognition_bm1688_result_v1",
        "model": args.model,
        "images": results,
    }
    result_path = os.path.join(args.output, "results.json")
    _write_json(result_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
