from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_deployment,
    crud_model_version,
    crud_project,
    crud_task,
    crud_workflow,
    crud_yolo_bm1688_export,
)
from labelu.internal.application.command.workflow import (
    CreateWorkflowCommand,
    TransitionWorkflowCommand,
)
from labelu.internal.application.response.workflow import (
    WorkflowEventResponse,
    WorkflowResponse,
    WorkflowStepResponse,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.event_catalog import validate_event_name
from labelu.internal.common.phase_one_contract import ensure_legacy_control_plane_retired
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.workflow import (
    WorkflowEvent,
    WorkflowInstance,
    WorkflowOutbox,
    WorkflowStep,
)

TERMINAL = {"SUCCEEDED", "CANCELLED", "DEAD_LETTER"}
ALLOWED_TYPES = {
    "expert_feedback",
    "threshold_experiment",
    "model_optimization",
    "third_party_callback",
}
ALLOWED_TRANSITIONS = {
    "PENDING": {"RUNNING", "CANCELLED"},
    "RUNNING": {"WAITING", "SUCCEEDED", "FAILED", "CANCELLED"},
    "WAITING": {"RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"},
    "FAILED": {"RUNNING", "DEAD_LETTER", "CANCELLED"},
    "DEAD_LETTER": {"PENDING", "CANCELLED"},
    "SUCCEEDED": set(),
    "CANCELLED": set(),
}


def _err(message: str, http_status: int = 404, code=ErrorCode.CODE_30003_CLIENT_ERROR):
    return LabelUException(code=code, status_code=http_status, message=message)


def _project_access(db: Session, project_id: int, user: User, manage: bool = False):
    project = crud_project.get(db, project_id)
    if project is None:
        raise _err("项目不存在")
    if is_task_superuser(user) or project.owner_id == user.id:
        return project
    member = crud_project.get_member(db, project_id, user.id)
    if (
        not member
        or member.status != "ACTIVE"
        or (manage and member.role not in {"admin", "owner"})
    ):
        raise _err(
            "无权访问该项目",
            status.HTTP_403_FORBIDDEN,
            ErrorCode.CODE_30001_NO_PERMISSION,
        )
    return project


def _workflow_access(
    db: Session, row: WorkflowInstance, user: User, manage: bool = False
):
    """Authorize both historical project workflows and global evolution runs."""
    if row.project_id is not None:
        return _project_access(db, int(row.project_id), user, manage=manage)
    if is_task_superuser(user) or int(row.created_by or 0) == int(user.id) or int(
        row.owner_id or 0
    ) == int(user.id):
        return row
    raise _err(
        "无权访问全局工作流",
        status.HTTP_403_FORBIDDEN,
        ErrorCode.CODE_30001_NO_PERMISSION,
    )


def _json(value: Any) -> str | None:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if value is not None
        else None
    )


def _obj(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


def _deadline_expired(value: datetime | None) -> bool:
    """Compare DB timestamps consistently across SQLite (naive) and MySQL (aware)."""
    if value is None:
        return False
    if value.tzinfo is None:
        return datetime.utcnow() > value
    return datetime.now(timezone.utc) > value.astimezone(timezone.utc)
def _response(row: WorkflowInstance) -> WorkflowResponse:
    return WorkflowResponse(
        id=row.id,
        project_id=row.project_id,
        scope_key=row.scope_key,
        workflow_type=row.workflow_type,
        status=row.status,
        idempotency_key=row.idempotency_key,
        current_step=row.current_step,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        next_run_at=row.next_run_at,
        last_error=row.last_error,
        steps=[
            WorkflowStepResponse(
                id=s.id,
                step_key=s.step_key,
                sequence=s.sequence,
                status=s.status,
                attempts=s.attempts,
                max_attempts=s.max_attempts,
                available_at=s.available_at,
                output=_obj(s.output_json),
                last_error=s.last_error,
                started_at=s.started_at,
                finished_at=s.finished_at,
            )
            for s in row.steps
        ],
        events=[
            WorkflowEventResponse(
                id=e.id,
                event_type=e.event_type,
                from_status=e.from_status,
                to_status=e.to_status,
                step_key=e.step_key,
                payload=_obj(e.payload_json),
                actor_id=e.actor_id,
                created_at=e.created_at,
            )
            for e in row.events
        ],
        created_by=row.created_by,
        owner_id=row.owner_id,
        approver_id=row.approver_id,
        deadline_at=row.deadline_at,
        input_snapshot=_obj(row.input_snapshot_json),
        callback_url=row.callback_url,
        callback_signature=row.callback_signature,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def get_step(
    db: Session, workflow_id: int, step_key: str, user: User
) -> WorkflowStepResponse:
    """Read one workflow step without loading or mutating the whole workflow."""
    row = crud_workflow.get(db, workflow_id)
    if row is None:
        raise _err("工作流不存在")
    _workflow_access(db, row, user)
    step = next((item for item in row.steps if item.step_key == step_key), None)
    if step is None:
        raise _err("工作流步骤不存在", status.HTTP_404_NOT_FOUND)
    return WorkflowStepResponse(
        id=step.id,
        step_key=step.step_key,
        sequence=step.sequence,
        status=step.status,
        attempts=step.attempts,
        max_attempts=step.max_attempts,
        available_at=step.available_at,
        output=_obj(step.output_json),
        last_error=step.last_error,
        started_at=step.started_at,
        finished_at=step.finished_at,
    )


def _event(
    db: Session,
    row: WorkflowInstance,
    event_type: str,
    actor_id: int | None,
    *,
    old: str | None = None,
    new: str | None = None,
    step: str | None = None,
    payload: Any = None,
):
    event_type = validate_event_name(event_type, require_registered=True)
    crud_workflow.add_event(
        db,
        WorkflowEvent(
            workflow_id=row.id,
            event_type=event_type,
            from_status=old,
            to_status=new,
            step_key=step,
            payload_json=_json(payload),
            actor_id=actor_id,
        ),
    )


async def create(
    db: Session,
    cmd: CreateWorkflowCommand,
    user: User,
    *,
    allow_evolution: bool = False,
) -> WorkflowResponse:
    # Public generic workflow writes remain retired. The active-learning
    # service is the governed internal producer for the one supported flow.
    if not allow_evolution:
        ensure_legacy_control_plane_retired()
    project_id = int(cmd.project_id) if cmd.project_id is not None else None
    scope_key = f"project:{project_id}" if project_id is not None else "global"
    if project_id is not None:
        _project_access(db, project_id, user, manage=True)
    existing = crud_workflow.get_by_key(db, cmd.idempotency_key.strip())
    if existing:
        if (
            existing.project_id != project_id
            or existing.workflow_type != cmd.workflow_type
        ):
            raise _err("幂等键已用于其他工作流", status.HTTP_409_CONFLICT)
        return _response(existing)
    keys = [str(item).strip() for item in cmd.steps if str(item).strip()]
    if len(keys) != len(set(keys)):
        raise _err(
            "工作流步骤不能重复",
            status.HTTP_400_BAD_REQUEST,
            ErrorCode.CODE_30002_VALIDATION_ERROR,
        )
    now = datetime.now()
    if cmd.deadline_at is not None and cmd.deadline_at <= datetime.now(cmd.deadline_at.tzinfo):
        raise _err("工作流截止时间必须晚于当前时间", status.HTTP_400_BAD_REQUEST, ErrorCode.CODE_30002_VALIDATION_ERROR)
    owner_id = int(cmd.owner_id or user.id)
    if project_id is None and owner_id != user.id:
        raise _err("全局工作流负责人必须是创建者", status.HTTP_400_BAD_REQUEST, ErrorCode.CODE_30002_VALIDATION_ERROR)
    if project_id is not None and owner_id != user.id:
        member = crud_project.get_member(db, project_id, owner_id)
        if not member or member.status != "ACTIVE":
            raise _err("工作流负责人不是项目成员", status.HTTP_400_BAD_REQUEST, ErrorCode.CODE_30002_VALIDATION_ERROR)
    if cmd.approver_id is not None and project_id is None:
        raise _err("全局工作流不支持指定项目审批人", status.HTTP_400_BAD_REQUEST, ErrorCode.CODE_30002_VALIDATION_ERROR)
    if cmd.approver_id is not None and project_id is not None:
        approver = crud_project.get_member(db, project_id, int(cmd.approver_id))
        if not approver or approver.status != "ACTIVE":
            raise _err("工作流审批人不是项目成员", status.HTTP_400_BAD_REQUEST, ErrorCode.CODE_30002_VALIDATION_ERROR)
    snapshot = {
        "project_id": project_id,
        "scope_key": scope_key,
        "workflow_type": cmd.workflow_type,
        "steps": keys,
        "payload": cmd.payload or {},
        "created_by": int(user.id),
    }
    row = WorkflowInstance(
        project_id=project_id,
        scope_key=scope_key,
        workflow_type=cmd.workflow_type,
        status="PENDING",
        idempotency_key=cmd.idempotency_key.strip(),
        payload_json=_json(cmd.payload),
        current_step=keys[0],
        max_attempts=cmd.max_attempts,
        created_by=user.id,
        owner_id=owner_id,
        approver_id=cmd.approver_id,
        deadline_at=cmd.deadline_at,
        input_snapshot_json=_json(snapshot),
        callback_url=str(cmd.callback_url).strip() if cmd.callback_url else None,
        callback_signature=str(cmd.callback_signature).strip() if cmd.callback_signature else None,
        next_run_at=now,
    )
    with begin_transaction(db):
        crud_workflow.create(db, row)
        for seq, key in enumerate(keys):
            crud_workflow.add_step(
                db,
                WorkflowStep(
                    workflow_id=row.id,
                    step_key=key,
                    sequence=seq,
                    max_attempts=cmd.max_attempts,
                    available_at=now,
                ),
            )
        crud_workflow.add_outbox(
            db,
            WorkflowOutbox(
                workflow_id=row.id,
                event_type=validate_event_name(
                    "workflow.created", require_registered=True
                ),
                aggregate_type="workflow",
                aggregate_id=str(row.id),
                idempotency_key=f"workflow:{row.id}:created",
                payload_json=_json({"workflow_type": row.workflow_type}),
                status="PENDING",
                available_at=now,
                max_attempts=cmd.max_attempts,
            ),
        )
        _event(
            db, row, "workflow.created", user.id, new="PENDING", payload={"steps": keys}
        )
    return _response(row)


async def list_by(
    db: Session,
    project_id: int | None,
    page: int,
    size: int,
    status_filter: str | None,
    user: User,
    search: str | None = None,
    workflow_type: str | None = None,
):
    if project_id is not None:
        _project_access(db, int(project_id), user)
        rows, total = crud_workflow.list_by_project(
            db, int(project_id), page, size, status_filter, search, workflow_type
        )
    else:
        owner_id = None if is_task_superuser(user) else int(user.id)
        rows, total = crud_workflow.list_by_scope(
            db, "global", page, size, status_filter, search, owner_id, workflow_type
        )
    return [_response(row) for row in rows], total


async def get(db: Session, workflow_id: int, user: User) -> WorkflowResponse:
    row = crud_workflow.get(db, workflow_id)
    if row is None:
        raise _err("工作流不存在")
    _workflow_access(db, row, user)
    return _response(row)


async def transition(
    db: Session, workflow_id: int, cmd: TransitionWorkflowCommand, user: User
) -> WorkflowResponse:
    ensure_legacy_control_plane_retired()
    row = crud_workflow.get(db, workflow_id)
    if row is None:
        raise _err("工作流不存在")
    _workflow_access(db, row, user, manage=True)
    old, new = row.status, cmd.status
    if new == "RUNNING" and row.approver_id is not None and int(row.approver_id) != int(user.id):
        raise _err("工作流必须由指定审批人启动", status.HTTP_403_FORBIDDEN, ErrorCode.CODE_30001_NO_PERMISSION)
    if new not in ALLOWED_TRANSITIONS.get(old, set()):
        raise _err(f"不允许从 {old} 转移到 {new}", status.HTTP_409_CONFLICT)
    now = datetime.now()
    with begin_transaction(db):
        row.status = new
        row.updated_at = now
        row.attempts += 1 if new in {"RUNNING", "FAILED"} else 0
        row.last_error = cmd.error
        if new == "FAILED":
            if row.attempts >= row.max_attempts:
                row.status = "DEAD_LETTER"
                new = "DEAD_LETTER"
            else:
                row.next_run_at = now + timedelta(
                    seconds=min(3600, 30 * (2 ** max(0, row.attempts - 1)))
                )
        if new in TERMINAL:
            row.completed_at = now
        if cmd.step_key:
            step = next((s for s in row.steps if s.step_key == cmd.step_key), None)
            if step is None:
                raise _err(
                    "工作流步骤不存在",
                    status.HTTP_400_BAD_REQUEST,
                    ErrorCode.CODE_30002_VALIDATION_ERROR,
                )
            step.status = (
                "RUNNING"
                if new == "RUNNING"
                else (
                    "SUCCEEDED"
                    if new == "SUCCEEDED"
                    else ("FAILED" if new in {"FAILED", "DEAD_LETTER"} else step.status)
                )
            )
            step.attempts += 1 if new in {"RUNNING", "FAILED"} else 0
            step.output_json = _json(cmd.output)
            step.last_error = cmd.error
            if new == "RUNNING":
                step.started_at = now
            step.finished_at = now if step.status in {"SUCCEEDED", "FAILED"} else None
            if new == "SUCCEEDED":
                next_step = next(
                    (
                        candidate
                        for candidate in row.steps
                        if candidate.sequence > step.sequence
                        and candidate.status == "PENDING"
                    ),
                    None,
                )
                row.current_step = next_step.step_key if next_step else None
        _event(
            db,
            row,
            "workflow.transition",
            user.id,
            old=old,
            new=new,
            step=cmd.step_key,
            payload={"error": cmd.error} if cmd.error else cmd.output,
        )
    return _response(crud_workflow.get(db, row.id))


async def replay(db: Session, workflow_id: int, user: User) -> WorkflowResponse:
    ensure_legacy_control_plane_retired()
    row = crud_workflow.get(db, workflow_id)
    if row is None:
        raise _err("工作流不存在")
    _workflow_access(db, row, user, manage=True)
    if row.status != "DEAD_LETTER":
        raise _err("只有死信工作流可以重放", status.HTTP_409_CONFLICT)
    now = datetime.now()
    with begin_transaction(db):
        row.status = "PENDING"
        row.attempts = 0
        row.last_error = None
        row.next_run_at = now
        row.completed_at = None
        for step in row.steps:
            if step.status in {"FAILED", "DEAD_LETTER"}:
                step.status = "PENDING"
                step.attempts = 0
                step.last_error = None
                step.finished_at = None
        crud_workflow.add_outbox(
            db,
            WorkflowOutbox(
                workflow_id=row.id,
                event_type=validate_event_name(
                    "workflow.replay", require_registered=True
                ),
                aggregate_type="workflow",
                aggregate_id=str(row.id),
                idempotency_key=f"workflow:{row.id}:replay:{now.timestamp()}",
                payload_json="{}",
                status="PENDING",
                available_at=now,
                max_attempts=row.max_attempts,
            ),
        )
        _event(db, row, "workflow.replayed", user.id, old="DEAD_LETTER", new="PENDING")
    return _response(row)


def outbox_rows(db: Session, status_filter: str | None = None):
    return crud_workflow.list_outbox(db, status_filter)


def _threshold_recommendation(row: WorkflowInstance) -> dict[str, Any]:
    """Return the latest recommendation produced by this workflow.

    The workflow payload is immutable input; recommendations are read from the
    successful evaluation step so applying a threshold can never accidentally
    use a caller-supplied value that was not evaluated by the workflow.
    """

    for candidate in sorted(row.steps or [], key=lambda item: item.sequence, reverse=True):
        output = _obj(candidate.output_json) or {}
        recommendation = output.get("recommendation")
        if isinstance(recommendation, dict):
            return recommendation
    return {}


def _threshold_apply_approval(
    row: WorkflowInstance, payload: dict[str, Any], user: User
) -> None:
    """Require an explicit, attributable approval before mutating config."""

    if not bool(payload.get("apply_thresholds") or payload.get("auto_apply_thresholds")):
        raise _err(
            "未启用阈值应用，需在工作流 payload 中明确设置 apply_thresholds=true",
            status.HTTP_409_CONFLICT,
        )
    approval_status = str(payload.get("approval_status") or "").strip().upper()
    approved_by = payload.get("approved_by")
    approved = approval_status == "APPROVED" or payload.get("approved") is True
    if approved_by is not None:
        try:
            approved = approved and int(approved_by) == int(user.id)
        except (TypeError, ValueError):
            approved = False
    if row.approver_id is not None:
        if int(row.approver_id) != int(user.id):
            raise _err(
                "阈值应用必须由指定审批人执行",
                status.HTTP_403_FORBIDDEN,
                ErrorCode.CODE_30001_NO_PERMISSION,
            )
    if not approved:
        raise _err(
            "阈值应用缺少明确审批，需 approval_status=APPROVED 或 approved=true",
            status.HTTP_409_CONFLICT,
        )


def _threshold_target_payload(
    db: Session,
    *,
    row: WorkflowInstance,
    payload: dict[str, Any],
    recommendation: dict[str, Any],
    user: User,
) -> dict[str, Any]:
    """Apply one approved recommendation to model/export/deployment config.

    All target IDs are checked against the workflow project.  Export and
    deployment records keep their existing JSON compatibility fields; model
    versions use the postprocess contract, which is the canonical inference
    configuration owned by the model registry.
    """

    from labelu.internal.application.command.strategy_evolution import (
        BUSINESS_THRESHOLD_FIELDS,
        normalize_business_threshold_payload,
    )
    from labelu.internal.application.command.yolo_edge_validation import (
        EDGE_THRESHOLD_ALLOWED_FIELDS,
        EDGE_THRESHOLD_RANGES,
        normalize_edge_threshold_payload,
    )

    business = recommendation.get("recommended_thresholds")
    if not isinstance(business, dict):
        business = recommendation.get("effective_business_thresholds")
    validation = recommendation.get("internal_recommended_thresholds")
    if not isinstance(validation, dict):
        validation = recommendation.get("recommended_validation_thresholds")
    if not isinstance(business, dict) and not isinstance(validation, dict):
        raise _err("工作流未产生可应用的阈值推荐", status.HTTP_409_CONFLICT)
    try:
        business = normalize_business_threshold_payload(business or {}, field_name="recommendation.recommended_thresholds") if isinstance(business, dict) else {}
        # Internal recommendations may carry analysis-only fields such as
        # ``confidence_step``.  Persist only the versioned edge threshold
        # contract; arbitrary analysis metadata must not enter runtime config.
        validation = normalize_edge_threshold_payload(
            {
                key: value
                for key, value in (validation or {}).items()
                if key in EDGE_THRESHOLD_ALLOWED_FIELDS
            },
            field_name="recommendation.internal_recommended_thresholds",
        ) if isinstance(validation, dict) else {}
    except ValueError as exc:
        raise _err(str(exc), status.HTTP_400_BAD_REQUEST, ErrorCode.CODE_30002_VALIDATION_ERROR) from exc
    if not any(
        business.get(key) is not None for key in BUSINESS_THRESHOLD_FIELDS
    ) and not any(validation.get(key) is not None for key in EDGE_THRESHOLD_RANGES):
        raise _err("工作流推荐未包含可应用的阈值数值", status.HTTP_409_CONFLICT)

    now = datetime.now(timezone.utc).isoformat()
    def _target_id(value: Any, field_name: str) -> int:
        if value in (None, "", 0):
            return 0
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise _err(
                f"{field_name} 必须是正整数",
                status.HTTP_400_BAD_REQUEST,
                ErrorCode.CODE_30002_VALIDATION_ERROR,
            ) from exc
        if parsed <= 0:
            raise _err(
                f"{field_name} 必须是正整数",
                status.HTTP_400_BAD_REQUEST,
                ErrorCode.CODE_30002_VALIDATION_ERROR,
            )
        return parsed

    target_model_id = _target_id(payload.get("model_version_id"), "model_version_id")
    target_export_id = _target_id(
        payload.get("export_id")
        or payload.get("source_export_id")
        or payload.get("derived_export_id"),
        "export_id",
    )
    target_release_id = _target_id(payload.get("deployment_release_id"), "deployment_release_id")
    applied: list[str] = []
    application_key = f"workflow:{int(row.id)}:thresholds"
    binding = {
        "application_key": application_key,
        "source_workflow_id": int(row.id),
        "project_id": int(row.project_id),
        "applied_by": int(user.id),
        "applied_at": now,
        "threshold_unit": "ratio_0_1",
        "effective_business_thresholds": business or None,
        "effective_validation_thresholds": validation or None,
    }

    if target_model_id:
        model = crud_model_version.get(db, target_model_id)
        if model is None or int(model.project_id) != int(row.project_id):
            raise _err("阈值应用模型不存在或不属于当前项目", status.HTTP_409_CONFLICT)
        if str(getattr(model, "status", "")).upper() == "PUBLISHED":
            raise _err("已发布模型版本不可直接修改阈值，请创建新版本或更新部署配置", status.HTTP_409_CONFLICT)
        contract = _obj(model.postprocess_contract_json) or {}
        old_binding = contract.get("threshold_binding")
        if isinstance(old_binding, dict) and old_binding.get("application_key") == application_key:
            applied.append(f"model_version:{target_model_id}:idempotent")
        else:
            contract["threshold_unit"] = "ratio_0_1"
            contract["business_thresholds"] = business or None
            contract["validation_thresholds"] = validation or None
            contract["threshold_binding"] = binding
            model.postprocess_contract_json = _json(contract)
            model.updated_at = datetime.now()
            applied.append(f"model_version:{target_model_id}")

    export = None
    if target_export_id:
        export = crud_yolo_bm1688_export.get(db, target_export_id)
        task = crud_task.get(db, int(export.task_id)) if export is not None else None
        if export is None or task is None or int(getattr(task, "project_id", 0) or 0) != int(row.project_id):
            raise _err("阈值应用导出不存在或不属于当前项目", status.HTTP_409_CONFLICT)
        existing_binding = _obj(export.threshold_binding_json)
        if isinstance(existing_binding, dict) and existing_binding.get("application_key") == application_key:
            applied.append(f"export:{target_export_id}:idempotent")
        else:
            export.business_thresholds_json = _json({"unit": "ratio_0_1", "effective": business} if business else None)
            export.validation_thresholds_json = _json({"unit": "ratio_0_1", "effective": validation} if validation else None)
            export.threshold_binding_json = _json(binding)
            export.updated_at = datetime.now()
            applied.append(f"export:{target_export_id}")

    if target_release_id:
        release = crud_deployment.get_release(db, target_release_id)
        if release is None or int(release.project_id) != int(row.project_id):
            raise _err("阈值应用部署发布不存在或不属于当前项目", status.HTTP_409_CONFLICT)
        metrics = _obj(release.metrics_json) or {}
        old_binding = metrics.get("threshold_binding")
        if isinstance(old_binding, dict) and old_binding.get("application_key") == application_key:
            applied.append(f"deployment_release:{target_release_id}:idempotent")
        else:
            metrics["threshold_config"] = {
                "threshold_unit": "ratio_0_1",
                "business_thresholds": business or None,
                "validation_thresholds": validation or None,
            }
            metrics["threshold_binding"] = binding
            release.metrics_json = _json(metrics)
            release.updated_at = datetime.now()
            applied.append(f"deployment_release:{target_release_id}")

    if not applied:
        raise _err(
            "未指定阈值应用目标，需提供 model_version_id、export_id 或 deployment_release_id",
            status.HTTP_400_BAD_REQUEST,
            ErrorCode.CODE_30002_VALIDATION_ERROR,
        )
    return {
        "status": "SUCCEEDED",
        "stage": "thresholds_applied",
        "application_key": application_key,
        "targets": applied,
        "threshold_binding": binding,
    }


def outbox_for_user(
    db: Session, outbox_id: int, user: User, *, manage: bool = True
) -> WorkflowOutbox:
    row = crud_workflow.get_outbox(db, outbox_id)
    if row is None:
        raise _err("Outbox 事件不存在")
    if not is_task_superuser(user):
        raise _err(
            "仅管理人员可处理异常任务",
            status.HTTP_403_FORBIDDEN,
            ErrorCode.CODE_30001_NO_PERMISSION,
        )
    return row


def execute_step(
    db: Session,
    workflow_id: int,
    step_key: str,
    user: User,
    *,
    allow_evolution: bool = False,
) -> dict[str, Any]:
    """Execute the built-in active-learning hand-off steps.

    The worker is intentionally conservative: a dataset version that has not
    passed validation and been frozen remains WAITING, and no plan or training
    job is created.  Re-running a successful step returns its stored output.
    """
    if not allow_evolution:
        ensure_legacy_control_plane_retired()
    row = crud_workflow.get(db, workflow_id)
    if row is None:
        raise _err("工作流不存在")
    _workflow_access(db, row, user, manage=True)
    step = next((item for item in row.steps if item.step_key == step_key), None)
    if step is None:
        raise _err("工作流步骤不存在", status.HTTP_400_BAD_REQUEST)
    if step.status == "SUCCEEDED":
        return _obj(step.output_json) or {"status": "SUCCEEDED"}
    if row.status in TERMINAL:
        raise _err("终态工作流不可执行未完成步骤，请先重放死信工作流", status.HTTP_409_CONFLICT)
    if any(
        previous.sequence < step.sequence and previous.status != "SUCCEEDED"
        for previous in row.steps
    ):
        raise _err("前置工作流步骤尚未完成", status.HTTP_409_CONFLICT)

    if _deadline_expired(row.deadline_at):
        raise _err("工作流已超过截止时间", status.HTTP_409_CONFLICT)
    payload = _obj(row.payload_json) or {}
    version_id = int(payload.get("dataset_version_id") or 0)
    if row.workflow_type == "expert_feedback" and step_key in {
        "prepare_dataset",
        "trigger_training",
    }:
        from labelu.internal.adapter.persistence import crud_dataset

        version = crud_dataset.get_version(db, version_id) if version_id else None
        version_project_id = getattr(version, "project_id", None) if version is not None else None
        if version is None or (
            row.project_id is not None
            and version_project_id is not None
            and int(version_project_id) != int(row.project_id)
        ):
            raise _err("工作流数据集版本与项目不一致", status.HTTP_409_CONFLICT)
        if version.status != "FROZEN":
            now = datetime.now()
            row.status = "WAITING"
            row.current_step = step.step_key
            row.last_error = "数据集版本尚未冻结"
            row.next_run_at = now
            step.available_at = now
            _event(
                db,
                row,
                "workflow.step.waiting",
                user.id,
                step=step.step_key,
                payload={"reason": "DATASET_VERSION_NOT_FROZEN", "dataset_version_id": version_id},
            )
            db.flush()
            return {
                "status": "WAITING",
                "reason": "DATASET_VERSION_NOT_FROZEN",
                "dataset_version_id": version_id,
            }
        if step_key == "trigger_training":
            from labelu.internal.application.service.auto_training_plan import (
                create_plan_from_frozen_dataset_version,
            )

            plan = create_plan_from_frozen_dataset_version(
                db, user=user, dataset_version_id=version_id
            )
            output = {
                "status": "SUCCEEDED",
                "dataset_version_id": version_id,
                "plan_id": int(plan["id"]),
                "plan_status": plan["status"],
                "training_job_created": False,
            }
        else:
            output = {
                "status": "SUCCEEDED",
                "dataset_version_id": version_id,
                "manifest_sha256": version.manifest_sha256,
                "manifest_rel_path": version.manifest_rel_path,
            }
    elif row.workflow_type == "threshold_experiment":
        # Keep the orchestration generic: the existing strategy service owns
        # threshold semantics while the workflow records an immutable result.
        from labelu.internal.application.service import strategy_evolution
        experiment = payload.get("experiment") if isinstance(payload.get("experiment"), dict) else payload
        if step_key in {"prepare", "prepare_experiment"}:
            output = {"status": "SUCCEEDED", "stage": "prepared", "experiment": experiment}
        elif step_key in {"evaluate", "run", "optimize_threshold"}:
            output = strategy_evolution._build_threshold_recommendation(
                db, experiment, result={}, job_summary={}
            )
            output = {"status": "SUCCEEDED", "stage": "evaluated", "recommendation": output}
        elif step_key in {"apply", "apply_thresholds"} or (
            step_key == "finalize" and bool(payload.get("apply_thresholds") or payload.get("auto_apply_thresholds"))
        ):
            # Mutation is deliberately restricted to an explicit approval
            # payload.  A normal ``finalize`` step therefore remains a no-op
            # unless the caller opts into applying the evaluated recommendation.
            _threshold_apply_approval(row, payload, user)
            recommendation = _threshold_recommendation(row)
            output = _threshold_target_payload(
                db,
                row=row,
                payload=payload,
                recommendation=recommendation,
                user=user,
            )
            _event(
                db,
                row,
                "7gai.workflow.thresholds.applied.v1",
                user.id,
                step=step.step_key,
                payload=output,
            )
        else:
            output = {"status": "SUCCEEDED", "stage": "completed", "thresholds": experiment.get("requested_thresholds", []) if isinstance(experiment, dict) else []}
    elif row.workflow_type == "model_optimization":
        if step_key in {"prepare", "optimize", "run_optimization"}:
            model_version_id = payload.get("model_version_id")
            if model_version_id:
                from labelu.internal.application.service.model_optimization import run_local_sync

                try:
                    optimization = run_local_sync(
                        db,
                        project_id=int(row.project_id),
                        workflow_id=int(row.id),
                        model_version_id=int(model_version_id),
                        parameters=payload.get("parameters") if isinstance(payload.get("parameters"), dict) else {},
                        created_by=int(user.id),
                        idempotency_key=f"workflow:{row.id}:model-optimization:{step_key}",
                    )
                except ValueError as exc:
                    raise _err(str(exc), status.HTTP_409_CONFLICT) from exc
                output = {
                    "status": "SUCCEEDED",
                    "stage": "optimization_recorded",
                    "model_version_id": int(model_version_id),
                    "job_id": int(optimization["job_id"]),
                    "replayed": bool(optimization.get("replayed")),
                    "result": optimization["result"],
                }
            else:
                # Keep legacy workflows readable while making the missing
                # model binding explicit; no optimization job is fabricated.
                output = {"status": "SUCCEEDED", "stage": "optimization_requested", "model_version_id": None, "job_created": False, "parameters": payload.get("parameters", {})}
        else:
            output = {"status": "SUCCEEDED", "stage": "completed", "model_version_id": payload.get("model_version_id")}
    elif row.workflow_type == "third_party_callback":
        callback = row.callback_url or payload.get("callback_url")
        signature = row.callback_signature or payload.get("callback_signature")
        if not callback or not urllib.parse.urlparse(str(callback)).scheme:
            raise _err("第三方回调地址未配置", status.HTTP_409_CONFLICT)
        if not signature:
            raise _err("第三方回调签名未配置", status.HTTP_409_CONFLICT)
        from labelu.internal.application.service.flexible_callback import CALLBACK_EVENT_TYPE

        callback_event_id = f"workflow-{row.id}-callback"
        callback_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
        snapshot = {
            "project_id": int(row.project_id),
            "callback_url": str(callback),
            "callback_event_id": callback_event_id,
            "payload": callback_payload,
            "headers": {
                "Content-Type": "application/json",
                "X-LabelU-Event-Id": callback_event_id,
                **({"X-LabelU-Signature": str(signature)} if signature else {}),
            },
        }
        outbox_key = f"workflow:{row.id}:callback:{step_key}"
        existing = db.query(WorkflowOutbox).filter(WorkflowOutbox.idempotency_key == outbox_key).first()
        if existing is None:
            crud_workflow.add_outbox(
                db,
                WorkflowOutbox(
                    workflow_id=row.id,
                    event_type=validate_event_name(CALLBACK_EVENT_TYPE, require_registered=True),
                    aggregate_type="third_party_callback",
                    aggregate_id=str(row.id),
                    idempotency_key=outbox_key,
                    payload_json=_json(snapshot),
                    status="PENDING",
                    max_attempts=row.max_attempts,
                    available_at=datetime.now(),
                ),
            )
        # The step is queued, not reported as delivered. The dispatcher owns
        # transport and will move the workflow to success or dead-letter.
        output = {"status": "WAITING", "stage": "callback_queued", "callback_url": str(callback), "callback_event_id": callback_event_id, "signature_present": bool(signature)}
        step.status = "WAITING"
        row.status = "WAITING"
        row.current_step = step.step_key
        row.next_run_at = datetime.now()
        _event(db, row, "workflow.step.waiting", user.id, new="WAITING", step=step.step_key, payload=output)
        db.flush()
        return output
    else:
        raise _err("当前工作流步骤没有可用执行器", status.HTTP_409_CONFLICT)

    now = datetime.now()
    step.status = "SUCCEEDED"
    step.output_json = _json(output)
    step.last_error = None
    step.finished_at = now
    row.last_error = None
    row.updated_at = now
    next_step = next(
        (item for item in row.steps if item.sequence > step.sequence and item.status == "PENDING"),
        None,
    )
    row.current_step = next_step.step_key if next_step else None
    row.status = "RUNNING" if next_step else "SUCCEEDED"
    if not next_step:
        row.completed_at = now
    _event(
        db,
        row,
        "workflow.step.succeeded",
        user.id,
        new=row.status,
        step=step.step_key,
        payload=output,
    )
    db.flush()
    return output


def advance_expert_feedback_for_dataset(
    db: Session, *, dataset_version_id: int, user: User
) -> list[dict[str, Any]]:
    """Advance active-learning workflows after their dataset is frozen.

    The dataset service calls this inside the same transaction that changes the
    version to ``FROZEN``. This keeps the freeze, workflow audit events and
    governed DRAFT plan creation atomic while retaining the legacy per-step
    execution endpoints for retries and operations.
    """

    from labelu.internal.adapter.persistence import crud_dataset
    from labelu.internal.domain.models.active_learning import ActiveLearningBatch

    version = crud_dataset.get_version(db, int(dataset_version_id))
    if version is None:
        raise _err("数据集版本不存在")
    if version.status != "FROZEN":
        raise _err("数据集版本尚未冻结", status.HTTP_409_CONFLICT)

    version_project_id = getattr(version, "project_id", None)
    workflow_query = db.query(WorkflowInstance).filter(
        WorkflowInstance.workflow_type == "expert_feedback",
        WorkflowInstance.status.in_({"PENDING", "RUNNING", "WAITING"}),
    )
    if version_project_id is None:
        workflow_query = workflow_query.filter(WorkflowInstance.scope_key == "global")
    else:
        workflow_query = workflow_query.filter(WorkflowInstance.project_id == int(version_project_id))
    workflows = (
        workflow_query
        .order_by(WorkflowInstance.id.asc())
        .all()
    )
    results: list[dict[str, Any]] = []
    for workflow in workflows:
        payload = _obj(workflow.payload_json) or {}
        # Only the active-learning accept payload opts into automatic
        # advancement. Hand-authored expert-feedback workflows remain on the
        # legacy explicit step endpoints even when they reference a dataset.
        if (
            not int(payload.get("batch_id") or 0)
            or int(payload.get("dataset_version_id") or 0) != int(version.id)
        ):
            continue
        batch_query = db.query(ActiveLearningBatch).filter(
            ActiveLearningBatch.id == int(payload["batch_id"]),
            ActiveLearningBatch.workflow_id == int(workflow.id),
            ActiveLearningBatch.dataset_version_id == int(version.id),
            ActiveLearningBatch.status == "ACCEPTED",
        )
        if version_project_id is None:
            batch_query = batch_query.filter(ActiveLearningBatch.scope_key == "global")
        else:
            batch_query = batch_query.filter(ActiveLearningBatch.project_id == int(version_project_id))
        batch = batch_query.first()
        if batch is None:
            raise _err(
                "主动学习批次与工作流/数据集版本血缘不一致",
                status.HTTP_409_CONFLICT,
            )
        for step_key in ("prepare_dataset", "trigger_training"):
            step = next(
                (item for item in workflow.steps if item.step_key == step_key), None
            )
            if step is None:
                raise _err(
                    "主动学习工作流缺少必需步骤",
                    status.HTTP_409_CONFLICT,
                )
            output = execute_step(db, workflow.id, step_key, user, allow_evolution=True)
            results.append(
                {
                    "workflow_id": int(workflow.id),
                    "step_key": step_key,
                    **output,
                }
            )
    return results


def retry_outbox(db: Session, outbox_id: int, user: User) -> dict[str, Any]:
    row = outbox_for_user(db, outbox_id, user)
    workflow = crud_workflow.get(db, row.workflow_id)
    if workflow is None:
        raise _err("工作流不存在")
    if row.status not in {"FAILED", "RETRY", "DEAD_LETTER"}:
        raise _err("当前 Outbox 状态不可重试", status.HTTP_409_CONFLICT)
    now = datetime.now()
    was_dead_letter = row.status == "DEAD_LETTER"
    with begin_transaction(db):
        row.status = "RETRY"
        row.attempts = 0 if was_dead_letter else row.attempts
        row.available_at = now
        row.last_error = None
        row.lease_owner = None
        row.lease_expires_at = None
        row.updated_at = now
        _event(db, workflow, "outbox.replayed", user.id, payload={"outbox_id": row.id})
    return {
        "id": row.id,
        "workflow_id": row.workflow_id,
        "status": row.status,
        "attempts": row.attempts,
        "available_at": row.available_at,
    }


def fail_outbox(db: Session, outbox_id: int, error: str) -> WorkflowOutbox:
    """Worker hook: persist bounded exponential retry and dead-letter state."""
    row = crud_workflow.get_outbox(db, outbox_id)
    if row is None:
        raise _err("Outbox 事件不存在")
    now = datetime.now()
    with begin_transaction(db):
        row.attempts += 1
        row.last_error = str(error or "处理失败")[:4000]
        if row.attempts >= row.max_attempts:
            row.status = "DEAD_LETTER"
            row.available_at = None
        else:
            row.status = "RETRY"
            row.available_at = now + timedelta(
                seconds=min(3600, 30 * (2 ** max(0, row.attempts - 1)))
            )
        row.updated_at = now
        workflow = crud_workflow.get(db, row.workflow_id)
        if workflow is not None:
            _event(
                db,
                workflow,
                "outbox.failed",
                None,
                payload={
                    "outbox_id": row.id,
                    "error": row.last_error,
                    "status": row.status,
                },
            )
    return row
