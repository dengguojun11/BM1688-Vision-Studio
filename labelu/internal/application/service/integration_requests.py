from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import uuid4

from fastapi import status
from loguru import logger
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from labelu.internal.application.command.integration_requests import (
    ApprovePublishCommand,
    BindPackagesCommand,
    CreateOptimizationRequestCommand,
    ExternalImagePayload,
    PublishDecisionUpsertCommand,
)
from labelu.internal.application.command.strategy_evolution import (
    CreateStrategyEvolutionTaskCommand,
    StrategyEvolutionDataWindowPayload,
    StrategyEvolutionTargetPayload,
)
from labelu.internal.application.service import strategy_evolution
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.experiment_dataset_import import ExperimentDatasetImport
from labelu.internal.domain.models.published_model_catalog import (
    PublishedModelCatalog,
    PublishedModelIdentifierAlias,
)
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.strategy_task_integration import StrategyTaskIntegration
from labelu.internal.domain.models.third_party_call_audit import ThirdPartyCallAudit
from labelu.internal.domain.models.third_party_optimization import (
    IntegrationPublishDecision,
    OptimizationCycle,
    OptimizationRequest,
    ThirdPartyEventImage,
)
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.workflow import (
    WorkflowEvent,
    WorkflowInstance,
    WorkflowOutbox,
)

REQUEST_TYPES = {"model_optimization", "threshold_experiment", "scene_training"}
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
TERMINAL_IMPORT_STATUSES = {"COMPLETED", "PARTIAL_COMPLETED", "FAILED"}

STAGE_LABELS = {
    "RECEIVED": "已接收",
    "DATA_COLLECTING": "数据累积中",
    "WAITING_FOR_SAMPLES": "等待更多样本",
    "WAITING_FOR_MODEL": "等待模型映射",
    "WAITING_FOR_DATA": "等待数据补齐",
    "DATA_READY": "数据就绪",
    "DATASET_CREATED": "任务数据集已创建",
    "PRELABELING": "自动标注中",
    "WAITING_REVIEW": "人工审核中",
    "TRAINING": "模型训练中",
    "EXPORTING": "模型编译中",
    "EDGE_VALIDATING": "边端验证中",
    "WAITING_PUBLISH_DECISION": "等待发布决策",
    "PUBLISHING": "发布中",
    "AUTO_PUBLISHED": "自动发布完成",
    "WAITING_MANUAL_PUBLISH": "等待人工发布",
    "COMPLETED": "已完成",
    "FAILED": "失败",
    "CANCELLED": "已取消",
    # 回调派发事件使用的内部 step_key，避免时间线里露出 deliver。
    "deliver": "第三方回调派发",
}
# 执行时间线的事件类型同样必须中文化，避免详情里出现 request.stage_changed
# 这类内部英文标识。
EVENT_LABELS = {
    "request.created": "请求已创建",
    "request.stage_changed": "阶段变更",
    "request.strategy_stage_changed": "策略阶段变更",
    "request.package_auto_bound": "数据包自动绑定",
    "request.pipeline_started": "流水线已启动",
    "request.pipeline_rejected": "流水线已拒绝",
    "request.cancelled": "请求已取消",
    "request.publish_decision": "发布决策",
    "request.publish_approved": "发布已批准",
    "request.publish_started": "发布已开始",
    "request.publish_finished": "发布已完成",
    "7gai.workflow.callback.dispatch.v1": "第三方回调派发",
    "7gai.workflow.callback.succeeded.v1": "第三方回调成功",
    "7gai.workflow.callback.failed.v1": "第三方回调失败",
}


def event_type_label(event_type: str) -> str:
    """Return the operator-facing Chinese label for a workflow event type."""

    normalized = str(event_type or "").strip()
    label = EVENT_LABELS.get(normalized)
    if label:
        return label
    if normalized.startswith("request."):
        return "请求状态更新"
    return normalized or "状态更新"


def stage_label(stage: str) -> str:
    """Return the operator-facing Chinese label for a workflow step key."""

    normalized = str(stage or "").strip()
    return STAGE_LABELS.get(normalized, normalized or "未知阶段")


STATUS_LABELS = {
    "PENDING": "已受理",
    "RUNNING": "执行中",
    "WAITING": "等待中",
    "SUCCEEDED": "已完成",
    "FAILED": "失败",
    "CANCELLED": "已取消",
}


def _now() -> datetime:
    return datetime.now()


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _request_package_ids(request: OptimizationRequest) -> list[str]:
    value = _loads(request.package_ids_json, [])
    return [str(item) for item in value] if isinstance(value, list) else []


def _import_by_package(db: Session, package_id: str) -> ExperimentDatasetImport | None:
    return (
        db.query(ExperimentDatasetImport)
        .filter(
            ExperimentDatasetImport.package_id == package_id,
            ExperimentDatasetImport.deleted_at.is_(None),
        )
        .order_by(ExperimentDatasetImport.id.desc())
        .first()
    )


def _formal_task_ids(import_row: ExperimentDatasetImport | None) -> list[int]:
    if import_row is None:
        return []
    raw = _loads(import_row.formal_task_ids_json, [])
    if not isinstance(raw, list):
        return []
    result: list[int] = []
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in result:
            result.append(value)
    return result


def _image_is_available(image: ThirdPartyEventImage, import_row: ExperimentDatasetImport | None) -> bool:
    if image.task_id or image.sample_id:
        return True
    if import_row is not None:
        return str(import_row.status or "").upper() in TERMINAL_IMPORT_STATUSES
    return False


def _scene_matches(image_scene: dict[str, Any], requested_scene: dict[str, Any]) -> bool:
    if not requested_scene:
        return True
    if not isinstance(image_scene, dict):
        return False
    for key, expected in requested_scene.items():
        if key not in image_scene:
            return False
        actual = image_scene.get(key)
        if isinstance(expected, str) and isinstance(actual, str):
            if actual.strip().lower() != expected.strip().lower():
                return False
        elif actual != expected:
            return False
    return True


def _coverage(db: Session, request: OptimizationRequest) -> dict[str, Any]:
    images = (
        db.query(ThirdPartyEventImage)
        .filter(ThirdPartyEventImage.request_id == request.id)
        .order_by(ThirdPartyEventImage.id.asc())
        .all()
    )
    scene_filter = _loads(request.scene_json, {}) or {}
    if request.request_type == "scene_training" and scene_filter:
        images = [
            image
            for image in images
            if _scene_matches(_loads(image.scene_json, {}) or {}, scene_filter)
        ]
    if request.request_type == "threshold_experiment" and (
        request.event_time_from is not None or request.event_time_to is not None
    ):
        window_from = _naive_datetime(request.event_time_from)
        window_to = _naive_datetime(request.event_time_to)
        images = [
            image
            for image in images
            if image.event_time is None
            or (
                (window_from is None or _naive_datetime(image.event_time) >= window_from)
                and (window_to is None or _naive_datetime(image.event_time) <= window_to)
            )
        ]
    packages = _request_package_ids(request)
    package_rows = {
        package_id: _import_by_package(db, package_id) for package_id in packages
    }
    task_ids: list[int] = []
    available = 0
    pending = 0
    unknown = 0
    available_times: list[datetime] = []
    pending_packages: list[dict[str, Any]] = []
    for image in images:
        import_row = package_rows.get(image.package_id or "")
        is_available = _image_is_available(image, import_row)
        if is_available:
            available += 1
            if image.event_time is not None:
                available_times.append(image.event_time)
            if image.task_id and image.task_id not in task_ids:
                task_ids.append(image.task_id)
        elif image.package_id and import_row is not None:
            pending += 1
        elif image.task_id or image.sample_id:
            available += 1
        else:
            unknown += 1
        if image.task_id and image.task_id not in task_ids:
            task_ids.append(image.task_id)

    package_declared = 0
    package_valid = 0
    for package_id, import_row in package_rows.items():
        if import_row is None:
            pending_packages.append({"package_id": package_id, "status": "NOT_FOUND"})
            continue
        package_declared += int(import_row.declared_sample_count or 0)
        package_valid += int(import_row.valid_sample_count or 0)
        for task_id in _formal_task_ids(import_row):
            if task_id not in task_ids:
                task_ids.append(task_id)
        status_value = str(import_row.status or "").upper()
        if status_value not in TERMINAL_IMPORT_STATUSES:
            pending_packages.append(
                {
                    "package_id": package_id,
                    "status": status_value or "PENDING",
                    "declared": int(import_row.declared_sample_count or 0),
                    "valid": int(import_row.valid_sample_count or 0),
                }
            )

    if images:
        requested = len(images)
    else:
        requested = max(package_declared, 0)

    if images:
        complete = requested > 0 and pending == 0 and unknown == 0
    else:
        complete = (
            bool(package_rows)
            and requested > 0
            and all(row is not None for row in package_rows.values())
            and all(
                str((row.status if row else "") or "").upper()
                in TERMINAL_IMPORT_STATUSES
                for row in package_rows.values()
            )
        )

    if not images and package_valid > available:
        # No per-image rows: use package-level counts as a fallback so a
        # package-bound request can still report progress.
        available = min(package_valid, requested) if requested else package_valid
        pending = max(0, requested - available)

    complete_windows: list[list[str]] = []
    if complete:
        if request.event_time_from and request.event_time_to:
            complete_windows.append(
                [_iso(request.event_time_from), _iso(request.event_time_to)]  # type: ignore[list-item]
            )
        elif available_times:
            complete_windows.append([_iso(min(available_times)), _iso(max(available_times))])  # type: ignore[list-item]

    watermark = request.event_time_to if complete else (max(available_times) if available_times else None)

    return {
        "requested": requested,
        "available": available,
        "pending": pending,
        "unknown": unknown,
        "complete": complete,
        "coverage_ratio": round(available / requested, 4) if requested else 0.0,
        "watermark": _iso(watermark),
        "complete_windows": complete_windows,
        "task_ids": task_ids,
        "pending_packages": pending_packages[:50],
        "package_ids": packages,
        "partial": bool(request.allow_partial and not complete),
    }


def _progress(coverage: dict[str, Any], request: OptimizationRequest) -> dict[str, Any]:
    requested = int(coverage.get("requested") or 0)
    available = int(coverage.get("available") or 0)
    percent = round((available / requested) * 100, 1) if requested else 0.0
    return {
        "processed": available,
        "total": requested,
        "percent": percent,
        "message": (
            f"已就绪 {available}/{requested} 张事件图片"
            if requested
            else "等待第三方提交图片或数据包"
        ),
    }


def _refresh_request_state(db: Session, request: OptimizationRequest, actor_id: int | None = None) -> dict[str, Any]:
    if request.status in TERMINAL_STATUSES:
        return _coverage(db, request)
    coverage = _coverage(db, request)
    # Once the existing strategy pipeline owns the stage, only sync it from the
    # strategy row.  Recomputing from coverage here would oscillate between
    # DATA_READY/DATASET_CREATED and TRAINING/WAITING_FOR_MODEL every worker
    # tick, producing duplicate stage callbacks.
    if request.strategy_external_task_id:
        request.coverage_json = _dumps(coverage)
        request.progress_json = _dumps(_progress(coverage, request))
        return coverage
    previous_status = request.status
    previous_stage = request.stage
    requested = int(coverage.get("requested") or 0)
    available = int(coverage.get("available") or 0)
    pending = int(coverage.get("pending") or 0)
    unknown = int(coverage.get("unknown") or 0)
    min_count = int(request.min_sample_count or 1)
    complete = bool(coverage.get("complete"))
    partial_allowed = bool(request.allow_partial and request.request_type == "threshold_experiment")

    if requested == 0:
        request.status = "WAITING"
        request.stage = "DATA_COLLECTING"
    elif request.request_type == "threshold_experiment":
        if pending > 0 or unknown > 0:
            if partial_allowed and available > 0:
                request.status = "RUNNING"
                request.stage = "DATA_READY"
                coverage["partial"] = True
            else:
                request.status = "WAITING"
                request.stage = "WAITING_FOR_DATA"
        elif available > 0:
            request.status = "RUNNING"
            request.stage = "DATA_READY"
        else:
            request.status = "WAITING"
            request.stage = "WAITING_FOR_SAMPLES"
    elif available < min_count:
        request.status = "WAITING"
        request.stage = "WAITING_FOR_SAMPLES" if pending == 0 else "WAITING_FOR_DATA"
    elif not complete and request.require_complete:
        request.status = "WAITING"
        request.stage = "WAITING_FOR_DATA"
    else:
        request.status = "RUNNING"
        request.stage = "DATASET_CREATED" if coverage.get("task_ids") else "DATA_READY"

    request.coverage_json = _dumps(coverage)
    request.progress_json = _dumps(_progress(coverage, request))
    if request.stage != previous_stage or request.status != previous_status:
        _append_workflow_event(
            db,
            request,
            event_type="request.stage_changed",
            from_status=previous_status,
            to_status=request.status,
            payload={
                "stage": request.stage,
                "stage_label": STAGE_LABELS.get(request.stage, request.stage),
                "coverage": coverage,
            },
            actor_id=actor_id,
        )
    return coverage


def _append_workflow_event(
    db: Session,
    request: OptimizationRequest,
    *,
    event_type: str,
    from_status: str | None = None,
    to_status: str | None = None,
    payload: dict[str, Any] | None = None,
    actor_id: int | None = None,
) -> None:
    if not request.workflow_id:
        return
    db.add(
        WorkflowEvent(
            workflow_id=request.workflow_id,
            event_type=event_type,
            from_status=from_status,
            to_status=to_status,
            step_key=request.stage,
            payload_json=_dumps(payload or {}),
            actor_id=actor_id,
        )
    )
    workflow = db.get(WorkflowInstance, request.workflow_id)
    if workflow is not None:
        workflow.status = request.status
        workflow.current_step = request.stage
        workflow.updated_at = _now()
    if request.callback_url and event_type not in {
        "request.pipeline_rejected",
        "request.pipeline_started",
    }:
        _enqueue_request_callback(db, request, event_type)


def _enqueue_request_callback(
    db: Session, request: OptimizationRequest, event_type: str
) -> None:
    """Persist a v2 callback into the shared workflow outbox (no new queue)."""
    if not request.callback_url or not request.workflow_id:
        return
    event_id = f"optreq-{request.id}-{uuid4().hex[:12]}"
    request.callback_event_id = event_id
    summary = {
        "schema_version": "v2",
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": _iso(_now()),
        "is_final": request.status in TERMINAL_STATUSES,
        "request_id": str(request.id),
        "external_request_id": request.external_request_id,
        "request_type": request.request_type,
        "external_model_id": request.external_model_id,
        "event_label": request.event_label,
        "status": request.status,
        "stage": request.stage,
        "stage_label": STAGE_LABELS.get(request.stage, request.stage),
        "progress": _loads(request.progress_json, {}),
        "coverage": _loads(request.coverage_json, {}),
        "result": _loads(request.result_json, None),
        "error": _loads(request.error_json, None),
        "links": {
            "status_url": f"/api/v2/integrations/requests/{request.id}",
            "coverage_url": f"/api/v2/integrations/requests/{request.id}/coverage",
            "timeline_url": f"/api/v2/integrations/requests/{request.id}/timeline",
        },
    }
    headers = {
        "Content-Type": "application/json",
        "X-LabelU-Event-Id": event_id,
    }
    secret = str(
        getattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "") or ""
    ).strip()
    if secret:
        timestamp = str(int(time.time()))
        body = json.dumps(
            summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        signed_content = b".".join(
            (timestamp.encode("utf-8"), event_id.encode("utf-8"), body)
        )
        signature = hmac.new(
            secret.encode("utf-8"), signed_content, hashlib.sha256
        ).hexdigest()
        headers["X-LabelU-Timestamp"] = timestamp
        headers["X-LabelU-Signature"] = f"sha256={signature}"
    db.add(
        WorkflowOutbox(
            workflow_id=request.workflow_id,
            event_type="7gai.workflow.callback.dispatch.v1",
            aggregate_type="optimization_request",
            aggregate_id=str(request.id),
            idempotency_key=f"optreq-callback:{request.id}:{event_id}",
            payload_json=_dumps(
                {
                    "callback_url": request.callback_url,
                    "callback_event_id": event_id,
                    "headers": headers,
                    "payload": summary,
                }
            ),
            status="PENDING",
            available_at=_now(),
        )
    )



def _ensure_workflow(db: Session, request: OptimizationRequest, actor_id: int | None) -> None:
    if request.workflow_id:
        return
    workflow = WorkflowInstance(
        scope_key=f"integration:{request.source_system}",
        workflow_type=(
            "threshold_experiment"
            if request.request_type == "threshold_experiment"
            else "model_optimization"
        ),
        status=request.status,
        idempotency_key=f"optreq:{request.source_system}:{request.external_request_id}",
        payload_json=_dumps(
            {
                "request_id": request.id,
                "external_request_id": request.external_request_id,
                "request_type": request.request_type,
            }
        ),
        current_step=request.stage,
        created_by=int(actor_id or 1),
        owner_id=int(actor_id) if actor_id else None,
    )
    db.add(workflow)
    db.flush()
    request.workflow_id = int(workflow.id)


def _ensure_cycle(db: Session, request: OptimizationRequest) -> OptimizationCycle:
    cycle = (
        db.query(OptimizationCycle)
        .filter(OptimizationCycle.request_id == request.id)
        .order_by(OptimizationCycle.id.asc())
        .first()
    )
    if cycle is not None:
        return cycle
    cycle = OptimizationCycle(
        request_id=request.id,
        aggregation_key=f"optreq:{request.id}",
        model_id=request.external_model_id,
        event_label=request.event_label,
        scene_json=request.scene_json,
        min_sample_count=request.min_sample_count,
        status="COLLECTING",
    )
    db.add(cycle)
    db.flush()
    return cycle


def _bind_images(
    db: Session,
    request: OptimizationRequest,
    cycle: OptimizationCycle,
    images: Iterable[ExternalImagePayload],
) -> None:
    existing = {
        row.external_image_id: row
        for row in db.query(ThirdPartyEventImage)
        .filter(ThirdPartyEventImage.request_id == request.id)
        .all()
    }
    for image in images:
        external_id = str(image.external_image_id).strip()
        if not external_id:
            continue
        row = existing.get(external_id)
        if row is None:
            row = ThirdPartyEventImage(
                request_id=request.id,
                cycle_id=cycle.id,
                source_system=request.source_system,
                package_id=image.package_id,
                external_image_id=external_id,
                external_event_id=image.external_event_id,
                task_id=image.task_id,
                sample_id=image.sample_id,
                event_label=image.event_label or request.event_label,
                event_time=image.event_time or request.event_time_from,
                scene_json=_dumps(image.scene) if image.scene else request.scene_json,
            )
            db.add(row)
            existing[external_id] = row
        else:
            if image.package_id:
                row.package_id = image.package_id
            if image.task_id:
                row.task_id = image.task_id
            if image.sample_id:
                row.sample_id = image.sample_id
            if image.event_time:
                row.event_time = image.event_time
            if image.external_event_id:
                row.external_event_id = image.external_event_id
    db.flush()


def _bind_packages(
    db: Session,
    request: OptimizationRequest,
    cycle: OptimizationCycle,
    package_ids: list[str],
) -> None:
    current = _request_package_ids(request)
    for package_id in package_ids:
        if package_id not in current:
            current.append(package_id)
    request.package_ids_json = _dumps(current)
    import_rows: list[ExperimentDatasetImport] = []
    for package_id in current:
        row = _import_by_package(db, package_id)
        if row is not None:
            import_rows.append(row)
    if import_rows:
        group_ids = {
            int(row.aggregation_group_id)
            for row in import_rows
            if row.aggregation_group_id is not None
        }
        if len(group_ids) == 1:
            cycle.aggregation_group_id = next(iter(group_ids))
        cycle.declared_sample_count = sum(int(row.declared_sample_count or 0) for row in import_rows)
        cycle.valid_sample_count = sum(int(row.valid_sample_count or 0) for row in import_rows)
        for row in import_rows:
            _sync_event_images_from_import(db, request, cycle, row)


def _sync_event_images_from_import(
    db: Session,
    request: OptimizationRequest,
    cycle: OptimizationCycle,
    import_row: ExperimentDatasetImport,
) -> int:
    """Map metadata samples to external image rows without creating a task pipeline.

    Imports already own the authoritative image/task creation.  This projection
    only adds the external IDs needed by R2 (query by image) and by coverage.
    """
    metadata = _loads(import_row.metadata_json, {}) or {}
    raw_samples = metadata.get("samples")
    if isinstance(raw_samples, dict):
        samples: list[dict[str, Any]] = []
        for key, value in raw_samples.items():
            if not isinstance(value, dict):
                continue
            item = dict(value)
            image = item.get("image") if isinstance(item.get("image"), dict) else {}
            if not image.get("file_name"):
                image = dict(image)
                image["file_name"] = str(key)
                item["image"] = image
            samples.append(item)
    elif isinstance(raw_samples, list):
        samples = [item for item in raw_samples if isinstance(item, dict)]
    else:
        samples = []
    if not samples:
        return 0
    existing = {
        str(value)
        for (value,) in db.query(ThirdPartyEventImage.external_image_id)
        .filter(ThirdPartyEventImage.request_id == request.id)
        .all()
    }
    created = 0
    for sample in samples[:10000]:
        image = sample.get("image") if isinstance(sample.get("image"), dict) else {}
        external_id = str(
            sample.get("external_image_id")
            or sample.get("image_id")
            or sample.get("id")
            or image.get("file_name")
            or ""
        ).strip()
        if not external_id or external_id in existing:
            continue
        event_time = _parse_metadata_time(
            sample.get("event_time")
            or sample.get("reviewed_at")
            or sample.get("created_at")
        )
        scene = sample.get("scene")
        if not isinstance(scene, dict):
            scene = {}
        event_label = str(
            sample.get("event_label")
            or (sample.get("target") if isinstance(sample.get("target"), dict) else {}).get("event_type")
            or request.event_label
            or ""
        ).strip()
        db.add(
            ThirdPartyEventImage(
                request_id=request.id,
                cycle_id=cycle.id,
                source_system=request.source_system,
                package_id=str(import_row.package_id or ""),
                external_image_id=external_id,
                external_event_id=str(sample.get("external_event_id") or sample.get("event_id") or "") or None,
                event_label=event_label or None,
                event_time=event_time,
                scene_json=_dumps(scene) if scene else None,
            )
        )
        existing.add(external_id)
        created += 1
    if created:
        db.flush()
    return created


def _parse_metadata_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _naive_datetime(parsed)


def _workflow_events(db: Session, request: OptimizationRequest) -> list[dict[str, Any]]:
    if not request.workflow_id:
        return []
    rows = (
        db.query(WorkflowEvent)
        .filter(WorkflowEvent.workflow_id == request.workflow_id)
        .order_by(WorkflowEvent.id.asc())
        .all()
    )
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "event_type_label": event_type_label(row.event_type),
            "from_status": row.from_status,
            "to_status": row.to_status,
            "stage": row.step_key,
            "stage_label": stage_label(row.step_key),
            "payload": _loads(row.payload_json, {}),
            "actor_id": row.actor_id,
            "created_at": _iso(row.created_at),
        }
        for row in rows
    ]


def _review_stats(db: Session, task_ids: list[Any]) -> dict[str, Any]:
    ids: list[int] = []
    for value in task_ids:
        try:
            task_id = int(value)
        except (TypeError, ValueError):
            continue
        if task_id > 0 and task_id not in ids:
            ids.append(task_id)
    if not ids:
        return {"total": 0, "done": 0, "pending": 0, "state_counts": {}}
    rows = (
        db.query(TaskSample.state, func.count(TaskSample.id))
        .filter(
            TaskSample.task_id.in_(ids),
            TaskSample.deleted_at.is_(None),
        )
        .group_by(TaskSample.state)
        .all()
    )
    state_counts = {str(state or "NEW"): int(count) for state, count in rows}
    total = sum(state_counts.values())
    done = state_counts.get("DONE", 0) + state_counts.get("SKIPPED", 0)
    return {
        "total": total,
        "done": done,
        "pending": max(0, total - done),
        "state_counts": state_counts,
        "task_ids": ids,
    }


def _resolve_published_models(
    db: Session, model_ids: Iterable[str]
) -> dict[str, PublishedModelCatalog]:
    """Resolve third-party model identifiers against the published catalog.

    The lookup is deliberately batched (one query per identifier kind) so a
    request list poll never degrades into per-row catalog lookups.
    """

    texts = sorted(
        {str(value or "").strip() for value in model_ids if str(value or "").strip()}
    )
    if not texts:
        return {}
    resolved: dict[str, PublishedModelCatalog] = {}
    rows = (
        db.query(PublishedModelCatalog)
        .filter(
            PublishedModelCatalog.public_model_id.in_(texts),
            PublishedModelCatalog.deleted_at.is_(None),
        )
        .order_by(PublishedModelCatalog.id.desc())
        .limit(2000)
        .all()
    )
    for row in rows:
        resolved.setdefault(str(row.public_model_id or ""), row)
    numeric_ids = sorted(
        {int(text) for text in texts if text.isdigit() and int(text) > 0}
    )
    if numeric_ids:
        rows = (
            db.query(PublishedModelCatalog)
            .filter(
                PublishedModelCatalog.aicm_model_set_id.in_(numeric_ids),
                PublishedModelCatalog.deleted_at.is_(None),
            )
            .order_by(PublishedModelCatalog.id.desc())
            .limit(2000)
            .all()
        )
        for row in rows:
            resolved.setdefault(str(row.aicm_model_set_id or ""), row)
    alias_rows = (
        db.query(PublishedModelIdentifierAlias, PublishedModelCatalog)
        .join(
            PublishedModelCatalog,
            PublishedModelIdentifierAlias.catalog_id == PublishedModelCatalog.id,
        )
        .filter(
            PublishedModelIdentifierAlias.external_model_id.in_(texts),
            PublishedModelCatalog.deleted_at.is_(None),
        )
        .order_by(PublishedModelCatalog.id.desc())
        .limit(2000)
        .all()
    )
    for alias, catalog in alias_rows:
        resolved.setdefault(str(alias.external_model_id or ""), catalog)
    return resolved


def _model_display(
    request: OptimizationRequest, catalog: PublishedModelCatalog | None
) -> dict[str, Any]:
    raw = str(request.external_model_id or "").strip()
    display_name = ""
    if catalog is not None:
        display_name = str(
            getattr(catalog, "display_name", "") or getattr(catalog, "name", "") or ""
        ).strip()
    return {
        "external_model_id": raw or None,
        "display_name": display_name or None,
        "catalog_id": (
            int(catalog.id)
            if catalog is not None and catalog.id is not None
            else None
        ),
        "public_model_id": (
            str(catalog.public_model_id or "") if catalog is not None else None
        ),
        "resolved": catalog is not None,
    }


def _scene_items(scene: Any) -> list[dict[str, Any]]:
    if not isinstance(scene, dict):
        return []
    return [{"key": str(key), "value": value} for key, value in scene.items()]


def _display_context(
    db: Session, requests: Iterable[OptimizationRequest]
) -> dict[int, dict[str, Any]]:
    """Build list/detail display fields in a fixed number of queries."""

    request_list = [request for request in requests if request is not None]
    if not request_list:
        return {}
    strategy_ids: set[str] = set()
    model_ids: set[str] = set()
    for request in request_list:
        strategy_id = str(
            request.strategy_external_task_id or request.external_request_id or ""
        ).strip()
        if strategy_id:
            strategy_ids.add(strategy_id)
        model_id = str(request.external_model_id or "").strip()
        if model_id:
            model_ids.add(model_id)
    strategies: dict[str, StrategyTaskIntegration] = {}
    if strategy_ids:
        rows = (
            db.query(StrategyTaskIntegration)
            .filter(
                StrategyTaskIntegration.external_task_id.in_(sorted(strategy_ids)),
                StrategyTaskIntegration.deleted_at.is_(None),
            )
            .all()
        )
        for row in rows:
            strategies.setdefault(str(row.external_task_id or ""), row)
    catalogs = _resolve_published_models(db, model_ids)
    context: dict[int, dict[str, Any]] = {}
    for request in request_list:
        strategy_id = str(
            request.strategy_external_task_id or request.external_request_id or ""
        ).strip()
        strategy = strategies.get(strategy_id)
        model_id = str(request.external_model_id or "").strip()
        context[int(request.id)] = {
            "request_title": (
                str(strategy.task_name or "").strip() if strategy is not None else ""
            )
            or request.external_request_id,
            "external_model_display": _model_display(request, catalogs.get(model_id)),
            "scene_items": _scene_items(_loads(request.scene_json, {})),
        }
    return context


def _request_dict(
    db: Session,
    request: OptimizationRequest,
    *,
    include_images: bool = False,
    include_timeline: bool = False,
    display: dict[str, Any] | None = None,
) -> dict[str, Any]:
    coverage = _loads(request.coverage_json, {}) or _coverage(db, request)
    review = _review_stats(db, coverage.get("task_ids") or [])
    if display is None:
        display = _display_context(db, [request]).get(int(request.id), {})
    cycle = (
        db.query(OptimizationCycle)
        .filter(OptimizationCycle.request_id == request.id)
        .order_by(OptimizationCycle.id.asc())
        .first()
    )
    images: list[dict[str, Any]] = []
    if include_images:
        rows = (
            db.query(ThirdPartyEventImage)
            .filter(ThirdPartyEventImage.request_id == request.id)
            .order_by(ThirdPartyEventImage.id.asc())
            .limit(500)
            .all()
        )
        images = [
            {
                "external_image_id": row.external_image_id,
                "external_event_id": row.external_event_id,
                "package_id": row.package_id,
                "task_id": row.task_id,
                "sample_id": row.sample_id,
                "event_label": row.event_label,
                "event_time": _iso(row.event_time),
                "scene": _loads(row.scene_json, {}),
            }
            for row in rows
        ]
    decision = (
        db.query(IntegrationPublishDecision)
        .filter(IntegrationPublishDecision.request_id == request.id)
        .first()
    )
    return {
        "id": request.id,
        "request_id": str(request.id),
        "external_request_id": request.external_request_id,
        "source_system": request.source_system,
        "client_id": request.client_id,
        "request_type": request.request_type,
        "external_model_id": request.external_model_id,
        "labelu_model_id": request.labelu_model_id,
        "export_id": request.export_id,
        "event_label": request.event_label,
        "request_title": display.get("request_title") or request.external_request_id,
        "external_model_display": display.get("external_model_display")
        or _model_display(request, None),
        "scene_items": display.get("scene_items")
        or _scene_items(_loads(request.scene_json, {})),
        "scene": _loads(request.scene_json, {}),
        "event_time_from": _iso(request.event_time_from),
        "event_time_to": _iso(request.event_time_to),
        "require_complete": request.require_complete,
        "allow_partial": request.allow_partial,
        "min_sample_count": request.min_sample_count,
        "status": request.status,
        "status_label": STATUS_LABELS.get(request.status, request.status),
        "stage": request.stage,
        "stage_label": STAGE_LABELS.get(request.stage, request.stage),
        "progress": _loads(request.progress_json, {}) or _progress(coverage, request),
        "coverage": coverage,
        "review": review,
        "result": _loads(request.result_json, None),
        "error": _loads(request.error_json, None),
        "callback_url": request.callback_url,
        "callback_event_id": request.callback_event_id,
        "strategy_external_task_id": request.strategy_external_task_id,
        "workflow_id": request.workflow_id,
        "package_ids": _request_package_ids(request),
        "cycle": {
            "id": cycle.id,
            "status": cycle.status,
            "aggregation_group_id": cycle.aggregation_group_id,
            "declared_sample_count": cycle.declared_sample_count,
            "valid_sample_count": cycle.valid_sample_count,
            "task_ids": _loads(cycle.task_ids_json, []),
            "dataset_version_id": cycle.dataset_version_id,
            "started_at": _iso(cycle.started_at),
            "frozen_at": _iso(cycle.frozen_at),
            "finished_at": _iso(cycle.finished_at),
        }
        if cycle
        else None,
        "publish_decision": (
            {
                "decision": decision.decision,
                "auto_publish_eligible": decision.auto_publish_eligible,
                "reason": decision.reason,
                "candidate_model_id": decision.candidate_model_id,
                "baseline_model_id": decision.baseline_model_id,
                "candidate_export_id": decision.candidate_export_id,
                "metrics": _loads(decision.metrics_json, {}),
                "evidence": _loads(decision.evidence_json, {}),
                "decided_by": decision.decided_by,
                "decided_at": _iso(decision.decided_at),
                "published_model_id": decision.published_model_id,
                "aicm_model_set_id": decision.aicm_model_set_id,
            }
            if decision
            else None
        ),
        "images": images if include_images else None,
        "timeline": _workflow_events(db, request) if include_timeline else None,
        "deadline_at": _iso(request.deadline_at),
        "finished_at": _iso(request.finished_at),
        "created_at": _iso(request.created_at),
        "updated_at": _iso(request.updated_at),
        "links": {
            "status_url": f"/api/v2/integrations/requests/{request.id}",
            "coverage_url": f"/api/v2/integrations/requests/{request.id}/coverage",
            "timeline_url": f"/api/v2/integrations/requests/{request.id}/timeline",
        },
    }


def _get_request(db: Session, request_id: int) -> OptimizationRequest:
    row = (
        db.query(OptimizationRequest)
        .filter(
            OptimizationRequest.id == int(request_id),
            OptimizationRequest.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"优化请求不存在：{request_id}",
        )
    return row


def create_request(
    db: Session,
    cmd: CreateOptimizationRequestCommand,
    current_user: User | None,
) -> dict[str, Any]:
    existing = (
        db.query(OptimizationRequest)
        .filter(
            OptimizationRequest.source_system == cmd.source_system,
            OptimizationRequest.external_request_id == cmd.external_request_id,
            OptimizationRequest.deleted_at.is_(None),
        )
        .first()
    )
    if existing is not None:
        if existing.request_type != cmd.request_type:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="external_request_id 已存在但 request_type 不一致",
            )
        return _request_dict(db, existing, include_images=True, include_timeline=True)

    with begin_transaction(db):
        request = OptimizationRequest(
            external_request_id=cmd.external_request_id,
            source_system=cmd.source_system,
            client_id=cmd.client_id,
            request_type=cmd.request_type,
            external_model_id=cmd.external_model_id,
            labelu_model_id=cmd.labelu_model_id,
            export_id=cmd.export_id,
            event_label=cmd.event_label,
            scene_json=_dumps(cmd.scene) if cmd.scene else None,
            package_ids_json=_dumps(cmd.package_ids),
            event_time_from=cmd.event_time_from,
            event_time_to=cmd.event_time_to,
            require_complete=cmd.require_complete,
            allow_partial=cmd.allow_partial,
            min_sample_count=cmd.min_sample_count,
            status="PENDING",
            stage="RECEIVED",
            callback_url=cmd.callback_url,
            deadline_at=cmd.deadline_at,
            created_by=int(current_user.id) if current_user else None,
        )
        db.add(request)
        db.flush()
        _ensure_workflow(db, request, int(current_user.id) if current_user else None)
        cycle = _ensure_cycle(db, request)
        _bind_packages(db, request, cycle, cmd.package_ids)
        _bind_images(db, request, cycle, cmd.images)
        _append_workflow_event(
            db,
            request,
            event_type="request.created",
            to_status=request.status,
            payload={
                "request_type": request.request_type,
                "external_request_id": request.external_request_id,
                "package_ids": _request_package_ids(request),
            },
            actor_id=int(current_user.id) if current_user else None,
        )
        _refresh_request_state(db, request, int(current_user.id) if current_user else None)
        db.flush()
        data = _request_dict(db, request, include_images=True, include_timeline=True)
    return data


def bind_packages(
    db: Session,
    request_id: int,
    cmd: BindPackagesCommand,
    current_user: User | None,
) -> dict[str, Any]:
    request = _get_request(db, request_id)
    if request.status in TERMINAL_STATUSES:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="已结束的请求不能继续绑定数据",
        )
    with begin_transaction(db):
        cycle = _ensure_cycle(db, request)
        _bind_packages(db, request, cycle, cmd.package_ids)
        _bind_images(db, request, cycle, cmd.images)
        _refresh_request_state(db, request, int(current_user.id) if current_user else None)
        db.flush()
        data = _request_dict(db, request, include_images=True, include_timeline=True)
    return data


def get_request(db: Session, request_id: int, *, include_timeline: bool = True) -> dict[str, Any]:
    row = _get_request(db, request_id)
    return _request_dict(db, row, include_images=True, include_timeline=include_timeline)


def get_request_by_external(
    db: Session, external_request_id: str, source_system: str | None = None
) -> dict[str, Any]:
    query = db.query(OptimizationRequest).filter(
        OptimizationRequest.external_request_id == str(external_request_id).strip(),
        OptimizationRequest.deleted_at.is_(None),
    )
    if source_system:
        query = query.filter(OptimizationRequest.source_system == str(source_system).strip())
    row = query.order_by(OptimizationRequest.id.desc()).first()
    if row is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"优化请求不存在：{external_request_id}",
        )
    return _request_dict(db, row, include_images=True, include_timeline=True)


def get_requests_by_image(db: Session, external_image_id: str) -> list[dict[str, Any]]:
    rows = (
        db.query(OptimizationRequest)
        .join(
            ThirdPartyEventImage,
            ThirdPartyEventImage.request_id == OptimizationRequest.id,
        )
        .filter(
            ThirdPartyEventImage.external_image_id == str(external_image_id).strip(),
            OptimizationRequest.deleted_at.is_(None),
        )
        .distinct()
        .order_by(OptimizationRequest.id.desc())
        .all()
    )
    display = _display_context(db, rows)
    return [
        _request_dict(db, row, include_images=True, display=display.get(int(row.id)))
        for row in rows
    ]


def get_requests_by_package(db: Session, package_id: str) -> list[dict[str, Any]]:
    package = str(package_id).strip()
    candidates = (
        db.query(OptimizationRequest)
        .filter(OptimizationRequest.deleted_at.is_(None))
        .order_by(OptimizationRequest.id.desc())
        .all()
    )
    matched: list[OptimizationRequest] = []
    for row in candidates:
        package_ids = _request_package_ids(row)
        if package in package_ids:
            matched.append(row)
            continue
        exists = (
            db.query(ThirdPartyEventImage.id)
            .filter(
                ThirdPartyEventImage.request_id == row.id,
                ThirdPartyEventImage.package_id == package,
            )
            .first()
        )
        if exists:
            matched.append(row)
    display = _display_context(db, matched)
    return [
        _request_dict(db, row, include_images=True, display=display.get(int(row.id)))
        for row in matched
    ]


ORIGINAL_REQUEST_MAX_BYTES = 256 * 1024


def _latest_request_audit(
    db: Session, external_task_id: str | None
) -> ThirdPartyCallAudit | None:
    identifier = str(external_task_id or "").strip()
    if not identifier:
        return None
    return (
        db.query(ThirdPartyCallAudit)
        .filter(ThirdPartyCallAudit.external_task_id == identifier)
        .order_by(ThirdPartyCallAudit.id.desc())
        .first()
    )


def _compose_legacy_request(row: StrategyTaskIntegration) -> dict[str, Any]:
    """Rebuild a legacy strategy payload from the columns we kept."""

    return {
        "external_task_id": row.external_task_id,
        "task_name": row.task_name,
        "task_type": row.task_type,
        "schema_version": row.schema_version,
        "source_system": row.source_system,
        "source_package_id": row.source_package_id,
        "target": _loads(row.target_json, {}),
        "data_window": _loads(row.data_window_json, {}),
        "experiment": _loads(row.experiment_json, {}),
    }


def _compose_v2_request(db: Session, request: OptimizationRequest) -> dict[str, Any]:
    """Whitelist the persisted request fields for a v2 intake.

    The v2 intake never stores the raw HTTP body, so the operator-facing
    "original request" view has to be reconstructed from the columns that were
    actually kept.  Image rows are capped to keep the response bounded.
    """

    data = _request_dict(db, request, include_images=True)
    images = data.get("images") or []
    return {
        "request_type": data.get("request_type"),
        "external_request_id": data.get("external_request_id"),
        "source_system": data.get("source_system"),
        "client_id": data.get("client_id"),
        "external_model_id": data.get("external_model_id"),
        "labelu_model_id": data.get("labelu_model_id"),
        "event_label": data.get("event_label"),
        "scene": data.get("scene"),
        "event_time_from": data.get("event_time_from"),
        "event_time_to": data.get("event_time_to"),
        "require_complete": data.get("require_complete"),
        "allow_partial": data.get("allow_partial"),
        "min_sample_count": data.get("min_sample_count"),
        "callback_url": data.get("callback_url"),
        "package_ids": data.get("package_ids") or [],
        "images": {"count": len(images), "items": images[:100]},
    }


def _original_intake(audit: ThirdPartyCallAudit | None) -> dict[str, Any]:
    if audit is None:
        return {"method": None, "path": None, "outcome": None}
    return {
        "method": audit.request_method,
        "path": audit.request_path,
        "outcome": audit.outcome,
    }


def _guard_original_payload(
    payload: Any, warning: str | None
) -> tuple[Any, bool, str | None]:
    if payload is None:
        return None, False, warning
    try:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(payload)
    encoded = text.encode("utf-8")
    if len(encoded) <= ORIGINAL_REQUEST_MAX_BYTES:
        return payload, False, warning
    shortened = encoded[:ORIGINAL_REQUEST_MAX_BYTES].decode("utf-8", errors="ignore")
    notice = "原始报文超过 256KB，已截断"
    return shortened, True, f"{warning}；{notice}" if warning else notice


def original_request(db: Session, request_id: int) -> dict[str, Any]:
    """Return the admin-only raw/composed third-party request view.

    This is intentionally a separate endpoint: raw request bodies are large and
    must never inflate the polled list/detail/SSE payloads.
    """

    request = _get_request(db, request_id)
    strategy_id = str(
        request.strategy_external_task_id or request.external_request_id or ""
    ).strip()
    strategy = (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.external_task_id == strategy_id,
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .first()
        if strategy_id
        else None
    )
    audit = _latest_request_audit(db, request.external_request_id)
    if audit is None and strategy_id != str(request.external_request_id or "").strip():
        audit = _latest_request_audit(db, strategy_id)

    raw_available = False
    payload: Any = None
    composed: Any = None
    warning: str | None = None
    if strategy is not None:
        raw_text = str(strategy.request_payload_json or "").strip()
        parsed = _loads(raw_text, None) if raw_text else None
        if parsed is not None and parsed != {}:
            payload = parsed
            raw_available = True
        else:
            composed = _compose_legacy_request(strategy)
            warning = "未找到原始请求报文，以下按已保存字段还原"
    else:
        composed = _compose_v2_request(db, request)
        warning = "v2 接口未持久化原始报文，以下为按字段还原"

    payload, payload_truncated, warning = _guard_original_payload(payload, warning)
    composed, composed_truncated, warning = _guard_original_payload(composed, warning)
    truncated = payload_truncated or composed_truncated

    category = strategy.dataset_category if strategy is not None else None
    return {
        "request_id": request.id,
        "source": request.source_system,
        "raw_available": raw_available,
        "raw_sha256": audit.request_sha256 if audit is not None else None,
        "raw_bytes": audit.request_bytes if audit is not None else None,
        "received_at": _iso(audit.received_at) if audit is not None else None,
        "intake": _original_intake(audit),
        "payload": payload,
        "composed": composed,
        "linked": {
            "strategy_external_task_id": request.strategy_external_task_id
            or (strategy.external_task_id if strategy is not None else None),
            "strategy_task_name": strategy.task_name if strategy is not None else None,
            "platform_status": (
                strategy.platform_status if strategy is not None else None
            ),
            "event_type": request.event_label,
            "model_id": request.external_model_id,
            "dataset_category_key": (
                category.category_key if category is not None else None
            ),
            "dataset_category_name": category.name if category is not None else None,
        },
        "truncated": truncated,
        "warning": warning,
    }


def list_requests(
    db: Session,
    *,
    status_value: str | None = None,
    stage: str | None = None,
    request_type: str | None = None,
    source_system: str | None = None,
    model_id: str | None = None,
    external_request_id: str | None = None,
    keyword: str | None = None,
    page: int = 0,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    query = db.query(OptimizationRequest).filter(OptimizationRequest.deleted_at.is_(None))
    if status_value:
        query = query.filter(OptimizationRequest.status == str(status_value).strip())
    if stage:
        query = query.filter(OptimizationRequest.stage == str(stage).strip())
    if request_type:
        query = query.filter(OptimizationRequest.request_type == str(request_type).strip())
    if source_system:
        query = query.filter(OptimizationRequest.source_system == str(source_system).strip())
    if model_id:
        query = query.filter(OptimizationRequest.external_model_id == str(model_id).strip())
    if external_request_id:
        query = query.filter(
            OptimizationRequest.external_request_id == str(external_request_id).strip()
        )
    if keyword:
        pattern = f"%{str(keyword).strip()}%"
        query = query.filter(
            or_(
                OptimizationRequest.external_request_id.ilike(pattern),
                OptimizationRequest.external_model_id.ilike(pattern),
                OptimizationRequest.event_label.ilike(pattern),
            )
        )
    total = query.count()
    rows = (
        query.order_by(OptimizationRequest.updated_at.desc(), OptimizationRequest.id.desc())
        .offset(max(0, int(page)) * max(1, int(size)))
        .limit(max(1, min(int(size), 200)))
        .all()
    )
    display = _display_context(db, rows)
    return [
        _request_dict(db, row, display=display.get(int(row.id))) for row in rows
    ], int(total)


def data_availability(
    db: Session,
    *,
    model_id: str | None = None,
    event_label: str | None = None,
    event_time_from: datetime | None = None,
    event_time_to: datetime | None = None,
) -> dict[str, Any]:
    query = (
        db.query(ThirdPartyEventImage)
        .join(OptimizationRequest, OptimizationRequest.id == ThirdPartyEventImage.request_id)
        .filter(OptimizationRequest.deleted_at.is_(None))
    )
    if model_id:
        query = query.filter(OptimizationRequest.external_model_id == str(model_id).strip())
    if event_label:
        query = query.filter(
            or_(
                ThirdPartyEventImage.event_label == str(event_label).strip(),
                OptimizationRequest.event_label == str(event_label).strip(),
            )
        )
    if event_time_from is not None:
        query = query.filter(ThirdPartyEventImage.event_time >= event_time_from)
    if event_time_to is not None:
        query = query.filter(ThirdPartyEventImage.event_time <= event_time_to)
    rows = query.order_by(ThirdPartyEventImage.event_time.asc()).all()
    available = 0
    pending = 0
    unknown = 0
    available_times: list[datetime] = []
    for row in rows:
        import_row = _import_by_package(db, row.package_id) if row.package_id else None
        if _image_is_available(row, import_row):
            available += 1
            if row.event_time:
                available_times.append(row.event_time)
        elif row.package_id and import_row is not None:
            pending += 1
        elif row.task_id or row.sample_id:
            available += 1
        else:
            unknown += 1
    requested = len(rows)
    complete = requested > 0 and pending == 0 and unknown == 0
    complete_windows: list[list[str]] = []
    if complete and available_times:
        complete_windows.append([_iso(min(available_times)), _iso(max(available_times))])  # type: ignore[list-item]
    return {
        "requested": requested,
        "available": available,
        "pending": pending,
        "unknown": unknown,
        "coverage_ratio": round(available / requested, 4) if requested else 0.0,
        "complete": complete,
        "watermark": _iso(max(available_times)) if available_times else None,
        "complete_windows": complete_windows,
    }


def coverage_for_request(db: Session, request_id: int) -> dict[str, Any]:
    request = _get_request(db, request_id)
    coverage = _coverage(db, request)
    request.coverage_json = _dumps(coverage)
    db.flush()
    return coverage


def refresh_request(db: Session, request_id: int, actor_id: int | None = None) -> dict[str, Any]:
    """Recompute coverage/stage from the latest package/task state."""
    request = _get_request(db, request_id)
    with begin_transaction(db):
        _refresh_request_state(db, request, actor_id)
        db.flush()
        data = _request_dict(db, request, include_images=True, include_timeline=True)
    return data


def refresh_requests_for_package(
    db: Session, package_id: str, actor_id: int | None = None
) -> int:
    """Called by the import worker after a package/group reaches a new state."""
    package = str(package_id or "").strip()
    if not package:
        return 0
    candidates = (
        db.query(OptimizationRequest)
        .filter(
            OptimizationRequest.deleted_at.is_(None),
            OptimizationRequest.status.notin_(list(TERMINAL_STATUSES)),
        )
        .all()
    )
    matched = [row for row in candidates if package in _request_package_ids(row)]
    if not matched:
        # Third-party clients may create the request before uploading the
        # package.  Bind a newly-arrived package to the newest compatible
        # request using model + event + overlapping window; explicit package
        # binding remains available through the bind API.
        import_row = _import_by_package(db, package)
        if import_row is not None:
            compatible = [
                row
                for row in candidates
                if not _request_package_ids(row)
                and _package_matches_request(import_row, row)
            ]
            matched = compatible[:1]
            if matched:
                with begin_transaction(db):
                    cycle = _ensure_cycle(db, matched[0])
                    _bind_packages(db, matched[0], cycle, [package])
                    _append_workflow_event(
                        db,
                        matched[0],
                        event_type="request.package_auto_bound",
                        payload={"package_id": package},
                        actor_id=actor_id,
                    )
    if not matched:
        return 0
    with begin_transaction(db):
        for request in matched:
            cycle = _ensure_cycle(db, request)
            for package in _request_package_ids(request):
                import_row = _import_by_package(db, package)
                if import_row is not None:
                    _sync_event_images_from_import(db, request, cycle, import_row)
            _refresh_request_state(db, request, actor_id)
        db.flush()
    return len(matched)


def _package_matches_request(
    import_row: ExperimentDatasetImport, request: OptimizationRequest
) -> bool:
    package_event = str(import_row.event_type or "").strip()
    request_event = str(request.event_label or "").strip()
    if package_event and request_event and package_event != request_event:
        return False
    package_model = str(import_row.model_id or "").strip()
    request_model = str(request.external_model_id or "").strip()
    if package_model and request_model and package_model != request_model:
        return False
    if not (package_event or package_model):
        return False
    source = _loads(import_row.source_payload_json, {}) or {}
    package_start = str(source.get("window_start") or "").strip()
    package_end = str(source.get("window_end") or "").strip()
    if not (package_start or package_end):
        return True
    try:
        package_from = _naive_datetime(
            datetime.fromisoformat(package_start.replace("Z", "+00:00"))
            if package_start
            else None
        )
        package_to = _naive_datetime(
            datetime.fromisoformat(package_end.replace("Z", "+00:00"))
            if package_end
            else None
        )
    except ValueError:
        return True
    request_from = _naive_datetime(request.event_time_from)
    request_to = _naive_datetime(request.event_time_to)
    if package_from is not None and request_to is not None:
        if package_from > request_to:
            return False
    if package_to is not None and request_from is not None:
        if package_to < request_from:
            return False
    return True


def _naive_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def timeline_for_request(db: Session, request_id: int) -> dict[str, Any]:
    request = _get_request(db, request_id)
    return {
        "request_id": request.id,
        "workflow_id": request.workflow_id,
        "events": _workflow_events(db, request),
    }


def callbacks_for_request(db: Session, request_id: int) -> dict[str, Any]:
    request = _get_request(db, request_id)
    rows = (
        db.query(WorkflowOutbox)
        .filter(
            WorkflowOutbox.aggregate_type == "optimization_request",
            WorkflowOutbox.aggregate_id == str(request.id),
        )
        .order_by(WorkflowOutbox.id.asc())
        .limit(200)
        .all()
    )
    return {
        "request_id": request.id,
        "callback_url": request.callback_url,
        "callbacks": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "status": row.status,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.updated_at),
                "processed_at": _iso(row.processed_at),
                "payload": _loads(row.payload_json, {}),
            }
            for row in rows
        ],
    }


def cancel_request(db: Session, request_id: int, current_user: User | None) -> dict[str, Any]:
    request = _get_request(db, request_id)
    if request.status in TERMINAL_STATUSES:
        return _request_dict(db, request, include_images=True)
    with begin_transaction(db):
        previous_status = request.status
        request.status = "CANCELLED"
        request.stage = "CANCELLED"
        request.finished_at = _now()
        _append_workflow_event(
            db,
            request,
            event_type="request.cancelled",
            from_status=previous_status,
            to_status="CANCELLED",
            payload={},
            actor_id=int(current_user.id) if current_user else None,
        )
        db.flush()
        data = _request_dict(db, request, include_images=True, include_timeline=True)
    return data


def _get_decision(db: Session, request_id: int) -> IntegrationPublishDecision | None:
    return (
        db.query(IntegrationPublishDecision)
        .filter(IntegrationPublishDecision.request_id == int(request_id))
        .first()
    )


def upsert_publish_decision(
    db: Session,
    request_id: int,
    cmd: PublishDecisionUpsertCommand,
    current_user: User | None,
) -> dict[str, Any]:
    request = _get_request(db, request_id)
    with begin_transaction(db):
        decision = _get_decision(db, request.id)
        if decision is None:
            decision = IntegrationPublishDecision(request_id=request.id)
            db.add(decision)
            db.flush()
        decision.candidate_model_id = cmd.candidate_model_id
        decision.baseline_model_id = cmd.baseline_model_id
        decision.candidate_export_id = cmd.candidate_export_id
        decision.metrics_json = _dumps(cmd.metrics)
        decision.auto_publish_eligible = bool(cmd.auto_publish_eligible)
        decision.decision = cmd.decision
        decision.reason = cmd.reason
        decision.evidence_json = _dumps(cmd.evidence)
        if cmd.decision == "AUTO":
            request.stage = "AUTO_PUBLISHED"
            request.status = "RUNNING"
        elif cmd.decision == "MANUAL_PENDING":
            request.stage = "WAITING_MANUAL_PUBLISH"
            request.status = "WAITING"
        _append_workflow_event(
            db,
            request,
            event_type="request.publish_decision",
            payload={
                "decision": decision.decision,
                "auto_publish_eligible": decision.auto_publish_eligible,
                "reason": decision.reason,
            },
            actor_id=int(current_user.id) if current_user else None,
        )
        db.flush()
        data = _request_dict(db, request, include_images=True, include_timeline=True)
    return data


def approve_publish(
    db: Session,
    request_id: int,
    cmd: ApprovePublishCommand,
    current_user: User | None,
) -> dict[str, Any]:
    request = _get_request(db, request_id)
    decision = _get_decision(db, request.id)
    if decision is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="尚未生成发布决策",
        )
    with begin_transaction(db):
        decision.decision = cmd.decision
        decision.reason = cmd.reason or decision.reason
        decision.decided_by = int(current_user.id) if current_user else None
        decision.decided_at = _now()
        if cmd.decision == "APPROVED":
            request.stage = "PUBLISHING"
            request.status = "RUNNING"
        else:
            request.stage = "WAITING_MANUAL_PUBLISH"
            request.status = "WAITING"
        _append_workflow_event(
            db,
            request,
            event_type="request.publish_approved",
            payload={"decision": cmd.decision, "reason": cmd.reason},
            actor_id=int(current_user.id) if current_user else None,
        )
        db.flush()
        data = _request_dict(db, request, include_images=True, include_timeline=True)
    return data


async def publish_request(
    db: Session,
    request_id: int,
    current_user: User | None,
) -> dict[str, Any]:
    """Execute a manually approved publish through the existing AICM service."""
    request = _get_request(db, request_id)
    decision = _get_decision(db, request.id)
    if decision is None or decision.decision != "APPROVED":
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="发布决策尚未批准",
        )
    export_id = decision.candidate_export_id or request.export_id
    if not export_id:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="发布决策缺少候选 bmodel 导出 ID",
        )
    from labelu.internal.application.service import yolo_aicm_publish

    job = await yolo_aicm_publish.start_publish_job(
        db,
        current_user,
        export_id=int(export_id),
        publish_mode="manual",
    )
    job_payload = (
        job.model_dump(mode="json") if hasattr(job, "model_dump") else dict(job)
    )
    with begin_transaction(db):
        request.stage = "PUBLISHING"
        request.status = "RUNNING"
        request.result_json = _dumps(job_payload)
        decision.published_model_id = job_payload.get("model_set_id") or job_payload.get("model_id")
        decision.aicm_model_set_id = decision.published_model_id
        _append_workflow_event(
            db,
            request,
            event_type="request.publish_started",
            payload={"export_id": int(export_id), "job": job_payload},
            actor_id=int(current_user.id) if current_user else None,
        )
        db.flush()
        data = _request_dict(db, request, include_images=True, include_timeline=True)
    return data


def stream_snapshot(
    db: Session,
    *,
    status_value: str | None = None,
    stage: str | None = None,
    request_type: str | None = None,
    source_system: str | None = None,
    model_id: str | None = None,
    keyword: str | None = None,
    size: int = 50,
) -> dict[str, Any]:
    rows, total = list_requests(
        db,
        status_value=status_value,
        stage=stage,
        request_type=request_type,
        source_system=source_system,
        model_id=model_id,
        keyword=keyword,
        page=0,
        size=size,
    )
    return {
        "generated_at": _iso(_now()),
        "filters": {
            "status": status_value,
            "stage": stage,
            "request_type": request_type,
            "source_system": source_system,
            "model_id": model_id,
            "q": keyword,
        },
        "total": total,
        "rows": rows,
        "summary": summary(db),
    }


def summary(db: Session) -> dict[str, Any]:
    total = db.query(OptimizationRequest).filter(OptimizationRequest.deleted_at.is_(None)).count()
    rows = (
        db.query(OptimizationRequest.stage, func.count(OptimizationRequest.id))
        .filter(OptimizationRequest.deleted_at.is_(None))
        .group_by(OptimizationRequest.stage)
        .all()
    )
    stages = {str(stage or ""): int(count) for stage, count in rows}
    status_rows = (
        db.query(OptimizationRequest.status, func.count(OptimizationRequest.id))
        .filter(OptimizationRequest.deleted_at.is_(None))
        .group_by(OptimizationRequest.status)
        .all()
    )
    statuses = {str(status_value or ""): int(count) for status_value, count in status_rows}
    return {
        "total": total,
        "stages": stages,
        "statuses": statuses,
        "waiting_review": stages.get("WAITING_REVIEW", 0),
        "waiting_manual_publish": stages.get("WAITING_MANUAL_PUBLISH", 0),
        "completed": statuses.get("SUCCEEDED", 0),
        "failed": statuses.get("FAILED", 0),
        "generated_at": _iso(_now()),
    }


def alerts(db: Session, *, stale_minutes: int = 30, limit: int = 50) -> dict[str, Any]:
    """Operational alert projection for overdue, stale and failed requests."""
    now = _now()
    cutoff = now - timedelta(minutes=max(1, int(stale_minutes)))
    size = max(1, min(int(limit), 200))
    active = (
        db.query(OptimizationRequest)
        .filter(
            OptimizationRequest.deleted_at.is_(None),
            OptimizationRequest.status.notin_(list(TERMINAL_STATUSES)),
        )
        .all()
    )

    def brief(row: OptimizationRequest) -> dict[str, Any]:
        return {
            "id": row.id,
            "external_request_id": row.external_request_id,
            "request_type": row.request_type,
            "external_model_id": row.external_model_id,
            "event_label": row.event_label,
            "stage": row.stage,
            "status": row.status,
            "updated_at": _iso(row.updated_at),
            "deadline_at": _iso(row.deadline_at),
            "strategy_external_task_id": row.strategy_external_task_id,
        }

    deadline_rows = [
        row
        for row in active
        if row.deadline_at is not None
        and _naive_datetime(row.deadline_at) is not None
        and _naive_datetime(row.deadline_at) < now
    ][:size]
    stale_rows = [
        row
        for row in active
        if row.status in {"WAITING", "RUNNING"}
        and row.updated_at is not None
        and _naive_datetime(row.updated_at) is not None
        and _naive_datetime(row.updated_at) < cutoff
    ][:size]
    failed_rows = (
        db.query(OptimizationRequest)
        .filter(
            OptimizationRequest.deleted_at.is_(None),
            OptimizationRequest.status == "FAILED",
        )
        .order_by(OptimizationRequest.updated_at.desc())
        .limit(size)
        .all()
    )
    callback_rows = (
        db.query(WorkflowOutbox)
        .filter(
            WorkflowOutbox.aggregate_type == "optimization_request",
            WorkflowOutbox.status.in_({"FAILED", "DEAD_LETTER"}),
        )
        .order_by(WorkflowOutbox.updated_at.desc())
        .limit(size)
        .all()
    )
    callback_items = [
        {
            "id": row.id,
            "request_id": row.aggregate_id,
            "status": row.status,
            "attempts": row.attempts,
            "last_error": row.last_error,
            "updated_at": _iso(row.updated_at),
        }
        for row in callback_rows
    ]
    counts = {
        "deadline": len(deadline_rows),
        "stale": len(stale_rows),
        "failed": len(failed_rows),
        "callback_failed": len(callback_items),
    }
    counts["total"] = sum(counts.values())
    return {
        "generated_at": _iso(now),
        "stale_minutes": int(stale_minutes),
        "counts": counts,
        "deadline": [brief(row) for row in deadline_rows],
        "stale": [brief(row) for row in stale_rows],
        "failed": [brief(row) for row in failed_rows],
        "callback_failed": callback_items,
    }



def _short_time(value: datetime | None, fallback: str = "") -> str:
    if value is not None:
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    text = str(fallback or "").strip()
    if not text:
        return _now().strftime("%Y-%m-%dT%H:%M:%SZ")
    if len(text) <= 32:
        return text
    return text[:32]


def _strategy_command_for_request(
    db: Session, request: OptimizationRequest
) -> tuple[CreateStrategyEvolutionTaskCommand, ExperimentDatasetImport] | None:
    ready_statuses = {"COMPLETED", "PARTIAL_COMPLETED"}
    import_row: ExperimentDatasetImport | None = None
    for package_id in _request_package_ids(request):
        row = _import_by_package(db, package_id)
        if row is not None and str(row.status or "").upper() in ready_statuses:
            import_row = row
            break
    if import_row is None:
        return None

    source = _loads(import_row.source_payload_json, {}) or {}
    source_target = source.get("target") if isinstance(source.get("target"), dict) else {}
    model_id = str(
        source_target.get("modelId")
        or source_target.get("model_id")
        or request.external_model_id
        or ""
    ).strip()
    event_type = str(
        source_target.get("event_type") or request.event_label or ""
    ).strip()
    if not model_id or not event_type:
        return None
    scene = _loads(request.scene_json, {}) or {}
    if request.request_type == "scene_training" and scene:
        scene_type = str(
            scene.get("scene_type")
            or scene.get("scene")
            or scene.get("lighting")
            or "default"
        ).strip()
    else:
        scene_type = str(
            source_target.get("scene_type")
            or scene.get("scene_type")
            or scene.get("scene")
            or "default"
        ).strip()
    device_type = str(source_target.get("device_type") or "camera").strip()
    strategy_id = str(source_target.get("strategy_id") or f"opt-{request.id}").strip()
    start_time = _short_time(
        request.event_time_from,
        str(source.get("window_start") or "") or f"{import_row.dataset_date}T00:00:00Z",
    )
    end_time = _short_time(
        request.event_time_to,
        str(source.get("window_end") or "") or _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    external_task_id = f"optreq-{request.id}"
    task_type = (
        "threshold_experiment"
        if request.request_type == "threshold_experiment"
        else "model_capability"
    )
    cmd = CreateStrategyEvolutionTaskCommand(
        schema_version="v1",
        external_task_id=external_task_id,
        task_name=f"优化请求#{request.id} {event_type}"[:128],
        task_type=task_type,
        source_package_id=str(import_row.package_id or ""),
        target=StrategyEvolutionTargetPayload(
            device_type=device_type[:128],
            event_type=event_type[:128],
            model_id=model_id[:128],
            scene_type=scene_type[:128],
            strategy_id=strategy_id[:128],
        ),
        data_window=StrategyEvolutionDataWindowPayload(
            start_time=start_time,
            end_time=end_time,
        ),
        callback_url=request.callback_url or None,
        experiment={},
    )
    return cmd, import_row


def _strategy_stage_mapping(row: StrategyTaskIntegration) -> tuple[str, str]:
    platform_status = str(row.platform_status or "").strip().lower()
    automation_stage = str(row.automation_stage or "").strip().lower()
    if platform_status in {"waiting_for_annotations"}:
        return "WAITING", "WAITING_REVIEW"
    if platform_status in {"waiting_for_model"}:
        return "WAITING", "WAITING_FOR_MODEL"
    if platform_status in {"failed", "callback_failed"}:
        return "FAILED", "FAILED"
    if platform_status in {"comparison_failed"}:
        # A candidate that did not beat the baseline is a manual publish
        # decision, not a dead request.
        return "WAITING", "WAITING_PUBLISH_DECISION"
    if platform_status in {"published", "completed", "succeeded"}:
        return "SUCCEEDED", "COMPLETED"
    if platform_status in {"waiting_for_publish", "waiting_publish_decision"}:
        return "WAITING", "WAITING_PUBLISH_DECISION"
    if automation_stage in {"training", "model_training"}:
        return "RUNNING", "TRAINING"
    if automation_stage in {"export", "exporting"}:
        return "RUNNING", "EXPORTING"
    if automation_stage in {"edge_validation", "validation"}:
        return "RUNNING", "EDGE_VALIDATING"
    if automation_stage in {"comparison", "publish", "publishing"}:
        return "RUNNING", "WAITING_PUBLISH_DECISION"
    return "RUNNING", "WAITING_REVIEW"


def sync_strategy_flow(db: Session, request: OptimizationRequest) -> bool:
    """Project an existing strategy task into the request stage/result."""
    external_task_id = str(request.strategy_external_task_id or "").strip()
    if not external_task_id:
        return False
    decision = _get_decision(db, request.id)
    if (
        request.stage == "PUBLISHING"
        and decision is not None
        and decision.decision == "APPROVED"
    ):
        # Manual publish has already been handed to AICM; do not let an old
        # comparison_failed strategy snapshot pull the request backwards.
        return False
    row = (
        db.query(StrategyTaskIntegration)
        .filter(StrategyTaskIntegration.external_task_id == external_task_id)
        .first()
    )
    if row is None:
        return False
    if request.status in TERMINAL_STATUSES:
        return False
    next_status, next_stage = _strategy_stage_mapping(row)
    result = _loads(row.platform_result_json, None)
    error = _loads(row.platform_error_json, None)
    previous_status, previous_stage = request.status, request.stage
    request.status = next_status
    request.stage = next_stage
    if result is not None:
        request.result_json = _dumps(result)
    if error is not None:
        request.error_json = _dumps(error)
    if next_status in TERMINAL_STATUSES:
        request.finished_at = request.finished_at or _now()
    decision = _get_decision(db, request.id)
    if next_stage in {"WAITING_PUBLISH_DECISION", "WAITING_MANUAL_PUBLISH"} and decision is None:
        decision = IntegrationPublishDecision(
            request_id=request.id,
            decision="MANUAL_PENDING",
            auto_publish_eligible=False,
            reason="策略编排已到发布决策阶段",
            metrics_json=_dumps(result or {}),
        )
        db.add(decision)
    elif next_stage == "COMPLETED":
        if decision is None:
            decision = IntegrationPublishDecision(
                request_id=request.id,
                decision="AUTO",
                auto_publish_eligible=True,
                reason="策略编排发布完成",
                metrics_json=_dumps(result or {}),
                decided_at=_now(),
            )
            db.add(decision)
        elif decision.decision in {"PENDING", "MANUAL_PENDING"}:
            decision.decision = "AUTO"
            decision.auto_publish_eligible = True
            decision.reason = decision.reason or "策略编排发布完成"
            decision.decided_at = decision.decided_at or _now()
    if previous_status != request.status or previous_stage != request.stage:
        _append_workflow_event(
            db,
            request,
            event_type="request.strategy_stage_changed",
            from_status=previous_status,
            to_status=request.status,
            payload={
                "stage": request.stage,
                "strategy_external_task_id": external_task_id,
                "platform_status": row.platform_status,
                "automation_stage": row.automation_stage,
                "result": result,
            },
        )
    return True


async def start_strategy_flow(
    db: Session, request_id: int, actor_id: int | None = None
) -> dict[str, Any]:
    """Start the existing strategy/training/validation/publish pipeline once.

    The bridge deliberately reuses strategy_evolution's public acceptance
    entrypoint rather than duplicating its orchestration.
    """
    request = _get_request(db, request_id)
    if request.strategy_external_task_id:
        with begin_transaction(db):
            sync_strategy_flow(db, request)
        return {"started": False, "reason": "already_started", "request_id": request.id}
    coverage = _coverage(db, request)
    if request.request_type == "threshold_experiment":
        ready = bool(coverage.get("available")) and (
            bool(coverage.get("complete")) or bool(request.allow_partial)
        )
    else:
        ready = bool(coverage.get("available") >= int(request.min_sample_count or 1)) and (
            bool(coverage.get("complete")) or not request.require_complete
        )
    if not ready:
        return {"started": False, "reason": "data_not_ready", "request_id": request.id}
    built = _strategy_command_for_request(db, request)
    if built is None:
        return {
            "started": False,
            "reason": "package_or_source_not_ready",
            "request_id": request.id,
        }
    cmd, _import_row = built
    try:
        await strategy_evolution.accept_task(db=db, cmd=cmd)
    except Exception as exc:  # strategy validation is surfaced and retryable
        logger.warning(
            "optimization request strategy bridge rejected request_id={} reason={}",
            request.id,
            exc,
        )
        with begin_transaction(db):
            _append_workflow_event(
                db,
                request,
                event_type="request.pipeline_rejected",
                payload={"reason": str(exc)[:1000], "external_task_id": cmd.external_task_id},
                actor_id=actor_id,
            )
        return {"started": False, "reason": str(exc)[:500], "request_id": request.id}

    with begin_transaction(db):
        request.strategy_external_task_id = cmd.external_task_id
        _append_workflow_event(
            db,
            request,
            event_type="request.pipeline_started",
            payload={"external_task_id": cmd.external_task_id},
            actor_id=actor_id,
        )
        sync_strategy_flow(db, request)
    return {
        "started": True,
        "request_id": request.id,
        "strategy_external_task_id": cmd.external_task_id,
    }


def sync_active_requests(db: Session) -> int:
    rows = (
        db.query(OptimizationRequest)
        .filter(
            OptimizationRequest.deleted_at.is_(None),
            OptimizationRequest.status.notin_(list(TERMINAL_STATUSES)),
        )
        .all()
    )
    changed = 0
    with begin_transaction(db):
        for request in rows:
            _refresh_request_state(db, request)
            if request.strategy_external_task_id:
                sync_strategy_flow(db, request)
                changed += 1
        db.flush()
    return changed


def sync_publish_jobs(db: Session, limit: int = 20) -> int:
    """Poll AICM publish jobs created by the manual publish action."""
    from labelu.internal.common.aicm_publish_job import load_job

    rows = (
        db.query(OptimizationRequest)
        .filter(
            OptimizationRequest.deleted_at.is_(None),
            OptimizationRequest.stage == "PUBLISHING",
        )
        .order_by(OptimizationRequest.updated_at.asc())
        .limit(max(1, min(int(limit), 100)))
        .all()
    )
    changed = 0
    with begin_transaction(db):
        for request in rows:
            decision = _get_decision(db, request.id)
            if decision is None or decision.decision != "APPROVED":
                continue
            result = _loads(request.result_json, {}) or {}
            job_id = str(result.get("job_id") or "").strip()
            if not job_id:
                continue
            raw = load_job(job_id)
            if not isinstance(raw, dict):
                continue
            job_status = str(raw.get("status") or "").upper()
            if job_status not in {"COMPLETED", "FAILED"}:
                continue
            previous = request.status
            if job_status == "COMPLETED":
                request.status = "SUCCEEDED"
                request.stage = "COMPLETED"
                request.finished_at = request.finished_at or _now()
                request.result_json = _dumps({**result, "publish_job": raw})
                request.error_json = None
            else:
                request.status = "FAILED"
                request.stage = "FAILED"
                request.finished_at = request.finished_at or _now()
                request.error_json = _dumps(
                    {
                        "code": "AICM_PUBLISH_FAILED",
                        "message": raw.get("error") or "AICM 发布失败",
                    }
                )
            _append_workflow_event(
                db,
                request,
                event_type="request.publish_finished",
                from_status=previous,
                to_status=request.status,
                payload={"job_id": job_id, "job_status": job_status},
            )
            changed += 1
        db.flush()
    return changed



def dispatch_request_callbacks(db: Session, limit: int = 20) -> list[dict[str, Any]]:
    """Deliver request callbacks through the shared workflow outbox."""
    from labelu.internal.application.service import flexible_callback

    rows = (
        db.query(WorkflowOutbox)
        .filter(
            WorkflowOutbox.aggregate_type == "optimization_request",
            WorkflowOutbox.event_type == "7gai.workflow.callback.dispatch.v1",
            WorkflowOutbox.status.in_({"PENDING", "RETRY"}),
            or_(
                WorkflowOutbox.available_at.is_(None),
                WorkflowOutbox.available_at <= _now(),
            ),
        )
        .order_by(WorkflowOutbox.id.asc())
        .limit(max(1, min(int(limit), 100)))
        .all()
    )
    results = []
    for row in rows:
        try:
            results.append(
                flexible_callback.dispatch_outbox(db, outbox_id=int(row.id))
            )
        except Exception as exc:
            logger.warning(
                "optimization request callback dispatch failed outbox_id={} error={}",
                row.id,
                exc,
            )
    return results


