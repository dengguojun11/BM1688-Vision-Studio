#!/usr/bin/env python3
"""Run the single plate-detection Pose network on a BM1688 device."""

from __future__ import print_function

import argparse
import json
import os
import time

import cv2
import numpy as np

from plate_recognition_bm1688_infer import (
    _load_json,
    _write_json,
    decode_pose,
    preprocess,
)


class PlateDetectionEngine(object):
    def __init__(self, model_path, contract, device_id=0):
        import sophon.sail as sail

        self.engine = sail.Engine(model_path, device_id, sail.IOMode.SYSIO)
        self.contract = contract
        self.graph = str(
            (contract.get("network") or {}).get("name") or "plate_detection"
        )
        graphs = set(self.engine.get_graph_names())
        if self.graph not in graphs:
            raise RuntimeError("BModel missing network: {}".format(self.graph))

    def infer(self, image, confidence=0.25, iou_threshold=0.5):
        input_name = self.engine.get_input_names(self.graph)[0]
        shape = self.engine.get_input_shape(self.graph, input_name)
        tensor, ratio, pad = preprocess(
            image, int(shape[3]), int(shape[2]), use_letterbox=True
        )
        started = time.time()
        outputs = self.engine.process(self.graph, {input_name: tensor})
        elapsed_ms = (time.time() - started) * 1000.0
        raw = list(outputs.values())[0] if isinstance(outputs, dict) else outputs
        detections = decode_pose(
            raw, image.shape, ratio, pad, confidence, iou_threshold
        )
        class_names = (self.contract.get("network") or {}).get("class_names") or [
            "doubleEP"
        ]
        for item in detections:
            item["label"] = str(class_names[0])
        raw_pose = np.asarray(raw)
        return {
            "detections": detections,
            "pose_max_score": (
                float(np.max(raw_pose[:, 4:5, :]))
                if raw_pose.ndim == 3 and raw_pose.shape[1] >= 5
                else None
            ),
            "inference_ms": elapsed_ms,
        }


def _annotate(image, detections):
    output = image.copy()
    for item in detections:
        box = [int(round(value)) for value in item["box"]]
        cv2.rectangle(output, (box[0], box[1]), (box[2], box[3]), (0, 255, 0), 2)
        corners = np.asarray(item["corners"], dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(output, [corners], True, (0, 165, 255), 2)
        cv2.putText(
            output,
            "plate {:.2f}".format(float(item["score"])),
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
    engine = PlateDetectionEngine(args.model, contract, args.device_id)
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
        cv2.imwrite(
            os.path.join(args.output, os.path.basename(path)),
            _annotate(image, inference["detections"]),
        )
    payload = {
        "format": "labelu_plate_detection_bm1688_result_v1",
        "model": args.model,
        "images": results,
    }
    _write_json(os.path.join(args.output, "results.json"), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
