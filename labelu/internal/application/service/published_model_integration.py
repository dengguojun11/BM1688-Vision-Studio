"""Build public-model integration metadata from edge validation + quantization.

The public catalog must not do this work per request.  Edge validation and
TPU-MLIR quantization are the authoritative sources; this module snapshots
their results into ``published_model_catalog`` before publication.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_task,
    crud_yolo_edge_validation_job,
)
from labelu.internal.common.config import settings
from labelu.internal.domain.models.published_model_catalog import (
    PublishedModelCatalog,
)
from labelu.internal.domain.models.sample import TaskSample


def _percentile(values: list[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, int(round(percentile / 100 * (len(ordered) - 1)))))
    return ordered[index]


def _candidate_calibration_dirs(export_id: int) -> list[Path]:
    media_root = Path(settings.MEDIA_ROOT)
    root = media_root / "yolo_bm1688_exports" / str(int(export_id))
    return [
        root / "cali_sample_out",
        root / "models" / "BM1688",
        root / "models" / "onnx",
    ]


def find_calibration_table(export_id: int) -> Optional[Path]:
    for directory in _candidate_calibration_dirs(int(export_id)):
        if not directory.is_dir():
            continue
        candidates = sorted(directory.glob("*_cali_table"))
        if candidates:
            return candidates[0]
    return None


def _parse_calibration_rows(path: Path) -> list[tuple[str, float, float, float]]:
    rows: list[tuple[str, float, float, float]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            rows.append(
                (
                    str(parts[0]),
                    float(parts[1]),
                    float(parts[2]),
                    float(parts[3]),
                )
            )
        except (TypeError, ValueError):
            continue
    return rows


def _head_calibration_rows(
    rows: list[tuple[str, float, float, float]],
) -> list[tuple[str, float, float, float]]:
    excluded = (
        "output0_Transpose",
        "/model.23/Concat_3",
        "/model.23/Mul_2",
        "/model.23/Concat_2",
        "/model.23/Div_1",
        "/model.23/Add_2",
        "/model.23/Add_1",
        "/model.23/Sub_",
    )
    result: list[tuple[str, float, float, float]] = []
    for row in rows:
        name = row[0]
        if not (name.startswith("/model.22/") or name.startswith("/model.23/")):
            continue
        if any(token in name for token in excluded):
            continue
        result.append(row)
    if result:
        return result
    # Older exports used short op names without the /model.N/ prefix.
    return [
        row
        for row in rows
        if row[0] != "images" and not any(token in row[0] for token in excluded)
    ]


def calibration_thresholds(export_id: int) -> Optional[dict[str, Any]]:
    """Summarize the head-layer quantization range.

    low  = P50(abs(min))      -> lower clamp
    high = P90(threshold)     -> upper clamp
    bypass = P95(max)         -> outlier/high-precision bypass guard
    """
    path = find_calibration_table(int(export_id))
    if path is None:
        return None
    rows = _head_calibration_rows(_parse_calibration_rows(path))
    if not rows:
        return None
    low_values = [abs(row[2]) for row in rows]
    high_values = [abs(row[1]) for row in rows]
    bypass_values = [abs(row[3]) for row in rows]
    low = _percentile(low_values, 50)
    high = _percentile(high_values, 90)
    bypass = _percentile(bypass_values, 95)
    if low is None or high is None or bypass is None:
        return None
    # Keep the three fields semantically ordered even for unusual calibration
    # distributions.
    high = max(high, low + 0.01)
    bypass = max(bypass, high + 0.01)
    return {
        "low_thresh": round(low, 2),
        "high_thresh": round(high, 2),
        "bypass_thresh": round(bypass, 2),
        "source": "tpu_mlir_cali_table",
        "method": "head_percentile",
        "calibration_table": path.name,
        "rows_used": len(rows),
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _threshold_percent(value: Any) -> Optional[float]:
    try:
        return round(float(value) * 100, 2)
    except (TypeError, ValueError):
        return None


def _latest_edge_result(db: Session, export_id: int) -> dict[str, Any]:
    try:
        job = crud_yolo_edge_validation_job.latest_completed_for_export(
            db, export_id=int(export_id)
        )
    except Exception:
        return {}
    if job is None:
        return {}
    payload = _json_object(getattr(job, "result_json", None))
    models = payload.get("eval_models") or payload.get("bmodels") or {}
    if not isinstance(models, dict):
        return {}
    model = models.get("int8") or models.get("INT8") or {}
    if not isinstance(model, dict):
        return {}
    metrics = model.get("metrics") if isinstance(model.get("metrics"), dict) else model
    class_metrics = model.get("class_metrics") or model.get("per_class_rows") or []
    analysis = model.get("business_threshold_analysis")
    if not isinstance(analysis, dict):
        analysis = {}
    return {
        "f1": _threshold_percent(metrics.get("f1")),
        "model": model,
        "class_metrics": class_metrics if isinstance(class_metrics, list) else [],
        "analysis": analysis,
    }


def edge_metrics(export_id: int, db: Session) -> dict[str, Any]:
    result = _latest_edge_result(db, int(export_id))
    f1 = result.get("f1")
    class_thresholds: dict[str, dict[str, Any]] = {}
    analysis = result.get("analysis") or {}
    for item in analysis.get("per_class") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("class_name") or item.get("name") or "").strip()
        if not name:
            continue
        values = {
            "low_thresh": _threshold_percent(item.get("conf_threshold_gray_recall")),
            "high_thresh": _threshold_percent(item.get("conf_threshold_flink_pass")),
            "bypass_thresh": _threshold_percent(item.get("conf_threshold_alert_f1")),
        }
        if any(value is not None for value in values.values()):
            class_thresholds[name] = values
    return {"f1": f1, "class_thresholds": class_thresholds}


def _task_profile(db: Session, task_id: int) -> dict[str, Any]:
    """Aggregate weather/lighting/scene/angle values from task samples."""
    if not task_id:
        return {}
    values: dict[str, Counter[str]] = {
        "weather": Counter(),
        "lighting": Counter(),
        "sceneType": Counter(),
        "angle": Counter(),
    }
    samples = (
        db.query(TaskSample)
        .filter(
            TaskSample.task_id == int(task_id),
            TaskSample.deleted_at.is_(None),
        )
        .all()
    )
    for sample in samples:
        payload = _json_object(getattr(sample, "data", None))
        result = payload.get("result")
        result = _json_object(result) if isinstance(result, str) else result
        if not isinstance(result, dict):
            continue
        tag_payload = result.get("tagTool")
        if not isinstance(tag_payload, dict):
            continue
        for item in tag_payload.get("result") or []:
            if not isinstance(item, dict):
                continue
            tag_values = item.get("value")
            if not isinstance(tag_values, dict):
                continue
            for key in values:
                raw_values = tag_values.get(key)
                if not isinstance(raw_values, list):
                    raw_values = [raw_values] if raw_values else []
                for raw in raw_values:
                    text = str(raw or "").strip()
                    if text:
                        values[key][text] += 1
    profile: dict[str, Any] = {}
    profile_all: dict[str, list[str]] = {}
    for key, counter in values.items():
        if not counter:
            continue
        profile[key] = counter.most_common(1)[0][0]
        profile_all[key] = sorted(counter)
    if not profile:
        return {}
    return {
        "env_profile": {
            key: profile[key]
            for key in ("weather", "lighting")
            if key in profile
        },
        "env_profile_all": {
            key: profile_all[key]
            for key in ("weather", "lighting")
            if key in profile_all
        },
        "viewProfile": {
            key: profile[key] for key in ("sceneType", "angle") if key in profile
        },
        "viewProfile_all": {
            key: profile_all[key]
            for key in ("sceneType", "angle")
            if key in profile_all
        },
    }


def refresh_model_integration_metadata(db: Session, row) -> bool:
    """Snapshot edge metrics and calibration thresholds on one catalog row.

    Existing environment/profile values are preserved; this function only
    replaces fields that can be derived from the export artifacts.
    """
    export_id = getattr(row, "export_id", None)
    if not export_id:
        return False
    meta = _json_object(getattr(row, "integration_meta_json", None))
    changed = False
    edge = edge_metrics(int(export_id), db)
    if edge.get("f1") is not None:
        row.f1 = float(edge["f1"])
        meta["f1"] = float(edge["f1"])
        changed = True
    if edge.get("class_thresholds"):
        meta["class_thresholds"] = edge["class_thresholds"]
        changed = True
    thresholds = calibration_thresholds(int(export_id))
    if thresholds:
        meta["thresh"] = {
            key: thresholds[key]
            for key in ("low_thresh", "high_thresh", "bypass_thresh")
        }
        meta["thresh_source"] = {
            key: thresholds[key]
            for key in ("source", "method", "calibration_table", "rows_used")
        }
        changed = True
    profile = _task_profile(db, getattr(row, "task_id", None))
    if profile:
        for key in (
            "env_profile",
            "env_profile_all",
            "viewProfile",
            "viewProfile_all",
        ):
            if not meta.get(key):
                meta[key] = profile.get(key)
                changed = changed or bool(profile.get(key))
    if changed:
        row.integration_meta_json = json.dumps(meta, ensure_ascii=False)
    return changed


def refresh_models_for_export(db: Session, export_id: int) -> int:
    """Refresh every catalog row that references an export."""
    rows = (
        db.query(PublishedModelCatalog)
        .filter(
            PublishedModelCatalog.export_id == int(export_id),
            PublishedModelCatalog.deleted_at.is_(None),
        )
        .all()
    )
    refreshed = 0
    for row in rows:
        if refresh_model_integration_metadata(db, row):
            refreshed += 1
    if refreshed:
        db.flush()
    return refreshed
