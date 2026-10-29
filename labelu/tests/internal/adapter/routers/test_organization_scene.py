from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.bootstrap_admin import DEFAULT_ADMIN_USERNAME
from labelu.internal.domain.models.organization import Organization, Scene
from labelu.internal.domain.models.project import Project
from labelu.internal.domain.models.user import User

RETIRED_ERROR = {
    "msg": "该能力已下线，请使用任务、训练、BM1688 编译、真机验证和模型发布主流程",
    "err_code": 30004,
}


def _admin(db: Session) -> User:
    return db.query(User).filter(User.username == DEFAULT_ADMIN_USERNAME).one()


def _assert_retired(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json() == RETIRED_ERROR


def _history(db: Session) -> tuple[Organization, Project, Scene]:
    admin = _admin(db)
    organization = Organization(
        owner_id=admin.id,
        name="历史组织",
        description="仅供迁移审计读取",
        status="ACTIVE",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(organization)
    db.flush()
    project = Project(
        organization_id=organization.id,
        owner_id=admin.id,
        name="历史项目",
        status="ARCHIVED",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(project)
    db.flush()
    scene = Scene(
        project_id=project.id,
        scene_key="historical_road",
        name="历史道路场景",
        status="ACTIVE",
        metadata_json=json.dumps({"来源": "历史迁移"}, ensure_ascii=False),
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(scene)
    db.commit()
    return organization, project, scene


def test_organization_and_scene_history_is_read_only_for_administrator(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    organization, project, scene = _history(db)

    organizations = client.get("/api/v1/organizations", headers=testadmin_token_headers)
    scenes = client.get(
        f"/api/v1/projects/{project.id}/scenes", headers=testadmin_token_headers
    )
    detail = client.get(
        f"/api/v1/projects/{project.id}/scenes/{scene.id}",
        headers=testadmin_token_headers,
    )

    assert organizations.status_code == 200, organizations.text
    assert organizations.json()["data"][0]["id"] == organization.id
    assert scenes.status_code == 200, scenes.text
    assert scenes.json()["data"][0]["id"] == scene.id
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["metadata"] == {"来源": "历史迁移"}


def test_organization_and_scene_writes_are_retired_without_database_changes(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    _, project, _ = _history(db)
    before = {
        Organization: db.query(Organization).count(),
        Project: db.query(Project).count(),
        Scene: db.query(Scene).count(),
    }

    organization = client.post(
        "/api/v1/organizations",
        json={"name": "不得创建的组织"},
        headers=testadmin_token_headers,
    )
    scene = client.post(
        f"/api/v1/projects/{project.id}/scenes",
        json={"scene_key": "retired", "name": "不得创建的场景"},
        headers=testadmin_token_headers,
    )

    _assert_retired(organization)
    _assert_retired(scene)
    db.expire_all()
    for model, expected in before.items():
        assert db.query(model).count() == expected
