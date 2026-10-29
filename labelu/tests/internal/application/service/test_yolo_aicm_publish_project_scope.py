"""AICM publication list project-boundary tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_user
from labelu.internal.application.service import yolo_aicm_publish as service
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.project import Project
from labelu.internal.domain.models.project import ProjectMember
from labelu.internal.domain.models.task import MediaType, Task


def _seed_project_tasks(db: Session, user_id: int):
    with begin_transaction(db):
        first = Project(
            owner_id=user_id,
            name="publish-scope-a",
            created_by=user_id,
            updated_by=user_id,
        )
        second = Project(
            owner_id=user_id,
            name="publish-scope-b",
            created_by=user_id,
            updated_by=user_id,
        )
        db.add_all([first, second])
        db.flush()
        task_a = Task(
            name="publish-task-a",
            media_type=MediaType.IMAGE.value,
            project_id=first.id,
            created_by=user_id,
        )
        task_b = Task(
            name="publish-task-b",
            media_type=MediaType.IMAGE.value,
            project_id=second.id,
            created_by=user_id,
        )
        db.add_all([task_a, task_b])
        db.flush()
        return first.id, second.id, task_a.id, task_b.id


def test_publish_history_filters_explicit_project_scope(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None
    project_a, project_b, task_a, task_b = _seed_project_tasks(db, int(user.id))

    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda **kwargs: (
            [
                {"job_id": "a", "task_id": task_a, "user_id": user.id},
                {"job_id": "b", "task_id": task_b, "user_id": user.id},
            ],
            2,
        ),
    )
    monkeypatch.setattr(
        service,
        "_job_to_history_row",
        lambda _db, raw: raw,
    )

    rows, total = service.list_publish_history(
        db,
        user,
        project_id=project_a,
    )
    assert total == 1
    assert [row["job_id"] for row in rows] == ["a"]

    # The old aggregate call remains compatible for a user who owns both
    # tasks; the explicit project call above is the tenant-safe contract.
    rows, total = service.list_publish_history(db, user)
    assert total == 2
    assert {row["job_id"] for row in rows} == {"a", "b"}

    # A project member can inspect that project's history even when they did
    # not create or collaborate on the underlying legacy task.
    member = SimpleNamespace(id=int(user.id) + 10000, username="project-member")
    with begin_transaction(db):
        db.add(
            ProjectMember(
                project_id=project_a,
                user_id=member.id,
                role="viewer",
                status="ACTIVE",
                invited_by=user.id,
            )
        )
    rows, total = service.list_publish_history(db, member, project_id=project_a)
    assert total == 1
    assert rows[0]["job_id"] == "a"

    with pytest.raises(LabelUException) as caught:
        service.list_publish_history(db, user, project_id=project_b + 1000)
    assert caught.value.status_code == status.HTTP_404_NOT_FOUND


def test_publishable_requires_project_access_before_query(db: Session):
    user = crud_user.get_user_by_username(db, username="test@example.com")
    assert user is not None
    project_a, project_b, _task_a, _task_b = _seed_project_tasks(db, int(user.id))
    other = SimpleNamespace(id=int(user.id) + 10000, username="other@example.com")

    # Owner access is accepted before the export query executes.  The empty
    # result is expected because this fixture intentionally has no completed
    # BM1688 export.
    rows, total = service.list_publishable(db, user, project_id=project_a)
    assert rows == []
    assert total == 0

    with pytest.raises(LabelUException) as caught:
        service.list_publishable(db, other, project_id=project_b)
    assert caught.value.status_code == status.HTTP_403_FORBIDDEN


def test_publish_list_routes_do_not_forward_retired_project_id(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, int | None] = {}

    def fake_publishable(db, current_user, **kwargs):
        captured["publishable"] = kwargs.get("project_id")
        return [], 0

    def fake_history(db, current_user, **kwargs):
        captured["history"] = kwargs.get("project_id")
        return [], 0

    monkeypatch.setattr(service, "list_publishable", fake_publishable)
    monkeypatch.setattr(service, "list_publish_history", fake_history)

    response = client.get(
        "/api/v1/yolo-aicm-publish/publishable",
        params={"project_id": 17},
        headers=testuser_token_headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert captured["publishable"] is None

    response = client.get(
        "/api/v1/yolo-aicm-publish/publish-jobs",
        params={"project_id": 17},
        headers=testuser_token_headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert captured["history"] is None


def test_publish_routes_do_not_expose_project_query_parameters(client):
    paths = client.app.openapi()["paths"]
    for path in (
        "/api/v1/yolo-aicm-publish/publishable",
        "/api/v1/yolo-aicm-publish/publish-jobs",
        "/api/v1/yolo-aicm-publish/publish-jobs/{job_id}",
    ):
        parameters = paths[path].get("get", {}).get("parameters", [])
        assert "project_id" not in {
            str(item.get("name") or "") for item in parameters
        }
