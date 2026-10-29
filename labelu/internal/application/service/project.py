from __future__ import annotations

from typing import Tuple

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_project, crud_user
from labelu.internal.application.command.project import (
    AddProjectMemberCommand,
    CreateProjectCommand,
    UpdateProjectCommand,
)
from labelu.internal.application.response.project import (
    ProjectMemberResponse,
    ProjectResponse,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.phase_one_contract import ensure_legacy_control_plane_retired
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.project import Project, ProjectMember
from labelu.internal.domain.models.organization import Organization
from labelu.internal.domain.models.user import User


def _project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        organization_id=project.organization_id,
        owner_id=project.owner_id,
        name=project.name,
        description=project.description,
        status=project.status,
        created_by=project.created_by,
        updated_by=project.updated_by,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _member_response(member: ProjectMember) -> ProjectMemberResponse:
    return ProjectMemberResponse(
        project_id=member.project_id,
        user_id=member.user_id,
        role=member.role,
        status=member.status,
        invited_by=member.invited_by,
        created_at=member.created_at,
        updated_at=member.updated_at,
    )


def _not_found() -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_404_NOT_FOUND,
        message="项目不存在",
    )


def _forbidden() -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30001_NO_PERMISSION,
        status_code=status.HTTP_403_FORBIDDEN,
        message="无权访问该项目",
    )


def _normalise_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="项目名称不能为空",
        )
    return name


def _get_project_or_raise(db: Session, project_id: int) -> Project:
    project = crud_project.get(db, project_id)
    if project is None:
        raise _not_found()
    return project


def _can_access(db: Session, project: Project, user: User) -> bool:
    if is_task_superuser(user) or project.owner_id == user.id:
        return True
    member = crud_project.get_member(db, project.id, user.id)
    return bool(member and member.status == "ACTIVE")


def _can_manage(db: Session, project: Project, user: User) -> bool:
    if is_task_superuser(user) or project.owner_id == user.id:
        return True
    member = crud_project.get_member(db, project.id, user.id)
    return bool(member and member.status == "ACTIVE" and member.role == "admin")


async def create(
    db: Session, cmd: CreateProjectCommand, current_user: User
) -> ProjectResponse:
    ensure_legacy_control_plane_retired()
    project = Project(
        owner_id=current_user.id,
        organization_id=cmd.organization_id,
        name=_normalise_name(cmd.name),
        description=cmd.description,
        status="DRAFT",
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    if cmd.organization_id is not None:
        organization = db.query(Organization).filter(
            Organization.id == cmd.organization_id, Organization.deleted_at.is_(None)
        ).one_or_none()
        if organization is None:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=status.HTTP_404_NOT_FOUND,
                message="组织不存在",
            )
        if not (is_task_superuser(current_user) or organization.owner_id == current_user.id):
            raise _forbidden()
    with begin_transaction(db):
        crud_project.create(db, project)
        crud_project.add_member(
            db,
            ProjectMember(
                project_id=project.id,
                user_id=current_user.id,
                role="owner",
                status="ACTIVE",
                invited_by=current_user.id,
            ),
        )
    return _project_response(project)


async def list_by(
    db: Session,
    current_user: User,
    page: int,
    size: int,
    search: str | None = None,
) -> Tuple[list[ProjectResponse], int]:
    rows, total = crud_project.list_accessible(
        db,
        current_user.id,
        is_superuser=is_task_superuser(current_user),
        page=page,
        size=size,
        search=search,
    )
    return [_project_response(row) for row in rows], total


async def get(db: Session, project_id: int, current_user: User) -> ProjectResponse:
    project = _get_project_or_raise(db, project_id)
    if not _can_access(db, project, current_user):
        raise _forbidden()
    return _project_response(project)


async def update(
    db: Session,
    project_id: int,
    cmd: UpdateProjectCommand,
    current_user: User,
) -> ProjectResponse:
    ensure_legacy_control_plane_retired()
    project = _get_project_or_raise(db, project_id)
    if not _can_manage(db, project, current_user):
        raise _forbidden()
    values = cmd.model_dump(exclude_unset=True)
    if "name" in values:
        values["name"] = _normalise_name(values["name"])
    values["updated_by"] = current_user.id
    with begin_transaction(db):
        for key, value in values.items():
            setattr(project, key, value)
        db.flush()
        db.refresh(project)
    return _project_response(project)


async def list_members(
    db: Session, project_id: int, current_user: User
) -> list[ProjectMemberResponse]:
    project = _get_project_or_raise(db, project_id)
    if not _can_access(db, project, current_user):
        raise _forbidden()
    return [_member_response(row) for row in crud_project.list_members(db, project.id)]


async def add_member(
    db: Session,
    project_id: int,
    cmd: AddProjectMemberCommand,
    current_user: User,
) -> ProjectMemberResponse:
    ensure_legacy_control_plane_retired()
    project = _get_project_or_raise(db, project_id)
    if not _can_manage(db, project, current_user):
        raise _forbidden()
    if cmd.role == "owner":
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="项目只能有一个所有者",
        )
    if crud_user.get(db, cmd.user_id) is None:
        raise LabelUException(
            code=ErrorCode.CODE_40002_USER_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    existing = crud_project.get_member(db, project.id, cmd.user_id)
    if existing is not None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="用户已经是项目成员",
        )
    with begin_transaction(db):
        member = crud_project.add_member(
            db,
            ProjectMember(
                project_id=project.id,
                user_id=cmd.user_id,
                role=cmd.role,
                status="ACTIVE",
                invited_by=current_user.id,
            ),
        )
    return _member_response(member)
