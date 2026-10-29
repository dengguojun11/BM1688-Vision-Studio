from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import requests
from fastapi.encoders import jsonable_encoder
from fastapi import status
from loguru import logger
from pydantic import ValidationError
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_attachment,
    crud_experiment_dataset_import,
    crud_sample,
    crud_strategy_task_integration,
    crud_task,
    crud_user,
    crud_yolo_bm1688_export,
    crud_yolo_edge_validation_job,
    crud_yolo_train_job,
)
from labelu.internal.application.command.strategy_evolution import (
    BUSINESS_THRESHOLD_FIELDS,
    CreateStrategyEvolutionTaskCommand,
    StrategyEvolutionPlatformCallbackCommand,
)
from labelu.internal.application.command.yolo_edge_validation import (
    CreateYoloEdgeValidationJobCommand,
    EDGE_THRESHOLD_RANGES,
    THRESHOLD_RATIO_RANGE,
    THRESHOLD_RATIO_UNIT,
)
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.application.response.base import MetaData
from labelu.internal.application.response.strategy_evolution import (
    StrategyEvolutionPlatformCallbackResponse,
    StrategyEvolutionThresholdContractResponse,
    StrategyEvolutionTaskAcceptedResponse,
    StrategyEvolutionTaskDetailResponse,
    StrategyEvolutionTaskListRowResponse,
)
from labelu.internal.application.service import yolo_edge_validation
from labelu.internal.application.service import yolo_aicm_publish
from labelu.internal.application.service import yolo_bm1688_export
from labelu.internal.application.service import yolo_train
from labelu.internal.application.service.yolo_quality_policy import (
    resolve_policy_for_preset,
)
from labelu.internal.application.service.yolo_dataset import build_yolo_rect_class_names
from labelu.internal.common import aicm_publish_job
from labelu.internal.common.service_account import (
    OPEN_SYSTEM_USERNAME,
    ensure_open_system_user,
)
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.config import settings
from labelu.internal.common.storage import build_annotation_display_api_path
from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)
from labelu.internal.domain.models.sample import SampleState
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.sample import TaskSample

PLATFORM_STATUS_ACCEPTED = "accepted"
EXTERNAL_PLATFORM = "experiment_platform"
THRESHOLD_TASK_TYPE = "threshold_experiment"
MODEL_CAPABILITY_TASK_TYPE = "model_capability"
FORMAL_THRESHOLD_SCAN_CONF = 0.21
FORMAL_THRESHOLD_MATCH_IOU = 0.5
CALLBACK_SIGNATURE_HEADER = "X-LabelU-Signature"
CALLBACK_TIMESTAMP_HEADER = "X-LabelU-Timestamp"
CALLBACK_EVENT_ID_HEADER = "X-LabelU-Event-Id"
IMPORT_READY_STATUSES = {"COMPLETED", "PARTIAL_COMPLETED"}


def _externalize_model_id_keys(value: Any) -> Any:
    """Expose camelCase model IDs at the external strategy API boundary."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            output_key = "modelId" if key == "model_id" else key
            if output_key == "modelId" and output_key in result:
                continue
            result[output_key] = _externalize_model_id_keys(item)
        return result
    if isinstance(value, list):
        return [_externalize_model_id_keys(item) for item in value]
    return value
FINAL_PLATFORM_STATUSES = {
    "succeeded",
    "failed",
    "cancelled",
    "comparison_failed",
    "callback_failed",
}
_WATCHDOG_STARTED = False
_WATCHDOG_LOCK = threading.Lock()
_AUTOMATION_WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
_THRESHOLD_ASSET_UNSET = object()


class _AutomationLeaseLost(RuntimeError):
    pass


STATUS_ALIASES = {
    "running": "running",
    "success": "succeeded",
    "completed": "succeeded",
    "succeeded": "succeeded",
    "failed": "failed",
    "canceled": "cancelled",
    "cancelled": "cancelled",
}


class StrategyEvolutionError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        self.message = message
        super().__init__(message)


def gateway_timestamp_ms() -> str:
    return str(int(time.time() * 1000))


def _json_dumps(value: Any) -> str:
    return json.dumps(jsonable_encoder(value), ensure_ascii=False)


def _strategy_request_sha256(cmd: CreateStrategyEvolutionTaskCommand) -> str:
    """Build the idempotency fingerprint for a strategy task request.

    The fingerprint is calculated from the normalized command rather than the
    raw HTTP bytes.  This makes harmless JSON formatting/key-order differences
    idempotent while still rejecting a reused ``external_task_id`` whose
    business payload changed.
    """
    payload = cmd.model_dump(mode="json", by_alias=False)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _json_loads_object(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_loads_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _normalize_status(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in STATUS_ALIASES:
        raise StrategyEvolutionError(
            400,
            f"无效的策略演化平台回调状态，不支持的 status：{value}",
        )
    return STATUS_ALIASES[normalized]


def _validation_message(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "无效的策略演化平台回调参数"
    first = errors[0]
    loc = ".".join(str(item) for item in first.get("loc", []) if str(item))
    msg = str(first.get("msg") or "参数值无效")
    return f"无效的策略演化平台回调参数：{loc} {msg}".strip()


def _media_root_path() -> Path:
    return Path(settings.MEDIA_ROOT).resolve()


def _media_relative_path(raw_path: Any) -> str | None:
    path_text = str(raw_path or "").strip()
    if not path_text:
        return None
    try:
        path = Path(path_text).resolve()
    except OSError:
        return None
    try:
        relative = path.relative_to(_media_root_path())
    except ValueError:
        return None
    return relative.as_posix()


def _annotation_display_url_from_local_path(raw_path: Any, *, max_side: int = 1680) -> str | None:
    relative = _media_relative_path(raw_path)
    if not relative:
        return None
    return build_annotation_display_api_path(relative, max_side=max_side)


def _build_edge_image_compare(result: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    pack = result.get("pack") if isinstance(result.get("pack"), dict) else {}
    file_mappings = pack.get("file_mappings") if isinstance(pack.get("file_mappings"), list) else []
    if not file_mappings:
        return None

    job_root = Path(str(pack.get("job_root") or "")).resolve() if pack.get("job_root") else None
    preview_dir = job_root / "server_eval" / "bmcv_preview" if job_root else None
    items: list[dict[str, Any]] = []
    matched_count = 0

    for index, raw_item in enumerate(file_mappings):
        if not isinstance(raw_item, dict):
            continue
        packed_name = str(raw_item.get("packed_name") or "").strip()
        if not packed_name:
            continue
        preview_path = preview_dir / packed_name if preview_dir else None
        result_url = (
            _annotation_display_url_from_local_path(preview_path)
            if preview_path and preview_path.is_file()
            else None
        )
        if result_url:
            matched_count += 1
        item = {
            "index": index,
            "sample_id": raw_item.get("sample_id"),
            "attachment_id": raw_item.get("attachment_id"),
            "packed_name": packed_name,
            "original_name": raw_item.get("original_name"),
            "source_kind": raw_item.get("source_kind"),
            "has_annotation": bool(raw_item.get("has_annotation")),
            "original_url": _annotation_display_url_from_local_path(raw_item.get("source_path")),
            "result_url": result_url,
            "has_result": bool(result_url),
        }
        items.append(item)

    if not items:
        return None
    return {
        "matched_count": matched_count,
        "total_count": len(items),
        "items": items,
    }


def _decorate_strategy_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return result
    for key in ("edge_validation_jobs", "edge_experiment_jobs"):
        jobs = result.get(key)
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_result = job.get("result")
            if not isinstance(job_result, dict):
                continue
            image_compare = _build_edge_image_compare(job_result)
            if image_compare:
                job_result["image_compare"] = image_compare
    return result


def _admin_user(db: Session):
    try:
        return ensure_open_system_user(db)
    except Exception as exc:
        raise StrategyEvolutionError(
            500,
            f"service account unavailable: {OPEN_SYSTEM_USERNAME}",
        ) from exc


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_terminal_status(value: str | None) -> bool:
    return str(value or "").strip().upper() in {
        "COMPLETED",
        "PARTIAL_COMPLETED",
        "FAILED",
        "CANCELLED",
    }


def _is_success_status(value: str | None) -> bool:
    return str(value or "").strip().upper() in {"COMPLETED", "PARTIAL_COMPLETED"}


def _strategy_callback_url() -> str | None:
    base = str(settings.STRATEGY_EVOLUTION_OUTBOUND_CALLBACK_BASE_URL or "").strip()
    if not base:
        return None
    return f"{base.rstrip('/')}/api/v1/strategy-evolution/platform-callbacks"


def _configured_callback_url_for_task_type(task_type: str) -> str | None:
    if task_type == THRESHOLD_TASK_TYPE:
        value = settings.STRATEGY_EVOLUTION_THRESHOLD_CALLBACK_URL
    elif task_type == MODEL_CAPABILITY_TASK_TYPE:
        value = settings.STRATEGY_EVOLUTION_MODEL_CALLBACK_URL
    else:
        value = ""
    return str(value or "").strip() or None


def _normalized_callback_url(value: str) -> str:
    return str(value).strip().rstrip("/")


def _validate_task_callback_url(
    *, task_type: str, callback_url: str | None
) -> str | None:
    """Apply the configured per-task endpoint and reject port mix-ups."""
    expected_url = _configured_callback_url_for_task_type(task_type)
    requested_url = str(callback_url or "").strip() or None
    if (
        expected_url
        and requested_url
        and _normalized_callback_url(requested_url)
        != _normalized_callback_url(expected_url)
    ):
        raise StrategyEvolutionError(
            400,
            f"{task_type} 的 callback_url 必须使用已配置地址：{expected_url}",
        )
    return expected_url or requested_url


def _resolved_outbound_callback_url(row: StrategyTaskIntegration) -> str | None:
    return (
        _configured_callback_url_for_task_type(str(row.task_type or ""))
        or str(row.outbound_callback_url or "").strip()
        or _strategy_callback_url()
    )


def _extract_eval_summary(job_payload: dict[str, Any]) -> dict[str, Any]:
    result = job_payload.get("result") if isinstance(job_payload.get("result"), dict) else {}
    summary = result.get("eval_summary") if isinstance(result.get("eval_summary"), dict) else {}
    return summary if isinstance(summary, dict) else {}


def _eval_summary_f1(eval_summary: dict[str, Any]) -> float | None:
    direct = _safe_float(eval_summary.get("f1"))
    if direct is not None:
        return direct
    precision = _safe_float(eval_summary.get("precision"))
    recall = _safe_float(eval_summary.get("recall"))
    if precision is None or recall is None or precision + recall <= 0:
        return None
    return round((2.0 * precision * recall) / (precision + recall), 6)


def _edge_job_score(job_payload: dict[str, Any]) -> float | None:
    summary = _extract_eval_summary(job_payload)
    for candidate in (
        _eval_summary_f1(summary),
        _safe_float(summary.get("map50_95")),
        _safe_float(summary.get("map50")),
    ):
        if candidate is not None:
            return candidate
    return None


def _strategy_export_ready(export_row: Any) -> bool:
    """Accept completed exports and partial exports with a real bmodel."""
    return yolo_bm1688_export.is_bmodel_artifact_ready(export_row)


def _aggregate_edge_jobs(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    terminal = [_is_terminal_status(job.get("status")) for job in jobs]
    success_jobs = [job for job in jobs if _is_success_status(job.get("status"))]
    scores = [score for score in (_edge_job_score(job) for job in success_jobs) if score is not None]
    return {
        "job_count": len(jobs),
        "terminal_count": sum(1 for item in terminal if item),
        "success_count": len(success_jobs),
        "failed_count": sum(
            1 for job in jobs if str(job.get("status") or "").strip().upper() == "FAILED"
        ),
        "avg_score": round(sum(scores) / len(scores), 6) if scores else None,
        "scores": scores,
    }


def _train_job_score(job_payload: dict[str, Any]) -> float | None:
    metrics = (
        job_payload.get("training_val_metrics")
        if isinstance(job_payload.get("training_val_metrics"), dict)
        else {}
    )
    for key in ("map50_95", "map50", "precision", "recall"):
        value = _safe_float(metrics.get(key))
        if value is not None:
            return value
    return None


def _build_threshold_recommendation(
    db: Session,
    experiment: dict[str, Any],
    *,
    result: dict[str, Any],
    job_summary: dict[str, Any],
) -> dict[str, Any]:
    current = (
        experiment.get("current_thresholds")
        if isinstance(experiment.get("current_thresholds"), dict)
        else {}
    )
    requested = [
        str(item).strip()
        for item in (experiment.get("requested_thresholds") or [])
        if str(item).strip()
    ]
    recommended = {key: current.get(key) for key in requested if key in current}
    internal_recommended_thresholds = None
    internal_threshold_source = "edge_validation_formal_analysis"
    mapping_detail: dict[str, Any] = {
        "edge_low_threshold": "formal_analysis.conf_threshold_gray_recall",
        "flink_filter_threshold": "formal_analysis.conf_threshold_alert_f1",
        "edge_high_threshold": "formal_analysis.conf_threshold_flink_pass",
    }

    jobs = _result_edge_jobs(result)
    latest_feedback_job = next(
        (
            job
            for job in reversed(jobs)
            if _safe_int(job.get("job_id"))
            and _is_terminal_status(job.get("status"))
        ),
        None,
    )
    formal_analysis = None
    if latest_feedback_job:
        eval_summary = _extract_eval_summary(latest_feedback_job)
        candidate = eval_summary.get("business_threshold_analysis")
        if isinstance(candidate, dict):
            formal_analysis = candidate
    if isinstance(formal_analysis, dict) and formal_analysis.get("status") == "ok":
        thresholds = formal_analysis.get("recommended_thresholds")
        if isinstance(thresholds, dict):
            for key in requested:
                if key in thresholds:
                    recommended[key] = thresholds[key]
        internal_recommended_thresholds = {
            "conf_thres": formal_analysis.get("recommended_thresholds", {}).get(
                "edge_low_threshold"
            ),
            "nms_thres": None,
            "match_iou_thres": formal_analysis.get("iou_threshold"),
            "source": "formal_threshold_analysis",
            "confidence_step": formal_analysis.get("confidence_step"),
        }
    else:
        internal_threshold_source = "edge_validation_formal_analysis_missing"
        mapping_detail["analysis_error"] = "边端结果缺少正式阈值分析"

    return {
        "recommended_thresholds": recommended,
        "goal": experiment.get("goal"),
        "recommendation_source": internal_threshold_source,
        "internal_recommended_thresholds": internal_recommended_thresholds,
        "mapping_detail": mapping_detail,
        "formal_analysis": formal_analysis,
        "metrics_summary": job_summary,
    }


def _load_export_threshold_assets(
    export_row,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not export_row:
        return {}, {}, {}
    business = _json_loads_object(getattr(export_row, "business_thresholds_json", None))
    validation = _json_loads_object(
        getattr(export_row, "validation_thresholds_json", None)
    )
    binding = _json_loads_object(getattr(export_row, "threshold_binding_json", None))
    return business, validation, binding


def _best_threshold_bucket(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    for key in ("effective", "recommended", "current"):
        value = raw.get(key)
        if isinstance(value, dict) and value:
            return dict(value)
    return None


def _resolve_current_business_thresholds(
    db: Session,
    *,
    experiment: dict[str, Any],
    source_export_id: int | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    current = (
        experiment.get("current_thresholds")
        if isinstance(experiment.get("current_thresholds"), dict)
        else None
    )
    if current:
        return dict(current), "request_current_thresholds"
    export_id = int(source_export_id or 0)
    if export_id > 0:
        export_row = crud_yolo_bm1688_export.get(db, export_id)
        business, _, _ = _load_export_threshold_assets(export_row)
        resolved = _best_threshold_bucket(business)
        if resolved:
            return resolved, "published_model_thresholds"
    return None, None


def _derive_business_thresholds_from_validation(
    current_business_thresholds: dict[str, Any] | None,
    validation_thresholds: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(current_business_thresholds, dict) or not current_business_thresholds:
        return None
    if not isinstance(validation_thresholds, dict) or not validation_thresholds:
        return dict(current_business_thresholds)
    recommended = dict(current_business_thresholds)
    conf_value = validation_thresholds.get("conf_thres")
    if conf_value is not None and "edge_low_threshold" in recommended:
        recommended["edge_low_threshold"] = conf_value
    return _compact_payload_dict(recommended)


def _build_model_capability_threshold_recommendation(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    result: dict[str, Any],
) -> dict[str, Any]:
    experiment = _json_loads_object(row.experiment_json)
    current_business, current_source = _resolve_current_business_thresholds(
        db,
        experiment=experiment,
        source_export_id=_safe_int(
            result.get("source_export_id") or row.source_export_id
        ),
    )
    candidate_edge_metrics = (
        _summarize_edge_job_collection(_result_edge_jobs(result) or []) or {}
    )
    current_validation = (
        candidate_edge_metrics.get("validation_thresholds")
        if isinstance(candidate_edge_metrics.get("validation_thresholds"), dict)
        else None
    )
    recommended_business = _derive_business_thresholds_from_validation(
        current_business,
        current_validation,
    )
    formal_analysis = candidate_edge_metrics.get("business_threshold_analysis")
    formal_ok = bool(
        isinstance(formal_analysis, dict) and formal_analysis.get("status") == "ok"
    )
    if formal_ok:
        formal_recommended = formal_analysis.get("recommended_thresholds")
        if isinstance(formal_recommended, dict):
            recommended_business = formal_recommended
    if formal_ok and recommended_business:
        mapping_detail = {
            "edge_low_threshold": "candidate.formal_analysis.conf_threshold_gray_recall",
            "flink_filter_threshold": "candidate.formal_analysis.conf_threshold_alert_f1",
            "edge_high_threshold": "candidate.formal_analysis.conf_threshold_flink_pass",
        }
    elif current_business:
        mapping_detail = {
            "edge_low_threshold": (
                "candidate.validation_thresholds.conf_thres"
                if isinstance(current_validation, dict)
                and current_validation.get("conf_thres") is not None
                else "current.edge_low_threshold"
            ),
            "flink_filter_threshold": "current.flink_filter_threshold",
            "edge_high_threshold": "current.edge_high_threshold",
        }
    else:
        mapping_detail = {"current_thresholds": current_source}
    return (
        _compact_payload_dict(
            {
                "current_thresholds": current_business,
                "recommended_thresholds": recommended_business,
                "internal_recommended_thresholds": current_validation,
                "formal_analysis": formal_analysis,
                "recommendation_source": (
                    "candidate_edge_formal_threshold_analysis"
                    if formal_ok
                    else (
                        "candidate_edge_validation_thresholds"
                        if current_validation
                        else current_source
                    )
                ),
                "goal": experiment.get("goal"),
                "mapping_detail": mapping_detail,
            }
        )
        or {}
    )


def _threshold_binding_payload(
    *,
    row: StrategyTaskIntegration,
    result: dict[str, Any],
    publish_job: dict[str, Any] | None = None,
    effective_business_thresholds: dict[str, Any] | None = None,
    effective_validation_thresholds: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    publish_result = (
        publish_job.get("result")
        if isinstance(publish_job, dict) and isinstance(publish_job.get("result"), dict)
        else {}
    )
    published_model_id = _published_model_set_id_from_publish_job(publish_job or {})
    model_set_id = _safe_int(
        published_model_id or publish_result.get("model_set_id") or publish_result.get("model_id")
    )
    export_id = _safe_int(result.get("derived_export_id") or row.derived_export_id)
    payload = _compact_payload_dict(
        {
            "source_task_type": row.task_type,
            "source_external_task_id": row.external_task_id,
            "source_publish_job_id": (
                str(publish_job.get("job_id") or "").strip() or None
                if isinstance(publish_job, dict)
                else None
            ),
            "export_id": export_id,
            "published_model_id": published_model_id,
            "model_set_id": model_set_id,
            "applied_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "threshold_unit": THRESHOLD_RATIO_UNIT,
            "effective_business_thresholds": effective_business_thresholds,
            "effective_validation_thresholds": effective_validation_thresholds,
        }
    )
    return payload or None


def _threshold_asset_section_payload(
    *,
    current: dict[str, Any] | None = None,
    recommended: dict[str, Any] | None = None,
    effective: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    def _usable_thresholds(value: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        compact = _compact_payload_dict(value)
        return compact or None

    current_payload = _usable_thresholds(current)
    recommended_payload = _usable_thresholds(recommended)
    effective_payload = _usable_thresholds(effective)
    if not any((current_payload, recommended_payload, effective_payload)):
        return None
    return _compact_payload_dict(
        {
            "unit": THRESHOLD_RATIO_UNIT,
            "current": current_payload,
            "recommended": recommended_payload,
            "effective": effective_payload,
            **(metadata or {}),
        }
    ) or None


def _persist_export_threshold_assets(
    db: Session,
    *,
    export_id: int,
    published_model_id: int | None | object = _THRESHOLD_ASSET_UNSET,
    business_thresholds: dict[str, Any] | None | object = _THRESHOLD_ASSET_UNSET,
    validation_thresholds: dict[str, Any] | None | object = _THRESHOLD_ASSET_UNSET,
    threshold_binding: dict[str, Any] | None | object = _THRESHOLD_ASSET_UNSET,
    quality_gate: dict[str, Any] | None | object = _THRESHOLD_ASSET_UNSET,
) -> None:
    if int(export_id or 0) <= 0:
        return
    export_row = crud_yolo_bm1688_export.get(db, int(export_id))
    if not export_row:
        return
    updates: dict[str, Any] = {}
    if published_model_id is not _THRESHOLD_ASSET_UNSET:
        updates["published_model_id"] = (
            int(published_model_id) if published_model_id else None
        )
    for column_name, value in (
        ("business_thresholds_json", business_thresholds),
        ("validation_thresholds_json", validation_thresholds),
        ("threshold_binding_json", threshold_binding),
        ("quality_gate_json", quality_gate),
    ):
        if value is _THRESHOLD_ASSET_UNSET:
            continue
        if value is None or value == {}:
            updates[column_name] = None
            continue
        if not isinstance(value, dict):
            raise TypeError(f"{column_name} must be a dictionary or null")
        updates[column_name] = _json_dumps(value)
    if not updates:
        return
    with begin_transaction(db):
        crud_yolo_bm1688_export.update_row(db, export_row, **updates)


def _compact_payload_dict(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    compact: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        compact[key] = value
    return compact


def _derive_f1_score(
    *,
    precision: float | None,
    recall: float | None,
) -> float | None:
    if precision is None or recall is None:
        return None
    denominator = precision + recall
    if denominator <= 0:
        return None
    return round((2 * precision * recall) / denominator, 6)


def _metric_payload_with_f1(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    payload = dict(raw)
    precision = _safe_float(payload.get("precision"))
    recall = _safe_float(payload.get("recall"))
    if _safe_float(payload.get("f1")) is None:
        f1_score = _derive_f1_score(precision=precision, recall=recall)
        if f1_score is not None:
            payload["f1"] = f1_score
    return _compact_payload_dict(payload)


def _job_threshold_payload(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    payload: dict[str, Any] = {
        "conf_thres": raw.get("conf_thres"),
        "nms_thres": raw.get("nms_thres"),
        "match_iou_thres": raw.get("match_iou_thres"),
    }
    for key in (
        "source",
        "reason",
        "confidence_level",
        "provider_key",
        "requested",
        "defaulted_fields",
    ):
        if key in raw:
            payload[key] = raw[key]
    return payload


def _threshold_section_payload(
    *,
    current: dict[str, Any] | None,
    recommended: dict[str, Any] | None,
    effective: dict[str, Any] | None,
    requested: dict[str, Any] | None = None,
    **metadata: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "unit": THRESHOLD_RATIO_UNIT,
        "requested": requested,
        "current": current,
        "recommended": recommended,
        "effective": effective,
    }
    payload.update(_compact_payload_dict(metadata))
    return payload


def _extract_edge_runtime_metrics(
    result_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(result_payload, dict):
        return None
    execution = (
        result_payload.get("execution")
        if isinstance(result_payload.get("execution"), dict)
        else {}
    )
    execution_summary = (
        result_payload.get("execution_summary")
        if isinstance(result_payload.get("execution_summary"), dict)
        else {}
    )
    prediction_summary = (
        result_payload.get("prediction_summary")
        if isinstance(result_payload.get("prediction_summary"), dict)
        else {}
    )
    runtime_metrics = _compact_payload_dict(
        {
            "avg_inference_ms": execution.get("avg_inference_ms"),
            "p50_inference_ms": execution.get("p50_inference_ms"),
            "p95_inference_ms": execution.get("p95_inference_ms"),
            "preprocess_ms": execution.get("preprocess_ms"),
            "postprocess_ms": execution.get("postprocess_ms"),
            "fps": execution.get("fps") or execution.get("throughput_fps"),
            "memory_mb": execution.get("memory_mb"),
            "npu_usage": execution.get("npu_usage"),
            "execution_summary": execution_summary,
            "prediction_summary": prediction_summary,
        }
    )
    return runtime_metrics or None


def _summarize_edge_job_payload(job_payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(job_payload, dict):
        return None
    result_payload = (
        job_payload.get("result")
        if isinstance(job_payload.get("result"), dict)
        else {}
    )
    eval_summary = _metric_payload_with_f1(
        result_payload.get("eval_summary")
        if isinstance(result_payload.get("eval_summary"), dict)
        else None
    ) or {}
    return _compact_payload_dict(
        {
            "job_id": _safe_int(job_payload.get("job_id")),
            "export_id": _safe_int(job_payload.get("export_id")),
            "task_id": _safe_int(job_payload.get("task_id")),
            "status": str(job_payload.get("status") or "").strip() or None,
            "edge_result_status": str(job_payload.get("edge_result_status") or "").strip()
            or None,
            "image_count": _safe_int(job_payload.get("image_count")),
            "labeled_image_count": _safe_int(job_payload.get("labeled_image_count")),
            "unlabeled_image_count": _safe_int(job_payload.get("unlabeled_image_count")),
            "start_status": str(job_payload.get("start_status") or "").strip() or None,
            "report_summary": str(job_payload.get("report_summary") or "").strip() or None,
            "validation_thresholds": _job_threshold_payload(
                job_payload.get("thresholds")
                if isinstance(job_payload.get("thresholds"), dict)
                else None
            ),
            "edge_runtime_metrics": _extract_edge_runtime_metrics(result_payload),
            "edge_accuracy_metrics": eval_summary,
            "class_metrics": eval_summary.get("class_metrics"),
            "business_threshold_analysis": eval_summary.get(
                "business_threshold_analysis"
            ),
        }
    )


def _summarize_edge_job_collection(
    jobs: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    valid_jobs = [job for job in (jobs or []) if isinstance(job, dict)]
    if not valid_jobs:
        return None
    latest_terminal = next(
        (
            job
            for job in reversed(valid_jobs)
            if _is_terminal_status(str(job.get("status") or ""))
        ),
        valid_jobs[-1],
    )
    latest_job = _summarize_edge_job_payload(latest_terminal)
    return _compact_payload_dict(
        {
            "job_summary": _aggregate_edge_jobs(valid_jobs),
            "latest_job": latest_job,
            "validation_thresholds": (
                latest_job.get("validation_thresholds")
                if isinstance(latest_job, dict)
                else None
            ),
            "edge_runtime_metrics": (
                latest_job.get("edge_runtime_metrics")
                if isinstance(latest_job, dict)
                else None
            ),
            "edge_accuracy_metrics": (
                latest_job.get("edge_accuracy_metrics")
                if isinstance(latest_job, dict)
                else None
            ),
            "class_metrics": (
                latest_job.get("class_metrics")
                if isinstance(latest_job, dict)
                else None
            ),
            "business_threshold_analysis": (
                latest_job.get("business_threshold_analysis")
                if isinstance(latest_job, dict)
                else None
            ),
        }
    )


def _summarize_training_job_payload(
    job_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(job_payload, dict):
        return None
    return _compact_payload_dict(
        {
            "job_id": _safe_int(job_payload.get("job_id")),
            "task_id": _safe_int(job_payload.get("task_id")),
            "status": str(job_payload.get("status") or "").strip() or None,
            "category": job_payload.get("category"),
            "model_weights": job_payload.get("model_weights"),
            "epochs": _safe_int(job_payload.get("epochs")),
            "imgsz": _safe_int(job_payload.get("imgsz")),
            "hyperparams": (
                dict(job_payload.get("hyperparams"))
                if isinstance(job_payload.get("hyperparams"), dict)
                else None
            ),
            "execution_mode": job_payload.get("execution_mode"),
            "train_node_id": job_payload.get("train_node_id"),
            "runtime_status": job_payload.get("runtime_status"),
            "current_epoch": _safe_int(job_payload.get("current_epoch")),
            "progress_snapshot": job_payload.get("progress_snapshot"),
            "train_sample_count": _safe_int(job_payload.get("train_sample_count")),
            "val_sample_count": _safe_int(job_payload.get("val_sample_count")),
            "test_sample_count": _safe_int(job_payload.get("test_sample_count")),
            "total_sample_count": _safe_int(job_payload.get("total_sample_count")),
            "training_val_metrics": _metric_payload_with_f1(
                job_payload.get("training_val_metrics")
                if isinstance(job_payload.get("training_val_metrics"), dict)
                else None
            ),
            "test_val_metrics": _metric_payload_with_f1(
                job_payload.get("test_val_metrics")
                if isinstance(job_payload.get("test_val_metrics"), dict)
                else None
            ),
            "val_test_comparison": (
                _compact_payload_dict(job_payload.get("val_test_comparison"))
                if isinstance(job_payload.get("val_test_comparison"), dict)
                else None
            ),
            "quality_assessment": (
                _compact_payload_dict(job_payload.get("quality_assessment"))
                if isinstance(job_payload.get("quality_assessment"), dict)
                else None
            ),
            "class_metrics": (
                _compact_payload_dict(job_payload.get("class_metrics"))
                if isinstance(job_payload.get("class_metrics"), dict)
                else None
            ),
            "task_rect_classes": (
                list(job_payload.get("task_rect_classes") or [])
                if isinstance(job_payload.get("task_rect_classes"), list)
                else None
            ),
        }
    )


def _load_training_job_summary(
    db: Session,
    *,
    job_id: int | None,
) -> dict[str, Any] | None:
    if not job_id:
        return None
    try:
        detail = yolo_train.get_train_job_by_id(
            db=db,
            job_id=int(job_id),
            current_user=_admin_user(db),
        )
    except Exception:
        return None
    return _summarize_training_job_payload(_model_job_payload_from_detail(detail))


def _threshold_comparison_payload(
    *,
    source_edge_metrics: dict[str, Any] | None,
    candidate_edge_metrics: dict[str, Any] | None,
) -> dict[str, Any] | None:
    source_summary = (
        source_edge_metrics.get("job_summary")
        if isinstance(source_edge_metrics, dict)
        and isinstance(source_edge_metrics.get("job_summary"), dict)
        else {}
    )
    candidate_summary = (
        candidate_edge_metrics.get("job_summary")
        if isinstance(candidate_edge_metrics, dict)
        and isinstance(candidate_edge_metrics.get("job_summary"), dict)
        else {}
    )
    source_score = _safe_float(source_summary.get("avg_score"))
    candidate_score = _safe_float(candidate_summary.get("avg_score"))
    delta_score = None
    if source_score is not None and candidate_score is not None:
        delta_score = round(candidate_score - source_score, 6)
    passed = None
    reason = None
    if delta_score is not None:
        passed = bool(delta_score >= 0)
        reason = (
            "candidate edge validation score is better"
            if delta_score > 0
            else (
                "edge validation tie"
                if delta_score == 0
                else "candidate edge validation score is worse"
            )
        )
    return (
        _compact_payload_dict(
            {
                "passed": passed,
                "reason": reason,
                "source_score": source_score,
                "candidate_score": candidate_score,
                "delta_score": delta_score,
            }
        )
        or None
    )


def _publish_summary_payload(
    job_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(job_payload, dict):
        return None
    result = (
        job_payload.get("result") if isinstance(job_payload.get("result"), dict) else {}
    )
    allowed_value = result.get("allowed") if "allowed" in result else result.get("ok")
    published_model_id = _published_model_set_id_from_publish_job(job_payload)
    return _compact_payload_dict(
        {
            "allowed": bool(allowed_value),
            "status": str(job_payload.get("status") or "").strip() or None,
            "job_id": str(job_payload.get("job_id") or "").strip() or None,
            "export_id": _safe_int(
                result.get("export_id") or job_payload.get("export_id")
            ),
            "published_model_id": (
                str(published_model_id)
                if published_model_id
                else result.get("model_id")
            ),
            "model_set_id": published_model_id,
            "error_message": str(job_payload.get("error_message") or "").strip()
            or None,
        }
    )


def _threshold_contract_payload() -> dict[str, Any]:
    ratio_range = {
        "minimum": THRESHOLD_RATIO_RANGE[0],
        "maximum": THRESHOLD_RATIO_RANGE[1],
    }
    contract = StrategyEvolutionThresholdContractResponse(
        value_scale=THRESHOLD_RATIO_UNIT,
        ranges={
            "business_thresholds": {
                field: dict(ratio_range) for field in BUSINESS_THRESHOLD_FIELDS
            },
            "validation_thresholds": {
                field: dict(ratio_range) for field in EDGE_THRESHOLD_RANGES
            },
        },
        operating_ranges={
            field: {"minimum": bounds[0], "maximum": bounds[1]}
            for field, bounds in EDGE_THRESHOLD_RANGES.items()
        },
    )
    return contract.model_dump(mode="json")


def _safe_artifact_relative_path(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    windows_path = PureWindowsPath(text)
    posix_path = PurePosixPath(text.replace("\\", "/"))
    if (
        windows_path.is_absolute()
        or bool(windows_path.drive)
        or posix_path.is_absolute()
        or ".." in posix_path.parts
    ):
        return None
    return posix_path.as_posix()


def _safe_path_basename(value: Any) -> str | None:
    text = str(value or "").strip().replace("\\", "/").rstrip("/")
    if not text:
        return None
    basename = text.rsplit("/", 1)[-1].strip()
    if not basename or basename in {".", ".."} or basename.endswith(":"):
        return None
    return basename


def _artifact_manifest_from_export(export_row: Any) -> dict[str, Any]:
    if export_row is None:
        return {}
    return _json_loads_object(getattr(export_row, "artifact_manifest_json", None))


def _artifact_payload(
    *,
    export_row: Any,
    publish_job: dict[str, Any] | None,
) -> dict[str, Any]:
    manifest = _artifact_manifest_from_export(export_row)
    publish_result = (
        publish_job.get("result")
        if isinstance(publish_job, dict) and isinstance(publish_job.get("result"), dict)
        else {}
    )
    package_path = str(publish_result.get("package_path") or "").strip()
    package_basename = _safe_path_basename(package_path)
    files: list[dict[str, Any]] = []
    for raw in manifest.get("artifacts") or []:
        if not isinstance(raw, dict):
            continue
        relative_path = _safe_artifact_relative_path(raw.get("relative_path"))
        if not relative_path:
            continue
        files.append(
            {
                "role": str(raw.get("role") or "").strip() or None,
                "relative_path": relative_path,
                "size_bytes": _safe_int(raw.get("bytes")),
                "sha256": str(raw.get("sha256") or "").strip() or None,
            }
        )

    manifest_version = str(manifest.get("manifest_version") or "").strip() or None
    manifest_sha256 = str(manifest.get("content_sha256") or "").strip() or None
    available = bool(files or package_basename or manifest_sha256)
    missing_reason = None
    if export_row is None:
        missing_reason = "export_not_found"
    elif not manifest and not package_basename:
        missing_reason = "artifact_manifest_not_available"
    elif not available:
        missing_reason = "artifact_metadata_not_available"
    return {
        "availability": "available" if available else "unavailable",
        "missing_reason": missing_reason,
        "manifest_version": manifest_version,
        "manifest_sha256": manifest_sha256,
        "package_basename": package_basename,
        "files": files,
    }


def _candidate_precision(
    *,
    export_row: Any,
    publish_job: dict[str, Any] | None,
) -> str | None:
    publish_result = (
        publish_job.get("result")
        if isinstance(publish_job, dict) and isinstance(publish_job.get("result"), dict)
        else {}
    )
    manifest = _artifact_manifest_from_export(export_row)
    compiler = (
        manifest.get("compiler") if isinstance(manifest.get("compiler"), dict) else {}
    )
    compiler_config = (
        compiler.get("config") if isinstance(compiler.get("config"), dict) else {}
    )
    for raw in (
        publish_result.get("precision"),
        publish_result.get("bmodel_precision"),
        compiler_config.get("precision"),
        compiler_config.get("quantize"),
        compiler_config.get("quantization"),
    ):
        value = str(raw or "").strip().lower()
        if value:
            return value
    for artifact in manifest.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        filename = str(
            artifact.get("relative_path") or artifact.get("path") or ""
        ).lower()
        for precision in ("int8", "fp16", "fp32"):
            if precision in filename:
                return precision
    return None


def _candidate_model_payload(
    db: Session,
    *,
    task_type: str,
    base: dict[str, Any],
    export_id: int | None,
    publish_job: dict[str, Any] | None,
) -> dict[str, Any]:
    export_row = crud_yolo_bm1688_export.get(db, int(export_id)) if export_id else None
    publish_summary = _publish_summary_payload(publish_job)
    published_model_id = _safe_int(
        (publish_summary or {}).get("published_model_id")
        or getattr(export_row, "published_model_id", None)
    )
    publish_status = str((publish_summary or {}).get("status") or "").upper()
    if published_model_id and (publish_summary or {}).get("allowed"):
        publication_status = "published"
    elif publish_job and publish_status in {"FAILED", "CANCELLED", "COMPLETED"}:
        publication_status = "failed"
    elif publish_job:
        publication_status = "publishing"
    elif published_model_id:
        publication_status = "published"
    elif task_type == THRESHOLD_TASK_TYPE:
        publication_status = "not_applicable"
    else:
        publication_status = "not_requested"

    return {
        **base,
        "published_model_id": published_model_id,
        "model_set_id": published_model_id,
        "publication_status": publication_status,
        "precision": _candidate_precision(
            export_row=export_row,
            publish_job=publish_job,
        ),
        "artifact": _artifact_payload(
            export_row=export_row,
            publish_job=publish_job,
        ),
    }


def _optimization_payload(
    *,
    experiment: dict[str, Any],
    result: dict[str, Any],
    candidate_training_metrics: dict[str, Any] | None,
) -> dict[str, Any]:
    training = candidate_training_metrics or {}
    applied_parameters = {
        "model_weights": training.get("model_weights"),
        "epochs": _safe_int(training.get("epochs")),
        "imgsz": _safe_int(training.get("imgsz")),
        "hyperparams": training.get("hyperparams"),
        "execution_mode": training.get("execution_mode") or "local",
        "train_node_id": training.get("train_node_id"),
    }
    missing_fields = [key for key, value in applied_parameters.items() if value is None]
    return {
        "status": "applied" if training else "unavailable",
        "recommendation_source": (
            experiment.get("optimization_recommendation_source")
            or experiment.get("recommendation_source")
            or "strategy_evolution_fixed_defaults"
        ),
        "issue_type": result.get("issue_type")
        or experiment.get("capability_issue_type"),
        "symptoms": result.get("symptoms") or experiment.get("symptoms") or [],
        "applied_parameters": applied_parameters,
        "missing_fields": missing_fields,
    }


def _build_outbound_callback_result_payload(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    result: dict[str, Any],
) -> dict[str, Any]:
    target = _json_loads_object(row.target_json)
    data_window = _json_loads_object(row.data_window_json)
    experiment = _json_loads_object(row.experiment_json)
    payload: dict[str, Any] = {
        "task_info": {
            "external_task_id": row.external_task_id,
            "task_name": row.task_name,
            "task_type": row.task_type,
            "source_package_id": str(row.source_package_id or "").strip() or None,
            "platform_status": row.platform_status or PLATFORM_STATUS_ACCEPTED,
        },
        "target": _externalize_model_id_keys(target),
        "data_window": data_window,
        "threshold_contract": _threshold_contract_payload(),
        "dataset_summary": _compact_payload_dict(
            {
                "sample_count": _safe_int(
                    result.get("selected_sample_count")
                    or result.get("accepted_sample_count")
                    or result.get("completed_sample_count")
                ),
                "matched_import_job_ids": result.get("matched_import_job_ids"),
                "matched_task_ids": result.get("matched_task_ids"),
                "selection_mode": result.get("sample_selection_mode"),
                "incompatible_task_ids": result.get("incompatible_task_ids"),
            }
        ),
    }

    if row.task_type == THRESHOLD_TASK_TYPE:
        candidate_jobs = _result_edge_jobs(result)
        baseline_job = (
            result.get("baseline_edge_validation_job")
            if isinstance(result.get("baseline_edge_validation_job"), dict)
            else None
        )
        source_edge_metrics = _summarize_edge_job_collection(
            [baseline_job] if baseline_job else []
        )
        candidate_edge_metrics = _summarize_edge_job_collection(candidate_jobs)
        recommendation = (
            result.get("threshold_recommendation")
            if isinstance(result.get("threshold_recommendation"), dict)
            else {}
        )
        candidate_export_id = _safe_int(
            result.get("derived_export_id") or row.derived_export_id
        )
        current_business_thresholds = (
            experiment.get("current_thresholds")
            if isinstance(experiment.get("current_thresholds"), dict)
            else None
        )
        recommended_business_thresholds = (
            recommendation.get("recommended_thresholds")
            if isinstance(recommendation.get("recommended_thresholds"), dict)
            else None
        )
        current_validation_thresholds = (
            candidate_edge_metrics.get("validation_thresholds")
            if isinstance(candidate_edge_metrics, dict)
            else None
        )
        recommended_validation_thresholds = _job_threshold_payload(
            recommendation.get("internal_recommended_thresholds")
            if isinstance(recommendation.get("internal_recommended_thresholds"), dict)
            else None
        )
        payload.update(
            {
                "business_thresholds": _threshold_section_payload(
                    current=current_business_thresholds,
                    recommended=recommended_business_thresholds,
                    effective=(
                        recommended_business_thresholds
                        or current_business_thresholds
                    ),
                    goal=recommendation.get("goal") or experiment.get("goal"),
                    recommendation_source=recommendation.get(
                        "recommendation_source"
                    ),
                ),
                "validation_thresholds": _threshold_section_payload(
                    current=current_validation_thresholds,
                    recommended=recommended_validation_thresholds,
                    effective=(
                        recommended_validation_thresholds
                        or current_validation_thresholds
                    ),
                ),
                "source_model": _compact_payload_dict(
                    {
                        "model_id": target.get("model_id"),
                        "train_job_id": _safe_int(
                            result.get("source_train_job_id") or row.source_train_job_id
                        ),
                        "export_id": _safe_int(
                            result.get("source_export_id") or row.source_export_id
                        ),
                    }
                ),
                "candidate_model": _candidate_model_payload(
                    db,
                    task_type=row.task_type,
                    base={
                        "model_id": target.get("model_id"),
                        "export_id": candidate_export_id,
                    },
                    export_id=candidate_export_id,
                    publish_job=None,
                ),
                "source_edge_metrics": source_edge_metrics,
                "candidate_edge_metrics": candidate_edge_metrics,
                "threshold_analysis": recommendation.get("formal_analysis"),
                "comparison": _threshold_comparison_payload(
                    source_edge_metrics=source_edge_metrics,
                    candidate_edge_metrics=candidate_edge_metrics,
                ),
            }
        )
    else:
        source_training_metrics = _load_training_job_summary(
            db,
            job_id=_safe_int(
                result.get("source_train_job_id") or row.source_train_job_id
            ),
        )
        candidate_training_metrics = _summarize_training_job_payload(
            (_result_model_jobs(result) or [None])[-1]
        )
        source_edge_metrics = _summarize_edge_job_collection(
            [
                job
                for job in (result.get("source_model_edge_validation_jobs") or [])
                if isinstance(job, dict)
            ]
        )
        candidate_edge_metrics = _summarize_edge_job_collection(
            [
                job
                for job in (result.get("edge_validation_jobs") or [])
                if isinstance(job, dict)
            ]
        )
        comparison = (
            result.get("comparison")
            if isinstance(result.get("comparison"), dict)
            else {}
        )
        source_score = _safe_float(
            comparison.get("source_edge_summary", {}).get("avg_score")
        )
        candidate_score = _safe_float(
            comparison.get("candidate_edge_summary", {}).get("avg_score")
        )
        delta_score = None
        if source_score is not None and candidate_score is not None:
            delta_score = round(candidate_score - source_score, 6)
        publish_job = (
            result.get("publish_job")
            if isinstance(result.get("publish_job"), dict)
            else None
        )
        candidate_export_id = _safe_int(
            result.get("derived_export_id") or row.derived_export_id
        )
        threshold_recommendation = (
            result.get("threshold_recommendation")
            if isinstance(result.get("threshold_recommendation"), dict)
            else {}
        )
        current_business_thresholds = (
            threshold_recommendation.get("current_thresholds")
            if isinstance(threshold_recommendation.get("current_thresholds"), dict)
            else None
        )
        recommended_business_thresholds = (
            threshold_recommendation.get("recommended_thresholds")
            if isinstance(
                threshold_recommendation.get("recommended_thresholds"), dict
            )
            else None
        )
        current_validation_thresholds = (
            candidate_edge_metrics.get("validation_thresholds")
            if isinstance(candidate_edge_metrics, dict)
            else None
        )
        recommended_validation_thresholds = _job_threshold_payload(
            threshold_recommendation.get("internal_recommended_thresholds")
            if isinstance(
                threshold_recommendation.get("internal_recommended_thresholds"), dict
            )
            else None
        )
        payload.update(
            {
                "business_thresholds": _threshold_section_payload(
                    current=current_business_thresholds,
                    recommended=recommended_business_thresholds,
                    effective=(
                        recommended_business_thresholds
                        or current_business_thresholds
                    ),
                    goal=threshold_recommendation.get("goal"),
                    recommendation_source=threshold_recommendation.get(
                        "recommendation_source"
                    ),
                ),
                "source_model": _compact_payload_dict(
                    {
                        "model_id": target.get("model_id"),
                        "task_id": _safe_int(result.get("source_task_id")),
                        "train_job_id": _safe_int(
                            result.get("source_train_job_id") or row.source_train_job_id
                        ),
                        "export_id": _safe_int(
                            result.get("source_export_id") or row.source_export_id
                        ),
                        "training_metrics": source_training_metrics,
                    }
                ),
                "candidate_model": _candidate_model_payload(
                    db,
                    task_type=row.task_type,
                    base={
                        "derived_task_id": _safe_int(
                            result.get("derived_task_id") or row.labelu_task_id
                        ),
                        "derived_task_name": result.get("derived_task_name"),
                        "train_job_id": _safe_int(
                            result.get("derived_train_job_id")
                            or row.derived_train_job_id
                        ),
                        "export_id": candidate_export_id,
                        "training_metrics": candidate_training_metrics,
                    },
                    export_id=candidate_export_id,
                    publish_job=publish_job,
                ),
                "optimization": _optimization_payload(
                    experiment=experiment,
                    result=result,
                    candidate_training_metrics=candidate_training_metrics,
                ),
                "validation_thresholds": _threshold_section_payload(
                    current=current_validation_thresholds,
                    recommended=recommended_validation_thresholds,
                    effective=(
                        recommended_validation_thresholds
                        or current_validation_thresholds
                    ),
                ),
                "source_edge_metrics": source_edge_metrics,
                "candidate_edge_metrics": candidate_edge_metrics,
                "threshold_analysis": threshold_recommendation.get("formal_analysis"),
                "comparison": _compact_payload_dict(
                    {
                        "passed": comparison.get("passed"),
                        "reason": comparison.get("reason"),
                        "source_score": source_score,
                        "candidate_score": candidate_score,
                        "delta_score": delta_score,
                        "source_train_score": _safe_float(
                            comparison.get("source_train_score")
                        ),
                        "candidate_train_score": _safe_float(
                            comparison.get("candidate_train_score")
                        ),
                        "quality_gate": comparison.get("quality_gate"),
                    }
                ),
                "publish": _publish_summary_payload(publish_job),
                "threshold_binding": (
                    result.get("threshold_binding")
                    if isinstance(result.get("threshold_binding"), dict)
                    else None
                ),
            }
        )

    return _externalize_model_id_keys(_compact_payload_dict(payload))


def _outbound_callback_payload(
    row: StrategyTaskIntegration,
    *,
    status_value: str,
    result_payload: dict[str, Any] | None = None,
    error_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": row.schema_version or "v1",
        "callback_event_id": str(
            getattr(row, "outbound_callback_event_id", None)
            or f"strategy-auto-{int(row.id)}"
        ),
        "external_task_id": row.external_task_id,
        "task_type": row.task_type,
        "status": status_value,
        "finished_at": _now_text(),
    }
    if result_payload is not None:
        payload["result"] = result_payload
    if error_payload is not None:
        payload["error"] = error_payload
    return payload


def _outbound_callback_request_kwargs(payload: dict[str, Any]) -> dict[str, Any]:
    secret = str(settings.STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET or "")
    if not secret:
        if settings.STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS:
            return {"json": payload, "timeout": 30}
        raise StrategyEvolutionError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "策略演化回调 HMAC 密钥未配置，且未启用无签名兼容模式",
        )

    timestamp = str(int(time.time()))
    event_id = str(payload.get("callback_event_id") or "")
    body = json.dumps(
        jsonable_encoder(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signed_content = b".".join(
        (timestamp.encode("utf-8"), event_id.encode("utf-8"), body)
    )
    signature = hmac.new(
        secret.encode("utf-8"),
        signed_content,
        hashlib.sha256,
    ).hexdigest()
    return {
        "data": body,
        "headers": {
            "Content-Type": "application/json",
            CALLBACK_TIMESTAMP_HEADER: timestamp,
            CALLBACK_EVENT_ID_HEADER: event_id,
            CALLBACK_SIGNATURE_HEADER: f"sha256={signature}",
        },
        "timeout": 30,
    }


def verify_platform_callback_request(
    *,
    payload: dict[str, Any],
    raw_body: bytes,
    headers: Any,
) -> None:
    secret = str(settings.STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET or "")
    if not secret:
        if settings.STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS:
            return
        raise StrategyEvolutionError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "策略演化回调 HMAC 密钥未配置，拒绝无签名请求",
        )

    timestamp_text = str(headers.get(CALLBACK_TIMESTAMP_HEADER) or "").strip()
    event_id = str(headers.get(CALLBACK_EVENT_ID_HEADER) or "").strip()
    signature_header = str(headers.get(CALLBACK_SIGNATURE_HEADER) or "").strip()
    payload_event_id = str(
        payload.get("callback_event_id") or payload.get("event_id") or ""
    ).strip()
    if not timestamp_text or not event_id or not signature_header:
        raise StrategyEvolutionError(401, "第三方回调缺少 HMAC 签名头")
    if not payload_event_id or event_id != payload_event_id:
        raise StrategyEvolutionError(401, "第三方回调事件 ID 与签名头不一致")
    try:
        timestamp = int(timestamp_text)
    except ValueError as exc:
        raise StrategyEvolutionError(401, "第三方回调时间戳无效") from exc
    max_skew = int(settings.STRATEGY_EVOLUTION_CALLBACK_HMAC_MAX_SKEW_SECONDS)
    if abs(int(time.time()) - timestamp) > max_skew:
        raise StrategyEvolutionError(401, "第三方回调签名已过期")

    signed_content = b".".join(
        (timestamp_text.encode("utf-8"), event_id.encode("utf-8"), raw_body)
    )
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        signed_content,
        hashlib.sha256,
    ).hexdigest()
    provided_signature = (
        signature_header[7:]
        if signature_header.lower().startswith("sha256=")
        else ""
    )
    if not provided_signature or not hmac.compare_digest(
        provided_signature.lower(), expected_signature
    ):
        raise StrategyEvolutionError(401, "第三方回调 HMAC 签名无效")


def _send_outbound_callback(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    result_payload: dict[str, Any] | None = None,
    error_payload: dict[str, Any] | None = None,
) -> tuple[str, str | None]:
    callback_url = _resolved_outbound_callback_url(row)
    attempts = int(row.outbound_callback_attempts or 0) + 1
    callback_event_id = str(
        getattr(row, "outbound_callback_event_id", None)
        or f"strategy-auto-{int(row.id)}"
    )
    if not callback_url:
        with begin_transaction(db):
            crud_strategy_task_integration.record_outbound_callback_result(
                db=db,
                row=row,
                callback_status="skipped",
                callback_attempts=attempts,
                callback_last_error="strategy evolution outbound callback url is not configured",
                callback_event_id=callback_event_id,
                callback_next_retry_at=None,
            )
        return "skipped", "strategy evolution outbound callback url is not configured"

    status_value = "failed" if error_payload else "succeeded"
    payload = _outbound_callback_payload(
        row,
        status_value=status_value,
        result_payload=result_payload,
        error_payload=error_payload,
    )
    try:
        response = requests.post(
            callback_url,
            **_outbound_callback_request_kwargs(payload),
        )
        response.raise_for_status()
    except Exception as exc:
        error_message = str(exc)
        retry_seconds = min(
            int(settings.STRATEGY_EVOLUTION_CALLBACK_RETRY_MAX_SECONDS),
            int(settings.STRATEGY_EVOLUTION_CALLBACK_RETRY_BASE_SECONDS)
            * (2 ** max(0, attempts - 1)),
        )
        next_retry_at = datetime.now() + timedelta(seconds=retry_seconds)
        with begin_transaction(db):
            crud_strategy_task_integration.record_outbound_callback_result(
                db=db,
                row=row,
                callback_status="failed",
                callback_attempts=attempts,
                callback_last_error=error_message[:4000],
                callback_url=callback_url,
                callback_event_id=callback_event_id,
                callback_next_retry_at=next_retry_at,
            )
        return "failed", error_message[:4000]

    with begin_transaction(db):
        crud_strategy_task_integration.record_outbound_callback_result(
            db=db,
            row=row,
            callback_status="succeeded",
            callback_attempts=attempts,
            callback_last_error=None,
            callback_url=callback_url,
            callback_event_id=callback_event_id,
            callback_next_retry_at=None,
        )
    return "succeeded", None


def _parse_datetime(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for candidate, fmt in (
        (text, "%Y-%m-%d %H:%M:%S"),
        (text, "%Y-%m-%dT%H:%M:%S"),
        (text[:10], "%Y-%m-%d"),
    ):
        try:
            return datetime.strptime(candidate, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _source_payload(row: ExperimentDatasetImport) -> dict[str, Any]:
    return _json_loads_object(row.source_payload_json)


def _window_overlaps(
    *,
    request_start: str,
    request_end: str,
    import_start: str,
    import_end: str,
) -> bool:
    req_start_dt = _parse_datetime(request_start)
    req_end_dt = _parse_datetime(request_end)
    import_start_dt = _parse_datetime(import_start)
    import_end_dt = _parse_datetime(import_end)
    if req_start_dt and req_end_dt and import_start_dt and import_end_dt:
        return import_start_dt < req_end_dt and import_end_dt > req_start_dt
    return bool(
        import_start
        and import_end
        and import_start == request_start
        and import_end == request_end
    )


def _target_field_matches(
    source_target: dict[str, Any], key: str, expected: str
) -> bool:
    source_value = str(source_target.get(key) or "").strip()
    expected_value = str(expected or "").strip()
    return not source_value or not expected_value or source_value == expected_value


def _matching_import_rows(
    db: Session,
    *,
    cmd: CreateStrategyEvolutionTaskCommand,
) -> list[ExperimentDatasetImport]:
    source_package_id = str(cmd.source_package_id or "").strip()
    if source_package_id:
        row = _validate_source_package(db, cmd=cmd, package_id=source_package_id)
        return [row]

    rows = (
        db.query(ExperimentDatasetImport)
        .filter(
            ExperimentDatasetImport.deleted_at.is_(None),
            ExperimentDatasetImport.status.in_(IMPORT_READY_STATUSES),
            ExperimentDatasetImport.event_type == cmd.target.event_type,
        )
        .order_by(ExperimentDatasetImport.id.desc())
        .all()
    )
    matched: list[ExperimentDatasetImport] = []
    for row in rows:
        if row.model_id and row.model_id != cmd.target.model_id:
            continue
        source = _source_payload(row)
        source_target = (
            source.get("target") if isinstance(source.get("target"), dict) else {}
        )
        if not all(
            (
                _target_field_matches(
                    source_target, "device_type", cmd.target.device_type
                ),
                _target_field_matches(
                    source_target, "scene_type", cmd.target.scene_type
                ),
                _target_field_matches(
                    source_target, "strategy_id", cmd.target.strategy_id
                ),
            )
        ):
            continue
        if not _window_overlaps(
            request_start=cmd.data_window.start_time,
            request_end=cmd.data_window.end_time,
            import_start=str(source.get("window_start") or ""),
            import_end=str(source.get("window_end") or ""),
        ):
            continue
        matched.append(row)
    return matched


def _validate_source_package(
    db: Session,
    *,
    cmd: CreateStrategyEvolutionTaskCommand,
    package_id: str,
) -> ExperimentDatasetImport:
    row = crud_experiment_dataset_import.get_by_package_id(db, package_id=package_id)
    if not row:
        raise StrategyEvolutionError(
            404,
            f"未找到专家回流数据包：{package_id}",
        )
    if str(row.status or "").strip() not in IMPORT_READY_STATUSES:
        raise StrategyEvolutionError(
            409,
            f"专家回流数据包尚未完成导入：package_id={package_id}，当前状态为 {row.status}",
        )
    if str(row.event_type or "").strip() and row.event_type != cmd.target.event_type:
        raise StrategyEvolutionError(
            400,
            f"专家回流数据包事件类型与策略任务不一致：package_id={package_id}",
        )
    if row.model_id and row.model_id != cmd.target.model_id:
        raise StrategyEvolutionError(
            400,
            f"专家回流数据包模型 ID 与策略任务不一致：package_id={package_id}",
        )

    source = _source_payload(row)
    source_target = source.get("target") if isinstance(source.get("target"), dict) else {}
    for key, expected in (
        ("event_type", cmd.target.event_type),
        ("device_type", cmd.target.device_type),
        ("scene_type", cmd.target.scene_type),
        ("strategy_id", cmd.target.strategy_id),
    ):
        if not _target_field_matches(source_target, key, expected):
            raise StrategyEvolutionError(
                400,
                f"专家回流数据包目标字段与策略任务不一致：{key}，package_id={package_id}",
            )
    if not _window_overlaps(
        request_start=cmd.data_window.start_time,
        request_end=cmd.data_window.end_time,
        import_start=str(source.get("window_start") or ""),
        import_end=str(source.get("window_end") or ""),
    ):
        raise StrategyEvolutionError(
            400,
            f"专家回流数据包时间范围与策略任务不重叠：package_id={package_id}",
        )
    return row


def _parse_int_ids(raw: str | None) -> list[int]:
    out: list[int] = []
    for item in _json_loads_list(raw):
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in out:
            out.append(value)
    return out


def _task_ids_from_imports(rows: list[ExperimentDatasetImport]) -> list[int]:
    task_ids: list[int] = []
    for row in rows:
        for task_id in _parse_int_ids(row.formal_task_ids_json):
            if task_id not in task_ids:
                task_ids.append(task_id)
        if row.draft_task_id and int(row.draft_task_id) not in task_ids:
            task_ids.append(int(row.draft_task_id))
    return task_ids


def _linked_task_ids_for_import(
    db: Session,
    *,
    row: ExperimentDatasetImport,
) -> list[int]:
    task_ids = _task_ids_from_imports([row])
    package_id = str(row.package_id or "").strip()
    if not package_id:
        return task_ids

    linked_rows = (
        db.query(Task.id)
        .filter(
            Task.deleted_at.is_(None),
            Task.description.isnot(None),
            Task.description.like(f"%package_id={package_id}%"),
            Task.description.like("%source=experiment_dataset_%"),
        )
        .order_by(Task.id.asc())
        .all()
    )
    for task_id, in linked_rows:
        value = _safe_int(task_id)
        if value and value not in task_ids:
            task_ids.append(int(value))
    return task_ids


def _metadata_samples(row: ExperimentDatasetImport) -> list[dict[str, Any]]:
    metadata = _json_loads_object(row.metadata_json)
    samples = metadata.get("samples")
    if isinstance(samples, list):
        return [sample for sample in samples if isinstance(sample, dict)]
    if isinstance(samples, dict):
        normalized = []
        for key, sample in samples.items():
            if not isinstance(sample, dict):
                continue
            row = dict(sample)
            image = row.get("image") if isinstance(row.get("image"), dict) else {}
            if not image.get("file_name"):
                image = dict(image)
                image["file_name"] = str(key)
                row["image"] = image
            normalized.append(row)
        return normalized
    return []


def _metadata_sample_value(sample: dict[str, Any], key: str) -> str:
    value = sample.get(key)
    if value is not None:
        return str(value).strip()
    target = sample.get("target") if isinstance(sample.get("target"), dict) else {}
    value = target.get(key)
    if value is not None:
        return str(value).strip()
    event_report = (
        sample.get("event_report")
        if isinstance(sample.get("event_report"), dict)
        else {}
    )
    value = event_report.get(key)
    return str(value).strip() if value is not None else ""


def _metadata_sample_event_time(sample: dict[str, Any]) -> str:
    for key in ("event_time", "reviewed_at", "created_at"):
        value = _metadata_sample_value(sample, key)
        if value:
            return value
    selection = (
        sample.get("selection") if isinstance(sample.get("selection"), dict) else {}
    )
    return str(selection.get("start_time") or "").strip()


def _datetime_in_window(value: str, *, start: str, end: str) -> bool | None:
    event_dt = _parse_datetime(value)
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if not event_dt or not start_dt or not end_dt:
        return None
    return start_dt <= event_dt < end_dt


def _metadata_sample_target_matches(
    *,
    sample: dict[str, Any],
    cmd: CreateStrategyEvolutionTaskCommand,
) -> bool:
    checks = (
        ("event_type", cmd.target.event_type),
        ("device_type", cmd.target.device_type),
        ("scene_type", cmd.target.scene_type),
        ("strategy_id", cmd.target.strategy_id),
        ("model_id", cmd.target.model_id),
    )
    for key, expected in checks:
        actual = _metadata_sample_value(sample, key)
        if actual and expected and actual != expected:
            return False
    in_window = _datetime_in_window(
        _metadata_sample_event_time(sample),
        start=cmd.data_window.start_time,
        end=cmd.data_window.end_time,
    )
    return in_window is not False


def _normalize_object_key(value: str) -> str:
    return str(value or "").strip().replace("\\", "/")


def _basename(value: str) -> str:
    return _normalize_object_key(value).rsplit("/", 1)[-1]


def _metadata_sample_match_keys(sample: dict[str, Any]) -> set[str]:
    values: list[str] = []
    image = sample.get("image") if isinstance(sample.get("image"), dict) else {}
    for key in ("file_name", "object_key", "source_object_key"):
        values.append(str(image.get(key) or ""))
    for key in (
        "dataset_file_name",
        "dataset_object_key",
        "source_object_key",
        "file_name",
        "filename",
        "image_name",
    ):
        values.append(str(sample.get(key) or ""))

    keys: set[str] = set()
    for value in values:
        normalized = _normalize_object_key(value)
        if not normalized:
            continue
        keys.add(normalized)
        keys.add(_basename(normalized))
    return keys


def _sample_match_keys(sample) -> set[str]:
    attachment = getattr(sample, "file", None)
    values = [
        str(getattr(attachment, "filename", "") or ""),
        str(getattr(attachment, "path", "") or ""),
        str(getattr(attachment, "url", "") or ""),
    ]
    keys: set[str] = set()
    for value in values:
        normalized = _normalize_object_key(value)
        if not normalized:
            continue
        keys.add(normalized)
        keys.add(_basename(normalized))
    return keys


def _done_sample_ids_by_task(db: Session, task_ids: list[int]) -> dict[int, list[int]]:
    from labelu.internal.adapter.persistence import crud_sample

    selected: dict[int, list[int]] = {}
    max_images = int(settings.YOLO_EDGE_VALIDATION_MAX_IMAGES or 50)
    for task_id in task_ids:
        samples = crud_sample.list_by_task(db, task_id=task_id)
        sample_ids = [
            int(sample.id)
            for sample in samples
            if str(getattr(sample, "state", "") or "") == SampleState.DONE.value
        ]
        if sample_ids:
            selected[int(task_id)] = sample_ids[:max_images]
    return selected


def _select_done_sample_ids_from_imports(
    db: Session,
    *,
    imports: list[ExperimentDatasetImport],
    cmd: CreateStrategyEvolutionTaskCommand,
) -> tuple[dict[int, list[int]], list[dict[str, Any]], str]:
    from labelu.internal.adapter.persistence import crud_sample

    max_images = int(settings.YOLO_EDGE_VALIDATION_MAX_IMAGES or 50)
    selected: dict[int, list[int]] = {}
    selected_details: list[dict[str, Any]] = []
    saw_metadata = False

    for import_row in imports:
        task_ids = _linked_task_ids_for_import(db, row=import_row)
        samples_by_task = {
            task_id: crud_sample.list_by_task(db, task_id=task_id)
            for task_id in task_ids
        }
        sample_index_by_task: dict[int, dict[str, Any]] = {}
        for task_id, samples in samples_by_task.items():
            sample_index: dict[str, Any] = {}
            for sample in samples:
                for key in _sample_match_keys(sample):
                    sample_index.setdefault(key, sample)
            sample_index_by_task[task_id] = sample_index

        metadata_samples = _metadata_samples(import_row)
        if not metadata_samples:
            continue
        saw_metadata = True
        for metadata_sample in metadata_samples:
            if not _metadata_sample_target_matches(sample=metadata_sample, cmd=cmd):
                continue
            match_keys = _metadata_sample_match_keys(metadata_sample)
            if not match_keys:
                continue
            for task_id, sample_index in sample_index_by_task.items():
                matched_sample = None
                for key in match_keys:
                    matched_sample = sample_index.get(key)
                    if matched_sample is not None:
                        break
                if matched_sample is None:
                    continue
                if (
                    str(getattr(matched_sample, "state", "") or "")
                    != SampleState.DONE.value
                ):
                    continue
                bucket = selected.setdefault(int(task_id), [])
                if int(matched_sample.id) in bucket:
                    continue
                if len(bucket) >= max_images:
                    continue
                bucket.append(int(matched_sample.id))
                selected_details.append(
                    {
                        "import_job_id": int(import_row.id),
                        "package_id": import_row.package_id,
                        "task_id": int(task_id),
                        "sample_id": int(matched_sample.id),
                        "file_name": _basename(next(iter(match_keys))),
                        "event_time": _metadata_sample_event_time(metadata_sample),
                    }
                )

    fallback_task_ids: list[int] = []
    for import_row in imports:
        for task_id in _linked_task_ids_for_import(db, row=import_row):
            if task_id not in fallback_task_ids:
                fallback_task_ids.append(task_id)
    if saw_metadata and selected:
        return selected, selected_details, "metadata_sample_event_time"

    selected = _done_sample_ids_by_task(db, fallback_task_ids)
    for task_id, sample_ids in selected.items():
        for sample_id in sample_ids:
            selected_details.append(
                {
                    "task_id": int(task_id),
                    "sample_id": int(sample_id),
                    "selection_warning": (
                        "metadata_sample_match_missing_package_window_fallback"
                        if saw_metadata
                        else "metadata_json_missing_package_window_fallback"
                    ),
                }
            )
    return (
        selected,
        selected_details,
        "package_window_done_fallback"
        if saw_metadata
        else "package_window_fallback",
    )


def _model_id_candidates(model_id: str) -> tuple[list[str], list[int]]:
    text = str(model_id or "").strip()
    strings = [text] if text else []
    integers: list[int] = []
    for match in re.findall(r"^\d+$", text):
        try:
            value = int(match)
        except ValueError:
            continue
        if value not in integers:
            integers.append(value)
        if match not in strings:
            strings.append(match)
    return strings, integers


def _published_model_set_id_from_publish_job(job: dict[str, Any]) -> int | None:
    texts: list[str] = []
    for item in job.get("log_lines") or []:
        if isinstance(item, str):
            texts.append(item)
    for item in job.get("steps") or []:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            for key in ("detail", "label"):
                value = item.get(key)
                if isinstance(value, str):
                    texts.append(value)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    for item in result.get("steps") or []:
        if isinstance(item, str):
            texts.append(item)
    for text in reversed(texts):
        match = re.search(r"最新模型集\s*id\s*=\s*(\d+)", text)
        if not match:
            continue
        try:
            return int(match.group(1))
        except ValueError:
            continue

    for key in ("model_id", "model_set_id"):
        value = result.get(key)
        if value is None:
            continue
        match = re.search(r"\d+", str(value))
        if not match:
            continue
        try:
            return int(match.group(0))
        except ValueError:
            continue
    return None


def _resolve_export_id_from_model_id(
    db: Session, model_id: str
) -> tuple[int | None, dict[str, Any]]:
    from labelu.internal.application.service import published_model_catalog

    try:
        catalog_row = published_model_catalog.resolve_model(db, model_id)
    except LabelUException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
        catalog_row = None
    if catalog_row is not None and catalog_row.export_id:
        return int(catalog_row.export_id), {
            "model_id": model_id,
            "resolved_by": "published_model_catalog",
            "catalog_id": int(catalog_row.id),
            "published_model_id": catalog_row.aicm_model_set_id,
            "export_id": int(catalog_row.export_id),
            "source_train_job_id": int(catalog_row.yolo_train_job_id or 0),
            "source_task_id": int(catalog_row.task_id or 0),
        }
    string_candidates, int_candidates = _model_id_candidates(model_id)
    if not string_candidates and not int_candidates:
        return None, {"model_id": model_id, "reason": "empty model_id"}

    for candidate in int_candidates:
        export_row = crud_yolo_bm1688_export.get_by_published_model_id(db, candidate)
        if export_row:
            return int(export_row.id), {
                "model_id": model_id,
                "resolved_by": "export_row_published_model_id",
                "published_model_id": int(candidate),
                "export_id": int(export_row.id),
                "source_train_job_id": int(getattr(export_row, "yolo_train_job_id", 0) or 0),
                "source_task_id": int(getattr(export_row, "task_id", 0) or 0),
            }

    page = 0
    while True:
        jobs, total = aicm_publish_job.list_jobs(page=page, size=100)
        for job in jobs:
            result = job.get("result") if isinstance(job.get("result"), dict) else {}
            if not result or not result.get("ok"):
                continue
            model_set_id = _published_model_set_id_from_publish_job(job)
            model_set_match = bool(model_set_id and model_set_id in int_candidates)
            model_string_match = any(
                str(result.get(key) or "").strip() in string_candidates
                for key in ("model_id",)
            )
            if model_set_match or model_string_match:
                export_id = int(result.get("export_id") or job.get("export_id") or 0)
                if export_id > 0:
                    if model_set_id:
                        export_row = crud_yolo_bm1688_export.get(db, export_id)
                        if (
                            export_row
                            and int(getattr(export_row, "published_model_id", 0) or 0)
                            != int(model_set_id)
                        ):
                            crud_yolo_bm1688_export.update_row(
                                db,
                                export_row,
                                published_model_id=int(model_set_id),
                            )
                    return export_id, {
                        "model_id": model_id,
                        "resolved_by": "aicm_publish_job",
                        "publish_job_id": job.get("job_id"),
                        "model_set_id": model_set_id,
                    }
        if (page + 1) * 100 >= total:
            break
        page += 1

    if len(string_candidates) == 1 and string_candidates[0].isdigit():
        export_id = int(string_candidates[0])
        if crud_yolo_bm1688_export.get(db, export_id):
            return export_id, {
                "model_id": model_id,
                "resolved_by": "direct_export_id",
            }

    return None, {
        "model_id": model_id,
        "reason": "no published model mapping found",
        "candidates": string_candidates,
    }


def _edge_thresholds_from_experiment(
    experiment: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    if not isinstance(experiment, dict):
        return {}, "strategy_evolution"
    for key in ("edge_validation_thresholds", "thresholds"):
        value = experiment.get(key)
        if isinstance(value, dict):
            thresholds = dict(value)
            thresholds["conf_thres"] = min(
                float(thresholds.get("conf_thres") or FORMAL_THRESHOLD_SCAN_CONF),
                FORMAL_THRESHOLD_SCAN_CONF,
            )
            thresholds["match_iou_thres"] = FORMAL_THRESHOLD_MATCH_IOU
            return thresholds, "strategy_evolution_formal_threshold_scan"
    current = experiment.get("current_thresholds")
    if isinstance(current, dict):
        supported = {
            "conf_thres": FORMAL_THRESHOLD_SCAN_CONF,
            "match_iou_thres": FORMAL_THRESHOLD_MATCH_IOU,
        }
        if "nms_thres" in current:
            supported["nms_thres"] = current["nms_thres"]
        return supported, "strategy_evolution_formal_threshold_scan"
    return {
        "conf_thres": FORMAL_THRESHOLD_SCAN_CONF,
        "match_iou_thres": FORMAL_THRESHOLD_MATCH_IOU,
    }, "strategy_evolution_formal_threshold_scan"


def _existing_edge_validation_result(
    row: StrategyTaskIntegration | None,
) -> dict[str, Any] | None:
    if not row:
        return None
    result = _json_loads_object(row.platform_result_json)
    jobs = result.get("edge_validation_jobs")
    if isinstance(jobs, list) and jobs:
        return result
    return None


def _result_edge_jobs(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    for key in ("edge_validation_jobs", "edge_experiment_jobs"):
        jobs = result.get(key)
        if isinstance(jobs, list):
            return [job for job in jobs if isinstance(job, dict)]
    return []


def _existing_model_training_result(
    row: StrategyTaskIntegration | None,
) -> dict[str, Any] | None:
    if not row:
        return None
    result = _json_loads_object(row.platform_result_json)
    jobs = result.get("model_training_jobs")
    if isinstance(jobs, list) and jobs:
        return result
    return None


def _result_model_jobs(result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    jobs = result.get("model_training_jobs")
    if isinstance(jobs, list):
        return [job for job in jobs if isinstance(job, dict)]
    return []


def _should_use_cached_task_detail(
    row: StrategyTaskIntegration | None,
) -> bool:
    if not row:
        return False
    status_value = str(row.platform_status or "").strip()
    if status_value not in (
        FINAL_PLATFORM_STATUSES
        | {"waiting_for_model", "waiting_for_annotations", "accepted_not_implemented"}
    ):
        return False
    result = _json_loads_object(row.platform_result_json)
    return bool(result)


def _model_dump_json_compat(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _edge_job_payload_from_detail(
    detail: Any,
    *,
    previous_payload: dict[str, Any] | None = None,
    start_status: str | None = None,
    start_error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous_payload = previous_payload or {}
    payload = {
        "job_id": int(detail.id),
        "export_id": int(detail.export_id),
        "task_id": int(detail.task_id),
        "status": detail.status,
        "mode": detail.mode,
        "image_count": int(detail.image_count or 0),
        "labeled_image_count": int(detail.labeled_image_count or 0),
        "unlabeled_image_count": int(detail.unlabeled_image_count or 0),
        "threshold_source": detail.threshold_source,
        "workflow_stage": detail.workflow_stage,
        "edge_result_status": detail.edge_result_status,
        "report_summary": detail.report_summary,
        "progress": _model_dump_json_compat(detail.progress),
        "thresholds": _model_dump_json_compat(detail.thresholds),
        "ai_thresholds": detail.ai_thresholds,
        "result": detail.result,
        "error_message": detail.error_message,
        "started_at": _model_dump_json_compat(detail.started_at),
        "completed_at": _model_dump_json_compat(detail.completed_at),
        "sample_ids": list(detail.sample_ids or []),
        "attachment_ids": list(detail.attachment_ids or []),
    }
    effective_start_status = (
        str(start_status or "").strip()
        or str(previous_payload.get("start_status") or "").strip()
    )
    if not effective_start_status:
        effective_start_status = (
            "started"
            if str(detail.status or "").strip().upper()
            in {"PROCESSING", "COMPLETED", "PARTIAL_COMPLETED"}
            else "pending"
        )
    payload["start_status"] = effective_start_status
    effective_start_error = (
        start_error
        if isinstance(start_error, dict)
        else (
            previous_payload.get("start_error")
            if isinstance(previous_payload.get("start_error"), dict)
            else None
        )
    )
    if effective_start_error and effective_start_status == "failed":
        payload["start_error"] = effective_start_error
    return payload


def _model_job_payload_from_detail(
    detail: Any,
    *,
    previous_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    previous_payload = previous_payload or {}
    payload = {
        "job_id": int(detail.id),
        "task_id": int(detail.task_id),
        "status": str(detail.status or "").strip(),
        "category": detail.category,
        "model_weights": detail.model_weights,
        "epochs": detail.epochs,
        "imgsz": detail.imgsz,
        "hyperparams": _model_dump_json_compat(getattr(detail, "hyperparams", None)),
        "execution_mode": getattr(detail, "execution_mode", None),
        "train_node_id": getattr(detail, "train_node_id", None),
        "merge_task_ids": list(detail.merge_task_ids or []),
        "merge_task_sample_counts": detail.merge_task_sample_counts,
        "train_sample_count": detail.train_sample_count,
        "val_sample_count": detail.val_sample_count,
        "test_sample_count": detail.test_sample_count,
        "total_sample_count": detail.total_sample_count,
        "weights_path": detail.weights_path,
        "error_message": detail.error_message,
        "current_epoch": detail.current_epoch,
        "progress_snapshot": detail.progress_snapshot,
        "train_phase": detail.train_phase,
        "train_phase_label": detail.train_phase_label,
        "train_phase_detail": detail.train_phase_detail,
        "runtime_status": detail.runtime_status,
        "training_val_metrics": _model_dump_json_compat(detail.training_val_metrics),
        "test_val_metrics": _model_dump_json_compat(detail.test_val_metrics),
        "val_test_comparison": _model_dump_json_compat(detail.val_test_comparison),
        "quality_assessment": _model_dump_json_compat(detail.quality_assessment),
        "task_rect_preset_slug": detail.task_rect_preset_slug,
        "task_rect_classes": list(detail.task_rect_classes or []),
        "created_at": _model_dump_json_compat(detail.created_at),
        "updated_at": _model_dump_json_compat(detail.updated_at),
    }
    if previous_payload.get("created_at") and payload.get("created_at") is None:
        payload["created_at"] = previous_payload.get("created_at")
    return payload


def _should_retry_existing_edge_job(row: Any) -> bool:
    status_value = str(getattr(row, "status", "") or "").strip().upper()
    if status_value in {"PENDING", "FAILED", "PARTIAL_COMPLETED"}:
        return True
    if status_value == "PROCESSING":
        return yolo_edge_validation.is_stale_edge_validation_job(row)
    return False


def _sync_existing_edge_validation_result(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    restart_existing_jobs: bool,
) -> tuple[dict[str, Any] | None, str, bool]:
    result = _json_loads_object(row.platform_result_json)
    existing_jobs = _result_edge_jobs(result)
    if not existing_jobs:
        return (result or None), "", False

    admin = _admin_user(db)
    changed = False
    resumed_count = 0
    resumed_failed_count = 0
    synced_jobs: list[dict[str, Any]] = []

    for previous_payload in existing_jobs:
        payload = dict(previous_payload)
        job_id = int(payload.get("job_id") or 0)
        start_status = str(payload.get("start_status") or "").strip() or None
        start_error = (
            payload.get("start_error")
            if isinstance(payload.get("start_error"), dict)
            else None
        )
        if job_id <= 0:
            synced_jobs.append(payload)
            continue

        if restart_existing_jobs:
            job_row = crud_yolo_edge_validation_job.get(db, job_id)
            if job_row and _should_retry_existing_edge_job(job_row):
                try:
                    yolo_edge_validation.start_job(
                        db=db,
                        job_id=job_id,
                        current_user=admin,
                    )
                    start_status = "started"
                    start_error = None
                    resumed_count += 1
                except LabelUException as exc:
                    start_status = "failed"
                    start_error = {
                        "status_code": int(
                            getattr(exc, "status_code", status.HTTP_500_INTERNAL_SERVER_ERROR)
                        ),
                        "message": str(getattr(exc, "msg", "") or exc),
                    }
                    resumed_failed_count += 1
                except Exception as exc:
                    start_status = "failed"
                    start_error = {
                        "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                        "message": str(exc),
                    }
                    resumed_failed_count += 1

        try:
            live_detail = yolo_edge_validation.get_job(
                db=db,
                job_id=job_id,
                current_user=admin,
            )
            live_payload = _edge_job_payload_from_detail(
                live_detail,
                previous_payload=payload,
                start_status=start_status,
                start_error=start_error,
            )
        except Exception:
            live_payload = dict(payload)
            if start_status:
                live_payload["start_status"] = start_status
            if start_error and start_status == "failed":
                live_payload["start_error"] = start_error
        if live_payload != payload:
            changed = True
        synced_jobs.append(live_payload)

    result["edge_validation_jobs"] = synced_jobs
    result["edge_experiment_jobs"] = synced_jobs
    if resumed_failed_count > 0:
        message = "已有边端验证任务恢复执行失败"
    elif resumed_count > 0:
        message = "已有边端验证任务已恢复执行"
    else:
        message = "边端验证任务已存在"
    return result, message, changed or resumed_count > 0 or resumed_failed_count > 0


def _sync_existing_model_training_result(
    db: Session,
    *,
    row: StrategyTaskIntegration,
) -> tuple[dict[str, Any] | None, str, bool]:
    result = _json_loads_object(row.platform_result_json)
    existing_jobs = _result_model_jobs(result)
    if not existing_jobs:
        return (result or None), "", False

    admin = _admin_user(db)
    changed = False
    synced_jobs: list[dict[str, Any]] = []
    for previous_payload in existing_jobs:
        payload = dict(previous_payload)
        job_id = int(payload.get("job_id") or 0)
        if job_id <= 0:
            synced_jobs.append(payload)
            continue
        try:
            live_detail = yolo_train.get_train_job_by_id(
                db=db,
                job_id=job_id,
                current_user=admin,
            )
            live_payload = _model_job_payload_from_detail(
                live_detail,
                previous_payload=payload,
            )
        except LabelUException as exc:
            if int(getattr(exc, "status_code", 0) or 0) == 404:
                changed = True
                continue
            live_payload = dict(payload)
        except Exception:
            live_payload = dict(payload)
        if live_payload != payload:
            changed = True
        synced_jobs.append(live_payload)

    if not synced_jobs:
        result.pop("model_training_jobs", None)
        result["model_training_status"] = "stale_reference_cleared"
        return result, "stale model training job reference cleared", changed

    result["model_training_jobs"] = synced_jobs
    latest_job = synced_jobs[-1] if synced_jobs else {}
    latest_status = str(latest_job.get("status") or "").strip().upper()
    if latest_status == "PROCESSING":
        result["model_training_status"] = "running"
    elif latest_status == "COMPLETED":
        result["model_training_status"] = "succeeded"
    elif latest_status == "FAILED":
        result["model_training_status"] = "failed"
    elif latest_status == "CANCELLED":
        result["model_training_status"] = "cancelled"
    else:
        result["model_training_status"] = "training_job_created"
    return result, "model training job already exists", changed


def _first_text_message(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("message", "reason", "detail"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _row_last_message(
    row: StrategyTaskIntegration,
    *,
    result: dict[str, Any] | None,
    error: dict[str, Any] | None,
    platform_status: str | None = None,
) -> str:
    current_status = str(platform_status or row.platform_status or "").strip()
    jobs = _result_edge_jobs(result)
    latest_job = jobs[-1] if jobs else {}
    start_error = (
        latest_job.get("start_error")
        if isinstance(latest_job.get("start_error"), dict)
        else None
    )
    if start_error and str(start_error.get("message") or "").strip():
        return str(start_error.get("message") or "").strip()
    if str(latest_job.get("error_message") or "").strip():
        return str(latest_job.get("error_message") or "").strip()
    if str(latest_job.get("report_summary") or "").strip():
        return str(latest_job.get("report_summary") or "").strip()
    progress = (
        latest_job.get("progress") if isinstance(latest_job.get("progress"), dict) else {}
    )
    if str(progress.get("message") or "").strip():
        return str(progress.get("message") or "").strip()
    model_jobs = _result_model_jobs(result)
    latest_model_job = model_jobs[-1] if model_jobs else {}
    if str(latest_model_job.get("error_message") or "").strip():
        return str(latest_model_job.get("error_message") or "").strip()
    if str(latest_model_job.get("progress_snapshot") or "").strip():
        return str(latest_model_job.get("progress_snapshot") or "").strip()
    if str(latest_model_job.get("train_phase_detail") or "").strip():
        return str(latest_model_job.get("train_phase_detail") or "").strip()
    if str(latest_model_job.get("train_phase_label") or "").strip():
        return str(latest_model_job.get("train_phase_label") or "").strip()
    if current_status == "waiting_for_model":
        return "待发布模型映射"
    if current_status == "waiting_for_annotations":
        return "待匹配已完成标注样本"
    if current_status == "edge_validation_created":
        return "已创建边端验证任务"
    if current_status == "model_training_created":
        return "已创建模型训练任务"
    if current_status == "export_created":
        return "已创建 BM1688 导出任务"
    if current_status == "publish_created":
        return "已创建自动发布任务"
    if current_status == "comparison_failed":
        return "新模型边端验证结果未通过"
    if current_status == "callback_failed":
        return "结果回调失败"
    message = _first_text_message(error) or _first_text_message(result)
    if message:
        return message
    return ""


def _list_row_platform_status(
    row: StrategyTaskIntegration,
    *,
    result: dict[str, Any] | None,
) -> str:
    platform_status = str(row.platform_status or PLATFORM_STATUS_ACCEPTED).strip()
    if (
        row.task_type == THRESHOLD_TASK_TYPE
        and platform_status == "edge_validation_created"
        and not _safe_int((result or {}).get("derived_export_id") or row.derived_export_id)
        and not _result_edge_jobs(result)
    ):
        return "export_created"
    return platform_status


def _to_list_row(
    row: StrategyTaskIntegration,
    *,
    result_override: dict[str, Any] | None = None,
    decorate_result: bool = False,
) -> StrategyEvolutionTaskListRowResponse:
    target = _json_loads_object(row.target_json)
    data_window = _json_loads_object(row.data_window_json)
    result = result_override if result_override is not None else (_json_loads_object(row.platform_result_json) or None)
    if decorate_result:
        result = _decorate_strategy_result(result)
    error = _json_loads_object(row.platform_error_json) or None
    jobs = _result_edge_jobs(result)
    latest_job = jobs[-1] if jobs else {}
    model_jobs = _result_model_jobs(result)
    latest_model_job = model_jobs[-1] if model_jobs else {}
    platform_status = _list_row_platform_status(row, result=result)
    return StrategyEvolutionTaskListRowResponse(
        external_task_id=row.external_task_id,
        task_name=row.task_name,
        task_type=row.task_type,
        source_package_id=(str(row.source_package_id or "").strip() or None),
        source_type=str(row.source_type or "third_party"),
        source_system=str(row.source_system or "strategy_evolution"),
        source_external_task_id=row.external_task_id,
        source_external_package_id=(
            str(row.source_external_package_id or row.source_package_id or "").strip()
            or None
        ),
        created_by_username=(
            str(getattr(row.owner, "username", "") or "").strip() or None
        ),
        target=_externalize_model_id_keys(target),
        data_window=data_window,
        platform_status=platform_status,
        automation_stage=(str(row.automation_stage or "").strip() or None),
        automation_status=(str(row.automation_status or "").strip() or None),
        outbound_callback_status=(str(row.outbound_callback_status or "").strip() or None),
        outbound_callback_attempts=int(row.outbound_callback_attempts or 0),
        outbound_callback_next_retry_at=row.outbound_callback_next_retry_at,
        automation_attempts=int(row.automation_attempts or 0),
        edge_experiment_status=(
            str(result.get("edge_experiment_status") or "").strip()
            if isinstance(result, dict)
            else None
        )
        or None,
        selected_sample_count=(
            int(result.get("selected_sample_count"))
            if isinstance(result, dict)
            and result.get("selected_sample_count") is not None
            else None
        ),
        edge_job_count=len(jobs),
        latest_edge_job_id=(
            int(latest_job.get("job_id"))
            if latest_job.get("job_id") is not None
            else None
        ),
        latest_edge_job_status=(
            str(latest_job.get("status") or "").strip() or None
        ),
        latest_edge_job_start_status=(
            str(latest_job.get("start_status") or "").strip() or None
        ),
        model_job_count=len(model_jobs),
        latest_model_job_id=(
            int(latest_model_job.get("job_id"))
            if latest_model_job.get("job_id") is not None
            else None
        ),
        latest_model_job_status=(
            str(latest_model_job.get("status") or "").strip() or None
        ),
        last_message=_row_last_message(
            row,
            result=result,
            error=error,
            platform_status=platform_status,
        ),
        callback_event_id=(
            row.outbound_callback_event_id or row.platform_callback_event_id or ""
        ),
        finished_at=row.platform_finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _create_threshold_edge_jobs(
    db: Session,
    *,
    export_id: int,
    source_export_id: int,
    result: dict[str, Any],
    experiment: dict[str, Any] | None,
) -> dict[str, Any]:
    sample_ids_by_task = (
        result.get("selected_sample_ids_by_task")
        if isinstance(result.get("selected_sample_ids_by_task"), dict)
        else {}
    )
    if not sample_ids_by_task:
        return result

    admin = _admin_user(db)
    thresholds, threshold_source = _edge_thresholds_from_experiment(experiment or {})
    edge_jobs: list[dict[str, Any]] = []
    for task_id_text, raw_sample_ids in sample_ids_by_task.items():
        task_id = _safe_int(task_id_text)
        sample_ids = [_safe_int(item) for item in (raw_sample_ids or [])]
        sample_ids = [int(item) for item in sample_ids if item]
        if not task_id or not sample_ids:
            continue
        edge_cmd = CreateYoloEdgeValidationJobCommand(
            export_id=int(export_id),
            task_id=int(task_id),
            mode="run_and_eval",
            sample_ids=sample_ids,
            thresholds=thresholds,
            threshold_source=threshold_source,
        )
        job_detail = yolo_edge_validation.create_job(
            db=db,
            cmd=edge_cmd,
            current_user=admin,
        )
        job_row = crud_yolo_edge_validation_job.get(db, int(job_detail.id))
        if job_row:
            job_row.source_type = "third_party"
            job_row.source_system = "strategy_evolution"
            job_row.source_external_task_id = str(result.get("external_task_id") or "") or None
            job_row.source_external_package_id = str(result.get("source_package_id") or "") or None
            job_row.source_integration_id = _safe_int(result.get("strategy_task_integration_id"))
            db.add(job_row)
        job_payload = {
            "job_id": int(job_detail.id),
            "export_id": int(job_detail.export_id),
            "task_id": int(job_detail.task_id),
            "status": job_detail.status,
            "image_count": int(job_detail.image_count or 0),
            "labeled_image_count": int(job_detail.labeled_image_count or 0),
            "unlabeled_image_count": int(job_detail.unlabeled_image_count or 0),
            "start_status": "pending",
        }
        try:
            started = yolo_edge_validation.start_job(
                db=db,
                job_id=int(job_detail.id),
                current_user=admin,
            )
            job_payload["status"] = started.status
            job_payload["start_status"] = "started"
        except LabelUException as exc:
            job_payload["start_status"] = "failed"
            job_payload["start_error"] = {
                "status_code": int(
                    getattr(exc, "status_code", status.HTTP_500_INTERNAL_SERVER_ERROR)
                ),
                "message": str(getattr(exc, "msg", "") or exc),
            }
        except Exception as exc:
            job_payload["start_status"] = "failed"
            job_payload["start_error"] = {
                "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                "message": str(exc),
            }
        edge_jobs.append(job_payload)

    baseline_job_payload = (
        result.get("baseline_edge_validation_job")
        if isinstance(result.get("baseline_edge_validation_job"), dict)
        else None
    )
    source_export_row = crud_yolo_bm1688_export.get(db, int(source_export_id))
    if (
        baseline_job_payload is None
        and source_export_row
        and getattr(source_export_row, "yolo_train_job_id", None)
        and int(source_export_id) != int(export_id)
    ):
        try:
            baseline_detail = yolo_edge_validation.create_legacy_edge_ssh_job_for_export(
                db=db,
                export_id=int(source_export_id),
                current_user=admin,
            )
            baseline_row = crud_yolo_edge_validation_job.get(db, int(baseline_detail.id))
            if baseline_row:
                baseline_row.source_type = "third_party"
                baseline_row.source_system = "strategy_evolution"
                baseline_row.source_external_task_id = str(result.get("external_task_id") or "") or None
                baseline_row.source_external_package_id = str(result.get("source_package_id") or "") or None
                baseline_row.source_integration_id = _safe_int(result.get("strategy_task_integration_id"))
                db.add(baseline_row)
            baseline_job_payload = {
                "job_id": int(baseline_detail.id),
                "export_id": int(baseline_detail.export_id),
                "task_id": int(baseline_detail.task_id),
                "status": baseline_detail.status,
                "image_count": int(baseline_detail.image_count or 0),
                "labeled_image_count": int(baseline_detail.labeled_image_count or 0),
                "unlabeled_image_count": int(baseline_detail.unlabeled_image_count or 0),
                "start_status": "pending",
                "selection_source": "legacy_edge_ssh_dataset",
            }
            started = yolo_edge_validation.start_job(
                db=db,
                job_id=int(baseline_detail.id),
                current_user=admin,
            )
            baseline_job_payload["status"] = started.status
            baseline_job_payload["start_status"] = "started"
        except LabelUException as exc:
            if "test/val" not in str(getattr(exc, "msg", "") or exc):
                baseline_job_payload = {
                    "selection_source": "legacy_edge_ssh_dataset",
                    "start_status": "failed",
                    "start_error": {
                        "status_code": getattr(
                            exc, "status_code", status.HTTP_500_INTERNAL_SERVER_ERROR
                        ),
                        "message": str(getattr(exc, "msg", "") or exc),
                    },
                }
        except Exception as exc:
            baseline_job_payload = {
                "selection_source": "legacy_edge_ssh_dataset",
                "start_status": "failed",
                "start_error": {
                    "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "message": str(exc),
                },
            }

    result["edge_experiment_status"] = "edge_validation_created"
    result["edge_experiment_jobs"] = edge_jobs
    result["edge_validation_jobs"] = edge_jobs
    result["baseline_edge_validation_job"] = baseline_job_payload
    if edge_jobs:
        result["derived_edge_validation_job_id"] = int(edge_jobs[-1]["job_id"])
    return result


async def _orchestrate_threshold_experiment(
    db: Session,
    *,
    cmd: CreateStrategyEvolutionTaskCommand,
    row: StrategyTaskIntegration,
) -> tuple[str, str, dict[str, Any]]:
    existing_result = _json_loads_object(row.platform_result_json)
    existing_result.setdefault("external_task_id", row.external_task_id)
    existing_result.setdefault("source_package_id", str(row.source_package_id or "") or None)
    existing_result.setdefault("strategy_task_integration_id", int(row.id))
    existing = _existing_edge_validation_result(row)
    if existing:
        synced_result, message, _ = _sync_existing_edge_validation_result(
            db,
            row=row,
            restart_existing_jobs=True,
        )
        synced_result = synced_result or existing
        synced_result.setdefault("edge_experiment_status", "edge_validation_created")
        synced_result.setdefault(
            "edge_experiment_jobs", synced_result.get("edge_validation_jobs") or []
        )
        return "edge_validation_created", message or "边端验证任务已存在", synced_result

    existing_export_id = _safe_int(
        existing_result.get("derived_export_id") or row.derived_export_id
    )
    if existing_result and existing_export_id:
        return (
            str(row.platform_status or "export_created"),
            "bm1688 export already exists",
            existing_result,
        )

    export_id, model_resolution = _resolve_export_id_from_model_id(
        db, cmd.target.model_id
    )
    if not export_id:
        return (
            "waiting_for_model",
            "已发布模型映射尚未就绪",
            {
                "edge_experiment_status": "waiting_for_model",
                "model_resolution": model_resolution,
                "edge_experiment_jobs": [],
                "edge_validation_jobs": [],
            },
        )

    imports = _matching_import_rows(db, cmd=cmd)
    task_ids: list[int] = []
    for import_row in imports:
        for task_id in _linked_task_ids_for_import(db, row=import_row):
            if task_id not in task_ids:
                task_ids.append(task_id)
    sample_ids_by_task, selected_sample_details, sample_selection_mode = (
        _select_done_sample_ids_from_imports(db, imports=imports, cmd=cmd)
    )
    # Keep the published export mapping even while waiting for annotations so
    # the next watchdog pass does not have to rediscover it.
    source_export_row = crud_yolo_bm1688_export.get(db, int(export_id))
    total_done_samples = sum(len(ids) for ids in sample_ids_by_task.values())
    if total_done_samples <= 0:
        return (
            "waiting_for_annotations",
            "请求时间窗内未匹配到已完成标注样本",
            {
                "edge_experiment_status": "waiting_for_annotations",
                "model_resolution": model_resolution,
                "source_export_id": int(export_id),
                "source_train_job_id": (
                    int(source_export_row.yolo_train_job_id)
                    if source_export_row
                    and getattr(source_export_row, "yolo_train_job_id", None)
                    else None
                ),
                "matched_import_job_ids": [row.id for row in imports],
                "matched_task_ids": task_ids,
                "sample_selection_mode": sample_selection_mode,
                "edge_experiment_jobs": [],
                "edge_validation_jobs": [],
            },
        )

    source_export_row = source_export_row or crud_yolo_bm1688_export.get(db, int(export_id))
    source_train_job_id = (
        int(source_export_row.yolo_train_job_id)
        if source_export_row and getattr(source_export_row, "yolo_train_job_id", None)
        else 0
    )
    source_task_id = (
        int(source_export_row.task_id)
        if source_export_row and getattr(source_export_row, "task_id", None)
        else 0
    )
    if source_train_job_id <= 0 or source_task_id <= 0:
        return (
            "waiting_for_model",
            "已发布模型映射尚未就绪",
            {
                "edge_experiment_status": "waiting_for_model",
                "message": "已发布模型映射尚未就绪",
                "model_resolution": {
                    **model_resolution,
                    "reason": "已发布导出对应的源训练任务尚未就绪",
                },
                "edge_experiment_jobs": [],
                "edge_validation_jobs": [],
            },
        )

    source_export_status = str(getattr(source_export_row, "status", "") or "").strip().upper()
    if source_export_status in {"", "PENDING", "PROCESSING"}:
        message = "BM1688 导出任务执行中"
    elif source_export_status == "COMPLETED":
        message = "已创建 BM1688 导出任务"
    else:
        return (
            "failed",
            "已发布模型的 BM1688 导出尚未就绪",
            {
                "edge_experiment_status": "export_created",
                "message": "已发布模型的 BM1688 导出尚未就绪",
                "input_experiment": cmd.experiment,
                "model_resolution": {
                    **model_resolution,
                    "source_export_status": source_export_status,
                },
                "source_export_id": int(export_id),
                "source_train_job_id": int(source_train_job_id),
                "matched_import_job_ids": [row.id for row in imports],
                "matched_task_ids": task_ids,
                "selected_sample_count": total_done_samples,
                "sample_selection_mode": sample_selection_mode,
                "selected_sample_ids_by_task": {
                    str(task_id): sample_ids
                    for task_id, sample_ids in sample_ids_by_task.items()
                },
                "selected_samples": selected_sample_details,
                "baseline_edge_validation_job": None,
                "edge_experiment_jobs": [],
                "edge_validation_jobs": [],
            },
        )

    result = {
        "edge_experiment_status": "export_created",
        "message": message,
        "input_experiment": cmd.experiment,
        "model_resolution": model_resolution,
        "source_export_id": int(export_id),
        "source_train_job_id": int(source_train_job_id),
        "derived_export_id": int(export_id),
        "matched_import_job_ids": [row.id for row in imports],
        "matched_task_ids": task_ids,
        "selected_sample_count": total_done_samples,
        "sample_selection_mode": sample_selection_mode,
        "selected_sample_ids_by_task": {
            str(task_id): sample_ids
            for task_id, sample_ids in sample_ids_by_task.items()
        },
        "selected_samples": selected_sample_details,
        "baseline_edge_validation_job": None,
        "edge_experiment_jobs": [],
        "edge_validation_jobs": [],
    }
    return "export_created", message, result


def _parse_task_config(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_task_classes(task: Task) -> list[str]:
    return build_yolo_rect_class_names(_parse_task_config(task.config))


def _task_name_for_strategy_dataset(task_name: str, external_task_id: str) -> str:
    base = str(task_name or "").strip() or f"strategy_{external_task_id}"
    name = f"{base}-train"
    if len(name) <= 64:
        return name
    suffix = f"-{external_task_id}"[-20:]
    trimmed = base[: max(1, 64 - len("-train") - len(suffix))]
    return f"{trimmed}{suffix}-train"[:64]


def _selected_samples_by_task(
    db: Session,
    sample_ids_by_task: dict[int, list[int]],
) -> dict[int, list[TaskSample]]:
    selected: dict[int, list[TaskSample]] = {}
    for task_id, sample_ids in sample_ids_by_task.items():
        rows: list[TaskSample] = []
        for sample_id in sample_ids:
            row = crud_sample.get(db, int(sample_id))
            if not row or int(row.task_id) != int(task_id):
                continue
            if str(getattr(row, "state", "") or "") != SampleState.DONE.value:
                continue
            if getattr(row, "file", None) is None:
                continue
            rows.append(row)
        if rows:
            selected[int(task_id)] = rows
    return selected


def _filter_selected_samples_by_template_classes(
    db: Session,
    *,
    selected_samples_by_task: dict[int, list[TaskSample]],
    template_task: Task | None,
) -> tuple[dict[int, list[TaskSample]], list[int]]:
    template_classes = _source_task_classes(template_task) if template_task else []
    if not template_classes:
        return selected_samples_by_task, []

    filtered: dict[int, list[TaskSample]] = {}
    skipped_task_ids: list[int] = []
    for task_id, samples in selected_samples_by_task.items():
        task = crud_task.get(db, int(task_id))
        if not task:
            skipped_task_ids.append(int(task_id))
            continue
        if _source_task_classes(task) != template_classes:
            skipped_task_ids.append(int(task_id))
            continue
        filtered[int(task_id)] = samples
    return filtered, skipped_task_ids


def _filter_sample_ids_by_template_classes(
    db: Session,
    *,
    sample_ids_by_task: dict[int, list[int]],
    selected_sample_details: list[dict[str, Any]],
    template_task: Task | None,
) -> tuple[dict[int, list[int]], list[dict[str, Any]], list[int]]:
    template_classes = _source_task_classes(template_task) if template_task else []
    if not template_classes:
        return sample_ids_by_task, selected_sample_details, []

    filtered: dict[int, list[int]] = {}
    compatible_task_ids: set[int] = set()
    skipped_task_ids: list[int] = []
    for task_id, sample_ids in sample_ids_by_task.items():
        task = crud_task.get(db, int(task_id))
        if not task:
            skipped_task_ids.append(int(task_id))
            continue
        if _source_task_classes(task) != template_classes:
            skipped_task_ids.append(int(task_id))
            continue
        filtered[int(task_id)] = sample_ids
        compatible_task_ids.add(int(task_id))

    filtered_details = [
        item
        for item in selected_sample_details
        if _safe_int(item.get("task_id")) in compatible_task_ids
    ]
    return filtered, filtered_details, skipped_task_ids


def _create_strategy_training_dataset_task(
    db: Session,
    *,
    current_user_id: int,
    source_integration_id: int,
    cmd: CreateStrategyEvolutionTaskCommand,
    selected_samples_by_task: dict[int, list[TaskSample]],
    template_task: Task | None = None,
) -> tuple[Task, list[str]]:
    source_task_ids = sorted(selected_samples_by_task.keys())
    if not source_task_ids:
        raise StrategyEvolutionError(
            400,
            "请求时间窗内未匹配到已完成标注样本",
        )

    source_tasks: list[Task] = []
    source_classes: list[str] | None = None
    source_config: dict[str, Any] | None = None
    template_classes = _source_task_classes(template_task) if template_task else None
    template_config = _parse_task_config(template_task.config) if template_task else None
    for task_id in source_task_ids:
        task = crud_task.get(db, task_id=task_id)
        if not task:
            continue
        classes = _source_task_classes(task)
        if not classes:
            raise StrategyEvolutionError(
                409,
                f"task #{task_id} rect classes are not configured",
            )
        if template_classes and classes != template_classes:
            raise StrategyEvolutionError(
                409,
                f"matched tasks have incompatible rect classes: task #{task_id}",
            )
        if source_classes is None:
            source_tasks.append(task)
            source_classes = template_classes or classes
            source_config = template_config or _parse_task_config(task.config)
            continue
        if classes != source_classes:
            raise StrategyEvolutionError(
                409,
                f"matched tasks have incompatible rect classes: task #{task_id}",
            )
        source_tasks.append(task)

    if not source_tasks or source_config is None or source_classes is None:
        raise StrategyEvolutionError(
            404,
            "matched strategy tasks are not available",
        )

    strategy_task = crud_task.create(
        db=db,
        task=Task(
            name=_task_name_for_strategy_dataset(cmd.task_name, cmd.external_task_id),
            description=f"strategy evolution model capability dataset {cmd.external_task_id}",
            tips="",
            config=json.dumps(source_config, ensure_ascii=False),
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.CONFIGURED.value,
            training_ready=True,
            dataset_category_id=(
                int(template_task.dataset_category_id)
                if template_task is not None
                and template_task.dataset_category_id is not None
                else None
            ),
            source_type="third_party",
            source_system="strategy_evolution",
            source_external_task_id=cmd.external_task_id,
            source_external_package_id=(cmd.source_package_id or None),
            source_integration_id=source_integration_id,
            created_by=current_user_id,
            updated_by=current_user_id,
        ),
    )

    cloned_samples: list[TaskSample] = []
    inner_id = 1
    for task_id in source_task_ids:
        for sample in selected_samples_by_task.get(task_id, []):
            attachment = sample.file
            cloned_attachment = crud_attachment.create(
                db,
                TaskAttachment(
                    filename=attachment.filename,
                    url=attachment.url,
                    path=attachment.path,
                    task_id=strategy_task.id,
                    created_by=current_user_id,
                    updated_by=current_user_id,
                ),
            )
            cloned_samples.append(
                TaskSample(
                    inner_id=inner_id,
                    task_id=strategy_task.id,
                    file_id=cloned_attachment.id,
                    created_by=current_user_id,
                    updated_by=current_user_id,
                    annotated_count=int(sample.annotated_count or 0),
                    data=sample.data,
                    state=SampleState.DONE.value,
                )
            )
            inner_id += 1
    crud_sample.batch(db=db, samples=cloned_samples)
    crud_task.update(
        db,
        strategy_task,
        {
            Task.last_sample_inner_id.key: max(0, inner_id - 1),
        },
    )
    return strategy_task, source_classes


def _training_weights_command_fields(
    db: Session,
    *,
    model_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    export_id, model_resolution = _resolve_export_id_from_model_id(db, model_id)
    if export_id is None:
        return None, model_resolution
    export_row = crud_yolo_bm1688_export.get(db, int(export_id))
    if not export_row or not getattr(export_row, "yolo_train_job_id", None):
        return None, {
            **model_resolution,
            "export_id": export_id,
            "reason": "published export source train job is not ready",
        }
    return (
        {
            "weights_source": "trained",
            "weights_ref_id": int(export_row.yolo_train_job_id),
        },
        {
            **model_resolution,
            "export_id": int(export_id),
            "source_train_job_id": int(export_row.yolo_train_job_id),
        },
    )


async def _orchestrate_model_capability(
    db: Session,
    *,
    cmd: CreateStrategyEvolutionTaskCommand,
    row: StrategyTaskIntegration,
) -> tuple[str, str, dict[str, Any]]:
    existing_result = _existing_model_training_result(row)
    if existing_result is not None:
        synced_result, message, changed = _sync_existing_model_training_result(
            db,
            row=row,
        )
        live_model_jobs = _result_model_jobs(synced_result)
        if live_model_jobs:
            if changed and synced_result is not None:
                with begin_transaction(db):
                    locked = crud_strategy_task_integration.get_by_external_task_id(
                        db=db,
                        external_task_id=cmd.external_task_id,
                        lock=True,
                    )
                    if locked:
                        crud_strategy_task_integration.update(
                            db=db,
                            row=locked,
                            obj_in={
                                StrategyTaskIntegration.platform_result_json.key: _json_dumps(
                                    synced_result
                                ),
                            },
                        )
            return row.platform_status or "model_training_created", message, synced_result

        # A deleted training job must not keep the external task permanently idempotent.
        # Clear only the derived execution state; source model references remain valid.
        with begin_transaction(db):
            locked = crud_strategy_task_integration.get_by_external_task_id(
                db=db,
                external_task_id=cmd.external_task_id,
                lock=True,
            )
            if locked:
                crud_strategy_task_integration.update(
                    db=db,
                    row=locked,
                    obj_in={
                        StrategyTaskIntegration.platform_result_json.key: None,
                        StrategyTaskIntegration.automation_result_json.key: None,
                        StrategyTaskIntegration.platform_error_json.key: None,
                        StrategyTaskIntegration.derived_train_job_id.key: None,
                        StrategyTaskIntegration.derived_export_id.key: None,
                        StrategyTaskIntegration.derived_edge_validation_job_id.key: None,
                    },
                )
        row = crud_strategy_task_integration.get_by_external_task_id(
            db=db,
            external_task_id=cmd.external_task_id,
        ) or row

    weight_fields, model_resolution = _training_weights_command_fields(
        db,
        model_id=cmd.target.model_id,
    )
    if weight_fields is None:
        return (
            "waiting_for_model",
            "已发布模型映射尚未就绪",
            {
                "model_training_status": "waiting_for_model",
                "message": "已发布模型映射尚未就绪",
                "model_resolution": model_resolution,
                "issue_type": str(cmd.experiment.get("capability_issue_type") or "").strip(),
                "symptoms": [
                    str(item).strip()
                    for item in (cmd.experiment.get("symptoms") or [])
                    if str(item).strip()
                ],
                "labeling_required": cmd.experiment.get("labeling_required"),
            },
        )

    source_export_id = _safe_int(model_resolution.get("export_id"))
    source_train_job_id = _safe_int(model_resolution.get("source_train_job_id"))
    source_export_row = (
        crud_yolo_bm1688_export.get(db, int(source_export_id))
        if source_export_id
        else None
    )
    source_task = (
        crud_task.get(db, int(source_export_row.task_id))
        if source_export_row and getattr(source_export_row, "task_id", None)
        else None
    )
    if source_export_row is None or source_task is None:
        return (
            "waiting_for_model",
            "已发布模型映射尚未就绪",
            {
                "model_training_status": "waiting_for_model",
                "message": "已发布模型映射尚未就绪",
                "model_resolution": {
                    **model_resolution,
                    "reason": "published export source task is not ready",
                },
            },
        )

    matched_imports = _matching_import_rows(db, cmd=cmd)
    if not matched_imports:
        return (
            "waiting_for_annotations",
            "请求时间窗内未匹配到已完成标注样本",
            {
                "model_training_status": "waiting_for_annotations",
                "message": "请求时间窗内未匹配到已完成标注样本",
                "model_resolution": model_resolution,
                "issue_type": str(cmd.experiment.get("capability_issue_type") or "").strip(),
                "symptoms": [
                    str(item).strip()
                    for item in (cmd.experiment.get("symptoms") or [])
                    if str(item).strip()
                ],
                "labeling_required": cmd.experiment.get("labeling_required"),
            },
        )

    sample_ids_by_task, selected_sample_details, sample_selection_mode = (
        _select_done_sample_ids_from_imports(db, imports=matched_imports, cmd=cmd)
    )
    sample_ids_by_task = {
        int(task_id): [int(sample_id) for sample_id in sample_ids]
        for task_id, sample_ids in sample_ids_by_task.items()
        if sample_ids
    }
    matched_task_ids = sorted(int(task_id) for task_id in sample_ids_by_task.keys())
    if not sample_ids_by_task:
        return (
            "waiting_for_annotations",
            "请求时间窗内未匹配到已完成标注样本",
            {
                "model_training_status": "waiting_for_annotations",
                "message": "请求时间窗内未匹配到已完成标注样本",
                "model_resolution": model_resolution,
                "matched_import_job_ids": [int(item.id) for item in matched_imports],
                "matched_task_ids": matched_task_ids,
                "selected_sample_count": 0,
                "selected_sample_ids_by_task": {},
                "selected_samples": [],
                "sample_selection_mode": sample_selection_mode,
                "issue_type": str(cmd.experiment.get("capability_issue_type") or "").strip(),
                "symptoms": [
                    str(item).strip()
                    for item in (cmd.experiment.get("symptoms") or [])
                    if str(item).strip()
                ],
                "labeling_required": cmd.experiment.get("labeling_required"),
            },
        )

    sample_ids_by_task, selected_sample_details, incompatible_task_ids = (
        _filter_sample_ids_by_template_classes(
            db,
            sample_ids_by_task=sample_ids_by_task,
            selected_sample_details=selected_sample_details,
            template_task=source_task,
        )
    )
    matched_task_ids = sorted(int(task_id) for task_id in sample_ids_by_task.keys())
    if not sample_ids_by_task:
        return (
            "waiting_for_annotations",
            "请求时间窗内未匹配到兼容的已完成标注样本",
            {
                "model_training_status": "waiting_for_annotations",
                "message": "请求时间窗内未匹配到兼容的已完成标注样本",
                "model_resolution": model_resolution,
                "matched_import_job_ids": [int(item.id) for item in matched_imports],
                "matched_task_ids": matched_task_ids,
                "incompatible_task_ids": incompatible_task_ids,
                "selected_sample_count": 0,
                "selected_sample_ids_by_task": {},
                "selected_samples": [],
                "sample_selection_mode": sample_selection_mode,
                "issue_type": str(cmd.experiment.get("capability_issue_type") or "").strip(),
                "symptoms": [
                    str(item).strip()
                    for item in (cmd.experiment.get("symptoms") or [])
                    if str(item).strip()
                ],
                "labeling_required": cmd.experiment.get("labeling_required"),
            },
        )

    selected_samples_by_task = _selected_samples_by_task(db, sample_ids_by_task)
    if not selected_samples_by_task:
        return (
            "waiting_for_annotations",
            "请求时间窗内未匹配到兼容的已完成标注样本",
            {
                "model_training_status": "waiting_for_annotations",
                "message": "请求时间窗内未匹配到兼容的已完成标注样本",
                "model_resolution": model_resolution,
                "matched_import_job_ids": [int(item.id) for item in matched_imports],
                "matched_task_ids": matched_task_ids,
                "incompatible_task_ids": incompatible_task_ids,
                "selected_sample_count": 0,
                "selected_sample_ids_by_task": {},
                "selected_samples": [],
                "sample_selection_mode": sample_selection_mode,
                "issue_type": str(cmd.experiment.get("capability_issue_type") or "").strip(),
                "symptoms": [
                    str(item).strip()
                    for item in (cmd.experiment.get("symptoms") or [])
                    if str(item).strip()
                ],
                "labeling_required": cmd.experiment.get("labeling_required"),
            },
        )

    sample_ids_by_task = {
        int(task_id): [
            int(sample.id)
            for sample in samples
        ]
        for task_id, samples in selected_samples_by_task.items()
        if samples
    }
    matched_task_ids = sorted(int(task_id) for task_id in sample_ids_by_task.keys())
    selected_sample_details = [
        item
        for item in selected_sample_details
        if _safe_int(item.get("task_id")) in matched_task_ids
    ]

    admin = _admin_user(db)
    with begin_transaction(db):
        strategy_task, source_classes = _create_strategy_training_dataset_task(
            db,
            current_user_id=admin.id,
            source_integration_id=int(row.id),
            cmd=cmd,
            selected_samples_by_task=selected_samples_by_task,
            template_task=source_task,
        )
        locked = crud_strategy_task_integration.get_by_external_task_id(
            db=db,
            external_task_id=cmd.external_task_id,
            lock=True,
        )
        if locked:
            crud_strategy_task_integration.update(
                db=db,
                row=locked,
                obj_in={
                    StrategyTaskIntegration.labelu_task_id.key: strategy_task.id,
                    StrategyTaskIntegration.source_train_job_id.key: source_train_job_id,
                    StrategyTaskIntegration.source_export_id.key: source_export_id,
                },
            )

    train_cmd = CreateYoloTrainJobCommand(
        category=cmd.task_name,
        epochs=50,
        imgsz=640,
        merge_task_ids=[int(source_task.id)],
        **weight_fields,
    )
    train_job = await yolo_train.create_train_job(
        db=db,
        task_id=int(strategy_task.id),
        cmd=train_cmd,
        current_user=admin,
    )
    train_job_row = crud_yolo_train_job.get(db, int(train_job.id))
    if train_job_row:
        train_job_row.source_type = "third_party"
        train_job_row.source_system = "strategy_evolution"
        train_job_row.source_external_task_id = cmd.external_task_id
        train_job_row.source_external_package_id = cmd.source_package_id or None
        train_job_row.source_integration_id = int(row.id)
        train_job_row.dataset_category_id = row.dataset_category_id
        db.add(train_job_row)
    with begin_transaction(db):
        locked = crud_strategy_task_integration.get_by_external_task_id(
            db=db,
            external_task_id=cmd.external_task_id,
            lock=True,
        )
        if locked:
            crud_strategy_task_integration.update(
                db=db,
                row=locked,
                obj_in={
                    StrategyTaskIntegration.derived_train_job_id.key: int(train_job.id),
                },
            )
    train_job_payload = _model_job_payload_from_detail(train_job)

    result = {
        "model_training_status": "training_job_created",
        "message": "已创建模型训练任务",
        "issue_type": str(cmd.experiment.get("capability_issue_type") or "").strip(),
        "symptoms": [
            str(item).strip()
            for item in (cmd.experiment.get("symptoms") or [])
            if str(item).strip()
        ],
        "labeling_required": cmd.experiment.get("labeling_required"),
        "model_resolution": model_resolution,
        "matched_import_job_ids": [int(item.id) for item in matched_imports],
        "matched_task_ids": matched_task_ids,
        "incompatible_task_ids": incompatible_task_ids,
        "selected_sample_count": sum(len(items) for items in sample_ids_by_task.values()),
        "selected_sample_ids_by_task": {
            str(task_id): sample_ids
            for task_id, sample_ids in sample_ids_by_task.items()
        },
        "selected_samples": selected_sample_details,
        "sample_selection_mode": sample_selection_mode,
        "derived_task_id": int(strategy_task.id),
        "derived_task_name": strategy_task.name,
        "task_rect_classes": source_classes,
        "source_task_id": int(source_task.id),
        "completed_sample_count": sum(len(items) for items in sample_ids_by_task.values()),
        "accepted_sample_count": sum(len(items) for items in sample_ids_by_task.values()),
        "source_train_job_id": source_train_job_id,
        "source_export_id": source_export_id,
        "derived_train_job_id": int(train_job.id),
        "strategy_task_integration_id": int(row.id),
        "model_training_jobs": [train_job_payload],
    }
    return "model_training_created", "已创建模型训练任务", result


def _persist_strategy_snapshot(
    db: Session,
    *,
    external_task_id: str,
    platform_status: str,
    automation_stage: str,
    automation_status: str,
    result: dict[str, Any],
    last_error: str | None = None,
    extra_updates: dict[str, Any] | None = None,
    expected_lease_owner: str | None = None,
) -> StrategyTaskIntegration | None:
    updates = {
        StrategyTaskIntegration.automation_stage.key: automation_stage,
        StrategyTaskIntegration.automation_status.key: automation_status,
        StrategyTaskIntegration.automation_result_json.key: _json_dumps(result),
        StrategyTaskIntegration.platform_status.key: platform_status,
        StrategyTaskIntegration.platform_result_json.key: _json_dumps(result),
        StrategyTaskIntegration.last_error.key: last_error,
        **(extra_updates or {}),
    }
    with begin_transaction(db):
        if expected_lease_owner is not None:
            return crud_strategy_task_integration.update_by_external_task_id_and_lease_owner(
                db=db,
                external_task_id=external_task_id,
                lease_owner=expected_lease_owner,
                obj_in=updates,
            )
        return crud_strategy_task_integration.update_by_external_task_id(
            db=db,
            external_task_id=external_task_id,
            obj_in=updates,
        )


def _sync_edge_job_payload(
    db: Session,
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    job_id = int(payload.get("job_id") or 0)
    if job_id <= 0:
        return payload
    admin = _admin_user(db)
    try:
        live_detail = yolo_edge_validation.get_job(
            db=db,
            job_id=job_id,
            current_user=admin,
        )
        return _edge_job_payload_from_detail(
            live_detail,
            previous_payload=payload,
            start_status=str(payload.get("start_status") or "").strip() or None,
            start_error=payload.get("start_error")
            if isinstance(payload.get("start_error"), dict)
            else None,
        )
    except Exception:
        return payload


def _sync_baseline_edge_job(
    db: Session,
    *,
    result: dict[str, Any],
) -> None:
    baseline = (
        result.get("baseline_edge_validation_job")
        if isinstance(result.get("baseline_edge_validation_job"), dict)
        else None
    )
    if not baseline:
        return
    result["baseline_edge_validation_job"] = _sync_edge_job_payload(db, payload=baseline)


def _build_model_comparison_payload(
    *,
    source_jobs: list[dict[str, Any]],
    candidate_jobs: list[dict[str, Any]],
    source_train_job: dict[str, Any] | None,
    candidate_train_job: dict[str, Any] | None,
    quality_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source_summary = _aggregate_edge_jobs(source_jobs)
    candidate_summary = _aggregate_edge_jobs(candidate_jobs)
    source_score = source_summary.get("avg_score")
    candidate_score = candidate_summary.get("avg_score")
    source_train_score = _train_job_score(source_train_job or {})
    candidate_train_score = _train_job_score(candidate_train_job or {})
    policy = quality_policy if isinstance(quality_policy, dict) else {}
    edge_policy = policy.get("edge_usability") if isinstance(policy.get("edge_usability"), dict) else {}
    release_policy = policy.get("release_readiness") if isinstance(policy.get("release_readiness"), dict) else {}
    checks: list[dict[str, Any]] = []

    def add_check(key: str, passed: bool, reason: str, **evidence: Any) -> None:
        checks.append(
            {
                "key": key,
                "passed": bool(passed),
                "reason": reason,
                "evidence": _compact_payload_dict(evidence),
            }
        )

    add_check(
        "source_edge_validation_complete",
        source_summary["failed_count"] == 0
        and source_summary["success_count"] == source_summary["job_count"]
        and source_score is not None,
        "原模型边端验证结果完整" if source_score is not None else "原模型缺少可用边端指标",
        summary=source_summary,
    )
    add_check(
        "candidate_edge_validation_complete",
        candidate_summary["failed_count"] == 0
        and candidate_summary["success_count"] == candidate_summary["job_count"]
        and candidate_score is not None,
        "候选模型边端验证结果完整" if candidate_score is not None else "候选模型缺少可用边端指标",
        summary=candidate_summary,
    )

    comparison_passed = False
    reason = "候选模型边端指标不优于原模型"
    if (
        candidate_summary["failed_count"] == 0
        and source_summary["failed_count"] == 0
        and candidate_score is not None
        and source_score is not None
    ):
        if candidate_score > source_score:
            comparison_passed = True
            reason = "candidate edge validation score is better"
        elif candidate_score == source_score:
            if candidate_train_score is not None and source_train_score is not None:
                comparison_passed = candidate_train_score >= source_train_score
                reason = (
                    "edge validation tie, candidate training score is not worse"
                    if comparison_passed
                    else "edge validation tie, candidate training score is worse"
                )
            else:
                comparison_passed = True
                reason = "edge validation tie"
    add_check(
        "candidate_edge_score_not_worse",
        comparison_passed,
        reason,
        source_score=source_score,
        candidate_score=candidate_score,
        source_train_score=source_train_score,
        candidate_train_score=candidate_train_score,
    )

    min_map50 = _safe_float(edge_policy.get("min_map50"))
    min_map50_95 = _safe_float(edge_policy.get("min_map50_95"))
    min_f1 = _safe_float(edge_policy.get("min_f1"))
    candidate_metrics = _extract_eval_summary(
        (candidate_jobs[-1] if candidate_jobs else {})
    )
    minimum_metric_passed = True
    if min_map50 is not None:
        minimum_metric_passed = minimum_metric_passed and (
            _safe_float(candidate_metrics.get("map50")) is not None
            and float(candidate_metrics["map50"]) >= min_map50
        )
    if min_map50_95 is not None:
        minimum_metric_passed = minimum_metric_passed and (
            _safe_float(candidate_metrics.get("map50_95")) is not None
            and float(candidate_metrics["map50_95"]) >= min_map50_95
        )
    if min_f1 is not None:
        minimum_metric_passed = minimum_metric_passed and (
            _eval_summary_f1(candidate_metrics) is not None
            and float(_eval_summary_f1(candidate_metrics)) >= min_f1
        )
    add_check(
        "candidate_edge_minimum_metrics",
        minimum_metric_passed,
        "候选模型达到质量标准" if minimum_metric_passed else "候选模型未达到边端最低指标",
        requirements={
            "min_map50": min_map50,
            "min_map50_95": min_map50_95,
            "min_f1": min_f1,
        },
        metrics=candidate_metrics,
    )

    source_classes = _extract_eval_summary(
        (source_jobs[-1] if source_jobs else {})
    ).get("class_metrics")
    candidate_classes = candidate_metrics.get("class_metrics")
    class_regression_passed = True
    class_regressions: list[dict[str, Any]] = []
    if isinstance(source_classes, list) and isinstance(candidate_classes, list):
        source_by_name = {
            str(item.get("name") or item.get("class_name") or item.get("id")): item
            for item in source_classes
            if isinstance(item, dict)
        }
        allowed_drop = _safe_float(edge_policy.get("max_class_f1_drop")) or 0.0
        for candidate_class in candidate_classes:
            if not isinstance(candidate_class, dict):
                continue
            name = str(
                candidate_class.get("name")
                or candidate_class.get("class_name")
                or candidate_class.get("id")
            )
            source_class = source_by_name.get(name)
            if not source_class:
                continue
            source_f1 = _safe_float(source_class.get("f1"))
            candidate_f1 = _safe_float(candidate_class.get("f1"))
            if source_f1 is None or candidate_f1 is None:
                continue
            if candidate_f1 < source_f1 - allowed_drop:
                class_regression_passed = False
                class_regressions.append(
                    {
                        "class_name": name,
                        "source_f1": source_f1,
                        "candidate_f1": candidate_f1,
                        "allowed_drop": allowed_drop,
                    }
                )
    add_check(
        "per_class_f1_not_regressed",
        class_regression_passed,
        "各类别 F1 未下降" if class_regression_passed else "存在类别 F1 下降",
        regressions=class_regressions,
    )

    formal_analysis = None
    candidate_analysis = candidate_metrics.get("business_threshold_analysis")
    if isinstance(candidate_analysis, dict):
        formal_analysis = candidate_analysis
    formal_passed = isinstance(formal_analysis, dict) and formal_analysis.get("status") == "ok"
    add_check(
        "formal_threshold_analysis",
        formal_passed,
        "已生成正式 T1/T2/T3 阈值" if formal_passed else "缺少正式 T1/T2/T3 阈值分析",
        analysis=formal_analysis,
    )

    passed = all(item["passed"] for item in checks)
    failed_checks = [item["key"] for item in checks if not item["passed"]]
    quality_gate = {
        "passed": passed,
        "policy": _compact_payload_dict(
            {
                "policy_name": policy.get("policy_name"),
                "rect_preset_slug": policy.get("rect_preset_slug"),
                "version": policy.get("version"),
                "release_readiness": release_policy,
            }
        ),
        "checks": checks,
        "failed_checks": failed_checks,
    }
    return {
        "passed": passed,
        "reason": reason if passed else "质量准入未通过：" + ", ".join(failed_checks),
        "source_edge_summary": source_summary,
        "candidate_edge_summary": candidate_summary,
        "source_train_score": source_train_score,
        "candidate_train_score": candidate_train_score,
        "quality_gate": quality_gate,
    }


def _sync_publish_job_payload(job_payload: dict[str, Any]) -> dict[str, Any]:
    job_id = str(job_payload.get("job_id") or "").strip()
    if not job_id:
        return job_payload
    live = aicm_publish_job.load_job(job_id)
    if not isinstance(live, dict):
        return job_payload
    payload = dict(job_payload)
    payload["status"] = str(live.get("status") or payload.get("status") or "").strip()
    payload["result"] = live.get("result") if isinstance(live.get("result"), dict) else payload.get("result")
    payload["error_message"] = str(live.get("error_message") or payload.get("error_message") or "").strip() or None
    payload["steps"] = live.get("steps") if isinstance(live.get("steps"), list) else payload.get("steps")
    payload["log_lines"] = live.get("log_lines") if isinstance(live.get("log_lines"), list) else payload.get("log_lines")
    return payload


def _maybe_send_final_callback(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    platform_status: str,
    result: dict[str, Any],
) -> tuple[str, str | None]:
    if platform_status not in FINAL_PLATFORM_STATUSES:
        return str(row.outbound_callback_status or ""), None
    current_callback_status = str(row.outbound_callback_status or "").strip()
    if current_callback_status in {"succeeded", "skipped"}:
        return current_callback_status, None
    if int(row.outbound_callback_attempts or 0) >= int(
        settings.STRATEGY_EVOLUTION_CALLBACK_MAX_ATTEMPTS
    ):
        return str(row.outbound_callback_status or ""), str(row.outbound_callback_last_error or "")
    error_payload = None
    result_payload = None
    if platform_status != "succeeded":
        error_payload = {
            "code": platform_status.upper(),
            "message": str(result.get("message") or result.get("comparison", {}).get("reason") or platform_status),
        }
    else:
        result_payload = _build_outbound_callback_result_payload(
            db,
            row=row,
            result=result,
        )
    callback_result = _send_outbound_callback(
        db,
        row=row,
        result_payload=result_payload,
        error_payload=error_payload,
    )
    if isinstance(callback_result, tuple) and len(callback_result) == 2:
        return str(callback_result[0] or ""), callback_result[1]
    if isinstance(callback_result, StrategyTaskIntegration):
        callback_status = str(
            callback_result.outbound_callback_status
            or row.outbound_callback_status
            or ""
        )
        callback_error = str(
            callback_result.outbound_callback_last_error
            or row.outbound_callback_last_error
            or ""
        ) or None
        if callback_status:
            with begin_transaction(db):
                crud_strategy_task_integration.update_by_external_task_id(
                    db=db,
                    external_task_id=row.external_task_id,
                    obj_in={
                        StrategyTaskIntegration.outbound_callback_status.key: callback_status,
                        StrategyTaskIntegration.outbound_callback_attempts.key: int(
                            callback_result.outbound_callback_attempts
                            or row.outbound_callback_attempts
                            or 0
                        ),
                        StrategyTaskIntegration.outbound_callback_last_error.key: callback_error,
                    },
                )
        return callback_status, callback_error
    if isinstance(callback_result, str):
        return callback_result, None
    return str(row.outbound_callback_status or ""), None


async def _advance_threshold_experiment(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    result: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    current_status = str(row.platform_status or "").strip()
    result.setdefault("external_task_id", row.external_task_id)
    result.setdefault("source_package_id", str(row.source_package_id or "") or None)
    result.setdefault("strategy_task_integration_id", int(row.id))
    # Waiting states are retryable checkpoints, not terminal results. Re-run
    # model, package and sample resolution on every watchdog advance so a
    # later import/annotation/publish can unblock the same external task.
    if current_status in {"waiting_for_model", "waiting_for_annotations"}:
        try:
            cmd = CreateStrategyEvolutionTaskCommand.model_validate(
                {
                    "schema_version": row.schema_version or "v1",
                    "external_task_id": row.external_task_id,
                    "task_name": row.task_name,
                    "task_type": row.task_type,
                    "source_package_id": row.source_package_id or "",
                    "target": _json_loads_object(row.target_json),
                    "data_window": _json_loads_object(row.data_window_json),
                    "callback_url": row.outbound_callback_url or None,
                    "experiment": _json_loads_object(row.experiment_json),
                }
            )
        except ValidationError as exc:
            result["message"] = f"stored strategy task contract is invalid: {exc}"
            return "failed", result["message"], result
        next_status, message, refreshed = await _orchestrate_threshold_experiment(
            db,
            cmd=cmd,
            row=row,
        )
        refreshed.setdefault("external_task_id", row.external_task_id)
        refreshed.setdefault("source_package_id", str(row.source_package_id or "") or None)
        refreshed.setdefault("strategy_task_integration_id", int(row.id))
        return next_status, message, refreshed

    derived_export_id = int(
        _safe_int(result.get("derived_export_id") or row.derived_export_id) or 0
    )
    source_export_id = int(
        _safe_int(result.get("source_export_id") or row.source_export_id) or 0
    )
    if derived_export_id <= 0:
        result["message"] = "BM1688 导出结果尚未就绪"
        return "export_created", "BM1688 导出结果尚未就绪", result

    export_row = crud_yolo_bm1688_export.get(db, int(derived_export_id))
    export_status = str(getattr(export_row, "status", "") or "").strip().upper()
    if not export_row or export_status in {"", "PENDING", "PROCESSING"}:
        result["message"] = "BM1688 导出任务执行中"
        return "export_created", "BM1688 导出任务执行中", result
    export_ready = _strategy_export_ready(export_row)
    result["derived_export_status"] = export_status
    result["derived_export_ready"] = export_ready
    if not export_ready:
        result["message"] = "BM1688 导出未生成可用 bmodel"
        return "failed", "BM1688 导出未生成可用 bmodel", result
    if export_status == "ONNX_READY":
        result["message"] = "INT8 bmodel 已就绪，继续边端验证"

    if not _result_edge_jobs(result):
        result = _create_threshold_edge_jobs(
            db,
            export_id=int(derived_export_id),
            source_export_id=int(source_export_id or derived_export_id),
            result=result,
            experiment=_json_loads_object(row.experiment_json)
            or (
                result.get("input_experiment")
                if isinstance(result.get("input_experiment"), dict)
                else None
            ),
        )
        result["message"] = "已创建边端验证任务"
        return "edge_validation_created", "已创建边端验证任务", result

    synced_result, _, _ = _sync_existing_edge_validation_result(
        db,
        row=row,
        restart_existing_jobs=False,
    )
    if synced_result:
        result = synced_result
    _sync_baseline_edge_job(db, result=result)
    jobs = _result_edge_jobs(result)
    baseline_job = (
        result.get("baseline_edge_validation_job")
        if isinstance(result.get("baseline_edge_validation_job"), dict)
        else None
    )
    all_terminal = all(_is_terminal_status(job.get("status")) for job in jobs)
    if baseline_job:
        all_terminal = all_terminal and _is_terminal_status(baseline_job.get("status"))
    if not jobs or not all_terminal:
        return "edge_validation_created", "边端验证执行中", result

    job_summary = {
        "feedback_jobs": _aggregate_edge_jobs(jobs),
        "baseline_job": _aggregate_edge_jobs([baseline_job]) if baseline_job else None,
    }
    result["threshold_experiment_summary"] = job_summary
    if job_summary["feedback_jobs"].get("failed_count") or (
        isinstance(job_summary.get("baseline_job"), dict)
        and job_summary["baseline_job"].get("failed_count")
    ):
        result["message"] = "阈值实验执行失败"
        return "failed", "阈值实验执行失败", result
    result["threshold_recommendation"] = _build_threshold_recommendation(
        db,
        _json_loads_object(row.experiment_json),
        result=result,
        job_summary=job_summary,
    )
    formal_analysis = result["threshold_recommendation"].get("formal_analysis")
    if not isinstance(formal_analysis, dict) or formal_analysis.get("status") != "ok":
        result["message"] = "边端正式阈值分析未完成"
        return "failed", "边端正式阈值分析未完成", result
    result["message"] = "阈值实验已完成"
    return "succeeded", "阈值实验已完成", result


def _ensure_model_comparison_jobs(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    result: dict[str, Any],
) -> dict[str, Any]:
    if result.get("source_model_edge_validation_jobs") and result.get("edge_validation_jobs"):
        return result
    source_export_id = _safe_int(result.get("source_export_id") or row.source_export_id)
    candidate_export_id = _safe_int(result.get("derived_export_id") or row.derived_export_id)
    selected_by_task = (
        result.get("selected_sample_ids_by_task")
        if isinstance(result.get("selected_sample_ids_by_task"), dict)
        else {}
    )
    if not source_export_id or not candidate_export_id or not selected_by_task:
        return result

    admin = _admin_user(db)
    source_jobs: list[dict[str, Any]] = []
    candidate_jobs: list[dict[str, Any]] = []
    latest_candidate_job_id = None
    for task_id_text, raw_sample_ids in selected_by_task.items():
        task_id = _safe_int(task_id_text)
        sample_ids = [_safe_int(item) for item in (raw_sample_ids or [])]
        sample_ids = [int(item) for item in sample_ids if item]
        if not task_id or not sample_ids:
            continue
        for export_id, bucket in (
            (int(source_export_id), source_jobs),
            (int(candidate_export_id), candidate_jobs),
        ):
            detail = yolo_edge_validation.create_job(
                db=db,
                cmd=CreateYoloEdgeValidationJobCommand(
                    export_id=export_id,
                    task_id=int(task_id),
                    mode="run_and_eval",
                    sample_ids=sample_ids,
                    thresholds=_edge_thresholds_from_experiment({})[0],
                    threshold_source="strategy_evolution_formal_threshold_scan",
                ),
                current_user=admin,
            )
            edge_row = crud_yolo_edge_validation_job.get(db, int(detail.id))
            if edge_row:
                edge_row.source_type = "third_party"
                edge_row.source_system = "strategy_evolution"
                edge_row.source_external_task_id = row.external_task_id
                edge_row.source_external_package_id = row.source_package_id
                edge_row.source_integration_id = row.id
                db.add(edge_row)
            payload = _edge_job_payload_from_detail(detail, start_status="pending")
            try:
                started = yolo_edge_validation.start_job(
                    db=db,
                    job_id=int(detail.id),
                    current_user=admin,
                )
                payload["status"] = started.status
                payload["start_status"] = "started"
            except Exception as exc:
                payload["start_status"] = "failed"
                payload["start_error"] = {
                    "status_code": getattr(exc, "status_code", status.HTTP_500_INTERNAL_SERVER_ERROR),
                    "message": str(getattr(exc, "msg", "") or exc),
                }
            bucket.append(payload)
            if export_id == int(candidate_export_id):
                latest_candidate_job_id = int(detail.id)

    result["source_model_edge_validation_jobs"] = source_jobs
    result["edge_validation_jobs"] = candidate_jobs
    result["candidate_model_edge_validation_jobs"] = candidate_jobs
    if latest_candidate_job_id:
        result["derived_edge_validation_job_id"] = latest_candidate_job_id
    return result


async def _advance_model_capability(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    result: dict[str, Any],
) -> tuple[str, str, dict[str, Any], dict[str, Any] | None]:
    current_status = str(row.platform_status or "").strip()
    synced_result, _, _ = _sync_existing_model_training_result(db, row=row)
    if synced_result:
        result = synced_result
    model_jobs = _result_model_jobs(result)
    latest_model_job = model_jobs[-1] if model_jobs else {}
    derived_train_job_id = _safe_int(result.get("derived_train_job_id") or row.derived_train_job_id)
    if not latest_model_job and not derived_train_job_id and current_status in {
        "waiting_for_model",
        "waiting_for_annotations",
    }:
        return (
            current_status,
            str(result.get("message") or current_status),
            result,
            None,
        )
    if derived_train_job_id and not latest_model_job:
        admin = _admin_user(db)
        live_detail = yolo_train.get_train_job_by_id(
            db=db,
            job_id=int(derived_train_job_id),
            current_user=admin,
        )
        latest_model_job = _model_job_payload_from_detail(live_detail)
        result["model_training_jobs"] = [latest_model_job]

    train_status = str(latest_model_job.get("status") or "").strip().upper()
    if train_status in {"", "PENDING", "PROCESSING"}:
        result["message"] = "模型训练执行中"
        return "model_training_created", "模型训练执行中", result, None
    if train_status != "COMPLETED":
        result["message"] = "模型训练失败"
        return "failed", "模型训练失败", result, None

    derived_export_id = _safe_int(result.get("derived_export_id") or row.derived_export_id)
    if not derived_export_id:
        admin = _admin_user(db)
        export_row = await yolo_bm1688_export.start_export_for_job(
            db=db,
            task_id=int(latest_model_job.get("task_id")),
            job_id=int(latest_model_job.get("job_id")),
            current_user=admin,
        )
        export_record = crud_yolo_bm1688_export.get(db, int(export_row.id))
        if export_record:
            export_record.source_type = "third_party"
            export_record.source_system = "strategy_evolution"
            export_record.source_external_task_id = row.external_task_id
            export_record.source_external_package_id = row.source_package_id
            export_record.source_integration_id = row.id
            db.add(export_record)
        derived_export_id = int(export_row.id)
        result["derived_export_id"] = derived_export_id
        result["message"] = "已创建 BM1688 导出任务"
        return "export_created", "已创建 BM1688 导出任务", result, {
            StrategyTaskIntegration.derived_export_id.key: derived_export_id,
        }

    export_row = crud_yolo_bm1688_export.get(db, int(derived_export_id))
    if not export_row or str(export_row.status or "").strip().upper() in {"PENDING", "PROCESSING"}:
        result["message"] = "BM1688 导出任务执行中"
        return "export_created", "BM1688 导出任务执行中", result, None
    export_status = str(export_row.status or "").strip().upper()
    export_ready = _strategy_export_ready(export_row)
    result["derived_export_status"] = export_status
    result["derived_export_ready"] = export_ready
    if not export_ready:
        result["message"] = "BM1688 导出未生成可用 bmodel"
        return "failed", "BM1688 导出未生成可用 bmodel", result, None
    if export_status == "ONNX_READY":
        result["message"] = "INT8 bmodel 已就绪，继续边端验证"

    result = _ensure_model_comparison_jobs(db, row=row, result=result)
    source_jobs = [
        _sync_edge_job_payload(db, payload=job)
        for job in (result.get("source_model_edge_validation_jobs") or [])
        if isinstance(job, dict)
    ]
    candidate_jobs = [
        _sync_edge_job_payload(db, payload=job)
        for job in (result.get("edge_validation_jobs") or [])
        if isinstance(job, dict)
    ]
    result["source_model_edge_validation_jobs"] = source_jobs
    result["edge_validation_jobs"] = candidate_jobs
    result["candidate_model_edge_validation_jobs"] = candidate_jobs
    if not source_jobs or not candidate_jobs or not all(
        _is_terminal_status(job.get("status")) for job in source_jobs + candidate_jobs
    ):
        result["message"] = "边端验证执行中"
        return "edge_validation_created", "边端验证执行中", result, None

    result["threshold_recommendation"] = _build_model_capability_threshold_recommendation(
        db,
        row=row,
        result=result,
    )

    source_train_payload = None
    if row.source_train_job_id:
        try:
            source_train_payload = _model_job_payload_from_detail(
                yolo_train.get_train_job_by_id(
                    db=db,
                    job_id=int(row.source_train_job_id),
                    current_user=_admin_user(db),
                )
            )
        except Exception:
            source_train_payload = None

    comparison = _build_model_comparison_payload(
        source_jobs=source_jobs,
        candidate_jobs=candidate_jobs,
        source_train_job=source_train_payload,
        candidate_train_job=latest_model_job,
        quality_policy=resolve_policy_for_preset(
            latest_model_job.get("task_rect_preset_slug")
        ),
    )
    result["comparison"] = comparison
    if not comparison.get("passed"):
        result["message"] = "候选模型对比未通过"
        return "comparison_failed", "候选模型对比未通过", result, None

    publish_job = (
        result.get("publish_job") if isinstance(result.get("publish_job"), dict) else None
    )
    if not publish_job:
        admin = _admin_user(db)
        publish_response = await yolo_aicm_publish.start_publish_job(
            db=db,
            current_user=admin,
            export_id=int(derived_export_id),
            publish_mode="automatic",
        )
        publish_job = {
            "job_id": publish_response.job_id,
            "status": publish_response.status,
            "result": _model_dump_json_compat(publish_response.result),
        }
        result["publish_job"] = publish_job
        result["message"] = "已创建自动发布任务"
        return "publish_created", "已创建自动发布任务", result, None

    publish_job = _sync_publish_job_payload(publish_job)
    result["publish_job"] = publish_job
    publish_status = str(publish_job.get("status") or "").strip().upper()
    if publish_status in {"PENDING", "PROCESSING", ""}:
        result["message"] = "自动发布执行中"
        return "publish_created", "自动发布执行中", result, None
    if publish_status == "COMPLETED" and isinstance(publish_job.get("result"), dict) and publish_job["result"].get("ok"):
        threshold_recommendation = (
            result.get("threshold_recommendation")
            if isinstance(result.get("threshold_recommendation"), dict)
            else {}
        )
        current_business_thresholds = (
            threshold_recommendation.get("current_thresholds")
            if isinstance(threshold_recommendation.get("current_thresholds"), dict)
            else None
        )
        recommended_business_thresholds = (
            threshold_recommendation.get("recommended_thresholds")
            if isinstance(threshold_recommendation.get("recommended_thresholds"), dict)
            else None
        )
        recommended_validation_thresholds = _job_threshold_payload(
            threshold_recommendation.get("internal_recommended_thresholds")
            if isinstance(
                threshold_recommendation.get("internal_recommended_thresholds"), dict
            )
            else None
        )
        candidate_edge_metrics = _summarize_edge_job_collection(
            _result_edge_jobs(result) or []
        ) or {}
        current_validation_thresholds = (
            candidate_edge_metrics.get("validation_thresholds")
            if isinstance(candidate_edge_metrics.get("validation_thresholds"), dict)
            else None
        )
        effective_business_thresholds = (
            recommended_business_thresholds or current_business_thresholds
        )
        effective_validation_thresholds = (
            recommended_validation_thresholds or current_validation_thresholds
        )
        threshold_binding = _threshold_binding_payload(
            row=row,
            result=result,
            publish_job=publish_job,
            effective_business_thresholds=effective_business_thresholds,
            effective_validation_thresholds=effective_validation_thresholds,
        )
        result["threshold_binding"] = threshold_binding
        _persist_export_threshold_assets(
            db,
            export_id=int(derived_export_id),
            published_model_id=_published_model_set_id_from_publish_job(publish_job),
            business_thresholds=_threshold_asset_section_payload(
                current=current_business_thresholds,
                recommended=recommended_business_thresholds,
                effective=effective_business_thresholds,
                metadata={
                    "goal": threshold_recommendation.get("goal"),
                    "recommendation_source": threshold_recommendation.get(
                        "recommendation_source"
                    ),
                },
            ),
            validation_thresholds=_threshold_asset_section_payload(
                current=current_validation_thresholds,
                recommended=recommended_validation_thresholds,
                effective=effective_validation_thresholds,
            ),
            threshold_binding=threshold_binding,
            quality_gate=(
                comparison.get("quality_gate")
                if isinstance(comparison.get("quality_gate"), dict)
                else None
            ),
        )
        result["message"] = "策略演化任务已完成"
        return "succeeded", "策略演化任务已完成", result, None
    result["message"] = str(publish_job.get("error_message") or "自动发布失败")
    return "failed", "自动发布失败", result, None


async def _advance_automation(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    lease_owner: str | None = None,
) -> tuple[StrategyTaskIntegration, dict[str, Any] | None]:
    if str(row.platform_callback_event_id or "").strip() and str(row.platform_status or "").strip() in {
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }:
        return row, _json_loads_object(row.platform_result_json) or None
    if row.task_type not in {THRESHOLD_TASK_TYPE, MODEL_CAPABILITY_TASK_TYPE}:
        return row, _json_loads_object(row.platform_result_json) or None
    result = _json_loads_object(row.platform_result_json)
    if row.task_type == THRESHOLD_TASK_TYPE:
        platform_status, _, result = await _advance_threshold_experiment(
            db, row=row, result=result
        )
        extra_updates = {
            StrategyTaskIntegration.source_train_job_id.key: _safe_int(
                result.get("source_train_job_id") or row.source_train_job_id
            ),
            StrategyTaskIntegration.source_export_id.key: _safe_int(result.get("source_export_id") or row.source_export_id),
            StrategyTaskIntegration.derived_export_id.key: _safe_int(result.get("derived_export_id") or row.derived_export_id),
            StrategyTaskIntegration.derived_edge_validation_job_id.key: _safe_int(
                result.get("derived_edge_validation_job_id")
                or row.derived_edge_validation_job_id
            ),
        }
        automation_stage_map = {
            "export_created": "export",
            "edge_validation_created": "edge_validation",
            "waiting_for_model": "model_resolution",
            "waiting_for_annotations": "annotation_resolution",
            "succeeded": "completed",
            "failed": "completed",
        }
        automation_stage = automation_stage_map.get(
            platform_status, str(row.automation_stage or "edge_validation")
        )
        automation_status = (
            "running"
            if platform_status in {
                "export_created",
                "edge_validation_created",
                "waiting_for_model",
                "waiting_for_annotations",
            }
            else (
                "failed"
                if platform_status in {"failed", "callback_failed"}
                else "succeeded"
            )
        )
    else:
        platform_status, _, result, extra_updates = await _advance_model_capability(
            db,
            row=row,
            result=result,
        )
        extra_updates = dict(extra_updates or {})
        # Keep the relational pointers in sync with the detailed automation result.
        # Model capability tasks create edge jobs in a later advance step, so these
        # IDs cannot be populated reliably at task creation time.
        for field, value in (
            (
                StrategyTaskIntegration.source_train_job_id.key,
                result.get("source_train_job_id") or row.source_train_job_id,
            ),
            (
                StrategyTaskIntegration.source_export_id.key,
                result.get("source_export_id") or row.source_export_id,
            ),
            (
                StrategyTaskIntegration.derived_train_job_id.key,
                result.get("derived_train_job_id") or row.derived_train_job_id,
            ),
            (
                StrategyTaskIntegration.derived_export_id.key,
                result.get("derived_export_id") or row.derived_export_id,
            ),
            (
                StrategyTaskIntegration.derived_edge_validation_job_id.key,
                result.get("derived_edge_validation_job_id")
                or row.derived_edge_validation_job_id,
            ),
        ):
            parsed_value = _safe_int(value)
            if parsed_value:
                extra_updates[field] = parsed_value
        automation_stage_map = {
            "model_training_created": "training",
            "export_created": "export",
            "edge_validation_created": "edge_validation",
            "publish_created": "publish",
            "comparison_failed": "comparison",
            "succeeded": "completed",
            "failed": "completed",
        }
        automation_stage = automation_stage_map.get(platform_status, str(row.automation_stage or "training"))
        automation_status = "running" if platform_status not in FINAL_PLATFORM_STATUSES else ("failed" if platform_status in {"failed", "comparison_failed", "callback_failed"} else "succeeded")

    persisted = _persist_strategy_snapshot(
        db,
        external_task_id=row.external_task_id,
        platform_status=platform_status,
        automation_stage=automation_stage,
        automation_status=automation_status,
        result=result,
        last_error=None if platform_status not in {"failed", "comparison_failed"} else _json_dumps(result),
        extra_updates=extra_updates,
        expected_lease_owner=lease_owner,
    )
    if lease_owner is not None and persisted is None:
        raise _AutomationLeaseLost(
            f"策略演化自动推进租约已失效：{row.external_task_id}"
        )
    row = persisted or row
    callback_status, callback_error = _maybe_send_final_callback(
        db,
        row=row,
        platform_status=platform_status,
        result=result,
    )
    if callback_status == "failed" and platform_status == "succeeded":
        result["callback_error"] = callback_error
        persisted = _persist_strategy_snapshot(
            db,
            external_task_id=row.external_task_id,
            platform_status=platform_status,
            automation_stage="completed",
            automation_status="succeeded",
            result=result,
            last_error=None,
            expected_lease_owner=lease_owner,
        )
        if lease_owner is not None and persisted is None:
            raise _AutomationLeaseLost(
                f"策略演化自动推进租约已失效：{row.external_task_id}"
            )
        row = persisted or row
    return row, result


def _to_detail(
    row: StrategyTaskIntegration,
    *,
    result_override: dict[str, Any] | None = None,
) -> StrategyEvolutionTaskDetailResponse:
    result = (
        result_override
        if result_override is not None
        else (_json_loads_object(row.platform_result_json) or None)
    )
    result = _decorate_strategy_result(result)
    return StrategyEvolutionTaskDetailResponse(
        external_task_id=row.external_task_id,
        task_name=row.task_name,
        task_type=row.task_type,
        source_package_id=(str(row.source_package_id or "").strip() or None),
        source_type=str(row.source_type or "third_party"),
        source_system=str(row.source_system or "strategy_evolution"),
        source_external_task_id=row.external_task_id,
        source_external_package_id=(
            str(row.source_external_package_id or row.source_package_id or "").strip()
            or None
        ),
        created_by_username=(
            str(getattr(row.owner, "username", "") or "").strip() or None
        ),
        target=_externalize_model_id_keys(_json_loads_object(row.target_json)),
        data_window=_json_loads_object(row.data_window_json),
        experiment=_json_loads_object(row.experiment_json),
        platform_status=row.platform_status or PLATFORM_STATUS_ACCEPTED,
        automation_stage=(str(row.automation_stage or "").strip() or None),
        automation_status=(str(row.automation_status or "").strip() or None),
        outbound_callback_status=(str(row.outbound_callback_status or "").strip() or None),
        outbound_callback_attempts=int(row.outbound_callback_attempts or 0),
        outbound_callback_next_retry_at=row.outbound_callback_next_retry_at,
        automation_attempts=int(row.automation_attempts or 0),
        automation_lease_expires_at=row.automation_lease_expires_at,
        source_train_job_id=_safe_int(row.source_train_job_id),
        source_export_id=_safe_int(row.source_export_id),
        derived_train_job_id=_safe_int(row.derived_train_job_id),
        derived_export_id=_safe_int(row.derived_export_id),
        derived_edge_validation_job_id=_safe_int(row.derived_edge_validation_job_id),
        progress=_json_loads_object(row.platform_progress_json) or None,
        result=_externalize_model_id_keys(result),
        error=_json_loads_object(row.platform_error_json) or None,
        callback_event_id=(
            row.outbound_callback_event_id or row.platform_callback_event_id or ""
        ),
        finished_at=row.platform_finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def accept_task(
    db: Session,
    *,
    cmd: CreateStrategyEvolutionTaskCommand,
) -> StrategyEvolutionTaskAcceptedResponse:
    if cmd.schema_version != "v1":
        raise StrategyEvolutionError(
            400,
            f"无效的策略演化任务参数，不支持的 schema_version：{cmd.schema_version}",
        )

    source_package_id = str(cmd.source_package_id or "").strip() or None
    if source_package_id:
        _validate_source_package(db, cmd=cmd, package_id=source_package_id)

    requested_callback_url = (
        str(cmd.callback_url).strip() if cmd.callback_url is not None else None
    )
    callback_url = _validate_task_callback_url(
        task_type=cmd.task_type,
        callback_url=requested_callback_url,
    )

    target_json = _json_dumps(cmd.target.model_dump())
    data_window_json = _json_dumps(cmd.data_window.model_dump())
    experiment_json = _json_dumps(cmd.experiment)
    request_sha256 = _strategy_request_sha256(cmd)

    automation_user = _admin_user(db)
    existing_request = False
    with begin_transaction(db):
        row = crud_strategy_task_integration.get_by_external_task_id(
            db=db,
            external_task_id=cmd.external_task_id,
            lock=True,
        )
        if row:
            stored_sha256 = str(row.request_sha256 or "").strip()
            if stored_sha256 and stored_sha256 != request_sha256:
                raise StrategyEvolutionError(
                    409,
                    "external_task_id 已存在，但请求内容与首次提交不一致；请使用新的 external_task_id",
                )
            existing_request = True
            crud_strategy_task_integration.update(
                db=db,
                row=row,
                obj_in={
                    StrategyTaskIntegration.request_sha256.key: request_sha256,
                    StrategyTaskIntegration.request_payload_json.key: _json_dumps(
                        cmd.model_dump(mode="json", by_alias=False)
                    ),
                    StrategyTaskIntegration.updated_by.key: automation_user.id,
                },
            )
        else:
            row = crud_strategy_task_integration.create(
                db=db,
                row=StrategyTaskIntegration(
                    external_task_id=cmd.external_task_id,
                    labelu_task_id=None,
                    datasource_id=None,
                    schema_version=cmd.schema_version,
                    task_name=cmd.task_name,
                    task_type=cmd.task_type,
                    source_package_id=source_package_id,
                    source_external_package_id=source_package_id,
                    source_type="third_party",
                    source_system="strategy_evolution",
                    dataset_s3_uri="",
                    dataset_image_prefix="",
                    metadata_key="",
                    bucket="",
                    prefix="",
                    labeling_required=False,
                    labeling_media_type=None,
                    labeling_annotation_type=None,
                    labeling_labels_json="[]",
                    target_json=target_json,
                    data_window_json=data_window_json,
                    experiment_json=experiment_json,
                    request_payload_json=_json_dumps(
                        cmd.model_dump(mode="json", by_alias=False)
                    ),
                    request_sha256=request_sha256,
                    import_status="PENDING",
                    platform_status=PLATFORM_STATUS_ACCEPTED,
                    automation_source="strategy_evolution",
                    outbound_callback_url=(
                        callback_url or _strategy_callback_url()
                    ),
                    outbound_callback_status="pending",
                    created_by=automation_user.id,
                    updated_by=automation_user.id,
                ),
            )

        # Persist the integration's identity on the incoming package and all
        # formal tasks already produced by that package. This keeps the UI and
        # downstream jobs able to distinguish third-party data from operator work.
        if source_package_id:
            package = crud_experiment_dataset_import.get_by_package_id(
                db, package_id=source_package_id
            )
            if package:
                package.source_type = "third_party"
                package.source_system = "strategy_evolution"
                package.source_external_task_id = cmd.external_task_id
                package.source_external_package_id = source_package_id
                package.source_integration_id = row.id
                package.created_by = automation_user.id
                package.updated_by = automation_user.id
                db.add(package)
                for task_id in _linked_task_ids_for_import(db, row=package):
                    linked_task = crud_task.get(db=db, task_id=int(task_id))
                    if not linked_task:
                        continue
                    linked_task.source_type = "third_party"
                    linked_task.source_system = "strategy_evolution"
                    linked_task.source_external_task_id = cmd.external_task_id
                    linked_task.source_external_package_id = source_package_id
                    linked_task.source_integration_id = row.id
                    linked_task.created_by = automation_user.id
                    linked_task.updated_by = automation_user.id
                    db.add(linked_task)

    platform_status = PLATFORM_STATUS_ACCEPTED
    message = ""
    result: dict[str, Any] | None = None
    if cmd.task_type == THRESHOLD_TASK_TYPE:
        platform_status, message, result = await _orchestrate_threshold_experiment(
            db,
            cmd=cmd,
            row=row,
        )
        if isinstance(result, dict):
            result.setdefault("external_task_id", row.external_task_id)
            result.setdefault("source_package_id", source_package_id)
            result.setdefault("strategy_task_integration_id", int(row.id))
    elif cmd.task_type == "model_capability":
        platform_status, message, result = await _orchestrate_model_capability(
            db,
            cmd=cmd,
            row=row,
        )

    if result is not None:
        if cmd.task_type == MODEL_CAPABILITY_TASK_TYPE:
            initial_stage = "training"
        else:
            initial_stage = (
                "export" if platform_status == "export_created" else "edge_validation"
            )
        initial_status = (
            "failed"
            if platform_status in {"failed", "comparison_failed", "callback_failed"}
            else "running"
        )
        row = _persist_strategy_snapshot(
            db,
            external_task_id=cmd.external_task_id,
            platform_status=platform_status,
            automation_stage=initial_stage,
            automation_status=initial_status,
            result=result,
            last_error=(
                _json_dumps(result)
                if platform_status in {"waiting_for_model", "waiting_for_annotations"}
                else None
            ),
        ) or row

    return StrategyEvolutionTaskAcceptedResponse(message=message)


async def get_task_detail(
    db: Session,
    *,
    external_task_id: str,
) -> StrategyEvolutionTaskDetailResponse | None:
    row = crud_strategy_task_integration.get_by_external_task_id(
        db=db,
        external_task_id=external_task_id,
    )
    if not row:
        return None
    return _to_detail(row)


def retry_outbound_callback(
    db: Session,
    *,
    external_task_id: str,
    callback_url: str | None = None,
) -> StrategyEvolutionTaskDetailResponse:
    """Resend a persisted terminal result without restarting automation."""
    row = crud_strategy_task_integration.get_by_external_task_id(
        db=db,
        external_task_id=external_task_id,
        lock=True,
    )
    if not row:
        raise StrategyEvolutionError(
            404, f"strategy task not found: {external_task_id}"
        )

    platform_status = str(row.platform_status or "").strip()
    if platform_status not in FINAL_PLATFORM_STATUSES:
        raise StrategyEvolutionError(
            409,
            f"strategy task is not terminal: {external_task_id} ({platform_status or 'unknown'})",
        )

    requested_url = _validate_task_callback_url(
        task_type=str(row.task_type or ""),
        callback_url=str(callback_url or "").strip() or None,
    )
    if requested_url:
        with begin_transaction(db):
            crud_strategy_task_integration.update(
                db=db,
                row=row,
                obj_in={StrategyTaskIntegration.outbound_callback_url.key: requested_url},
            )
        db.refresh(row)

    result = _json_loads_object(row.platform_result_json)
    result_payload = None
    error_payload = None
    if platform_status == "succeeded":
        result_payload = _build_outbound_callback_result_payload(
            db,
            row=row,
            result=result,
        )
    else:
        stored_error = _json_loads_object(row.platform_error_json)
        comparison = (
            result.get("comparison")
            if isinstance(result.get("comparison"), dict)
            else {}
        )
        error_payload = {
            "code": platform_status.upper(),
            "message": str(
                stored_error.get("message")
                or result.get("message")
                or comparison.get("reason")
                or platform_status
            ),
        }

    _send_outbound_callback(
        db,
        row=row,
        result_payload=result_payload,
        error_payload=error_payload,
    )
    db.expire_all()
    refreshed = crud_strategy_task_integration.get_by_external_task_id(
        db=db,
        external_task_id=external_task_id,
    )
    if not refreshed:
        raise StrategyEvolutionError(
            404, f"strategy task not found: {external_task_id}"
        )
    return _to_detail(refreshed)


async def list_tasks(
    db: Session,
    *,
    task_type: str | None,
    platform_status: str | None,
    keyword: str | None,
    external_task_id: str | None,
    model_id: str | None,
    page: int,
    size: int,
) -> tuple[list[StrategyEvolutionTaskListRowResponse], MetaData]:
    rows, total = crud_strategy_task_integration.list_rows(
        db=db,
        task_type=task_type,
        platform_status=platform_status,
        keyword=keyword,
        external_task_id=external_task_id,
        model_id=model_id,
        page=page,
        size=size,
    )
    data = [_to_list_row(row) for row in rows]
    return data, MetaData(total=total, page=page, size=size)


def _list_strategy_auto_advance_task_ids(db: Session) -> list[str]:
    now = datetime.now()
    max_callback_attempts = int(settings.STRATEGY_EVOLUTION_CALLBACK_MAX_ATTEMPTS)
    rows = (
        db.query(StrategyTaskIntegration.external_task_id)
        .filter(
            StrategyTaskIntegration.deleted_at.is_(None),
            StrategyTaskIntegration.automation_source == "strategy_evolution",
            StrategyTaskIntegration.task_type.in_(
                (THRESHOLD_TASK_TYPE, MODEL_CAPABILITY_TASK_TYPE)
            ),
            or_(
                StrategyTaskIntegration.automation_lease_owner.is_(None),
                StrategyTaskIntegration.automation_lease_expires_at.is_(None),
                StrategyTaskIntegration.automation_lease_expires_at <= now,
            ),
            or_(
                StrategyTaskIntegration.automation_status == "running",
                # Older rows may have persisted a waiting platform status with
                # a terminal automation status. Waiting is inherently
                # retryable, so the platform status must keep them discoverable.
                StrategyTaskIntegration.platform_status.in_(
                    ("waiting_for_model", "waiting_for_annotations")
                ),
                and_(
                    StrategyTaskIntegration.platform_status.in_(
                        tuple(FINAL_PLATFORM_STATUSES)
                    ),
                    or_(
                        StrategyTaskIntegration.outbound_callback_status.is_(None),
                        StrategyTaskIntegration.outbound_callback_status.notin_(("succeeded", "skipped")),
                    ),
                    StrategyTaskIntegration.outbound_callback_attempts
                    < max_callback_attempts,
                    or_(
                        StrategyTaskIntegration.outbound_callback_next_retry_at.is_(None),
                        StrategyTaskIntegration.outbound_callback_next_retry_at <= now,
                    ),
                ),
            ),
        )
        .order_by(StrategyTaskIntegration.id.asc())
        .all()
    )
    return [str(item[0]) for item in rows if item and item[0]]


def auto_advance_strategy_task(external_task_id: str) -> bool:
    task_id = str(external_task_id or "").strip()
    if not task_id:
        return False
    lease_owner = f"{_AUTOMATION_WORKER_ID}:{uuid.uuid4().hex}"
    with session_scope() as db:
        now = datetime.now()
        lease_expires_at = now + timedelta(
            seconds=int(settings.STRATEGY_EVOLUTION_AUTOMATION_LEASE_SECONDS)
        )
        with begin_transaction(db):
            claimed = crud_strategy_task_integration.claim_automation_lease(
                db,
                external_task_id=task_id,
                lease_owner=lease_owner,
                now=now,
                lease_expires_at=lease_expires_at,
            )
        if not claimed:
            return False
        try:
            row = crud_strategy_task_integration.get_by_external_task_id(
                db=db,
                external_task_id=task_id,
            )
            if not row:
                return False
            before = (
                str(row.platform_status or ""),
                str(row.automation_stage or ""),
                str(row.automation_status or ""),
                str(row.outbound_callback_status or ""),
                int(row.outbound_callback_attempts or 0),
                str(row.platform_result_json or ""),
            )
            asyncio.run(_advance_automation(db, row=row, lease_owner=lease_owner))
            db.expire_all()
            refreshed = crud_strategy_task_integration.get_by_external_task_id(
                db=db,
                external_task_id=task_id,
            )
            if not refreshed:
                return False
            after = (
                str(refreshed.platform_status or ""),
                str(refreshed.automation_stage or ""),
                str(refreshed.automation_status or ""),
                str(refreshed.outbound_callback_status or ""),
                int(refreshed.outbound_callback_attempts or 0),
                str(refreshed.platform_result_json or ""),
            )
            return before != after
        except _AutomationLeaseLost:
            logger.info(
                "strategy evolution auto-advance lease lost external_task_id={}",
                task_id,
            )
            return False
        finally:
            with begin_transaction(db):
                crud_strategy_task_integration.release_automation_lease(
                    db,
                    external_task_id=task_id,
                    lease_owner=lease_owner,
                )


def scan_and_auto_advance_strategy_tasks() -> int:
    if not settings.STRATEGY_EVOLUTION_AUTO_ADVANCE_ENABLED:
        return 0
    with session_scope() as db:
        external_task_ids = _list_strategy_auto_advance_task_ids(db)
    advanced = 0
    for external_task_id in external_task_ids:
        try:
            if auto_advance_strategy_task(external_task_id):
                advanced += 1
        except Exception:
            logger.exception(
                "strategy evolution auto-advance failed external_task_id={}",
                external_task_id,
            )
    if advanced:
        logger.info("strategy evolution auto-advance: advanced {} task(s)", advanced)
    return advanced


def schedule_strategy_evolution_watchdog() -> None:
    global _WATCHDOG_STARTED
    if not settings.STRATEGY_EVOLUTION_AUTO_ADVANCE_ENABLED:
        return
    interval = max(
        10,
        int(settings.STRATEGY_EVOLUTION_AUTO_ADVANCE_SCAN_INTERVAL_SECONDS),
    )
    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    def _tick() -> None:
        try:
            scan_and_auto_advance_strategy_tasks()
        except Exception:
            logger.exception("strategy evolution watchdog tick failed")
        timer = threading.Timer(float(interval), _tick)
        timer.daemon = True
        timer.start()

    threading.Thread(
        target=scan_and_auto_advance_strategy_tasks,
        daemon=True,
        name="strategy-evolution-auto-advance",
    ).start()
    timer = threading.Timer(float(interval), _tick)
    timer.daemon = True
    timer.start()
    logger.info(
        "strategy evolution watchdog started (interval={}s)",
        interval,
    )


async def _handle_platform_callback_once(
    db: Session,
    *,
    payload: dict[str, Any],
) -> StrategyEvolutionPlatformCallbackResponse:
    try:
        cmd = StrategyEvolutionPlatformCallbackCommand.model_validate(payload)
    except ValidationError as exc:
        raise StrategyEvolutionError(422, _validation_message(exc)) from exc

    if cmd.schema_version != "v1":
        raise StrategyEvolutionError(
            400,
            f"无效的策略演化平台回调参数，不支持的 schema_version：{cmd.schema_version}",
        )

    normalized_status = _normalize_status(cmd.status)
    with begin_transaction(db):
        row = crud_strategy_task_integration.get_by_external_task_id(
            db=db,
            external_task_id=cmd.external_task_id,
            lock=True,
        )
        if not row:
            raise StrategyEvolutionError(
                404,
                f"未找到策略演化任务：{cmd.external_task_id}",
            )
        if row.task_type != cmd.task_type:
            raise StrategyEvolutionError(
                409,
                f"策略演化任务类型不匹配：期望 {row.task_type}，实际收到 {cmd.task_type}",
            )

        callback_event_id = str(cmd.callback_event_id or "").strip()
        existing_event_id = str(row.platform_callback_event_id or "").strip()
        current_status = str(row.platform_status or "").strip().lower()
        current_normalized_status = STATUS_ALIASES.get(current_status, current_status)
        receipt = (
            crud_strategy_task_integration.get_platform_callback_receipt(
                db=db,
                strategy_task_integration_id=row.id,
                callback_event_id=callback_event_id,
            )
            if callback_event_id
            else None
        )
        if receipt or (callback_event_id and callback_event_id == existing_event_id):
            if receipt is None:
                crud_strategy_task_integration.create_platform_callback_receipt(
                    db=db,
                    strategy_task_integration_id=row.id,
                    callback_event_id=callback_event_id,
                    platform_status=current_normalized_status or normalized_status,
                )
            return StrategyEvolutionPlatformCallbackResponse(
                callback_id=callback_event_id,
                task_id=cmd.external_task_id,
                task_type=cmd.task_type,
                status=current_normalized_status or normalized_status,
                external_platform=EXTERNAL_PLATFORM,
            )

        if (
            current_normalized_status in FINAL_PLATFORM_STATUSES
            and normalized_status != current_normalized_status
        ):
            raise StrategyEvolutionError(
                409,
                "策略演化任务已进入终态 "
                f"{current_normalized_status}，拒绝回退到 {normalized_status}",
            )

        if callback_event_id:
            crud_strategy_task_integration.create_platform_callback_receipt(
                db=db,
                strategy_task_integration_id=row.id,
                callback_event_id=callback_event_id,
                platform_status=normalized_status,
            )

        crud_strategy_task_integration.update(
            db=db,
            row=row,
            obj_in={
                StrategyTaskIntegration.platform_status.key: normalized_status,
                StrategyTaskIntegration.platform_progress_json.key: (
                    _json_dumps(cmd.progress)
                    if cmd.progress is not None
                    else row.platform_progress_json
                ),
                StrategyTaskIntegration.platform_result_json.key: (
                    _json_dumps(cmd.result)
                    if cmd.result is not None
                    else row.platform_result_json
                ),
                StrategyTaskIntegration.platform_error_json.key: (
                    _json_dumps(cmd.error)
                    if cmd.error is not None
                    else row.platform_error_json
                ),
                StrategyTaskIntegration.platform_callback_event_id.key: callback_event_id
                or row.platform_callback_event_id,
                StrategyTaskIntegration.platform_finished_at.key: cmd.finished_at
                or row.platform_finished_at,
                StrategyTaskIntegration.last_error.key: (
                    _json_dumps(cmd.error) if cmd.error is not None else None
                ),
            },
        )

    return StrategyEvolutionPlatformCallbackResponse(
        callback_id=cmd.callback_event_id or f"strategy-callback-{row.id}",
        task_id=cmd.external_task_id,
        task_type=cmd.task_type,
        status=normalized_status,
        external_platform=EXTERNAL_PLATFORM,
    )


async def handle_platform_callback(
    db: Session,
    *,
    payload: dict[str, Any],
) -> StrategyEvolutionPlatformCallbackResponse:
    callback_event_id = str(
        payload.get("callback_event_id") or payload.get("event_id") or ""
    ).strip()
    last_race_error: IntegrityError | OperationalError | None = None
    for attempt in range(3):
        try:
            return await _handle_platform_callback_once(db=db, payload=payload)
        except IntegrityError as exc:
            if not callback_event_id:
                raise
            last_race_error = exc
        except OperationalError as exc:
            bind = db.get_bind()
            is_sqlite_lock = (
                bind.dialect.name == "sqlite" and "locked" in str(exc).lower()
            )
            if not callback_event_id or not is_sqlite_lock:
                raise
            last_race_error = exc

        # SQLite ignores SELECT FOR UPDATE, so a concurrent winner may either
        # take the receipt unique key or briefly hold the database write lock.
        db.rollback()
        if attempt < 2:
            await asyncio.sleep(0.02 * (attempt + 1))

    if last_race_error is not None:
        raise last_race_error
    raise RuntimeError("strategy callback retry exited without a result")
