"""Operational read/write services for the edge validation task center.

This module deliberately sits beside the existing validation service.  The
older service owns creation and result hydration; this module owns queue
visibility and the small set of operator mutations needed by a high-volume
queue UI.  It does not start an SSH worker and it never exposes device
credentials.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import status
from loguru import logger
from sqlalchemy import and_, case, func, literal, or_
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_yolo_edge_validation_job
from labelu.internal.application.command.edge_queue_center import (
    EdgeQueueListQuery,
)
from labelu.internal.application.response.edge_queue_center import (
    EdgeQueueCountsResponse,
    EdgeQueueDeviceResponse,
    EdgeQueueJobListResponse,
    EdgeQueueJobResponse,
    EdgeQueueMutationResponse,
    EdgeQueueSummaryResponse,
)
from labelu.internal.application.service import yolo_edge_validation_scheduler
from labelu.internal.application.service import (
    edge_device_registry as edge_device_registry_service,
)
from labelu.internal.application.service.yolo_edge_validation_pack import (
    edge_validation_pack_ready,
)
from labelu.internal.application.service.edge_queue_timing import (
    as_utc as _timing_as_utc,
    duration_seconds as _timing_duration_seconds,
    estimate_for_row as _timing_estimate_for_row,
    execution_profile as _timing_execution_profile,
    fallback_duration_seconds as _timing_fallback_duration_seconds,
    history_durations as _timing_history_durations,
    profile_duration_medians as _timing_profile_duration_medians,
    seconds_since as _timing_seconds_since,
    timing_for_row as _timing_for_row,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.common import edge_device_registry
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob

_ACTIVE_STATUSES = ("PENDING", "QUEUED", "PROCESSING")
_RETRYABLE_STATUSES = ("FAILED", "CANCELLED", "PARTIAL_COMPLETED")
_CANCELLABLE_STATUSES = ("PENDING", "QUEUED")
_BLOCKED_QUEUE_REASONS = {
    "waiting_for_idle_device",
    "waiting_for_online_device",
    "waiting_for_environment_ready",
    "waiting_for_agent",
    "waiting_for_pack",
    "environment_not_ready",
    "agent_bootstrap_in_progress",
    # Execution fencing is a hard safety gate.  The worker deliberately
    # requeues instead of attempting remote execution when the lease/token
    # cannot be acquired, so operators must see this as blocked work.
    "execution_fencing_unavailable",
}
_PREPARING_QUEUE_REASONS = {
    "preparing_pack",
    "packing",
    "pack_preparing",
    # Written by the execution path when a task has to return to the queue
    # until its immutable sample package is committed.
    "pack_not_ready",
}
_RETRY_QUEUE_REASONS = {"retry_scheduled", "retrying"}


def _nulls_last_order(column):
    """Build a portable ascending ``NULLS LAST`` ordering.

    SQLAlchemy's ``ColumnElement.nullslast()`` emits the native ``NULLS LAST``
    clause.  PostgreSQL accepts it, but MySQL 8 does not.  The queue is backed
    by both SQLite and MySQL, so use a CASE sort key that has the same ordering
    semantics on every supported dialect.
    """

    return (
        case((column.is_(None), 1), else_=0),
        column.asc(),
    )


_PACK_PROGRESS_STAGES = ("pack", "packing", "prepare_pack", "preparing")


def _now() -> datetime:
    # Existing LabelU job timestamps are mostly naive local datetimes.  Keep
    # the value naive when writing, but normalize both forms for arithmetic.
    return datetime.now()


def _as_utc(value: datetime | None) -> datetime | None:
    return _timing_as_utc(value)


def _seconds_since(
    value: datetime | None, *, now: datetime | None = None
) -> int | None:
    return _timing_seconds_since(value, current=now)


def _decode_json(raw: Any, default: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if raw in (None, ""):
        return default
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _progress_payload(row: YoloEdgeValidationJob) -> dict[str, Any]:
    value = _decode_json(getattr(row, "progress_json", None), {})
    return value if isinstance(value, dict) else {}


def _execution_profile(row: YoloEdgeValidationJob) -> str:
    return _timing_execution_profile(row)


def _is_automatic_validation_job(row: YoloEdgeValidationJob) -> bool:
    """Return whether a row belongs to the gated automatic-training chain."""

    return (
        getattr(row, "auto_training_plan_id", None) is not None
        or getattr(row, "auto_training_round_id", None) is not None
    )


def _pack_gate_projection(row: YoloEdgeValidationJob) -> str | None:
    """Project an explicit pack gate into an operator-facing queue state.

    Filesystem validation is intentionally limited to rows the queue center
    has hydrated (the current page or a mutation target). The scheduler
    persists ``pack_not_ready``/``waiting_for_pack`` when it encounters the
    same failure during its claim scan, while this projection closes the
    read-side gap for a freshly returned row.
    """

    # Pack readiness controls dispatch only. Terminal history and an already
    # running row must retain their lifecycle status even when an old pack is
    # later removed from disk.
    persisted_status = str(getattr(row, "status", "") or "").strip().upper()
    if persisted_status not in {"PENDING", "QUEUED"}:
        return None
    payload = _decode_json(getattr(row, "result_json", None), {})
    pack = payload.get("pack") if isinstance(payload, dict) else None
    if not isinstance(pack, dict):
        return "BLOCKED" if _is_automatic_validation_job(row) else None
    state = str(pack.get("state") or "").strip().upper()
    if state in {"PREPARING", "UPLOADING"}:
        return "PREPARING"
    if not edge_validation_pack_ready(row):
        return "BLOCKED"
    return None


def _is_pack_preparing(row: YoloEdgeValidationJob) -> bool:
    reason = str(getattr(row, "queue_reason", None) or "").strip().lower()
    if reason in _PREPARING_QUEUE_REASONS:
        return True
    progress = _progress_payload(row)
    stage = str(progress.get("stage") or "").strip().lower()
    if stage in {"pack", "packing", "prepare_pack", "preparing"}:
        return True
    result = _decode_json(getattr(row, "result_json", None), {})
    pack = result.get("pack") if isinstance(result, dict) else None
    if isinstance(pack, dict) and str(pack.get("state") or "").upper() in {
        "PREPARING",
        "UPLOADING",
    }:
        return True
    return _pack_gate_projection(row) == "PREPARING"


def _reject_closed_automatic_retry(row: YoloEdgeValidationJob) -> None:
    """Fail closed before a retry can create an unclaimable queue row.

    The scheduler intentionally excludes automatic rows while the phase-one
    plan-write gate is closed.  A manual retry endpoint must enforce the same
    gate instead of resetting such a row to ``PENDING`` and leaving it there
    indefinitely.
    """

    if (
        _is_automatic_validation_job(row)
        and not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
    ):
        raise LabelUException(
            ErrorCode.CODE_57122_AUTO_TRAIN_PLAN_NOT_OPEN,
            status_code=status.HTTP_403_FORBIDDEN,
        )


def _is_blocked(row: YoloEdgeValidationJob) -> bool:
    reason = str(getattr(row, "queue_reason", None) or "").strip().lower()
    return reason in _BLOCKED_QUEUE_REASONS or _pack_gate_projection(row) == "BLOCKED"


def _display_status(row: YoloEdgeValidationJob) -> str:
    """Return the operator-facing status for a persisted queue row."""

    if _is_blocked(row):
        return "BLOCKED"
    if _is_pack_preparing(row):
        return "PREPARING"
    reason = str(getattr(row, "queue_reason", None) or "").strip().lower()
    if reason in _RETRY_QUEUE_REASONS:
        return "RETRYING"
    status_value = str(getattr(row, "status", "") or "").upper()
    return "RUNNING" if status_value == "PROCESSING" else status_value


def _display_queue_reason(row: YoloEdgeValidationJob) -> str | None:
    """Return a reason that explains a page-level pack projection."""

    reason = getattr(row, "queue_reason", None)
    projection = _pack_gate_projection(row)
    if (
        projection == "BLOCKED"
        and str(reason or "").strip().lower() not in _BLOCKED_QUEUE_REASONS
    ):
        return "waiting_for_pack"
    if (
        projection == "PREPARING"
        and str(reason or "").strip().lower() not in _PREPARING_QUEUE_REASONS
    ):
        return "pack_not_ready"
    return reason


def _sql_pack_preparing_predicate():
    """Match only the current top-level pack stage in legacy JSON rows.

    Queue history can legitimately contain an old ``pack`` entry, so patterns
    are anchored at the beginning of the JSON object instead of searching the
    entire history blob.  New rows use ``queue_reason`` and are handled by the
    durable reason predicate as well.
    """
    # ``json.dumps`` normally starts at ``{``, but legacy/manual rows can
    # contain a leading newline or spaces.  Trimming only the outside keeps
    # the predicate anchored to the current top-level stage and avoids
    # matching historical ``history[].stage`` values embedded later in JSON.
    progress = func.lower(func.coalesce(YoloEdgeValidationJob.progress_json, ""))
    # SQLite's one-argument ``ltrim`` removes spaces but not newlines/tabs;
    # replacing control whitespace first keeps this expression portable to
    # MySQL while still anchoring the match at the JSON object's top level.
    for whitespace in ("\r", "\n", "\t"):
        progress = func.replace(progress, whitespace, " ")
    progress = func.ltrim(progress)
    patterns = []
    for stage in _PACK_PROGRESS_STAGES:
        patterns.extend(
            (
                f'{{"stage":"{stage}"%',
                f'{{"stage": "{stage}"%',
            )
        )
    # Some older jobs persist the pack lifecycle only in result_json and
    # leave progress_json at the queue stage. The Python display projection
    # treats those rows as PREPARING too; keep SQL filters and aggregate
    # counts on the same side of that projection. result_json has no
    # queue-history envelope, so matching the pack marker remains portable
    # across SQLite/MySQL text columns.
    result = func.lower(func.coalesce(YoloEdgeValidationJob.result_json, ""))
    for whitespace in ("\r", "\n", "\t", " "):
        result = func.replace(result, whitespace, "")
    result_pack_preparing = or_(
        result.like('%"pack":%"state":"preparing"%'),
        result.like('%"pack":%"state":"uploading"%'),
    )
    return or_(
        *(progress.like(pattern) for pattern in patterns),
        result_pack_preparing,
    )


def _sql_compact_result_json_expression():
    result = func.lower(func.coalesce(YoloEdgeValidationJob.result_json, ""))
    for whitespace in ("\r", "\n", "\t", " "):
        result = func.replace(result, whitespace, "")
    return result


def _sql_pack_unready_predicate():
    """Match durable pack states known to be ineligible for dispatch.

    A missing file cannot be tested portably in SQL. The scheduler therefore
    writes ``waiting_for_pack`` after its filesystem gate fails; this SQL
    predicate covers rows that already carry an explicit non-READY marker and
    automatic rows that have no pack marker yet.
    """

    result = _sql_compact_result_json_expression()
    explicit_pack = result.like('%"pack":%')
    ready_pack = result.like('%"pack":%"state":"ready"%')
    automatic = or_(
        YoloEdgeValidationJob.auto_training_plan_id.isnot(None),
        YoloEdgeValidationJob.auto_training_round_id.isnot(None),
    )
    dispatch_status = func.upper(func.coalesce(YoloEdgeValidationJob.status, "")).in_(
        tuple(_ACTIVE_STATUSES[:2])
    )
    return and_(
        dispatch_status,
        or_(
            and_(automatic, ~explicit_pack),
            and_(explicit_pack, ~ready_pack, ~_sql_pack_preparing_predicate()),
        ),
    )


def _sql_not_pack_preparing_predicate():
    reason = func.lower(func.coalesce(YoloEdgeValidationJob.queue_reason, ""))
    return ~or_(
        reason.in_(tuple(sorted(_PREPARING_QUEUE_REASONS))),
        _sql_pack_preparing_predicate(),
    )


def _sql_queue_reason_expression():
    return func.lower(func.coalesce(YoloEdgeValidationJob.queue_reason, ""))


def _sql_preparing_predicate():
    reason = _sql_queue_reason_expression()
    return or_(
        reason.in_(tuple(sorted(_PREPARING_QUEUE_REASONS))),
        _sql_pack_preparing_predicate(),
    )


def _sql_blocked_predicate():
    return or_(
        _sql_queue_reason_expression().in_(tuple(sorted(_BLOCKED_QUEUE_REASONS))),
        _sql_pack_unready_predicate(),
    )


def _sql_retrying_predicate():
    return _sql_queue_reason_expression().in_(tuple(sorted(_RETRY_QUEUE_REASONS)))


def _sql_display_status_expression():
    """Project persisted rows to the same operator-facing state as Python.

    Queue reasons and legacy pack markers are projections, not independent
    persisted statuses. Keeping one CASE expression for aggregate counts and
    derived filters prevents overlap (for example a retry row whose pack is
    still preparing) from changing total or appearing in two buckets.
    The order intentionally mirrors _display_status.
    """
    status_value = func.upper(func.coalesce(YoloEdgeValidationJob.status, ""))
    return case(
        (_sql_blocked_predicate(), literal("BLOCKED")),
        (_sql_preparing_predicate(), literal("PREPARING")),
        (_sql_retrying_predicate(), literal("RETRYING")),
        (status_value == "PROCESSING", literal("RUNNING")),
        else_=status_value,
    )


def _sql_plain_queue_predicate():
    """Match only rows currently eligible for the dispatch FIFO.

    ``PENDING`` and ``QUEUED`` are the persisted states used by different
    scheduler generations. Queue reasons are a projection on top of those
    states, so rows waiting for a device/agent, retry backoff, or pack
    preparation must not consume a dispatch-lane position until that
    projection returns to the plain queue state.
    """
    status_value = func.upper(func.coalesce(YoloEdgeValidationJob.status, ""))
    return and_(
        status_value.in_(tuple(_ACTIVE_STATUSES[:2])),
        ~_sql_blocked_predicate(),
        ~_sql_preparing_predicate(),
        ~_sql_retrying_predicate(),
    )


def _explicit_pack_is_retryable(row: YoloEdgeValidationJob) -> bool:
    """Reject retries that the scheduler would silently leave unclaimable.

    Legacy manual rows without a ``pack`` object are intentionally accepted;
    the existing worker supports those historical records.  Once a row has
    an explicit pack marker, however, it must be a valid committed pack.
    """
    payload = _decode_json(getattr(row, "result_json", None), {})
    if not isinstance(payload, dict) or "pack" not in payload:
        return True
    pack = payload.get("pack")
    if not isinstance(pack, dict):
        return False
    return str(pack.get("state") or "").upper() == "READY" and bool(
        edge_validation_pack_ready(row)
    )


def _queue_position_map(rows: list[YoloEdgeValidationJob]) -> dict[int, int]:
    """Compute FIFO positions once for a page/summary instead of N+1 queries."""
    pending = [
        row
        for row in rows
        if _display_status(row) in {"PENDING", "QUEUED"}
        and getattr(row, "queued_at", None) is not None
    ]
    pending.sort(
        key=lambda row: (
            _as_utc(getattr(row, "queued_at", None))
            or datetime.max.replace(tzinfo=timezone.utc),
            int(getattr(row, "id", 0) or 0),
        )
    )
    return {int(row.id): index for index, row in enumerate(pending, start=1)}


def _queue_position_map_for_ids(
    db: Session,
    scoped_query,
    job_ids: set[int] | list[int] | tuple[int, ...],
) -> dict[int, int]:
    """Return FIFO positions for only the requested visible rows.

    The window is evaluated by the database, so a page request never
    materializes the complete pending queue in Python. Derived blocked,
    retrying, and preparation reasons plus the current top-level JSON stage
    used by older rows are excluded from dispatch FIFO.
    """
    normalized_ids = sorted({int(job_id) for job_id in job_ids if job_id})
    if not normalized_ids:
        return {}
    ranked = (
        scoped_query.with_entities(
            YoloEdgeValidationJob.id.label("job_id"),
            func.row_number()
            .over(
                order_by=(
                    *_nulls_last_order(YoloEdgeValidationJob.queued_at),
                    YoloEdgeValidationJob.id.asc(),
                )
            )
            .label("queue_position"),
        )
        .filter(
            _sql_plain_queue_predicate(),
            YoloEdgeValidationJob.queued_at.isnot(None),
        )
        .subquery()
    )
    rows = (
        db.query(ranked.c.job_id, ranked.c.queue_position)
        .filter(ranked.c.job_id.in_(normalized_ids))
        .all()
    )
    return {int(job_id): int(position) for job_id, position in rows}


def _duration_seconds(row: YoloEdgeValidationJob) -> float | None:
    return _timing_duration_seconds(row)


def _fallback_duration_seconds(profile: str) -> int:
    return _timing_fallback_duration_seconds(profile)


def _estimate_for_row(
    row: YoloEdgeValidationJob,
    *,
    median_duration: float | None,
    now: datetime,
    median_by_profile: dict[str, float] | None = None,
) -> int | None:
    return _timing_estimate_for_row(
        row,
        median_duration=median_duration,
        median_by_profile=median_by_profile,
        current=now,
    )


def _raise_not_found() -> None:
    raise LabelUException(
        code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
        status_code=status.HTTP_404_NOT_FOUND,
        message="未找到该边端验证任务或无权访问",
    )


def _raise_conflict(message: str) -> None:
    raise LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_409_CONFLICT,
        message=message,
    )


def _load_scoped_job(
    db: Session, *, job_id: int, current_user: User
) -> tuple[YoloEdgeValidationJob, Task]:
    rows = (
        crud_yolo_edge_validation_job.query_for_user(
            db,
            int(getattr(current_user, "id", 0) or 0),
            task_access_unrestricted=is_task_superuser(current_user),
        )
        .filter(YoloEdgeValidationJob.id == int(job_id))
        .first()
    )
    if rows is None:
        _raise_not_found()
    return rows


def _job_row_response(
    row: YoloEdgeValidationJob,
    task_name: str | None,
    *,
    queue_positions: dict[int, int],
    median_duration: float | None,
    now: datetime,
    median_by_profile: dict[str, float] | None = None,
) -> EdgeQueueJobResponse:
    status_value = _display_status(row)
    # Keep historical timing visible after a task leaves the active queue.
    # The shared helper falls back to ``updated_at`` for old terminal rows
    # that never persisted ``completed_at``.
    waiting, running = _timing_for_row(row, current=now)
    return EdgeQueueJobResponse(
        id=int(row.id),
        export_id=int(row.export_id),
        task_id=int(row.task_id),
        task_name=task_name,
        status=status_value,
        display_status=status_value,
        source_type=str(getattr(row, "source_type", None) or "internal"),
        source_system=getattr(row, "source_system", None),
        pipeline_origin=getattr(row, "pipeline_origin", None),
        mode=str(getattr(row, "mode", None) or "run_only"),
        execution_profile=_execution_profile(row),
        image_count=int(getattr(row, "image_count", 0) or 0),
        labeled_image_count=int(getattr(row, "labeled_image_count", 0) or 0),
        unlabeled_image_count=int(getattr(row, "unlabeled_image_count", 0) or 0),
        edge_device_id=getattr(row, "edge_device_id", None),
        edge_device_name=getattr(row, "edge_device_name", None),
        queue_reason=_display_queue_reason(row),
        # A page-level filesystem gate can project a freshly discovered bad
        # pack to BLOCKED even before the scheduler persists its reason. Do
        # not expose a stale FIFO position for that row.
        queue_position=(
            queue_positions.get(int(row.id))
            if status_value in {"PENDING", "QUEUED"}
            else None
        ),
        queued_at=getattr(row, "queued_at", None),
        assigned_at=getattr(row, "assigned_at", None),
        started_at=getattr(row, "started_at", None),
        completed_at=getattr(row, "completed_at", None),
        waiting_seconds=waiting,
        running_seconds=running,
        estimated_seconds=_estimate_for_row(
            row,
            median_duration=median_duration,
            median_by_profile=median_by_profile,
            now=now,
        ),
        error_message=getattr(row, "error_message", None),
        report_summary=getattr(row, "report_summary", None),
        progress=_progress_payload(row) or None,
        created_at=getattr(row, "created_at", None),
        updated_at=getattr(row, "updated_at", None),
    )


def _history_durations(rows: list[YoloEdgeValidationJob]) -> list[float]:
    return _timing_history_durations(rows)


def _job_entity(value: object) -> YoloEdgeValidationJob:
    """Normalize a single-entity SQLAlchemy result across query shapes."""

    if isinstance(value, YoloEdgeValidationJob):
        return value
    try:
        candidate = value[0]  # type: ignore[index]
    except (IndexError, KeyError, TypeError):
        candidate = None
    if isinstance(candidate, YoloEdgeValidationJob):
        return candidate
    raise TypeError("unexpected queue history row shape")


def _device_online_and_ready(record: dict[str, Any]) -> tuple[bool, bool, str]:
    enabled = bool(record.get("enabled", True))
    if not enabled:
        return False, False, "disabled"
    if yolo_edge_validation_scheduler._agent_bootstrap_in_progress(record):
        return False, False, "agent_bootstrap_in_progress"
    if not edge_device_registry_service.agent_bootstrap_dispatch_ready(record):
        return False, False, "waiting_for_agent"
    channel = str(record.get("validation_channel") or "ssh").strip().lower()
    if channel in {"board_agent", "board-agent", "主动拉取", "板端主动拉取"}:
        online = str(record.get("board_agent_status") or "").lower() == "online"
        checked = record.get("board_agent_last_heartbeat_at")
        try:
            heartbeat = datetime.fromisoformat(str(checked).replace("Z", "+00:00"))
            if heartbeat.tzinfo is None:
                heartbeat = heartbeat.replace(tzinfo=timezone.utc)
            ttl = max(
                10,
                int(getattr(settings, "BOARD_AGENT_HEARTBEAT_TTL_SECONDS", 120) or 120),
            )
            age_seconds = (datetime.now(timezone.utc) - heartbeat).total_seconds()
            online = online and 0 <= age_seconds <= ttl
        except (TypeError, ValueError, OverflowError):
            online = False
        ready = bool(record.get("board_agent_environment_ready"))
        return (
            online,
            online and ready,
            (
                "ready"
                if online and ready
                else ("environment_not_ready" if online else "offline")
            ),
        )
    probe_ok = (
        str(record.get("last_probe_status") or record.get("probe_status") or "").lower()
        == "ok"
    )
    # Keep the dashboard's SSH health contract identical to the scheduler:
    # an old successful probe must not make a device look dispatchable after
    # its freshness window has expired.
    if probe_ok:
        probe_record = dict(record)
        probe_record["last_probe_status"] = "ok"
        probe_record["probe_status"] = "ok"
        try:
            probe_ok = bool(
                yolo_edge_validation_scheduler._probe_is_fresh(probe_record)
            )
        except Exception:
            probe_ok = False
    report = record.get("environment_report")
    ready = (
        str(record.get("environment_status") or "") == "ready"
        and isinstance(report, dict)
        and report.get("environment_ready") is True
    )
    return (
        probe_ok,
        probe_ok and ready,
        (
            "ready"
            if probe_ok and ready
            else ("environment_not_ready" if probe_ok else "offline")
        ),
    )


def _configured_device_records() -> list[dict[str, Any]]:
    try:
        records = edge_device_registry.list_device_records()
    except Exception:
        logger.exception("读取边端设备队列摘要失败")
        return []
    return [
        item
        for item in records
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]


def _has_unavailable_active_work(
    devices: list[EdgeQueueDeviceResponse],
    processing_by_device: dict[str, list[YoloEdgeValidationJob]],
    pending_count_by_device: dict[str, int],
) -> bool:
    """Return whether active work is stranded on a non-ready device.

    A queue-wide ETA that ignores an offline lane is optimistic; including the
    lane in the maximum is also misleading because its recovery time is
    unknown.  The honest contract is therefore ``None`` until every assigned
    active lane is dispatchable again.
    """

    ready_by_device = {item.device_id: bool(item.is_ready) for item in devices}
    for device_id, rows in processing_by_device.items():
        if rows and not ready_by_device.get(device_id, False):
            return True
    return any(
        int(pending_count or 0) > 0 and not ready_by_device.get(str(device_id), False)
        for device_id, pending_count in pending_count_by_device.items()
    )


def _classify_counts(rows: list[YoloEdgeValidationJob]) -> EdgeQueueCountsResponse:
    counts = Counter()
    for row in rows:
        status_value = str(getattr(row, "status", "") or "").upper()
        if status_value in {"PENDING", "QUEUED"}:
            # Derived queue states are mutually exclusive in the operator
            # view. BLOCKED takes precedence over preparation/retry markers,
            # matching _display_status and keeping active totals honest.
            if _is_blocked(row):
                counts["blocked"] += 1
            elif (
                str(getattr(row, "queue_reason", "") or "").lower()
                in _RETRY_QUEUE_REASONS
            ):
                counts["retrying"] += 1
            elif _is_pack_preparing(row):
                counts["preparing"] += 1
            else:
                counts["queued"] += 1
        elif status_value == "PROCESSING":
            counts["processing"] += 1
        elif status_value == "FAILED":
            counts["failed"] += 1
        elif status_value == "CANCELLED":
            counts["cancelled"] += 1
        elif status_value == "COMPLETED":
            counts["completed"] += 1
        elif status_value == "PARTIAL_COMPLETED":
            counts["completed"] += 1
    active = (
        counts["preparing"]
        + counts["queued"]
        + counts["processing"]
        + counts["retrying"]
    )
    return EdgeQueueCountsResponse(
        preparing=counts["preparing"],
        queued=counts["queued"],
        processing=counts["processing"],
        retrying=counts["retrying"],
        blocked=counts["blocked"],
        failed=counts["failed"],
        completed=counts["completed"],
        cancelled=counts["cancelled"],
        active=active,
        total=len(rows),
    )


def _counts_from_status_and_active(
    status_counts: dict[str, int], active_rows: list[YoloEdgeValidationJob]
) -> EdgeQueueCountsResponse:
    """Combine SQL status totals with active-row JSON classification."""
    active_counts = _classify_counts(active_rows)
    completed = int(status_counts.get("COMPLETED", 0)) + int(
        status_counts.get("PARTIAL_COMPLETED", 0)
    )
    processing = int(status_counts.get("PROCESSING", 0))
    failed = int(status_counts.get("FAILED", 0))
    cancelled = int(status_counts.get("CANCELLED", 0))
    pending_total = int(status_counts.get("PENDING", 0)) + int(
        status_counts.get("QUEUED", 0)
    )
    preparing = active_counts.preparing
    retrying = active_counts.retrying
    queued = max(0, pending_total - preparing - retrying - active_counts.blocked)
    active = preparing + queued + processing + retrying
    return EdgeQueueCountsResponse(
        preparing=preparing,
        queued=queued,
        processing=processing,
        retrying=retrying,
        blocked=active_counts.blocked,
        failed=failed,
        completed=completed,
        cancelled=cancelled,
        active=active,
        total=sum(int(value or 0) for value in status_counts.values()),
    )


def _queue_reason_counts(scoped_query) -> dict[str, int]:
    """Aggregate pending queue reasons without loading active job rows."""
    reason_expr = func.lower(func.coalesce(YoloEdgeValidationJob.queue_reason, ""))
    rows = (
        scoped_query.with_entities(
            reason_expr.label("queue_reason"),
            func.count(YoloEdgeValidationJob.id),
        )
        .filter(YoloEdgeValidationJob.status.in_(("PENDING", "QUEUED")))
        .group_by(reason_expr)
        .all()
    )
    return {
        str(reason or "").strip().lower(): int(count or 0) for reason, count in rows
    }


def _dynamic_pack_preparing_count(scoped_query) -> int:
    """Count legacy rows whose current JSON stage is still preparing a pack."""
    reason = func.lower(func.coalesce(YoloEdgeValidationJob.queue_reason, ""))
    return int(
        scoped_query.filter(
            YoloEdgeValidationJob.status.in_(("PENDING", "QUEUED")),
            _sql_pack_preparing_predicate(),
            ~reason.in_(
                tuple(
                    sorted(
                        _PREPARING_QUEUE_REASONS
                        | _RETRY_QUEUE_REASONS
                        | _BLOCKED_QUEUE_REASONS
                    )
                )
            ),
        )
        .with_entities(func.count(YoloEdgeValidationJob.id))
        .scalar()
        or 0
    )


def _counts_from_status_and_reasons(
    status_counts: dict[str, int],
    reason_counts: dict[str, int],
    *,
    dynamic_preparing: int = 0,
) -> EdgeQueueCountsResponse:
    """Build header counts from SQL ``GROUP BY`` results.

    Queue reason is the durable scheduler classification.  A bounded SQL
    predicate also accounts for legacy rows whose current JSON stage is still
    preparing a pack.
    """
    preparing = sum(
        int(reason_counts.get(reason, 0)) for reason in _PREPARING_QUEUE_REASONS
    ) + int(dynamic_preparing or 0)
    retrying = sum(
        int(reason_counts.get(reason, 0)) for reason in ("retry_scheduled", "retrying")
    )
    blocked = sum(
        int(reason_counts.get(reason, 0)) for reason in _BLOCKED_QUEUE_REASONS
    )
    pending_total = int(status_counts.get("PENDING", 0)) + int(
        status_counts.get("QUEUED", 0)
    )
    queued = max(0, pending_total - preparing - retrying - blocked)
    processing = int(status_counts.get("PROCESSING", 0))
    failed = int(status_counts.get("FAILED", 0))
    completed = int(status_counts.get("COMPLETED", 0)) + int(
        status_counts.get("PARTIAL_COMPLETED", 0)
    )
    cancelled = int(status_counts.get("CANCELLED", 0))
    active = preparing + queued + processing + retrying
    return EdgeQueueCountsResponse(
        preparing=preparing,
        queued=queued,
        processing=processing,
        retrying=retrying,
        blocked=blocked,
        failed=failed,
        completed=completed,
        cancelled=cancelled,
        active=active,
        total=sum(int(value or 0) for value in status_counts.values()),
    )


def _counts_from_display_status_rows(
    rows: list[tuple[object, object]],
) -> EdgeQueueCountsResponse:
    """Build mutually exclusive header buckets from projected SQL states."""
    counts = Counter()
    for display_status, value in rows:
        normalized = str(display_status or "").upper()
        amount = int(value or 0)
        if normalized in {"PENDING", "QUEUED"}:
            counts["queued"] += amount
        elif normalized in {"PROCESSING", "RUNNING"}:
            counts["processing"] += amount
        elif normalized == "PREPARING":
            counts["preparing"] += amount
        elif normalized == "RETRYING":
            counts["retrying"] += amount
        elif normalized == "BLOCKED":
            counts["blocked"] += amount
        elif normalized == "FAILED":
            counts["failed"] += amount
        elif normalized in {"COMPLETED", "PARTIAL_COMPLETED"}:
            counts["completed"] += amount
        elif normalized == "CANCELLED":
            counts["cancelled"] += amount
        # Unknown persisted states still contribute to total below, but are
        # intentionally not folded into an operational bucket.
    active = (
        counts["preparing"]
        + counts["queued"]
        + counts["processing"]
        + counts["retrying"]
    )
    return EdgeQueueCountsResponse(
        preparing=counts["preparing"],
        queued=counts["queued"],
        processing=counts["processing"],
        retrying=counts["retrying"],
        blocked=counts["blocked"],
        failed=counts["failed"],
        completed=counts["completed"],
        cancelled=counts["cancelled"],
        active=active,
        total=sum(int(value or 0) for _status, value in rows),
    )


def _queue_display_status_counts(scoped_query) -> EdgeQueueCountsResponse:
    """Aggregate the same derived status used by list filtering."""
    display_status = _sql_display_status_expression()
    rows = (
        scoped_query.with_entities(
            display_status.label("display_status"),
            func.count(YoloEdgeValidationJob.id),
        )
        .group_by(display_status)
        .all()
    )
    return _counts_from_display_status_rows(rows)


def _global_processing_heads(
    db: Session, *, legacy_active_device_id: str
) -> list[tuple[YoloEdgeValidationJob, str]]:
    """Return one PROCESSING row per device using a SQL window rank.

    Device occupancy is global (not task-scoped), but only the first row per
    device is needed for the dashboard.  Legacy rows without an assigned
    device are attributed to the current registry device for compatibility.
    """
    raw_device = func.nullif(YoloEdgeValidationJob.edge_device_id, "")
    device_expr = (
        func.coalesce(raw_device, literal(legacy_active_device_id))
        if legacy_active_device_id
        else raw_device
    )
    ranked = (
        db.query(
            YoloEdgeValidationJob.id.label("job_id"),
            device_expr.label("device_id"),
            func.row_number()
            .over(
                partition_by=device_expr,
                order_by=(
                    *_nulls_last_order(YoloEdgeValidationJob.assigned_at),
                    YoloEdgeValidationJob.id.asc(),
                ),
            )
            .label("device_rank"),
        )
        .filter(YoloEdgeValidationJob.status == "PROCESSING")
        .filter(device_expr.isnot(None))
        .subquery()
    )
    return (
        db.query(YoloEdgeValidationJob, ranked.c.device_id)
        .join(
            ranked,
            ranked.c.job_id == YoloEdgeValidationJob.id,
        )
        .filter(ranked.c.device_rank == 1)
        .all()
    )


def _pending_device_heads(scoped_query) -> list[tuple[YoloEdgeValidationJob, str, int]]:
    """Return each assigned device's FIFO head and SQL-computed queue count."""
    raw_device = func.nullif(YoloEdgeValidationJob.edge_device_id, "")
    ranked = (
        scoped_query.with_entities(
            YoloEdgeValidationJob.id.label("job_id"),
            raw_device.label("device_id"),
            func.count(YoloEdgeValidationJob.id)
            .over(partition_by=raw_device)
            .label("pending_count"),
            func.row_number()
            .over(
                partition_by=raw_device,
                order_by=(
                    *_nulls_last_order(YoloEdgeValidationJob.queued_at),
                    YoloEdgeValidationJob.id.asc(),
                ),
            )
            .label("device_rank"),
        )
        .filter(
            _sql_plain_queue_predicate(),
            YoloEdgeValidationJob.queued_at.isnot(None),
            raw_device.isnot(None),
        )
        .subquery()
    )
    head_rows = (
        scoped_query.session.query(
            YoloEdgeValidationJob,
            ranked.c.device_id,
            ranked.c.pending_count,
        )
        .join(ranked, ranked.c.job_id == YoloEdgeValidationJob.id)
        .filter(ranked.c.device_rank == 1)
        .all()
    )
    return [
        (row, str(device_id), int(pending_count or 0))
        for row, device_id, pending_count in head_rows
        if device_id
    ]


def _unassigned_pending_preview(
    scoped_query, *, limit: int
) -> tuple[int, list[YoloEdgeValidationJob]]:
    """Count unassigned queue work and fetch only its display heads."""
    raw_device = func.nullif(YoloEdgeValidationJob.edge_device_id, "")
    base = scoped_query.filter(
        _sql_plain_queue_predicate(),
        YoloEdgeValidationJob.queued_at.isnot(None),
        raw_device.is_(None),
    )
    total = int(base.with_entities(func.count(YoloEdgeValidationJob.id)).scalar() or 0)
    if not limit or not total:
        return total, []
    rows = (
        base.with_entities(YoloEdgeValidationJob)
        .order_by(
            *_nulls_last_order(YoloEdgeValidationJob.queued_at),
            YoloEdgeValidationJob.id.asc(),
        )
        .limit(int(limit))
        .all()
    )
    return total, [
        row if isinstance(row, YoloEdgeValidationJob) else row[0] for row in rows
    ]


def _apply_queue_status_filter(query, status_value: str | None):
    """Apply display statuses without confusing derived states with DB states.

    The task-center UI exposes pack preparation, retrying, and ``RUNNING`` as
    operator-facing states, while the persisted job table stores those rows as
    ``PENDING``/``QUEUED``/``PROCESSING`` plus a queue reason or progress JSON.
    Keeping this translation here makes totals and pagination agree with the
    row labels returned by the same endpoint.
    """
    normalized = str(status_value or "").strip().upper()
    if not normalized or normalized == "ALL":
        return query
    if normalized in {"RUNNING", "PREPARING", "RETRYING", "BLOCKED"}:
        # Use the same precedence as _display_status so a row carrying both a
        # retry reason and a preparing pack is returned only by PREPARING.
        return query.filter(_sql_display_status_expression() == normalized)
    if normalized in {"PENDING", "QUEUED"}:
        # ``PENDING``/``QUEUED`` are the plain dispatch lane. Preparing, retrying, and
        # blocked rows remain persisted as PENDING/QUEUED for scheduler
        # compatibility, but their operator-facing status is different and
        # must not leak into this filter.  The JSON predicate keeps legacy
        # pack-preparation rows consistent with newer queue_reason markers.
        return query.filter(_sql_plain_queue_predicate())
    return query.filter(YoloEdgeValidationJob.status == normalized)


def list_queue_jobs(
    db: Session,
    *,
    current_user: User,
    query: EdgeQueueListQuery | None = None,
    # Keyword arguments are kept for callers that predate the query model.
    export_id: int | None = None,
    task_id: int | None = None,
    keyword: str | None = None,
    device_id: str | None = None,
    edge_device_id: str | None = None,
    source_type: str | None = None,
    source_system: str | None = None,
    pipeline_origin: str | None = None,
    status_value: str | None = None,
    status: str | None = None,
    active_only: bool = False,
    page: int = 0,
    size: int = 20,
) -> EdgeQueueJobListResponse:
    """Return a bounded, server-paginated queue view.

    ``edge_device_id`` and ``device_id`` are accepted as aliases so the
    frontend can use the same name as the device API.
    """
    if query is not None:
        export_id = query.export_id
        task_id = query.task_id
        keyword = query.keyword
        device_id = query.device_id
        source_type = query.source_type
        source_system = query.source_system
        pipeline_origin = query.pipeline_origin
        status_value = query.status
        active_only = query.active_only
        page = query.page
        size = query.size
    device_id = device_id or edge_device_id
    status_value = status_value or status
    if status_value is not None:
        status_value = str(status_value).strip().upper() or None
    page = max(0, int(page or 0))
    size = min(100, max(1, int(size or 20)))
    # Statuses such as PREPARING/RETRYING are derived from queue metadata, so
    # do not pass them through CRUD's exact persisted-status predicate first.
    persisted_status = (
        None
        if str(status_value or "").strip().upper()
        in {
            "ALL",
            "PREPARING",
            "RETRYING",
            "BLOCKED",
            "RUNNING",
            "PENDING",
            "QUEUED",
        }
        else status_value
    )
    q = crud_yolo_edge_validation_job.query_for_user(
        db,
        int(getattr(current_user, "id", 0) or 0),
        export_id=export_id,
        task_id=task_id,
        keyword=keyword,
        status=persisted_status,
        edge_device_id=device_id,
        source_type=source_type,
        source_system=source_system,
        pipeline_origin=pipeline_origin,
        active_only=active_only,
        task_access_unrestricted=is_task_superuser(current_user),
    )
    q = _apply_queue_status_filter(q, status_value)
    total = int(q.count())
    rows = (
        q.order_by(
            *_nulls_last_order(YoloEdgeValidationJob.queued_at),
            YoloEdgeValidationJob.id.asc(),
        )
        .offset(page * size)
        .limit(size)
        .all()
    )
    # A page needs only one extra bounded query for historical duration.
    history_scope = crud_yolo_edge_validation_job.query_for_user(
        db,
        int(getattr(current_user, "id", 0) or 0),
        export_id=export_id,
        task_id=task_id,
        task_access_unrestricted=is_task_superuser(current_user),
    )
    history_rows = [
        _job_entity(item)
        for item in history_scope
        .filter(
            YoloEdgeValidationJob.status.in_(("COMPLETED", "PARTIAL_COMPLETED")),
            YoloEdgeValidationJob.started_at.isnot(None),
            YoloEdgeValidationJob.completed_at.isnot(None),
        )
        .order_by(YoloEdgeValidationJob.completed_at.desc())
        .limit(200)
        .all()
    ]
    durations = _history_durations(history_rows)
    median_by_profile = _timing_profile_duration_medians(history_rows)
    median_duration = statistics.median(durations) if durations else None
    # Queue position is relative to the user's full visible queue, not the
    # current page or an optional device/source filter.  Compute the rank in
    # SQL and fetch only ids present on this page; loading every pending row
    # makes a high-volume queue endpoint degrade linearly with history.
    position_scope = crud_yolo_edge_validation_job.query_for_user(
        db,
        int(getattr(current_user, "id", 0) or 0),
        export_id=export_id,
        task_id=task_id,
        task_access_unrestricted=is_task_superuser(current_user),
    )
    positions = _queue_position_map_for_ids(
        db,
        position_scope,
        {int(row.id) for row, _task in rows},
    )
    now = _now()
    return EdgeQueueJobListResponse(
        items=[
            _job_row_response(
                row,
                task.name,
                queue_positions=positions,
                median_duration=median_duration,
                now=now,
                median_by_profile=median_by_profile,
            )
            for row, task in rows
        ],
        total=total,
        page=page,
        size=size,
    )


def get_queue_summary(
    db: Session,
    *,
    current_user: User,
    export_id: int | None = None,
    task_id: int | None = None,
) -> EdgeQueueSummaryResponse:
    """Build one consistent snapshot for the queue dashboard.

    ``export_id`` and ``task_id`` optionally narrow the job view for a
    model/task-context entry point.  With neither filter, the result remains
    the global operational snapshot used by the task-center route.

    All job queries reuse the same visibility scope, while large collections
    are reduced in SQL (``GROUP BY``, ``MIN`` and window ranks).  Only device
    heads, a bounded history sample and a bounded unassigned preview are
    hydrated, so periodic polling does not scale with the full job history.
    """
    scoped_query = crud_yolo_edge_validation_job.query_for_user(
        db,
        int(getattr(current_user, "id", 0) or 0),
        export_id=export_id,
        task_id=task_id,
        task_access_unrestricted=is_task_superuser(current_user),
    )
    now = _now()
    # Aggregate the projected operator state used by the list filters. This
    # keeps queued/preparing/retrying/blocked mutually exclusive and makes the
    # summary total equal the unfiltered list total even for legacy rows whose
    # pack state lives only in result_json.
    counts = _queue_display_status_counts(scoped_query)

    # The oldest queue timestamp is a scalar aggregate, not a Python min over
    # every pending row. Derived blocked/retrying/preparing rows are
    # intentionally not part of dispatch FIFO.
    pending_scope = scoped_query.filter(
        _sql_plain_queue_predicate(),
        YoloEdgeValidationJob.queued_at.isnot(None),
    )
    oldest = pending_scope.with_entities(
        func.min(YoloEdgeValidationJob.queued_at)
    ).scalar()

    history_rows = (
        scoped_query.with_entities(YoloEdgeValidationJob)
        .filter(
            YoloEdgeValidationJob.status.in_(("COMPLETED", "PARTIAL_COMPLETED")),
            YoloEdgeValidationJob.started_at.isnot(None),
            YoloEdgeValidationJob.completed_at.isnot(None),
        )
        .order_by(YoloEdgeValidationJob.completed_at.desc())
        .limit(200)
        .all()
    )
    history_rows = [
        item[0] if isinstance(item, tuple) else item for item in history_rows
    ]
    durations = _history_durations(history_rows)
    median_by_profile = _timing_profile_duration_medians(history_rows)
    median_duration = statistics.median(durations) if durations else None
    confidence = "high" if len(durations) >= 10 else ("medium" if durations else "low")
    basis = (
        f"最近 {len(durations)} 条已完成边端任务中位耗时"
        if durations
        else "暂无同类历史任务，使用模式默认估算"
    )

    records = _configured_device_records()
    try:
        legacy_active_device_id = str(
            edge_device_registry.get_active_device_id() or ""
        ).strip()
    except Exception:
        legacy_active_device_id = ""

    # Device occupancy is global, while job ids are only revealed when the
    # current user can see the corresponding task.  A window rank keeps this
    # query bounded to one row per device.
    processing_by_device: dict[str, list[YoloEdgeValidationJob]] = defaultdict(list)
    for row, device_id in _global_processing_heads(
        db, legacy_active_device_id=legacy_active_device_id
    ):
        device_key = str(device_id or "").strip()
        if device_key:
            processing_by_device[device_key].append(row)

    processing_ids = {
        int(row.id)
        for rows_for_device in processing_by_device.values()
        for row in rows_for_device
    }
    visible_ids: set[int] = set()
    if processing_ids:
        visible_rows = (
            scoped_query.with_entities(YoloEdgeValidationJob.id)
            .filter(YoloEdgeValidationJob.id.in_(sorted(processing_ids)))
            .all()
        )
        visible_ids = {int(item[0]) for item in visible_rows}

    # One FIFO head and one SQL count per assigned device.  The complete lane
    # is represented by ``pending_count``; only its head row is hydrated for
    # ETA and preview rendering.
    pending_by_device: dict[str, YoloEdgeValidationJob] = {}
    pending_count_by_device: dict[str, int] = {}
    for row, device_id, pending_count in _pending_device_heads(scoped_query):
        pending_by_device[device_id] = row
        pending_count_by_device[device_id] = pending_count

    ready_ids = [
        str(record.get("id") or "").strip()
        for record in records
        if _device_online_and_ready(record)[1]
    ]
    unassigned_total, unassigned_preview = _unassigned_pending_preview(
        scoped_query, limit=len(ready_ids)
    )

    position_ids = {
        int(pending_by_device_row.id)
        for pending_by_device_row in pending_by_device.values()
    }
    position_ids.update(int(row.id) for row in unassigned_preview)
    positions = _queue_position_map_for_ids(db, scoped_query, position_ids)

    devices: list[EdgeQueueDeviceResponse] = []
    for record in records:
        device_id = str(record.get("id") or "").strip()
        online, ready, health_status = _device_online_and_ready(record)
        current = (processing_by_device.get(device_id) or [None])[0]
        next_row = pending_by_device.get(device_id)
        assigned_count = pending_count_by_device.get(device_id, 0)
        devices.append(
            EdgeQueueDeviceResponse(
                device_id=device_id,
                device_name=str(record.get("name") or ""),
                status="busy" if current else health_status,
                is_online=online,
                is_ready=ready,
                is_busy=bool(current),
                current_job_id=(
                    int(current.id)
                    if current and int(current.id) in visible_ids
                    else None
                ),
                current_job_status=(
                    str(current.status)
                    if current and int(current.id) in visible_ids
                    else ("PROCESSING" if current else None)
                ),
                current_job_started_at=(
                    getattr(current, "started_at", None)
                    if current and int(current.id) in visible_ids
                    else None
                ),
                next_job_id=int(next_row.id) if next_row else None,
                next_queue_position=(
                    positions.get(int(next_row.id)) if next_row else None
                ),
                pending_count=assigned_count,
                eta_seconds=None,
            )
        )

    # Keep orphan/stale assignments visible even when a device was removed
    # from the registry.
    known_device_ids = {item.device_id for item in devices}
    unknown_device_ids = (
        set(processing_by_device) | set(pending_by_device)
    ) - known_device_ids
    for device_id in sorted(unknown_device_ids):
        current = (processing_by_device.get(device_id) or [None])[0]
        next_row = pending_by_device.get(device_id)
        devices.append(
            EdgeQueueDeviceResponse(
                device_id=device_id,
                device_name="未注册设备",
                status="busy" if current else "unknown",
                is_online=False,
                is_ready=False,
                is_busy=bool(current),
                current_job_id=(
                    int(current.id)
                    if current and int(current.id) in visible_ids
                    else None
                ),
                current_job_status=(
                    str(current.status)
                    if current and int(current.id) in visible_ids
                    else ("PROCESSING" if current else None)
                ),
                current_job_started_at=(
                    getattr(current, "started_at", None)
                    if current and int(current.id) in visible_ids
                    else None
                ),
                next_job_id=int(next_row.id) if next_row else None,
                next_queue_position=(
                    positions.get(int(next_row.id)) if next_row else None
                ),
                pending_count=pending_count_by_device.get(device_id, 0),
                eta_seconds=None,
            )
        )

    # Assign only queue heads to ready lanes for display.  The remaining
    # unassigned count is distributed arithmetically so a million-row queue
    # never becomes a million-element Python list.
    preview_by_device: dict[str, list[YoloEdgeValidationJob]] = defaultdict(list)
    allocated_counts: dict[str, int] = {device_id: 0 for device_id in ready_ids}
    for row in unassigned_preview:
        if not ready_ids:
            break
        device_id = min(
            ready_ids,
            key=lambda value: (
                pending_count_by_device.get(value, 0)
                + allocated_counts.get(value, 0)
                + (1 if processing_by_device.get(value) else 0),
                value,
            ),
        )
        preview_by_device[device_id].append(row)
        allocated_counts[device_id] += 1

    remaining = max(0, int(unassigned_total) - len(unassigned_preview))
    if remaining and ready_ids:
        ordered_ready = sorted(
            ready_ids,
            key=lambda value: (
                pending_count_by_device.get(value, 0) + allocated_counts.get(value, 0),
                value,
            ),
        )
        base, remainder = divmod(remaining, len(ordered_ready))
        for index, device_id in enumerate(ordered_ready):
            allocated_counts[device_id] += base + (1 if index < remainder else 0)

    for item in devices:
        allocation = allocated_counts.get(item.device_id, 0)
        if allocation:
            item.pending_count += allocation
        if item.next_job_id is None and preview_by_device.get(item.device_id):
            row = preview_by_device[item.device_id][0]
            item.next_job_id = int(row.id)
            item.next_queue_position = positions.get(int(row.id))

    unavailable_active_work = _has_unavailable_active_work(
        devices,
        processing_by_device,
        pending_count_by_device,
    )
    lane_workloads: list[float] = []
    for item in devices:
        current = (processing_by_device.get(item.device_id) or [None])[0]
        item.eta_seconds = (
            _estimate_for_row(
                current,
                median_duration=median_duration,
                median_by_profile=median_by_profile,
                now=now,
            )
            if current and item.is_ready
            else (0 if item.is_ready else None)
        )
        if not item.is_ready:
            # A non-ready lane has no trustworthy recovery time. Its work is
            # represented in the queue counts, but must not look immediately
            # available in the device card or distort a ready-lane estimate.
            continue
        assigned_count = pending_count_by_device.get(item.device_id, 0)
        head = pending_by_device.get(item.device_id)
        lane_workload = float(item.eta_seconds or 0)
        if head and assigned_count:
            lane_workload += (
                float(
                    _estimate_for_row(
                        head,
                        median_duration=median_duration,
                        median_by_profile=median_by_profile,
                        now=now,
                    )
                    or 0
                )
                * assigned_count
            )
        preview_rows = preview_by_device.get(item.device_id, [])
        lane_workload += sum(
            float(
                _estimate_for_row(
                    row,
                    median_duration=median_duration,
                    median_by_profile=median_by_profile,
                    now=now,
                )
                or 0
            )
            for row in preview_rows
        )
        extra_count = max(0, item.pending_count - assigned_count - len(preview_rows))
        lane_workload += extra_count * _fallback_duration_seconds("quick")
        lane_workloads.append(lane_workload)

    ready_count = sum(1 for item in devices if item.is_ready)
    if counts.preparing:
        # A pack-preparing row has not entered a dispatch lane yet.  Its
        # preparation duration is not represented by the execution history,
        # so reporting the ready-lane workload (often zero) would be an
        # optimistic and misleading queue-clear ETA.
        confidence = "low"
        basis = "存在样本包准备中的任务，准备完成时间未知"
        total_eta = None
    elif unavailable_active_work:
        confidence = "low"
        basis = "存在离线或未就绪设备承载活动任务，恢复后才能估算"
        total_eta = None
    elif ready_count:
        total_eta = max(lane_workloads, default=0.0)
    else:
        total_eta = 0 if counts.active == 0 else None
        if counts.active:
            confidence = "low"
            basis = "暂无就绪设备，暂不估算清空时间"
    return EdgeQueueSummaryResponse(
        generated_at=now,
        counts=counts,
        oldest_queued_at=oldest,
        oldest_wait_seconds=_seconds_since(oldest, now=now),
        estimated_clear_seconds=(
            max(0, int(round(total_eta))) if total_eta is not None else None
        ),
        eta_confidence=confidence,
        eta_basis=basis,
        completed_duration_samples=len(durations),
        devices=devices,
    )


def cancel_job(
    db: Session,
    *,
    job_id: int,
    current_user: User,
    reason: str | None = None,
) -> EdgeQueueMutationResponse:
    """Cancel a queued job atomically.

    Processing cancellation is intentionally rejected until the Board Agent
    stop/ack protocol is wired.  Marking a running row cancelled here would
    let the remote evaluator continue writing results as an orphan.
    """
    row, _task = _load_scoped_job(db, job_id=job_id, current_user=current_user)
    status_value = str(row.status or "")
    if status_value == "PROCESSING":
        _raise_conflict("任务正在边端执行，需先由 Agent 确认停止后才能取消")
    if status_value not in _CANCELLABLE_STATUSES:
        _raise_conflict(f"任务当前状态 {status_value} 不允许取消")
    now = _now()
    reason_text = str(reason or "操作员取消").strip()[:500]
    with begin_transaction(db):
        updated = (
            db.query(YoloEdgeValidationJob)
            .filter(
                YoloEdgeValidationJob.id == int(job_id),
                YoloEdgeValidationJob.status.in_(_CANCELLABLE_STATUSES),
            )
            .update(
                {
                    YoloEdgeValidationJob.status: "CANCELLED",
                    YoloEdgeValidationJob.queue_reason: "cancelled",
                    YoloEdgeValidationJob.queue_lease_owner: None,
                    YoloEdgeValidationJob.queue_lease_expires_at: None,
                    YoloEdgeValidationJob.lease_expires_at: None,
                    YoloEdgeValidationJob.execution_lease_owner: None,
                    YoloEdgeValidationJob.execution_lease_expires_at: None,
                    # Fence a stale worker that may still hold an old
                    # execution token after a dispatcher crash.
                    YoloEdgeValidationJob.execution_fencing_token: (
                        func.coalesce(YoloEdgeValidationJob.execution_fencing_token, 0)
                        + 1
                    ),
                    YoloEdgeValidationJob.assigned_at: None,
                    YoloEdgeValidationJob.edge_device_id: None,
                    YoloEdgeValidationJob.edge_device_name: None,
                    YoloEdgeValidationJob.edge_host: None,
                    YoloEdgeValidationJob.edge_port: None,
                    YoloEdgeValidationJob.edge_user: None,
                    YoloEdgeValidationJob.started_at: None,
                    YoloEdgeValidationJob.completed_at: now,
                    YoloEdgeValidationJob.error_message: None,
                    YoloEdgeValidationJob.report_summary: reason_text,
                    YoloEdgeValidationJob.progress_json: json.dumps(
                        {
                            "stage": "cancelled",
                            "message": "边端任务已取消",
                            "detail": {"reason": reason_text},
                            "updated_at": now.isoformat(timespec="seconds"),
                        },
                        ensure_ascii=False,
                    ),
                    YoloEdgeValidationJob.updated_at: now,
                },
                synchronize_session="fetch",
            )
        )
        if updated != 1:
            _raise_conflict("任务状态已变化，请刷新后重试")
    # A cancelled head can uncover the next FIFO row immediately.
    yolo_edge_validation_scheduler.schedule_edge_validation_dispatch()
    return EdgeQueueMutationResponse(
        job_id=int(job_id), status="CANCELLED", message="边端任务已取消"
    )


def retry_job(
    db: Session,
    *,
    job_id: int,
    current_user: User,
    reason: str | None = None,
) -> EdgeQueueMutationResponse:
    """Requeue a terminal edge job while retaining its immutable pack/request."""
    row, _task = _load_scoped_job(db, job_id=job_id, current_user=current_user)
    status_value = str(row.status or "")
    if status_value not in _RETRYABLE_STATUSES:
        _raise_conflict(f"任务当前状态 {status_value} 不允许重试")
    _reject_closed_automatic_retry(row)
    if not _explicit_pack_is_retryable(row):
        _raise_conflict("边端验证样本包不可用，请重新准备样本包后再重试")
    now = _now()
    reason_text = str(reason or "操作员重新排队").strip()[:500]
    progress = {
        "stage": "queue",
        "message": "任务已重新排队，等待边端设备",
        "detail": {"queue_reason": "retry_scheduled", "reason": reason_text},
        "updated_at": now.isoformat(timespec="seconds"),
        "history": [
            {
                "stage": "queue",
                "message": "任务已重新排队，等待边端设备",
                "detail": {"queue_reason": "retry_scheduled", "reason": reason_text},
                "at": now.isoformat(timespec="seconds"),
            }
        ],
    }
    with begin_transaction(db):
        current = (
            db.query(YoloEdgeValidationJob)
            .filter(
                YoloEdgeValidationJob.id == int(job_id),
                YoloEdgeValidationJob.status.in_(_RETRYABLE_STATUSES),
            )
            .with_for_update()
            .first()
        )
        if current is None:
            _raise_conflict("任务状态已变化，请刷新后重试")
        # The plan-write switch can change between the optimistic read and
        # the transaction. Re-check while holding the row lock so a closed
        # automatic chain can never be reset to a scheduler-invisible state.
        _reject_closed_automatic_retry(current)
        # The pack can be replaced or removed after the optimistic check
        # above. Revalidate while holding the row lock so a retry cannot
        # enqueue a job whose immutable input became unusable in the gap.
        if not _explicit_pack_is_retryable(current):
            _raise_conflict("边端验证样本包不可用，请重新准备样本包后再重试")
        crud_yolo_edge_validation_job.update(
            db,
            current,
            status="PENDING",
            queued_at=now,
            queue_reason="retry_scheduled",
            assigned_at=None,
            lease_expires_at=None,
            queue_lease_owner=None,
            queue_lease_expires_at=None,
            execution_lease_owner=None,
            execution_lease_expires_at=None,
            edge_device_id=None,
            edge_device_name=None,
            edge_host=None,
            edge_port=None,
            edge_user=None,
            started_at=None,
            completed_at=None,
            error_message=None,
            report_summary=reason_text,
            progress_json=json.dumps(progress, ensure_ascii=False),
            updated_at=now,
            execution_fencing_token=int(
                getattr(current, "execution_fencing_token", 0) or 0
            )
            + 1,
        )
    # Enqueue after commit so a scheduler never observes a half-reset row.
    yolo_edge_validation_scheduler.enqueue_edge_validation_job(int(job_id))
    # ``enqueue_edge_validation_job`` intentionally short-circuits already
    # queued rows; retry has just created such a row, so explicitly request a
    # dispatch pass here.
    yolo_edge_validation_scheduler.schedule_edge_validation_dispatch()
    return EdgeQueueMutationResponse(
        job_id=int(job_id), status="PENDING", message="边端任务已重新排队"
    )


# Explicit aliases make the route adapter self-documenting and preserve a
# small compatibility surface for callers that use ``queue_*`` terminology.
queue_summary = get_queue_summary
cancel_queue_job = cancel_job
retry_queue_job = retry_job
list_jobs = list_queue_jobs
