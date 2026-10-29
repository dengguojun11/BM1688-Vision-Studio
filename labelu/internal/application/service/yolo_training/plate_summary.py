from __future__ import annotations

import json
from typing import Any, Optional

COMPONENT_DEFINITIONS = (
    ("pose", "车牌定位与四角"),
    ("ocr", "车牌号码 OCR"),
    ("color", "车牌颜色"),
    ("plate_type", "车牌类型"),
)
PLATE_DETECTION_COMPONENT_DEFINITIONS = COMPONENT_DEFINITIONS[:1]

OCR_ACCURACY_THRESHOLD = 0.90
CLASSIFIER_ACCURACY_THRESHOLD = 0.90


def _json_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sample_counts(dataset_manifest_json: Any) -> dict[str, Optional[int]]:
    manifest = _json_dict(dataset_manifest_json)
    splits = (manifest.get("composition") or {}).get("splits") or {}
    counts = {
        split: _int((splits.get(split) or {}).get("total_images"))
        for split in ("train", "val", "test")
    }
    present = [value for value in counts.values() if value is not None]
    counts["total"] = sum(present) if present else None
    return counts


def component_progress(
    component_status_json: Any,
    metrics_summary: Any,
    live_progress: Optional[dict[str, dict[str, Any]]] = None,
    component_definitions: tuple[tuple[str, str], ...] = COMPONENT_DEFINITIONS,
) -> dict[str, Any]:
    statuses = _json_dict(component_status_json)
    metric_components = _json_dict(metrics_summary).get("components") or {}
    live_progress = live_progress or {}
    completed = 0
    current_key: Optional[str] = None
    failed_key: Optional[str] = None
    items = []
    for index, (key, label) in enumerate(component_definitions, start=1):
        raw = statuses.get(key) or {}
        status = str(raw.get("status") or "").strip().upper()
        if not status and isinstance(metric_components.get(key), dict):
            status = str(metric_components[key].get("status") or "").strip().upper()
        status = status or "PENDING"
        live = live_progress.get(key) or {}
        current_epoch = _int(live.get("current_epoch"))
        target_epochs = _int(live.get("target_epochs"))
        if status == "COMPLETED" and target_epochs and current_epoch is None:
            current_epoch = target_epochs
        item_percent = (
            round((current_epoch / target_epochs) * 100)
            if current_epoch is not None and target_epochs
            else 0
        )
        if status == "COMPLETED":
            completed += 1
        elif status == "PROCESSING" and current_key is None:
            current_key = key
        elif status == "FAILED" and failed_key is None:
            failed_key = key
        items.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "order": index,
                "current_epoch": current_epoch,
                "target_epochs": target_epochs,
                "percent": max(0, min(100, item_percent)),
                "progress_snapshot": str(live.get("snapshot") or ""),
                "phase": str(live.get("phase") or ""),
                "updated_at": live.get("updated_at"),
            }
        )

    total = len(component_definitions)
    percent = int(round((completed / total) * 100)) if total else 0
    labels = {key: label for key, label in component_definitions}
    current_item = next((item for item in items if item["key"] == current_key), None)
    if failed_key:
        summary = f"{labels[failed_key]}失败（已完成 {completed}/{total}）"
    elif current_key:
        position = next(item["order"] for item in items if item["key"] == current_key)
        epoch_text = ""
        if current_item and current_item.get("target_epochs"):
            epoch_text = (
                f"，第 {current_item.get('current_epoch') or 0}/"
                f"{current_item['target_epochs']} 轮"
            )
        summary = (
            f"{labels[current_key]}训练中（第 {position}/{total} 个组件，"
            f"已完成 {completed} 个{epoch_text}）"
        )
    elif completed == total:
        summary = f"{completed}/{total} 组件完成"
    else:
        summary = f"等待训练（已完成 {completed}/{total}）"
    weighted_percent = (
        sum(
            (
                float(item.get("percent") or 0)
                if item.get("target_epochs")
                else (100.0 if item["status"] == "COMPLETED" else 0.0)
            )
            for item in items
        )
        / total
        if total
        else 0.0
    )
    return {
        "completed": completed,
        "total": total,
        "percent": max(0, min(100, int(round(weighted_percent)))),
        "current_component": current_key,
        "current_component_label": labels.get(current_key),
        "summary": summary,
        "items": items,
    }


def component_metrics(
    metrics_summary: Any,
    component_definitions: tuple[tuple[str, str], ...] = COMPONENT_DEFINITIONS,
) -> Optional[dict[str, Any]]:
    summary = _json_dict(metrics_summary)
    if summary.get("training_profile") != "plate_recognition_v1":
        return None
    components = summary.get("components") or {}
    result: dict[str, Any] = {}
    for key, label in component_definitions:
        component = components.get(key) or {}
        metrics = component.get("evaluation_metrics") or {}
        row: dict[str, Any] = {
            "key": key,
            "label": label,
            "status": str(component.get("status") or "PENDING").upper(),
            "train_sample_count": _int(component.get("train_sample_count")),
        }
        if key == "pose":
            row["metrics"] = {
                "box_precision": _float(metrics.get("metrics/precision(B)")),
                "box_recall": _float(metrics.get("metrics/recall(B)")),
                "box_map50": _float(metrics.get("metrics/mAP50(B)")),
                "box_map50_95": _float(metrics.get("metrics/mAP50-95(B)")),
                "pose_map50": _float(metrics.get("metrics/mAP50(P)")),
                "pose_map50_95": _float(metrics.get("metrics/mAP50-95(P)")),
            }
        else:
            row["metrics"] = {
                split: {
                    "accuracy": _float((metrics.get(split) or {}).get("accuracy")),
                    "correct": _int((metrics.get(split) or {}).get("correct")),
                    "total": _int((metrics.get(split) or {}).get("total")),
                }
                for split in ("train", "val", "test")
            }
        result[key] = row
    return result


def _metric_check(
    *,
    key: str,
    label: str,
    value: Optional[float],
    threshold: float,
    metric_label: str,
) -> dict[str, Any]:
    if value is None:
        return {
            "key": key,
            "label": label,
            "status": "待评估",
            "value": None,
            "threshold": threshold,
            "detail": f"未读取到 {metric_label}",
        }
    passed = value >= threshold
    return {
        "key": key,
        "label": label,
        "status": "达标" if passed else "不达标",
        "value": value,
        "threshold": threshold,
        "detail": f"{metric_label}={value:.4f}，达标线 {threshold:.2f}",
    }


def composite_assessment(
    metrics_summary: Any,
    *,
    pose_map50_threshold: float,
    pose_map50_95_threshold: float,
) -> Optional[dict[str, Any]]:
    metrics = component_metrics(metrics_summary)
    if not metrics:
        return None

    pose = metrics["pose"]["metrics"]
    checks = [
        _metric_check(
            key="pose_box_map50",
            label="Pose 车牌框 mAP50",
            value=pose.get("box_map50"),
            threshold=pose_map50_threshold,
            metric_label="框 mAP50",
        ),
        _metric_check(
            key="pose_box_map50_95",
            label="Pose 车牌框 mAP50-95",
            value=pose.get("box_map50_95"),
            threshold=pose_map50_95_threshold,
            metric_label="框 mAP50-95",
        ),
        _metric_check(
            key="pose_keypoint_map50",
            label="Pose 四角点 mAP50",
            value=pose.get("pose_map50"),
            threshold=pose_map50_threshold,
            metric_label="关键点 mAP50",
        ),
        _metric_check(
            key="pose_keypoint_map50_95",
            label="Pose 四角点 mAP50-95",
            value=pose.get("pose_map50_95"),
            threshold=pose_map50_95_threshold,
            metric_label="关键点 mAP50-95",
        ),
    ]
    for key, label in (
        ("ocr", "车牌号码 OCR"),
        ("color", "车牌颜色"),
        ("plate_type", "车牌类型"),
    ):
        threshold = (
            OCR_ACCURACY_THRESHOLD if key == "ocr" else CLASSIFIER_ACCURACY_THRESHOLD
        )
        for split in ("val", "test"):
            checks.append(
                _metric_check(
                    key=f"{key}_{split}_accuracy",
                    label=f"{label} {split} 准确率",
                    value=metrics[key]["metrics"][split].get("accuracy"),
                    threshold=threshold,
                    metric_label=f"{split} accuracy",
                )
            )

    raw_summary = _json_dict(metrics_summary)
    warnings = [
        str(item)
        for item in (raw_summary.get("quality_warnings") or [])
        if str(item).strip()
    ]
    failed = [item for item in checks if item["status"] == "不达标"]
    pending = [item for item in checks if item["status"] == "待评估"]
    if failed:
        status = "不达标"
        summary = "；".join(item["detail"] for item in failed[:3])
    elif pending:
        status = "需复核"
        summary = f"{len(pending)} 项关键指标缺失，需要补充验证"
    elif warnings:
        status = "需复核"
        summary = "指标达标，但训练数据存在类别覆盖风险"
    else:
        status = "达标"
        summary = "Pose、OCR、颜色和车牌类型组件均达到当前阈值"
    return {
        "status": status,
        "summary": summary,
        "checks": checks,
        "warnings": warnings,
    }
