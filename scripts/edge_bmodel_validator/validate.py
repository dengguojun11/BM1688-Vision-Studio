#!/usr/bin/env python3
"""Run a BModel against images on a BM1688 edge device.

The default workflow intentionally needs no arguments: keep one .bmodel in
model/, put images in images/, and execute ``bash run.sh``.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
DEFAULT_CONFIG = {
    "conf_threshold": 0.25,
    "nms_threshold": 0.70,
    "plate_iou_threshold": 0.50,
    "device_id": 0,
    "bmrt_test": True,
}
PLATE_MODEL_NAME = "plate_recognition_fp16_1b.bmodel"
PLATE_CONTRACT_NAME = "plate_recognition_contract.json"
PLATE_DETECTION_MODEL_NAME = "plate_detection_fp16_1b.bmodel"
PLATE_DETECTION_CONTRACT_NAME = "plate_detection_contract.json"
PLATE_REQUIRED_NETWORKS = {
    "plate_pose",
    "plate_ocr",
    "plate_color",
    "plate_type",
}
EDGE_ROOT = Path("/data/labelu_edge")
DEMO_CANDIDATES = (
    "/data/labelu_edge/runtime/sophon-demo/sample/YOLOv8_plus_det",
)


class ValidationError(RuntimeError):
    pass


def _require_edge_path(path):
    candidate = Path(path).expanduser().resolve()
    try:
        candidate.relative_to(EDGE_ROOT.resolve())
    except ValueError:
        raise ValidationError(
            "Path must be under /data/labelu_edge: {}".format(path)
        )
    return candidate


def _load_config(path):
    config = dict(DEFAULT_CONFIG)
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValidationError("Invalid config file {}: {}".format(path, exc))
        if not isinstance(loaded, dict):
            raise ValidationError(
                "Config file must contain a JSON object: {}".format(path)
            )
        config.update(loaded)
    return config


def _resolve_single_model(model_path):
    model_path = model_path.expanduser().resolve()
    if model_path.is_file():
        if model_path.suffix.lower() != ".bmodel":
            raise ValidationError(
                "Model file must end with .bmodel: {}".format(model_path)
            )
        return model_path
    if not model_path.is_dir():
        raise ValidationError("Model directory does not exist: {}".format(model_path))
    models = sorted(
        p for p in model_path.iterdir() if p.is_file() and p.suffix.lower() == ".bmodel"
    )
    if not models:
        raise ValidationError("No .bmodel file found in {}".format(model_path))
    if len(models) > 1:
        names = ", ".join(p.name for p in models)
        raise ValidationError("Keep exactly one .bmodel in model/: {}".format(names))
    return models[0].resolve()


def _load_json_object(path, label):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError("Invalid {} {}: {}".format(label, path, exc))
    if not isinstance(payload, dict):
        raise ValidationError("{} must contain a JSON object: {}".format(label, path))
    return payload


def _resolve_model_bundle(model_path):
    model = _resolve_single_model(model_path)
    detection_contract_path = model.parent / PLATE_DETECTION_CONTRACT_NAME
    contract_path = model.parent / PLATE_CONTRACT_NAME
    if detection_contract_path.is_file():
        contract = _load_json_object(
            detection_contract_path, "plate-detection contract"
        )
        if contract.get("format") != "labelu_plate_detection_bm1688_v1":
            raise ValidationError("Unsupported plate-detection contract format")
        if contract.get("job_type") != "plate_detection":
            raise ValidationError("Plate-detection contract has an invalid job_type")
        network = contract.get("network")
        if not isinstance(network, dict) or network.get("name") != "plate_detection":
            raise ValidationError("Plate-detection contract has no valid network")
        declared_model = str(contract.get("bmodel") or "").strip()
        if declared_model and declared_model != model.name:
            raise ValidationError(
                "Contract expects {}, but model/ contains {}".format(
                    declared_model, model.name
                )
            )
        return {
            "model_type": "plate_detection",
            "model": model,
            "contract": detection_contract_path.resolve(),
        }
    is_plate_filename = model.name == PLATE_MODEL_NAME
    is_detection_filename = model.name == PLATE_DETECTION_MODEL_NAME

    if not contract_path.is_file():
        if is_plate_filename:
            raise ValidationError(
                "Plate-recognition model requires {} in model/".format(
                    PLATE_CONTRACT_NAME
                )
            )
        if is_detection_filename:
            raise ValidationError(
                "Plate-detection model requires {} in model/".format(
                    PLATE_DETECTION_CONTRACT_NAME
                )
            )
        return {"model_type": "yolo_detection", "model": model, "contract": None}

    contract = _load_json_object(contract_path, "plate-recognition contract")
    if contract.get("format") != "labelu_plate_recognition_bm1688_v1":
        raise ValidationError("Unsupported plate-recognition contract format")
    if contract.get("job_type") != "plate_recognition":
        raise ValidationError("Plate-recognition contract has an invalid job_type")
    networks = contract.get("networks")
    if not isinstance(networks, dict):
        raise ValidationError("Plate-recognition contract has no networks object")
    missing = sorted(PLATE_REQUIRED_NETWORKS - set(networks))
    if missing:
        raise ValidationError(
            "Plate-recognition contract is missing networks: {}".format(
                ", ".join(missing)
            )
        )
    declared_model = str(contract.get("combined_bmodel") or "").strip()
    if declared_model and declared_model != model.name:
        raise ValidationError(
            "Contract expects {}, but model/ contains {}".format(
                declared_model, model.name
            )
        )
    return {
        "model_type": "plate_recognition",
        "model": model,
        "contract": contract_path.resolve(),
    }


def _discover_images(images_dir):
    images_dir = images_dir.expanduser().resolve()
    if not images_dir.is_dir():
        raise ValidationError("Image directory does not exist: {}".format(images_dir))
    images = sorted(
        p
        for p in images_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        raise ValidationError("No supported images found in {}".format(images_dir))
    return images


def _resolve_demo_root(explicit=""):
    candidates = []
    if explicit:
        candidates.append(explicit)
    env_root = os.environ.get("SOPHON_DEMO_ROOT", "").strip()
    if env_root:
        candidates.append(env_root)
    candidates.extend(DEMO_CANDIDATES)

    tried = []
    seen = set()
    for raw in candidates:
        try:
            path = _require_edge_path(raw)
        except ValidationError:
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        tried.append(key)
        if (path / "python" / "yolov8_bmcv.py").is_file():
            return path.resolve()
    raise ValidationError(
        "Sophon Demo YOLOv8_plus_det was not found. Tried: {}".format(", ".join(tried))
    )


def _read_class_names(path):
    if not path.is_file():
        return []
    names = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        name = raw.strip()
        if name and not name.startswith("#"):
            names.append(name)
    return names


def _validate_threshold(value, name):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValidationError("{} must be a number".format(name))
    if number < 0.0 or number > 1.0:
        raise ValidationError("{} must be between 0 and 1".format(name))
    return number


def _is_ascii(text):
    try:
        text.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _snapshot_files(root):
    if not root.is_dir():
        return {}
    snapshot = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[str(path.resolve())] = (
            getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)),
            stat.st_size,
        )
    return snapshot


def _changed_files(root, before):
    changed = []
    if not root.is_dir():
        return changed
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        current = (getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)), stat.st_size)
        if before.get(str(path.resolve())) != current:
            changed.append(path)
    return sorted(changed, key=lambda p: p.stat().st_mtime, reverse=True)


def _run_command(command, cwd, log_path):
    started = time.time()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write("command: {}\n".format(" ".join(str(v) for v in command)))
        log_file.flush()
        process = subprocess.Popen(
            [str(v) for v in command],
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            sys.stdout.write(line)
            sys.stdout.flush()
            log_file.write(line)
        process.stdout.close()
        return_code = process.wait()
    return {
        "return_code": int(return_code),
        "elapsed_seconds": round(time.time() - started, 3),
    }


def _load_result_rows(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationError("Cannot read inference result {}: {}".format(path, exc))
    if not isinstance(data, list):
        raise ValidationError("Inference result must be a JSON list: {}".format(path))
    return data


def _select_result_json(changed, model):
    candidates = [p for p in changed if p.suffix.lower() == ".json"]
    if not candidates:
        return None
    model_tokens = (model.name.lower(), model.stem.lower())
    matched = [
        p for p in candidates if any(token in p.name.lower() for token in model_tokens)
    ]
    return (matched or candidates)[0]


def _normalize_results(rows, class_names):
    normalized = []
    class_counts = {}
    with_boxes = 0
    total_boxes = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        boxes = row.get("bboxes") or []
        detections = []
        for box in boxes:
            if not isinstance(box, dict):
                continue
            bbox = box.get("bbox") or box.get("box") or []
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            class_id = int(box.get("category_id", box.get("class_id", -1)))
            class_name = (
                class_names[class_id]
                if 0 <= class_id < len(class_names)
                else "class_{}".format(class_id)
            )
            score = float(box.get("score", box.get("confidence", 0.0)))
            detection = {
                "class_id": class_id,
                "class_name": class_name,
                "score": score,
                "bbox_xywh": [float(v) for v in bbox[:4]],
            }
            detections.append(detection)
            total_boxes += 1
            class_counts[class_name] = class_counts.get(class_name, 0) + 1
        if detections:
            with_boxes += 1
        normalized.append(
            {
                "image": str(
                    row.get("image_name")
                    or row.get("filename")
                    or row.get("image_path")
                    or ""
                ),
                "detections": detections,
            }
        )
    return normalized, {
        "result_image_count": len(normalized),
        "images_with_detections": with_boxes,
        "total_detections": total_boxes,
        "class_counts": class_counts,
    }


def _normalize_plate_results(payload):
    rows = payload.get("images")
    if not isinstance(rows, list):
        raise ValidationError("Plate-recognition result has no images list")

    normalized = []
    plate_type_counts = {}
    color_counts = {}
    plate_numbers = []
    with_detections = 0
    total_detections = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        detections = []
        for item in row.get("detections") or []:
            if not isinstance(item, dict):
                continue
            plate_number = str(item.get("plate_number") or "")
            plate_type = str(item.get("plate_type") or "unknown")
            color = str(item.get("color") or "unknown")
            detection = {
                "label": str(item.get("label") or ""),
                "plate_number": plate_number,
                "plate_number_score": item.get("plate_number_score"),
                "plate_type": plate_type,
                "plate_type_score": item.get("plate_type_score"),
                "color": color,
                "color_score": item.get("color_score"),
                "score": item.get("score"),
                "bbox_xyxy": item.get("box"),
                "corners": item.get("corners"),
                "timing_ms": item.get("timing_ms"),
            }
            detections.append(detection)
            total_detections += 1
            plate_type_counts[plate_type] = plate_type_counts.get(plate_type, 0) + 1
            color_counts[color] = color_counts.get(color, 0) + 1
            if plate_number:
                plate_numbers.append(plate_number)
        if detections:
            with_detections += 1
        normalized.append(
            {
                "image": str(row.get("image") or ""),
                "detections": detections,
                "rejected_predictions": row.get("rejected_predictions") or [],
            }
        )
    return normalized, {
        "result_image_count": len(normalized),
        "images_with_detections": with_detections,
        "total_detections": total_detections,
        "plate_numbers": plate_numbers,
        "plate_type_counts": plate_type_counts,
        "color_counts": color_counts,
    }


def _resolve_plate_runner(base_dir, model_type="plate_recognition"):
    filename = (
        "plate_detection_bm1688_infer.py"
        if model_type == "plate_detection"
        else "plate_recognition_bm1688_infer.py"
    )
    bundled = base_dir / filename
    if bundled.is_file():
        return bundled.resolve()
    try:
        repo_root = base_dir.parents[1]
    except IndexError:
        repo_root = None
    if repo_root is not None:
        repository_script = repo_root / "labelu" / "scripts" / filename
        if repository_script.is_file():
            return repository_script.resolve()
    raise ValidationError("Bundled {} inference script is missing".format(model_type))


def _render_images(images, normalized, output_dir):
    try:
        import cv2
    except ImportError:
        return {"status": "skipped", "reason": "cv2 is unavailable", "count": 0}

    output_dir.mkdir(parents=True, exist_ok=True)
    image_by_name = {path.name: path for path in images}
    count = 0
    for row in normalized:
        source = image_by_name.get(Path(row.get("image") or "").name)
        if source is None:
            continue
        image = cv2.imread(str(source))
        if image is None:
            continue
        for detection in row.get("detections") or []:
            x, y, width, height = detection["bbox_xywh"]
            x1, y1 = max(0, int(round(x))), max(0, int(round(y)))
            x2, y2 = max(x1, int(round(x + width))), max(y1, int(round(y + height)))
            class_id = int(detection["class_id"])
            color = (
                int((37 * (class_id + 3)) % 205 + 40),
                int((67 * (class_id + 5)) % 205 + 40),
                int((97 * (class_id + 7)) % 205 + 40),
            )
            cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
            label = "{} {:.2f}".format(detection["class_name"], detection["score"])
            if not _is_ascii(label):
                label = "class_{} {:.2f}".format(class_id, detection["score"])
            cv2.putText(
                image,
                label,
                (x1, max(18, y1 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
            )
        destination = output_dir / source.name
        if cv2.imwrite(str(destination), image):
            count += 1
    return {"status": "ok", "count": count}


def _prepare_output(output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "images",
        "summary.json",
        "detections.json",
        "raw_detections.json",
        "bmrt_test.log",
        "inference.log",
    ):
        path = output_dir / name
        if path.is_dir():
            shutil.rmtree(str(path))
        elif path.exists():
            path.unlink()


def _write_summary(output_dir, summary):
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _run_bmrt_test(model, device_id, cwd, output_dir, enabled):
    bmrt_path = shutil.which("bmrt_test")
    if not enabled or not bmrt_path:
        return {
            "status": "skipped",
            "reason": "disabled" if not enabled else "bmrt_test command not found",
        }
    print("\n[1/2] Loading BModel with bmrt_test ...", flush=True)
    result = _run_command(
        [bmrt_path, "--bmodel", str(model), "--devid", str(device_id)],
        cwd,
        output_dir / "bmrt_test.log",
    )
    if result["return_code"] != 0:
        raise ValidationError(
            "bmrt_test failed; see {}".format(output_dir / "bmrt_test.log")
        )
    return result


def _run_yolo_inference(
    model,
    images_dir,
    images,
    output_dir,
    demo_root,
    class_names,
    confidence,
    nms_threshold,
    device_id,
):
    results_root = demo_root / "results"
    before = _snapshot_files(results_root)
    command = [
        sys.executable,
        "python/yolov8_bmcv.py",
        "--input",
        str(images_dir),
        "--bmodel",
        str(model),
        "--dev_id",
        str(device_id),
        "--conf_thresh",
        str(confidence),
        "--nms_thresh",
        str(nms_threshold),
    ]
    print("\n[2/2] Running YOLO image inference ...", flush=True)
    inference = _run_command(command, demo_root, output_dir / "inference.log")
    if inference["return_code"] != 0:
        raise ValidationError(
            "Image inference failed; see {}".format(output_dir / "inference.log")
        )

    changed = _changed_files(results_root, before)
    result_json = _select_result_json(changed, model)
    if result_json is None:
        raise ValidationError("Inference finished but no fresh result JSON was found")
    rows = _load_result_rows(result_json)
    normalized, detection_summary = _normalize_results(rows, class_names)
    shutil.copy2(str(result_json), str(output_dir / "raw_detections.json"))
    (output_dir / "detections.json").write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    render = _render_images(images, normalized, output_dir / "images")
    return {
        "inference": inference,
        "detection_summary": detection_summary,
        "result_json_source": str(result_json),
        "rendered_images": render,
    }


def _run_plate_inference(
    base_dir,
    model,
    contract,
    images_dir,
    output_dir,
    confidence,
    iou_threshold,
    device_id,
    model_type="plate_recognition",
):
    runner = _resolve_plate_runner(base_dir, model_type=model_type)
    rendered_dir = output_dir / "images"
    command = [
        sys.executable,
        str(runner),
        "--model",
        str(model),
        "--contract",
        str(contract),
        "--input",
        str(images_dir),
        "--output",
        str(rendered_dir),
        "--device-id",
        str(device_id),
        "--confidence",
        str(confidence),
        "--iou",
        str(iou_threshold),
    ]
    print("\n[2/2] Running {} inference ...".format(model_type), flush=True)
    inference = _run_command(command, model.parent, output_dir / "inference.log")
    if inference["return_code"] != 0:
        raise ValidationError(
            "{} inference failed; see {}".format(
                model_type, output_dir / "inference.log"
            )
        )
    result_json = rendered_dir / "results.json"
    if not result_json.is_file():
        raise ValidationError(
            "{} inference returned no results.json".format(model_type)
        )
    payload = _load_json_object(result_json, "{} result".format(model_type))
    normalized, detection_summary = _normalize_plate_results(payload)
    shutil.copy2(str(result_json), str(output_dir / "raw_detections.json"))
    (output_dir / "detections.json").write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rendered_count = sum(
        1
        for path in rendered_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    return {
        "inference": inference,
        "detection_summary": detection_summary,
        "result_json_source": str(result_json),
        "rendered_images": {"status": "ok", "count": rendered_count},
        "plate_runner": str(runner),
    }


def _build_parser(base_dir):
    parser = argparse.ArgumentParser(
        description="Validate the single BModel in model/ against images in images/."
    )
    parser.add_argument("--model", default=str(base_dir / "model"))
    parser.add_argument("--images", default=str(base_dir / "images"))
    parser.add_argument("--output", default=str(base_dir / "output" / "current"))
    parser.add_argument("--config", default=str(base_dir / "config.json"))
    parser.add_argument("--classes", default=str(base_dir / "classes.txt"))
    parser.add_argument("--sophon-demo-root", default="")
    parser.add_argument("--conf", type=float, default=None)
    parser.add_argument("--nms", type=float, default=None)
    parser.add_argument("--iou", type=float, default=None)
    parser.add_argument("--dev-id", type=int, default=None)
    parser.add_argument("--skip-bmrt-test", action="store_true")
    return parser


def main(argv=None):
    base_dir = Path(__file__).resolve().parent
    args = _build_parser(base_dir).parse_args(argv)
    output_dir = _require_edge_path(args.output)
    _prepare_output(output_dir)
    summary = {"status": "failed", "runtime_ok": False, "objects_detected": False}

    try:
        config = _load_config(_require_edge_path(args.config))
        model_bundle = _resolve_model_bundle(_require_edge_path(args.model))
        model = model_bundle["model"]
        model_type = model_bundle["model_type"]
        contract = model_bundle["contract"]
        images_dir = _require_edge_path(args.images)
        images = _discover_images(images_dir)
        class_names = _read_class_names(_require_edge_path(args.classes))
        conf = _validate_threshold(
            args.conf if args.conf is not None else config.get("conf_threshold"),
            "conf_threshold",
        )
        nms = _validate_threshold(
            args.nms if args.nms is not None else config.get("nms_threshold"),
            "nms_threshold",
        )
        iou = _validate_threshold(
            (
                args.iou
                if args.iou is not None
                else config.get("plate_iou_threshold", 0.5)
            ),
            "plate_iou_threshold",
        )
        dev_id = int(
            args.dev_id if args.dev_id is not None else config.get("device_id", 0)
        )

        demo_root = None
        if model_type == "yolo_detection":
            demo_root = _resolve_demo_root(args.sophon_demo_root)

        summary.update(
            {
                "model_type": model_type,
                "model": str(model),
                "model_filename": model.name,
                "contract": str(contract) if contract else None,
                "input_directory": str(images_dir),
                "input_image_count": len(images),
                "sophon_demo_root": str(demo_root) if demo_root else None,
                "device_id": dev_id,
                "conf_threshold": conf,
                "nms_threshold": nms,
                "plate_iou_threshold": iou,
                "class_names": class_names,
            }
        )

        run_bmrt = bool(config.get("bmrt_test", True)) and not args.skip_bmrt_test
        summary["bmrt_test"] = _run_bmrt_test(
            model,
            dev_id,
            demo_root or model.parent,
            output_dir,
            run_bmrt,
        )

        if model_type in {"plate_detection", "plate_recognition"}:
            run_result = _run_plate_inference(
                base_dir,
                model,
                contract,
                images_dir,
                output_dir,
                conf,
                iou,
                dev_id,
                model_type=model_type,
            )
        else:
            run_result = _run_yolo_inference(
                model,
                images_dir,
                images,
                output_dir,
                demo_root,
                class_names,
                conf,
                nms,
                dev_id,
            )

        detection_summary = run_result["detection_summary"]
        summary["inference"] = run_result["inference"]
        summary.update(detection_summary)
        summary.update(
            {
                "status": "completed",
                "runtime_ok": True,
                "objects_detected": detection_summary["total_detections"] > 0,
                "result_json_source": run_result["result_json_source"],
                "rendered_images": run_result["rendered_images"],
                "output_directory": str(output_dir),
            }
        )
        if run_result.get("plate_runner"):
            summary["plate_runner"] = run_result["plate_runner"]
        _write_summary(output_dir, summary)

        print("\nValidation completed.")
        print("Input images: {}".format(summary["input_image_count"]))
        print("Images with detections: {}".format(summary["images_with_detections"]))
        print("Total detections: {}".format(summary["total_detections"]))
        print("Output: {}".format(output_dir))
        if not summary["objects_detected"]:
            print(
                "NOTE: The model ran successfully, but no object exceeded "
                "the confidence threshold."
            )
        return 0
    except (ValidationError, OSError, ValueError) as exc:
        summary["error"] = str(exc)
        _write_summary(output_dir, summary)
        print("ERROR: {}".format(exc), file=sys.stderr)
        print("Summary: {}".format(output_dir / "summary.json"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
