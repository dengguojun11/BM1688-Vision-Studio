from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.exc import IntegrityError

from labelu.internal.adapter.persistence import (
    crud_auto_label_job,
    crud_pre_annotation,
    crud_sample,
    crud_task,
    crud_user,
)
from labelu.internal.application.service import auto_label as auto_label_mod
from labelu.internal.application.service import tagtool_preannotate
from labelu.internal.application.service import yolo_preannotate
from labelu.internal.application.service.task_training_ready import (
    refresh_task_training_ready,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.optional_dependencies import ensure_ultralytics_installed
from labelu.internal.common.pre_annotation_sample_repair import (
    convert_uploaded_pre_annotation_to_sample_result,
    count_annotation_items,
)
from labelu.internal.common.yolo_detection_geometry import configured_geometry_tools
from labelu.internal.domain.models.task import MediaType
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.auto_label_job import AutoLabelJob, AutoLabelStatus

_LOG = "[auto-sample-preannotate]"
_JOB_LEASE_SECONDS = 900
_JOB_MAX_ATTEMPTS = 3
_ACTIVE_JOB_IDS: set[int] = set()
_ACTIVE_JOB_LOCK = threading.Lock()
_WATCHDOG_LOCK = threading.Lock()
_WATCHDOG_STARTED = False


def _tool_result_items(block: Any) -> list[dict[str, Any]]:
    if not isinstance(block, dict):
        return []
    items = block.get("result")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _normalize_existing_pre_annotation_result(
    raw_payload: Any,
    *,
    allowed_tool_names: set[str] | None = None,
) -> dict[str, Any]:
    try:
        normalized = convert_uploaded_pre_annotation_to_sample_result(raw_payload)
    except ValueError:
        normalized = {}
    if normalized:
        return normalized

    payload = auto_label_mod._json_loads_maybe(raw_payload) or {}
    if not isinstance(payload, dict):
        return {}

    annotations = payload.get("annotations")
    if not isinstance(annotations, dict):
        return {}

    meta = payload.get("meta_data")
    if not isinstance(meta, dict):
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}

    out: dict[str, Any] = {
        "width": meta.get("width"),
        "height": meta.get("height"),
        "rotate": meta.get("rotate", 0),
    }
    for tool_name, block in annotations.items():
        if not isinstance(tool_name, str) or not tool_name.endswith("Tool"):
            continue
        if allowed_tool_names is not None and tool_name not in allowed_tool_names:
            continue
        items = _tool_result_items(block)
        if not items:
            continue
        out[tool_name] = {
            "toolName": (
                block.get("toolName", tool_name)
                if isinstance(block, dict)
                else tool_name
            ),
            "result": items,
        }
    return out


def _merge_missing_tool_results(
    current_result: dict[str, Any],
    incoming_result: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    merged_result = dict(current_result)
    changed = False

    for meta_key in ("width", "height", "rotate"):
        if (
            merged_result.get(meta_key) is None
            and incoming_result.get(meta_key) is not None
        ):
            merged_result[meta_key] = incoming_result.get(meta_key)
            changed = True

    for tool_name, incoming_block in incoming_result.items():
        if not isinstance(tool_name, str) or not tool_name.endswith("Tool"):
            continue
        incoming_items = _tool_result_items(incoming_block)
        if not incoming_items:
            continue
        current_items = _tool_result_items(merged_result.get(tool_name))
        if current_items:
            continue
        merged_result[tool_name] = {
            "toolName": (
                incoming_block.get("toolName", tool_name)
                if isinstance(incoming_block, dict)
                else tool_name
            ),
            "result": incoming_items,
        }
        changed = True

    return merged_result, changed


def _apply_existing_pre_annotations_to_sample(
    db,
    *,
    task_id: int,
    sample,
    updated_by: int,
    task_config: dict[str, Any] | None = None,
) -> bool:
    sample_name = getattr(getattr(sample, "file", None), "filename", None)
    if not sample_name:
        return False

    rows, _ = crud_pre_annotation.list_by(
        db=db,
        task_id=task_id,
        sample_name=sample_name,
        page=0,
        size=200,
    )
    if not rows:
        return False

    sample_data = auto_label_mod._json_loads_maybe(sample.data)
    if not isinstance(sample_data, dict):
        sample_data = {}

    raw_result = sample_data.get("result")
    result_was_string = isinstance(raw_result, str)
    result_payload = (
        auto_label_mod._json_loads_maybe(raw_result)
        if result_was_string
        else raw_result
    )
    if not isinstance(result_payload, dict):
        result_payload = {}

    merged_result = dict(result_payload)
    changed = False
    allowed_tool_names = None
    if task_config is not None:
        allowed_tool_names = set(configured_geometry_tools(task_config)) | {"tagTool"}
    for row in rows:
        # Generated rows (AI/YOLO/TagTool/VLX/LA) are canvas previews owned by
        # their own action.  An automatic job must not persist them as formal
        # sample data: geometry stays reviewable on the canvas and global tags
        # are written by the tag pipeline itself.
        source_type = (
            crud_pre_annotation._parse_pre_annotation_data(row.data).get("meta") or {}
        ).get("source_type")
        if source_type in crud_pre_annotation.GENERATED_PRE_ANNOTATION_SOURCE_TYPES:
            continue
        normalized_result = _normalize_existing_pre_annotation_result(
            row.data,
            allowed_tool_names=allowed_tool_names,
        )
        if not normalized_result:
            continue
        merged_result, row_changed = _merge_missing_tool_results(
            merged_result,
            normalized_result,
        )
        changed = changed or row_changed

    if not changed:
        return False

    sample_data["result"] = (
        json.dumps(merged_result, ensure_ascii=False)
        if result_was_string
        else merged_result
    )
    with begin_transaction(db):
        crud_sample.update(
            db=db,
            db_obj=sample,
            obj_in={
                TaskSample.data.key: json.dumps(sample_data, ensure_ascii=False),
                TaskSample.annotated_count.key: count_annotation_items(merged_result),
                TaskSample.updated_by.key: updated_by,
            },
        )
    return True


def _sample_has_any_geometry(db, *, task_id: int, sample_id: int) -> bool:
    sample = crud_sample.get(db=db, sample_id=sample_id)
    if not sample:
        return False
    if auto_label_mod._sample_has_formal_geometry(sample):
        return True
    sample_name = getattr(getattr(sample, "file", None), "filename", None)
    if not sample_name:
        return False
    rows, _ = crud_pre_annotation.list_by(
        db=db,
        task_id=task_id,
        sample_name=sample_name,
        page=0,
        size=200,
    )
    return any(
        crud_pre_annotation._pre_annotation_has_geometry(row.data) for row in rows
    )


def schedule_auto_preannotate_for_new_samples(
    *,
    task_id: int,
    sample_ids: list[int],
    user_id: int,
    source_hint: str,
    requested_model_id: str | None = None,
    idempotency_key: str | None = None,
) -> int | None:
    uniq_sample_ids: list[int] = []
    seen: set[int] = set()
    for raw in sample_ids or []:
        sample_id = int(raw)
        if sample_id <= 0 or sample_id in seen:
            continue
        seen.add(sample_id)
        uniq_sample_ids.append(sample_id)
    if not uniq_sample_ids:
        return None

    normalized_source_hint = str(source_hint or "").strip() or "sample_create"
    normalized_model_id = str(requested_model_id or "").strip() or None
    source_key = _normalize_source_key(idempotency_key)
    payload = {
        "version": 1,
        "sample_ids": uniq_sample_ids,
        "processed_sample_ids": [],
        "failed_sample_ids": [],
        "source_hint": normalized_source_hint,
        "requested_model_id": normalized_model_id,
        "errors": [],
    }

    from labelu.internal.common.db import session_scope

    try:
        with session_scope() as db:
            with begin_transaction(db):
                existing = (
                    crud_auto_label_job.get_by_source_key(db, source_key)
                    if source_key
                    else None
                )
                if existing is not None:
                    job_id = int(existing.id)
                else:
                    job = crud_auto_label_job.create(
                        db=db,
                        task_id=int(task_id),
                        user_id=int(user_id),
                        sample_count=len(uniq_sample_ids),
                        filter_by_labels=True,
                        job_kind=crud_auto_label_job.AUTO_SAMPLE_PREANNOTATE_JOB_KIND,
                        source_hint=normalized_source_hint,
                        source_key=source_key,
                        payload_json=json.dumps(payload, ensure_ascii=False),
                    )
                    job_id = int(job.id)
    except IntegrityError:
        if not source_key:
            raise
        with session_scope() as db:
            existing = crud_auto_label_job.get_by_source_key(db, source_key)
            if existing is None:
                raise
            job_id = int(existing.id)

    _submit_auto_preannotate_job(job_id)
    return job_id


def _normalize_source_key(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if len(normalized) <= 128:
        return normalized
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _job_payload(job) -> dict[str, Any]:
    try:
        payload = json.loads(job.payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _submit_auto_preannotate_job(job_id: int) -> bool:
    # Production uses the separate worker process.  Keeping this guard in the
    # enqueue path prevents an API process from accidentally starting a long
    # model-inference thread when the dedicated worker switch is enabled.
    if bool(getattr(settings, "AUTO_PREANNOTATE_DEDICATED_WORKER_ENABLED", False)):
        logger.info("{} queued_for_dedicated_worker job_id={}", _LOG, job_id)
        return False
    job_id = int(job_id)
    with _ACTIVE_JOB_LOCK:
        if job_id in _ACTIVE_JOB_IDS:
            return False
        _ACTIVE_JOB_IDS.add(job_id)

    def run() -> None:
        try:
            _run_durable_auto_preannotate_job(job_id)
        finally:
            with _ACTIVE_JOB_LOCK:
                _ACTIVE_JOB_IDS.discard(job_id)

    threading.Thread(
        target=run,
        name=f"auto-sample-preannotate-{job_id}",
        daemon=True,
    ).start()
    return True


def dispatch_pending_auto_preannotate_jobs(*, limit: int = 20) -> int:
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        rows = crud_auto_label_job.list_runnable_auto_sample_jobs(db, limit=limit)
        job_ids = [int(row.id) for row in rows]
    return sum(1 for job_id in job_ids if _submit_auto_preannotate_job(job_id))


def schedule_auto_preannotate_watchdog() -> None:
    if bool(getattr(settings, "AUTO_PREANNOTATE_DEDICATED_WORKER_ENABLED", False)):
        logger.info("{} api_watchdog_disabled dedicated_worker_enabled=true", _LOG)
        return
    global _WATCHDOG_STARTED
    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    interval = max(
        10,
        int(os.environ.get("LABELU_AUTO_PREANNOTATE_WATCHDOG_SECONDS", "30")),
    )

    def tick() -> None:
        try:
            dispatch_pending_auto_preannotate_jobs()
        except Exception:
            logger.exception("{} watchdog scan failed", _LOG)
        timer = threading.Timer(float(interval), tick)
        timer.daemon = True
        timer.start()

    threading.Thread(
        target=dispatch_pending_auto_preannotate_jobs,
        name="auto-sample-preannotate-bootstrap",
        daemon=True,
    ).start()
    timer = threading.Timer(float(interval), tick)
    timer.daemon = True
    timer.start()
    logger.info("{} watchdog started interval={}s", _LOG, interval)


def schedule_auto_preannotate_for_task_samples(
    *, db, task_id: int, user_id: int, source_hint: str
) -> None:
    """Retry existing samples after a task's label contract is saved."""
    samples = crud_sample.list_by_task(db=db, task_id=int(task_id))
    sample_ids = [int(sample.id) for sample in samples if getattr(sample, "id", None)]
    if sample_ids:
        schedule_auto_preannotate_for_new_samples(
            task_id=int(task_id),
            sample_ids=sample_ids,
            user_id=int(user_id),
            source_hint=source_hint,
        )


def _run_durable_auto_preannotate_job(
    job_id: int, *, lease_seconds: int | None = None
) -> None:
    from labelu.internal.common.db import session_scope

    lease_owner = f"{os.getpid()}:{uuid.uuid4().hex}"
    with session_scope() as db:
        with begin_transaction(db):
            lease_fencing_token = crud_auto_label_job.claim_auto_sample_job_with_token(
                db,
                job_id=int(job_id),
                lease_owner=lease_owner,
                lease_seconds=int(lease_seconds or _JOB_LEASE_SECONDS),
            )
        if lease_fencing_token is None:
            return
        job = crud_auto_label_job.get(db=db, job_id=int(job_id))
        if not job:
            return
        payload = _job_payload(job)
        sample_ids = [
            int(value)
            for value in payload.get("sample_ids") or []
            if str(value).isdigit() and int(value) > 0
        ]
        processed_sample_ids = {
            int(value)
            for value in payload.get("processed_sample_ids") or []
            if str(value).isdigit() and int(value) > 0
        }
        task_id = int(job.task_id)
        user_id = int(job.created_by)
        source_hint = str(
            payload.get("source_hint") or job.source_hint or "durable_job"
        )
        requested_model_id = (
            str(payload.get("requested_model_id") or "").strip() or None
        )

    if not sample_ids:
        _fail_durable_job(
            job_id,
            lease_owner,
            "durable job has no sample_ids",
            lease_fencing_token,
        )
        return
    _run_auto_preannotate_sync(
        task_id,
        sample_ids,
        user_id,
        source_hint,
        requested_model_id,
        job_id=job_id,
        lease_owner=lease_owner,
        lease_fencing_token=lease_fencing_token,
        lease_seconds=int(lease_seconds or _JOB_LEASE_SECONDS),
        processed_sample_ids=processed_sample_ids,
    )


def _renew_durable_job_lease(
    job_id: int,
    lease_owner: str,
    lease_fencing_token: int | None = None,
    lease_seconds: int = _JOB_LEASE_SECONDS,
) -> None:
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        with begin_transaction(db):
            matched = (
                db.query(AutoLabelJob)
                .filter(
                    AutoLabelJob.id == int(job_id),
                    AutoLabelJob.status == AutoLabelStatus.PROCESSING.value,
                    AutoLabelJob.lease_owner == lease_owner,
                    *(
                        [AutoLabelJob.lease_fencing_token == int(lease_fencing_token)]
                        if lease_fencing_token is not None
                        else []
                    ),
                )
                .update(
                    {
                        AutoLabelJob.lease_expires_at: datetime.now()
                        + timedelta(seconds=max(int(lease_seconds), 30)),
                        AutoLabelJob.updated_at: datetime.now(),
                    },
                    synchronize_session=False,
                )
            )
            if not matched:
                raise RuntimeError(f"auto preannotation job {job_id} lease lost")


def _record_durable_job_sample(
    job_id: int,
    lease_owner: str,
    *,
    sample_id: int,
    success: bool,
    error: str | None = None,
    lease_fencing_token: int | None = None,
    lease_seconds: int = _JOB_LEASE_SECONDS,
) -> None:
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        with begin_transaction(db):
            job = crud_auto_label_job.get(db=db, job_id=int(job_id))
            if (
                not job
                or job.status != AutoLabelStatus.PROCESSING.value
                or job.lease_owner != lease_owner
                or (
                    lease_fencing_token is not None
                    and int(job.lease_fencing_token or 0) != int(lease_fencing_token)
                )
            ):
                raise RuntimeError(f"auto preannotation job {job_id} lease lost")
            payload = _job_payload(job)
            processed = [
                int(value)
                for value in payload.get("processed_sample_ids") or []
                if str(value).isdigit() and int(value) > 0
            ]
            if int(sample_id) not in processed:
                processed.append(int(sample_id))
                job.processed_count = len(processed)
                if success:
                    job.success_count = int(job.success_count or 0) + 1
                else:
                    job.failed_count = int(job.failed_count or 0) + 1
            payload["processed_sample_ids"] = processed
            if error:
                errors = list(payload.get("errors") or [])
                errors.append({"sample_id": int(sample_id), "error": str(error)[:1000]})
                payload["errors"] = errors[-20:]
                failed_sample_ids = [
                    int(value)
                    for value in payload.get("failed_sample_ids") or []
                    if str(value).isdigit() and int(value) > 0
                ]
                if int(sample_id) not in failed_sample_ids:
                    failed_sample_ids.append(int(sample_id))
                payload["failed_sample_ids"] = failed_sample_ids
                job.error_message = str(error)[:4000]
            job.payload_json = json.dumps(payload, ensure_ascii=False)
            job.lease_expires_at = datetime.now() + timedelta(
                seconds=max(int(lease_seconds), 30)
            )
            job.updated_at = datetime.now()
            db.flush()


def _finish_durable_job(
    job_id: int, lease_owner: str, lease_fencing_token: int | None = None
) -> None:
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        with begin_transaction(db):
            job = crud_auto_label_job.get(db=db, job_id=int(job_id))
            if (
                not job
                or job.lease_owner != lease_owner
                or (
                    lease_fencing_token is not None
                    and int(job.lease_fencing_token or 0) != int(lease_fencing_token)
                )
            ):
                return
            success_count = int(job.success_count or 0)
            failed_count = int(job.failed_count or 0)
            payload = _job_payload(job)
            failed_sample_ids = {
                int(value)
                for value in payload.get("failed_sample_ids") or []
                if str(value).isdigit() and int(value) > 0
            }
            if failed_count and int(job.attempt_count or 0) < _JOB_MAX_ATTEMPTS:
                processed_sample_ids = [
                    int(value)
                    for value in payload.get("processed_sample_ids") or []
                    if str(value).isdigit()
                    and int(value) > 0
                    and int(value) not in failed_sample_ids
                ]
                payload["processed_sample_ids"] = processed_sample_ids
                payload["failed_sample_ids"] = []
                payload["errors"] = []
                crud_auto_label_job.update_status(
                    db,
                    job,
                    AutoLabelStatus.PENDING.value,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    processed_count=len(processed_sample_ids),
                    success_count=len(processed_sample_ids),
                    failed_count=0,
                    error_message=(
                        f"retry scheduled after attempt {int(job.attempt_count or 0)}"
                    ),
                    lease_owner=None,
                    lease_expires_at=None,
                )
                logger.warning(
                    "{} retry_scheduled job_id={} attempt={} failed_sample_ids={}",
                    _LOG,
                    job_id,
                    int(job.attempt_count or 0),
                    sorted(failed_sample_ids),
                )
                return
            if failed_count == 0:
                final_status = AutoLabelStatus.COMPLETED.value
                error_message = None
            elif success_count == 0:
                final_status = AutoLabelStatus.FAILED.value
                error_message = (
                    job.error_message or "all automatic preannotations failed"
                )
            else:
                final_status = AutoLabelStatus.PARTIAL_COMPLETED.value
                error_message = (
                    job.error_message or "some automatic preannotations failed"
                )
            crud_auto_label_job.update_status(
                db,
                job,
                final_status,
                error_message=error_message,
                lease_owner=None,
                lease_expires_at=None,
            )


def _fail_durable_job(
    job_id: int,
    lease_owner: str,
    error: str,
    lease_fencing_token: int | None = None,
) -> None:
    from labelu.internal.common.db import session_scope

    try:
        with session_scope() as db:
            with begin_transaction(db):
                job = crud_auto_label_job.get(db=db, job_id=int(job_id))
                if (
                    not job
                    or job.lease_owner != lease_owner
                    or (
                        lease_fencing_token is not None
                        and int(job.lease_fencing_token or 0)
                        != int(lease_fencing_token)
                    )
                ):
                    return
                crud_auto_label_job.update_status(
                    db,
                    job,
                    AutoLabelStatus.FAILED.value,
                    error_message=str(error)[:4000],
                    lease_owner=None,
                    lease_expires_at=None,
                )
    except Exception:
        logger.exception("{} failed to persist fatal job error job_id={}", _LOG, job_id)


def _run_auto_preannotate_sync(
    task_id: int,
    sample_ids: list[int],
    user_id: int,
    source_hint: str,
    requested_model_id: str | None = None,
    *,
    job_id: int | None = None,
    lease_owner: str | None = None,
    lease_fencing_token: int | None = None,
    lease_seconds: int = _JOB_LEASE_SECONDS,
    processed_sample_ids: set[int] | None = None,
) -> None:
    from labelu.internal.common.db import session_scope

    processed_sample_ids = processed_sample_ids or set()
    try:
        with session_scope() as db:
            task = crud_task.get(db=db, task_id=task_id)
            user = crud_user.get(db=db, id=user_id)
            if not task or not user:
                logger.info(
                    "{} skip task_id={} reason=missing_task_or_user source_hint={}",
                    _LOG,
                    task_id,
                    source_hint,
                )
                if job_id is not None:
                    raise RuntimeError(f"task {task_id} or user {user_id} not found")
                return
            if task.media_type != MediaType.IMAGE.value:
                logger.info(
                    "{} skip task_id={} reason=non_image_task media_type={} source_hint={}",
                    _LOG,
                    task_id,
                    task.media_type,
                    source_hint,
                )
                if job_id is not None:
                    raise RuntimeError(f"task {task_id} is not an image task")
                return

            task_config = auto_label_mod.resolve_ai_task_config(db, task)
            _, _, tag_dimensions = auto_label_mod._extract_tool_configs(task_config)
            has_global_tag_dims = bool(tag_dimensions)
            tagtool_candidate = None
            if has_global_tag_dims:
                try:
                    tagtool_candidate = (
                        tagtool_preannotate.resolve_auto_candidate_for_task(
                            db=db,
                            task=task,
                            current_user=user,
                            task_cfg_merged=task_config,
                        )
                    )
                except Exception as exc:
                    logger.opt(exception=exc).warning(
                        "{} tagtool_candidate_unavailable task_id={} source_hint={}",
                        _LOG,
                        task_id,
                        source_hint,
                    )
            yolo_candidate = None
            try:
                if settings.YOLO_TRAIN_ENABLED and ensure_ultralytics_installed(
                    reason="automatic YOLO sample preannotation"
                ):
                    if requested_model_id:
                        yolo_candidate = (
                            yolo_preannotate.resolve_requested_candidate_for_task(
                                db=db,
                                task=task,
                                current_user=user,
                                published_model_id=requested_model_id,
                            )
                        )
                    else:
                        yolo_candidate = (
                            yolo_preannotate.resolve_auto_candidate_for_task(
                                db=db,
                                task=task,
                                current_user=user,
                                prefer_imported=True,
                            )
                        )
            except Exception as exc:
                logger.opt(exception=exc).warning(
                    "{} yolo_candidate_unavailable task_id={} source_hint={}",
                    _LOG,
                    task_id,
                    source_hint,
                )

            # Requirement: YOLO models win whenever any of their labels match
            # the task contract; the managed LLM detector is the fallback only
            # when no compatible .pt model exists.
            has_rect_labels = False
            try:
                rect_contract = yolo_preannotate._task_contract_descriptor(db, task)
                has_rect_labels = bool(rect_contract.get("labels"))
            except Exception as exc:
                logger.opt(exception=exc).warning(
                    "{} rect_contract_unavailable task_id={} source_hint={}",
                    _LOG,
                    task_id,
                    source_hint,
                )
            ai_bbox_available = False
            ai_bbox_ready = False
            if has_rect_labels:
                try:
                    ai_bbox_ready = bool(
                        auto_label_mod._configured_bbox_vision_available()
                    )
                except Exception as exc:
                    logger.opt(exception=exc).warning(
                        "{} ai_bbox_availability_unknown task_id={} source_hint={}",
                        _LOG,
                        task_id,
                        source_hint,
                    )
            ai_detection_limit = int(
                getattr(settings, "AUTO_PREANNOTATE_AI_DETECTION_MAX_SAMPLES", 0) or 0
            )
            ai_bbox_available = bool(
                ai_bbox_ready
                and bool(getattr(settings, "AUTO_PREANNOTATE_AI_DETECTION_ENABLED", True))
                and (ai_detection_limit <= 0 or len(sample_ids) <= ai_detection_limit)
            )
            if ai_bbox_ready and not ai_bbox_available:
                logger.info(
                    "{} ai_detection_skipped task_id={} source_hint={} samples={} "
                    "enabled={} max_samples={}",
                    _LOG,
                    task_id,
                    source_hint,
                    len(sample_ids),
                    bool(getattr(settings, "AUTO_PREANNOTATE_AI_DETECTION_ENABLED", True)),
                    ai_detection_limit,
                )

        yolo_runtime = None
        if yolo_candidate is not None:
            try:
                weights_path = Path(yolo_candidate["weights_path"])
                job_type = str(yolo_candidate.get("job_type") or "yolo_detection")
                model = None
                names_map = {}
                if job_type not in {
                    yolo_preannotate.PLATE_RECOGNITION_JOB_TYPE,
                    yolo_preannotate.PLATE_DETECTION_JOB_TYPE,
                }:
                    from ultralytics import YOLO

                    model = YOLO(str(weights_path))
                    names_map = yolo_preannotate._yolo_names_map(model)
                yolo_runtime = {
                    "task_cfg_merged": task_config,
                    "model": model,
                    "names_map": names_map,
                    "allowed_labels": set(yolo_candidate["rect_labels"]),
                    "label_aliases": yolo_preannotate._build_detection_label_aliases(
                        names_map.values(), set(yolo_candidate["rect_labels"])
                    ),
                    "imgsz": int(yolo_candidate["imgsz"]),
                    "model_kind": str(yolo_candidate["model_kind"]),
                    "model_id": int(yolo_candidate["model_id"]),
                    "weights_name": weights_path.name,
                    "job_type": job_type,
                    "plate_artifacts": yolo_candidate.get("plate_artifacts") or {},
                    "plate_metadata": yolo_candidate.get("plate_metadata") or {},
                }
            except Exception as exc:
                logger.opt(exception=exc).warning(
                    "{} yolo_runtime_unavailable task_id={} model_kind={} model_id={} source_hint={}",
                    _LOG,
                    task_id,
                    yolo_candidate.get("model_kind"),
                    yolo_candidate.get("model_id"),
                    source_hint,
                )

        logger.info(
            "{} start task_id={} sample_ids={} source_hint={} has_global_tag_dims={} "
            "tagtool_candidate={} yolo_candidate={}",
            _LOG,
            task_id,
            sample_ids,
            source_hint,
            has_global_tag_dims,
            (
                {
                    "model_kind": tagtool_candidate["model_kind"],
                    "model_id": tagtool_candidate["model_id"],
                }
                if tagtool_candidate
                else None
            ),
            (
                {
                    "model_kind": yolo_runtime["model_kind"],
                    "model_id": yolo_runtime["model_id"],
                    "imgsz": yolo_runtime["imgsz"],
                    "labels": sorted(yolo_runtime["allowed_labels"]),
                }
                if yolo_runtime
                else None
            ),
        )

        for sample_id in sample_ids:
            if int(sample_id) in processed_sample_ids:
                continue
            if job_id is not None and lease_owner is not None:
                _renew_durable_job_lease(
                    job_id, lease_owner, lease_fencing_token, lease_seconds
                )
            sample_success = True
            sample_error: str | None = None
            with session_scope() as db:
                sample = crud_sample.get(db=db, sample_id=sample_id)
                if not sample or sample.task_id != task_id:
                    logger.info(
                        "{} skip_sample task_id={} sample_id={} reason=missing_or_task_mismatch source_hint={}",
                        _LOG,
                        task_id,
                        sample_id,
                        source_hint,
                    )
                    sample_success = False
                    sample_error = "sample missing or task mismatch"
                    if job_id is not None and lease_owner is not None:
                        _record_durable_job_sample(
                            job_id,
                            lease_owner,
                            sample_id=sample_id,
                            success=False,
                            error=sample_error,
                            lease_fencing_token=lease_fencing_token,
                            lease_seconds=lease_seconds,
                        )
                    continue
                merged_existing_preannotations = (
                    _apply_existing_pre_annotations_to_sample(
                        db,
                        task_id=task_id,
                        sample=sample,
                        updated_by=user_id,
                        task_config=task_config,
                    )
                )
                if merged_existing_preannotations:
                    sample = crud_sample.get(db=db, sample_id=sample_id)
                needs_tags = (
                    has_global_tag_dims
                    and not auto_label_mod._sample_global_tags_complete(
                        sample,
                        task_config,
                    )
                )
                needs_rect = (
                    bool(yolo_runtime) or ai_bbox_available
                ) and not _sample_has_any_geometry(
                    db,
                    task_id=task_id,
                    sample_id=sample_id,
                )

            if not needs_tags and not needs_rect:
                logger.info(
                    "{} skip_sample task_id={} sample_id={} reason=nothing_missing source_hint={} merged_existing_preannotations={}",
                    _LOG,
                    task_id,
                    sample_id,
                    source_hint,
                    merged_existing_preannotations,
                )
                if job_id is not None and lease_owner is not None:
                    _record_durable_job_sample(
                        job_id,
                        lease_owner,
                        sample_id=sample_id,
                        success=True,
                        lease_fencing_token=lease_fencing_token,
                        lease_seconds=lease_seconds,
                    )
                continue

            if needs_tags:
                tags_completed = False
                if tagtool_candidate is not None:
                    try:
                        tagtool_preannotate._process_one_sample(
                            task_id=task_id,
                            task_cfg_merged=task_config,
                            sample_id=sample_id,
                            weights_path=tagtool_candidate["weights_path"],
                            label_map=tagtool_candidate["label_map"],
                            overwrite=False,
                            model_kind=tagtool_candidate["model_kind"],
                            model_id=tagtool_candidate["model_id"],
                            user_id=user_id,
                            device_hint=tagtool_candidate["device"],
                        )
                        tags_completed = True
                    except Exception as exc:
                        logger.opt(exception=exc).warning(
                            "{} imported_tag_fail_fallback_ai task_id={} sample_id={} "
                            "model_id={} source_hint={}",
                            _LOG,
                            task_id,
                            sample_id,
                            tagtool_candidate["model_id"],
                            source_hint,
                        )
                if not tags_completed:
                    try:
                        auto_label_mod._process_single_sample(
                            task_id=task_id,
                            task_config=task_config,
                            sample_id=sample_id,
                            filter_by_labels=True,
                            include_geometry_tools=False,
                            overwrite_existing_annotations=False,
                            created_by=user_id,
                        )
                    except Exception as exc:
                        sample_success = False
                        sample_error = str(exc)
                        logger.opt(exception=exc).error(
                            "{} ai_tag_fail task_id={} sample_id={} source_hint={}",
                            _LOG,
                            task_id,
                            sample_id,
                            source_hint,
                        )

            if needs_rect and yolo_runtime:
                try:
                    yolo_preannotate._process_one_sample(
                        task_id=task_id,
                        task_cfg_merged=yolo_runtime["task_cfg_merged"],
                        sample_id=sample_id,
                        model=yolo_runtime["model"],
                        names_map=yolo_runtime["names_map"],
                        allowed_labels=yolo_runtime["allowed_labels"],
                        label_aliases=yolo_runtime["label_aliases"],
                        imgsz=yolo_runtime["imgsz"],
                        conf=0.25,
                        overwrite=False,
                        model_kind=yolo_runtime["model_kind"],
                        model_id=yolo_runtime["model_id"],
                        user_id=user_id,
                        weights_name=yolo_runtime["weights_name"],
                        job_type=yolo_runtime["job_type"],
                        plate_artifacts=yolo_runtime["plate_artifacts"],
                        plate_metadata=yolo_runtime["plate_metadata"],
                    )
                except Exception as exc:
                    sample_success = False
                    sample_error = str(exc)
                    logger.opt(exception=exc).error(
                        "{} yolo_fail task_id={} sample_id={} source_hint={}",
                        _LOG,
                        task_id,
                        sample_id,
                        source_hint,
                    )

            elif needs_rect and ai_bbox_available:
                # No compatible YOLO model: fall back to the managed vision
                # detector exactly like the manual "AI目标检测" action.  Only
                # geometry is requested; global tags remain owned by the tag
                # pipeline.
                try:
                    auto_label_mod._process_single_sample(
                        task_id=task_id,
                        task_config=task_config,
                        sample_id=sample_id,
                        filter_by_labels=True,
                        include_geometry_tools=True,
                        overwrite_existing_annotations=False,
                        created_by=user_id,
                    )
                except Exception as exc:
                    sample_success = False
                    sample_error = str(exc)
                    logger.opt(exception=exc).error(
                        "{} ai_detection_fallback_fail task_id={} sample_id={} source_hint={}",
                        _LOG,
                        task_id,
                        sample_id,
                        source_hint,
                    )

            if job_id is not None and lease_owner is not None:
                _record_durable_job_sample(
                    job_id,
                    lease_owner,
                    sample_id=sample_id,
                    success=sample_success,
                    error=sample_error,
                    lease_fencing_token=lease_fencing_token,
                    lease_seconds=lease_seconds,
                )

        with session_scope() as db:
            task = crud_task.get(db=db, task_id=task_id)
            if task:
                try:
                    refresh_task_training_ready(db, task)
                except Exception as exc:
                    logger.opt(exception=exc).error(
                        "{} refresh_training_ready_fail task_id={} sample_ids={} source_hint={}",
                        _LOG,
                        task_id,
                        sample_ids,
                        source_hint,
                    )

        logger.info(
            "{} done task_id={} sample_ids={} source_hint={}",
            _LOG,
            task_id,
            sample_ids,
            source_hint,
        )
        if job_id is not None and lease_owner is not None:
            _finish_durable_job(job_id, lease_owner, lease_fencing_token)
    except Exception as exc:
        logger.opt(exception=exc).error(
            "{} fatal task_id={} sample_ids={} source_hint={}",
            _LOG,
            task_id,
            sample_ids,
            source_hint,
        )
        if job_id is not None and lease_owner is not None:
            _fail_durable_job(job_id, lease_owner, str(exc), lease_fencing_token)
