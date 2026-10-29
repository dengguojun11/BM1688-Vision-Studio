from __future__ import annotations

import json
from datetime import datetime

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_edge_feedback,
    crud_project,
    crud_model_version,
)
from labelu.internal.application.command.edge_feedback import (
    CreateInferenceEventCommand,
    ReviewHardExampleCommand,
)
from labelu.internal.application.response.edge_feedback import HardExampleResponse
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.edge_feedback import EdgeHardExample, InferenceEvent
from labelu.internal.domain.models.user import User


def _error(
    message: str,
    code=ErrorCode.CODE_30003_CLIENT_ERROR,
    http_status=status.HTTP_404_NOT_FOUND,
):
    return LabelUException(code=code, status_code=http_status, message=message)


def _project(db: Session, project_id: int | None, user: User, manage=False):
    if project_id is None:
        if manage and not is_task_superuser(user):
            raise _error(
                "全局难例审核仅允许管理人员操作",
                ErrorCode.CODE_30001_NO_PERMISSION,
                status.HTTP_403_FORBIDDEN,
            )
        return None
    project = crud_project.get(db, project_id)
    if project is None:
        raise _error("项目不存在")
    if is_task_superuser(user) or project.owner_id == user.id:
        return project
    member = crud_project.get_member(db, project_id, user.id)
    if (
        not member
        or member.status != "ACTIVE"
        or (manage and member.role not in {"admin", "owner"})
    ):
        raise _error(
            "无权访问该项目",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    return project


def _response(row: EdgeHardExample) -> HardExampleResponse:
    return HardExampleResponse(
        id=row.id,
        project_id=row.project_id,
        scope_key=row.scope_key,
        created_by=row.created_by,
        inference_event_id=row.inference_event_id,
        device_id=row.device_id,
        model_version_id=row.model_version_id,
        fingerprint=row.fingerprint,
        asset_uri=row.asset_uri,
        confidence=row.confidence,
        hard_example_type=row.hard_example_type,
        review_status=row.review_status,
        review_reason=row.review_reason,
        training_status=row.training_status,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
        created_at=row.created_at,
    )


async def ingest(
    db: Session, cmd: CreateInferenceEventCommand, user: User
) -> HardExampleResponse:
    _project(db, cmd.project_id, user, manage=False)
    scope_key = f"project:{cmd.project_id}" if cmd.project_id is not None else "global"
    if cmd.model_version_id is not None:
        model = crud_model_version.get(db, cmd.model_version_id)
        if model is None:
            raise _error(
                "模型版本不存在",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_400_BAD_REQUEST,
            )
    if cmd.asset_sha256 and any(
        ch not in "0123456789abcdefABCDEF" for ch in cmd.asset_sha256
    ):
        raise _error(
            "asset_sha256 必须是十六进制摘要",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    existing = crud_edge_feedback.hard_by_fingerprint(
        db, cmd.project_id, cmd.fingerprint, scope_key
    )
    if existing:
        return _response(existing)
    event = crud_edge_feedback.event_by_key(db, cmd.project_id, cmd.event_key, scope_key)
    if event is None:
        event = InferenceEvent(
            project_id=cmd.project_id,
            scope_key=scope_key,
            created_by=user.id,
            device_id=cmd.device_id,
            model_version_id=cmd.model_version_id,
            event_key=cmd.event_key,
            event_type=cmd.event_type.upper(),
            confidence=cmd.confidence,
            asset_uri=cmd.asset_uri,
            asset_sha256=cmd.asset_sha256,
            payload_json=json.dumps(cmd.payload, ensure_ascii=False),
        )
        try:
            with begin_transaction(db):
                crud_edge_feedback.create_event(db, event)
        except IntegrityError:
            # Another edge retry may have won the event-key race. Converge to
            # that durable row and continue through the immutable-field checks
            # below instead of leaking a database error to the agent.
            db.rollback()
            event = crud_edge_feedback.event_by_key(db, cmd.project_id, cmd.event_key, scope_key)
            if event is None:
                raise
    else:
        immutable_mismatch = (
            event.device_id != cmd.device_id
            or event.model_version_id != cmd.model_version_id
            or event.event_type != cmd.event_type.upper()
            or event.confidence != cmd.confidence
        )
        if immutable_mismatch:
            raise _error(
                "event_key 已绑定其他推理事件",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        existing_hard = crud_edge_feedback.hard_by_event(db, event.id)
        if existing_hard is not None:
            return _response(existing_hard)
    hard = EdgeHardExample(
        project_id=cmd.project_id,
        scope_key=scope_key,
        created_by=user.id,
        inference_event_id=event.id,
        device_id=cmd.device_id,
        model_version_id=cmd.model_version_id,
        fingerprint=cmd.fingerprint,
        asset_uri=cmd.asset_uri,
        confidence=cmd.confidence,
        hard_example_type=cmd.event_type.upper(),
    )
    try:
        with begin_transaction(db):
            crud_edge_feedback.create_hard(db, hard)
            # Make a newly reported low-confidence example visible to the
            # active-learning queue immediately. The rebuild endpoint still
            # performs a complete project-wide reconciliation when policy
            # changes.
            from labelu.internal.application.service.active_learning import (
                refresh_cluster_for_hard_example,
            )

            # Projectless events use the global evolution scope and must enter
            # the same cluster queue immediately as historical project events.
            refresh_cluster_for_hard_example(db, hard_example=hard)
    except IntegrityError:
        # Fingerprint uniqueness is the idempotency boundary for repeated edge
        # uploads. Return the committed winner when a concurrent request wins.
        db.rollback()
        existing = crud_edge_feedback.hard_by_fingerprint(
            db, cmd.project_id, cmd.fingerprint, scope_key
        )
        if existing is not None:
            return _response(existing)
        existing = crud_edge_feedback.hard_by_event(db, event.id)
        if existing is not None:
            return _response(existing)
        raise _error(
            "难例指纹已被其他请求占用，请使用原指纹重试",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    return _response(hard)


async def list_by(
    db: Session,
    project_id: int | None,
    page: int,
    size: int,
    status_filter: str | None,
    user: User,
    search: str | None = None,
):
    _project(db, project_id, user)
    rows, total = crud_edge_feedback.list_hard(
        db,
        project_id,
        page,
        size,
        status_filter,
        search=search,
        scope_key="project:" + str(project_id) if project_id is not None else None,
        creator_id=user.id,
        unrestricted=is_task_superuser(user),
    )
    return [_response(row) for row in rows], total


async def get_by_id(db: Session, hard_id: int, user: User) -> HardExampleResponse:
    row = crud_edge_feedback.get_hard(db, hard_id)
    if row is None:
        raise _error("难例不存在")
    _project(db, row.project_id, user)
    if row.project_id is None and not is_task_superuser(user) and row.created_by != user.id:
        raise _error("无权访问该难例", ErrorCode.CODE_30001_NO_PERMISSION, status.HTTP_403_FORBIDDEN)
    return _response(row)


async def review(
    db: Session, hard_id: int, cmd: ReviewHardExampleCommand, user: User
) -> HardExampleResponse:
    row = crud_edge_feedback.get_hard(db, hard_id)
    if row is None:
        raise _error("难例不存在")
    _project(db, row.project_id, user, manage=True)
    if row.project_id is None and not is_task_superuser(user) and row.created_by != user.id:
        raise _error("无权审核该难例", ErrorCode.CODE_30001_NO_PERMISSION, status.HTTP_403_FORBIDDEN)
    if cmd.decision == "REJECTED" and not (cmd.reason or "").strip():
        raise _error(
            "拒绝难例必须填写原因",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    with begin_transaction(db):
        row.review_status = cmd.decision
        row.review_reason = cmd.reason
        row.reviewed_by = user.id
        row.reviewed_at = datetime.now()
        row.training_status = "READY" if cmd.decision == "ACCEPTED" else "NOT_INCLUDED"
        # SessionLocal disables autoflush; make the new review state visible to
        # the queue query before recalculating the affected cluster.
        db.flush()
        from labelu.internal.application.service.active_learning import (
            refresh_cluster_for_hard_example,
        )

        refresh_cluster_for_hard_example(db, hard_example=row)
        db.flush()
    return _response(row)
