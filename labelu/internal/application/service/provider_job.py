from __future__ import annotations

import json
import hashlib
from datetime import datetime

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_ingestion,
    crud_model_version,
    crud_task,
    crud_provider_job,
    crud_provider_registry,
)
from labelu.internal.application.command.provider_job import (
    CreatePredictionSetCommand,
    CreateProviderJobCommand,
    TransitionPredictionSetReviewCommand,
)
from labelu.internal.application.response.provider_job import (
    PredictionSetProjectionResponse,
    PredictionSetReviewAuditResponse,
    PredictionSetResponse,
    ProviderJobResponse,
)
from labelu.internal.application.service.provider_registry import ensure_seeded
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.provider_job import PredictionSet, ProviderJob
from labelu.internal.domain.models.prediction_review import (
    PredictionSetProjection,
    PredictionSetReviewAudit,
)
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User


def _error(
    message: str, code=ErrorCode.CODE_30002_VALIDATION_ERROR, http_status: int = 400
):
    return LabelUException(code=code, status_code=http_status, message=message)


def _owner_access(owner_id: int | None, user: User) -> None:
    if is_task_superuser(user) or int(owner_id or 0) == int(user.id):
        return
    raise _error("无权访问该预标注资源", ErrorCode.CODE_30001_NO_PERMISSION, 403)


def _task_access(db: Session, task_id: int, user: User) -> Task:
    task = (
        db.query(Task)
        .filter(Task.id == int(task_id), Task.deleted_at.is_(None))
        .first()
    )
    if task is None:
        raise _error("任务不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    visible = (
        crud_task.visible_query(
            db,
            owner_id=user.id,
            list_all_tasks=is_task_superuser(user),
        )
        .filter(Task.id == int(task_id))
        .first()
    )
    if visible is None:
        raise _error("无权访问该任务", ErrorCode.CODE_30001_NO_PERMISSION, 403)
    return task


def _json_object(raw: str | None):
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_list(raw: str | None):
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [int(item) for item in value] if isinstance(value, list) else []


def _job_response(row: ProviderJob) -> ProviderJobResponse:
    return ProviderJobResponse(
        id=row.id,
        provider_key=row.provider_key,
        provider_version=row.provider_version,
        model_version_id=row.model_version_id,
        input_asset_ids=_json_list(row.input_asset_ids_json),
        contract_version=row.contract_version,
        config=_json_object(row.config_json),
        request_signature=row.request_signature,
        status=row.status,
        total_count=row.total_count or 0,
        completed_count=row.completed_count or 0,
        error_code=row.error_code,
        error_message=row.error_message,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _set_response(row: PredictionSet) -> PredictionSetResponse:
    return PredictionSetResponse(
        id=row.id,
        provider_job_id=row.provider_job_id,
        sample_id=row.sample_id,
        version=row.version,
        annotation_contract_version=row.annotation_contract_version,
        prediction_schema_version=row.prediction_schema_version,
        payload_uri=row.payload_uri,
        payload_sha256=row.payload_sha256,
        result_manifest_uri=row.result_manifest_uri,
        result_sha256=row.result_sha256,
        status=row.status,
        review_status=row.review_status,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        review_reason=row.review_reason,
        source=row.source,
        item_count=row.item_count or 0,
        payload=_json_object(row.payload_json) if row.payload_json else None,
        metrics=_json_object(row.metrics_json),
        error_code=row.error_code,
        error_message=row.error_message,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _prediction_matches_command(row: PredictionSet, cmd: CreatePredictionSetCommand) -> bool:
    """Compare a retried prediction request with the immutable persisted result."""
    actual_payload = _json_object(row.payload_json) if row.payload_json else None
    actual_metrics = _json_object(row.metrics_json)
    requested_metrics = cmd.metrics or {}
    return all(
        (
            row.sample_id == cmd.sample_id,
            int(row.version) == int(cmd.version),
            row.annotation_contract_version == cmd.annotation_contract_version,
            row.prediction_schema_version == cmd.prediction_schema_version,
            row.payload_uri == cmd.payload_uri,
            row.payload_sha256 == cmd.payload_sha256,
            row.result_manifest_uri == cmd.result_manifest_uri,
            row.result_sha256 == cmd.result_sha256,
            _canonical_json(actual_payload) == _canonical_json(cmd.payload),
            str(row.status or "").upper() == cmd.status,
            int(row.item_count or 0) == int(cmd.item_count),
            _canonical_json(actual_metrics) == _canonical_json(requested_metrics),
            row.error_code == cmd.error_code,
            row.error_message == cmd.error_message,
        )
    )


async def create_job(
    db: Session,
    cmd: CreateProviderJobCommand,
    current_user: User,
    *,
    idempotency_key: str | None = None,
) -> ProviderJobResponse:
    ensure_seeded(db)
    provider = crud_provider_registry.get_provider(
        db, cmd.provider_key, cmd.provider_version
    )
    if provider is None or provider.status != "ACTIVE":
        raise _error("Provider 不存在或已禁用", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    if cmd.model_version_id is not None:
        model = crud_model_version.get(db, cmd.model_version_id)
        if model is None:
            raise _error("模型版本不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    signature = (idempotency_key or cmd.request_signature or "").strip()
    if not signature:
        raise _error("必须提供 Idempotency-Key 或 request_signature", http_status=422)
    if not idempotency_key and cmd.request_signature:
        # Explicit signatures are accepted for worker/internal callers; HTTP
        # clients should use Idempotency-Key so retries remain visible.
        signature = cmd.request_signature.strip()
    if len(signature) > 128:
        raise _error("request_signature 过长")
    assets = []
    for asset_id in cmd.input_asset_ids:
        asset = crud_ingestion.get_asset(db, asset_id)
        if asset is None or (
            not is_task_superuser(current_user)
            and int(asset.created_by or 0) != int(current_user.id)
        ):
            raise _error("输入媒体资产不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
        if asset.scan_status != "PASSED":
            raise _error("输入媒体资产尚未通过扫描", http_status=409)
        assets.append(asset)
    existing = crud_provider_job.get_job_by_signature(db, current_user.id, signature)
    if existing is not None:
        return _job_response(existing)
    row = ProviderJob(
        provider_key=provider.provider_key,
        provider_version=provider.provider_version,
        model_version_id=cmd.model_version_id,
        input_asset_ids_json=json.dumps(cmd.input_asset_ids, separators=(",", ":")),
        contract_version=cmd.contract_version,
        config_json=json.dumps(
            cmd.config or {}, ensure_ascii=False, separators=(",", ":")
        ),
        request_signature=signature,
        status="QUEUED",
        total_count=len(assets),
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    try:
        with begin_transaction(db):
            crud_provider_job.create_job(db, row)
    except IntegrityError:
        existing = crud_provider_job.get_job_by_signature(db, current_user.id, signature)
        if existing is not None:
            return _job_response(existing)
        raise
    return _job_response(row)


async def get_job(db: Session, job_id: int, current_user: User) -> ProviderJobResponse:
    row = crud_provider_job.get_job(db, job_id)
    if row is None:
        raise _error("Provider 作业不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    _owner_access(row.created_by, current_user)
    return _job_response(row)


async def list_jobs(
    db: Session,
    current_user: User,
    *,
    status_filter: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
):
    allowed = {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}
    normalized = status_filter.strip().upper() if status_filter else None
    if normalized and normalized not in allowed:
        raise _error("Provider 作业状态不受支持")
    rows, total = crud_provider_job.list_jobs(
        db,
        user_id=current_user.id,
        is_superuser=is_task_superuser(current_user),
        status=normalized,
        search=search,
        page=page,
        size=size,
    )
    return [_job_response(row) for row in rows], total


async def cancel_job(
    db: Session, job_id: int, current_user: User
) -> ProviderJobResponse:
    row = crud_provider_job.get_job(db, job_id)
    if row is None:
        raise _error("Provider 作业不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    _owner_access(row.created_by, current_user)
    if row.status not in {"QUEUED", "RUNNING"}:
        raise _error("当前状态不可取消", http_status=409)
    with begin_transaction(db):
        row.status = "CANCELLED"
        row.completed_at = datetime.now()
        row.updated_by = current_user.id
        db.flush()
    return _job_response(row)


async def create_prediction_set(
    db: Session,
    job_id: int,
    cmd: CreatePredictionSetCommand,
    current_user: User,
    *,
    idempotency_key: str | None = None,
) -> PredictionSetResponse:
    key = (idempotency_key or "").strip()
    if not key:
        raise _error("必须提供 Idempotency-Key", http_status=422)
    if len(key) > 128:
        raise _error("Idempotency-Key 过长", http_status=422)
    job = crud_provider_job.get_job(db, job_id)
    if job is None:
        raise _error("Provider 作业不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    _owner_access(job.created_by, current_user)
    # A result always enters the review queue.  ACCEPTED/REJECTED and other
    # terminal states must be reached through the audited transition endpoint.
    if cmd.review_status != "PENDING":
        raise _error(
            "PredictionSet 初始审核状态必须为 PENDING，请通过审核接口变更",
            http_status=status.HTTP_409_CONFLICT,
        )
    if cmd.sample_id is not None:
        sample = (
            db.query(TaskSample)
            .join(Task, Task.id == TaskSample.task_id)
            .filter(
                TaskSample.id == int(cmd.sample_id), TaskSample.deleted_at.is_(None)
            )
            .first()
        )
        if sample is None:
            raise _error("样本不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
        _task_access(db, int(sample.task_id), current_user)
        if int(sample.task.created_by or 0) != int(job.created_by):
            raise _error("样本与预标注作业创建者不一致", ErrorCode.CODE_30001_NO_PERMISSION, 403)
    if job.status in {"FAILED", "CANCELLED"}:
        raise _error("当前作业不允许写入预测结果", http_status=409)
    existing = crud_provider_job.get_prediction_by_job_version(db, job.id, cmd.version)
    if existing is not None:
        if not _prediction_matches_command(existing, cmd):
            raise _error(
                "相同作业版本已存在不同预测结果，禁止覆盖",
                http_status=status.HTTP_409_CONFLICT,
            )
        return _set_response(existing)
    row = PredictionSet(
        provider_job_id=job.id,
        sample_id=cmd.sample_id,
        version=cmd.version,
        annotation_contract_version=cmd.annotation_contract_version,
        prediction_schema_version=cmd.prediction_schema_version,
        payload_uri=cmd.payload_uri,
        payload_sha256=cmd.payload_sha256,
        result_manifest_uri=cmd.result_manifest_uri,
        result_sha256=cmd.result_sha256,
        payload_json=(
            json.dumps(cmd.payload, ensure_ascii=False, separators=(",", ":"))
            if cmd.payload is not None
            else None
        ),
        status=cmd.status,
        review_status=cmd.review_status,
        source="AI",
        item_count=cmd.item_count,
        metrics_json=json.dumps(
            cmd.metrics or {}, ensure_ascii=False, separators=(",", ":")
        ),
        error_code=cmd.error_code,
        error_message=cmd.error_message,
        created_by=current_user.id,
    )
    try:
        with begin_transaction(db):
            crud_provider_job.create_prediction_set(db, row)
            if job.status == "QUEUED":
                job.status = "RUNNING"
                job.started_at = datetime.now()
            job.completed_count = int(job.completed_count or 0) + 1
            if cmd.status in {"FAILED", "CANCELLED"}:
                job.status = "FAILED" if cmd.status == "FAILED" else "CANCELLED"
            else:
                job.status = "SUCCEEDED"
            job.completed_at = datetime.now()
            job.updated_by = current_user.id
            db.flush()
    except IntegrityError:
        existing = crud_provider_job.get_prediction_by_job_version(
            db, job.id, cmd.version
        )
        if existing is not None:
            if not _prediction_matches_command(existing, cmd):
                raise _error(
                    "相同作业版本已存在不同预测结果，禁止覆盖",
                    http_status=status.HTTP_409_CONFLICT,
                )
            return _set_response(existing)
        raise
    return _set_response(row)


async def transition_prediction_review(
    db: Session,
    set_id: int,
    cmd: TransitionPredictionSetReviewCommand,
    current_user: User,
    *,
    idempotency_key: str | None = None,
) -> PredictionSetResponse:
    """Move a prediction set through review without touching formal labels."""
    row = crud_provider_job.get_prediction_set(db, set_id)
    if row is None:
        raise _error("PredictionSet 不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    _owner_access(row.created_by, current_user)
    target = cmd.target_status
    current = str(row.review_status or "PENDING").upper()
    if current == target:
        return _set_response(row)
    allowed = {
        "PENDING": {"IN_REVIEW", "REJECTED"},
        "IN_REVIEW": {"ACCEPTED", "REJECTED"},
        "ACCEPTED": {"SUPERSEDED"},
        "REJECTED": {"SUPERSEDED"},
        "SUPERSEDED": set(),
    }
    if target not in allowed.get(current, set()):
        raise _error(
            f"PredictionSet 审核状态不能从 {current} 转为 {target}",
            http_status=status.HTTP_409_CONFLICT,
        )
    if target in {"REJECTED", "SUPERSEDED"} and not cmd.reason:
        raise _error(
            "驳回或替代必须填写原因",
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    key = (
        idempotency_key or ""
    ).strip() or f"prediction-review:{row.id}:{current}:{target}:{current_user.id}"
    if len(key) > 128:
        raise _error(
            "Idempotency-Key 过长", http_status=status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    prior = crud_provider_job.get_review_audit_by_key(db, row.id, key)
    if prior is not None:
        if prior.to_status != target:
            raise _error(
                "Idempotency-Key 已用于其他审核目标",
                http_status=status.HTTP_409_CONFLICT,
            )
        return _set_response(row)
    now = datetime.now()
    audit = PredictionSetReviewAudit(
        prediction_set_id=row.id,
        from_status=current,
        to_status=target,
        reason=cmd.reason,
        actor_id=current_user.id,
        idempotency_key=key,
        created_at=now,
    )
    try:
        with begin_transaction(db):
            row.review_status = target
            row.reviewed_by = current_user.id
            row.reviewed_at = now
            row.review_reason = cmd.reason
            crud_provider_job.create_review_audit(db, audit)
            db.flush()
            db.refresh(row)
    except IntegrityError:
        # A retry racing the unique audit key is successful only when the
        # persisted event belongs to this PredictionSet.
        db.rollback()
        prior = crud_provider_job.get_review_audit_by_key(db, row.id, key)
        if prior is None:
            raise
        if prior.to_status != target:
            raise _error(
                "Idempotency-Key 已用于其他审核目标",
                http_status=status.HTTP_409_CONFLICT,
            )
    return _set_response(row)


def _sample_name(sample: TaskSample) -> str:
    """Resolve the legacy pre-annotation sample key deterministically."""
    if getattr(sample, "file", None) is not None and sample.file.filename:
        return str(sample.file.filename)
    try:
        data = json.loads(sample.data or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        data = {}
    if isinstance(data, dict):
        file_data = data.get("file")
        if isinstance(file_data, dict):
            for key in ("filename", "name"):
                if file_data.get(key):
                    return str(file_data[key])
        names = data.get("fileNames")
        if isinstance(names, list) and names and names[0]:
            return str(names[0])
    return str(sample.inner_id or sample.id)


def bridge_legacy_preannotation(
    db: Session,
    *,
    task_id: int,
    sample_id: int | None,
    payload: dict,
    provider_key: str,
    created_by: int,
) -> PredictionSetResponse | None:
    """Ingest one old pre-annotation result into the provider boundary.

    Legacy TagTool/Auto Label/annotation-import workers may still write
    ``TaskPreAnnotation`` for the existing canvas.  The bridge creates an
    immutable ``ProviderJob`` and ``PredictionSet`` in ``PENDING`` review
    state using the task and creator as the scope.  It never auto-accepts the
    result; a missing task or disabled provider returns ``None``.
    """

    task = (
        db.query(Task)
        .filter(Task.id == int(task_id), Task.deleted_at.is_(None))
        .first()
    )
    if task is None or not isinstance(payload, dict):
        return None
    ensure_seeded(db)
    provider = crud_provider_registry.get_provider(db, provider_key, None)
    if provider is None or provider.status != "ACTIVE":
        return None
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(
        f"legacy-preannotation:{int(task_id)}:{int(sample_id or 0)}:{provider.provider_key}:{canonical}".encode(
            "utf-8"
        )
    ).hexdigest()
    signature = f"legacy:{digest}"
    existing_job = crud_provider_job.get_job_by_signature(db, int(created_by), signature)
    if existing_job is not None:
        existing_set = crud_provider_job.get_prediction_by_job_version(
            db, existing_job.id, 1
        )
        return _set_response(existing_set) if existing_set is not None else None
    now = datetime.now()
    input_asset_ids: list[int] = []
    if sample_id is not None:
        sample_row = (
            db.query(TaskSample)
            .filter(TaskSample.id == int(sample_id), TaskSample.deleted_at.is_(None))
            .first()
        )
        attachment = (
            getattr(sample_row, "file", None) if sample_row is not None else None
        )
        media_asset_id = getattr(attachment, "media_asset_id", None)
        if media_asset_id is not None:
            media_asset = crud_ingestion.get_asset(db, int(media_asset_id))
            if media_asset is not None:
                input_asset_ids = [int(media_asset.id)]
    payload_meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    model_version_id = payload_meta.get("model_version_id")
    try:
        model_version_id = (
            int(model_version_id) if model_version_id is not None else None
        )
    except (TypeError, ValueError):
        model_version_id = None
    if model_version_id is not None:
        model_version = crud_model_version.get(db, model_version_id)
        if model_version is None:
            model_version_id = None
    job = ProviderJob(
        provider_key=provider.provider_key,
        provider_version=provider.provider_version,
        model_version_id=model_version_id,
        input_asset_ids_json=json.dumps(input_asset_ids, separators=(",", ":")),
        contract_version="prediction.v1",
        config_json=json.dumps(
            {
                "legacy_task_id": int(task_id),
                "legacy_sample_id": int(sample_id) if sample_id is not None else None,
                "legacy_bridge": True,
            },
            separators=(",", ":"),
        ),
        request_signature=signature,
        status="RUNNING",
        total_count=1,
        completed_count=0,
        created_by=int(created_by),
        updated_by=int(created_by),
        started_at=now,
    )
    prediction = PredictionSet(
        provider_job_id=0,
        sample_id=int(sample_id) if sample_id is not None else None,
        version=1,
        annotation_contract_version="annotation_contract.v1",
        prediction_schema_version="prediction.v1",
        payload_json=canonical,
        status="COMPLETED",
        review_status="PENDING",
        source="LEGACY",
        item_count=1,
        # Filled with the concrete ProviderJob id immediately after insertion;
        # keeping lineage on the immutable snapshot makes legacy results
        # traceable without consulting mutable task/sample data.
        metrics_json=json.dumps({"legacy_bridge": True}, separators=(",", ":")),
        created_by=int(created_by),
    )
    try:
        with begin_transaction(db):
            crud_provider_job.create_job(db, job)
            prediction.provider_job_id = int(job.id)
            prediction.metrics_json = json.dumps(
                {
                    "legacy_bridge": True,
                    "lineage": {
                        "provider_job_id": int(job.id),
                        "provider_key": provider.provider_key,
                        "provider_version": provider.provider_version,
                        "model_version_id": model_version_id,
                        "input_asset_ids": input_asset_ids,
                    },
                },
                separators=(",", ":"),
            )
            crud_provider_job.create_prediction_set(db, prediction)
            job.status = "SUCCEEDED"
            job.completed_count = 1
            job.completed_at = now
            job.updated_by = int(created_by)
            db.flush()
    except IntegrityError:
        db.rollback()
        existing_job = crud_provider_job.get_job_by_signature(db, int(created_by), signature)
        if existing_job is None:
            raise
        existing_set = crud_provider_job.get_prediction_by_job_version(
            db, existing_job.id, 1
        )
        return _set_response(existing_set) if existing_set is not None else None
    return _set_response(prediction)


async def project_prediction_set_to_legacy(
    db: Session,
    set_id: int,
    current_user: User,
    *,
    idempotency_key: str | None = None,
) -> PredictionSetProjectionResponse:
    """Project an accepted inline result to TaskPreAnnotation exactly once.

    This is deliberately a one-way compatibility projection.  It never
    updates ``TaskSample.data`` and never edits an existing pre-annotation.
    """
    row = crud_provider_job.get_prediction_set(db, set_id)
    if row is None:
        raise _error("PredictionSet 不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    _owner_access(row.created_by, current_user)
    existing = crud_provider_job.get_projection(db, row.id)
    if existing is not None:
        return _projection_response(existing)
    if row.review_status != "ACCEPTED":
        raise _error(
            "PredictionSet 必须审核通过后才能投影", http_status=status.HTTP_409_CONFLICT
        )
    if row.sample_id is None:
        raise _error(
            "PredictionSet 未绑定样本，不能投影",
            http_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    sample = (
        db.query(TaskSample)
        .join(Task, Task.id == TaskSample.task_id)
        .filter(TaskSample.id == int(row.sample_id), TaskSample.deleted_at.is_(None))
        .first()
    )
    if sample is None or sample.task is None:
        raise _error("样本不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    _task_access(db, int(sample.task_id), current_user)
    if sample.file_id is not None:
        attachment = (
            db.query(TaskAttachment)
            .filter(
                TaskAttachment.id == int(sample.file_id),
                TaskAttachment.deleted_at.is_(None),
            )
            .first()
        )
        if attachment is None or attachment.task_id != sample.task_id:
            raise _error("样本附件不属于当前任务", http_status=status.HTTP_409_CONFLICT)
    payload = _json_object(row.payload_json)
    if not payload:
        raise _error(
            "PredictionSet 没有可投影的内联结果；请使用 payload 字段创建结果",
            http_status=status.HTTP_409_CONFLICT,
        )
    key = (idempotency_key or "").strip() or f"prediction-projection:{row.id}"
    if len(key) > 128:
        raise _error(
            "Idempotency-Key 过长", http_status=status.HTTP_422_UNPROCESSABLE_ENTITY
        )
    existing = crud_provider_job.get_projection_by_key(db, key)
    if existing is not None:
        if int(existing.prediction_set_id) != int(row.id):
            raise _error(
                "Idempotency-Key 已用于其他投影", http_status=status.HTTP_409_CONFLICT
            )
        return _projection_response(existing)
    import hashlib

    payload_text = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    # Keep the established pre-annotation shape and explicitly identify the
    # source so downstream legacy readers can select it without trusting URI.
    projected = dict(payload)
    raw_meta = projected.get("meta")
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    meta.update(
        {
            "source_type": "ai_generated",
            "provider_source_type": "prediction_set",
            "prediction_set_id": int(row.id),
            "provider_job_id": int(row.provider_job_id),
            "review_status": "ACCEPTED",
            "reviewed_by": row.reviewed_by,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
            "payload_sha256": digest,
            "projection_idempotency_key": key,
        }
    )
    projected["meta"] = meta
    pre_annotation = TaskPreAnnotation(
        task_id=sample.task_id,
        file_id=sample.file_id,
        sample_name=_sample_name(sample),
        data=json.dumps(projected, ensure_ascii=False, separators=(",", ":")),
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    now = datetime.now()
    projection = PredictionSetProjection(
        prediction_set_id=row.id,
        provider_job_id=row.provider_job_id,
        task_id=sample.task_id,
        sample_id=sample.id,
        pre_annotation_id=0,
        payload_sha256=digest,
        status="PROJECTED",
        idempotency_key=key,
        created_by=current_user.id,
        created_at=now,
        metadata_json=json.dumps(
            {"source": "PredictionSet", "formal_annotation_untouched": True},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    try:
        with begin_transaction(db):
            db.add(pre_annotation)
            db.flush()
            projection.pre_annotation_id = pre_annotation.id
            crud_provider_job.create_projection(db, projection)
            db.flush()
            db.refresh(projection)
    except IntegrityError:
        db.rollback()
        existing = crud_provider_job.get_projection(db, row.id)
        if existing is None:
            existing = crud_provider_job.get_projection_by_key(db, key)
        if existing is None:
            raise
        return _projection_response(existing)
    return _projection_response(projection)


async def list_prediction_review_audits(
    db: Session, set_id: int, current_user: User
) -> list[PredictionSetReviewAuditResponse]:
    row = crud_provider_job.get_prediction_set(db, set_id)
    if row is None:
        raise _error("PredictionSet 不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    _owner_access(row.created_by, current_user)
    return [
        PredictionSetReviewAuditResponse(
            id=audit.id,
            prediction_set_id=audit.prediction_set_id,
            from_status=audit.from_status,
            to_status=audit.to_status,
            reason=audit.reason,
            actor_id=audit.actor_id,
            idempotency_key=audit.idempotency_key,
            created_at=audit.created_at,
        )
        for audit in crud_provider_job.list_review_audits(db, set_id)
    ]


async def get_prediction_projection(
    db: Session, set_id: int, current_user: User
) -> PredictionSetProjectionResponse:
    row = crud_provider_job.get_prediction_set(db, set_id)
    if row is None:
        raise _error("PredictionSet 不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    _owner_access(row.created_by, current_user)
    projection = crud_provider_job.get_projection(db, set_id)
    if projection is None:
        raise _error("PredictionSet 尚未投影", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    return _projection_response(projection)


def _projection_response(
    row: PredictionSetProjection,
) -> PredictionSetProjectionResponse:
    return PredictionSetProjectionResponse(
        id=row.id,
        prediction_set_id=row.prediction_set_id,
        provider_job_id=row.provider_job_id,
        task_id=row.task_id,
        sample_id=row.sample_id,
        pre_annotation_id=row.pre_annotation_id,
        payload_sha256=row.payload_sha256,
        status=row.status,
        idempotency_key=row.idempotency_key,
        created_by=row.created_by,
        created_at=row.created_at,
        metadata=_json_object(row.metadata_json),
    )


async def get_prediction_set(
    db: Session, set_id: int, current_user: User
) -> PredictionSetResponse:
    row = crud_provider_job.get_prediction_set(db, set_id)
    if row is None:
        raise _error("PredictionSet 不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
    _owner_access(row.created_by, current_user)
    return _set_response(row)


async def list_prediction_sets(
    db: Session,
    current_user: User,
    *,
    provider_job_id: int | None = None,
    sample_id: int | None = None,
    review_status: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
):
    normalized = review_status.strip().upper() if review_status else None
    if normalized and normalized not in {
        "PENDING",
        "IN_REVIEW",
        "ACCEPTED",
        "REJECTED",
        "SUPERSEDED",
    }:
        raise _error("PredictionSet 审核状态不受支持")
    if provider_job_id is not None:
        job = crud_provider_job.get_job(db, provider_job_id)
        if job is None:
            raise _error("Provider 作业不存在", ErrorCode.CODE_30003_CLIENT_ERROR, 404)
        _owner_access(job.created_by, current_user)
    rows, total = crud_provider_job.list_prediction_sets(
        db,
        user_id=current_user.id,
        is_superuser=is_task_superuser(current_user),
        provider_job_id=provider_job_id,
        sample_id=sample_id,
        review_status=normalized,
        search=search,
        page=page,
        size=size,
    )
    return [_set_response(row) for row in rows], total
