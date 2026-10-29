from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.bootstrap_admin import DEFAULT_ADMIN_USERNAME
from labelu.internal.domain.models.deployment import (
    DeploymentObservation,
    DeploymentRelease,
    DeploymentTarget,
    DeploymentTransitionAudit,
)
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


def _history(db: Session) -> tuple[Project, DeploymentRelease]:
    admin = _admin(db)
    project = Project(
        owner_id=admin.id,
        name="历史部署项目",
        status="ARCHIVED",
        created_by=admin.id,
        updated_by=admin.id,
    )
    db.add(project)
    db.flush()
    release = DeploymentRelease(
        project_id=project.id,
        model_version_id=1,
        release_key="historical-release",
        status="COMPLETED",
        target_device_ids_json="[]",
        gray_ratio=0.05,
        observation_seconds=900,
        created_by=admin.id,
    )
    db.add(release)
    db.commit()
    return project, release


def test_deployment_history_is_read_only_for_administrator(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    project, release = _history(db)

    listed = client.get(
        "/api/v1/deployment-releases",
        params={"project_id": project.id},
        headers=testadmin_token_headers,
    )
    detail = client.get(
        f"/api/v1/deployment-releases/{release.id}",
        headers=testadmin_token_headers,
    )
    audits = client.get(
        f"/api/v1/deployment-releases/{release.id}/transition-audits",
        headers=testadmin_token_headers,
    )

    assert listed.status_code == 200, listed.text
    assert listed.json()["data"][0]["id"] == release.id
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["status"] == "COMPLETED"
    assert audits.status_code == 200, audits.text
    assert audits.json()["data"] == []


def test_deployment_history_supports_literal_keyword_search(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    project, release = _history(db)
    release.release_key = "night-road-release"
    db.add(
        DeploymentRelease(
            project_id=project.id,
            model_version_id=2,
            release_key="day-road-release",
            status="DRAFT",
            target_device_ids_json='["edge-day-01"]',
            created_by=release.created_by,
        )
    )
    db.commit()

    response = client.get(
        "/api/v1/deployment-releases",
        params={"project_id": project.id, "q": "night-road"},
        headers=testadmin_token_headers,
    )

    assert response.status_code == 200, response.text
    assert [row["release_key"] for row in response.json()["data"]] == [
        "night-road-release"
    ]
    assert response.json()["meta_data"]["total"] == 1


def test_deployment_writes_are_retired_without_database_changes(
    client: TestClient,
    testadmin_token_headers: dict[str, str],
    db: Session,
):
    project, release = _history(db)
    before = {
        DeploymentRelease: db.query(DeploymentRelease).count(),
        DeploymentTarget: db.query(DeploymentTarget).count(),
        DeploymentObservation: db.query(DeploymentObservation).count(),
        DeploymentTransitionAudit: db.query(DeploymentTransitionAudit).count(),
    }

    create = client.post(
        "/api/v1/deployment-releases",
        json={
            "project_id": project.id,
            "model_version_id": 1,
            "target_device_ids": ["retired-device"],
            "target_bundle_sha256": "0" * 64,
        },
        headers=testadmin_token_headers,
    )
    transitions = [
        client.post(
            f"/api/v1/deployment-releases/{release.id}/{operation}",
            json={},
            headers=testadmin_token_headers,
        )
        for operation in (
            "approve",
            "start",
            "precheck",
            "canary",
            "promote",
            "expand",
            "complete",
            "rollback",
        )
    ]
    report = client.post(
        f"/api/v1/deployment-releases/{release.id}/targets/retired-device/report",
        json={"reported_state": "ACTIVE"},
        headers={"X-Board-Agent-Token": "retired"},
    )
    observation = client.post(
        f"/api/v1/deployment-releases/{release.id}/targets/retired-device/observations",
        json={"observation_key": "retired-observation", "status": "HEALTHY"},
        headers={"X-Board-Agent-Token": "retired"},
    )

    for response in [create, *transitions, report, observation]:
        _assert_retired(response)

    db.expire_all()
    for model, expected in before.items():
        assert db.query(model).count() == expected
    stored = (
        db.query(DeploymentRelease).filter(DeploymentRelease.id == release.id).one()
    )
    assert stored.status == "COMPLETED"
