"""
BM1688 边端 bmodel mAP 评估。

优先 sophon-demo yolov8_bmcv（与板端验证一致）；回退 sophon.sail + 共用后处理。
"""
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from evaluation_utils import (
    Detection,
    SIZE_BINS,
    _empty_stats,
    attach_scene_attrs,
    build_business_threshold_analysis,
    get_cpu_memory_mb,
    load_dataset_from_yaml,
    load_yolo_labels,
    match_detections_grouped,
    plot_f1_summary_curve,
    plot_scene_metrics,
    plot_size_metrics,
    summarize_metrics,
    summarize_scene_metrics,
    summarize_size_metrics,
    write_markdown_report,
)
from yolo_eval_postprocess import (
    NMS_THRESH,
    postprocess_yolo_onnx,
    preprocess_image,
)
from edge_performance_probe import collect_environment, run_performance_probe

EVAL_DATA_PATH = os.getenv("EVAL_DATA_PATH", "./data/")
EVAL_XML_DIR = os.getenv("EVAL_XML_DIR", "")
EVAL_SAVE_DIR = os.getenv("EVAL_SAVE_DIR", "./results_bmodel")
EVAL_MODEL_PATH = os.getenv("EVAL_MODEL_PATH", "./models/BM1688")
EVAL_MODEL_FILE = os.getenv("EVAL_MODEL_FILE", "").strip()
EVAL_MODEL_TAG = os.getenv("EVAL_MODEL_TAG", "").strip().lower()
EVAL_CONF_THRES = float(os.getenv("EVAL_CONF_THRES", "0.35"))
EVAL_IOU_THRES = float(os.getenv("EVAL_IOU_THRES", "0.5"))
EVAL_DEV_ID = int(os.getenv("EVAL_DEV_ID", "0"))
SOPHON_DEMO_ROOT = os.getenv("SOPHON_DEMO_ROOT", "").strip()
DUMP_PREDICTIONS = os.getenv("EVAL_DUMP_PREDICTIONS", "1") != "0"
EVAL_PERFORMANCE_ENABLED = os.getenv("EVAL_PERFORMANCE_ENABLED", "1") != "0"
EVAL_PERFORMANCE_WARMUP_COUNT = int(os.getenv("EVAL_PERFORMANCE_WARMUP_COUNT", "50"))
EVAL_PERFORMANCE_REPEAT_COUNT = int(os.getenv("EVAL_PERFORMANCE_REPEAT_COUNT", "1000"))
EVAL_RESOURCE_INTERVAL_SECONDS = float(os.getenv("EVAL_RESOURCE_INTERVAL_SECONDS", "1"))
EVAL_STABILITY_SECONDS = float(os.getenv("EVAL_STABILITY_SECONDS", "0"))


def _ascii_detection_label(class_names, detection):
    class_id = int(detection.class_id)
    name = (
        str(class_names[class_id])
        if 0 <= class_id < len(class_names)
        else "class_{}".format(class_id)
    )
    try:
        name.encode("ascii")
    except UnicodeEncodeError:
        name = "class_{}".format(class_id)
    return "{} {:.2f}".format(name, float(detection.score))


def _render_prediction_image(image, source_path, predictions, class_names, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    canvas = image.copy()
    for detection in predictions:
        class_id = int(detection.class_id)
        color = (
            int((37 * (class_id + 3)) % 205 + 40),
            int((67 * (class_id + 5)) % 205 + 40),
            int((97 * (class_id + 7)) % 205 + 40),
        )
        x1 = max(0, int(round(detection.x1)))
        y1 = max(0, int(round(detection.y1)))
        x2 = max(x1, int(round(detection.x2)))
        y2 = max(y1, int(round(detection.y2)))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        cv2.putText(
            canvas,
            _ascii_detection_label(class_names, detection),
            (x1, max(18, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
        )
    if not predictions:
        cv2.rectangle(canvas, (8, 8), (190, 38), (80, 80, 80), -1)
        cv2.putText(
            canvas,
            "NO DETECTION",
            (16, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62,
            (255, 255, 255),
            2,
        )
    destination = output_dir / Path(source_path).name
    if not cv2.imwrite(str(destination), canvas):
        return None
    return destination


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    raise TypeError("Object of type '%s' is not JSON serializable" % type(value).__name__)


def _env_class_names(default_names):
    raw = os.getenv("EVAL_CLASS_NAMES", "").strip()
    if not raw:
        return list(default_names)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return [parsed[k] for k in sorted(parsed, key=lambda x: int(x))]
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        pass
    return [x.strip() for x in raw.split(",") if x.strip()]


def _resolve_int8_bmodel(model_dir: Path) -> Optional[Path]:
    if model_dir.is_file() and model_dir.suffix == ".bmodel":
        return model_dir
    if not model_dir.is_dir():
        return None
    preferred = sorted(model_dir.glob("*int8*_1b.bmodel"))
    if preferred:
        return preferred[0]
    any_int8 = sorted(model_dir.glob("*int8*.bmodel"))
    if any_int8:
        return any_int8[0]
    any_bm = sorted(model_dir.glob("*.bmodel"))
    return any_bm[0] if any_bm else None


def _resolve_requested_bmodel(model_dir: Path) -> Optional[Path]:
    """Resolve a requested precision, falling back to the historical INT8 behavior."""
    if EVAL_MODEL_FILE:
        requested = Path(EVAL_MODEL_FILE)
        if not requested.is_absolute():
            requested = model_dir / requested
        return requested if requested.is_file() else None
    if EVAL_MODEL_TAG in {"fp32", "fp16", "int8"} and model_dir.is_dir():
        matches = sorted(model_dir.glob(f"*_{EVAL_MODEL_TAG}_1b.bmodel"))
        if matches:
            return matches[0]
    return _resolve_int8_bmodel(model_dir)


def _find_bmcv_result_json(demo_root: Path, bmodel: Path, input_dir: Path) -> Optional[Path]:
    results = demo_root / "results"
    if not results.is_dir():
        return None
    bname = bmodel.name
    inname = input_dir.name
    cands = sorted(results.glob("{}_{}_bmcv_python_result.json".format(bname, inname)))
    if not cands:
        cands = sorted(results.glob("{}_*_bmcv_python_result.json".format(bname)))
    return cands[-1] if cands else None


class _BmcvResultsGuard:
    def __init__(self, demo_root: Path, bmodel_name: str):
        self.demo_root = demo_root.resolve()
        self.bmodel_name = bmodel_name
        self._lock_fh = None
        self.started_at = 0.0

    def __enter__(self) -> float:
        results = self.demo_root / "results"
        results.mkdir(parents=True, exist_ok=True)
        lock_path = results / ".labelu_bmcv.lock"
        self._lock_fh = open(lock_path, "a+", encoding="utf-8")
        try:
            import fcntl  # type: ignore

            fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        _cleanup_stale_bmcv_outputs(self.demo_root, self.bmodel_name)
        self.started_at = time.time()
        return self.started_at

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._lock_fh is None:
            return
        try:
            import fcntl  # type: ignore

            fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._lock_fh.close()
        except Exception:
            pass
        self._lock_fh = None


def _cleanup_stale_bmcv_outputs(demo_root: Path, bmodel_name: str) -> None:
    results = demo_root / "results"
    if not results.is_dir():
        return
    stem = Path(bmodel_name).stem
    for pat in (
        "{}_*_bmcv_python_result.json".format(bmodel_name),
        "{}_*_bmcv_python_result.json".format(stem),
        "*{}*bmcv*result*.json".format(stem),
    ):
        for fp in results.glob(pat):
            try:
                fp.unlink()
            except OSError:
                pass
    images_dir = results / "images"
    if images_dir.is_dir():
        import shutil

        shutil.rmtree(str(images_dir), ignore_errors=True)


def _find_fresh_bmcv_result_json(
    demo_root: Path,
    bmodel: Path,
    *,
    started_at: float,
) -> Optional[Path]:
    results = demo_root / "results"
    if not results.is_dir():
        return None
    stem = bmodel.stem
    cutoff = max(0.0, float(started_at) - 1.0)
    candidates = []
    for pat in (
        "{}_*_bmcv_python_result.json".format(bmodel.name),
        "{}_*_bmcv_python_result.json".format(stem),
        "*{}*bmcv*result*.json".format(stem),
    ):
        candidates.extend(results.glob(pat))
    fresh = []
    seen = set()
    for fp in candidates:
        try:
            resolved = fp.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if fp.stat().st_mtime >= cutoff:
                fresh.append(fp)
        except OSError:
            continue
    fresh.sort(key=lambda p: p.stat().st_mtime)
    return fresh[-1] if fresh else None


def _load_bmcv_predictions(result_json: Path, class_count: int) -> Dict[str, List[Detection]]:
    data = json.loads(result_json.read_text(encoding="utf-8"))
    out: Dict[str, List[Detection]] = {}
    for item in data:
        name = str(item.get("image_name") or "")
        preds: List[Detection] = []
        for box in item.get("bboxes") or []:
            bbox = box.get("bbox") or []
            if len(bbox) < 4:
                continue
            x, y, w, h = [float(v) for v in bbox[:4]]
            cid = int(box.get("category_id", -1))
            score = float(box.get("score", 0.0))
            if score < EVAL_CONF_THRES or not (0 <= cid < class_count):
                continue
            preds.append(Detection(x, y, x + w, y + h, cid, score))
        out[name] = preds
    return out


def _run_yolov8_bmcv(demo_root: Path, bmodel: Path, input_dir: Path) -> Tuple[bool, str, Optional[Path]]:
    script = demo_root / "python" / "yolov8_bmcv.py"
    if not script.is_file():
        return False, "缺少 yolov8_bmcv.py", None
    cmd = [
        sys.executable,
        str(script),
        "--input",
        str(input_dir.resolve()),
        "--bmodel",
        str(bmodel.resolve()),
        "--dev_id",
        str(EVAL_DEV_ID),
        "--conf_thresh",
        str(EVAL_CONF_THRES),
        "--nms_thresh",
        "0.7",
    ]
    timeout_sec = int(os.getenv("EVAL_TIMEOUT_SEC", "7200"))
    try:
        if sys.version_info >= (3, 7):
            proc = subprocess.run(
                cmd,
                cwd=str(demo_root),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        else:
            proc = subprocess.run(
                cmd,
                cwd=str(demo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
                timeout=timeout_sec,
            )
    except subprocess.TimeoutExpired:
        return False, "yolov8_bmcv 超时", None
    log = (proc.stdout or "") + "\n" + (proc.stderr or "")
    if proc.returncode != 0:
        return False, log[-2000:], None
    result_json = _find_bmcv_result_json(demo_root, bmodel, input_dir)
    if not result_json:
        return False, log[-2000:] + "\n未找到 bmcv_python_result.json", None
    return True, log[-800:], result_json


def _run_yolov8_bmcv(demo_root: Path, bmodel: Path, input_dir: Path) -> Tuple[bool, str, Optional[Path]]:
    script = demo_root / "python" / "yolov8_bmcv.py"
    if not script.is_file():
        return False, "缂哄皯 yolov8_bmcv.py", None
    cmd = [
        sys.executable,
        str(script),
        "--input",
        str(input_dir.resolve()),
        "--bmodel",
        str(bmodel.resolve()),
        "--dev_id",
        str(EVAL_DEV_ID),
        "--conf_thresh",
        str(EVAL_CONF_THRES),
        "--nms_thresh",
        "0.7",
    ]
    timeout_sec = int(os.getenv("EVAL_TIMEOUT_SEC", "7200"))
    with _BmcvResultsGuard(demo_root, bmodel.name) as started_at:
        try:
            if sys.version_info >= (3, 7):
                proc = subprocess.run(
                    cmd,
                    cwd=str(demo_root),
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                )
            else:
                proc = subprocess.run(
                    cmd,
                    cwd=str(demo_root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True,
                    timeout=timeout_sec,
                )
        except subprocess.TimeoutExpired:
            return False, "yolov8_bmcv 瓒呮椂", None
        log = (proc.stdout or "") + "\n" + (proc.stderr or "")
        if proc.returncode != 0:
            return False, log[-2000:], None
        result_json = _find_fresh_bmcv_result_json(
            demo_root, bmodel, started_at=started_at
        )
        if not result_json:
            return (
                False,
                log[-2000:] + "\n鏈壘鍒板綋鍓嶈繍琛岀敓鎴愮殑 bmcv_python_result.json",
                None,
            )
        return True, log[-800:], result_json


class SailYoloDetector:
    def __init__(self, bmodel_path: str, dev_id: int = 0):
        import sophon.sail as sail

        self.engine = sail.Engine(bmodel_path, dev_id, sail.IOMode.SYSIO)
        self.graph_name = self.engine.get_graph_names()[0]
        self.input_name = self.engine.get_input_names(self.graph_name)[0]
        shape = self.engine.get_input_shape(self.graph_name, self.input_name)
        self.net_h = int(shape[2])
        self.net_w = int(shape[3])

    def predict(self, image_bgr: np.ndarray):
        input_tensor, ratio, pad = preprocess_image(image_bgr, self.net_w, self.net_h)
        outputs = self.engine.process(self.graph_name, {self.input_name: input_tensor})
        raw = list(outputs.values())[0] if isinstance(outputs, dict) else outputs
        return raw, ratio, pad


def _performance_frame_runner(detector: SailYoloDetector, class_count: int):
    def run(image_path: str) -> dict:
        total_started = time.perf_counter()
        stage_started = total_started
        image = cv2.imread(str(image_path))
        decode_ms = (time.perf_counter() - stage_started) * 1000.0
        if image is None:
            raise RuntimeError("无法读取性能测试图片: {}".format(image_path))

        img_h, img_w = image.shape[:2]
        stage_started = time.perf_counter()
        input_tensor, ratio, pad = preprocess_image(
            image, detector.net_w, detector.net_h
        )
        preprocess_ms = (time.perf_counter() - stage_started) * 1000.0

        stage_started = time.perf_counter()
        outputs = detector.engine.process(
            detector.graph_name, {detector.input_name: input_tensor}
        )
        raw = list(outputs.values())[0] if isinstance(outputs, dict) else outputs
        inference_ms = (time.perf_counter() - stage_started) * 1000.0

        stage_started = time.perf_counter()
        postprocess_yolo_onnx(raw, img_h, img_w, class_count, ratio, pad)
        postprocess_ms = (time.perf_counter() - stage_started) * 1000.0
        return {
            "decode_ms": round(decode_ms, 4),
            "preprocess_ms": round(preprocess_ms, 4),
            "inference_ms": round(inference_ms, 4),
            "postprocess_ms": round(postprocess_ms, 4),
            "total_ms": round((time.perf_counter() - total_started) * 1000.0, 4),
        }

    return run


def _run_model_performance_probe(
    *, detector: SailYoloDetector, samples, class_count: int, bmodel: Path
) -> dict:
    if not EVAL_PERFORMANCE_ENABLED:
        return {
            "status": "disabled",
            "environment": collect_environment(model_path=bmodel),
        }
    frame_inputs = [
        str(sample.image_path)
        for sample in samples
        if getattr(sample, "image_path", None)
        and Path(str(sample.image_path)).is_file()
    ]
    try:
        payload = run_performance_probe(
            frame_runner=_performance_frame_runner(detector, class_count),
            frame_inputs=frame_inputs,
            output_dir=Path(EVAL_SAVE_DIR) / "performance",
            model_path=bmodel,
            warmup_count=EVAL_PERFORMANCE_WARMUP_COUNT,
            repeat_count=EVAL_PERFORMANCE_REPEAT_COUNT,
            resource_interval_seconds=EVAL_RESOURCE_INTERVAL_SECONDS,
            stability_seconds=EVAL_STABILITY_SECONDS,
        )
        artifacts = payload.get("performance_artifacts")
        if isinstance(artifacts, dict):
            payload["performance_artifacts"] = {
                key: str(Path("performance") / str(value))
                for key, value in artifacts.items()
            }
        return payload
    except Exception as exc:
        return {
            "status": "unavailable",
            "error": str(exc)[:1000],
            "environment": collect_environment(model_path=bmodel),
        }


def _evaluate_loop(
    *,
    samples,
    class_names: List[str],
    class_count: int,
    backend: str,
    bmodel: Path,
    pred_by_basename: Optional[Dict[str, List[Detection]]] = None,
    detector: Optional[SailYoloDetector] = None,
) -> Tuple[dict, int, float]:
    global_per_class = {cid: _empty_stats() for cid in range(class_count)}
    scene_accum: dict = {}
    size_accum = {b[0]: {cid: _empty_stats() for cid in range(class_count)} for b in SIZE_BINS}
    label_counts = np.zeros(class_count, dtype=int)
    inference_seconds = 0.0
    predictions_path = os.path.join(EVAL_SAVE_DIR, "predictions.jsonl")
    predictions_file = open(predictions_path, "w", encoding="utf-8") if DUMP_PREDICTIONS else None
    evaluated = 0
    threshold_samples = []
    visualization_rows = []
    visualization_dir = Path(EVAL_SAVE_DIR) / "images"
    start_time = time.perf_counter()

    for sample in samples:
        image = cv2.imread(sample.image_path)
        if image is None:
            continue
        img_h, img_w = image.shape[:2]
        gt_boxes = load_yolo_labels(
            sample.label_path,
            img_w,
            img_h,
            class_names=class_names,
            xml_label_path=getattr(sample, "xml_label_path", None),
        )
        for gt in gt_boxes:
            if 0 <= gt.class_id < class_count:
                label_counts[gt.class_id] += 1

        basename = os.path.basename(sample.image_path)
        if pred_by_basename is not None:
            predictions = list(pred_by_basename.get(basename, []))
        else:
            t0 = time.perf_counter()
            raw_out, ratio, pad = detector.predict(image)
            inference_seconds += time.perf_counter() - t0
            pred_boxes = postprocess_yolo_onnx(raw_out, img_h, img_w, class_count, ratio, pad)
            predictions = [
                Detection(b.xmin, b.ymin, b.xmax, b.ymax, b.class_id, b.score) for b in pred_boxes
            ]

        if predictions_file:
            predictions_file.write(
                json.dumps(
                    {"image": sample.image_path, "boxes": [{"class_id": p.class_id, "score": p.score, "x1": p.x1, "y1": p.y1, "x2": p.x2, "y2": p.y2} for p in predictions]},
                    ensure_ascii=False,
                )
                + "\n"
            )

        grouped = match_detections_grouped(
            predictions,
            gt_boxes,
            class_count,
            iou_threshold=EVAL_IOU_THRES,
            scene_attrs=sample.scene_attrs if sample.scene_attrs else None,
            img_w=img_w,
            img_h=img_h,
        )
        image_tp = sum(int(stats["tp"]) for stats in grouped["per_class"].values())
        image_fp = sum(int(stats["fp"]) for stats in grouped["per_class"].values())
        image_fn = sum(int(stats["fn"]) for stats in grouped["per_class"].values())
        annotation_exists = Path(str(getattr(sample, "label_path", "") or "")).is_file() or Path(
            str(getattr(sample, "xml_label_path", "") or "")
        ).is_file()
        if not gt_boxes and not predictions:
            image_status = "true_negative"
        elif image_fp and image_fn:
            image_status = "mismatch"
        elif image_fp:
            image_status = "false_positive"
        elif image_fn:
            image_status = "false_negative"
        else:
            image_status = "matched"

        rendered = _render_prediction_image(
            image,
            sample.image_path,
            predictions,
            class_names,
            visualization_dir,
        )
        visualization_rows.append(
            {
                "image": sample.image_path,
                "preview_file": rendered.name if rendered is not None else None,
                "detection_state": "detected" if predictions else "no_detection",
                "detection_count": len(predictions),
                "annotation_state": (
                    "positive"
                    if gt_boxes
                    else ("negative" if annotation_exists else "unlabeled")
                ),
                "gt_count": len(gt_boxes),
                "tp": image_tp,
                "fp": image_fp,
                "fn": image_fn,
                "status": image_status,
                "detections": [
                    {
                        "class_id": int(item.class_id),
                        "class_name": (
                            str(class_names[int(item.class_id)])
                            if 0 <= int(item.class_id) < len(class_names)
                            else "class_{}".format(int(item.class_id))
                        ),
                        "score": float(item.score),
                        "bbox_xyxy": [
                            float(item.x1),
                            float(item.y1),
                            float(item.x2),
                            float(item.y2),
                        ],
                    }
                    for item in predictions
                ],
            }
        )

        threshold_samples.append((list(predictions), list(gt_boxes)))
        for cid in range(class_count):
            for k in ("tp", "fp", "fn"):
                global_per_class[cid][k] += grouped["per_class"][cid][k]
            global_per_class[cid]["scores"].extend(grouped["per_class"][cid]["scores"])
            global_per_class[cid]["matches"].extend(grouped["per_class"][cid]["matches"])
        for field_key, val_dict in grouped["scene"].items():
            if field_key not in scene_accum:
                scene_accum[field_key] = {}
            for val, pc in val_dict.items():
                if val not in scene_accum[field_key]:
                    scene_accum[field_key][val] = {cid: _empty_stats() for cid in range(class_count)}
                for cid in range(class_count):
                    for k in ("tp", "fp", "fn"):
                        scene_accum[field_key][val][cid][k] += pc[cid][k]
        for bin_name, pc in grouped["size"].items():
            for cid in range(class_count):
                for k in ("tp", "fp", "fn"):
                    size_accum[bin_name][cid][k] += pc[cid][k]
        evaluated += 1

    if predictions_file:
        predictions_file.close()
    with open(
        os.path.join(EVAL_SAVE_DIR, "visualization_summary.json"),
        "w",
        encoding="utf-8",
    ) as fp:
        json.dump(
            {
                "image_count": len(visualization_rows),
                "with_boxes": sum(
                    1 for row in visualization_rows if row["detection_count"] > 0
                ),
                "total_boxes": sum(
                    int(row["detection_count"]) for row in visualization_rows
                ),
                "positive_images": sum(
                    1 for row in visualization_rows if row["annotation_state"] == "positive"
                ),
                "negative_images": sum(
                    1 for row in visualization_rows if row["annotation_state"] == "negative"
                ),
                "unlabeled_images": sum(
                    1 for row in visualization_rows if row["annotation_state"] == "unlabeled"
                ),
                "matched_images": sum(
                    1 for row in visualization_rows if row["status"] == "matched"
                ),
                "true_negative_images": sum(
                    1 for row in visualization_rows if row["status"] == "true_negative"
                ),
                "false_positive_images": sum(
                    1 for row in visualization_rows if int(row["fp"]) > 0
                ),
                "false_negative_images": sum(
                    1 for row in visualization_rows if int(row["fn"]) > 0
                ),
                "tp": sum(int(row["tp"]) for row in visualization_rows),
                "fp": sum(int(row["fp"]) for row in visualization_rows),
                "fn": sum(int(row["fn"]) for row in visualization_rows),
                "images": visualization_rows,
            },
            fp,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )

    total_time = time.perf_counter() - start_time
    summary = summarize_metrics(
        class_names=class_names,
        per_class=global_per_class,
        label_counts=label_counts,
        total_images=evaluated,
        total_time_seconds=total_time,
        cpu_memory_mb=get_cpu_memory_mb(),
        gpu_memory_mb=None,
        evaluation_samples=threshold_samples,
    )
    if inference_seconds > 0:
        summary["extra_speed_metrics"] = {
            "平均推理耗时(ms/图)": (inference_seconds / max(evaluated, 1)) * 1000,
        }
    scene_summary = summarize_scene_metrics(scene_accum, class_names) if scene_accum else {}
    size_summary = summarize_size_metrics(size_accum, class_names)
    summary["class_names"] = class_names
    summary["business_threshold_analysis"] = build_business_threshold_analysis(
        threshold_samples,
        class_names,
        iou_threshold=0.5,
        min_conf=0.21,
        max_conf=0.99,
        step=0.01,
    )
    return summary, evaluated, scene_summary, size_summary


def _write_outputs(
    summary: dict,
    evaluated: int,
    scene_summary: dict,
    size_summary: dict,
    *,
    backend: str,
    bmodel: Path,
    performance: Optional[dict] = None,
) -> str:
    curve_path = None
    scene_img_paths = {}
    size_img_path = None
    plot_warnings = []
    try:
        curve_path = plot_f1_summary_curve(EVAL_SAVE_DIR, summary.get("class_names", []), summary["per_class_rows"])
    except Exception as plot_exc:
        warning = {
            "code": "PLOT_DEPENDENCY_MISSING" if isinstance(plot_exc, ModuleNotFoundError) else "PLOT_GENERATION_FAILED",
            "message": "曲线图未生成：{}（不影响推理和精度计算）".format(plot_exc),
        }
        plot_warnings.append(warning)
        print("WARN: {}".format(warning["message"]), file=sys.stderr)
    try:
        scene_img_paths = plot_scene_metrics(EVAL_SAVE_DIR, scene_summary) if scene_summary else {}
    except Exception as plot_exc:
        warning = {"code": "SCENE_PLOT_GENERATION_FAILED", "message": "场景分析图未生成：{}".format(plot_exc)}
        plot_warnings.append(warning)
        print("WARN: {}".format(warning["message"]), file=sys.stderr)
    try:
        size_img_path = plot_size_metrics(EVAL_SAVE_DIR, size_summary)
    except Exception as plot_exc:
        warning = {"code": "SIZE_PLOT_GENERATION_FAILED", "message": "目标尺寸分析图未生成：{}".format(plot_exc)}
        plot_warnings.append(warning)
        print("WARN: {}".format(warning["message"]), file=sys.stderr)

    report_path = write_markdown_report(
        EVAL_SAVE_DIR,
        summary,
        curve_image_name=os.path.basename(curve_path) if curve_path else None,
        scene_summary=scene_summary,
        scene_image_paths=scene_img_paths,
        size_summary=size_summary,
        size_image_path=size_img_path,
        thresholds={
            "模型路径": str(bmodel),
            "数据集": EVAL_DATA_PATH,
            "置信度阈值": EVAL_CONF_THRES,
            "NMS IoU 阈值": NMS_THRESH,
            "匹配 IoU 阈值": EVAL_IOU_THRES,
            "推理后端": backend,
        },
    )
    eval_summary = {
        "kind": "bmodel",
        "model_tag": EVAL_MODEL_TAG or "int8",
        "model_filename": bmodel.name,
        "backend": backend,
        "bmodel": str(bmodel),
        "images": evaluated,
        "map50": summary.get("map50"),
        "map50_95": summary.get("map5095"),
        "map50_95_status": summary.get("map5095_status"),
        "metric_validity": summary.get("metric_validity"),
        "precision": summary.get("overall_precision"),
        "recall": summary.get("overall_recall"),
        "f1": summary.get("overall_f1"),
        "speed_avg_ms": summary.get("speed_avg_ms"),
        "cpu_memory_mb": summary.get("cpu_memory_mb"),
        "class_metrics": summary.get("per_class_rows"),
        "business_threshold_analysis": summary.get("business_threshold_analysis"),
        "report_md": os.path.basename(report_path),
    }
    if plot_warnings:
        eval_summary["warnings"] = plot_warnings
        eval_summary["plot_artifacts"] = {
            "status": "partial",
            "generated": bool(curve_path or scene_img_paths or size_img_path),
        }
    if isinstance(performance, dict):
        eval_summary["performance_probe_status"] = performance.get("status")
        if performance.get("error"):
            eval_summary["performance_probe_error"] = performance.get("error")
        for key in (
            "runtime_metrics",
            "resource_usage",
            "environment",
            "stability",
            "performance_artifacts",
        ):
            if performance.get(key) is not None:
                eval_summary[key] = performance.get(key)
    with open(os.path.join(EVAL_SAVE_DIR, "eval_summary.json"), "w", encoding="utf-8") as f:
        json.dump(
            eval_summary,
            f,
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        )
    print("图片: {} | mAP50: {:.4f}".format(evaluated, summary.get("map50", 0)))
    print("报告: {}".format(report_path))
    return report_path


def main() -> int:
    os.makedirs(EVAL_SAVE_DIR, exist_ok=True)
    model_dir = Path(EVAL_MODEL_PATH)
    bmodel = _resolve_requested_bmodel(model_dir)
    if not bmodel or not bmodel.is_file():
        print("未找到 int8 bmodel: {}".format(model_dir), file=sys.stderr)
        return 2

    data_class_names, samples = load_dataset_from_yaml(EVAL_DATA_PATH)
    class_names = _env_class_names(data_class_names) or data_class_names
    class_count = len(class_names)
    if not class_count:
        print("无类别名", file=sys.stderr)
        return 5
    if EVAL_XML_DIR and os.path.isdir(EVAL_XML_DIR):
        attach_scene_attrs(samples, EVAL_XML_DIR)

    input_dir = Path(EVAL_DATA_PATH)
    demo_root = Path(SOPHON_DEMO_ROOT) if SOPHON_DEMO_ROOT else None
    if demo_root and demo_root.is_dir():
        ok, log, result_json = _run_yolov8_bmcv(demo_root, bmodel, input_dir)
        if ok and result_json:
            pred_map = _load_bmcv_predictions(result_json, class_count)
            summary, evaluated, scene_summary, size_summary = _evaluate_loop(
                samples=samples,
                class_names=class_names,
                class_count=class_count,
                backend="yolov8_bmcv",
                bmodel=bmodel,
                pred_by_basename=pred_map,
            )
            try:
                performance_detector = SailYoloDetector(
                    str(bmodel.resolve()), dev_id=EVAL_DEV_ID
                )
                performance = _run_model_performance_probe(
                    detector=performance_detector,
                    samples=samples,
                    class_count=class_count,
                    bmodel=bmodel,
                )
            except Exception as exc:
                performance = {
                    "status": "unavailable",
                    "error": str(exc)[:1000],
                    "environment": collect_environment(model_path=bmodel),
                }
            _write_outputs(
                summary,
                evaluated,
                scene_summary,
                size_summary,
                backend="yolov8_bmcv",
                bmodel=bmodel,
                performance=performance,
            )
            return 0
        print("WARN: yolov8_bmcv 失败，回退 sail: {}".format(log[-400:]), file=sys.stderr)

    try:
        detector = SailYoloDetector(str(bmodel.resolve()), dev_id=EVAL_DEV_ID)
    except ImportError:
        print("sophon.sail 未安装", file=sys.stderr)
        return 3
    except Exception as exc:
        print("加载 bmodel 失败: {}".format(exc), file=sys.stderr)
        return 4

    summary, evaluated, scene_summary, size_summary = _evaluate_loop(
        samples=samples,
        class_names=class_names,
        class_count=class_count,
        backend="sophon.sail",
        bmodel=bmodel,
        detector=detector,
    )
    performance = _run_model_performance_probe(
        detector=detector,
        samples=samples,
        class_count=class_count,
        bmodel=bmodel,
    )
    _write_outputs(
        summary,
        evaluated,
        scene_summary,
        size_summary,
        backend="sophon.sail (BM1688)",
        bmodel=bmodel,
        performance=performance,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
