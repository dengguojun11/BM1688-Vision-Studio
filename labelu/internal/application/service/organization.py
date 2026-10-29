from __future__ import annotations

import json

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.application.command.organization import CreateOrganizationCommand, CreateSceneCommand
from labelu.internal.application.response.organization import OrganizationResponse, SceneResponse
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.phase_one_contract import ensure_legacy_control_plane_retired
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.organization import Organization, Scene
from labelu.internal.domain.models.project import Project
from labelu.internal.domain.models.user import User


def _error(message: str, http_status: int) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=http_status,
        message=message,
    )


def _organization_response(row: Organization) -> OrganizationResponse:
    return OrganizationResponse(
        id=row.id, owner_id=row.owner_id, name=row.name, description=row.description,
        status=row.status, created_by=row.created_by, updated_by=row.updated_by,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _scene_response(row: Scene) -> SceneResponse:
    try:
        metadata = json.loads(row.metadata_json) if row.metadata_json else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        metadata = {}
    return SceneResponse(
        id=row.id, project_id=row.project_id, scene_key=row.scene_key, name=row.name,
        description=row.description, status=row.status, metadata=metadata,
        created_by=row.created_by, updated_by=row.updated_by,
        created_at=row.created_at, updated_at=row.updated_at,
    )


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise _error(f"{label}不能为空", status.HTTP_400_BAD_REQUEST)
    return cleaned


def _can_access_project(db: Session, project: Project, user: User) -> bool:
    if is_task_superuser(user) or project.owner_id == user.id:
        return True
    from labelu.internal.adapter.persistence import crud_project

    member = crud_project.get_member(db, project.id, user.id)
    return bool(member and member.status == "ACTIVE")


def _get_project(db: Session, project_id: int, user: User) -> Project:
    project = db.query(Project).filter(Project.id == project_id, Project.deleted_at.is_(None)).one_or_none()
    if project is None:
        raise _error("项目不存在", status.HTTP_404_NOT_FOUND)
    if not _can_access_project(db, project, user):
        raise _error("无权访问该项目", status.HTTP_403_FORBIDDEN)
    return project


def create_organization(db: Session, cmd: CreateOrganizationCommand, user: User) -> OrganizationResponse:
    ensure_legacy_control_plane_retired()
    name = _clean(cmd.name, "组织名称")
    existing = db.query(Organization).filter(
        Organization.owner_id == user.id, Organization.name == name, Organization.deleted_at.is_(None)
    ).one_or_none()
    if existing is not None:
        raise _error("组织名称已存在", status.HTTP_409_CONFLICT)
    row = Organization(
        owner_id=user.id, name=name, description=cmd.description, status="ACTIVE",
        created_by=user.id, updated_by=user.id,
    )
    with begin_transaction(db):
        db.add(row)
        db.flush()
    return _organization_response(row)


def list_organizations(db: Session, user: User, page: int, size: int) -> tuple[list[OrganizationResponse], int]:
    query = db.query(Organization).filter(Organization.deleted_at.is_(None))
    if not is_task_superuser(user):
        query = query.filter(Organization.owner_id == user.id)
    total = query.count()
    rows = query.order_by(Organization.updated_at.desc(), Organization.id.desc()).offset(page * size).limit(size).all()
    return [_organization_response(row) for row in rows], total


def create_scene(db: Session, project_id: int, cmd: CreateSceneCommand, user: User) -> SceneResponse:
    ensure_legacy_control_plane_retired()
    project = _get_project(db, project_id, user)
    scene_key = _clean(cmd.scene_key, "场景标识")
    name = _clean(cmd.name, "场景名称")
    existing = db.query(Scene).filter(
        Scene.project_id == project.id, Scene.scene_key == scene_key, Scene.deleted_at.is_(None)
    ).one_or_none()
    if existing is not None:
        raise _error("场景标识已存在", status.HTTP_409_CONFLICT)
    row = Scene(
        project_id=project.id, scene_key=scene_key, name=name, description=cmd.description,
        metadata_json=json.dumps(cmd.metadata or {}, ensure_ascii=False, sort_keys=True),
        status="ACTIVE", created_by=user.id, updated_by=user.id,
    )
    with begin_transaction(db):
        db.add(row)
        db.flush()
    return _scene_response(row)


def list_scenes(db: Session, project_id: int, user: User, page: int, size: int) -> tuple[list[SceneResponse], int]:
    project = _get_project(db, project_id, user)
    query = db.query(Scene).filter(Scene.project_id == project.id, Scene.deleted_at.is_(None))
    total = query.count()
    rows = query.order_by(Scene.updated_at.desc(), Scene.id.desc()).offset(page * size).limit(size).all()
    return [_scene_response(row) for row in rows], total


def get_scene(db: Session, project_id: int, scene_id: int, user: User) -> SceneResponse:
    project = _get_project(db, project_id, user)
    row = db.query(Scene).filter(Scene.id == scene_id, Scene.project_id == project.id, Scene.deleted_at.is_(None)).one_or_none()
    if row is None:
        raise _error("场景不存在", status.HTTP_404_NOT_FOUND)
    return _scene_response(row)
