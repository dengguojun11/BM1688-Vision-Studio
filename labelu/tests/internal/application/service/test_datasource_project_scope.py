from __future__ import annotations

import asyncio
import pytest
from sqlalchemy.orm import Session

from labelu.internal.application.command.datasource import CreateDataSourceCommand
from labelu.internal.application.service import datasource as service
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.data_source import DataSource
from labelu.internal.domain.models.project import Project, ProjectMember
from labelu.internal.domain.models.user import User


def _user(db: Session, username: str) -> User:
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        return existing
    row = User(username=username, hashed_password="test")
    db.add(row)
    db.flush()
    return row


def _project(db: Session, owner_id: int) -> Project:
    row = Project(
        owner_id=owner_id,
        name="scope-project",
        status="ACTIVE",
        created_by=owner_id,
        updated_by=owner_id,
    )
    db.add(row)
    db.flush()
    return row


def _source(db: Session, *, project_id: int | None, created_by: int) -> DataSource:
    row = DataSource(
        project_id=project_id,
        name="source",
        type="S3",
        bucket="bucket",
        access_key_id="encrypted-ak",
        secret_access_key="encrypted-sk",
        created_by=created_by,
        updated_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def test_project_data_source_allows_active_member_read_only(db: Session):
    owner = _user(db, "scope-owner@example.com")
    member = _user(db, "scope-member@example.com")
    project = _project(db, owner.id)
    db.add(
        ProjectMember(
            project_id=project.id, user_id=member.id, role="viewer", status="ACTIVE"
        )
    )
    source = _source(db, project_id=project.id, created_by=owner.id)
    db.commit()

    response = service._to_response(source)
    assert response.project_id == project.id
    service._authorize(db, source, member)
    with pytest.raises(LabelUException) as exc:
        service._authorize(db, source, member, manage=True)
    assert exc.value.status_code == 403


def test_project_data_source_rejects_foreign_user(db: Session):
    owner = _user(db, "scope-owner-2@example.com")
    foreign = _user(db, "scope-foreign@example.com")
    project = _project(db, owner.id)
    source = _source(db, project_id=project.id, created_by=owner.id)
    db.commit()

    with pytest.raises(LabelUException) as exc:
        service._authorize(db, source, foreign)
    assert exc.value.status_code == 403


def test_legacy_data_source_is_only_visible_to_creator(db: Session):
    creator = _user(db, "legacy-source-owner@example.com")
    other = _user(db, "legacy-source-other@example.com")
    source = _source(db, project_id=None, created_by=creator.id)
    db.commit()

    service._authorize(db, source, creator)
    with pytest.raises(LabelUException) as exc:
        service._authorize(db, source, other)
    assert exc.value.status_code == 403


def test_create_scoped_data_source_requires_project_manage_access(db: Session):
    owner = _user(db, "create-owner@example.com")
    foreign = _user(db, "create-foreign@example.com")
    project = _project(db, owner.id)
    db.commit()
    command = CreateDataSourceCommand(
        project_id=project.id,
        name="scoped",
        bucket="bucket",
        access_key_id="ak",
        secret_access_key="sk",
    )

    with pytest.raises(LabelUException) as exc:
        asyncio.run(service.create(db, command, foreign))
    assert exc.value.status_code == 403


def test_list_data_sources_searches_visible_fields_and_reports_filtered_total(db: Session):
    creator = _user(db, "search-source-owner@example.com")
    _source(db, project_id=None, created_by=creator.id)
    match = DataSource(
        name="production-media",
        type="S3",
        endpoint="https://objects.example.com",
        region="cn-north-1",
        bucket="media-bucket",
        prefix="incoming/",
        access_key_id="encrypted-ak",
        secret_access_key="encrypted-sk",
        created_by=creator.id,
        updated_by=creator.id,
    )
    db.add(match)
    db.commit()

    rows, total = asyncio.run(
        service.list_by(db, creator, page=0, size=20, search="objects.example.com")
    )

    assert total == 1
    assert [row.name for row in rows] == ["production-media"]
