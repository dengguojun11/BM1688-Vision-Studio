import json
import hashlib
import os
import re
import shutil
import statistics
import tarfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import status
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_attachment,
    crud_sample,
    crud_task,
    crud_yolo_bm1688_export,
    crud_yolo_edge_validation_job,
    crud_yolo_train_job,
)
from labelu.internal.application.command.yolo_edge_validation import (
    CreateYoloEdgeValidationJobCommand,
    normalize_edge_threshold_payload,
)
from labelu.internal.application.response.base import MetaData
from labelu.internal.application.response.yolo_edge_validation import (
    EdgeThresholdsResponse,
    EdgeThresholdsSnapshotResponse,
    YoloEdgeValidationProgressResponse,
    YoloEdgeValidationJobDetailResponse,
    YoloEdgeValidationJobRowResponse,
)
from labelu.internal.application.service import edge_ssh_validation
from labelu.internal.application.service import edge_runtime_provision
from labelu.internal.application.service.attachment import build_attachment_response
from labelu.internal.application.service.yolo_sop_eval_analysis import (
    build_sop_eval_analysis,
)
from labelu.internal.common.storage import build_attachment_api_path
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.application.service.yolo_bm1688_export import (
    _compile_platforms,
    _export_dir_for_row,
    get_export_for_download,
)
from labelu.internal.application.service.yolo_edge_validation_pack import (
    class_contract_for_export,
    clear_job_pack,
    edge_validation_job_root,
    pack_selected_samples_for_job,
    _manifest_sha256,
)
from labelu.internal.application.service.edge_queue_timing import (
    estimate_for_row as _estimate_edge_row,
    history_durations as _history_edge_durations,
    profile_duration_medians as _profile_edge_duration_medians,
    timing_for_row as _timing_for_edge_row,
)
from labelu.internal.common.archive import safe_extract_tar, safe_extract_zip
from labelu.internal.common.bm1688_quality_rules import get_bm1688_quality_rule
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.execution_fencing import (
    conditional_execution_update,
    ExecutionFencingLost,
    ExecutionFencingUnavailable,
    ExecutionLeaseMonitor,
    claim_execution_lease,
)
from labelu.internal.common.project_scope import (
    ProjectScopeError,
    assert_project_chain,
    task_project_id,
)
from labelu.internal.common.phase_one_contract import (
    ensure_phase_one_bm1688_platforms,
    ensure_phase_one_training_job_type,
)
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob

_job_run_locks_guard = threading.Lock()
_job_run_locks: dict[int, threading.Lock] = {}


def _job_run_lock(job_id: int) -> threading.Lock:
    with _job_run_locks_guard:
        lock = _job_run_locks.get(int(job_id))
        if lock is None:
            lock = threading.Lock()
            _job_run_locks[int(job_id)] = lock
        return lock


def _registered_board_agent_device(device_id: object) -> bool:
    """Return whether a device id is owned by the pull-based Board Agent.

    The legacy runner is still used for SSH devices, but it must never execute
    a job assigned to a registered Board Agent.  Missing records are retained
    for pre-registry jobs; registry read failures fail closed so a transient
    control-plane outage cannot create a second consumer.
    """

    normalized = str(device_id or "").strip()
    if not normalized:
        return False
    try:
        from labelu.internal.common import edge_device_registry

        record = edge_device_registry.find_device_record(normalized)
    except Exception:
        logger.exception(
            "读取边端设备通道失败，阻止 SSH Runner job device_id={}", normalized
        )
        return True
    return bool(
        isinstance(record, dict)
        and str(record.get("validation_channel") or "ssh").strip().lower()
        == "board_agent"
    )


def _touch_manual_job_heartbeat(
    job_id: int,
    *,
    execution_monitor: ExecutionLeaseMonitor | None = None,
) -> None:
    """Refresh a running job only while this worker still owns its lease.

    The heartbeat is consumed by stale-job recovery.  An unfenced write here
    would let a worker that has already been taken over keep the row looking
    fresh and indefinitely delay requeue of the old execution.
    """
    try:
        with session_scope() as heartbeat_db:
            heartbeat_row = crud_yolo_edge_validation_job.get(heartbeat_db, int(job_id))
            if not heartbeat_row or str(heartbeat_row.status or "") != "PROCESSING":
                return
            if execution_monitor is not None:
                execution_monitor.conditional_update(
                    {"updated_at": datetime.now()},
                    db=heartbeat_db,
                    manage_transaction=True,
                )
            else:
                with begin_transaction(heartbeat_db):
                    crud_yolo_edge_validation_job.update(
                        heartbeat_db,
                        heartbeat_row,
                        updated_at=datetime.now(),
                    )
    except ExecutionFencingLost:
        # The execution loop will stop at its next side-effect gate.  The
        # heartbeat must not keep touching the row after takeover.
        logger.info("skip stale edge validation heartbeat job_id={}", job_id)
    except Exception:
        logger.exception("manual edge validation heartbeat failed job_id={}", job_id)


@contextmanager
def _manual_job_heartbeat(
    job_id: int,
    *,
    interval_seconds: float = 30.0,
    execution_monitor: ExecutionLeaseMonitor | None = None,
):
    stop = threading.Event()

    def _run() -> None:
        while not stop.wait(max(0.01, float(interval_seconds))):
            if execution_monitor is None:
                _touch_manual_job_heartbeat(job_id)
            else:
                _touch_manual_job_heartbeat(
                    job_id,
                    execution_monitor=execution_monitor,
                )

    thread = threading.Thread(
        target=_run,
        daemon=True,
        name=f"yolo-edge-validation-heartbeat-{int(job_id)}",
    )
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)


def edge_validation_stale_minutes() -> int:
    return max(1, int(getattr(settings, "YOLO_EDGE_VALIDATION_STALE_MINUTES", 5)))


def _edge_validation_reference_time(row) -> datetime | None:
    for attr in ("updated_at", "started_at", "created_at"):
        value = getattr(row, attr, None)
        if value is not None:
            return value
    return None


def is_stale_edge_validation_job(row, *, now: datetime | None = None) -> bool:
    if row is None or str(getattr(row, "status", "") or "") != "PROCESSING":
        return False
    ref = _edge_validation_reference_time(row)
    if ref is None:
        return True
    now = now or datetime.now()
    age_sec = max(0.0, (now - ref).total_seconds())
    return age_sec >= edge_validation_stale_minutes() * 60


def can_resume_edge_validation_job(row) -> bool:
    status_value = str(getattr(row, "status", "") or "")
    if status_value in ("PENDING", "FAILED", "COMPLETED", "PARTIAL_COMPLETED"):
        return True
    if status_value == "PROCESSING":
        return is_stale_edge_validation_job(row)
    return False


def _mark_edge_validation_recovery(db: Session, row, *, reason: str) -> None:
    with begin_transaction(db):
        crud_yolo_edge_validation_job.update(
            db,
            row,
            error_message=None,
            report_summary=reason[:500],
        )
        _update_manual_job_progress(
            db,
            row,
            stage="recover",
            message=reason,
            detail={"job_id": int(row.id), "export_id": int(row.export_id)},
            report_summary=reason[:500],
        )


def _decode_json(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _model_dump_compat(model) -> dict:
    if hasattr(model, "model_dump"):
        data = model.model_dump()
    elif hasattr(model, "dict"):
        data = model.dict()
    else:
        raise TypeError(f"Unsupported model type: {type(model)!r}")
    return data if isinstance(data, dict) else dict(data or {})


def _sample_has_annotation(sample) -> bool:
    if int(getattr(sample, "annotated_count", 0) or 0) > 0:
        return True
    data = _decode_json(getattr(sample, "data", None), {})
    result = data.get("result")
    if isinstance(result, str):
        result = _decode_json(result, {})
    if not isinstance(result, dict):
        return False
    rects = (result.get("rectTool") or {}).get("result") or []
    return isinstance(rects, list) and len(rects) > 0


def _normalize_thresholds(
    thresholds: dict, threshold_source: str | None
) -> EdgeThresholdsResponse:
    conf_default = float(settings.YOLO_SOP_EVAL_CONF_THRES)
    nms_default = 0.7
    match_iou_default = float(settings.YOLO_SOP_EVAL_IOU_THRES)
    try:
        normalized = normalize_edge_threshold_payload(
            thresholds or {},
            field_name="thresholds",
        )
        defaults = {
            "conf_thres": conf_default,
            "nms_thres": nms_default,
            "match_iou_thres": match_iou_default,
        }
        requested = {key: normalized[key] for key in defaults if key in normalized}
        defaulted_fields = [
            key
            for key in defaults
            if key not in normalized or normalized.get(key) is None
        ]
        for key, default_value in defaults.items():
            if normalized.get(key) is None:
                normalized[key] = default_value
        normalized = normalize_edge_threshold_payload(
            normalized,
            field_name="thresholds",
        )
    except ValueError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=str(exc),
        ) from exc

    source = (threshold_source or "").strip()
    if not source:
        source = "manual" if requested else "system_default"
    return EdgeThresholdsResponse(
        conf_thres=normalized["conf_thres"],
        nms_thres=normalized["nms_thres"],
        match_iou_thres=normalized["match_iou_thres"],
        source=source,
        reason=str(normalized.get("reason") or "").strip() or None,
        confidence_level=str(normalized.get("confidence_level") or "").strip() or None,
        provider_key=str(normalized.get("provider_key") or "").strip() or None,
        requested=requested,
        defaulted_fields=defaulted_fields,
    )


def _edge_result_status_for_job(row) -> str:
    status_value = str(getattr(row, "status", "") or "").upper()
    if status_value in {"PENDING", "PROCESSING"}:
        return "running"
    if status_value == "FAILED":
        return "fail"
    if status_value == "PARTIAL_COMPLETED":
        return "result_pending"
    if status_value != "COMPLETED":
        return "unknown"
    result = _decode_json(getattr(row, "result_json", None), {})
    if not isinstance(result, dict) or not result:
        return "result_pending"
    # Keep manual and board-agent jobs on the same quality-state contract.
    # ``eval_summary.status=ok`` only proves that evaluation ran; the nested
    # analysis/comparison verdict decides whether the model is usable.
    from labelu.internal.application.service.board_agent import (
        edge_result_status_from_report,
    )

    derived = edge_result_status_from_report({"result": result})
    if derived != "unknown":
        return derived
    return "result_pending"


def _to_row_response(
    row, task_name: str | None, *, queue_position: int | None = None
) -> YoloEdgeValidationJobRowResponse:
    thresholds = _decode_json(getattr(row, "thresholds_json", None), {})
    ai_thresholds = _decode_json(getattr(row, "ai_thresholds_json", None), None)
    # Import lazily to keep the existing validation-service import graph
    # acyclic.  The queue center owns the canonical operator-state projection
    # used by both the paginated queue and the detail drawer.
    from labelu.internal.application.service.edge_queue_center import (
        _display_status,
    )
    return YoloEdgeValidationJobRowResponse(
        id=row.id,
        export_id=row.export_id,
        task_id=row.task_id,
        task_name=task_name,
        source_type=str(getattr(row, "source_type", None) or "internal"),
        source_system=getattr(row, "source_system", None),
        pipeline_origin=getattr(row, "pipeline_origin", None),
        source_external_task_id=getattr(row, "source_external_task_id", None),
        source_external_package_id=getattr(row, "source_external_package_id", None),
        source_integration_id=getattr(row, "source_integration_id", None),
        status=row.status,
        display_status=_display_status(row),
        mode=row.mode,
        execution_profile=_job_execution_profile(row),
        image_count=int(row.image_count or 0),
        labeled_image_count=int(row.labeled_image_count or 0),
        unlabeled_image_count=int(row.unlabeled_image_count or 0),
        threshold_source=row.threshold_source,
        edge_device_id=getattr(row, "edge_device_id", None),
        edge_device_name=getattr(row, "edge_device_name", None),
        edge_host=getattr(row, "edge_host", None),
        edge_port=getattr(row, "edge_port", None),
        edge_user=getattr(row, "edge_user", None),
        queue_reason=getattr(row, "queue_reason", None),
        queue_position=queue_position,
        queued_at=getattr(row, "queued_at", None),
        assigned_at=getattr(row, "assigned_at", None),
        lease_expires_at=getattr(row, "lease_expires_at", None),
        thresholds=(
            EdgeThresholdsSnapshotResponse(**thresholds)
            if isinstance(thresholds, dict) and thresholds
            else None
        ),
        ai_thresholds=ai_thresholds if isinstance(ai_thresholds, dict) else None,
        workflow_stage="edge_validation",
        edge_result_status=_edge_result_status_for_job(row),
        report_summary=row.report_summary,
        progress=_job_progress_response(row),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _canonical_queue_positions(
    db: Session | None,
    rows: list[Any] | tuple[Any, ...],
    current_user: User | None,
) -> dict[int, int]:
    """Return queue ranks using the task-center's canonical projection.

    The legacy validation endpoints still serve older clients, but their
    queue position must describe the same queue as the high-volume task
    center.  Keep the import lazy because the task-center imports this module
    for result/timing helpers.  A failure to calculate a rank is fail-closed:
    returning ``None`` is safer than presenting a stale or contradictory
    position to an operator.
    """

    if db is None or current_user is None:
        return {}
    job_ids = sorted(
        {
            int(getattr(row, "id", 0) or 0)
            for row in rows
            if int(getattr(row, "id", 0) or 0) > 0
        }
    )
    if not job_ids:
        return {}
    try:
        # Import here rather than at module load time to avoid the
        # yolo_edge_validation <-> edge_queue_center import cycle.
        from labelu.internal.application.service import edge_queue_center

        scoped_query = crud_yolo_edge_validation_job.query_for_user(
            db,
            int(getattr(current_user, "id", 0) or 0),
            task_access_unrestricted=is_task_superuser(current_user),
        )
        return edge_queue_center._queue_position_map_for_ids(
            db,
            scoped_query,
            job_ids,
        )
    except Exception:
        logger.exception(
            "读取边端验证任务 canonical queue position 失败 job_ids={}",
            job_ids,
        )
        return {}


def _detail_timing(
    row, *, now: datetime | None = None
) -> tuple[int | None, int | None]:
    """Compute queue wait and execution durations for the detail contract."""

    return _timing_for_edge_row(row, current=now)


def _detail_eta_context(
    db: Session | None,
    current_user: User | None,
) -> tuple[float | None, dict[str, float]]:
    """Load the same bounded history sample used by the queue list."""

    if db is None or current_user is None:
        return None, {}
    try:
        history_rows = (
            crud_yolo_edge_validation_job.query_for_user(
                db,
                int(getattr(current_user, "id", 0) or 0),
                task_access_unrestricted=is_task_superuser(current_user),
            )
            .with_entities(YoloEdgeValidationJob)
            .filter(
                YoloEdgeValidationJob.status.in_(("COMPLETED", "PARTIAL_COMPLETED")),
                YoloEdgeValidationJob.started_at.isnot(None),
                YoloEdgeValidationJob.completed_at.isnot(None),
            )
            .order_by(YoloEdgeValidationJob.completed_at.desc())
            .limit(200)
            .all()
        )
        rows = [item[0] if isinstance(item, tuple) else item for item in history_rows]
        durations = _history_edge_durations(rows)
        return (
            statistics.median(durations) if durations else None,
            _profile_edge_duration_medians(rows),
        )
    except Exception:
        logger.exception("读取边端任务详情 ETA 历史失败")
        return None, {}


def _to_detail_response(
    row,
    task_name: str | None,
    *,
    queue_position: int | None = None,
    db: Session | None = None,
    current_user: User | None = None,
    median_duration: float | None = None,
    median_by_profile: dict[str, float] | None = None,
) -> YoloEdgeValidationJobDetailResponse:
    thresholds = _decode_json(row.thresholds_json, {})
    ai_thresholds = _decode_json(row.ai_thresholds_json, None)
    result = _decode_json(row.result_json, None)
    if isinstance(result, dict):
        result = _hydrate_edge_quality_evidence(row, result)
    sample_ids = _decode_json(row.sample_ids_json, [])
    request_payload = _job_request_payload(row)
    attachment_ids = request_payload.get("attachment_ids") or []
    external_attachments = request_payload.get("external_attachments") or []
    row_payload = _model_dump_compat(
        _to_row_response(row, task_name, queue_position=queue_position)
    )
    row_payload["thresholds"] = EdgeThresholdsResponse(**thresholds)
    row_payload["ai_thresholds"] = (
        ai_thresholds if isinstance(ai_thresholds, dict) else None
    )
    snapshot_now = datetime.now()
    waiting_seconds, running_seconds = _detail_timing(row, now=snapshot_now)
    if median_duration is None and median_by_profile is None:
        median_duration, median_by_profile = _detail_eta_context(db, current_user)
    estimated_seconds = _estimate_edge_row(
        row,
        median_duration=median_duration,
        median_by_profile=median_by_profile,
        current=snapshot_now,
    )
    return YoloEdgeValidationJobDetailResponse(
        **row_payload,
        sample_ids=sample_ids if isinstance(sample_ids, list) else [],
        attachment_ids=attachment_ids if isinstance(attachment_ids, list) else [],
        external_attachments=(
            external_attachments if isinstance(external_attachments, list) else []
        ),
        result=result if isinstance(result, dict) else None,
        error_message=row.error_message,
        started_at=row.started_at,
        completed_at=row.completed_at,
        waiting_seconds=waiting_seconds,
        running_seconds=running_seconds,
        estimated_seconds=estimated_seconds,
    )


def _job_result_json(row) -> dict:
    data = _decode_json(getattr(row, "result_json", None), {})
    return data if isinstance(data, dict) else {}


def _plot_warning(value: Any) -> dict[str, str] | None:
    """Normalize the optional plot dependency warning without treating it as a run error."""
    text = str(value or "").strip()
    if not text or "matplotlib" not in text.lower() or "曲线图" not in text:
        return None
    return {
        "code": "PLOT_DEPENDENCY_MISSING",
        "message": "曲线图未生成：边端缺少 matplotlib（不影响推理和精度计算）",
    }


def _hydrate_edge_quality_evidence(row, result: dict) -> dict:
    """Backfill quality-gate and optional-artifact evidence for older local jobs.

    Early manual jobs persisted the useful model metrics but only kept the
    plotting warning in ``error_message``.  Read the already pulled local JSON
    files on detail requests so those historical results are displayed with
    the same execution/quality split as new jobs.  This is read-only and does
    not create or rerun a validation task.
    """
    if not isinstance(result, dict):
        return result
    result = dict(result)
    execution = result.get("execution")
    execution = dict(execution) if isinstance(execution, dict) else {}
    warnings = []
    for value in (
        execution.get("error"),
        execution.get("stderr_tail"),
        getattr(row, "error_message", None),
    ):
        warning = _plot_warning(value)
        if warning and warning["code"] not in {item.get("code") for item in warnings}:
            warnings.append(warning)

    output_root = _manual_output_root(int(row.id), int(row.export_id))
    bmodel_path = output_root / "bmodel_sop_eval.json"
    board_path = output_root / "board_report.json"
    bmodel_sop = {}
    board_report = {}
    for path, target in ((bmodel_path, "bmodel"), (board_path, "board")):
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            if target == "bmodel":
                bmodel_sop = payload
            else:
                board_report = payload

    metric_warnings = (
        bmodel_sop.get("warnings") if isinstance(bmodel_sop, dict) else None
    )
    if isinstance(metric_warnings, list):
        warnings.extend(item for item in metric_warnings if isinstance(item, dict))
    stderr_warning = (
        _plot_warning(bmodel_sop.get("stderr_tail")) if bmodel_sop else None
    )
    if stderr_warning:
        warnings.append(stderr_warning)
    if warnings:
        unique = {}
        for item in warnings:
            code = str(item.get("code") or "WARNING")
            unique[code] = {
                "code": code,
                "message": str(item.get("message") or "").strip(),
            }
        result["warnings"] = list(unique.values())
        execution["warnings"] = result["warnings"]
        if _plot_warning(execution.get("error")):
            execution["error"] = None

    quality_gate = (
        bmodel_sop.get("usability_gate") if isinstance(bmodel_sop, dict) else None
    )
    if isinstance(quality_gate, dict):
        result["quality_gate"] = quality_gate
    fixed_eval = (
        board_report.get("fixed_eval_dataset")
        if isinstance(board_report, dict)
        else None
    )
    if isinstance(fixed_eval, dict):
        result["fixed_eval_dataset"] = fixed_eval
        if not isinstance(result.get("quality_gate"), dict):
            reasons = list(fixed_eval.get("binding_reasons") or [])
            if reasons:
                result["quality_gate"] = {
                    "status": "fail",
                    "reasons": reasons,
                    "fixed_eval": fixed_eval,
                }

    # Older records stored the quality-gate exit code as the only runtime
    # signal. Rebuild just the display dimension from persisted per-precision
    # evidence so a gate failure is not shown as inference failure. This is
    # read-only; the historical gate remains unchanged until a rerun.
    persisted_eval_models = result.get("eval_models")
    if isinstance(persisted_eval_models, dict) and persisted_eval_models:
        persisted_execution = result.get("execution")
        persisted_execution = (
            persisted_execution if isinstance(persisted_execution, dict) else {}
        )
        persisted_summary = result.get("execution_summary")
        persisted_summary = (
            persisted_summary if isinstance(persisted_summary, dict) else {}
        )
        recomputed = _build_manual_validation_dimensions(
            mode=str(getattr(row, "mode", None) or "run_and_eval"),
            manifest=result.get("pack") if isinstance(result.get("pack"), dict) else {},
            summary={
                **persisted_summary,
                "remote_exit_code": persisted_summary.get(
                    "remote_exit_code", persisted_execution.get("remote_exit_code")
                ),
                "environment": persisted_execution.get("environment") or {},
            },
            ingest={
                "bmodel_sop": {
                    "status": (result.get("eval_summary") or {}).get("status"),
                    "models": persisted_eval_models,
                },
                "board_report": board_report,
            },
            result_summaries={
                "eval_models": persisted_eval_models,
                "prediction_summary": result.get("prediction_summary") or {},
            },
        )
        dimensions = result.get("validation_dimensions")
        if isinstance(dimensions, dict):
            dimensions = dict(dimensions)
            dimensions["inference"] = recomputed.get("inference")
            result["validation_dimensions"] = dimensions
    result["execution"] = execution
    return result


def _assert_edge_result_class_contract(
    expected: dict | None, reported: dict | None
) -> None:
    expected = expected if isinstance(expected, dict) else {}
    reported = reported if isinstance(reported, dict) else {}
    if not str(expected.get("class_order_signature") or "").strip():
        return
    mismatches = []
    for key in (
        "class_names",
        "label_set_signature",
        "class_order_signature",
        "data_yaml_sha256",
        "dataset_content_signature",
    ):
        if expected.get(key) != reported.get(key):
            mismatches.append(key)
    if mismatches:
        raise RuntimeError("边端回传类别契约与训练作业不一致: " + ", ".join(mismatches))


def _job_progress_payload(row) -> dict:
    data = _decode_json(getattr(row, "progress_json", None), {})
    return data if isinstance(data, dict) else {}


def _job_progress_response(row) -> YoloEdgeValidationProgressResponse | None:
    data = _job_progress_payload(row)
    if not data:
        return None
    history = data.get("history") if isinstance(data.get("history"), list) else []
    return YoloEdgeValidationProgressResponse(
        stage=str(data.get("stage") or ""),
        message=str(data.get("message") or ""),
        detail=data.get("detail") if isinstance(data.get("detail"), dict) else None,
        updated_at=str(data.get("updated_at") or "") or None,
        history=history[-25:],
    )


def _job_request_payload(row) -> dict:
    request = _job_result_json(row).get("request") or {}
    return request if isinstance(request, dict) else {}


def _job_execution_profile(row) -> str:
    value = str(_job_request_payload(row).get("execution_profile") or "quick")
    return value if value in {"quick", "certification"} else "quick"


def _update_manual_job_progress(
    db: Session,
    row,
    *,
    stage: str,
    message: str,
    detail: dict | None = None,
    report_summary: str | None = None,
) -> None:
    payload = _job_progress_payload(row)
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    entry = {
        "stage": stage,
        "message": message,
        "detail": detail or {},
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    history.append(entry)
    history = history[-25:]
    progress = {
        "stage": stage,
        "message": message,
        "detail": detail or {},
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "history": history,
    }
    update_kwargs = {
        "progress_json": json.dumps(progress, ensure_ascii=False),
    }
    if report_summary is not None:
        update_kwargs["report_summary"] = report_summary
    execution_monitor = getattr(row, "_execution_lease_monitor", None)
    if execution_monitor is not None:
        execution_monitor.conditional_update(update_kwargs, db=db)
        # The conditional update reuses the current transaction. Expire this
        # instance so the next progress event rebuilds its history from DB.
        db.expire(row)
    else:
        crud_yolo_edge_validation_job.update(
            db,
            row,
            **update_kwargs,
        )


def _ensure_manual_execution_current(
    execution_monitor: ExecutionLeaseMonitor | None,
) -> None:
    """在边端网络、文件或结果副作用前阻断失去 fencing 的 worker。"""
    if execution_monitor is not None:
        execution_monitor.ensure_current()


def _requeue_when_execution_fencing_unavailable(db: Session, job_id: int) -> bool:
    """迁移尚未完成时，将边端任务安全地退回队列，禁止设备副作用。"""
    current = crud_yolo_edge_validation_job.get(db, int(job_id))
    if current is None:
        return False
    crud_yolo_edge_validation_job.update(
        db,
        current,
        status="PENDING",
        queue_reason="execution_fencing_unavailable",
        queued_at=datetime.now(),
        assigned_at=None,
        lease_expires_at=None,
        edge_device_id=None,
        edge_device_name=None,
        edge_host=None,
        edge_port=None,
        edge_user=None,
        report_summary="边端执行租约字段不可用，已阻止设备执行，等待迁移完成",
    )
    return True


def _to_float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _manual_bmodel_eval_models(bmodel_sop: dict) -> dict:
    """Normalize runner model blocks into the API's precision-keyed shape."""
    raw = bmodel_sop.get("models") if isinstance(bmodel_sop, dict) else None
    if not isinstance(raw, dict):
        return {}
    models = {}
    for tag in ("fp32", "fp16", "int8"):
        item = raw.get(tag)
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else item
        models[tag] = {
            "model_tag": tag,
            "model_filename": item.get("model_filename"),
            "status": str(item.get("status") or "unknown"),
            "backend": item.get("backend") or "sophon.sail",
            "results_dir": item.get("results_dir"),
            "report_md_path": item.get("report_md_path") or item.get("report_md"),
            "eval_summary_path": item.get("eval_summary_path"),
            "map50": _to_float_or_none(metrics.get("map50")),
            "map50_95": _to_float_or_none(metrics.get("map50_95")),
            "map50_95_status": metrics.get("map50_95_status"),
            "map50_95_approximation": _to_float_or_none(
                metrics.get("map50_95_approximation")
            ),
            "precision": _to_float_or_none(metrics.get("precision")),
            "recall": _to_float_or_none(metrics.get("recall")),
            "f1": _to_float_or_none(metrics.get("f1")),
            "speed_avg_ms": _to_float_or_none(metrics.get("speed_avg_ms")),
            "cpu_memory_mb": _to_float_or_none(metrics.get("cpu_memory_mb")),
            "images": _to_int_or_none(metrics.get("images")),
            "class_metrics": metrics.get("class_metrics")
            or metrics.get("per_class_rows"),
            "business_threshold_analysis": metrics.get("business_threshold_analysis"),
            "performance_probe_status": metrics.get("performance_probe_status"),
            "performance_probe_error": metrics.get("performance_probe_error"),
            "runtime_metrics": metrics.get("runtime_metrics"),
            "resource_usage": metrics.get("resource_usage"),
            "environment": metrics.get("environment"),
            "stability": metrics.get("stability"),
            "performance_artifacts": metrics.get("performance_artifacts"),
            "runtime_ok": metrics.get("runtime_ok"),
            "objects_detected": metrics.get("objects_detected"),
            "model_type": metrics.get("model_type"),
            "input_image_count": _to_int_or_none(metrics.get("input_image_count")),
            "result_image_count": _to_int_or_none(metrics.get("result_image_count")),
            "images_with_detections": _to_int_or_none(
                metrics.get("images_with_detections")
            ),
            "total_detections": _to_int_or_none(metrics.get("total_detections")),
            "bmrt_test": metrics.get("bmrt_test"),
            "inference": metrics.get("inference"),
            "rendered_images": metrics.get("rendered_images"),
        }
    return models


def _manual_eval_dataset_evidence(
    *, job_id: int, export_id: int, manifest: dict
) -> dict:
    mappings = manifest.get("file_mappings") if isinstance(manifest, dict) else []
    stable_mappings = []
    for item in mappings if isinstance(mappings, list) else []:
        if not isinstance(item, dict):
            continue
        stable_mappings.append(
            {
                "sample_id": item.get("sample_id"),
                "task_id": item.get("task_id"),
                "packed_name": item.get("packed_name"),
                "has_annotation": bool(item.get("has_annotation")),
            }
        )
    pack_signature = hashlib.sha256(
        json.dumps(
            stable_mappings,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    class_contract = (
        manifest.get("class_contract")
        if isinstance(manifest.get("class_contract"), dict)
        else {}
    )
    return {
        "source": "manual_edge_validation_pack",
        "export_id": int(export_id),
        "edge_validation_job_id": int(job_id),
        "pack_signature": pack_signature,
        "image_count": int(manifest.get("copied_count") or len(stable_mappings)),
        "labeled_image_count": int(manifest.get("labeled_count") or 0),
        "unlabeled_image_count": int(manifest.get("unlabeled_count") or 0),
        "class_names": manifest.get("class_names") or [],
        "class_order_signature": class_contract.get("class_order_signature"),
        "dataset_content_signature": class_contract.get("dataset_content_signature"),
    }


def _resolve_same_batch_pt_weights(train_job) -> Path:
    candidates = [str(getattr(train_job, "weights_path", None) or "").strip()]
    artifacts = _decode_json(getattr(train_job, "component_artifacts_json", None), {})
    pose = artifacts.get("pose") if isinstance(artifacts, dict) else None
    if isinstance(pose, dict):
        candidates.extend(
            str(pose.get(key) or "").strip() for key in ("pt", "evaluation_weights")
        )
    train_job_id = int(getattr(train_job, "id", 0) or 0)
    if train_job_id > 0:
        candidates.append(
            str(
                edge_ssh_validation._job_work_root(train_job_id)
                / "train"
                / "weights"
                / "best.pt"
            )
        )

    from labelu.internal.application.service.yolo_train import resolve_weights_path

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return resolve_weights_path(candidate)
        except LabelUException:
            path = Path(candidate)
            if path.is_file():
                return path.resolve()
    raise RuntimeError("PT 权重不存在，无法执行同批次精度评估")


def _resolve_plate_detection_pt_weights(train_job) -> Path:
    return _resolve_same_batch_pt_weights(train_job)


def _run_same_batch_pt_eval(
    *,
    job_id: int,
    export_id: int,
    train_job,
    manifest: dict,
) -> dict:
    if int(manifest.get("labeled_count") or 0) <= 0:
        return {
            "status": "skip",
            "reason": "manual_edge_validation_pack_has_no_labeled_images",
        }

    job_root = edge_validation_job_root(export_id=export_id, job_id=job_id)
    # The immutable selected-sample pack is staged under the SSH bundle before
    # upload.  Older jobs materialized ``eval_data`` directly under the job
    # root, so keep that location as a compatibility fallback.
    eval_roots = (
        job_root / "eval_data",
        job_root / "edge_ssh_staging" / "eval_data",
    )
    eval_root = next(
        (
            root
            for root in eval_roots
            if (root / "images" / "test").is_dir()
            and (root / "labelsxml" / "test").is_dir()
        ),
        None,
    )
    if eval_root is None:
        return {
            "status": "fail",
            "reason": "manual_edge_validation_pack_missing_images_or_labels",
        }
    images_dir = eval_root / "images" / "test"
    xml_dir = eval_root / "labelsxml" / "test"

    evidence = _manual_eval_dataset_evidence(
        job_id=job_id, export_id=export_id, manifest=manifest
    )
    run_config_path = job_root / "edge_ssh_staging" / "run_config.json"
    run_config = _decode_json(
        (
            run_config_path.read_text(encoding="utf-8")
            if run_config_path.is_file()
            else None
        ),
        {},
    )
    eval_params = {
        "imgsz": int(
            run_config.get("imgsz") or getattr(train_job, "imgsz", 640) or 640
        ),
        "conf_thres": float(run_config.get("conf_thres") or 0.35),
        "nms_thres": float(run_config.get("nms_thres") or 0.7),
        "match_iou_thres": float(run_config.get("match_iou_thres") or 0.5),
        "threshold_unit": str(run_config.get("threshold_unit") or "ratio_0_1"),
        "preprocessing_contract": run_config.get("preprocessing_contract")
        or {
            "resize": "letterbox",
            "pad_value": 114,
            "color_order": "RGB",
            "scale": 1.0 / 255.0,
        },
    }
    report_root = _manual_report_root(job_id, export_id)
    results_dir = report_root / "results_cloud"
    if results_dir.exists():
        shutil.rmtree(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    try:
        weights_path = _resolve_same_batch_pt_weights(train_job)
        from labelu.internal.application.service.yolo_sop_model_evaluation import (
            _load_eval_summary,
            _run_eval_script,
        )

        ok, log = _run_eval_script(
            "cloud_based_evaluation.py",
            env_extra={
                "EVAL_MODEL_PATH": str(weights_path),
                "EVAL_MODEL_TASK": "auto",
                "EVAL_DATA_PATH": str(images_dir.resolve()),
                "EVAL_XML_DIR": str(xml_dir.resolve()),
                "EVAL_SAVE_DIR": str(results_dir.resolve()),
                "EVAL_IMGSZ": str(eval_params["imgsz"]),
                "EVAL_CONF_THRES": str(eval_params["conf_thres"]),
                "EVAL_NMS_THRES": str(eval_params["nms_thres"]),
                "EVAL_MATCH_IOU_THRES": str(eval_params["match_iou_thres"]),
            },
            timeout_sec=int(settings.YOLO_SOP_SERVER_EVAL_TIMEOUT_SEC or 7200),
        )
        metrics = _load_eval_summary(results_dir)
        status_value = "ok" if ok and metrics.get("map50") is not None else "fail"
        result = {
            "status": status_value,
            "model": str(weights_path),
            "model_type": str(getattr(train_job, "job_type", None) or "yolo_detection"),
            "results_dir": str(results_dir),
            "report_md_path": str(results_dir / "evaluation_report.md"),
            "eval_summary_path": str(results_dir / "eval_summary.json"),
            "metrics": metrics,
            "eval_params": eval_params,
            "dataset_evidence": evidence,
            "log_tail": log[-2000:] if status_value != "ok" else None,
        }
    except Exception as exc:
        logger.exception(
            "same-batch PT evaluation failed job_id={} export_id={}",
            job_id,
            export_id,
        )
        result = {
            "status": "fail",
            "model_type": str(getattr(train_job, "job_type", None) or "yolo_detection"),
            "results_dir": str(results_dir),
            "metrics": {},
            "eval_params": eval_params,
            "dataset_evidence": evidence,
            "error": str(exc)[:2000],
        }

    (report_root / "same_batch_pt_eval.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def _run_plate_detection_same_batch_pt_eval(
    *,
    job_id: int,
    export_id: int,
    train_job,
    manifest: dict,
) -> dict:
    return _run_same_batch_pt_eval(
        job_id=job_id,
        export_id=export_id,
        train_job=train_job,
        manifest=manifest,
    )


def _build_same_batch_pt_bmodel_comparison(
    *, pt_eval: dict | None, ingest: dict | None
) -> dict:
    pt_eval = pt_eval if isinstance(pt_eval, dict) else {}
    ingest = ingest if isinstance(ingest, dict) else {}
    bmodel_sop = (
        ingest.get("bmodel_sop") if isinstance(ingest.get("bmodel_sop"), dict) else {}
    )
    models = (
        bmodel_sop.get("models") if isinstance(bmodel_sop.get("models"), dict) else {}
    )
    from labelu.internal.application.service.yolo_sop_model_evaluation import (
        _compare_pt_bmodel,
    )

    pt_metrics = (
        pt_eval.get("metrics") if isinstance(pt_eval.get("metrics"), dict) else {}
    )
    dataset_evidence = pt_eval.get("dataset_evidence") or {}
    eval_params = pt_eval.get("eval_params") or {}

    def compare_one(tag: str, target_metrics: dict) -> dict:
        item = _compare_pt_bmodel(pt_metrics, target_metrics, precision=tag or None)
        item["same_batch"] = False
        item["same_thresholds"] = bool(eval_params)
        item["same_preprocessing"] = bool(
            isinstance(eval_params.get("preprocessing_contract"), dict)
        )
        item["dataset_evidence"] = dataset_evidence
        item["eval_params"] = eval_params
        pt_images = _to_int_or_none(pt_metrics.get("images"))
        bmodel_images = _to_int_or_none(target_metrics.get("images"))
        if (
            pt_images is not None
            and bmodel_images is not None
            and pt_images != bmodel_images
        ):
            item.update(
                {
                    "status": "skip",
                    "verdict": "not_evaluable",
                    "reason_code": "evaluation_image_count_mismatch",
                    "detail": (
                        f"PT 与 {str(tag or 'BModel').upper()} 的评估图片数不一致"
                        f"（{pt_images} vs {bmodel_images}），不能判定部署转换精度损失"
                    ),
                }
            )
        elif item.get("status") in {"ok", "warn", "fail"}:
            item["same_batch"] = True
        return item

    by_precision: dict[str, dict] = {}
    model_metrics: dict[str, dict] = {}
    for tag in ("fp32", "fp16", "int8"):
        model = models.get(tag)
        if not isinstance(model, dict):
            continue
        metrics = (
            model.get("metrics") if isinstance(model.get("metrics"), dict) else model
        )
        if metrics.get("map50") is None:
            continue
        model_metrics[tag] = metrics
        by_precision[tag] = compare_one(tag, metrics)

    if not by_precision:
        fallback_metrics = (
            bmodel_sop.get("metrics")
            if isinstance(bmodel_sop.get("metrics"), dict)
            else {}
        )
        fallback_tag = str(bmodel_sop.get("model_tag") or "").strip().lower()
        if fallback_metrics:
            by_precision[fallback_tag or "bmodel"] = compare_one(
                fallback_tag or "bmodel", fallback_metrics
            )

    primary_tag = next(
        (tag for tag in ("int8", "fp16", "fp32", "bmodel") if tag in by_precision),
        next(iter(by_precision), "bmodel"),
    )
    comparison = dict(by_precision.get(primary_tag) or compare_one(primary_tag, {}))
    comparison["primary_precision"] = primary_tag
    comparison["by_precision"] = by_precision

    fp32_metrics = model_metrics.get("fp32") or {}
    int8_metrics = model_metrics.get("int8") or {}
    if fp32_metrics.get("map50") is not None and int8_metrics.get("map50") is not None:
        quantization = _compare_pt_bmodel(
            fp32_metrics,
            int8_metrics,
            precision="int8",
        )
        fp32_images = _to_int_or_none(fp32_metrics.get("images"))
        int8_images = _to_int_or_none(int8_metrics.get("images"))
        quantization.update(
            {
                "baseline": "fp32",
                "candidate": "int8",
                "fp32_map50": quantization.pop("pt_map50", None),
                "int8_map50": quantization.pop("bmodel_map50", None),
                "same_batch": (
                    fp32_images is not None
                    and int8_images is not None
                    and fp32_images == int8_images
                ),
                "detail": str(quantization.get("detail") or "").replace(
                    "相对 PT", "相对 FP32"
                ),
            }
        )
        comparison["int8_vs_fp32"] = quantization
    return comparison


def _manual_plate_prediction_summary(bmodel_sop: dict) -> dict:
    raw_models = bmodel_sop.get("models") if isinstance(bmodel_sop, dict) else None
    if not isinstance(raw_models, dict):
        return {}
    for tag in ("fp16", "fp32", "int8"):
        item = raw_models.get(tag)
        if not isinstance(item, dict):
            continue
        metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
        if str(metrics.get("model_type") or "") not in (
            "plate_detection",
            "plate_recognition",
        ):
            continue
        image_count = _to_int_or_none(
            metrics.get("result_image_count", metrics.get("input_image_count"))
        )
        with_boxes = _to_int_or_none(metrics.get("images_with_detections"))
        total_boxes = _to_int_or_none(metrics.get("total_detections"))
        if image_count is None and with_boxes is None and total_boxes is None:
            continue
        return {
            "image_count": image_count or 0,
            "with_boxes": with_boxes or 0,
            "total_boxes": total_boxes or 0,
        }
    return {}


def _build_manual_result_summaries(
    *, summary: dict | None, ingest: dict | None
) -> dict:
    summary = summary if isinstance(summary, dict) else {}
    ingest = ingest if isinstance(ingest, dict) else {}

    board_report = (
        ingest.get("board_report")
        if isinstance(ingest.get("board_report"), dict)
        else {}
    )
    bmcv_infer = (
        ingest.get("bmcv_infer") if isinstance(ingest.get("bmcv_infer"), dict) else {}
    )
    bmodel_sop = (
        ingest.get("bmodel_sop") if isinstance(ingest.get("bmodel_sop"), dict) else {}
    )

    warnings = []
    raw_warnings = bmodel_sop.get("warnings")
    if isinstance(raw_warnings, list):
        warnings.extend(item for item in raw_warnings if isinstance(item, dict))
    warning = _plot_warning(bmodel_sop.get("stderr_tail"))
    if warning:
        warnings.append(warning)
    warning = _plot_warning(summary.get("error"))
    if warning:
        warnings.append(warning)
    warning_by_code = {
        str(item.get("code") or "WARNING"): {
            "code": str(item.get("code") or "WARNING"),
            "message": str(item.get("message") or "").strip(),
        }
        for item in warnings
        if str(item.get("message") or "").strip()
    }
    warnings = list(warning_by_code.values())
    quality_gate = bmodel_sop.get("usability_gate")
    quality_gate = quality_gate if isinstance(quality_gate, dict) else None

    execution_summary = {
        "status": str(summary.get("status") or "unknown"),
        "remote_exit_code": _to_int_or_none(summary.get("remote_exit_code")),
        "files_pulled": _to_int_or_none(summary.get("files_pulled")) or 0,
        "remote_host": str(summary.get("remote_host") or "").strip() or None,
        "execution_profile": str(summary.get("execution_profile") or "quick"),
        "model_cache_hit": bool(summary.get("model_cache_hit")),
        "output_transfer": summary.get("output_transfer"),
        "stage_durations_seconds": summary.get("stage_durations_seconds") or {},
        "has_board_report": bool(board_report),
        "has_bmcv_infer": bool(bmcv_infer),
        "has_bmodel_eval": bool(bmodel_sop),
    }
    if warnings:
        execution_summary["warnings"] = warnings

    prediction_summary = None
    detection_summary = _manual_plate_prediction_summary(bmodel_sop)
    if not detection_summary:
        detection_summary = (
            bmcv_infer.get("detection_summary")
            if isinstance(bmcv_infer.get("detection_summary"), dict)
            else {}
        )
    if detection_summary:
        image_count = _to_int_or_none(detection_summary.get("image_count")) or 0
        with_boxes = _to_int_or_none(detection_summary.get("with_boxes")) or 0
        total_boxes = _to_int_or_none(detection_summary.get("total_boxes")) or 0
        prediction_summary = {
            "image_count": image_count,
            "with_boxes": with_boxes,
            "total_boxes": total_boxes,
            "avg_boxes_per_image": (
                round(float(total_boxes) / float(image_count), 4)
                if image_count > 0
                else 0.0
            ),
        }

    eval_summary = None
    metrics = (
        bmodel_sop.get("metrics") if isinstance(bmodel_sop.get("metrics"), dict) else {}
    )
    if bmodel_sop or metrics:
        eval_summary = {
            "status": str(bmodel_sop.get("status") or "").strip() or None,
            "map50": _to_float_or_none(metrics.get("map50")),
            "map50_95": _to_float_or_none(metrics.get("map50_95", metrics.get("map"))),
            "map50_95_status": metrics.get("map50_95_status"),
            "map50_95_approximation": _to_float_or_none(
                metrics.get("map50_95_approximation")
            ),
            "map50_95_approximation_basis": metrics.get("map50_95_approximation_basis"),
            "precision": _to_float_or_none(metrics.get("precision")),
            "recall": _to_float_or_none(metrics.get("recall")),
            "images": _to_int_or_none(metrics.get("images")),
            "performance_probe_status": metrics.get("performance_probe_status"),
            "performance_probe_error": metrics.get("performance_probe_error"),
            "runtime_metrics": metrics.get("runtime_metrics"),
            "resource_usage": metrics.get("resource_usage"),
            "environment": metrics.get("environment"),
            "stability": metrics.get("stability"),
            "performance_artifacts": metrics.get("performance_artifacts"),
        }
        if quality_gate is not None:
            eval_summary["quality_gate"] = quality_gate
        if warnings:
            eval_summary["warnings"] = warnings

    eval_models = _manual_bmodel_eval_models(bmodel_sop)
    if eval_summary is not None and eval_models:
        eval_summary["models"] = eval_models

    from labelu.internal.common.bm1688_quality_report import (
        normalize_plate_recognition_eval,
    )

    plate_recognition_eval = normalize_plate_recognition_eval(bmodel_sop)

    return {
        "execution_summary": execution_summary,
        "prediction_summary": prediction_summary,
        "eval_summary": eval_summary,
        "eval_models": eval_models,
        "plate_recognition_eval": plate_recognition_eval,
        "quality_gate": quality_gate,
        "warnings": warnings,
    }


def _build_manual_validation_dimensions(
    *,
    mode: str,
    manifest: dict | None,
    summary: dict | None,
    ingest: dict | None,
    result_summaries: dict | None = None,
) -> dict:
    manifest = manifest if isinstance(manifest, dict) else {}
    summary = summary if isinstance(summary, dict) else {}
    ingest = ingest if isinstance(ingest, dict) else {}
    result_summaries = result_summaries if isinstance(result_summaries, dict) else {}
    environment = (
        summary.get("environment")
        if isinstance(summary.get("environment"), dict)
        else {}
    )
    environment_after = (
        environment.get("after")
        if isinstance(environment.get("after"), dict)
        else environment
    )
    environment_ready = environment_after.get("environment_ready") is True

    bmcv_infer = (
        ingest.get("bmcv_infer") if isinstance(ingest.get("bmcv_infer"), dict) else {}
    )
    board_report = (
        ingest.get("board_report")
        if isinstance(ingest.get("board_report"), dict)
        else {}
    )
    prediction = (
        result_summaries.get("prediction_summary")
        if isinstance(result_summaries.get("prediction_summary"), dict)
        else {}
    )
    total_boxes = _to_int_or_none(prediction.get("total_boxes"))
    with_boxes = _to_int_or_none(prediction.get("with_boxes"))

    eval_models = (
        result_summaries.get("eval_models")
        if isinstance(result_summaries.get("eval_models"), dict)
        else {}
    )
    # A certification runner returns exit code 1 when the quality gate blocks
    # release.  That exit code must not turn a successful BModel execution
    # into an "inference failed" result.  Prefer the per-precision runtime
    # evidence when it is available; the process exit code is only a fallback
    # for runs that produced no model evidence at all.
    required_model_tags = ("fp32", "fp16", "int8")
    completed_model_tags = [
        tag
        for tag in required_model_tags
        if isinstance(eval_models.get(tag), dict)
        and str(eval_models[tag].get("status") or "").lower() == "ok"
        and _to_int_or_none(eval_models[tag].get("images")) not in (None, 0)
    ]
    bmodel_inference_observed = bool(eval_models)
    bmodel_inference_ok = len(completed_model_tags) == len(required_model_tags)
    inference_observed = (
        bool(bmcv_infer)
        or bool(board_report.get("bmcv_infer"))
        or bmodel_inference_observed
    )
    inference_ok = bmodel_inference_ok or (
        _to_int_or_none(summary.get("remote_exit_code")) == 0
        and inference_observed
        and bmcv_infer.get("ok") is not False
        and board_report.get("skipped") is not True
    )
    precision_model = next(
        (
            eval_models[tag]
            for tag in ("int8", "fp16", "fp32")
            if isinstance(eval_models.get(tag), dict)
        ),
        {},
    )
    precision_metrics = (
        precision_model.get("metrics")
        if isinstance(precision_model.get("metrics"), dict)
        else precision_model
    )
    precision_computed = precision_metrics.get("map50") is not None and _to_int_or_none(
        precision_metrics.get("images")
    ) not in (None, 0)
    labeled_count = int(manifest.get("labeled_count") or 0)
    normalized_mode = str(mode or "run_and_eval").strip().lower()
    if normalized_mode == "run_only":
        precision_status = "not_requested"
        precision_reason = "run_only"
    elif labeled_count <= 0:
        precision_status = "not_evaluable"
        precision_reason = "no_ground_truth"
    elif precision_computed:
        precision_status = "computed"
        precision_reason = None
    else:
        precision_status = "missing"
        precision_reason = "evaluation_report_missing"

    coverage = (
        manifest.get("class_coverage")
        if isinstance(manifest.get("class_coverage"), dict)
        else {}
    )
    expected_classes = list(
        coverage.get("expected_classes") or manifest.get("class_names") or []
    )
    selected_distribution = (
        coverage.get("selected_distribution")
        if isinstance(coverage.get("selected_distribution"), dict)
        else {}
    )
    covered_classes = list(coverage.get("covered_classes") or [])
    if not covered_classes and selected_distribution:
        covered_classes = [
            name
            for name in expected_classes
            if int(selected_distribution.get(name) or 0) > 0
        ]
    class_metric_rows = precision_metrics.get("class_metrics")
    if not isinstance(class_metric_rows, list):
        class_metric_rows = precision_metrics.get("per_class_rows")
    if not covered_classes and isinstance(class_metric_rows, list):
        covered_classes = [
            str(item.get("name") or "").strip()
            for item in class_metric_rows
            if isinstance(item, dict)
            and str(item.get("name") or "").strip()
            and int(item.get("labels") or 0) > 0
        ]
    missing_classes = list(coverage.get("missing_classes") or [])
    if expected_classes and not coverage.get("missing_classes"):
        missing_classes = [
            name for name in expected_classes if name not in covered_classes
        ]

    return {
        "environment": {
            "status": (
                "pass" if environment_ready else ("fail" if environment else "unknown")
            ),
            "ready": environment_ready,
        },
        "inference": {
            "status": (
                "pass"
                if inference_ok
                else ("fail" if inference_observed else "unknown")
            ),
            "remote_exit_code": _to_int_or_none(summary.get("remote_exit_code")),
            "observed": inference_observed,
        },
        "detection_output": {
            "status": (
                "detected"
                if total_boxes is not None and total_boxes > 0
                else ("no_detection" if total_boxes == 0 else "unknown")
            ),
            "image_count": _to_int_or_none(prediction.get("image_count")),
            "with_boxes": with_boxes,
            "total_boxes": total_boxes,
        },
        "precision_evaluation": {
            "status": precision_status,
            "reason": precision_reason,
            "ground_truth_source": "existing_training_annotations",
            "labeled_image_count": labeled_count,
            "evaluated_image_count": _to_int_or_none(precision_metrics.get("images")),
            "metrics_model": str(precision_model.get("model_tag") or "").lower()
            or None,
        },
        "class_coverage": {
            "status": (
                "complete"
                if expected_classes and not missing_classes
                else ("partial" if covered_classes else "unknown")
            ),
            "strategy": coverage.get("strategy"),
            "covered_class_count": len(covered_classes),
            "total_class_count": len(expected_classes),
            "covered_classes": covered_classes,
            "missing_classes": missing_classes,
            "selected_distribution": selected_distribution,
        },
    }


def _build_manual_analysis(
    *,
    job_id: int,
    export_id: int,
    ingest: dict | None,
    pt_eval: dict | None = None,
    comparison_pt_bmodel: dict | None = None,
) -> dict | None:
    ingest = ingest if isinstance(ingest, dict) else {}
    bmodel_sop = (
        ingest.get("bmodel_sop") if isinstance(ingest.get("bmodel_sop"), dict) else {}
    )
    metrics = (
        bmodel_sop.get("metrics") if isinstance(bmodel_sop.get("metrics"), dict) else {}
    )
    eval_models = _manual_bmodel_eval_models(bmodel_sop)
    if not metrics and not eval_models:
        return None

    export_root = _export_dir_for_row(export_id)
    server_sop = edge_ssh_validation.load_server_sop_eval_summary(export_root) or {}
    pt_block = pt_eval if isinstance(pt_eval, dict) else {}
    if not pt_block:
        pt_block = (
            server_sop.get("pt") if isinstance(server_sop.get("pt"), dict) else {}
        )
    if not pt_block:
        return None

    manual_report_root = _manual_report_root(job_id, export_id)
    manual_bmodel_dir = manual_report_root / "results_bmodel"
    bmodel_block = {
        "status": str(bmodel_sop.get("status") or "ok"),
        "results_dir": str(manual_bmodel_dir),
        "metrics": metrics,
        "report_md_path": str(manual_bmodel_dir / "evaluation_report.md"),
        "report_md_exists": (manual_bmodel_dir / "evaluation_report.md").is_file(),
        "eval_summary_path": str(manual_bmodel_dir / "eval_summary.json"),
        "eval_summary_exists": (manual_bmodel_dir / "eval_summary.json").is_file(),
    }

    bmodel_blocks = {}
    for tag, item in eval_models.items():
        model_block = dict(item)
        model_dir = (
            manual_bmodel_dir if tag == "int8" else manual_bmodel_dir / "variants" / tag
        )
        model_block["results_dir"] = str(model_dir)
        model_block["report_md_path"] = str(model_dir / "evaluation_report.md")
        model_block["eval_summary_path"] = str(model_dir / "eval_summary.json")
        model_block["report_md_exists"] = (model_dir / "evaluation_report.md").is_file()
        model_block["eval_summary_exists"] = (model_dir / "eval_summary.json").is_file()
        bmodel_blocks[tag] = model_block

    cmp_bm = comparison_pt_bmodel
    if not isinstance(cmp_bm, dict):
        cmp_bm = server_sop.get("comparison_pt_bmodel")
    if not isinstance(cmp_bm, dict):
        cmp_bm = {}
    pt_metrics = (
        pt_block.get("metrics") if isinstance(pt_block.get("metrics"), dict) else {}
    )
    pt_map50 = _to_float_or_none(pt_metrics.get("map50"))
    bm_map50 = _to_float_or_none(metrics.get("map50"))
    if (
        cmp_bm.get("status") not in {"ok", "warn", "fail"}
        and pt_map50 is not None
        and bm_map50 is not None
    ):
        from labelu.internal.application.service.yolo_sop_model_evaluation import (
            _compare_pt_bmodel,
        )

        cmp_bm = _compare_pt_bmodel(
            pt_metrics,
            metrics,
            precision=str(cmp_bm.get("precision") or "").strip() or None,
        )

    sop_like = dict(server_sop)
    sop_like["pt"] = pt_block
    sop_like["bmodel"] = bmodel_block
    if bmodel_blocks:
        sop_like["bmodels"] = bmodel_blocks
    sop_like["comparison_pt_bmodel"] = cmp_bm
    return build_sop_eval_analysis(export_root, sop_like)


def _truncate_text_tail(value, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[-limit:]


def _build_manual_execution_payload(
    *,
    job_id: int,
    export_id: int,
    summary: dict | None,
) -> dict:
    summary = summary if isinstance(summary, dict) else {}
    payload: dict[str, object] = {}
    for key in (
        "status",
        "error",
        "export_id",
        "job_id",
        "remote_host",
        "remote_job_dir",
        "remote_exit_code",
        "files_pulled",
        "upload_bytes",
        "upload_retry_count",
        "sophon_demo_probe",
        "sophon_demo_root",
        "environment",
        "remote_cleanup",
        "remote_cleanup_message",
        "remote_retention_hours",
        "execution_profile",
        "model_cache_key",
        "model_cache_hit",
        "model_cache_probe_error",
        "output_transfer",
        "output_archive_bytes",
        "output_archive_error",
        "stage_durations_seconds",
    ):
        value = summary.get(key)
        if value is not None:
            payload[key] = value

    stdout_tail = _truncate_text_tail(summary.get("remote_stdout_tail"), limit=1200)
    stderr_tail = _truncate_text_tail(summary.get("remote_stderr_tail"), limit=800)
    if stdout_tail:
        payload["remote_stdout_tail"] = stdout_tail
    if stderr_tail:
        payload["remote_stderr_tail"] = stderr_tail

    warning = _plot_warning(summary.get("error"))
    if warning:
        payload["warnings"] = [warning]
        payload["error"] = None
    elif isinstance(summary.get("warnings"), list) and summary.get("warnings"):
        payload["warnings"] = [
            item for item in summary["warnings"] if isinstance(item, dict)
        ]

    warnings = summary.get("ingest_warnings")
    if isinstance(warnings, list) and warnings:
        payload["ingest_warnings"] = [str(item)[:500] for item in warnings[:10]]

    ingest = summary.get("ingest") if isinstance(summary.get("ingest"), dict) else {}
    runner_summary = ingest.get("runner_summary")
    if isinstance(runner_summary, dict):
        payload["runner_summary"] = runner_summary
    local_output = _manual_output_root(job_id, export_id)
    report_root = _manual_report_root(job_id, export_id)
    artifact_paths: dict[str, str] = {}
    if (
        isinstance(ingest.get("board_report"), dict)
        or (local_output / "board_report.json").is_file()
    ):
        artifact_paths["board_report"] = str(local_output / "board_report.json")
    if (
        isinstance(ingest.get("bmcv_infer"), dict)
        or (local_output / "bmcv_infer_summary.json").is_file()
    ):
        artifact_paths["bmcv_infer"] = str(local_output / "bmcv_infer_summary.json")
    if (
        isinstance(ingest.get("bmodel_sop"), dict)
        or (local_output / "bmodel_sop_eval.json").is_file()
    ):
        artifact_paths["bmodel_sop"] = str(local_output / "bmodel_sop_eval.json")
    if (report_root / "results_bmodel" / "evaluation_report.md").is_file():
        artifact_paths["bmodel_report"] = str(
            report_root / "results_bmodel" / "evaluation_report.md"
        )

    bmodel_sop = (
        ingest.get("bmodel_sop") if isinstance(ingest.get("bmodel_sop"), dict) else {}
    )
    models = (
        bmodel_sop.get("models") if isinstance(bmodel_sop.get("models"), dict) else {}
    )
    int8_item = (
        models.get("int8") if isinstance(models.get("int8"), dict) else bmodel_sop
    )
    int8_metrics = (
        int8_item.get("metrics")
        if isinstance(int8_item.get("metrics"), dict)
        else int8_item
    )
    if isinstance(int8_metrics, dict):
        for source_key, target_key in (
            ("runtime_metrics", "runtime_metrics"),
            ("resource_usage", "resource_usage"),
            ("environment", "performance_environment"),
            ("stability", "stability"),
            ("performance_artifacts", "performance_artifacts"),
        ):
            value = int8_metrics.get(source_key)
            if value is not None:
                payload[target_key] = value
        performance_artifacts = int8_metrics.get("performance_artifacts")
        if isinstance(performance_artifacts, dict):
            performance_root = (report_root / "results_bmodel").resolve()
            for name, relative in performance_artifacts.items():
                candidate = (performance_root / str(relative)).resolve()
                try:
                    candidate.relative_to(performance_root)
                except ValueError:
                    continue
                if candidate.is_file():
                    artifact_paths["performance_{}".format(name)] = str(candidate)
    if artifact_paths:
        payload["artifacts"] = artifact_paths
    return payload


_CLASS_DROP_STORAGE_FIELDS = (
    "name",
    "labels",
    "pt_ap50",
    "bmodel_ap50",
    "ap50_delta",
    "pt_recall",
    "bmodel_recall",
    "recall_delta",
    "pt_fn",
    "bmodel_fn",
    "fn_delta",
    "severity",
)


def _compact_class_drops_for_storage(analysis: dict) -> None:
    drops = analysis.get("class_drops")
    if not isinstance(drops, list):
        return

    total = len(drops)
    compact_drops = []
    for item in drops[:25]:
        if not isinstance(item, dict):
            continue
        compact_drops.append(
            {key: item.get(key) for key in _CLASS_DROP_STORAGE_FIELDS if key in item}
        )
    analysis["class_drops"] = compact_drops
    analysis["class_drop_total"] = total
    if total > len(compact_drops):
        analysis["class_drop_truncated"] = True
    else:
        analysis.pop("class_drop_truncated", None)


def _serialize_job_result_json(result_json: dict) -> str:
    payload = dict(result_json or {})
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized.encode("utf-8")) <= 60000:
        return serialized

    execution = payload.get("execution")
    if isinstance(execution, dict):
        execution.pop("remote_stdout_tail", None)
        execution.pop("remote_stderr_tail", None)
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized.encode("utf-8")) <= 60000:
        return serialized

    analysis = payload.get("analysis")
    if isinstance(analysis, dict):
        _compact_class_drops_for_storage(analysis)
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized.encode("utf-8")) <= 60000:
        return serialized

    if isinstance(execution, dict):
        execution.pop("artifacts", None)
    return json.dumps(payload, ensure_ascii=False)


def _attachment_snapshot(attachment) -> dict:
    try:
        response = build_attachment_response(attachment)
        data = (
            response.model_dump()
            if hasattr(response, "model_dump")
            else response.dict()
        )
    except Exception:
        data = {
            "id": getattr(attachment, "id", None),
            "filename": getattr(attachment, "filename", None),
            "url": getattr(attachment, "url", None),
            "thumbnail_url": None,
            "stream_url": None,
            "annotation_display_url": None,
            "storage_backend": None,
        }
    created_at = getattr(attachment, "created_at", None)
    updated_at = getattr(attachment, "updated_at", None)
    data["created_at"] = (
        created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    )
    data["updated_at"] = (
        updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at
    )
    return data


def _append_external_attachment_pack_paths(
    snapshots: list[dict],
    *,
    pack_result: dict | None,
    attachments,
) -> list[dict]:
    if not isinstance(snapshots, list) or not snapshots:
        return snapshots
    file_mappings = (pack_result or {}).get("file_mappings") or []
    attachment_map = {
        int(getattr(row, "id", 0) or 0): row for row in (attachments or [])
    }
    mapping_by_attachment: dict[int, dict] = {}
    for item in file_mappings:
        if not isinstance(item, dict):
            continue
        attachment_id = int(item.get("attachment_id") or 0)
        if attachment_id > 0 and attachment_id not in mapping_by_attachment:
            mapping_by_attachment[attachment_id] = item

    enriched: list[dict] = []
    for snapshot in snapshots:
        item = dict(snapshot or {})
        attachment_id = int(item.get("id") or 0)
        mapping = mapping_by_attachment.get(attachment_id) or {}
        packed_name = str(mapping.get("packed_name") or "").strip()
        source_path = str(mapping.get("source_path") or "").strip()
        if source_path:
            item.setdefault("source_path", source_path)
        if packed_name:
            row = attachment_map.get(attachment_id)
            export_id = int((pack_result or {}).get("export_id") or 0)
            job_id = int((pack_result or {}).get("job_id") or 0)
            if export_id > 0 and job_id > 0:
                pack_copy = (
                    Path(
                        str(
                            (pack_result or {}).get("job_root")
                            or edge_validation_job_root(
                                export_id=export_id, job_id=job_id
                            )
                        )
                    )
                    / "eval_data"
                    / "images"
                    / "test"
                    / packed_name
                )
                item["pack_image_path"] = str(pack_copy)
            item["packed_name"] = packed_name
            if row is not None:
                item.setdefault(
                    "restored_from_attachment_id", int(getattr(row, "id", 0) or 0)
                )
        enriched.append(item)
    return enriched


def _restore_external_attachment_from_snapshot(
    db: Session,
    *,
    task_id: int,
    snapshot: dict,
    current_user: User,
):
    if not isinstance(snapshot, dict):
        return None
    filename = str(snapshot.get("filename") or "").strip()
    if not filename:
        filename = f"restored-{uuid.uuid4().hex[:8]}.png"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
        return None

    candidates: list[Path] = []
    for key in ("path",):
        rel = str(snapshot.get(key) or "").strip().lstrip("/\\")
        if rel:
            candidates.append((Path(settings.MEDIA_ROOT) / rel).resolve())
    media_root = Path(settings.MEDIA_ROOT).resolve()
    for key in ("pack_image_path", "source_path"):
        raw = str(snapshot.get(key) or "").strip()
        if raw:
            raw_path = Path(raw).expanduser()
            if raw_path.is_absolute():
                candidate = raw_path.resolve()
            else:
                # User-facing snapshots may contain only a media-root-relative
                # pack reference. Keep resolution confined to MEDIA_ROOT.
                try:
                    candidate = (media_root / raw_path).resolve()
                    candidate.relative_to(media_root)
                except (OSError, ValueError):
                    continue
            candidates.append(candidate)
    for key in ("url", "stream_url", "annotation_display_url", "thumbnail_url"):
        raw = str(snapshot.get(key) or "").strip()
        marker = "/tasks/attachment/"
        if marker in raw:
            rel = raw.split(marker, 1)[1].split("?", 1)[0].lstrip("/\\")
            if rel:
                candidates.append((Path(settings.MEDIA_ROOT) / rel).resolve())

    source_path = next((path for path in candidates if path.is_file()), None)
    if source_path is None:
        return None

    rel_dir = Path(settings.UPLOAD_DIR).joinpath(
        str(task_id), "edge_validation_external"
    )
    restored_name = f"restored-{uuid.uuid4().hex[:8]}-{Path(filename).name}"
    attachment_rel_path = str(rel_dir.joinpath(restored_name)).replace("\\", "/")
    dest_path = Path(settings.MEDIA_ROOT) / attachment_rel_path
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, dest_path)

    with begin_transaction(db):
        row = crud_attachment.create(
            db=db,
            attachment=TaskAttachment(
                path=attachment_rel_path,
                url=build_attachment_api_path(attachment_rel_path),
                filename=restored_name,
                created_by=current_user.id,
                updated_by=current_user.id,
                task_id=task_id,
            ),
        )
    return row


def _resolve_external_attachments(
    db: Session,
    *,
    task_id: int,
    attachment_ids: list[int],
    external_snapshots: list[dict] | None,
    current_user: User,
):
    attachments = (
        crud_attachment.get_by_ids(db, attachment_ids=attachment_ids)
        if attachment_ids
        else []
    )
    attachment_map = {
        int(row.id): row
        for row in attachments
        if int(getattr(row, "task_id", 0) or 0) == int(task_id)
    }
    snapshot_map = {}
    for snap in external_snapshots or []:
        try:
            snap_id = int(snap.get("id") or 0)
        except Exception:
            snap_id = 0
        if snap_id > 0 and snap_id not in snapshot_map:
            snapshot_map[snap_id] = snap

    resolved = []
    for attachment_id in attachment_ids:
        aid = int(attachment_id)
        row = attachment_map.get(aid)
        if row is None:
            row = _restore_external_attachment_from_snapshot(
                db,
                task_id=task_id,
                snapshot=snapshot_map.get(aid) or {},
                current_user=current_user,
            )
        if row is None or int(getattr(row, "task_id", 0) or 0) != int(task_id):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="external attachments invalid",
            )
        resolved.append(row)
    return resolved


def _safe_eval_filename(index: int, src_name: str) -> str:
    name = os.path.basename(src_name or "")
    path = Path(name)
    suffix = path.suffix.lower() or ".jpg"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem or "")
    stem = stem.strip("._-") or "image"
    return f"{index:04d}_{stem}{suffix}"


def _uniformly_sample_eval_files(files: list[Path], limit: int) -> list[Path]:
    if len(files) <= limit:
        return files
    if limit <= 1:
        return files[:1]
    last = len(files) - 1
    return [files[round(index * last / (limit - 1))] for index in range(limit)]


def _eval_label_class_counts(
    label_path: Path,
    *,
    label_suffix: str,
    class_names: list[str],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    if not label_path.is_file():
        return counts
    if label_suffix == ".xml":
        try:
            root = ET.parse(label_path).getroot()
        except (OSError, ET.ParseError):
            return counts
        expected = set(class_names)
        for obj in root.iter("object"):
            name = str(obj.findtext("name") or "").strip()
            if name and (not expected or name in expected):
                counts[name] += 1
        return counts

    try:
        lines = label_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return counts
    for line in lines:
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        try:
            class_id = int(float(parts[0]))
        except (TypeError, ValueError):
            continue
        name = (
            class_names[class_id]
            if 0 <= class_id < len(class_names)
            else f"class_{class_id}"
        )
        counts[name] += 1
    return counts


def _sample_eval_files_with_class_coverage(
    files: list[Path],
    *,
    limit: int,
    label_dir: Path,
    label_suffix: str,
    class_names: list[str],
) -> tuple[list[Path], dict]:
    limit = max(1, int(limit))
    profiles = {
        fp: _eval_label_class_counts(
            label_dir / f"{fp.stem}{label_suffix}",
            label_suffix=label_suffix,
            class_names=class_names,
        )
        for fp in files
    }
    source_distribution: Counter[str] = Counter()
    for counts in profiles.values():
        source_distribution.update(counts)

    expected_classes = list(class_names) or sorted(source_distribution)
    expected_set = set(expected_classes)
    available_classes = {
        name for name, count in source_distribution.items() if count > 0
    }
    uncovered = set(expected_set & available_classes)
    selected: list[Path] = []
    selected_set: set[Path] = set()
    file_order = {fp: index for index, fp in enumerate(files)}

    # Greedy set cover starts with rare classes so a scarce class cannot be
    # displaced by common-class images when the edge batch is capped.
    while uncovered and len(selected) < min(limit, len(files)):
        rarest = min(
            uncovered,
            key=lambda name: (
                sum(1 for counts in profiles.values() if counts.get(name, 0) > 0),
                (
                    expected_classes.index(name)
                    if name in expected_classes
                    else len(expected_classes)
                ),
                name,
            ),
        )
        candidates = [
            fp
            for fp in files
            if fp not in selected_set and profiles[fp].get(rarest, 0) > 0
        ]
        if not candidates:
            uncovered.discard(rarest)
            continue
        chosen = min(
            candidates,
            key=lambda fp: (
                -len(set(profiles[fp]) & uncovered),
                sum(
                    source_distribution[name] for name in set(profiles[fp]) & uncovered
                ),
                file_order[fp],
            ),
        )
        selected.append(chosen)
        selected_set.add(chosen)
        uncovered.difference_update(profiles[chosen])

    remaining_slots = min(limit, len(files)) - len(selected)
    if remaining_slots > 0:
        remaining = [fp for fp in files if fp not in selected_set]
        selected.extend(_uniformly_sample_eval_files(remaining, remaining_slots))

    selected = sorted(set(selected), key=file_order.get)
    selected_distribution: Counter[str] = Counter()
    for fp in selected:
        selected_distribution.update(profiles[fp])
    covered_classes = [
        name for name in expected_classes if selected_distribution.get(name, 0) > 0
    ]
    missing_classes = [
        name for name in expected_classes if selected_distribution.get(name, 0) <= 0
    ]
    return selected, {
        "strategy": "class_coverage_then_uniform",
        "expected_classes": expected_classes,
        "source_distribution": dict(source_distribution),
        "selected_distribution": dict(selected_distribution),
        "covered_classes": covered_classes,
        "missing_classes": missing_classes,
        "covered_class_count": len(covered_classes),
        "total_class_count": len(expected_classes),
        "coverage_complete": bool(expected_classes) and not missing_classes,
    }


def _copy_legacy_eval_dataset_into_job(
    *,
    export_id: int,
    job_id: int,
    yolo_train_job_id: int,
    db: Session | None = None,
    root_override: Path | None = None,
) -> dict:
    job_root = (
        Path(root_override)
        if root_override is not None
        else edge_validation_job_root(export_id=export_id, job_id=job_id)
    )
    if root_override is None:
        clear_job_pack(export_id=export_id, job_id=job_id)
    elif job_root.exists():
        shutil.rmtree(job_root, ignore_errors=True)
    eval_root = job_root / "eval_data"
    image_targets = [eval_root / "images" / "test", eval_root / "labelsxml" / "test"]
    for target in image_targets:
        target.mkdir(parents=True, exist_ok=True)

    train_job_root = edge_ssh_validation._job_work_root(int(yolo_train_job_id))
    dataset_candidates = (
        (train_job_root / "dataset", "yolo_dataset", "labelsxml", ".xml"),
        (
            train_job_root / "plate_dataset" / "pose",
            "plate_pose_dataset",
            "labels",
            ".txt",
        ),
    )
    copied_count = 0
    labeled_count = 0
    unlabeled_count = 0
    annotation_file_count = 0
    positive_image_count = 0
    negative_image_count = 0
    skipped: list[dict[str, str | int]] = []
    file_mappings: list[dict[str, str | int | bool | None]] = []
    image_name_map: dict[str, str] = {}
    chosen_split = None
    chosen_dataset_root = None
    chosen_dataset_kind = None
    chosen_label_dir = None
    chosen_label_suffix = None
    source_image_count = 0
    class_contract = (
        class_contract_for_export(db, export_id)
        if db is not None
        else {
            "class_names": edge_ssh_validation._read_class_names(
                int(yolo_train_job_id)
            ),
            "label_set_signature": None,
            "class_order_signature": None,
            "data_yaml_sha256": None,
            "dataset_content_signature": None,
        }
    )
    class_names = list(class_contract.get("class_names") or [])
    sampling_evidence: dict = {
        "strategy": "class_coverage_then_uniform",
        "expected_classes": class_names,
        "source_distribution": {},
        "selected_distribution": {},
        "covered_classes": [],
        "missing_classes": class_names,
        "covered_class_count": 0,
        "total_class_count": len(class_names),
        "coverage_complete": False,
    }

    for dataset_root, dataset_kind, label_dir, label_suffix in dataset_candidates:
        for split in ("test", "val"):
            img_src = dataset_root / "images" / split
            if not img_src.is_dir():
                continue
            source_files = [fp for fp in sorted(img_src.iterdir()) if fp.is_file()]
            if not source_files:
                continue
            source_image_count = len(source_files)
            max_images = max(1, int(settings.YOLO_EDGE_VALIDATION_MAX_IMAGES or 50))
            label_src = dataset_root / label_dir / split
            if not label_src.is_dir():
                label_src = dataset_root / label_dir / "test"
            files, sampling_evidence = _sample_eval_files_with_class_coverage(
                source_files,
                limit=max_images,
                label_dir=label_src,
                label_suffix=label_suffix,
                class_names=class_names,
            )
            chosen_split = split
            chosen_dataset_root = dataset_root
            chosen_dataset_kind = dataset_kind
            chosen_label_dir = label_dir
            chosen_label_suffix = label_suffix
            for fp in files:
                packed_name = _safe_eval_filename(copied_count, fp.name)
                shutil.copy2(fp, eval_root / "images" / "test" / packed_name)
                image_name_map[fp.stem] = Path(packed_name).stem
                file_mappings.append(
                    {
                        "sample_id": None,
                        "attachment_id": None,
                        "source_kind": f"{dataset_kind}_{split}",
                        "packed_name": packed_name,
                        "original_name": fp.name,
                        "source_path": str(fp),
                        "has_annotation": False,
                        "has_annotation_file": False,
                        "has_targets": False,
                        "target_count": 0,
                    }
                )
                copied_count += 1
            break
        if chosen_split is not None:
            break

    if chosen_split is None or copied_count <= 0:
        if root_override is None:
            clear_job_pack(export_id=export_id, job_id=job_id)
        else:
            shutil.rmtree(job_root, ignore_errors=True)
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="训练产物中未找到可用的 test/val 评估图片，无法发起边端验证",
        )

    if not class_names and sampling_evidence.get("expected_classes"):
        class_names = list(sampling_evidence["expected_classes"])
        class_contract["class_names"] = class_names

    label_src = chosen_dataset_root / chosen_label_dir / chosen_split
    if not label_src.is_dir():
        label_src = chosen_dataset_root / chosen_label_dir / "test"
    if label_src.is_dir():
        label_target = eval_root / chosen_label_dir / "test"
        label_target.mkdir(parents=True, exist_ok=True)
        for fp in sorted(label_src.iterdir()):
            if not fp.is_file():
                continue
            dst_stem = image_name_map.get(fp.stem)
            if not dst_stem:
                skipped.append({"label_name": fp.name, "reason": "image_not_copied"})
                continue
            shutil.copy2(fp, label_target / f"{dst_stem}{chosen_label_suffix}")
            labeled_count += 1
            annotation_file_count += 1
            packed_name = f"{dst_stem}{chosen_label_suffix}"
            packed_label_path = label_target / packed_name
            target_count = 0
            if chosen_label_suffix == ".xml":
                try:
                    target_count = sum(
                        1 for _ in ET.parse(packed_label_path).getroot().iter("object")
                    )
                except (OSError, ET.ParseError):
                    target_count = 0
            else:
                try:
                    target_count = sum(
                        1
                        for line in packed_label_path.read_text(
                            encoding="utf-8", errors="ignore"
                        ).splitlines()
                        if len(line.strip().split()) >= 5
                    )
                except OSError:
                    target_count = 0
            if target_count > 0:
                positive_image_count += 1
            else:
                negative_image_count += 1
            for mapping in file_mappings:
                if (
                    Path(str(mapping.get("packed_name") or "")).stem
                    == Path(packed_name).stem
                ):
                    mapping["has_annotation"] = True
                    mapping["has_annotation_file"] = True
                    mapping["has_targets"] = target_count > 0
                    mapping["target_count"] = target_count
                    break
        unlabeled_count = max(copied_count - labeled_count, 0)
    else:
        shutil.rmtree((eval_root / "labelsxml"), ignore_errors=True)
        unlabeled_count = copied_count

    manifest = {
        "export_id": int(export_id),
        "job_id": int(job_id),
        "task_id": None,
        "mode": "run_and_eval",
        "sample_count": 0,
        "attachment_count": 0,
        "copied_count": copied_count,
        "source_image_count": source_image_count,
        "selected_image_count": copied_count,
        "truncated_image_count": max(source_image_count - copied_count, 0),
        "labeled_count": labeled_count,
        "unlabeled_count": unlabeled_count,
        "annotation_file_count": annotation_file_count,
        "positive_image_count": positive_image_count,
        "negative_image_count": negative_image_count,
        "unlabeled_image_count": unlabeled_count,
        "class_names": class_names,
        "class_contract": class_contract,
        "sampling": sampling_evidence,
        "class_coverage": sampling_evidence,
        "file_mappings": file_mappings,
        "skipped": skipped,
        # Legacy evaluation intentionally truncates a larger test/val split;
        # skipped label files outside the selected image subset are therefore
        # informational rather than missing selected images.
        "blocking_skipped_count": 0,
        "legacy_dataset_split": chosen_split,
        "legacy_dataset_kind": chosen_dataset_kind,
        "legacy_dataset_source": True,
    }
    manifest["manifest_sha256"] = _manifest_sha256(manifest)
    (job_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "job_root": str(job_root),
        "eval_data_root": str(eval_root),
        "manifest_path": str(job_root / "manifest.json"),
        "copied_count": copied_count,
        "source_image_count": source_image_count,
        "selected_image_count": copied_count,
        "truncated_image_count": max(source_image_count - copied_count, 0),
        "labeled_count": labeled_count,
        "unlabeled_count": unlabeled_count,
        "annotation_file_count": annotation_file_count,
        "positive_image_count": positive_image_count,
        "negative_image_count": negative_image_count,
        "unlabeled_image_count": unlabeled_count,
        "sampling": sampling_evidence,
        "class_coverage": sampling_evidence,
        "skipped_count": len(skipped),
        "blocking_skipped_count": 0,
        "legacy_dataset_split": chosen_split,
        "legacy_dataset_kind": chosen_dataset_kind,
    }


def _load_job_with_access(db: Session, *, job_id: int, current_user: User):
    row = crud_yolo_edge_validation_job.get(db, job_id)
    if not row:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
            message="未找到该边端验证任务",
        )
    task = crud_task.get(db=db, task_id=row.task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
            message="未找到该边端验证任务",
        )
    export_row = get_export_for_download(
        db, export_id=row.export_id, current_user=current_user
    )
    if int(getattr(export_row, "task_id", 0) or 0) != int(task.id):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="边端验证任务与导出任务的父任务不一致",
        )
    try:
        assert_project_chain(
            [task_project_id(db, task.id), task_project_id(db, export_row.task_id)],
            context="edge validation export",
        )
    except ProjectScopeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="边端验证任务与导出任务项目归属不一致",
        ) from exc
    return row, task


def create_job(
    db: Session,
    *,
    cmd: CreateYoloEdgeValidationJobCommand,
    current_user: User,
) -> YoloEdgeValidationJobDetailResponse:
    if not settings.YOLO_TRAIN_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_57000_YOLO_TRAIN_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    export_row = get_export_for_download(
        db, export_id=cmd.export_id, current_user=current_user
    )
    task = crud_task.get(db=db, task_id=cmd.task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    # 先验证导出任务和当前任务的父任务关系，再读取训练作业和编译平台。
    # 这样跨任务请求稳定返回冲突，不会因为测试替身或历史导出缺少训练字段
    # 而提前暴露内部结构错误。
    if int(getattr(export_row, "task_id", 0) or 0) != int(task.id):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="边端验证任务与导出任务的父任务不一致",
        )
    train_job = crud_yolo_train_job.get(db, int(export_row.yolo_train_job_id))
    if train_job is None:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            message="未找到边端验证关联的训练作业",
        )
    ensure_phase_one_training_job_type(getattr(train_job, "job_type", None))
    ensure_phase_one_bm1688_platforms(_compile_platforms())
    try:
        assert_project_chain(
            [task_project_id(db, task.id), task_project_id(db, export_row.task_id)],
            context="edge validation export",
        )
    except ProjectScopeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="边端验证任务与导出任务项目归属不一致",
        ) from exc

    sample_ids = [int(sid) for sid in (cmd.sample_ids or []) if int(sid) > 0]
    attachment_ids = [int(aid) for aid in (cmd.attachment_ids or []) if int(aid) > 0]
    external_snapshots = (
        cmd.external_attachments
        if isinstance(getattr(cmd, "external_attachments", None), list)
        else None
    )
    if not sample_ids and not attachment_ids:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请至少选择一张图片后再发起边端验证",
        )

    total_selected = len(set(sample_ids)) + len(set(attachment_ids))
    if total_selected > int(settings.YOLO_EDGE_VALIDATION_MAX_IMAGES or 50):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=(
                f"鍗曟杈圭楠岃瘉鏈€澶氬彧鑳介€夋嫨 "
                f"{int(settings.YOLO_EDGE_VALIDATION_MAX_IMAGES or 50)} 寮犲浘鐗?"
            ),
        )

    samples = crud_sample.get_by_ids(db, sample_ids=sample_ids, task_id=cmd.task_id)
    if len(samples) != len(set(sample_ids)):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="存在无效样本，或样本不属于所选任务",
        )

    attachments = _resolve_external_attachments(
        db,
        task_id=cmd.task_id,
        attachment_ids=attachment_ids,
        external_snapshots=external_snapshots,
        current_user=current_user,
    )
    attachment_ids = [
        int(getattr(attachment, "id", 0) or 0) for attachment in attachments
    ]

    labeled_count = sum(1 for sample in samples if _sample_has_annotation(sample))
    image_count = len(samples) + len(attachments)
    unlabeled_count = image_count - labeled_count
    thresholds = _normalize_thresholds(cmd.thresholds or {}, cmd.threshold_source)
    report_summary = (
        f"已选 {image_count} 张图片，其中 {labeled_count} 张可参与精度评估，"
        f"{unlabeled_count} 张仅参与运行验证"
    )

    ai_thresholds_value = (
        cmd.ai_thresholds if isinstance(cmd.ai_thresholds, dict) else None
    )
    if ai_thresholds_value is None and str(thresholds.source or "").startswith("ai"):
        ai_thresholds_value = _model_dump_compat(thresholds)
    request_payload = {
        "sample_ids": sample_ids,
        "attachment_ids": attachment_ids,
        "external_attachments": [
            _attachment_snapshot(attachment) for attachment in attachments
        ],
        "execution_profile": cmd.execution_profile,
    }
    # Stage 1: commit only the request metadata. No filesystem side effect is
    # allowed while this transaction is open.
    with begin_transaction(db):
        row = crud_yolo_edge_validation_job.create(
            db,
            user_id=current_user.id,
            task_id=task.id,
            export_id=export_row.id,
            mode=cmd.mode,
            threshold_source=thresholds.source,
            sample_ids_json=json.dumps(sample_ids, ensure_ascii=False),
            thresholds_json=json.dumps(
                _model_dump_compat(thresholds), ensure_ascii=False
            ),
            ai_thresholds_json=(
                json.dumps(ai_thresholds_value, ensure_ascii=False)
                if isinstance(ai_thresholds_value, dict)
                else None
            ),
            image_count=image_count,
            labeled_image_count=labeled_count,
            unlabeled_image_count=unlabeled_count,
            result_json=json.dumps({"request": request_payload}, ensure_ascii=False),
            report_summary=f"{report_summary}；正在准备边端验证样本包",
        )
        db.flush()

    # Stage 2: build into an immutable signature directory outside the DB
    # transaction, then conditionally commit the READY pointer.
    from labelu.internal.application.service.auto_training_dispatch import (
        _edge_pack_signature,
        _edge_pack_source_evidence,
        _replace_edge_pack_staging,
    )

    db.expire_all()
    source_evidence = _edge_pack_source_evidence(db, sample_ids)
    signature = _edge_pack_signature(
        export_id=int(export_row.id),
        job_id=int(row.id),
        sample_ids=sample_ids,
        sample_task_ids=None,
        attachment_ids=attachment_ids,
        task_config_json=getattr(task, "config", None),
        source_evidence=source_evidence,
    )
    final_root = (
        edge_validation_job_root(export_id=int(export_row.id), job_id=int(row.id))
        / "packs"
        / signature
    )
    staging_root = final_root.parent / f".{signature}.pack-{uuid.uuid4().hex}"
    try:
        pack_result = pack_selected_samples_for_job(
            db,
            export_id=export_row.id,
            job_id=row.id,
            task_id=task.id,
            mode=cmd.mode,
            sample_ids=sample_ids,
            attachment_ids=attachment_ids,
            task_config_json=getattr(task, "config", None),
            root_override=staging_root,
        )
        expected_count = int(image_count)
        copied_count = int(pack_result.get("copied_count") or 0)
        skipped_count = int(pack_result.get("skipped_count") or 0)
        if skipped_count or copied_count != expected_count:
            raise RuntimeError(
                "边端验证样本包不完整，禁止进入边端执行："
                f"expected={expected_count}, copied={copied_count}, skipped={skipped_count}"
            )
        _replace_edge_pack_staging(staging=staging_root, final=final_root)
        pack_result.update(
            {
                "state": "READY",
                "pack_signature": signature,
                "export_id": int(export_row.id),
                "job_id": int(row.id),
                "job_root": str(final_root),
                "eval_data_root": str(final_root / "eval_data"),
                "manifest_path": str(final_root / "manifest.json"),
                "prepared_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        external_snapshots = _append_external_attachment_pack_paths(
            [_attachment_snapshot(attachment) for attachment in attachments],
            pack_result=pack_result,
            attachments=attachments,
        )
        request_payload["external_attachments"] = external_snapshots
        payload = {"request": request_payload, "pack": pack_result}
        with begin_transaction(db):
            updated = (
                db.query(YoloEdgeValidationJob)
                .filter(
                    YoloEdgeValidationJob.id == int(row.id),
                    YoloEdgeValidationJob.status == "PENDING",
                )
                .update(
                    {
                        YoloEdgeValidationJob.result_json: json.dumps(
                            payload, ensure_ascii=False
                        ),
                        YoloEdgeValidationJob.report_summary: (
                            f"{report_summary}；已生成 {pack_result['copied_count']} 张边端验证图片"
                        ),
                    },
                    synchronize_session=False,
                )
            )
            if int(updated or 0) != 1:
                raise RuntimeError("边端验证样本包 READY 提交失败")
        db.expire_all()
        row = crud_yolo_edge_validation_job.get(db, int(row.id))
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        try:
            with begin_transaction(db):
                current = crud_yolo_edge_validation_job.get(db, int(row.id))
                if current is not None and str(current.status or "") == "PENDING":
                    crud_yolo_edge_validation_job.update(
                        db,
                        current,
                        status="FAILED",
                        result_json=json.dumps(
                            {"request": request_payload, "pack": {"state": "FAILED"}},
                            ensure_ascii=False,
                        ),
                        error_message="边端验证样本包准备失败，请重试",
                        report_summary="边端验证样本包准备失败，未进入边端执行",
                        completed_at=datetime.now(),
                    )
        except Exception:
            logger.exception(
                "failed to persist edge pack failure job_id={}",
                getattr(row, "id", None),
            )
        raise
    return _to_detail_response(row, task.name, db=db, current_user=current_user)


def create_legacy_edge_ssh_job_for_export(
    db: Session,
    *,
    export_id: int,
    current_user: User,
    execution_monitor: ExecutionLeaseMonitor | None = None,
) -> YoloEdgeValidationJobDetailResponse:
    export_row = get_export_for_download(
        db, export_id=export_id, current_user=current_user
    )
    task = crud_task.get(db=db, task_id=export_row.task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    thresholds = _normalize_thresholds({}, "system_default")
    # Stage 1: lock the export and commit only the legacy job metadata.
    with begin_transaction(db):
        locked_export = crud_yolo_bm1688_export.get_for_update(db, int(export_row.id))
        if locked_export is None:
            raise RuntimeError("BM1688 导出记录不存在，无法创建边端验证任务")
        if execution_monitor is not None:
            # This conditional update runs in the same transaction as the
            # validation-job insert.  A takeover between the earlier read and
            # this point therefore prevents the stale worker from creating a
            # pending edge job.
            if not conditional_execution_update(
                db,
                execution_monitor.token,
                {"updated_at": datetime.now()},
                manage_transaction=False,
            ):
                raise ExecutionFencingLost("边端验证任务创建前导出执行租约已失效")
        active_row = crud_yolo_edge_validation_job.get_latest_active_for_export(
            db, export_id=int(export_row.id)
        )
        if active_row is not None:
            return _to_detail_response(
                active_row,
                task.name,
                db=db,
                current_user=current_user,
            )
        row = crud_yolo_edge_validation_job.create(
            db,
            user_id=current_user.id,
            task_id=task.id,
            export_id=export_row.id,
            mode="run_and_eval",
            threshold_source=thresholds.source,
            sample_ids_json=json.dumps([], ensure_ascii=False),
            thresholds_json=json.dumps(
                _model_dump_compat(thresholds), ensure_ascii=False
            ),
            ai_thresholds_json=None,
            image_count=0,
            labeled_image_count=0,
            unlabeled_image_count=0,
            result_json=json.dumps(
                {
                    "request": {
                        "sample_ids": [],
                        "attachment_ids": [],
                        "external_attachments": [],
                        "execution_profile": "certification",
                        "selection_source": "legacy_edge_ssh_dataset",
                    }
                },
                ensure_ascii=False,
            ),
            report_summary="正在按训练产物默认评估集准备边端验证任务",
        )
        db.flush()

    from labelu.internal.application.service.auto_training_dispatch import (
        _replace_edge_pack_staging,
    )

    if execution_monitor is not None:
        execution_monitor.ensure_current()
    db.expire_all()
    signature = hashlib.sha256(
        json.dumps(
            {
                "export_id": int(export_row.id),
                "job_id": int(row.id),
                "train_job_id": int(export_row.yolo_train_job_id),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    final_root = (
        edge_validation_job_root(export_id=int(export_row.id), job_id=int(row.id))
        / "packs"
        / signature
    )
    staging_root = final_root.parent / f".{signature}.pack-{uuid.uuid4().hex}"
    try:
        if execution_monitor is not None:
            execution_monitor.ensure_current()
        pack_result = _copy_legacy_eval_dataset_into_job(
            export_id=int(export_row.id),
            job_id=int(row.id),
            yolo_train_job_id=int(export_row.yolo_train_job_id),
            db=db,
            root_override=staging_root,
        )
        copied_count = int(pack_result.get("copied_count") or 0)
        blocking_skipped_count = int(
            pack_result.get("blocking_skipped_count")
            if pack_result.get("blocking_skipped_count") is not None
            else pack_result.get("skipped_count") or 0
        )
        if copied_count <= 0 or blocking_skipped_count > 0:
            raise RuntimeError(
                "训练产物边端验证样本包不完整，禁止进入边端执行："
                f"copied={copied_count}, blocking_skipped={blocking_skipped_count}"
            )
        _replace_edge_pack_staging(staging=staging_root, final=final_root)
        if execution_monitor is not None:
            execution_monitor.ensure_current()
        pack_result.update(
            {
                "state": "READY",
                "pack_signature": signature,
                "export_id": int(export_row.id),
                "job_id": int(row.id),
                "job_root": str(final_root),
                "eval_data_root": str(final_root / "eval_data"),
                "manifest_path": str(final_root / "manifest.json"),
                "prepared_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        labeled_count = int(pack_result.get("labeled_count") or 0)
        unlabeled_count = int(pack_result.get("unlabeled_count") or 0)
        request_payload = {
            "sample_ids": [],
            "attachment_ids": [],
            "external_attachments": [],
            "execution_profile": "certification",
            "selection_source": "legacy_edge_ssh_dataset",
            "legacy_dataset_split": pack_result.get("legacy_dataset_split"),
        }
        with begin_transaction(db):
            if execution_monitor is not None:
                execution_monitor.ensure_current()
            updated = (
                db.query(YoloEdgeValidationJob)
                .filter(
                    YoloEdgeValidationJob.id == int(row.id),
                    YoloEdgeValidationJob.status == "PENDING",
                )
                .update(
                    {
                        YoloEdgeValidationJob.image_count: copied_count,
                        YoloEdgeValidationJob.labeled_image_count: labeled_count,
                        YoloEdgeValidationJob.unlabeled_image_count: unlabeled_count,
                        YoloEdgeValidationJob.result_json: json.dumps(
                            {"request": request_payload, "pack": pack_result},
                            ensure_ascii=False,
                        ),
                        YoloEdgeValidationJob.report_summary: (
                            f"已按训练产物 {pack_result.get('legacy_dataset_split') or 'test/val'} "
                            f"评估集生成 {copied_count} 张边端验证图片"
                        ),
                    },
                    synchronize_session=False,
                )
            )
            if int(updated or 0) != 1:
                raise RuntimeError("边端验证样本包 READY 提交失败")
        db.expire_all()
        row = crud_yolo_edge_validation_job.get(db, int(row.id))
    except ExecutionFencingLost:
        shutil.rmtree(staging_root, ignore_errors=True)
        shutil.rmtree(final_root, ignore_errors=True)
        # Leave the pending row for the current worker/reconciler; a stale
        # worker must not rewrite it as FAILED without its execution token.
        raise
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        # Keep the submitted row as an auditable failure. FAILED is not an
        # executable queue state, so a partial/stale pack cannot reach SSH.
        try:
            with begin_transaction(db):
                current = crud_yolo_edge_validation_job.get(db, int(row.id))
                if current is not None and str(current.status or "") == "PENDING":
                    crud_yolo_edge_validation_job.update(
                        db,
                        current,
                        status="FAILED",
                        result_json=json.dumps(
                            {
                                "request": {
                                    "sample_ids": [],
                                    "attachment_ids": [],
                                    "external_attachments": [],
                                    "execution_profile": "quick",
                                    "selection_source": "legacy_edge_ssh_dataset",
                                },
                                "pack": {"state": "FAILED"},
                            },
                            ensure_ascii=False,
                        ),
                        error_message="边端验证样本包准备失败，请重试",
                        report_summary="边端验证样本包准备失败，未进入边端执行",
                        completed_at=datetime.now(),
                    )
        except Exception:
            logger.exception(
                "failed to persist legacy edge pack failure job_id={}",
                getattr(row, "id", None),
            )
        raise
    return _to_detail_response(row, task.name, db=db, current_user=current_user)


def run_legacy_edge_ssh_job_sync(
    db: Session,
    *,
    export_id: int,
    current_user: User,
    execution_monitor: ExecutionLeaseMonitor | None = None,
) -> YoloEdgeValidationJobDetailResponse:
    detail = create_legacy_edge_ssh_job_for_export(
        db,
        export_id=export_id,
        current_user=current_user,
        execution_monitor=execution_monitor,
    )
    db.expire_all()
    fresh = crud_yolo_edge_validation_job.get(db, int(detail.id))
    if fresh is not None and str(fresh.status or "") == "PENDING":
        if execution_monitor is not None:
            execution_monitor.ensure_current()
        from labelu.internal.application.service import yolo_edge_validation_scheduler

        yolo_edge_validation_scheduler.enqueue_edge_validation_job(int(detail.id))
        db.expire_all()
        fresh = crud_yolo_edge_validation_job.get(db, int(detail.id))
    task = crud_task.get(db=db, task_id=int(fresh.task_id)) if fresh else None
    positions = _canonical_queue_positions(
        db,
        [fresh] if fresh is not None else [],
        current_user,
    )
    return _to_detail_response(
        fresh,
        task.name if task else None,
        db=db,
        current_user=current_user,
        queue_position=positions.get(int(fresh.id)) if fresh is not None else None,
    )


def list_jobs(
    db: Session,
    *,
    current_user: User,
    export_id: int | None,
    task_id: int | None,
    keyword: str | None = None,
    status_value: str | None,
    page: int,
    size: int,
    source_system: str | None = None,
    pipeline_origin: str | None = None,
):
    rows, total = crud_yolo_edge_validation_job.list_for_user(
        db,
        current_user.id,
        export_id=export_id,
        task_id=task_id,
        keyword=keyword,
        status=status_value,
        source_system=source_system,
        pipeline_origin=pipeline_origin,
        page=page,
        size=size,
        task_access_unrestricted=is_task_superuser(current_user),
    )
    positions = _canonical_queue_positions(
        db,
        [row for row, _task in rows],
        current_user,
    )

    data = [
        _to_row_response(
            row,
            task.name,
            queue_position=positions.get(int(row.id)),
        )
        for row, task in rows
    ]
    meta = MetaData(total=total, page=page, size=len(data))
    return data, meta


def get_job(
    db: Session,
    *,
    job_id: int,
    current_user: User,
) -> YoloEdgeValidationJobDetailResponse:
    row, task = _load_job_with_access(db, job_id=job_id, current_user=current_user)
    positions = _canonical_queue_positions(db, [row], current_user)

    return _to_detail_response(
        row,
        task.name,
        db=db,
        current_user=current_user,
        queue_position=positions.get(int(row.id)),
    )


def get_job_preview_asset(
    db: Session,
    *,
    job_id: int,
    filename: str,
    current_user: User,
) -> Path:
    row, _ = _load_job_with_access(db, job_id=job_id, current_user=current_user)
    name = Path((filename or "").strip()).name
    if (
        not name
        or name != filename
        or ".." in filename
        or Path(name).suffix.lower() not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    ):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message="边端验证预览图片不存在",
        )

    job_root = edge_validation_job_root(
        export_id=int(row.export_id), job_id=int(row.id)
    )
    candidates = (
        job_root / "server_eval" / "bmcv_preview",
        job_root / "edge_ssh_pull" / "output" / "bmcv_preview",
        job_root / "edge_ssh_pull" / "output" / "results_bmodel" / "images",
        job_root / "bmcv_preview",
    )
    for base in candidates:
        base = base.resolve()
        path = (base / name).resolve()
        try:
            path.relative_to(base)
        except ValueError:
            continue
        if path.is_file():
            return path

    raise LabelUException(
        code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
        status_code=status.HTTP_404_NOT_FOUND,
        message="边端验证预览图片不存在",
    )


def _build_manual_edge_validation_bundle(
    db: Session,
    *,
    job_id: int,
    export_row,
    row,
    thresholds: dict,
) -> tuple[Path, Path]:
    from labelu.internal.application.service import board_agent
    from labelu.internal.common.edge_script_paths import (
        resolve_board_agent_script,
        resolve_edge_bmodel_validator,
        resolve_edge_board_validate_runner,
        resolve_plate_detection_bm1688_infer,
        resolve_plate_recognition_bm1688_infer,
        resolve_python_test_evaluation,
        resolve_sop_eval_dir,
    )

    job_root = edge_validation_job_root(export_id=export_row.id, job_id=job_id)
    persisted_pack = _job_result_json(row).get("pack") or {}
    persisted_pack_root = str(persisted_pack.get("job_root") or "").strip()
    pack_root = (
        Path(persisted_pack_root) if persisted_pack_root else job_root
    ) / "eval_data"
    if not pack_root.is_dir():
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="边端验证数据包不存在，请重新创建任务",
        )

    # A validation pack is immutable for one run, but it must not be reused
    # after the training class contract changes. Older jobs were created
    # before the training job persisted class order and could contain labels
    # sorted by display name, while the BModel still uses data.yaml ids.
    # Rebuild such a stale legacy pack automatically on retry.
    persisted_manifest_path = str(persisted_pack.get("manifest_path") or "").strip()
    persisted_manifest = (
        _decode_json(Path(persisted_manifest_path).read_text(encoding="utf-8"), {})
        if persisted_manifest_path and Path(persisted_manifest_path).is_file()
        else {}
    )
    # Older bundle callers (and lightweight offline pack probes) pass a
    # placeholder DB object because no class-contract rebuild is possible in
    # that mode.  Keep the compatibility path deterministic; a real
    # SQLAlchemy session always exposes ``query`` and therefore still enforces
    # the persisted training class contract.
    expected_contract = (
        class_contract_for_export(db, int(export_row.id))
        if callable(getattr(db, "query", None))
        else {}
    )
    expected_names = list(expected_contract.get("class_names") or [])
    manifest_names = list(persisted_manifest.get("class_names") or [])
    if expected_names and manifest_names != expected_names:
        signature_payload = {
            "export_id": int(export_row.id),
            "job_id": int(job_id),
            "class_names": expected_names,
            "data_yaml_sha256": expected_contract.get("data_yaml_sha256"),
        }
        signature = hashlib.sha256(
            json.dumps(signature_payload, ensure_ascii=False, sort_keys=True).encode(
                "utf-8"
            )
        ).hexdigest()
        rebuilt_root = job_root / "packs" / signature
        rebuilt = _copy_legacy_eval_dataset_into_job(
            export_id=int(export_row.id),
            job_id=int(job_id),
            yolo_train_job_id=int(export_row.yolo_train_job_id),
            db=db,
            root_override=rebuilt_root,
        )
        rebuilt.update(
            {
                "state": "READY",
                "pack_signature": signature,
                "export_id": int(export_row.id),
                "job_id": int(job_id),
                "job_root": str(rebuilt_root),
                "eval_data_root": str(rebuilt_root / "eval_data"),
                "manifest_path": str(rebuilt_root / "manifest.json"),
                "prepared_at": datetime.now().isoformat(timespec="seconds"),
                "rebuild_reason": "训练类别顺序发生变化，自动重建验证包",
            }
        )
        request_payload = _job_result_json(row).get("request") or {}
        with begin_transaction(db):
            crud_yolo_edge_validation_job.update(
                db,
                row,
                image_count=int(rebuilt.get("copied_count") or 0),
                labeled_image_count=int(rebuilt.get("labeled_count") or 0),
                unlabeled_image_count=int(rebuilt.get("unlabeled_count") or 0),
                result_json=json.dumps(
                    {"request": request_payload, "pack": rebuilt},
                    ensure_ascii=False,
                ),
                report_summary="检测到类别契约旧版本，已自动重建边端验证包",
            )
        persisted_pack = rebuilt
        pack_root = rebuilt_root / "eval_data"

    staging = job_root / "edge_ssh_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    zip_path = board_agent.build_models_zip_path(export_row.id, db)
    safe_extract_zip(zip_path, staging)

    shutil.copytree(pack_root, staging / "eval_data", dirs_exist_ok=True)
    persisted_manifest = str(persisted_pack.get("manifest_path") or "").strip()
    manifest_path = (
        Path(persisted_manifest) if persisted_manifest else job_root / "manifest.json"
    )
    manifest = {}
    if manifest_path.is_file():
        shutil.copy2(manifest_path, staging / "manifest.json")
        manifest = _decode_json(manifest_path.read_text(encoding="utf-8"), {})

    dest_scripts = staging / "scripts"
    (dest_scripts / "ops").mkdir(parents=True, exist_ok=True)
    ops_src = resolve_board_agent_script()
    runner_src = resolve_edge_board_validate_runner()
    sop_src = resolve_sop_eval_dir()
    python_test_src = resolve_python_test_evaluation()
    if ops_src.is_file():
        shutil.copy2(ops_src, dest_scripts / "ops" / "labelu_board_agent.py")
    if runner_src.is_file():
        shutil.copy2(runner_src, dest_scripts / "edge_board_validate_runner.py")
    sop_dest = dest_scripts / "sop_eval"
    sop_dest.mkdir(parents=True, exist_ok=True)
    for name in (
        "evaluation_utils.py",
        "yolo_eval_postprocess.py",
        "cloud_based_evaluation.py",
        "onnx_based_evaluation.py",
        "sophon_bmodel_evaluation.py",
        "edge_performance_probe.py",
    ):
        fp = sop_src / name
        if fp.is_file():
            shutil.copy2(fp, sop_dest / name)
    if python_test_src.is_file():
        shutil.copy2(python_test_src, sop_dest / "pythonTest_evaluation.py")

    export_root = _export_dir_for_row(int(export_row.id))
    plate_contract = export_root / "plate_recognition_contract.json"
    plate_model_dir = staging / "models" / "BM1688"
    if plate_contract.is_file():
        validator_src = resolve_edge_bmodel_validator()
        plate_runner_src = resolve_plate_recognition_bm1688_infer()
        if not validator_src.is_file() or not plate_runner_src.is_file():
            raise RuntimeError("plate-recognition edge validation scripts are missing")
        plate_model_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            plate_contract, plate_model_dir / "plate_recognition_contract.json"
        )
        plate_validation_dir = dest_scripts / "plate_validation"
        plate_validation_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(validator_src, plate_validation_dir / "validate.py")
        shutil.copy2(
            plate_runner_src,
            plate_validation_dir / "plate_recognition_bm1688_infer.py",
        )

    plate_detection_contract = export_root / "plate_detection_contract.json"
    if plate_detection_contract.is_file():
        validator_src = resolve_edge_bmodel_validator()
        plate_detection_runner_src = resolve_plate_detection_bm1688_infer()
        plate_recognition_runner_src = resolve_plate_recognition_bm1688_infer()
        if (
            not validator_src.is_file()
            or not plate_detection_runner_src.is_file()
            or not plate_recognition_runner_src.is_file()
        ):
            raise RuntimeError("plate-detection edge validation scripts are missing")
        plate_model_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            plate_detection_contract,
            plate_model_dir / "plate_detection_contract.json",
        )
        plate_validation_dir = dest_scripts / "plate_validation"
        plate_validation_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(validator_src, plate_validation_dir / "validate.py")
        shutil.copy2(
            plate_detection_runner_src,
            plate_validation_dir / "plate_detection_bm1688_infer.py",
        )
        shutil.copy2(
            plate_recognition_runner_src,
            plate_validation_dir / "plate_recognition_bm1688_infer.py",
        )

    edge_ssh_validation._prepare_edge_runtime_dependency_bundle(staging)

    pack = _job_result_json(row).get("pack") or {}
    from labelu.internal.common import edge_device_registry

    cfg = edge_device_registry.load_device_config(
        str(getattr(row, "edge_device_id", None) or "")
    )
    threshold_contract = board_agent.build_board_threshold_contract(
        export_row, fallback=thresholds
    )
    artifact_manifest = _decode_json(
        getattr(export_row, "artifact_manifest_json", None), {}
    )
    if not isinstance(artifact_manifest, dict):
        artifact_manifest = {}
    profile = artifact_manifest.get("bm1688_profile")
    if not isinstance(profile, dict):
        profile = artifact_manifest.get("profile_snapshot")
    profile_digest = (
        board_agent.profile_snapshot_digest(profile)
        if isinstance(profile, dict)
        else None
    )
    artifact_signature = (
        str(
            artifact_manifest.get("execution_signature")
            or getattr(export_row, "execution_signature", None)
            or ""
        ).strip()
        or None
    )
    run_cfg = {
        "schema_version": threshold_contract.get("schema_version"),
        # Freeze the same versioned conversion/edge gate that the server uses;
        # the board runner must never invent a looser default.
        "quality_rule": get_bm1688_quality_rule(),
        "validation_mode": str(row.mode or "run_and_eval"),
        "export_id": int(export_row.id),
        "edge_validation_job_id": int(job_id),
        "artifact_execution_signature": artifact_signature,
        "execution_generation": artifact_signature or f"export:{int(export_row.id)}",
        "execution_generation_scope": f"export:{int(export_row.id)}",
        "execution_generation_issued_at": artifact_manifest.get("created_at"),
        "artifact_manifest": artifact_manifest or None,
        "bm1688_profile": profile if isinstance(profile, dict) else None,
        "profile_digest": profile_digest,
        "execution_fencing": (
            {
                "token": int(getattr(row, "execution_fencing_token", 0) or 0),
                "status": "leased",
            }
            if int(getattr(row, "execution_fencing_token", 0) or 0) > 0
            else None
        ),
        "imgsz": int(export_row.imgsz or 640),
        "threshold_contract": threshold_contract,
        "threshold_unit": threshold_contract["threshold_unit"],
        "threshold_source": threshold_contract["source"],
        "defaulted_fields": threshold_contract["defaulted_fields"],
        "conf_thres": threshold_contract["conf_thres"],
        "nms_thres": threshold_contract["nms_thres"],
        "match_iou_thres": threshold_contract["match_iou_thres"],
        "preprocessing_contract": {
            "resize": "letterbox",
            "pad_value": 114,
            "color_order": "RGB",
            "scale": 1.0 / 255.0,
        },
        "class_names": (
            manifest.get("class_names")
            if isinstance(manifest.get("class_names"), list)
            else []
        ),
        "class_contract": (
            manifest.get("class_contract")
            if isinstance(manifest.get("class_contract"), dict)
            else {}
        ),
        "eval_image_count": int(pack.get("copied_count") or 0),
        # The manifest digest is the immutable identity of the selected eval
        # pack. Every PT/BModel precision and the board report must carry this
        # same value; it is not the full training dataset digest.
        "fixed_eval_signature": str(manifest.get("manifest_sha256") or "").strip(),
        "require_fixed_eval_binding": True,
        "bmcv_preview_max": int(pack.get("copied_count") or 0),
        "labeled_image_count": int(pack.get("labeled_count") or 0),
        "unlabeled_image_count": int(pack.get("unlabeled_count") or 0),
        "run_coco_eval": bool(cfg.get("run_coco_eval")),
        "timeout_sec": int(cfg.get("validation_timeout_sec") or 7200),
        "execution_profile": _job_execution_profile(row),
        "performance_enabled": _job_execution_profile(row) == "certification",
        # Complete checks collect comparable latency/resource evidence for all
        # three BM1688 precisions. INT8 remains the release baseline, while
        # FP32/FP16 provide useful conversion and performance comparisons.
        "performance_model_tags": (
            ["fp32", "fp16", "int8"]
            if _job_execution_profile(row) == "certification"
            else []
        ),
        "performance_warmup_count": int(settings.EDGE_PERFORMANCE_WARMUP_COUNT),
        "performance_repeat_count": int(settings.EDGE_PERFORMANCE_REPEAT_COUNT),
        "performance_resource_interval_seconds": float(
            settings.EDGE_PERFORMANCE_RESOURCE_INTERVAL_SECONDS
        ),
        "performance_stability_seconds": int(
            settings.EDGE_PERFORMANCE_STABILITY_SECONDS
        ),
    }
    (staging / "run_config.json").write_text(
        json.dumps(run_cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    bundle = job_root / "edge_ssh_bundle.tar.gz"
    with tarfile.open(bundle, "w:gz") as tar:
        for fp in staging.rglob("*"):
            if fp.is_file():
                tar.add(fp, arcname=fp.relative_to(staging).as_posix())
    return bundle, staging


def _edge_model_cache_key(staging: Path) -> str | None:
    model_root = Path(staging) / "models"
    files = sorted(path for path in model_root.rglob("*") if path.is_file())
    if not files:
        return None
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(model_root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(int(path.stat().st_size).to_bytes(8, "big"))
        with path.open("rb") as source:
            while True:
                chunk = source.read(8 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    return digest.hexdigest()


def _build_cached_edge_task_bundle(staging: Path, destination: Path) -> Path:
    destination = Path(destination)
    with tarfile.open(destination, "w:gz") as archive:
        for path in Path(staging).rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(staging)
            if relative.parts and relative.parts[0] == "models":
                continue
            archive.add(path, arcname=relative.as_posix())
    return destination


def _count_local_files(root: Path) -> int:
    return sum(1 for path in Path(root).rglob("*") if path.is_file())


def _download_manual_edge_output(
    client,
    *,
    remote_job: str,
    local_out: Path,
    summary: dict,
) -> int:
    local_out = Path(local_out)
    if local_out.exists():
        shutil.rmtree(local_out)
    pull_root = local_out.parent
    pull_root.mkdir(parents=True, exist_ok=True)
    output_archive = pull_root / "output-lite.tar.gz"
    extract_root = pull_root / ".output-lite-extract"
    output_archive.unlink(missing_ok=True)
    if extract_root.exists():
        shutil.rmtree(extract_root)

    try:
        archive_downloaded = edge_ssh_validation.sftp_download(
            client,
            f"{remote_job}/output-lite.tar.gz",
            output_archive,
        )
        if archive_downloaded:
            archive_bytes = int(output_archive.stat().st_size)
            safe_extract_tar(output_archive, extract_root, mode="r:gz")
            extracted_output = extract_root / "output"
            if not extracted_output.is_dir():
                raise ValueError("边端轻量结果归档缺少 output 目录")
            shutil.move(str(extracted_output), str(local_out))
            summary["output_transfer"] = "archive_lite"
            summary["output_archive_bytes"] = archive_bytes
            return _count_local_files(local_out)
    except Exception as exc:
        summary["output_archive_error"] = str(exc)[:500]
        if local_out.exists():
            shutil.rmtree(local_out)
    finally:
        output_archive.unlink(missing_ok=True)
        if extract_root.exists():
            shutil.rmtree(extract_root)

    pulled = edge_ssh_validation.sftp_download_tree(
        client, f"{remote_job}/output", local_out
    )
    if pulled == 0:
        pulled = edge_ssh_validation.sftp_download_tree(
            client, f"{remote_job}/work/output", local_out
        )
    summary["output_transfer"] = "sftp_tree_fallback"
    return pulled


def _manual_output_root(job_id: int, export_id: int) -> Path:
    return (
        edge_validation_job_root(export_id=export_id, job_id=job_id)
        / "edge_ssh_pull"
        / "output"
    )


def _manual_report_root(job_id: int, export_id: int) -> Path:
    return edge_validation_job_root(export_id=export_id, job_id=job_id) / "server_eval"


def _ingest_manual_results(job_id: int, export_id: int, local_output: Path) -> dict:
    report_root = _manual_report_root(job_id, export_id)
    report_root.mkdir(parents=True, exist_ok=True)
    result = {"ingested": [], "warnings": []}

    bmodel_src = local_output / "results_bmodel"
    if bmodel_src.is_dir():
        bmodel_dst = report_root / "results_bmodel"
        if bmodel_dst.exists():
            shutil.rmtree(bmodel_dst)
        shutil.copytree(bmodel_src, bmodel_dst)
        result["ingested"].append("results_bmodel")

    board_report = _decode_json(
        (
            (local_output / "board_report.json").read_text(encoding="utf-8")
            if (local_output / "board_report.json").is_file()
            else None
        ),
        None,
    )
    if isinstance(board_report, dict):
        result["board_report"] = board_report

    bmcv_summary = _decode_json(
        (
            (local_output / "bmcv_infer_summary.json").read_text(encoding="utf-8")
            if (local_output / "bmcv_infer_summary.json").is_file()
            else None
        ),
        None,
    )
    if isinstance(bmcv_summary, dict):
        result["bmcv_infer"] = bmcv_summary

    bmcv_preview_src = local_output / "bmcv_preview"
    if bmcv_preview_src.is_dir():
        bmcv_preview_dst = report_root / "bmcv_preview"
        if bmcv_preview_dst.exists():
            shutil.rmtree(bmcv_preview_dst)
        shutil.copytree(bmcv_preview_src, bmcv_preview_dst)
        result["ingested"].append("bmcv_preview")

    bmodel_sop = _decode_json(
        (
            (local_output / "bmodel_sop_eval.json").read_text(encoding="utf-8")
            if (local_output / "bmodel_sop_eval.json").is_file()
            else None
        ),
        None,
    )
    if isinstance(bmodel_sop, dict):
        result["bmodel_sop"] = bmodel_sop

    runner_summary = _decode_json(
        (
            (local_output / "summary.json").read_text(encoding="utf-8")
            if (local_output / "summary.json").is_file()
            else None
        ),
        None,
    )
    if isinstance(runner_summary, dict):
        result["runner_summary"] = runner_summary

    return result


def _run_manual_job_sync(job_id: int) -> None:
    lock = _job_run_lock(job_id)
    if not lock.acquire(blocking=False):
        logger.info("manual edge validation job {} already running", job_id)
        return

    device_id: str | None = None
    summary: dict = {}
    run_started = time.monotonic()
    execution_monitor: ExecutionLeaseMonitor | None = None
    try:
        with session_scope() as db:
            row = crud_yolo_edge_validation_job.get(db, job_id)
            if not row:
                return
            if execution_monitor is not None:
                row._execution_lease_monitor = execution_monitor
                execution_monitor.ensure_current()
            device_id = str(getattr(row, "edge_device_id", None) or "").strip() or None
            if device_id and _registered_board_agent_device(device_id):
                logger.info(
                    "skip legacy SSH Runner for Board Agent job_id={} device_id={}",
                    job_id,
                    device_id,
                )
                return
            if row.status == "PROCESSING" and is_stale_edge_validation_job(row):
                from labelu.internal.application.service import (
                    yolo_edge_validation_scheduler,
                )
                from labelu.internal.common import edge_device_registry

                if not device_id:
                    active = edge_device_registry.get_active_device_record()
                    if active is not None:
                        cfg = edge_device_registry.load_device_config(str(active["id"]))
                        device_id = str(active["id"])
                        with begin_transaction(db):
                            crud_yolo_edge_validation_job.update(
                                db,
                                row,
                                edge_device_id=device_id,
                                edge_device_name=str(active.get("name") or "")[:64],
                                edge_host=str(cfg.get("host") or "")[:256],
                                edge_port=int(cfg.get("port") or 22),
                                edge_user=str(cfg.get("user") or "")[:128],
                            )

                yolo_edge_validation_scheduler.enqueue_edge_validation_job(
                    job_id,
                    preserve_assigned_device=bool(device_id),
                )
                return
            if row.status != "PROCESSING" or not device_id:
                from labelu.internal.application.service import (
                    yolo_edge_validation_scheduler,
                )

                if can_resume_edge_validation_job(row):
                    yolo_edge_validation_scheduler.enqueue_edge_validation_job(
                        job_id,
                        preserve_assigned_device=bool(device_id),
                    )
                return
            from labelu.internal.application.service.yolo_edge_validation_pack import (
                edge_validation_pack_committed,
            )

            if not edge_validation_pack_committed(row):
                # Device assignment is committed by the scheduler before this
                # function starts. A missing/uncommitted pack must release that
                # assignment and return to the queue without SSH side effects.
                with begin_transaction(db):
                    crud_yolo_edge_validation_job.update(
                        db,
                        row,
                        status="PENDING",
                        queue_reason="pack_not_ready",
                        queued_at=datetime.now(),
                        assigned_at=None,
                        lease_expires_at=None,
                        edge_device_id=None,
                        edge_device_name=None,
                        edge_host=None,
                        edge_port=None,
                        edge_user=None,
                        report_summary="边端验证样本包尚未提交，等待重新准备",
                    )
                return
            owner = f"edge-validation:{os.getpid()}:{threading.get_ident()}:{job_id}"
            try:
                with session_scope() as fence_db:
                    execution_token = claim_execution_lease(
                        fence_db,
                        YoloEdgeValidationJob,
                        int(job_id),
                        owner=owner,
                        lease_seconds=900,
                    )
            except ExecutionFencingUnavailable:
                logger.warning(
                    "edge validation execution fencing columns unavailable; "
                    "fail closed without SSH job_id={}",
                    job_id,
                )
                with begin_transaction(db):
                    _requeue_when_execution_fencing_unavailable(db, job_id)
                return
            if execution_token is None:
                logger.warning(
                    "skip edge validation claimed by another worker job_id={}",
                    job_id,
                )
                return
            if execution_token is not None:
                execution_monitor = ExecutionLeaseMonitor(
                    execution_token,
                    session_scope_factory=session_scope,
                    label=f"edge-validation-{job_id}",
                ).start()
                execution_monitor.ensure_current()
                row._execution_lease_monitor = execution_monitor
            initial_updates = {
                "error_message": None,
                "started_at": datetime.now(),
                "completed_at": None,
                "progress_json": None,
            }
            if execution_monitor is not None:
                with begin_transaction(db):
                    execution_monitor.conditional_update(initial_updates, db=db)
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="prepare",
                        message="任务已启动，正在准备边端验证上下文",
                        detail={"job_id": int(job_id)},
                        report_summary="手动边端验证任务已启动",
                    )
            else:
                with begin_transaction(db):
                    crud_yolo_edge_validation_job.update(
                        db,
                        row,
                        **initial_updates,
                    )
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="prepare",
                        message="任务已启动，正在准备边端验证上下文",
                        detail={"job_id": int(job_id)},
                        report_summary="手动边端验证任务已启动",
                    )

        with session_scope() as db:
            row = crud_yolo_edge_validation_job.get(db, job_id)
            if execution_monitor is not None:
                row._execution_lease_monitor = execution_monitor
                execution_monitor.ensure_current()
            export_row = crud_yolo_bm1688_export.get(db, int(row.export_id))
            if not export_row:
                raise LabelUException(
                    code=ErrorCode.CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND,
                    status_code=status.HTTP_404_NOT_FOUND,
                    message="未找到关联的 BM1688 导出记录",
                )
            thresholds = _decode_json(row.thresholds_json, {})
            train_job = crud_yolo_train_job.get(db, int(export_row.yolo_train_job_id))
            if not train_job:
                raise RuntimeError("边端验证关联的训练作业不存在")
            with begin_transaction(db):
                _update_manual_job_progress(
                    db,
                    row,
                    stage="pack",
                    message="正在整理样本包与导出产物",
                    detail={"export_id": int(export_row.id)},
                    report_summary="正在整理边端验证样本包",
                )
            stage_started = time.monotonic()
            _ensure_manual_execution_current(execution_monitor)
            bundle, staging = _build_manual_edge_validation_bundle(
                db,
                job_id=job_id,
                export_row=export_row,
                row=row,
                thresholds=thresholds,
            )
            summary.setdefault("stage_durations_seconds", {})["pack"] = round(
                time.monotonic() - stage_started, 3
            )
            # The staging directory is the exact immutable package that will
            # be uploaded to BM1688. Read its manifest first so a stale
            # legacy manifest under the job root cannot change this run's
            # class contract or evaluation scope.
            manifest_path = staging / "manifest.json"
            if not manifest_path.is_file():
                persisted_pack = _job_result_json(row).get("pack") or {}
                persisted_manifest = str(
                    persisted_pack.get("manifest_path") or ""
                ).strip()
                manifest_path = (
                    Path(persisted_manifest)
                    if persisted_manifest
                    else edge_validation_job_root(
                        export_id=int(export_row.id), job_id=job_id
                    )
                    / "manifest.json"
                )
            manifest = _decode_json(
                (
                    manifest_path.read_text(encoding="utf-8")
                    if manifest_path.is_file()
                    else None
                ),
                {},
            )

            from labelu.internal.common import edge_device_registry

            cfg = edge_device_registry.load_device_config(str(row.edge_device_id or ""))
            if not cfg:
                raise RuntimeError(f"已分配的边端设备不存在: {row.edge_device_id}")
            host = str(row.edge_host or cfg.get("host") or "").strip()
            port = int(row.edge_port or cfg.get("port") or 22)
            user = str(row.edge_user or cfg.get("user") or "").strip()
            password = str(cfg.get("password") or "")
            private_key_path = str(cfg.get("private_key_path") or "")
            from labelu.internal.common.edge_remote_paths import edge_remote_paths

            paths = edge_remote_paths(cfg.get("edge_root"))
            remote_base = paths.jobs
            remote_job = paths.job(f"manual-job-{job_id}")
            docker_container = str(cfg.get("docker_container") or "").strip()
            docker_use_sudo = bool(cfg.get("docker_use_sudo", True))
            sophon_root = paths.sophon_demo_root
            edge_device_registry.mark_device_used(str(row.edge_device_id or ""))

            with begin_transaction(db):
                _update_manual_job_progress(
                    db,
                    row,
                    stage="ssh_connect",
                    message="正在连接边端设备",
                    detail={
                        "remote_host": host,
                        "remote_port": port,
                        "remote_user": user,
                    },
                    report_summary=f"正在连接边端设备 {host}:{port}",
                )
            _ensure_manual_execution_current(execution_monitor)
            client = edge_ssh_validation.ssh_connect(
                host=host,
                port=port,
                username=user,
                password=password,
                private_key_path=private_key_path or None,
                timeout=int(cfg.get("connect_timeout_sec") or 30),
            )
            summary.update(
                {
                    "status": "pending",
                    "export_id": int(export_row.id),
                    "job_id": int(job_id),
                    "remote_host": host,
                    "remote_job_dir": remote_job,
                    "execution_profile": _job_execution_profile(row),
                }
            )
            try:

                def _reconnect_for_upload():
                    return edge_ssh_validation.ssh_connect(
                        host=host,
                        port=port,
                        username=user,
                        password=password,
                        private_key_path=private_key_path or None,
                        timeout=int(cfg.get("connect_timeout_sec") or 30),
                    )

                runtime_container = docker_container or "bm1688"
                with begin_transaction(db):
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="environment_probe",
                        message="正在检查边端 BM1688 运行环境",
                        detail={"remote_host": host, "container": runtime_container},
                        report_summary="正在检查边端运行环境",
                    )

                runtime_upload_state = {"last_pct": -1, "retry_count": 0}

                def _persist_runtime_upload_progress(sent: int, total: int) -> None:
                    total_bytes = max(0, int(total or 0))
                    sent_bytes = max(0, min(int(sent or 0), total_bytes))
                    pct = int((sent_bytes * 100) / total_bytes) if total_bytes else 0
                    last_pct = int(runtime_upload_state["last_pct"])
                    if pct != 100 and last_pct >= 0 and pct < last_pct + 5:
                        return
                    runtime_upload_state["last_pct"] = pct
                    with begin_transaction(db):
                        _update_manual_job_progress(
                            db,
                            row,
                            stage="environment_provision",
                            message=f"正在上传边端离线运行时（{pct}%）",
                            detail={
                                "uploaded_bytes": sent_bytes,
                                "total_bytes": total_bytes,
                                "upload_pct": pct,
                                "retry_count": int(runtime_upload_state["retry_count"]),
                            },
                            report_summary=f"边端离线运行时上传 {pct}%",
                        )

                def _record_runtime_upload_retry(
                    next_attempt: int, attempt_cap: int, exc: Exception
                ) -> None:
                    runtime_upload_state["retry_count"] = max(1, next_attempt - 1)
                    with begin_transaction(db):
                        _update_manual_job_progress(
                            db,
                            row,
                            stage="environment_provision",
                            message="离线运行时上传中断，正在自动续传",
                            detail={
                                "next_attempt": next_attempt,
                                "attempt_cap": attempt_cap,
                                "error": str(exc)[:500],
                            },
                            report_summary="边端离线运行时正在自动续传",
                        )

                stage_started = time.monotonic()
                _ensure_manual_execution_current(execution_monitor)
                client, environment = edge_runtime_provision.ensure_edge_runtime(
                    client,
                    configured_demo_root=sophon_root,
                    docker_container=runtime_container,
                    reconnect=_reconnect_for_upload,
                    progress_callback=_persist_runtime_upload_progress,
                    retry_callback=_record_runtime_upload_retry,
                )
                summary["stage_durations_seconds"]["environment"] = round(
                    time.monotonic() - stage_started, 3
                )
                summary["environment"] = environment
                resolved_runtime_demo = str(
                    environment.get("sophon_demo_root") or ""
                ).strip()
                if resolved_runtime_demo:
                    sophon_root = resolved_runtime_demo
                docker_container = runtime_container
                with begin_transaction(db):
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="environment_verify",
                        message=(
                            "边端环境自动修复并复检通过"
                            if environment.get("provisioned")
                            else "边端环境检查通过，无需安装"
                        ),
                        detail={
                            "environment_ready": True,
                            "provisioned": bool(environment.get("provisioned")),
                            "sophon_demo_root": sophon_root,
                        },
                        report_summary="边端运行环境已就绪",
                    )

                model_cache_key = _edge_model_cache_key(staging)
                model_cache_hit = False
                if model_cache_key:
                    try:
                        model_cache_hit = edge_ssh_validation.edge_model_cache_exists(
                            client,
                            model_cache_key,
                            edge_root=paths.root,
                            docker_container=docker_container,
                            docker_use_sudo=docker_use_sudo,
                        )
                    except Exception as cache_exc:
                        summary["model_cache_probe_error"] = str(cache_exc)[:500]
                    if model_cache_hit:
                        bundle = _build_cached_edge_task_bundle(
                            staging,
                            bundle.with_name("edge_ssh_task_bundle.tar.gz"),
                        )
                summary["model_cache_key"] = model_cache_key
                summary["model_cache_hit"] = model_cache_hit
                bundle_bytes = int(bundle.stat().st_size)
                upload_state = {"last_pct": -1, "retry_count": 0}

                def _persist_upload_progress(sent: int, total: int) -> None:
                    total_bytes = max(0, int(total or bundle_bytes))
                    sent_bytes = max(0, min(int(sent or 0), total_bytes))
                    pct = int((sent_bytes * 100) / total_bytes) if total_bytes else 0
                    last_pct = int(upload_state["last_pct"])
                    if pct != 100 and last_pct >= 0 and pct < last_pct + 5:
                        return
                    upload_state["last_pct"] = pct
                    with begin_transaction(db):
                        _update_manual_job_progress(
                            db,
                            row,
                            stage="upload",
                            message=f"正在上传样本包到边端设备（{pct}%）",
                            detail={
                                "remote_job_dir": remote_job,
                                "uploaded_bytes": sent_bytes,
                                "total_bytes": total_bytes,
                                "upload_pct": pct,
                                "retry_count": int(upload_state["retry_count"]),
                            },
                            report_summary=f"边端样本包上传 {pct}%",
                        )

                def _record_upload_retry(
                    next_attempt: int, attempt_cap: int, exc: Exception
                ) -> None:
                    retry_count = max(1, int(next_attempt) - 1)
                    upload_state["retry_count"] = retry_count
                    with begin_transaction(db):
                        _update_manual_job_progress(
                            db,
                            row,
                            stage="upload_retry",
                            message=(
                                "上传连接中断，正在等待边端恢复后续传"
                                f"（第 {next_attempt}/{attempt_cap} 次尝试）"
                            ),
                            detail={
                                "remote_job_dir": remote_job,
                                "retry_count": retry_count,
                                "next_attempt": int(next_attempt),
                                "attempt_cap": int(attempt_cap),
                                "error": str(exc)[:500],
                            },
                            report_summary="边端连接中断，等待重连续传",
                        )

                with begin_transaction(db):
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="upload",
                        message="正在上传样本包到边端设备",
                        detail={
                            "remote_job_dir": remote_job,
                            "uploaded_bytes": 0,
                            "total_bytes": bundle_bytes,
                            "upload_pct": 0,
                            "retry_count": 0,
                        },
                        report_summary="正在上传样本包到边端设备",
                    )
                remote_bundle = f"{remote_job}/bundle.tar.gz"
                stage_started = time.monotonic()
                _ensure_manual_execution_current(execution_monitor)
                client = edge_ssh_validation._upload_edge_bundle_with_reconnect(
                    client,
                    bundle,
                    remote_bundle,
                    reconnect=_reconnect_for_upload,
                    progress_callback=_persist_upload_progress,
                    retry_callback=_record_upload_retry,
                )
                summary["stage_durations_seconds"]["upload"] = round(
                    time.monotonic() - stage_started, 3
                )
                summary["upload_bytes"] = bundle_bytes
                summary["upload_retry_count"] = int(upload_state["retry_count"])
                with begin_transaction(db):
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="probe_demo",
                        message="样本包上传完成，正在探测 Sophon Demo 环境",
                        detail={"remote_bundle": remote_bundle},
                        report_summary="正在探测 Sophon Demo 环境",
                    )
                resolved_demo, probe_note = (
                    edge_ssh_validation.probe_sophon_demo_root_via_ssh(
                        client,
                        configured=sophon_root,
                        docker_container=docker_container,
                        docker_use_sudo=docker_use_sudo,
                        restrict_to_configured=True,
                    )
                )
                demo_env = (
                    resolved_demo
                    or sophon_root
                    or edge_ssh_validation.DEFAULT_SOPHON_DEMO_ROOT
                )
                cmd = edge_ssh_validation.build_edge_remote_validation_command(
                    remote_job=remote_job,
                    demo_env=demo_env,
                    docker_container=docker_container,
                    docker_use_sudo=docker_use_sudo,
                    model_cache_key=model_cache_key or "",
                    edge_root=paths.root,
                )
                with begin_transaction(db):
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="remote_run",
                        message="环境探测完成，正在边端执行验证脚本",
                        detail={
                            "remote_job_dir": remote_job,
                            "sophon_demo_root": demo_env,
                        },
                        report_summary="边端脚本执行中",
                    )
                stage_started = time.monotonic()
                _ensure_manual_execution_current(execution_monitor)
                with _manual_job_heartbeat(job_id, execution_monitor=execution_monitor):
                    code, out, err = edge_ssh_validation.ssh_run(
                        client,
                        cmd,
                        timeout=int(cfg.get("validation_timeout_sec") or 7200),
                    )
                summary["stage_durations_seconds"]["remote_run"] = round(
                    time.monotonic() - stage_started, 3
                )
                summary["remote_exit_code"] = code
                summary["remote_stdout_tail"] = out[-4000:]
                summary["remote_stderr_tail"] = err[-2000:]
                summary["sophon_demo_probe"] = probe_note
                summary["sophon_demo_root"] = demo_env

                with begin_transaction(db):
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="pull_output",
                        message="边端执行结束，正在回传 output 目录",
                        detail={"remote_job_dir": remote_job, "remote_exit_code": code},
                        report_summary="正在回传边端输出结果",
                    )
                local_out = _manual_output_root(job_id, int(export_row.id))
                stage_started = time.monotonic()
                _ensure_manual_execution_current(execution_monitor)
                with _manual_job_heartbeat(job_id, execution_monitor=execution_monitor):
                    pulled = _download_manual_edge_output(
                        client,
                        remote_job=remote_job,
                        local_out=local_out,
                        summary=summary,
                    )
                summary["stage_durations_seconds"]["pull_output"] = round(
                    time.monotonic() - stage_started, 3
                )
                summary["files_pulled"] = pulled
                if not (local_out / "board_report.json").is_file():
                    try:
                        edge_ssh_validation.sftp_download(
                            client,
                            f"{remote_job}/work/output/board_report.json",
                            local_out / "board_report.json",
                        )
                    except Exception:
                        pass
                if not (local_out / "bmodel_sop_eval.json").is_file():
                    try:
                        edge_ssh_validation.sftp_download(
                            client,
                            f"{remote_job}/work/output/bmodel_sop_eval.json",
                            local_out / "bmodel_sop_eval.json",
                        )
                    except Exception:
                        pass

                with begin_transaction(db):
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="ingest",
                        message="输出回传完成，正在解析结果文件",
                        detail={"files_pulled": pulled},
                        report_summary="正在解析边端验证结果",
                    )
                stage_started = time.monotonic()
                _ensure_manual_execution_current(execution_monitor)
                with _manual_job_heartbeat(job_id, execution_monitor=execution_monitor):
                    ingest = _ingest_manual_results(
                        job_id, int(export_row.id), local_out
                    )
                summary["stage_durations_seconds"]["ingest"] = round(
                    time.monotonic() - stage_started, 3
                )
                summary["ingest"] = ingest
                board_report = ingest.get("board_report") or {}
                _assert_edge_result_class_contract(
                    manifest.get("class_contract"),
                    board_report.get("class_contract"),
                )
                bmodel_sop = ingest.get("bmodel_sop") or {}
                runner_summary = ingest.get("runner_summary") or {}
                quality_gate = (
                    bmodel_sop.get("usability_gate")
                    if isinstance(bmodel_sop, dict)
                    else None
                )
                model_blocks = (
                    bmodel_sop.get("models")
                    if isinstance(bmodel_sop, dict)
                    and isinstance(bmodel_sop.get("models"), dict)
                    else {}
                )
                model_runs_completed = bool(model_blocks) and all(
                    isinstance(model_blocks.get(tag), dict)
                    and str(model_blocks[tag].get("status") or "").lower() == "ok"
                    for tag in ("fp32", "fp16", "int8")
                )
                gate_failed_after_execution = (
                    isinstance(quality_gate, dict)
                    and str(quality_gate.get("status") or "").lower() == "fail"
                    and model_runs_completed
                )
                warning = _plot_warning(bmodel_sop.get("stderr_tail"))
                if warning:
                    summary.setdefault("warnings", []).append(warning)
                if (
                    code == 0
                    and bmodel_sop.get("status") == "ok"
                    and runner_summary.get("passed") is not False
                ):
                    summary["status"] = "ok"
                elif (
                    code == 0
                    and row.mode == "run_only"
                    and (board_report.get("passed") or board_report.get("skipped"))
                ):
                    summary["status"] = "ok"
                elif gate_failed_after_execution:
                    # The device ran all three models and produced metrics; a
                    # failed quality gate is a completed check with a blocked
                    # release, not a transport/inference failure.
                    summary["status"] = "quality_gate_failed"
                    summary["quality_gate"] = quality_gate
                    if warning and _plot_warning(summary.get("error")):
                        summary["error"] = None
                else:
                    summary["status"] = "fail"
                    summary["error"] = (
                        summary.get("error")
                        or bmodel_sop.get("stderr_tail")
                        or edge_ssh_validation._edge_run_log_failure_hint(
                            _export_dir_for_row(int(export_row.id))
                        )
                        or f"边端脚本 exit={code}"
                    )

                if summary.get("status") == "ok" and row.mode == "run_and_eval":
                    with begin_transaction(db):
                        _update_manual_job_progress(
                            db,
                            row,
                            stage="pt_same_batch_eval",
                            message="正在使用同一批边端样本执行 PT 基准评估",
                            detail={
                                "image_count": int(manifest.get("copied_count") or 0),
                                "labeled_image_count": int(
                                    manifest.get("labeled_count") or 0
                                ),
                            },
                            report_summary="正在生成 PT 与各精度 BModel 同口径对比",
                        )
                    stage_started = time.monotonic()
                    _ensure_manual_execution_current(execution_monitor)
                    with _manual_job_heartbeat(
                        job_id, execution_monitor=execution_monitor
                    ):
                        pt_eval = _run_same_batch_pt_eval(
                            job_id=job_id,
                            export_id=int(export_row.id),
                            train_job=train_job,
                            manifest=manifest,
                        )
                    summary["stage_durations_seconds"]["pt_same_batch_eval"] = round(
                        time.monotonic() - stage_started, 3
                    )
                    comparison = _build_same_batch_pt_bmodel_comparison(
                        pt_eval=pt_eval,
                        ingest=ingest,
                    )
                    summary["pt_eval"] = pt_eval
                    summary["comparison_pt_bmodel"] = comparison
                    comparison_path = (
                        _manual_report_root(job_id, int(export_row.id))
                        / "same_batch_pt_bmodel_comparison.json"
                    )
                    _ensure_manual_execution_current(execution_monitor)
                    comparison_path.write_text(
                        json.dumps(comparison, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
            finally:
                try:
                    _ensure_manual_execution_current(execution_monitor)
                    edge_ssh_validation._pull_remote_run_log_if_present(
                        client,
                        remote_job=remote_job,
                        export_root=edge_validation_job_root(
                            export_id=int(export_row.id), job_id=job_id
                        ),
                    )
                except ExecutionFencingLost:
                    summary["remote_log_pull"] = "skipped_stale_lease"
                except Exception:
                    pass
                try:
                    _ensure_manual_execution_current(execution_monitor)
                    if summary.get("status") == "ok":
                        cleaned, cleanup_message = (
                            edge_ssh_validation._cleanup_remote_job_dir(
                                client,
                                remote_job=remote_job,
                                edge_root=paths.root,
                            )
                        )
                        summary["remote_cleanup"] = "done" if cleaned else "failed"
                        summary["remote_retention_hours"] = 0
                    else:
                        retention_hours = int(
                            getattr(
                                settings,
                                "EDGE_REMOTE_FAILED_JOB_RETENTION_HOURS",
                                24,
                            )
                            or 24
                        )
                        retained, cleanup_message = (
                            edge_ssh_validation._retain_failed_remote_job_dir(
                                client,
                                remote_job=remote_job,
                                retention_hours=retention_hours,
                                edge_root=paths.root,
                            )
                        )
                        summary["remote_cleanup"] = (
                            "retained_for_diagnosis"
                            if retained
                            else "retention_mark_failed"
                        )
                        summary["remote_retention_hours"] = retention_hours
                    if cleanup_message:
                        summary["remote_cleanup_message"] = cleanup_message
                except ExecutionFencingLost:
                    summary["remote_cleanup"] = "skipped_stale_lease"
                except Exception as cleanup_exc:
                    summary["remote_cleanup"] = "failed"
                    summary["remote_cleanup_message"] = str(cleanup_exc)[:1000]
                try:
                    client.close()
                except Exception:
                    pass

        summary.setdefault("stage_durations_seconds", {})["total"] = round(
            time.monotonic() - run_started, 3
        )
        with session_scope() as db:
            row = crud_yolo_edge_validation_job.get(db, job_id)
            if not row:
                return
            result_json = _job_result_json(row)
            result_summaries = _build_manual_result_summaries(
                summary=summary,
                ingest=summary.get("ingest"),
            )
            manual_analysis = _build_manual_analysis(
                job_id=job_id,
                export_id=int(row.export_id),
                ingest=summary.get("ingest"),
                pt_eval=summary.get("pt_eval"),
                comparison_pt_bmodel=summary.get("comparison_pt_bmodel"),
            )
            result_json["execution"] = _build_manual_execution_payload(
                job_id=job_id,
                export_id=int(row.export_id),
                summary=summary,
            )
            result_json["execution_summary"] = result_summaries["execution_summary"]
            result_json["prediction_summary"] = result_summaries["prediction_summary"]
            result_json["eval_summary"] = result_summaries["eval_summary"]
            result_json["eval_models"] = result_summaries["eval_models"]
            if result_summaries.get("quality_gate") is not None:
                result_json["quality_gate"] = result_summaries["quality_gate"]
            if result_summaries.get("warnings"):
                result_json["warnings"] = result_summaries["warnings"]
            result_json["plate_recognition_eval"] = result_summaries[
                "plate_recognition_eval"
            ]
            result_json["validation_dimensions"] = _build_manual_validation_dimensions(
                mode=str(row.mode or "run_and_eval"),
                manifest=manifest,
                summary=summary,
                ingest=summary.get("ingest"),
                result_summaries=result_summaries,
            )
            result_json["analysis"] = manual_analysis
            if isinstance(summary.get("pt_eval"), dict):
                result_json["pt_eval"] = summary["pt_eval"]
            if isinstance(summary.get("comparison_pt_bmodel"), dict):
                result_json["comparison_pt_bmodel"] = summary["comparison_pt_bmodel"]
            final_status = "FAILED"
            if summary.get("status") == "ok":
                final_status = (
                    "PARTIAL_COMPLETED" if row.mode == "run_only" else "COMPLETED"
                )
            elif summary.get("status") == "quality_gate_failed":
                final_status = "PARTIAL_COMPLETED"
            edge_device_registry.mark_device_validation(
                str(row.edge_device_id or ""),
                "ok" if final_status != "FAILED" else "fail",
            )
            comparison = summary.get("comparison_pt_bmodel")
            comparison = comparison if isinstance(comparison, dict) else {}
            analysis = result_json.get("analysis")
            analysis = analysis if isinstance(analysis, dict) else {}
            # Execution success and publish eligibility are separate outcomes.
            # A quick/diagnostic run can execute all three BModels correctly
            # while still lacking the accuracy or release evidence required by
            # the publish gate. Persist that distinction so the model page
            # never presents a completed check as an implicitly publishable
            # model.
            comparison_status = str(comparison.get("status") or "").lower()
            comparison_verdict = str(comparison.get("verdict") or "").lower()
            comparison_reason = (
                str(comparison.get("detail") or "").strip()
                or str(analysis.get("summary") or "").strip()
            )
            if comparison_status in {"warn", "fail"} or comparison_verdict in {
                "review",
                "fail",
            }:
                result_json["publish_gate"] = {
                    "status": (
                        "review"
                        if comparison_status == "warn" or comparison_verdict == "review"
                        else "blocked"
                    ),
                    "reason": comparison_reason
                    or "BModel 与 PT 的同口径精度比较未达到发布通过标准",
                    "comparison": comparison,
                }
            failure_reason = (
                str(comparison.get("detail") or "").strip()
                or str(analysis.get("summary") or "").strip()
                or str(summary.get("error") or "").strip()
            )
            if summary.get("status") == "quality_gate_failed":
                report_summary = "边端检查完成，质量门禁未通过"
                if failure_reason:
                    report_summary += f"：{failure_reason[:420]}"
            elif comparison_status == "warn" or comparison_verdict == "review":
                report_summary = "边端检查完成，存在精度提示（不阻断发布）"
                if comparison_reason:
                    report_summary += f"：{comparison_reason[:420]}"
            elif comparison_status == "fail" or comparison_verdict == "fail":
                report_summary = "边端检查完成，当前不可发布"
                if comparison_reason:
                    report_summary += f"：{comparison_reason[:420]}"
            else:
                report_summary = (
                    "边端验证执行完成"
                    if final_status != "FAILED"
                    else "边端验证执行失败"
                )
                if final_status == "FAILED" and failure_reason:
                    report_summary += f"：{failure_reason[:420]}"
            if failure_reason:
                result_json["failure_reason"] = failure_reason[:2000]
            final_updates = {
                "status": final_status,
                "error_message": (
                    summary.get("error")
                    or (failure_reason if final_status == "FAILED" else None)
                ),
                "completed_at": datetime.now(),
                "result_json": _serialize_job_result_json(result_json),
                "report_summary": report_summary,
            }
            if execution_monitor is not None:
                with begin_transaction(db):
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="finalize",
                        message=(
                            "边端验证已完成，结果已回填"
                            if final_status != "FAILED"
                            else "边端验证失败，结果已停止在当前步骤"
                        ),
                        detail={
                            "final_status": final_status,
                            "remote_exit_code": summary.get("remote_exit_code"),
                            "has_eval_summary": bool(result_summaries["eval_summary"]),
                        },
                        report_summary=report_summary,
                    )
                    execution_monitor.conditional_update(final_updates, db=db)
            else:
                with begin_transaction(db):
                    _update_manual_job_progress(
                        db,
                        row,
                        stage="finalize",
                        message=(
                            "边端验证已完成，结果已回填"
                            if final_status != "FAILED"
                            else "边端验证失败，结果已停止在当前步骤"
                        ),
                        detail={
                            "final_status": final_status,
                            "remote_exit_code": summary.get("remote_exit_code"),
                            "has_eval_summary": bool(result_summaries["eval_summary"]),
                        },
                        report_summary=report_summary,
                    )
                    crud_yolo_edge_validation_job.update(
                        db,
                        row,
                        **final_updates,
                    )
    except Exception as exc:
        logger.exception("manual edge validation job failed job_id={}", job_id)
        summary.setdefault("stage_durations_seconds", {})["total"] = round(
            time.monotonic() - run_started, 3
        )
        if device_id and not isinstance(exc, ExecutionFencingLost):
            try:
                from labelu.internal.common import edge_device_registry

                if isinstance(
                    exc,
                    (
                        edge_ssh_validation.EdgeSshError,
                        edge_runtime_provision.EdgeRuntimeProvisionError,
                    ),
                ):
                    environment_failed = isinstance(
                        exc, edge_runtime_provision.EdgeRuntimeProvisionError
                    )
                    if environment_failed:
                        from labelu.internal.application.service import (
                            edge_device_registry as edge_registry_service,
                        )

                        status_value, environment_code, environment_message = (
                            edge_registry_service._environment_exception_state(exc)
                        )
                        edge_registry_service._persist_environment_state(
                            device_id,
                            status_value=status_value,
                            stage=(
                                "manual_intervention"
                                if environment_code == "manual_cleanup_required"
                                else "environment_provision"
                            ),
                            code=environment_code,
                            message=environment_message,
                            report=getattr(exc, "report", None),
                        )
                    else:
                        edge_device_registry.update_device_record(
                            device_id,
                            probe_status="fail",
                            probe_stage="execution",
                            probe_code="ssh_runtime_failed",
                            probe_message=str(exc)[:800],
                            probe_checked_at=edge_device_registry._utc_now_iso(),
                        )
                edge_device_registry.mark_device_validation(device_id, "fail")
            except Exception:
                logger.exception(
                    "record assigned edge device failure failed device_id={}", device_id
                )
        with session_scope() as db:
            row = crud_yolo_edge_validation_job.get(db, job_id)
            if row is not None:
                if execution_monitor is not None:
                    row._execution_lease_monitor = execution_monitor
                result_json = _job_result_json(row)
                failed_summary = dict(summary)
                failed_summary.update(
                    status="fail",
                    error=str(exc)[:4000],
                )
                result_json["execution"] = _build_manual_execution_payload(
                    job_id=job_id,
                    export_id=int(row.export_id),
                    summary=failed_summary,
                )
                result_json["execution_summary"] = _build_manual_result_summaries(
                    summary=failed_summary,
                    ingest=None,
                )["execution_summary"]
                result_json["prediction_summary"] = None
                result_json["eval_summary"] = None
                result_json["eval_models"] = {}
                result_json["plate_recognition_eval"] = None
                result_json["analysis"] = None
                error_updates = {
                    "status": "FAILED",
                    "error_message": str(exc)[:4000],
                    "completed_at": datetime.now(),
                    "result_json": _serialize_job_result_json(result_json),
                    "report_summary": "边端验证任务执行失败",
                }
                try:
                    if execution_monitor is not None:
                        with begin_transaction(db):
                            _update_manual_job_progress(
                                db,
                                row,
                                stage="finalize",
                                message="边端验证异常退出",
                                detail={"error": str(exc)[:1000]},
                                report_summary="边端验证任务执行失败",
                            )
                            execution_monitor.conditional_update(error_updates, db=db)
                    else:
                        with begin_transaction(db):
                            _update_manual_job_progress(
                                db,
                                row,
                                stage="finalize",
                                message="边端验证异常退出",
                                detail={"error": str(exc)[:1000]},
                                report_summary="边端验证任务执行失败",
                            )
                            crud_yolo_edge_validation_job.update(
                                db,
                                row,
                                **error_updates,
                            )
                except (ExecutionFencingLost, ExecutionFencingUnavailable):
                    logger.warning(
                        "skip stale edge validation failure write job_id={}", job_id
                    )
    finally:
        lock.release()
        if execution_monitor is not None:
            execution_monitor.stop()
            try:
                from labelu.internal.common.execution_fencing import (
                    release_execution_lease,
                )

                with session_scope() as fence_db:
                    release_execution_lease(fence_db, execution_monitor.token)
            except Exception:
                logger.exception(
                    "release edge validation execution lease failed job_id={}",
                    job_id,
                )
        from labelu.internal.application.service import yolo_edge_validation_scheduler

        yolo_edge_validation_scheduler.schedule_edge_validation_dispatch()


def start_job(
    db: Session,
    *,
    job_id: int,
    current_user: User,
) -> YoloEdgeValidationJobDetailResponse:
    from labelu.internal.application.service import yolo_edge_validation_scheduler

    if not (
        yolo_edge_validation_scheduler.has_configured_edge_devices()
        or edge_ssh_validation._edge_ssh_configured()
    ):
        raise LabelUException(
            code=ErrorCode.CODE_57029_EDGE_SSH_VALIDATION_DISABLED,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="请先至少完成一台已启用边端设备的 SSH 配置",
        )
    row, task = _load_job_with_access(db, job_id=job_id, current_user=current_user)
    if row.status == "PROCESSING" and not is_stale_edge_validation_job(row):
        raise LabelUException(
            code=ErrorCode.CODE_57032_EDGE_SSH_VALIDATION_BUSY,
            status_code=status.HTTP_409_CONFLICT,
            message="该边端验证任务正在执行中，请稍后刷新",
        )

    preserve_device = row.status == "PROCESSING" and is_stale_edge_validation_job(row)
    yolo_edge_validation_scheduler.enqueue_edge_validation_job(
        int(row.id),
        preserve_assigned_device=preserve_device,
    )
    # A stale row can already carry a Board Agent assignment.  Do not launch
    # the historical SSH runner in that case; the pull endpoint remains the
    # sole consumer.  For newly queued rows there is no device id yet, so the
    # runner may still be started for compatibility, but the guard inside
    # ``_run_manual_job_sync`` repeats the check after scheduler assignment.
    if not _registered_board_agent_device(getattr(row, "edge_device_id", None)):
        threading.Thread(
            target=_run_manual_job_sync,
            args=(int(row.id),),
            name=f"yolo-edge-validation-request-{row.id}",
            daemon=True,
        ).start()
    db.expire(row)
    db.refresh(row)
    positions = _canonical_queue_positions(db, [row], current_user)
    return _to_detail_response(
        row,
        task.name,
        db=db,
        current_user=current_user,
        queue_position=positions.get(int(row.id)),
    )
