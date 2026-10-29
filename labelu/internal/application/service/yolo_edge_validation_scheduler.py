"""FIFO scheduler for distributing edge validation jobs across BM1688 devices."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from loguru import logger
from sqlalchemy import text

from labelu.internal.adapter.persistence import crud_yolo_edge_validation_job
from labelu.internal.common import edge_device_registry
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, engine, session_scope
from labelu.internal.common.edge_ssh_probe import probe_edge_ssh_target
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob

# The scheduler reads records through the common registry adapter, while the
# automatic-enrollment gate is owned by the application service.  Import the
# small projection helper separately so the UI and claim path share one
# fail-closed decision without creating a module-level registry alias clash.
from labelu.internal.application.service import (
    edge_device_registry as edge_device_registry_service,
)

_DISPATCH_LOCK = threading.Lock()
_SCHEDULE_LOCK = threading.Lock()
_DISPATCH_RUNNING = False
_DISPATCH_REQUESTED = False
_MYSQL_LOCK_NAME = "labelu:yolo-edge-validation-dispatch"

# Both values are persisted by supported producers. ``PENDING`` is the
# historical/manual queue state while ``QUEUED`` is used by newer queue
# integrations. They share one dispatch lane and therefore must stay in sync
# across candidate selection, claim fencing, and queue-position reporting.
_DISPATCHABLE_QUEUE_STATUSES = ("PENDING", "QUEUED")

# A bootstrap changes the remote execution contract (and may switch an SSH
# device to the pull-based Board Agent channel). Treat every persisted
# lifecycle state between queueing and the first heartbeat as unavailable so a
# scheduler pass cannot claim the device while its connection is being
# replaced. The marker is persisted in the device registry so other
# application workers observe the same guard.
_AGENT_BOOTSTRAP_ACTIVE_STATUSES = frozenset(
    {
        "QUEUED",
        "PREFLIGHT",
        "RUNTIME_PROVISION",
        "UPLOADING",
        "INSTALLING",
        "STARTING",
        "WAITING_HEARTBEAT",
    }
)


def _now() -> datetime:
    return datetime.now()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _device_sort_key(record: dict) -> tuple[datetime, str]:
    used_at = _parse_iso_datetime(record.get("last_used_at"))
    return used_at or datetime.min.replace(tzinfo=timezone.utc), str(
        record.get("id") or ""
    )


def _configured_devices() -> list[dict]:
    records = []
    for record in edge_device_registry.list_device_records():
        if not isinstance(record, dict) or not bool(record.get("enabled", True)):
            continue
        device_id = str(record.get("id") or "").strip()
        cfg = edge_device_registry.load_device_config(device_id)
        if not device_id:
            continue
        channel = str(record.get("validation_channel") or "ssh").strip().lower()
        if channel == "board_agent":
            try:
                from labelu.internal.application.service.board_agent import (
                    unified_board_agent_enabled,
                )

                if not unified_board_agent_enabled():
                    continue
            except Exception:
                logger.exception("读取板端主动拉取开关失败 device_id={}", device_id)
                continue
        else:
            if not str(cfg.get("host") or "").strip():
                continue
            if not str(cfg.get("user") or "").strip() or not (
                str(cfg.get("password") or "").strip()
                or str(cfg.get("private_key_path") or "").strip()
            ):
                continue
        records.append(record)
    return sorted(records, key=_device_sort_key)


def _uses_board_agent(record: dict | None) -> bool:
    """Only explicit pull-channel devices bypass the SSH compatibility runner."""
    if not isinstance(record, dict):
        return False
    return str(record.get("validation_channel") or "ssh").strip().lower() in {
        "board_agent",
        "board-agent",
        "主动拉取",
        "板端主动拉取",
    }


def _agent_bootstrap_in_progress(record: dict | None) -> bool:
    """Return whether Agent lifecycle work temporarily owns a device.

    Newer registry records store the state at the top level and mirror it
    under ``environment_report`` for older readers. Accept both forms and the
    explicit environment transition marker so mixed-version workers fail
    closed while an installation is in flight.
    """

    if not isinstance(record, dict):
        return False
    state = record.get("agent_bootstrap")
    report = record.get("environment_report")
    nested_state = report.get("agent_bootstrap") if isinstance(report, dict) else None
    if not isinstance(state, dict) or not str(state.get("status") or "").strip():
        state = nested_state
    status = (
        str(state.get("status") or "").strip().upper()
        if isinstance(state, dict)
        else ""
    )
    if status in _AGENT_BOOTSTRAP_ACTIVE_STATUSES:
        return True
    return (
        str(record.get("environment_code") or "").strip().lower()
        == "agent_bootstrap_in_progress"
    )


def _board_agent_heartbeat_is_fresh(
    record: dict, *, now: datetime | None = None
) -> bool:
    if str(record.get("board_agent_status") or "").strip().lower() != "online":
        return False
    checked_at = _parse_iso_datetime(record.get("board_agent_last_heartbeat_at"))
    if checked_at is None:
        return False
    ttl_seconds = max(
        10,
        int(getattr(settings, "BOARD_AGENT_HEARTBEAT_TTL_SECONDS", 120) or 120),
    )
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age_seconds = (current.astimezone(timezone.utc) - checked_at).total_seconds()
    return 0 <= age_seconds <= ttl_seconds


def has_configured_edge_devices() -> bool:
    return bool(_configured_devices())


def should_auto_dispatch() -> bool:
    return any(
        bool(record.get("auto_after_export")) for record in _configured_devices()
    )


def _is_automatic_validation_job(row: YoloEdgeValidationJob) -> bool:
    return (
        getattr(row, "auto_training_plan_id", None) is not None
        or getattr(row, "auto_training_round_id", None) is not None
    )


def _environment_is_ready(record: dict) -> bool:
    if _agent_bootstrap_in_progress(record):
        return False
    if not edge_device_registry_service.agent_bootstrap_dispatch_ready(record):
        return False
    if _uses_board_agent(record):
        return bool(record.get("board_agent_environment_ready"))
    report = record.get("environment_report")
    return (
        str(record.get("environment_status") or "") == "ready"
        and isinstance(report, dict)
        and report.get("environment_ready") is True
    )


def _probe_is_fresh(record: dict, *, now: datetime | None = None) -> bool:
    # The device-management projection and queue summary use this same helper
    # so a persisted ``ok`` probe cannot remain dispatchable in one surface
    # after it has expired in another.
    return edge_device_registry_service.ssh_probe_is_fresh(record, now=now)


def _device_is_online(record: dict, health_cache: dict[str, bool]) -> bool:
    device_id = str(record.get("id") or "")
    if device_id in health_cache:
        return health_cache[device_id]
    if _uses_board_agent(record):
        online = _board_agent_heartbeat_is_fresh(record)
        health_cache[device_id] = online
        return online
    if _probe_is_fresh(record):
        health_cache[device_id] = True
        return True

    cfg = edge_device_registry.load_device_config(device_id)
    probe = probe_edge_ssh_target(cfg)
    online = str(probe.get("status") or "") == "ok"
    health_cache[device_id] = online
    try:
        edge_device_registry.update_device_record(
            device_id,
            probe_status=probe.get("status"),
            probe_stage=probe.get("stage"),
            probe_code=probe.get("code"),
            probe_message=probe.get("message"),
            probe_checked_at=probe.get("checked_at"),
        )
    except Exception:
        logger.exception("persist edge device probe failed device_id={}", device_id)
    return online


@contextmanager
def _dispatch_guard() -> Iterator[bool]:
    """Serialize allocation in-process and across MySQL application workers."""
    with _DISPATCH_LOCK:
        connection = None
        acquired = True
        try:
            if engine.dialect.name == "mysql":
                connection = engine.connect()
                acquired = (
                    connection.execute(
                        text("SELECT GET_LOCK(:lock_name, :timeout_sec)"),
                        {"lock_name": _MYSQL_LOCK_NAME, "timeout_sec": 10},
                    ).scalar()
                    == 1
                )
            yield acquired
        finally:
            if connection is not None:
                if acquired:
                    try:
                        connection.execute(
                            text("SELECT RELEASE_LOCK(:lock_name)"),
                            {"lock_name": _MYSQL_LOCK_NAME},
                        )
                    except Exception:
                        logger.exception("release edge scheduler database lock failed")
                connection.close()


def _queue_progress_payload(row, *, reason: str, message: str) -> str:
    try:
        payload = json.loads(str(getattr(row, "progress_json", None) or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    history = payload.get("history") if isinstance(payload.get("history"), list) else []
    now_text = _now().isoformat(timespec="seconds")
    if (
        not history
        or str(history[-1].get("detail", {}).get("queue_reason") or "") != reason
    ):
        history.append(
            {
                "stage": "queue",
                "message": message,
                "detail": {"queue_reason": reason},
                "at": now_text,
            }
        )
    return json.dumps(
        {
            "stage": "queue",
            "message": message,
            "detail": {"queue_reason": reason},
            "updated_at": now_text,
            "history": history[-25:],
        },
        ensure_ascii=False,
    )


def enqueue_edge_validation_job(
    job_id: int,
    *,
    preserve_assigned_device: bool = False,
) -> bool:
    """Place one requested job into the FIFO queue without blocking the API call."""
    with session_scope() as db:
        row = crud_yolo_edge_validation_job.get(db, int(job_id))
        if row is None:
            return False
        if (
            not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
            and _is_automatic_validation_job(row)
        ):
            return False
        if row.status == "PENDING" and getattr(row, "queued_at", None) is not None:
            return True
        queued_at = (
            row.queued_at
            if preserve_assigned_device and row.queued_at is not None
            else _now()
        )
        values = {
            "status": "PENDING",
            "queued_at": queued_at,
            "queue_reason": "waiting_for_dispatch",
            "assigned_at": None,
            "lease_expires_at": None,
            "started_at": None,
            "completed_at": None,
            "error_message": None,
            "report_summary": "边端验证任务已进入调度队列",
            "progress_json": _queue_progress_payload(
                row,
                reason="waiting_for_dispatch",
                message="任务已进入队列，正在分配在线空闲边端设备",
            ),
        }
        if not preserve_assigned_device:
            values.update(
                edge_device_id=None,
                edge_device_name=None,
                edge_host=None,
                edge_port=None,
                edge_user=None,
            )
        with begin_transaction(db):
            crud_yolo_edge_validation_job.update(db, row, **values)
    schedule_edge_validation_dispatch()
    return True


def _processing_device_ids(db) -> set[str]:
    rows = (
        db.query(YoloEdgeValidationJob.edge_device_id)
        .filter(YoloEdgeValidationJob.status == "PROCESSING")
        .all()
    )
    busy = {str(item[0]) for item in rows if str(item[0] or "").strip()}
    if any(not str(item[0] or "").strip() for item in rows):
        legacy_active = edge_device_registry.get_active_device_id()
        if legacy_active:
            busy.add(legacy_active)
    return busy


def _pending_jobs(db) -> list[YoloEdgeValidationJob]:
    query = db.query(YoloEdgeValidationJob).filter(
        YoloEdgeValidationJob.status.in_(_DISPATCHABLE_QUEUE_STATUSES),
        YoloEdgeValidationJob.queued_at.isnot(None),
    )
    if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
        query = query.filter(
            YoloEdgeValidationJob.auto_training_plan_id.is_(None),
            YoloEdgeValidationJob.auto_training_round_id.is_(None),
        )
    rows = query.order_by(
        YoloEdgeValidationJob.queued_at.asc(),
        YoloEdgeValidationJob.id.asc(),
    ).all()
    from labelu.internal.application.service.yolo_edge_validation_pack import (
        edge_validation_pack_ready,
    )

    ready_rows: list[YoloEdgeValidationJob] = []
    unready_rows: list[tuple[YoloEdgeValidationJob, str]] = []
    for row in rows:
        if edge_validation_pack_ready(row):
            ready_rows.append(row)
            continue
        # A package that is still being built is an operator-visible
        # preparation state; an explicit READY package that no longer has a
        # valid manifest is a hard blocked state. Persisting that distinction
        # keeps the queue center's SQL projection aligned with this claim gate
        # on the next read without scanning the whole job history.
        try:
            payload = json.loads(getattr(row, "result_json", None) or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        pack = payload.get("pack") if isinstance(payload, dict) else None
        pack_state = (
            str(pack.get("state") or "").strip().upper()
            if isinstance(pack, dict)
            else ""
        )
        reason = (
            "pack_not_ready"
            if pack_state in {"PREPARING", "UPLOADING"}
            else "waiting_for_pack"
        )
        unready_rows.append((row, reason))
    if unready_rows:
        # The candidate query opened a read transaction. Release it before
        # taking the write transaction used for the durable projection.
        db.rollback()
        with begin_transaction(db):
            for row, reason in unready_rows:
                current = (
                    db.query(YoloEdgeValidationJob)
                    .filter(
                        YoloEdgeValidationJob.id == int(row.id),
                        YoloEdgeValidationJob.status.in_(_DISPATCHABLE_QUEUE_STATUSES),
                    )
                    .with_for_update()
                    .first()
                )
                if current is None:
                    continue
                if str(getattr(current, "queue_reason", "") or "").lower() == reason:
                    continue
                message = (
                    "边端验证样本包正在准备，暂不进入设备队列"
                    if reason == "pack_not_ready"
                    else "边端验证样本包不可用，请重新准备后再调度"
                )
                crud_yolo_edge_validation_job.update(
                    db,
                    current,
                    queue_reason=reason,
                    assigned_at=None,
                    lease_expires_at=None,
                    queue_lease_owner=None,
                    queue_lease_expires_at=None,
                    edge_device_id=None,
                    edge_device_name=None,
                    edge_host=None,
                    edge_port=None,
                    edge_user=None,
                    report_summary=message,
                    progress_json=_queue_progress_payload(
                        current, reason=reason, message=message
                    ),
                )
        db.expire_all()
    unready_ids = {int(row.id) for row, _reason in unready_rows}
    return [row for row in ready_rows if int(row.id) not in unready_ids]


def _latest_completed_device_id(db, row: YoloEdgeValidationJob) -> str:
    previous = (
        db.query(YoloEdgeValidationJob.edge_device_id)
        .filter(
            YoloEdgeValidationJob.export_id == int(row.export_id),
            YoloEdgeValidationJob.id < int(row.id),
            YoloEdgeValidationJob.status.in_(("COMPLETED", "PARTIAL_COMPLETED")),
            YoloEdgeValidationJob.edge_device_id.isnot(None),
        )
        .order_by(YoloEdgeValidationJob.id.desc())
        .first()
    )
    return str(previous[0] or "").strip() if previous else ""


def _set_waiting_state(db, rows: list[YoloEdgeValidationJob], *, reason: str) -> None:
    messages = {
        "waiting_for_idle_device": "在线边端设备均在执行任务，当前任务继续排队",
        "waiting_for_online_device": "暂无可用的在线边端设备，当前任务继续排队",
        "waiting_for_environment_ready": "已配置边端设备尚未通过 BM1688 环境检查，当前任务继续排队",
    }
    message = messages[reason]
    with begin_transaction(db):
        for row in rows:
            if row.queue_reason == reason:
                continue
            crud_yolo_edge_validation_job.update(
                db,
                row,
                queue_reason=reason,
                report_summary=message,
                progress_json=_queue_progress_payload(
                    row, reason=reason, message=message
                ),
            )


def _claim_job(
    db,
    row: YoloEdgeValidationJob,
    record: dict,
    *,
    job_id: int | None = None,
) -> bool:
    device_id = str(record.get("id") or "")
    # The dispatcher releases its read transaction before taking the registry
    # lock. Keep a scalar identity so an expired SQLAlchemy row is never
    # reloaded while that lock is held. Direct callers retain the old fallback.
    resolved_job_id = int(job_id if job_id is not None else row.id)
    # The registry is a read/modify/write JSON document persisted through
    # independent short-lived sessions.  Hold the same host/process lock as
    # device CRUD while re-reading readiness and claiming the job, otherwise a
    # management request can pass its busy check between these two actions.
    with edge_device_registry.device_mutation_lock() as lock_acquired:
        if not lock_acquired:
            logger.warning(
                "edge scheduler skipped device claim because device mutation lock was not acquired device_id={}",
                device_id,
            )
            return False
        latest_record = next(
            (
                item
                for item in edge_device_registry.list_device_records()
                if isinstance(item, dict) and str(item.get("id") or "") == device_id
            ),
            None,
        )
        if latest_record is None or _agent_bootstrap_in_progress(latest_record):
            return False
        # A Board Agent can go offline after candidate selection but before
        # this final claim.  Re-check its heartbeat while holding the same
        # registry mutation lock used by heartbeat persistence so a stale
        # device can never receive a PROCESSING lease.
        if _uses_board_agent(latest_record) and not _board_agent_heartbeat_is_fresh(
            latest_record
        ):
            return False
        # SSH devices have no resident heartbeat.  Their persisted probe is
        # the liveness lease, so re-check its TTL after candidate selection and
        # immediately before the database claim.  A probe can expire while a
        # previous candidate is being inspected; fail closed and leave the job
        # PENDING for the next dispatch pass.
        if not _uses_board_agent(latest_record) and not _probe_is_fresh(latest_record):
            return False
        if not _environment_is_ready(latest_record):
            return False
        with begin_transaction(db):
            current = (
                db.query(YoloEdgeValidationJob)
                .filter(YoloEdgeValidationJob.id == resolved_job_id)
                .with_for_update()
                .first()
            )
            if (
                current is None
                or current.status not in _DISPATCHABLE_QUEUE_STATUSES
                or current.queued_at is None
            ):
                return False
            if (
                not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
                and _is_automatic_validation_job(current)
            ):
                return False
            from labelu.internal.application.service.yolo_edge_validation_pack import (
                edge_validation_pack_ready,
            )

            if not edge_validation_pack_ready(current):
                return False
            busy = (
                db.query(YoloEdgeValidationJob.id)
                .filter(
                    YoloEdgeValidationJob.status == "PROCESSING",
                    YoloEdgeValidationJob.edge_device_id == device_id,
                )
                .first()
            )
            if busy is not None:
                return False
            cfg = edge_device_registry.load_device_config(device_id)
            assigned_at = _now()
            lease_seconds = max(
                900, int(cfg.get("validation_timeout_sec") or 7200) + 900
            )
            message = (
                f"已分配边端设备 {record.get('name') or device_id} / {cfg.get('host')}"
            )
            crud_yolo_edge_validation_job.update(
                db,
                current,
                status="PROCESSING",
                edge_device_id=device_id,
                edge_device_name=str(record.get("name") or "")[:64],
                edge_host=str(cfg.get("host") or "")[:256],
                edge_port=int(cfg.get("port") or 22),
                edge_user=str(cfg.get("user") or "")[:128],
                queue_reason=None,
                assigned_at=assigned_at,
                lease_expires_at=assigned_at + timedelta(seconds=lease_seconds),
                report_summary=message,
            )
        try:
            edge_device_registry.mark_device_used(device_id)
        except Exception:
            logger.exception(
                "mark scheduled edge device used failed device_id={}", device_id
            )
        return True


def dispatch_pending_edge_validation_jobs(*, start_workers: bool = True) -> list[int]:
    """Assign as many queued jobs as there are healthy idle devices."""
    assigned_job_ids: list[int] = []
    with _dispatch_guard() as acquired:
        if not acquired:
            logger.warning(
                "edge scheduler skipped because database lock was not acquired"
            )
            return assigned_job_ids
        with session_scope() as db:
            pending = _pending_jobs(db)
            if not pending:
                return assigned_job_ids
            busy_ids = _processing_device_ids(db)
            # Queries above open a SQLAlchemy read transaction. Registry CRUD
            # and SSH health probes use a host-global mutation lock; release
            # the read transaction before touching that lock so scheduler and
            # device-management requests share one lock order.
            pending_candidates: list[tuple[YoloEdgeValidationJob, int, str, str]] = []
            for row in pending:
                row_id = int(row.id)
                preferred_id = str(row.edge_device_id or "").strip()
                affinity_id = ""
                if not preferred_id:
                    affinity_id = _latest_completed_device_id(db, row)
                pending_candidates.append((row, row_id, preferred_id, affinity_id))
            db.rollback()
            configured_devices = _configured_devices()
            devices = [
                record for record in configured_devices if _environment_is_ready(record)
            ]
            free_devices = [
                record
                for record in devices
                if str(record.get("id") or "") not in busy_ids
            ]
            if not free_devices:
                _set_waiting_state(
                    db,
                    pending,
                    reason=(
                        "waiting_for_idle_device"
                        if devices
                        else (
                            "waiting_for_environment_ready"
                            if configured_devices
                            else "waiting_for_online_device"
                        )
                    ),
                )
                return assigned_job_ids

            health_cache: dict[str, bool] = {}
            for row, row_id, preferred_id, affinity_id in pending_candidates:
                candidates = free_devices
                if preferred_id:
                    candidates = [
                        record
                        for record in free_devices
                        if str(record.get("id") or "") == preferred_id
                    ]
                else:
                    if affinity_id:
                        candidates = sorted(
                            free_devices,
                            key=lambda record: (
                                str(record.get("id") or "") != affinity_id,
                                _device_sort_key(record),
                            ),
                        )
                selected = next(
                    (
                        record
                        for record in candidates
                        if _device_is_online(record, health_cache)
                    ),
                    None,
                )
                if selected is None:
                    continue
                # Defensive rollback also clears any short-lived transaction
                # opened by a health probe or a failed claim before the next
                # registry interaction.
                db.rollback()
                if not _claim_job(db, row, selected, job_id=row_id):
                    continue
                assigned_job_ids.append(row_id)
                selected_id = str(selected.get("id") or "")
                busy_ids.add(selected_id)
                free_devices = [
                    record
                    for record in free_devices
                    if str(record.get("id") or "") != selected_id
                ]
                if not free_devices:
                    break

            remaining = _pending_jobs(db)
            if remaining:
                reason = (
                    "waiting_for_environment_ready"
                    if configured_devices and not devices
                    else (
                        "waiting_for_idle_device"
                        if assigned_job_ids or not free_devices
                        else "waiting_for_online_device"
                    )
                )
                _set_waiting_state(db, remaining, reason=reason)

    if start_workers:
        # A unified Board Agent device is consumed through the internal pull
        # endpoints.  Starting the historical in-process SSH runner here
        # would create two consumers for the same validation job.
        assigned_job_ids_to_start = list(assigned_job_ids)
        try:
            from labelu.internal.application.service.board_agent import (
                unified_board_agent_enabled,
            )

            board_agent_ids = {
                str(record.get("id") or "")
                for record in _configured_devices()
                if unified_board_agent_enabled() and _uses_board_agent(record)
            }
            if board_agent_ids:
                with session_scope() as db:
                    assigned_job_ids_to_start = [
                        int(job_id)
                        for job_id in assigned_job_ids
                        if str(
                            getattr(
                                crud_yolo_edge_validation_job.get(db, int(job_id)),
                                "edge_device_id",
                                "",
                            )
                            or ""
                        )
                        not in board_agent_ids
                    ]
        except Exception:
            logger.exception("读取板端主动拉取设备通道失败")
            # The channel projection is a safety boundary.  If it cannot be
            # read, do not start any legacy SSH runners for this dispatch pass;
            # the next pass can retry after the registry becomes available.
            assigned_job_ids_to_start = []
        from labelu.internal.application.service.edge_validation_worker_runtime import (
            dedicated_worker_enabled,
            dedicated_worker_ready,
        )

        if dedicated_worker_enabled():
            ready, reason = dedicated_worker_ready()
            if ready:
                logger.info(
                    "独立边端验证 Worker 已就绪，Web 进程只保留设备分配: {}",
                    assigned_job_ids_to_start,
                )
                return assigned_job_ids
            logger.error(
                "独立边端验证 Worker 未就绪，保留设备分配但禁止 Web SSH/推理线程: {}",
                reason,
            )
            return assigned_job_ids
        from labelu.internal.application.service.yolo_edge_validation import (
            _run_manual_job_sync,
        )

        for job_id in assigned_job_ids_to_start:
            threading.Thread(
                target=_run_manual_job_sync,
                args=(job_id,),
                name=f"yolo-edge-validation-{job_id}",
                daemon=True,
            ).start()
    return assigned_job_ids


def schedule_edge_validation_dispatch() -> None:
    """Coalesce concurrent dispatch requests into one background worker."""
    global _DISPATCH_REQUESTED, _DISPATCH_RUNNING
    with _SCHEDULE_LOCK:
        _DISPATCH_REQUESTED = True
        if _DISPATCH_RUNNING:
            return
        _DISPATCH_RUNNING = True

    def _run() -> None:
        global _DISPATCH_REQUESTED, _DISPATCH_RUNNING
        while True:
            with _SCHEDULE_LOCK:
                _DISPATCH_REQUESTED = False
            try:
                dispatch_pending_edge_validation_jobs()
            except Exception:
                logger.exception("edge validation dispatch failed")
            with _SCHEDULE_LOCK:
                if _DISPATCH_REQUESTED:
                    continue
                _DISPATCH_RUNNING = False
                return

    threading.Thread(
        target=_run,
        name="yolo-edge-validation-dispatch",
        daemon=True,
    ).start()


def queue_position(db, row: YoloEdgeValidationJob) -> int | None:
    if row.status not in _DISPATCHABLE_QUEUE_STATUSES or row.queued_at is None:
        return None
    if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED and _is_automatic_validation_job(
        row
    ):
        return None

    # The queue center projects persisted PENDING/QUEUED rows into a plain
    # dispatch lane and excludes BLOCKED/PREPARING/RETRYING reasons. Import
    # lazily because edge_queue_center imports this scheduler for dispatch
    # mutations; doing this at module import time would create a cycle.
    from labelu.internal.application.service import edge_queue_center

    query = db.query(YoloEdgeValidationJob).filter(
        YoloEdgeValidationJob.status.in_(_DISPATCHABLE_QUEUE_STATUSES),
        YoloEdgeValidationJob.queued_at.isnot(None),
        edge_queue_center._sql_plain_queue_predicate(),
        (
            (YoloEdgeValidationJob.queued_at < row.queued_at)
            | (
                (YoloEdgeValidationJob.queued_at == row.queued_at)
                & (YoloEdgeValidationJob.id <= int(row.id))
            )
        ),
    )
    if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
        query = query.filter(
            YoloEdgeValidationJob.auto_training_plan_id.is_(None),
            YoloEdgeValidationJob.auto_training_round_id.is_(None),
        )
    from labelu.internal.application.service.yolo_edge_validation_pack import (
        edge_validation_pack_ready,
    )

    candidates = query.order_by(
        YoloEdgeValidationJob.queued_at.asc(),
        YoloEdgeValidationJob.id.asc(),
    ).all()
    eligible = [
        candidate for candidate in candidates if edge_validation_pack_ready(candidate)
    ]
    if not any(int(candidate.id) == int(row.id) for candidate in eligible):
        return None
    return len(eligible)
