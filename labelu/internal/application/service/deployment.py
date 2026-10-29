from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_deployment,
    crud_model_version,
    crud_project,
)
from labelu.internal.application.command.deployment import (
    ApproveDeploymentReleaseCommand,
    CreateDeploymentObservationCommand,
    CreateDeploymentReleaseCommand,
    DeploymentTransitionCommand,
    ReportDeploymentTargetCommand,
)
from labelu.internal.application.response.deployment import (
    DeploymentObservationResponse,
    DeploymentReleaseResponse,
    DeploymentTargetResponse,
    DeploymentTransitionAuditResponse,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.phase_one_contract import ensure_legacy_control_plane_retired
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.deployment import (
    DeploymentObservation,
    DeploymentRelease,
    DeploymentTarget,
    DeploymentTransitionAudit,
)
from labelu.internal.domain.models.edge_agent import EdgeAgent
from labelu.internal.domain.models.user import User

REQUIRED_MODEL_GATES = ("training", "generalization", "conversion", "edge", "release")


def _error(
    message: str,
    code=ErrorCode.CODE_30003_CLIENT_ERROR,
    http_status=status.HTTP_404_NOT_FOUND,
):
    return LabelUException(code=code, status_code=http_status, message=message)


def _json(value: Any, default: Any):
    if not value:
        return default
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed


def _project(db: Session, project_id: int):
    row = crud_project.get(db, project_id)
    if row is None:
        raise _error("项目不存在")
    return row


def _can_access(db: Session, project, user: User, *, manage: bool = False) -> bool:
    if is_task_superuser(user) or project.owner_id == user.id:
        return True
    member = crud_project.get_member(db, project.id, user.id)
    if not member or member.status != "ACTIVE":
        return False
    return not manage or member.role == "admin"


def _require_project(db: Session, project_id: int, user: User, *, manage: bool = False):
    project = _project(db, project_id)
    if not _can_access(db, project, user, manage=manage):
        raise _error(
            "无权访问该项目",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    return project


def _target_response(
    row: DeploymentTarget, observations=None
) -> DeploymentTargetResponse:
    return DeploymentTargetResponse(
        id=row.id,
        release_id=row.release_id,
        device_id=row.device_id,
        desired_state=row.desired_state,
        reported_state=row.reported_state,
        target_bundle_sha256=row.target_bundle_sha256,
        reported_bundle_sha256=row.reported_bundle_sha256,
        agent_version=row.agent_version,
        capabilities=_json(row.capabilities_json, {}),
        metrics=_json(row.metrics_json, {}),
        error_message=row.error_message,
        last_heartbeat_at=row.last_heartbeat_at,
        activated_at=row.activated_at,
        completed_at=row.completed_at,
        updated_at=row.updated_at,
        fencing_token=int(row.fencing_token or 0),
        observations=[_observation_response(item) for item in (observations or [])],
    )


def _observation_response(row: DeploymentObservation) -> DeploymentObservationResponse:
    return DeploymentObservationResponse(
        id=row.id,
        target_id=row.target_id,
        observation_key=row.observation_key,
        status=row.status,
        fps=row.fps,
        latency_p95_ms=row.latency_p95_ms,
        latency_p99_ms=row.latency_p99_ms,
        cpu_percent=row.cpu_percent,
        npu_percent=row.npu_percent,
        memory_percent=row.memory_percent,
        temperature_c=row.temperature_c,
        metrics=_json(row.metrics_json, {}),
        error_message=row.error_message,
        sampled_at=row.sampled_at,
    )


def _response(row: DeploymentRelease) -> DeploymentReleaseResponse:
    return DeploymentReleaseResponse(
        id=row.id,
        project_id=row.project_id,
        model_version_id=row.model_version_id,
        release_key=row.release_key,
        status=row.status,
        target_device_ids=_json(row.target_device_ids_json, []),
        gray_ratio=row.gray_ratio,
        observation_seconds=row.observation_seconds,
        rollback_model_version_id=row.rollback_model_version_id,
        metrics=_json(row.metrics_json, {}),
        fencing_token=int(row.fencing_token or 0),
        created_by=row.created_by,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        targets=[
            _target_response(target, target.observations)
            for target in (row.targets or [])
        ],
    )


def _audit_snapshot(value: Any) -> str:
    try:
        return json.dumps(value if isinstance(value, dict) else {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _release_snapshot(row: DeploymentRelease) -> dict[str, Any]:
    return {
        "status": str(row.status or "DRAFT").upper(),
        "fencing_token": int(row.fencing_token or 0),
        "metrics": _json(row.metrics_json, {}),
    }


def _target_snapshot(row: DeploymentTarget) -> dict[str, Any]:
    return {
        "id": row.id,
        "device_id": row.device_id,
        "reported_state": str(row.reported_state or "UNKNOWN").upper(),
        "desired_state": str(row.desired_state or "PENDING").upper(),
        "fencing_token": int(row.fencing_token or 0),
        "reported_bundle_sha256": row.reported_bundle_sha256,
        "error_message": row.error_message,
    }


def _audit_context(command=None) -> tuple[str, str, str | None]:
    """Resolve request/correlation/idempotency values without coupling routers."""
    from labelu.internal.common.business_log import get_request_context

    context = get_request_context()
    request_id = str(context.get("request_id") or "-")
    if request_id == "-":
        request_id = secrets.token_hex(12)
    correlation_id = getattr(command, "correlation_id", None) or request_id
    idempotency_key = getattr(command, "idempotency_key", None)
    return (
        request_id[:128],
        str(correlation_id)[:128],
        (str(idempotency_key)[:160] if idempotency_key else None),
    )


def _append_audit(
    db: Session,
    *,
    release: DeploymentRelease,
    operation: str,
    from_status: str | None,
    to_status: str | None,
    actor_type: str,
    actor_id: int | None = None,
    agent_id: str | None = None,
    target: DeploymentTarget | None = None,
    reason: str | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
    idempotency_key: str | None = None,
    fencing_token: int | None = None,
    before_snapshot: dict[str, Any] | None = None,
    after_snapshot: dict[str, Any] | None = None,
) -> DeploymentTransitionAudit:
    if idempotency_key:
        existing = crud_deployment.get_transition_audit_by_idempotency(
            db, idempotency_key
        )
        if existing is not None:
            if (
                existing.release_id != release.id
                or existing.operation != str(operation).lower()
            ):
                raise _error(
                    "idempotency_key 已用于其他部署操作",
                    ErrorCode.CODE_30003_CLIENT_ERROR,
                    status.HTTP_409_CONFLICT,
                )
            return existing
    audit = DeploymentTransitionAudit(
        release_id=release.id,
        target_id=target.id if target is not None else None,
        operation=str(operation).lower(),
        actor_type=str(actor_type).upper(),
        actor_id=actor_id,
        agent_id=agent_id,
        from_status=from_status,
        to_status=to_status,
        result="APPLIED",
        reason=reason,
        request_id=request_id,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        fencing_token=fencing_token,
        before_snapshot_json=_audit_snapshot(before_snapshot or {}),
        after_snapshot_json=_audit_snapshot(after_snapshot or {}),
    )
    return crud_deployment.create_transition_audit(db, audit)


def _audit_response(
    row: DeploymentTransitionAudit,
) -> DeploymentTransitionAuditResponse:
    return DeploymentTransitionAuditResponse(
        id=row.id,
        release_id=row.release_id,
        target_id=row.target_id,
        operation=row.operation,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        agent_id=row.agent_id,
        from_status=row.from_status,
        to_status=row.to_status,
        result=row.result,
        reason=row.reason,
        request_id=row.request_id,
        correlation_id=row.correlation_id,
        idempotency_key=row.idempotency_key,
        fencing_token=row.fencing_token,
        before_snapshot=_json(row.before_snapshot_json, {}),
        after_snapshot=_json(row.after_snapshot_json, {}),
        created_at=row.created_at,
    )


async def list_transition_audits(
    db: Session,
    release_id: int,
    user: User,
    *,
    target_id: int | None = None,
    actor_type: str | None = None,
    actor_id: int | None = None,
    agent_id: str | None = None,
    operation: str | None = None,
    created_after=None,
    created_before=None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[DeploymentTransitionAuditResponse], int]:
    release = crud_deployment.get_release(db, release_id)
    if release is None:
        raise _error("部署发布不存在")
    _require_project(db, release.project_id, user)
    rows, total = crud_deployment.list_transition_audits(
        db,
        release_id,
        target_id=target_id,
        actor_type=actor_type,
        actor_id=actor_id,
        agent_id=agent_id,
        operation=operation,
        created_after=created_after,
        created_before=created_before,
        offset=offset,
        limit=limit,
    )
    return [_audit_response(row) for row in rows], total


def _sha256(value: str) -> str:
    value = str(value or "").strip().lower()
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise _error(
            "target_bundle_sha256 必须是 64 位十六进制摘要",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    return value


# A release is deliberately advanced by explicit control-plane commands.  The
# old ``approve`` endpoint remains the DRAFT -> APPROVED compatibility entry
# point; these operations own every subsequent rollout phase.
_DEPLOYMENT_TRANSITIONS: dict[str, tuple[set[str], str]] = {
    "start": ({"APPROVED"}, "PRECHECKING"),
    "precheck": ({"PRECHECKING"}, "STAGED"),
    "canary": ({"STAGED"}, "CANARY"),
    "promote": ({"CANARY"}, "OBSERVING"),
    "expand": ({"OBSERVING"}, "EXPANDING"),
    "complete": ({"EXPANDING"}, "COMPLETED"),
}

_ROLLBACK_SOURCES = {
    "APPROVED",
    "PRECHECKING",
    "STAGED",
    "CANARY",
    "OBSERVING",
    "EXPANDING",
    "COMPLETED",
    "FAILED",
}


def _transition_error(
    operation: str, current: str, expected: set[str]
) -> LabelUException:
    allowed = ", ".join(sorted(expected))
    return _error(
        f"部署发布当前状态为 {current}，操作 {operation} 仅允许状态：{allowed}",
        ErrorCode.CODE_30003_CLIENT_ERROR,
        status.HTTP_409_CONFLICT,
    )


def _record_transition(
    db: Session,
    row: DeploymentRelease,
    *,
    operation: str,
    previous: str,
    target: str,
    user: User,
    reason: str | None,
    now: datetime,
    command: DeploymentTransitionCommand | None = None,
) -> None:
    """Append a bounded, provider-neutral transition audit to metrics_json.

    DeploymentRelease predates the state machine and cannot gain a required
    migration field in a compatibility rollout.  Keeping this small history
    under a reserved JSON key preserves existing metrics consumers while
    making retries and operational diagnosis auditable.
    """

    before = _release_snapshot(row)
    before_fencing_token = int(row.fencing_token or 0)
    metrics = _json(row.metrics_json, {})
    if not isinstance(metrics, dict):
        metrics = {}
    history = metrics.get("_state_history", [])
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "operation": operation,
            "from": previous,
            "to": target,
            "actor_id": int(user.id),
            "reason": reason,
            "at": now.isoformat(),
        }
    )
    metrics["_state_history"] = history[-100:]
    metrics["last_transition"] = {
        "operation": operation,
        "from": previous,
        "to": target,
        "at": now.isoformat(),
    }
    row.metrics_json = json.dumps(metrics, ensure_ascii=False)
    row.status = target
    row.fencing_token = int(row.fencing_token or 0) + 1
    row.updated_at = now
    request_id, correlation_id, idempotency_key = _audit_context(command)
    _append_audit(
        db,
        release=row,
        operation=operation,
        from_status=previous,
        to_status=target,
        actor_type="USER",
        actor_id=int(user.id),
        reason=reason,
        request_id=request_id,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
        fencing_token=before_fencing_token,
        before_snapshot=before,
        after_snapshot=_release_snapshot(row),
    )


def _canary_target_ids(row: DeploymentRelease) -> set[str]:
    target_ids = [str(item) for item in _json(row.target_device_ids_json, [])]
    if not target_ids:
        return set()
    count = max(1, int(len(target_ids) * float(row.gray_ratio or 0.05) + 0.999999))
    return set(target_ids[: min(count, len(target_ids))])


def _latest_observation_status(target: DeploymentTarget) -> str | None:
    observations = list(target.observations or [])
    if not observations:
        return None
    latest = max(
        observations,
        key=lambda item: (
            (
                item.sampled_at.timestamp()
                if item.sampled_at is not None
                else float("-inf")
            ),
            item.id or 0,
        ),
    )
    return str(latest.status or "").upper()


async def transition(
    db: Session,
    release_id: int,
    operation: str,
    cmd: DeploymentTransitionCommand,
    user: User,
) -> DeploymentReleaseResponse:
    ensure_legacy_control_plane_retired()
    """Advance one controlled deployment phase with compare-and-set semantics."""

    operation = str(operation or "").strip().lower()
    if operation not in {*_DEPLOYMENT_TRANSITIONS, "rollback"}:
        raise _error(
            "不支持的部署状态操作",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )

    existing = crud_deployment.get_release(db, release_id)
    if existing is None:
        raise _error("部署发布不存在")
    _require_project(db, existing.project_id, user, manage=True)

    with begin_transaction(db):
        # Lock the aggregate for databases that support row locks.  SQLite
        # ignores FOR UPDATE, but the compare-and-set check still protects
        # stale UI requests within its transaction boundary.
        row = (
            db.query(DeploymentRelease)
            .filter(DeploymentRelease.id == int(release_id))
            .with_for_update()
            .first()
        )
        if row is None:  # pragma: no cover - guarded by the read above
            raise _error("部署发布不存在")
        current = str(row.status or "DRAFT").upper()
        if cmd.idempotency_key:
            prior = crud_deployment.get_transition_audit_by_idempotency(
                db, cmd.idempotency_key
            )
            if prior is not None and prior.release_id == row.id:
                return _response(row)
        if cmd.expected_status and str(cmd.expected_status).upper() != current:
            raise _error(
                f"部署发布状态已变化，期望 {str(cmd.expected_status).upper()}，实际为 {current}",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        if cmd.fencing_token is not None and int(cmd.fencing_token) != int(
            row.fencing_token or 0
        ):
            raise _error(
                "部署发布 fencing_token 已过期",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )

        if operation == "rollback":
            if current in {"ROLLBACK", "ROLLED_BACK"}:
                return _response(crud_deployment.get_release(db, row.id) or row)
            if current not in _ROLLBACK_SOURCES:
                raise _transition_error(operation, current, _ROLLBACK_SOURCES)
            now = datetime.now(timezone.utc)
            _record_transition(
                db,
                row,
                operation=operation,
                previous=current,
                target="ROLLBACK",
                user=user,
                reason=cmd.reason,
                now=now,
                command=cmd,
            )
            desired = "ROLLBACK" if row.rollback_model_version_id else "STOP"
            for target in row.targets or []:
                target.desired_state = desired
                target.updated_at = now
            return _response(crud_deployment.get_release(db, row.id) or row)

        allowed, target_status = _DEPLOYMENT_TRANSITIONS[operation]
        # A retry after the operation's target state is reached is a
        # successful no-op; requests arriving in any later phase are 409.
        if current == target_status:
            return _response(crud_deployment.get_release(db, row.id) or row)
        if current not in allowed:
            raise _transition_error(operation, current, allowed)

        targets = list(row.targets or [])
        if not targets:
            raise _error(
                "部署发布没有有效目标，不能推进状态",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        if operation == "precheck":
            invalid = [
                item.device_id for item in targets if not item.target_bundle_sha256
            ]
            if invalid:
                raise _error(
                    "部署目标缺少不可变产物摘要",
                    ErrorCode.CODE_30003_CLIENT_ERROR,
                    status.HTTP_409_CONFLICT,
                )
        elif operation == "promote" and not cmd.force:
            canary_ids = _canary_target_ids(row)
            active = [
                item
                for item in targets
                if item.device_id in canary_ids and item.reported_state == "ACTIVE"
            ]
            if not active:
                raise _error(
                    "灰度目标尚未有边端 ACTIVE 回报，不能提升为观察阶段",
                    ErrorCode.CODE_30003_CLIENT_ERROR,
                    status.HTTP_409_CONFLICT,
                )
        elif operation == "expand" and not cmd.force:
            canary_ids = _canary_target_ids(row)
            canary_targets = [item for item in targets if item.device_id in canary_ids]
            if not canary_targets or any(
                item.reported_state != "ACTIVE" for item in canary_targets
            ):
                raise _error(
                    "灰度目标尚未全部 ACTIVE，不能扩大部署",
                    ErrorCode.CODE_30003_CLIENT_ERROR,
                    status.HTTP_409_CONFLICT,
                )
            if any(
                _latest_observation_status(item) not in {"HEALTHY"}
                for item in canary_targets
            ):
                raise _error(
                    "灰度目标尚未形成 HEALTHY 观测，不能扩大部署",
                    ErrorCode.CODE_30003_CLIENT_ERROR,
                    status.HTTP_409_CONFLICT,
                )
        elif operation == "complete" and not cmd.force:
            if any(item.reported_state != "ACTIVE" for item in targets):
                raise _error(
                    "全部部署目标尚未 ACTIVE，不能完成发布",
                    ErrorCode.CODE_30003_CLIENT_ERROR,
                    status.HTTP_409_CONFLICT,
                )

        now = datetime.now(timezone.utc)
        _record_transition(
            db,
            row,
            operation=operation,
            previous=current,
            target=target_status,
            user=user,
            reason=cmd.reason,
            now=now,
            command=cmd,
        )
        if operation == "start":
            for item in targets:
                item.desired_state = "STAGED"
                item.updated_at = now
        elif operation == "precheck":
            for item in targets:
                item.desired_state = "STAGED"
                item.updated_at = now
        elif operation in {"canary", "promote"}:
            canary_ids = _canary_target_ids(row)
            for item in targets:
                item.desired_state = (
                    "CANARY"
                    if operation == "canary" and item.device_id in canary_ids
                    else (
                        "OBSERVE"
                        if operation == "promote" and item.device_id in canary_ids
                        else item.desired_state
                    )
                )
                item.updated_at = now
        elif operation == "expand":
            for item in targets:
                item.desired_state = "OBSERVE"
                item.updated_at = now
        elif operation == "complete":
            for item in targets:
                item.desired_state = "ACTIVE"
                item.completed_at = item.completed_at or now
                item.updated_at = now

    return _response(crud_deployment.get_release(db, release_id) or row)


async def create(
    db: Session, cmd: CreateDeploymentReleaseCommand, user: User
) -> DeploymentReleaseResponse:
    ensure_legacy_control_plane_retired()
    project = _require_project(db, cmd.project_id, user, manage=True)
    model = crud_model_version.get(db, cmd.model_version_id)
    if model is None or model.project_id != project.id:
        raise _error(
            "模型版本不存在或不属于当前项目",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    if str(model.status).upper() not in {"VALIDATED", "PUBLISHED"}:
        raise _error(
            "模型版本未通过质量门禁，不能创建部署发布",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    latest_gates: dict[str, str] = {gate: "PENDING" for gate in REQUIRED_MODEL_GATES}
    for evidence in sorted(
        model.quality_evidence or [], key=lambda item: (item.gate_type, item.revision)
    ):
        if evidence.gate_type in latest_gates:
            latest_gates[evidence.gate_type] = str(evidence.status).upper()
    if any(latest_gates[gate] != "PASS" for gate in REQUIRED_MODEL_GATES):
        raise _error(
            "模型版本质量证据不完整或未通过，不能创建部署发布",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )
    deployable_types = {"bmodel", "rknn"}
    if not any(
        str(artifact.status).upper() == "READY"
        and str(artifact.artifact_type).lower() in deployable_types
        for artifact in model.artifacts or []
    ):
        raise _error(
            "模型版本缺少 READY 状态的 BModel/RKNN 边端制品，不能创建部署发布",
            ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status.HTTP_409_CONFLICT,
        )
    target_ids = [
        str(item).strip() for item in cmd.target_device_ids if str(item).strip()
    ]
    if not target_ids or len(target_ids) != len(set(target_ids)):
        raise _error(
            "target_device_ids 不能为空且不能重复",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_400_BAD_REQUEST,
        )
    # Registered 7GAI agents carry an explicit project boundary.  Unknown IDs
    # remain accepted for legacy board-agent deployments and are validated by
    # the existing board token/report path.
    known_agents = {
        row.agent_id: row
        for row in db.query(EdgeAgent).filter(EdgeAgent.agent_id.in_(target_ids)).all()
    }
    foreign_agents = [
        device_id
        for device_id, agent in known_agents.items()
        if agent.project_id is not None and int(agent.project_id) != int(project.id)
    ]
    if foreign_agents:
        raise _error(
            "部署目标包含其他项目的边端设备",
            ErrorCode.CODE_30001_NO_PERMISSION,
            status.HTTP_403_FORBIDDEN,
        )
    bundle_sha = _sha256(cmd.target_bundle_sha256)
    rollback_id = cmd.rollback_model_version_id
    if rollback_id:
        rollback = crud_model_version.get(db, rollback_id)
        if rollback is None or rollback.project_id != project.id:
            raise _error(
                "回滚模型版本不存在或不属于当前项目",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_400_BAD_REQUEST,
            )
        if str(rollback.status).upper() not in {"VALIDATED", "PUBLISHED"}:
            raise _error(
                "回滚模型版本未通过质量门禁",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
    now = datetime.now(timezone.utc)
    row = DeploymentRelease(
        project_id=project.id,
        model_version_id=model.id,
        release_key=f"7gai-release-{secrets.token_hex(10)}",
        status="DRAFT",
        target_device_ids_json=json.dumps(target_ids),
        gray_ratio=cmd.gray_ratio,
        observation_seconds=cmd.observation_seconds,
        rollback_model_version_id=rollback_id,
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    with begin_transaction(db):
        crud_deployment.create_release(db, row)
        for device_id in target_ids:
            crud_deployment.create_target(
                db,
                DeploymentTarget(
                    release_id=row.id,
                    device_id=device_id,
                    desired_state="PENDING",
                    reported_state="UNKNOWN",
                    target_bundle_sha256=bundle_sha,
                    created_at=now,
                    updated_at=now,
                ),
            )
    return _response(crud_deployment.get_release(db, row.id) or row)


async def list_by(
    db: Session,
    project_id: int,
    page: int,
    size: int,
    status_filter: str | None,
    user: User,
    search: str | None = None,
):
    _require_project(db, project_id, user)
    rows, total = crud_deployment.list_releases(
        db, project_id, page, size, status_filter, search
    )
    return [_response(row) for row in rows], total


async def get(db: Session, release_id: int, user: User) -> DeploymentReleaseResponse:
    row = crud_deployment.get_release(db, release_id)
    if row is None:
        raise _error("部署发布不存在")
    _require_project(db, row.project_id, user)
    return _response(row)


async def approve(
    db: Session, release_id: int, cmd: ApproveDeploymentReleaseCommand, user: User
) -> DeploymentReleaseResponse:
    ensure_legacy_control_plane_retired()
    row = crud_deployment.get_release(db, release_id)
    if row is None:
        raise _error("部署发布不存在")
    _require_project(db, row.project_id, user, manage=True)
    with begin_transaction(db):
        row = (
            db.query(DeploymentRelease)
            .filter(DeploymentRelease.id == int(release_id))
            .with_for_update()
            .first()
        )
        if row is None:  # pragma: no cover - guarded by the read above
            raise _error("部署发布不存在")
        current = str(row.status or "DRAFT").upper()
        if cmd.idempotency_key:
            prior = crud_deployment.get_transition_audit_by_idempotency(
                db, cmd.idempotency_key
            )
            if prior is not None and prior.release_id == row.id:
                return _response(row)
        if cmd.expected_status and str(cmd.expected_status).upper() != current:
            raise _error(
                f"部署发布状态已变化，期望 {str(cmd.expected_status).upper()}，实际为 {current}",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        if cmd.fencing_token is not None and int(cmd.fencing_token) != int(
            row.fencing_token or 0
        ):
            raise _error(
                "部署发布 fencing_token 已过期",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        if current != "DRAFT":
            raise _error("只有草稿发布可以审批", http_status=status.HTTP_409_CONFLICT)
        now = datetime.now(timezone.utc)
        _record_transition(
            db,
            row,
            operation="approve",
            previous="DRAFT",
            target="APPROVED",
            user=user,
            reason=cmd.reason,
            now=now,
            command=cmd,
        )
        row.approved_by = user.id
        row.approved_at = now
        # Agent dispatcher consumes this desired state; no training job state is changed.
        for target in row.targets or []:
            target.desired_state = "STAGED"
            target.updated_at = now
    return _response(crud_deployment.get_release(db, row.id) or row)


async def report_target(
    db: Session, release_id: int, device_id: str, cmd: ReportDeploymentTargetCommand
) -> DeploymentTargetResponse:
    ensure_legacy_control_plane_retired()
    row = crud_deployment.get_release(db, release_id)
    if row is None:
        raise _error("部署发布不存在")
    target = crud_deployment.get_target(db, release_id, device_id)
    if target is None:
        raise _error("部署目标不存在")
    if cmd.idempotency_key:
        prior = crud_deployment.get_transition_audit_by_idempotency(
            db, cmd.idempotency_key
        )
        if prior is not None and prior.release_id == row.id:
            fresh = crud_deployment.get_target(db, release_id, device_id)
            return _target_response(fresh or target)
    if (
        cmd.expected_status
        and str(cmd.expected_status).upper()
        != str(target.reported_state or "UNKNOWN").upper()
    ):
        raise _error(
            f"目标状态已变化，期望 {str(cmd.expected_status).upper()}，实际为 {target.reported_state}",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if cmd.fencing_token is not None and int(cmd.fencing_token) != int(
        row.fencing_token or 0
    ):
        raise _error(
            "部署发布 fencing_token 已过期",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if row.status not in {
        "APPROVED",
        "PRECHECKING",
        "STAGED",
        "CANARY",
        "OBSERVING",
        "EXPANDING",
        "ROLLBACK",
        "FAILED",
    }:
        raise _error(
            "部署发布尚未审批，不能接受边端状态回报",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if row.status in {"ROLLED_BACK", "COMPLETED"}:
        raise _error(
            "部署发布已结束，不接受目标回报", http_status=status.HTTP_409_CONFLICT
        )
    if row.status == "ROLLBACK" and cmd.reported_state not in {"ROLLED_BACK", "FAILED"}:
        raise _error(
            "部署发布正在回滚，不接受新的激活状态",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if row.status == "FAILED" and cmd.reported_state == "ACTIVE":
        raise _error(
            "部署发布已失败，请先执行回滚后再激活",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    reported_sha = (
        _sha256(cmd.reported_bundle_sha256) if cmd.reported_bundle_sha256 else None
    )
    if cmd.reported_state == "ACTIVE" and reported_sha != target.target_bundle_sha256:
        # Never mark a target active when the downloaded artifact differs from
        # the immutable release bundle. The agent must fetch the expected
        # bundle (or the release will remain failed/rollback requested).
        raise _error(
            "边端产物摘要与发布目标不一致，拒绝激活",
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status.HTTP_409_CONFLICT,
        )
    now = datetime.now(timezone.utc)
    with begin_transaction(db):
        # Re-read under the aggregate lock.  The optimistic checks above make
        # invalid requests fail early; these checks close the race between two
        # Agent callbacks that were both accepted by their initial snapshot.
        locked_row = (
            db.query(DeploymentRelease)
            .filter(DeploymentRelease.id == int(release_id))
            .with_for_update()
            .first()
        )
        locked_target = (
            db.query(DeploymentTarget)
            .filter(
                DeploymentTarget.release_id == int(release_id),
                DeploymentTarget.device_id == str(device_id),
            )
            .with_for_update()
            .first()
        )
        if locked_row is None or locked_target is None:
            raise _error("部署目标不存在")
        if (
            cmd.expected_status
            and str(cmd.expected_status).upper()
            != str(locked_target.reported_state or "UNKNOWN").upper()
        ):
            raise _error(
                f"目标状态已变化，期望 {str(cmd.expected_status).upper()}，实际为 {locked_target.reported_state}",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        if cmd.fencing_token is not None and int(cmd.fencing_token) != int(
            locked_row.fencing_token or 0
        ):
            raise _error(
                "部署发布 fencing_token 已过期",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        row, target = locked_row, locked_target
        if row.status in {"ROLLED_BACK", "COMPLETED"}:
            raise _error(
                "部署发布已结束，不接受目标回报", http_status=status.HTTP_409_CONFLICT
            )
        if row.status == "ROLLBACK" and cmd.reported_state not in {
            "ROLLED_BACK",
            "FAILED",
        }:
            raise _error(
                "部署发布正在回滚，不接受新的激活状态",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        if row.status == "FAILED" and cmd.reported_state == "ACTIVE":
            raise _error(
                "部署发布已失败，请先执行回滚后再激活",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        if (
            cmd.reported_state == "ACTIVE"
            and reported_sha != target.target_bundle_sha256
        ):
            raise _error(
                "边端产物摘要与发布目标不一致，拒绝激活",
                ErrorCode.CODE_30002_VALIDATION_ERROR,
                status.HTTP_409_CONFLICT,
            )
        before_release_status = str(row.status or "DRAFT").upper()
        before_target = _target_snapshot(target)
        before_fencing_token = int(row.fencing_token or 0)
        target.reported_state = cmd.reported_state
        target.reported_bundle_sha256 = reported_sha
        target.agent_version = cmd.agent_version
        target.capabilities_json = json.dumps(
            cmd.capabilities or {}, ensure_ascii=False
        )
        target.metrics_json = json.dumps(cmd.metrics or {}, ensure_ascii=False)
        target.error_message = cmd.error_message
        target.last_heartbeat_at = now
        target.updated_at = now
        if cmd.reported_state == "ACTIVE":
            target.activated_at = target.activated_at or now
            # Legacy releases auto-enter observation when an agent becomes
            # active. Explicit rollout releases keep CANARY until the
            # operator calls promote(), so compare-and-set remains meaningful.
            if row.status == "APPROVED":
                target.desired_state = "OBSERVE"
                row.status = "OBSERVING"
            elif row.status in {"OBSERVING", "EXPANDING"}:
                target.desired_state = "OBSERVE"
        elif cmd.reported_state == "FAILED":
            row.status = "FAILED"
            target.desired_state = (
                "ROLLBACK" if row.rollback_model_version_id else "STOP"
            )
        elif cmd.reported_state == "ROLLED_BACK":
            target.completed_at = target.completed_at or now
            target.desired_state = "ROLLED_BACK"
            if row.status == "ROLLBACK":
                targets = list(row.targets or [])
                if targets and all(
                    item.reported_state == "ROLLED_BACK" for item in targets
                ):
                    row.status = "ROLLED_BACK"
        if str(row.status or "DRAFT").upper() != before_release_status:
            row.fencing_token = int(row.fencing_token or 0) + 1
        request_id, correlation_id, idempotency_key = _audit_context(cmd)
        _append_audit(
            db,
            release=row,
            target=target,
            operation="report_target",
            from_status=before_release_status,
            to_status=str(row.status or "DRAFT").upper(),
            actor_type="AGENT",
            agent_id=str(device_id),
            reason=cmd.error_message,
            request_id=request_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            fencing_token=(
                int(cmd.fencing_token)
                if cmd.fencing_token is not None
                else before_fencing_token
            ),
            before_snapshot={
                "release": {"status": before_release_status},
                "target": before_target,
            },
            after_snapshot={
                "release": _release_snapshot(row),
                "target": _target_snapshot(target),
            },
        )
    fresh = crud_deployment.get_target(db, release_id, device_id)
    return _target_response(fresh or target)


async def observe(
    db: Session,
    release_id: int,
    device_id: str,
    cmd: CreateDeploymentObservationCommand,
) -> DeploymentObservationResponse:
    ensure_legacy_control_plane_retired()
    row = crud_deployment.get_release(db, release_id)
    if row is None:
        raise _error("部署发布不存在")
    target = crud_deployment.get_target(db, release_id, device_id)
    if target is None:
        raise _error("部署目标不存在")
    if target.reported_state not in {"ACTIVE", "STAGED"}:
        raise _error("目标尚未进入可观测状态", http_status=status.HTTP_409_CONFLICT)
    if (
        cmd.expected_status
        and str(cmd.expected_status).upper() != str(row.status or "DRAFT").upper()
    ):
        raise _error(
            f"部署发布状态已变化，期望 {str(cmd.expected_status).upper()}，实际为 {row.status}",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    if cmd.fencing_token is not None and int(cmd.fencing_token) != int(
        row.fencing_token or 0
    ):
        raise _error(
            "部署发布 fencing_token 已过期",
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status.HTTP_409_CONFLICT,
        )
    existing = crud_deployment.get_observation_by_key(
        db, target.id, cmd.observation_key
    )
    if existing is not None:
        return _observation_response(existing)
    now = datetime.now(timezone.utc)
    observation = DeploymentObservation(
        target_id=target.id,
        observation_key=cmd.observation_key,
        status=cmd.status,
        fps=cmd.fps,
        latency_p95_ms=cmd.latency_p95_ms,
        latency_p99_ms=cmd.latency_p99_ms,
        cpu_percent=cmd.cpu_percent,
        npu_percent=cmd.npu_percent,
        memory_percent=cmd.memory_percent,
        temperature_c=cmd.temperature_c,
        metrics_json=json.dumps(cmd.metrics or {}, ensure_ascii=False),
        error_message=cmd.error_message,
        sampled_at=now,
        created_at=now,
    )
    with begin_transaction(db):
        locked_row = (
            db.query(DeploymentRelease)
            .filter(DeploymentRelease.id == int(release_id))
            .with_for_update()
            .first()
        )
        locked_target = (
            db.query(DeploymentTarget)
            .filter(
                DeploymentTarget.release_id == int(release_id),
                DeploymentTarget.device_id == str(device_id),
            )
            .with_for_update()
            .first()
        )
        if locked_row is None or locked_target is None:
            raise _error("部署目标不存在")
        if locked_target.reported_state not in {"ACTIVE", "STAGED"}:
            raise _error("目标尚未进入可观测状态", http_status=status.HTTP_409_CONFLICT)
        if (
            cmd.expected_status
            and str(cmd.expected_status).upper()
            != str(locked_row.status or "DRAFT").upper()
        ):
            raise _error(
                f"部署发布状态已变化，期望 {str(cmd.expected_status).upper()}，实际为 {locked_row.status}",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        if cmd.fencing_token is not None and int(cmd.fencing_token) != int(
            locked_row.fencing_token or 0
        ):
            raise _error(
                "部署发布 fencing_token 已过期",
                ErrorCode.CODE_30003_CLIENT_ERROR,
                status.HTTP_409_CONFLICT,
            )
        # A duplicate observation can race with another Agent request.  The
        # unique key is checked again while the target row is locked.
        existing = crud_deployment.get_observation_by_key(
            db, locked_target.id, cmd.observation_key
        )
        if existing is not None:
            return _observation_response(existing)
        row, target = locked_row, locked_target
        observation.target_id = target.id
        before_release_status = str(row.status or "DRAFT").upper()
        before_target = _target_snapshot(target)
        before_fencing_token = int(row.fencing_token or 0)
        crud_deployment.create_observation(db, observation)
        target.metrics_json = json.dumps(cmd.metrics or {}, ensure_ascii=False)
        target.updated_at = now
        if cmd.status != "HEALTHY":
            target.desired_state = (
                "ROLLBACK" if row.rollback_model_version_id else "STOP"
            )
            row.status = "FAILED"
        if str(row.status or "DRAFT").upper() != before_release_status:
            row.fencing_token = int(row.fencing_token or 0) + 1
        request_id, correlation_id, idempotency_key = _audit_context(cmd)
        _append_audit(
            db,
            release=row,
            target=target,
            operation="observe",
            from_status=before_release_status,
            to_status=str(row.status or "DRAFT").upper(),
            actor_type="AGENT",
            agent_id=str(device_id),
            reason=cmd.error_message,
            request_id=request_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key
            or f"observation:{target.id}:{cmd.observation_key}",
            fencing_token=(
                int(cmd.fencing_token)
                if cmd.fencing_token is not None
                else before_fencing_token
            ),
            before_snapshot={
                "release": {"status": before_release_status},
                "target": before_target,
            },
            after_snapshot={
                "release": _release_snapshot(row),
                "target": _target_snapshot(target),
            },
        )
    return _observation_response(observation)
