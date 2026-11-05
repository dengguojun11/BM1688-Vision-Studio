"""Project legacy strategy-evolution tasks into the optimization request model.

The third-party strategy platform still submits model-capability and threshold
tasks through ``/internal/v1/strategy-evolution/*``.  Operators watch the
evolution center's "模型优化请求" tab, which reads ``OptimizationRequest`` rows.
Without this bridge an accepted strategy task exists only in
``strategy_task_integration`` and is invisible to that page.

The bridge is a projection, not a second pipeline: it never starts training or
publishing and never emits v2 callbacks (the row intentionally has no
``workflow_id``).  Once the linked request exists, the existing optimization
request worker keeps its status/stage in sync through
``integration_requests.sync_strategy_flow``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.third_party_optimization import OptimizationRequest

SOURCE_SYSTEM = "strategy_evolution"
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
DEFAULT_MIN_SAMPLE_COUNT = 50
MAX_MIN_SAMPLE_COUNT = 100000


def _loads(value: str | None, default: Any) -> Any:
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return default
    return parsed


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _request_type_for(task_type: str) -> str:
    normalized = str(task_type or "").strip()
    return (
        "threshold_experiment"
        if normalized == "threshold_experiment"
        else "model_optimization"
    )


def _status_stage_for(row: StrategyTaskIntegration) -> tuple[str, str]:
    """Mirror the optimization request state machine for a strategy snapshot."""
    platform = str(row.platform_status or "").strip().lower()
    stage = str(row.automation_stage or "").strip().lower()
    if platform == "waiting_for_annotations":
        return "WAITING", "WAITING_REVIEW"
    if platform == "waiting_for_model":
        return "WAITING", "WAITING_FOR_MODEL"
    if platform == "waiting_for_gpu":
        return "WAITING", "WAITING_FOR_MODEL"
    if platform in {"failed", "callback_failed"}:
        return "FAILED", "FAILED"
    if platform == "comparison_failed":
        # A candidate that did not beat the baseline is a manual publish
        # decision, not a dead request.
        return "WAITING", "WAITING_PUBLISH_DECISION"
    if platform in {"published", "completed", "succeeded"}:
        return "SUCCEEDED", "COMPLETED"
    if platform in {"waiting_for_publish", "waiting_publish_decision"}:
        return "WAITING", "WAITING_PUBLISH_DECISION"
    if platform in {"cancelled", "canceled"}:
        return "CANCELLED", "CANCELLED"
    if stage in {"training", "model_training"}:
        return "RUNNING", "TRAINING"
    if stage in {"export", "exporting"}:
        return "RUNNING", "EXPORTING"
    if stage in {"edge_validation", "validation"}:
        return "RUNNING", "EDGE_VALIDATING"
    if stage in {"comparison", "publish", "publishing"}:
        return "RUNNING", "WAITING_PUBLISH_DECISION"
    return "RUNNING", "WAITING_REVIEW"


def _scene_payload(
    target: dict[str, Any], experiment: dict[str, Any]
) -> dict[str, Any]:
    scene: dict[str, Any] = {}
    for key in ("device_type", "scene_type", "strategy_id"):
        value = target.get(key)
        if value not in (None, ""):
            scene[key] = value
    if isinstance(experiment, dict):
        for key in (
            "capability_issue_type",
            "symptoms",
            "previous_experiment_refs",
            "dataset_requirements",
        ):
            value = experiment.get(key)
            if value not in (None, "", [], {}):
                scene[key] = value
    return scene


def _apply_projection(
    request: OptimizationRequest, row: StrategyTaskIntegration
) -> None:
    target = _loads(row.target_json, {})
    window = _loads(row.data_window_json, {})
    experiment = _loads(row.experiment_json, {})
    target = target if isinstance(target, dict) else {}
    window = window if isinstance(window, dict) else {}
    experiment = experiment if isinstance(experiment, dict) else {}

    requirements = experiment.get("dataset_requirements")
    target_sample_count = DEFAULT_MIN_SAMPLE_COUNT
    if isinstance(requirements, dict):
        target_sample_count = _as_int(
            requirements.get("target_sample_count"), DEFAULT_MIN_SAMPLE_COUNT
        )
    request.min_sample_count = max(
        1,
        min(int(target_sample_count or DEFAULT_MIN_SAMPLE_COUNT), MAX_MIN_SAMPLE_COUNT),
    )

    model_id = str(target.get("model_id") or "").strip()
    request.external_model_id = model_id[:160] or None
    event_label = str(target.get("event_type") or "").strip()
    request.event_label = event_label[:160] or None
    scene = _scene_payload(target, experiment)
    request.scene_json = json.dumps(scene, ensure_ascii=False) if scene else None
    request.event_time_from = _parse_time(window.get("start_time"))
    request.event_time_to = _parse_time(window.get("end_time"))
    callback_url = str(row.outbound_callback_url or "").strip()
    request.callback_url = callback_url[:1024] or None

    package_id = str(row.source_package_id or "").strip()
    request.package_ids_json = (
        json.dumps([package_id], ensure_ascii=False) if package_id else "[]"
    )

    request.strategy_external_task_id = str(row.external_task_id)
    request.status, request.stage = _status_stage_for(row)
    if request.status in TERMINAL_STATUSES:
        request.finished_at = request.finished_at or datetime.now()
    else:
        request.finished_at = None


def _load_strategy_row(
    db: Session, external_task_id: str | None
) -> StrategyTaskIntegration | None:
    task_id = str(external_task_id or "").strip()
    if not task_id:
        return None
    return (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.external_task_id == task_id,
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .one_or_none()
    )


def _linked_request(db: Session, external_task_id: str) -> OptimizationRequest | None:
    return (
        db.query(OptimizationRequest)
        .filter(
            OptimizationRequest.strategy_external_task_id == external_task_id,
            OptimizationRequest.deleted_at.is_(None),
        )
        .first()
    )


def bridge_strategy_task_to_optimization_request(
    db: Session,
    *,
    external_task_id: str | None = None,
    row: StrategyTaskIntegration | None = None,
) -> OptimizationRequest | None:
    """Ensure one linked ``OptimizationRequest`` exists for a strategy task.

    Idempotent: a request already linked through ``strategy_external_task_id``
    (for example one created by the optimization-request pipeline itself) is
    returned untouched, so the two intakes cannot create duplicates.
    """
    strategy_row = row or _load_strategy_row(db, external_task_id)
    if strategy_row is None:
        return None
    task_id = str(strategy_row.external_task_id or "").strip()
    if not task_id:
        return None

    linked = _linked_request(db, task_id)
    if linked is not None:
        return linked

    try:
        with begin_transaction(db):
            request = (
                db.query(OptimizationRequest)
                .filter(
                    OptimizationRequest.source_system == SOURCE_SYSTEM,
                    OptimizationRequest.external_request_id == task_id,
                )
                .one_or_none()
            )
            if request is None:
                request = OptimizationRequest(
                    source_system=SOURCE_SYSTEM,
                    external_request_id=task_id,
                    request_type=_request_type_for(strategy_row.task_type),
                    created_by=(
                        int(strategy_row.created_by)
                        if strategy_row.created_by
                        else None
                    ),
                    status="WAITING",
                    stage="RECEIVED",
                    require_complete=True,
                    allow_partial=False,
                )
                db.add(request)
                db.flush()
            _apply_projection(request, strategy_row)
            db.flush()
        return request
    except IntegrityError:
        # A concurrent intake inserted the same (source, external id) pair.
        db.rollback()
        request = (
            db.query(OptimizationRequest)
            .filter(
                OptimizationRequest.source_system == SOURCE_SYSTEM,
                OptimizationRequest.external_request_id == task_id,
            )
            .one_or_none()
        )
        if request is None:
            logger.warning(
                "strategy task optimization request bridge lost race external_task_id={}",
                task_id,
            )
            return None
        with begin_transaction(db):
            _apply_projection(request, strategy_row)
        return request
