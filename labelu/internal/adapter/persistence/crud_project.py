from __future__ import annotations

from typing import Tuple

from sqlalchemy import String, and_, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.project import Project, ProjectMember


def create(db: Session, project: Project) -> Project:
    db.add(project)
    db.flush()
    db.refresh(project)
    return project


def get(db: Session, project_id: int) -> Project | None:
    return (
        db.query(Project)
        .filter(Project.id == int(project_id), Project.deleted_at.is_(None))
        .first()
    )


def list_accessible(
    db: Session,
    user_id: int,
    *,
    is_superuser: bool,
    page: int,
    size: int,
    search: str | None = None,
) -> Tuple[list[Project], int]:
    query = db.query(Project).filter(Project.deleted_at.is_(None))
    if not is_superuser:
        query = query.outerjoin(
            ProjectMember,
            and_(
                ProjectMember.project_id == Project.id,
                ProjectMember.user_id == int(user_id),
                ProjectMember.status == "ACTIVE",
            ),
        ).filter(or_(Project.owner_id == int(user_id), ProjectMember.user_id.isnot(None)))
    normalized_search = str(search or "").strip()
    if normalized_search:
        pattern = literal_like_pattern(normalized_search)
        query = query.filter(
            or_(
                cast(Project.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                Project.name.ilike(pattern, escape=LIKE_ESCAPE),
                Project.description.ilike(pattern, escape=LIKE_ESCAPE),
                Project.status.ilike(pattern, escape=LIKE_ESCAPE),
                cast(Project.organization_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(Project.owner_id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(Project.created_by, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(Project.updated_by, String).ilike(pattern, escape=LIKE_ESCAPE),
            )
        )
    total = query.count()
    rows = (
        query.order_by(Project.updated_at.desc(), Project.id.desc())
        .offset(int(page) * int(size))
        .limit(int(size))
        .all()
    )
    return rows, total


def get_member(db: Session, project_id: int, user_id: int) -> ProjectMember | None:
    return (
        db.query(ProjectMember)
        .filter(
            ProjectMember.project_id == int(project_id),
            ProjectMember.user_id == int(user_id),
        )
        .first()
    )


def list_members(db: Session, project_id: int) -> list[ProjectMember]:
    return (
        db.query(ProjectMember)
        .filter(ProjectMember.project_id == int(project_id))
        .order_by(ProjectMember.created_at.asc(), ProjectMember.user_id.asc())
        .all()
    )


def add_member(db: Session, member: ProjectMember) -> ProjectMember:
    db.add(member)
    db.flush()
    db.refresh(member)
    return member
