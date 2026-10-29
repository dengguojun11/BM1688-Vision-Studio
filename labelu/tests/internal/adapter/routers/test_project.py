from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.bootstrap_admin import DEFAULT_ADMIN_USERNAME
from labelu.internal.domain.models.project import Project, ProjectMember
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


def test_project_history_is_read_only_for_administrator(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    admin = _admin(db)
    project = Project(
        owner_id=admin.id,
        name="历史项目",
        description="仅供迁移审计读取",
        status="ARCHIVED",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(project)
    db.flush()
    db.add(
        ProjectMember(
            project_id=project.id,
            user_id=admin.id,
            role="owner",
            status="ACTIVE",
            invited_by=admin.id,
        )
    )
    db.commit()

    listed = client.get("/api/v1/projects", headers=testadmin_token_headers)
    detail = client.get(
        f"/api/v1/projects/{project.id}", headers=testadmin_token_headers
    )
    members = client.get(
        f"/api/v1/projects/{project.id}/members", headers=testadmin_token_headers
    )

    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["data"]] == [project.id]
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["name"] == "历史项目"
    assert members.status_code == 200, members.text
    assert members.json()["data"][0]["user_id"] == admin.id


def test_project_list_supports_literal_search_and_server_pagination(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    admin = _admin(db)
    rows = [
        Project(
            owner_id=admin.id,
            name="道路巡检一",
            description="Road inspection batch one",
            status="ACTIVE",
            created_by=admin.id,
            updated_by=admin.id,
        ),
        Project(
            owner_id=admin.id,
            name="道路巡检二",
            description="Road inspection batch two",
            status="ARCHIVED",
            created_by=admin.id,
            updated_by=admin.id,
        ),
        Project(
            owner_id=admin.id,
            name="仓储盘点",
            description="Warehouse inventory",
            status="ACTIVE",
            created_by=admin.id,
            updated_by=admin.id,
        ),
    ]
    db.add_all(rows)
    db.commit()

    page = client.get(
        "/api/v1/projects",
        params={"q": "道路巡检", "page": 0, "size": 1},
        headers=testadmin_token_headers,
    )

    assert page.status_code == 200, page.text
    payload = page.json()
    assert payload["meta_data"] == {"total": 2, "page": 0, "size": 1}
    assert len(payload["data"]) == 1
    assert "道路巡检" in payload["data"][0]["name"]

    # LIKE metacharacters are treated as literal user text rather than a
    # wildcard that would return every project.
    literal = client.get(
        "/api/v1/projects",
        params={"q": "%", "page": 0, "size": 100},
        headers=testadmin_token_headers,
    )
    assert literal.status_code == 200, literal.text
    assert literal.json()["meta_data"]["total"] == 0


def test_project_writes_are_retired_without_database_changes(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    admin = _admin(db)
    project = Project(
        owner_id=admin.id,
        name="不可修改的历史项目",
        status="ARCHIVED",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(project)
    db.commit()

    before = {
        Project: db.query(Project).count(),
        ProjectMember: db.query(ProjectMember).count(),
    }
    calls = (
        client.post(
            "/api/v1/projects",
            json={"name": "不得创建的项目"},
            headers=testadmin_token_headers,
        ),
        client.patch(
            f"/api/v1/projects/{project.id}",
            json={"name": "不得修改"},
            headers=testadmin_token_headers,
        ),
        client.post(
            f"/api/v1/projects/{project.id}/members",
            json={"user_id": admin.id, "role": "member"},
            headers=testadmin_token_headers,
        ),
    )

    for response in calls:
        _assert_retired(response)

    db.expire_all()
    for model, expected in before.items():
        assert db.query(model).count() == expected
    stored = db.query(Project).filter(Project.id == project.id).one()
    assert stored.name == "不可修改的历史项目"
