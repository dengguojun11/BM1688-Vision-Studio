import hashlib
import json
import os
import threading
import uuid
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import status
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.user import User

SCHEMA_VERSION = "bm1688-visual-evidence-v2"
DEFAULT_PAGE_SIZE = 12
THUMBNAIL_MAX_SIDE = 960
THUMBNAIL_JPEG_QUALITY = 78
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
_ASSET_KINDS = {"original", "ground_truth", "output", "compare"}
_contract_cache: dict[tuple[int, tuple[tuple[str, int, int], ...]], dict[str, Any]] = {}
_cache_guard = threading.Lock()
_asset_guard = threading.Lock()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _file_signature(paths: list[Path]) -> tuple[tuple[str, int, int], ...]:
    signature: list[tuple[str, int, int]] = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((str(path), int(stat.st_mtime_ns), int(stat.st_size)))
    return tuple(signature)


def _summary_path(job_root: Path) -> Path | None:
    candidates = (
        job_root / "server_eval" / "results_bmodel" / "visualization_summary.json",
        job_root
        / "edge_ssh_pull"
        / "output"
        / "results_bmodel"
        / "visualization_summary.json",
    )
    return next((path for path in candidates if path.is_file()), None)


def _image_files(image_root: Path) -> list[Path]:
    if not image_root.is_dir():
        return []
    return sorted(
        path
        for path in image_root.iterdir()
        if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
    )


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _prediction_boxes(
    row: dict[str, Any], class_names: list[str]
) -> list[dict[str, Any]]:
    boxes: list[dict[str, Any]] = []
    for raw in row.get("detections") or []:
        if not isinstance(raw, dict):
            continue
        coords = raw.get("bbox_xyxy")
        if not isinstance(coords, list) or len(coords) < 4:
            continue
        class_id = int(_float_value(raw.get("class_id"), -1))
        class_name = str(raw.get("class_name") or "").strip()
        if not class_name and 0 <= class_id < len(class_names):
            class_name = str(class_names[class_id])
        boxes.append(
            {
                "class_id": class_id,
                "class_name": class_name or f"class_{class_id}",
                "score": _float_value(raw.get("score"), 0.0),
                "bbox": [_float_value(value) for value in coords[:4]],
            }
        )
    return boxes


def _voc_boxes(path: Path) -> tuple[bool, list[dict[str, Any]]]:
    if not path.is_file():
        return False, []
    boxes: list[dict[str, Any]] = []
    try:
        root = ET.parse(path).getroot()
        for obj in root.iter("object"):
            name = str(obj.findtext("name") or "").strip()
            box = obj.find("bndbox")
            if box is None:
                continue
            coords = [
                _float_value(box.findtext(key))
                for key in ("xmin", "ymin", "xmax", "ymax")
            ]
            if coords[2] <= coords[0] or coords[3] <= coords[1]:
                continue
            boxes.append({"class_name": name, "bbox": coords})
    except (OSError, ET.ParseError, ValueError):
        return True, []
    return True, boxes


def _iou(first: list[float], second: list[float]) -> float:
    x1 = max(first[0], second[0])
    y1 = max(first[1], second[1])
    x2 = min(first[2], second[2])
    y2 = min(first[3], second[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    union = first_area + second_area - intersection
    return intersection / union if union > 0 else 0.0


def _match_boxes(
    predictions: list[dict[str, Any]],
    ground_truth: list[dict[str, Any]],
    threshold: float,
) -> dict[str, Any]:
    candidates: list[tuple[float, int, int]] = []
    for pred_index, prediction in enumerate(predictions):
        for gt_index, target in enumerate(ground_truth):
            if prediction["class_name"] != target["class_name"]:
                continue
            overlap = _iou(prediction["bbox"], target["bbox"])
            if overlap >= threshold:
                candidates.append((overlap, pred_index, gt_index))
    matched_predictions: set[int] = set()
    matched_targets: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, pred_index, gt_index in sorted(candidates, reverse=True):
        if pred_index in matched_predictions or gt_index in matched_targets:
            continue
        matched_predictions.add(pred_index)
        matched_targets.add(gt_index)
        pairs.append((pred_index, gt_index))
    return {
        "tp": len(pairs),
        "fp": max(len(predictions) - len(pairs), 0),
        "fn": max(len(ground_truth) - len(pairs), 0),
        "matched_prediction_indexes": sorted(matched_predictions),
        "matched_target_indexes": sorted(matched_targets),
    }


def _item_status(*, gt_count: int, prediction_count: int, fp: int, fn: int) -> str:
    if gt_count == 0 and prediction_count == 0:
        return "true_negative"
    if fp and fn:
        return "mismatch"
    if fp:
        return "false_positive"
    if fn:
        return "false_negative"
    return "matched"


def _threshold_for_job(row: Any) -> float:
    try:
        payload = json.loads(getattr(row, "thresholds_json", None) or "{}")
    except (TypeError, ValueError):
        payload = {}
    threshold = _float_value(payload.get("match_iou_thres"), 0.5)
    return min(max(threshold, 0.0), 1.0)


def _asset_name(index: int, filename: str) -> str:
    digest = hashlib.sha256(filename.encode("utf-8", errors="ignore")).hexdigest()[:10]
    return f"{index:04d}-{digest}.jpg"


def _build_internal_contract(
    row: Any, job_root: Path, *, pack_root: Path | None = None
) -> dict[str, Any]:
    # The sample pack is immutable and may live below ``packs/<signature>``;
    # execution output remains under the stable job root so it can be appended.
    data_root = pack_root if pack_root is not None else job_root
    manifest_path = data_root / "manifest.json"
    manifest = _read_json(manifest_path)
    class_names = [str(value) for value in manifest.get("class_names") or []]
    summary_path = _summary_path(job_root) or (
        _summary_path(data_root) if data_root != job_root else None
    )
    summary = _read_json(summary_path) if summary_path else {}
    summary_rows = {
        Path(str(item.get("image") or item.get("filename") or "")).name: item
        for item in summary.get("images") or []
        if isinstance(item, dict)
    }
    image_root = data_root / "eval_data" / "images" / "test"
    label_root = data_root / "eval_data" / "labelsxml" / "test"
    prediction_root = summary_path.parent / "images" if summary_path else None
    threshold = _threshold_for_job(row)
    items: list[dict[str, Any]] = []

    for index, source_path in enumerate(_image_files(image_root)):
        filename = source_path.name
        summary_row = summary_rows.get(filename, {})
        prediction_boxes = _prediction_boxes(summary_row, class_names)
        annotation_exists, gt_boxes = _voc_boxes(label_root / f"{source_path.stem}.xml")
        matching = (
            _match_boxes(prediction_boxes, gt_boxes, threshold)
            if annotation_exists
            else {
                "tp": 0,
                "fp": 0,
                "fn": 0,
                "matched_prediction_indexes": [],
                "matched_target_indexes": [],
            }
        )
        gt_count = len(gt_boxes)
        prediction_count = len(prediction_boxes)
        evidence_status = (
            _item_status(
                gt_count=gt_count,
                prediction_count=prediction_count,
                fp=matching["fp"],
                fn=matching["fn"],
            )
            if annotation_exists
            else "unlabeled"
        )
        item = {
            "filename": filename,
            "status": evidence_status,
            "annotation_state": (
                "positive"
                if gt_count > 0
                else ("negative" if annotation_exists else "unlabeled")
            ),
            "gt_count": gt_count,
            "prediction_count": prediction_count,
            "ground_truth_labels": sorted(
                {str(box.get("class_name") or "") for box in gt_boxes if str(box.get("class_name") or "")}
            ),
            "prediction_labels": sorted(
                {str(box.get("class_name") or "") for box in prediction_boxes if str(box.get("class_name") or "")}
            ),
            "tp": matching["tp"],
            "fp": matching["fp"],
            "fn": matching["fn"],
            "output_asset": _asset_name(index, filename),
            "comparison_asset": _asset_name(index, filename),
            "original_asset": _asset_name(index, filename),
            "ground_truth_asset": _asset_name(index, filename),
            "_source_path": str(source_path),
            "_prediction_render_path": (
                str(prediction_root / filename) if prediction_root else ""
            ),
            "_ground_truth": gt_boxes,
            "_predictions": prediction_boxes,
            "_matched_prediction_indexes": matching["matched_prediction_indexes"],
            "_matched_target_indexes": matching["matched_target_indexes"],
        }
        items.append(item)

    counts = {
        "total_images": len(items),
        "positive_images": sum(
            item["annotation_state"] == "positive" for item in items
        ),
        "negative_images": sum(
            item["annotation_state"] == "negative" for item in items
        ),
        "unlabeled_images": sum(
            item["annotation_state"] == "unlabeled" for item in items
        ),
        "matched_images": sum(item["status"] == "matched" for item in items),
        "true_negative_images": sum(
            item["status"] == "true_negative" for item in items
        ),
        "false_positive_images": sum(item["fp"] > 0 for item in items),
        "false_negative_images": sum(item["fn"] > 0 for item in items),
        "mismatch_images": sum(item["status"] == "mismatch" for item in items),
        "tp": sum(item["tp"] for item in items),
        "fp": sum(item["fp"] for item in items),
        "fn": sum(item["fn"] for item in items),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "validation_id": int(row.id),
        "export_id": int(row.export_id),
        "model_variant": "int8",
        "page_size": DEFAULT_PAGE_SIZE,
        "match_iou_threshold": threshold,
        "available": bool(items and summary_path),
        "counts": counts,
        "items": items,
    }


def _load_contract(row: Any) -> dict[str, Any]:
    from labelu.internal.application.service.yolo_edge_validation_pack import (
        edge_validation_job_root,
    )

    job_root = edge_validation_job_root(
        export_id=int(row.export_id), job_id=int(row.id)
    )
    try:
        payload = json.loads(getattr(row, "result_json", None) or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    pack = payload.get("pack") if isinstance(payload, dict) else None
    persisted_pack_root = (
        str(pack.get("job_root") or "").strip()
        if isinstance(pack, dict)
        else ""
    )
    pack_root = Path(persisted_pack_root) if persisted_pack_root else None
    if pack_root is not None and not pack_root.is_dir():
        pack_root = None
    summary_path = _summary_path(job_root) or (
        _summary_path(pack_root) if pack_root is not None else None
    )
    tracked = [
        (pack_root or job_root) / "manifest.json",
        job_root / "manifest.json",
    ]
    if summary_path:
        tracked.append(summary_path)
    signature = _file_signature(tracked)
    cache_key = (int(row.id), signature)
    with _cache_guard:
        cached = _contract_cache.get(cache_key)
        if cached is not None:
            return deepcopy(cached)
    contract = _build_internal_contract(row, job_root, pack_root=pack_root)
    with _cache_guard:
        stale_keys = [key for key in _contract_cache if key[0] == int(row.id)]
        for key in stale_keys:
            _contract_cache.pop(key, None)
        _contract_cache[cache_key] = deepcopy(contract)
    return contract


def _load_job_with_access(db: Session, *, job_id: int, current_user: User):
    from labelu.internal.application.service import yolo_edge_validation

    row, _ = yolo_edge_validation._load_job_with_access(
        db, job_id=job_id, current_user=current_user
    )
    return row


def _public_contract(contract: dict[str, Any]) -> dict[str, Any]:
    public = deepcopy(contract)
    for item in public.get("items") or []:
        for key in list(item):
            if key.startswith("_"):
                item.pop(key, None)
    return public


def get_visual_evidence(
    db: Session, *, job_id: int, current_user: User
) -> dict[str, Any]:
    row = _load_job_with_access(db, job_id=job_id, current_user=current_user)
    return _public_contract(_load_contract(row))


def _fit_thumbnail(image: Image.Image) -> tuple[Image.Image, float, float]:
    original_width, original_height = image.size
    image.thumbnail((THUMBNAIL_MAX_SIDE, THUMBNAIL_MAX_SIDE), Image.Resampling.LANCZOS)
    return (
        image,
        image.width / max(original_width, 1),
        image.height / max(original_height, 1),
    )


def _draw_box(
    draw: ImageDraw.ImageDraw,
    box: list[float],
    *,
    scale_x: float,
    scale_y: float,
    color: str,
    label: str,
    width: int,
) -> None:
    coords = [
        int(round(box[0] * scale_x)),
        int(round(box[1] * scale_y)),
        int(round(box[2] * scale_x)),
        int(round(box[3] * scale_y)),
    ]
    draw.rectangle(coords, outline=color, width=width)
    font = ImageFont.load_default()
    text_box = draw.textbbox((coords[0], coords[1]), label, font=font)
    text_y = max(0, coords[1] - (text_box[3] - text_box[1]) - 4)
    draw.rectangle(
        (coords[0], text_y, coords[0] + (text_box[2] - text_box[0]) + 6, coords[1]),
        fill=color,
    )
    draw.text((coords[0] + 3, text_y + 1), label, fill="white", font=font)


def _render_output(item: dict[str, Any]) -> Image.Image:
    rendered_path = Path(item.get("_prediction_render_path") or "")
    source_path = Path(item["_source_path"])
    input_path = rendered_path if rendered_path.is_file() else source_path
    with Image.open(input_path) as opened:
        image = opened.convert("RGB")
    image, scale_x, scale_y = _fit_thumbnail(image)
    if input_path == source_path:
        draw = ImageDraw.Draw(image)
        for prediction in item.get("_predictions") or []:
            label = f"PRED {prediction['class_name']} {prediction['score']:.2f}"
            _draw_box(
                draw,
                prediction["bbox"],
                scale_x=scale_x,
                scale_y=scale_y,
                color="#1677ff",
                label=label,
                width=3,
            )
    return image


def _render_original(item: dict[str, Any]) -> Image.Image:
    source_path = Path(item["_source_path"])
    with Image.open(source_path) as opened:
        image = opened.convert("RGB")
    image, _, _ = _fit_thumbnail(image)
    return image


def _render_ground_truth(item: dict[str, Any]) -> Image.Image:
    source_path = Path(item["_source_path"])
    with Image.open(source_path) as opened:
        image = opened.convert("RGB")
    image, scale_x, scale_y = _fit_thumbnail(image)
    draw = ImageDraw.Draw(image)
    matched_targets = set(item.get("_matched_target_indexes") or [])
    for index, target in enumerate(item.get("_ground_truth") or []):
        _draw_box(
            draw,
            target["bbox"],
            scale_x=scale_x,
            scale_y=scale_y,
            color="#389e0d" if index in matched_targets else "#fa8c16",
            label=f"GT {target['class_name']}",
            width=4,
        )
    return image


def _render_comparison(item: dict[str, Any]) -> Image.Image:
    source_path = Path(item["_source_path"])
    with Image.open(source_path) as opened:
        image = opened.convert("RGB")
    image, scale_x, scale_y = _fit_thumbnail(image)
    draw = ImageDraw.Draw(image)
    matched_predictions = set(item.get("_matched_prediction_indexes") or [])
    matched_targets = set(item.get("_matched_target_indexes") or [])
    for index, target in enumerate(item.get("_ground_truth") or []):
        color = "#389e0d" if index in matched_targets else "#fa8c16"
        prefix = "GT" if index in matched_targets else "FN"
        _draw_box(
            draw,
            target["bbox"],
            scale_x=scale_x,
            scale_y=scale_y,
            color=color,
            label=f"{prefix} {target['class_name']}",
            width=4,
        )
    for index, prediction in enumerate(item.get("_predictions") or []):
        color = "#1677ff" if index in matched_predictions else "#cf1322"
        prefix = "TP" if index in matched_predictions else "FP"
        _draw_box(
            draw,
            prediction["bbox"],
            scale_x=scale_x,
            scale_y=scale_y,
            color=color,
            label=f"{prefix} {prediction['class_name']} {prediction['score']:.2f}",
            width=2,
        )
    return image


def _write_cached_asset(image: Image.Image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        image.save(
            temporary,
            format="JPEG",
            quality=THUMBNAIL_JPEG_QUALITY,
            optimize=True,
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def get_visual_evidence_asset(
    db: Session,
    *,
    job_id: int,
    kind: str,
    filename: str,
    current_user: User,
) -> Path:
    row = _load_job_with_access(db, job_id=job_id, current_user=current_user)
    kind = str(kind or "").strip().lower()
    name = Path(str(filename or "").strip()).name
    if (
        kind not in _ASSET_KINDS
        or not name
        or name != filename
        or ".." in filename
        or Path(name).suffix.lower() != ".jpg"
    ):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message="边端验证图片证据不存在",
        )
    contract = _load_contract(row)
    asset_key = {
        "original": "original_asset",
        "ground_truth": "ground_truth_asset",
        "output": "output_asset",
        "compare": "comparison_asset",
    }[kind]
    item = next(
        (
            entry
            for entry in contract.get("items") or []
            if entry.get(asset_key) == name
        ),
        None,
    )
    if item is None:
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message="边端验证图片证据不存在",
        )
    from labelu.internal.application.service.yolo_edge_validation_pack import (
        edge_validation_job_root,
    )

    job_root = edge_validation_job_root(
        export_id=int(row.export_id), job_id=int(row.id)
    )
    destination = (
        job_root / "server_eval" / "results_bmodel" / "visual_evidence" / kind / name
    )
    if destination.is_file():
        return destination
    with _asset_guard:
        if destination.is_file():
            return destination
        renderers = {
            "original": _render_original,
            "ground_truth": _render_ground_truth,
            "output": _render_output,
            "compare": _render_comparison,
        }
        image = renderers[kind](item)
        _write_cached_asset(image, destination)
    return destination
