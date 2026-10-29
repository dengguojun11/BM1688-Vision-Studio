"""Versioned edge Agent protocol and resumable OTA state machine."""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import status
from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.application.command.edge_agent import (
    ActivateEdgeAgentOtaCommand,
    CreateEdgeAgentReleaseCommand,
    HeartbeatEdgeAgentCommand,
    RegisterEdgeAgentCommand,
    ReportEdgeAgentCapabilitiesCommand,
    RollbackEdgeAgentOtaCommand,
    VerifyEdgeAgentOtaCommand,
)
from labelu.internal.application.response.edge_agent import (
    EdgeAgentDeploymentResponse,
    EdgeAgentDesiredStateResponse,
    EdgeAgentManifestResponse,
    EdgeAgentRegistrationResponse,
    EdgeAgentReleaseResponse,
    EdgeAgentResponse,
)
from labelu.internal.common.config import settings
from labelu.internal.common import edge_agent_control_plane
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.phase_one_contract import ensure_legacy_control_plane_retired
from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.edge_agent import EdgeAgent, EdgeAgentDeployment, EdgeAgentRelease
from labelu.internal.domain.models.user import User
from labelu.internal.adapter.persistence import crud_project
from labelu.internal.common.task_access import is_task_superuser


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _error(message: str, http_status: int = status.HTTP_400_BAD_REQUEST):
    return LabelUException(code=ErrorCode.CODE_30003_CLIENT_ERROR, status_code=http_status, message=message)


def _json(raw: Any) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    if not raw:
        return {}
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _effective_status(row: EdgeAgent) -> str:
    """Derive a read-only liveness status from the last heartbeat.

    The persisted lifecycle status remains untouched so a delayed dashboard
    read cannot race an Agent heartbeat or overwrite the control-plane state.
    """
    status_value = str(row.status or "UNKNOWN").upper()
    if status_value != "ONLINE":
        return status_value
    heartbeat = row.last_heartbeat_at
    if heartbeat is None:
        return "OFFLINE"
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    ttl = max(1, int(getattr(settings, "SEVEN_GAI_AGENT_OFFLINE_TTL_SECONDS", 120) or 120))
    current = _now()
    age_seconds = (current.astimezone(timezone.utc) - heartbeat).total_seconds()
    if not 0 <= age_seconds <= ttl:
        return "OFFLINE"
    return status_value


def _agent_response(row: EdgeAgent) -> EdgeAgentResponse:
    return EdgeAgentResponse(
        agent_id=row.agent_id, name=row.name, project_id=row.project_id, status=_effective_status(row),
        agent_version=row.agent_version, platform=row.platform,
        capabilities=_json(row.capabilities_json), desired_state=_json(row.desired_state_json),
        reported_state=_json(row.reported_state_json), last_heartbeat_at=row.last_heartbeat_at,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _release_response(row: EdgeAgentRelease) -> EdgeAgentReleaseResponse:
    return EdgeAgentReleaseResponse(
        id=row.id, release_key=row.release_key, version=row.version, project_id=row.project_id,
        artifact_sha256=row.artifact_sha256, artifact_size=row.artifact_size,
        chunk_size=row.chunk_size, chunk_count=math.ceil(row.artifact_size / row.chunk_size),
        signature=row.signature, status=row.status,
    )


def _deployment_response(row: EdgeAgentDeployment) -> EdgeAgentDeploymentResponse:
    return EdgeAgentDeploymentResponse(
        id=row.id, release_id=row.release_id, agent_id=row.agent_id, state=row.state,
        downloaded_bytes=row.downloaded_bytes, verified_sha256=row.verified_sha256,
        active_version=row.active_version, previous_version=row.previous_version,
        failure_reason=row.failure_reason, attempts=row.attempts,
        last_chunk_index=row.last_chunk_index, updated_at=row.updated_at,
    )


def _enrollment_token() -> str:
    return edge_agent_control_plane.enrollment_token()


def _ota_signature_required() -> bool:
    return bool(getattr(settings, "SEVEN_GAI_AGENT_REQUIRE_OTA_SIGNATURE", False) or getattr(settings, "SEVEN_GAI_AGENT_OTA_SIGNING_KEY", ""))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def register(db: Session, cmd: RegisterEdgeAgentCommand, *, client_ip: str | None = None) -> EdgeAgentRegistrationResponse:
    ensure_legacy_control_plane_retired()
    expected = _enrollment_token()
    if not expected or not secrets.compare_digest(cmd.enrollment_token, expected):
        raise _error("Agent 注册凭据无效", status.HTTP_401_UNAUTHORIZED)
    if cmd.project_id is not None and crud_project.get(db, cmd.project_id) is None:
        raise _error("绑定项目不存在", status.HTTP_400_BAD_REQUEST)
    now = _now()
    row = db.get(EdgeAgent, cmd.agent_id)
    if cmd.project_id is None and getattr(settings, "SEVEN_GAI_AGENT_REQUIRE_PROJECT", False) and (row is None or row.project_id is None):
        raise _error("Agent 注册必须绑定项目", status.HTTP_400_BAD_REQUEST)
    token = secrets.token_urlsafe(32)
    if row is None:
        row = EdgeAgent(agent_id=cmd.agent_id, name=cmd.name, project_id=cmd.project_id, token_hash=_hash_token(token), status="REGISTERED", created_at=now, updated_at=now)
        db.add(row)
    else:
        if row.status == "REVOKED":
            raise _error("Agent 已吊销，不能重新注册", status.HTTP_409_CONFLICT)
        if row.project_id is not None and cmd.project_id not in (None, row.project_id):
            raise _error("Agent 已绑定其他项目，不能跨项目重绑", status.HTTP_409_CONFLICT)
        row.name = cmd.name
        # A legacy registration may omit project_id; do not erase an existing
        # project binding when it refreshes its token.
        if cmd.project_id is not None:
            row.project_id = cmd.project_id
        row.token_hash = _hash_token(token)
        row.status = "REGISTERED"
        row.agent_version = cmd.agent_version
        row.platform = cmd.platform
        row.capabilities_json = json.dumps(cmd.capabilities, ensure_ascii=False)
        row.updated_at = now
    row.agent_version = cmd.agent_version or row.agent_version
    row.platform = cmd.platform or row.platform
    row.capabilities_json = json.dumps(cmd.capabilities, ensure_ascii=False)
    row.last_ip = client_ip
    db.flush()
    return EdgeAgentRegistrationResponse(agent=_agent_response(row), agent_token=token)


def authenticate(db: Session, agent_id: str, token: str) -> EdgeAgent:
    row = db.get(EdgeAgent, str(agent_id))
    if row is None or row.status == "REVOKED" or not token or not secrets.compare_digest(_hash_token(token), row.token_hash):
        raise _error("Agent Token 无效", status.HTTP_401_UNAUTHORIZED)
    return row


def heartbeat(db: Session, row: EdgeAgent, cmd: HeartbeatEdgeAgentCommand, *, client_ip: str | None = None) -> EdgeAgentResponse:
    ensure_legacy_control_plane_retired()
    now = _now()
    row.status = "ONLINE"
    row.agent_version = cmd.agent_version or row.agent_version
    row.platform = cmd.platform or row.platform
    row.last_ip = client_ip or row.last_ip
    row.last_heartbeat_at = now
    row.reported_state_json = json.dumps({"metrics": cmd.metrics, **cmd.reported_state}, ensure_ascii=False)
    row.updated_at = now
    db.flush()
    return _agent_response(row)


def capabilities(db: Session, row: EdgeAgent, cmd: ReportEdgeAgentCapabilitiesCommand) -> EdgeAgentResponse:
    ensure_legacy_control_plane_retired()
    row.capabilities_json = json.dumps(cmd.capabilities, ensure_ascii=False)
    row.updated_at = _now()
    db.flush()
    return _agent_response(row)


def desired_state(row: EdgeAgent) -> EdgeAgentDesiredStateResponse:
    return EdgeAgentDesiredStateResponse(agent_id=row.agent_id, desired_state=_json(row.desired_state_json), server_time=_now())


def create_release(db: Session, cmd: CreateEdgeAgentReleaseCommand, user: User | None = None) -> EdgeAgentReleaseResponse:
    ensure_legacy_control_plane_retired()
    if cmd.project_id is not None and crud_project.get(db, cmd.project_id) is None:
        raise _error("绑定项目不存在", status.HTTP_400_BAD_REQUEST)
    if cmd.project_id is None and getattr(settings, "SEVEN_GAI_AGENT_REQUIRE_PROJECT", False):
        raise _error("OTA Release 必须绑定项目", status.HTTP_400_BAD_REQUEST)
    path = Path(cmd.artifact_path).expanduser().resolve()
    if not path.is_file():
        raise _error("OTA 产物文件不存在")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != cmd.artifact_sha256.lower():
        raise _error("OTA 产物摘要不匹配", status.HTTP_409_CONFLICT)
    signing_key = str(getattr(settings, "SEVEN_GAI_AGENT_OTA_SIGNING_KEY", "") or "")
    if _ota_signature_required():
        if not signing_key:
            raise _error("OTA 签名硬门禁已启用但未配置签名密钥", status.HTTP_500_INTERNAL_SERVER_ERROR)
        expected_signature = hmac.new(signing_key.encode("utf-8"), digest.encode("ascii"), hashlib.sha256).hexdigest()
        if not cmd.signature or not hmac.compare_digest(str(cmd.signature), expected_signature):
            raise _error("OTA Release 签名无效或缺失", status.HTTP_409_CONFLICT)
    agents = []
    for agent_id in cmd.agent_ids:
        agent = db.get(EdgeAgent, agent_id)
        if agent is None or agent.status == "REVOKED":
            raise _error(f"Agent 不存在或已吊销: {agent_id}", status.HTTP_400_BAD_REQUEST)
        agents.append(agent)
    release_project_id = cmd.project_id
    if release_project_id is None:
        project_ids = {agent.project_id for agent in agents}
        if len(project_ids - {None}) > 1 or (None in project_ids and len(project_ids) > 1):
            raise _error("OTA Release 不能跨项目绑定 Agent", status.HTTP_400_BAD_REQUEST)
        release_project_id = next(iter(project_ids), None)
    if release_project_id is not None and any(agent.project_id != release_project_id for agent in agents):
        raise _error("OTA Release 与 Agent 项目不一致", status.HTTP_400_BAD_REQUEST)
    now = _now()
    row = EdgeAgentRelease(
        release_key=f"7gai-ota-{secrets.token_hex(10)}", project_id=release_project_id, version=cmd.version,
        artifact_path=str(path), artifact_sha256=digest, artifact_size=path.stat().st_size,
        chunk_size=cmd.chunk_size, signature=cmd.signature, status="PUBLISHED",
        created_by=getattr(user, "id", None), created_at=now,
    )
    db.add(row); db.flush()
    for agent in agents:
        agent_id = agent.agent_id
        agent.desired_state_json = json.dumps({"ota_release_id": row.id, "version": row.version}, ensure_ascii=False)
        db.add(EdgeAgentDeployment(release_id=row.id, agent_id=agent_id, state="PENDING", created_at=now, updated_at=now))
    db.flush()
    return _release_response(row)


def manifest(db: Session, row: EdgeAgent, release_id: int, base_url: str) -> EdgeAgentManifestResponse:
    ensure_legacy_control_plane_retired()
    release = db.get(EdgeAgentRelease, int(release_id))
    if release is None or release.status != "PUBLISHED":
        raise _error("OTA 发布不存在", status.HTTP_404_NOT_FOUND)
    _check_release_scope(row, release)
    dep = db.query(EdgeAgentDeployment).filter_by(release_id=release.id, agent_id=row.agent_id).first()
    if dep is None:
        dep = EdgeAgentDeployment(release_id=release.id, agent_id=row.agent_id, state="PENDING", created_at=_now(), updated_at=_now())
        db.add(dep); db.flush()
    return EdgeAgentManifestResponse(
        release=_release_response(release), deployment=_deployment_response(dep),
        download_url=f"{base_url}/edge-agents/{row.agent_id}/ota/{release.id}/chunks/{{index}}",
        verify_url=f"{base_url}/edge-agents/{row.agent_id}/ota/{release.id}/verify",
        activate_url=f"{base_url}/edge-agents/{row.agent_id}/ota/{release.id}/activate",
        rollback_url=f"{base_url}/edge-agents/{row.agent_id}/ota/{release.id}/rollback",
    )


def chunk(db: Session, row: EdgeAgent, release_id: int, index: int) -> tuple[bytes, str, int]:
    ensure_legacy_control_plane_retired()
    release = db.get(EdgeAgentRelease, int(release_id))
    dep = db.query(EdgeAgentDeployment).filter_by(release_id=release_id, agent_id=row.agent_id).first()
    if release is None or dep is None:
        raise _error("OTA 目标不存在", status.HTTP_404_NOT_FOUND)
    _check_release_scope(row, release)
    if release.status != "PUBLISHED":
        raise _error("OTA 发布当前不可下载", status.HTTP_409_CONFLICT)
    if index < 0 or index >= math.ceil(release.artifact_size / release.chunk_size):
        raise _error("OTA 分片序号越界", status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)
    path = Path(release.artifact_path)
    if not path.is_file():
        raise _error("OTA 产物已丢失", status.HTTP_410_GONE)
    with path.open("rb") as fh:
        fh.seek(index * release.chunk_size)
        payload = fh.read(release.chunk_size)
    dep.state = "DOWNLOADING"
    dep.last_chunk_index = index
    dep.downloaded_bytes = max(dep.downloaded_bytes, index * release.chunk_size + len(payload))
    dep.updated_at = _now()
    db.flush()
    return payload, release.version, release.chunk_size


def verify(db: Session, row: EdgeAgent, release_id: int, cmd: VerifyEdgeAgentOtaCommand) -> EdgeAgentDeploymentResponse:
    ensure_legacy_control_plane_retired()
    dep = db.query(EdgeAgentDeployment).filter_by(release_id=release_id, agent_id=row.agent_id).first()
    release = db.get(EdgeAgentRelease, int(release_id))
    if dep is None or release is None:
        raise _error("OTA 目标不存在", status.HTTP_404_NOT_FOUND)
    _check_release_scope(row, release)
    signing_key = str(getattr(settings, "SEVEN_GAI_AGENT_OTA_SIGNING_KEY", "") or "")
    signature_ok = not _ota_signature_required()
    if _ota_signature_required() and signing_key:
        expected_signature = hmac.new(signing_key.encode("utf-8"), release.artifact_sha256.encode("ascii"), hashlib.sha256).hexdigest()
        signature_ok = bool(release.signature) and hmac.compare_digest(str(release.signature), expected_signature)
    failed = (
        cmd.downloaded_sha256.lower() != release.artifact_sha256
        or cmd.downloaded_bytes != release.artifact_size
        or not signature_ok
    )
    with begin_transaction(db):
        dep.attempts += 1
        if failed:
            dep.state = "FAILED"
            dep.failure_reason = "OTA 摘要、大小或签名校验失败"
        else:
            dep.state = "VERIFIED"
            dep.verified_sha256 = release.artifact_sha256
            dep.downloaded_bytes = cmd.downloaded_bytes
            dep.failure_reason = None
        dep.updated_at = _now()
        db.flush()
    if failed:
        raise _error("OTA 摘要、大小或签名校验失败", status.HTTP_409_CONFLICT)
    return _deployment_response(dep)


def activate(db: Session, row: EdgeAgent, release_id: int, cmd: ActivateEdgeAgentOtaCommand) -> EdgeAgentDeploymentResponse:
    ensure_legacy_control_plane_retired()
    dep = db.query(EdgeAgentDeployment).filter_by(release_id=release_id, agent_id=row.agent_id).first()
    release = db.get(EdgeAgentRelease, int(release_id))
    if dep is None or release is None:
        raise _error("OTA 目标不存在", status.HTTP_404_NOT_FOUND)
    _check_release_scope(row, release)
    if dep.state != "VERIFIED" or cmd.active_version != release.version:
        dep.state = "ROLLBACK_PENDING" if dep.state in {"FAILED", "VERIFIED"} else dep.state
        dep.failure_reason = dep.failure_reason or "OTA 激活前置条件不满足"
        dep.updated_at = _now(); db.flush()
        raise _error("OTA 未完成校验，不能激活", status.HTTP_409_CONFLICT)
    dep.previous_version = dep.active_version
    dep.active_version = release.version
    dep.state = "ACTIVE"
    dep.updated_at = _now(); db.flush()
    return _deployment_response(dep)


def rollback(db: Session, row: EdgeAgent, release_id: int, cmd: RollbackEdgeAgentOtaCommand) -> EdgeAgentDeploymentResponse:
    ensure_legacy_control_plane_retired()
    dep = db.query(EdgeAgentDeployment).filter_by(release_id=release_id, agent_id=row.agent_id).first()
    if dep is None:
        raise _error("OTA 目标不存在", status.HTTP_404_NOT_FOUND)
    release = db.get(EdgeAgentRelease, int(release_id))
    if release is None:
        raise _error("OTA 发布不存在", status.HTTP_404_NOT_FOUND)
    _check_release_scope(row, release)
    if dep.state not in {"ACTIVE", "FAILED", "ROLLBACK_PENDING"}:
        raise _error("当前 OTA 状态不能回滚", status.HTTP_409_CONFLICT)
    dep.state = "ROLLED_BACK"
    dep.failure_reason = cmd.reason
    if dep.previous_version:
        dep.active_version = dep.previous_version
    dep.updated_at = _now(); db.flush()
    return _deployment_response(dep)


def _project_can_read(db: Session, project_id: int, user: User) -> bool:
    project = crud_project.get(db, project_id)
    if project is None:
        return False
    if is_task_superuser(user) or project.owner_id == user.id:
        return True
    member = crud_project.get_member(db, project_id, user.id)
    return bool(member and member.status == "ACTIVE")


def _agent_query(
    db: Session, project_id: int | None = None, *, user: User | None = None
):
    query = db.query(EdgeAgent)
    if project_id is not None:
        if user is not None and not _project_can_read(db, project_id, user):
            raise _error("无权访问该项目", status.HTTP_403_FORBIDDEN)
        query = query.filter(EdgeAgent.project_id == project_id)
    elif user is not None and not is_task_superuser(user):
        projects, _ = crud_project.list_accessible(
            db, user.id, is_superuser=False, page=0, size=5000
        )
        owned = [row.id for row in projects]
        if not owned:
            return []
        query = query.filter(EdgeAgent.project_id.in_(owned))
    else:
        # Unscoped historical agents are an ownership-review concern, not a
        # normal control-plane resource.  The read-only ownership audit is the
        # only endpoint that intentionally exposes their IDs.
        query = query.filter(EdgeAgent.project_id.is_not(None))
    return query


def list_agents_page(
    db: Session,
    project_id: int | None = None,
    *,
    page: int = 0,
    size: int = 100,
    search: str | None = None,
    user: User | None = None,
) -> tuple[list[EdgeAgentResponse], int]:
    query = _agent_query(db, project_id, user=user)
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                EdgeAgent.agent_id.ilike(pattern, escape=LIKE_ESCAPE),
                EdgeAgent.name.ilike(pattern, escape=LIKE_ESCAPE),
                EdgeAgent.status.ilike(pattern, escape=LIKE_ESCAPE),
                EdgeAgent.agent_version.ilike(pattern, escape=LIKE_ESCAPE),
                EdgeAgent.platform.ilike(pattern, escape=LIKE_ESCAPE),
                cast(EdgeAgent.project_id, String).ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = query.count()
    rows = (
        query.order_by(EdgeAgent.created_at.desc(), EdgeAgent.agent_id.asc())
        .offset(int(page) * int(size))
        .limit(int(size))
        .all()
    )
    return [_agent_response(row) for row in rows], total


def list_agents(
    db: Session, project_id: int | None = None, *, user: User | None = None
) -> list[EdgeAgentResponse]:
    rows, _ = list_agents_page(db, project_id, page=0, size=5000, user=user)
    return rows


def list_releases(
    db: Session, project_id: int | None = None, *, user: User | None = None
) -> list[EdgeAgentReleaseResponse]:
    query = db.query(EdgeAgentRelease)
    if project_id is not None:
        if user is not None and not _project_can_read(db, project_id, user):
            raise _error("无权访问该项目", status.HTTP_403_FORBIDDEN)
        query = query.filter(EdgeAgentRelease.project_id == project_id)
    elif user is not None and not is_task_superuser(user):
        projects, _ = crud_project.list_accessible(
            db, user.id, is_superuser=False, page=0, size=5000
        )
        owned = [row.id for row in projects]
        if not owned:
            return []
        query = query.filter(EdgeAgentRelease.project_id.in_(owned))
    else:
        # Keep legacy OTA releases out of operational lists until an operator
        # explicitly resolves their project boundary.
        query = query.filter(EdgeAgentRelease.project_id.is_not(None))
    return [_release_response(row) for row in query.order_by(EdgeAgentRelease.created_at.desc()).all()]


def _check_release_scope(agent: EdgeAgent, release: EdgeAgentRelease) -> None:
    if release.project_id is not None and agent.project_id != release.project_id:
        raise _error("Agent 无权访问其他项目的 OTA Release", status.HTTP_403_FORBIDDEN)
    if release.project_id is None and agent.project_id is not None:
        raise _error("OTA Release 未绑定 Agent 所属项目", status.HTTP_403_FORBIDDEN)
