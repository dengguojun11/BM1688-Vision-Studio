from labelu.internal.common.label_contract import (
    annotation_sha256 as canonical_annotation_sha256,
)
from labelu.internal.common.security import get_password_hash
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task_collaborator import TaskCollaborator
from labelu.internal.domain.models.user import User


def _seed_sample(db, user_id: int = 1) -> tuple[int, int]:
    task = Task(
        name="审核任务",
        media_type="IMAGE",
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(task)
    db.flush()
    sample = TaskSample(
        task_id=task.id,
        created_by=user_id,
        updated_by=user_id,
        state="DONE",
        data="{}",
    )
    db.add(sample)
    db.commit()
    return task.id, sample.id


def _create_user(db, username: str) -> User:
    row = User(username=username, hashed_password=get_password_hash("User@123"))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _login(client, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/users/login",
        json={"username": username, "password": "User@123"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": response.json()["data"]["token"]}


def _create_review(client, headers, task_id: int, sample_id: int):
    return client.post(
        "/api/v1/annotation-reviews",
        json={
            "task_id": task_id,
            "sample_id": sample_id,
            "annotation_sha256": canonical_annotation_sha256({}),
            "source_type": "formal",
        },
        headers=headers,
    )


def test_annotation_review_state_machine_and_idempotency_without_project(
    client, db, testuser_token_headers, testadmin_token_headers
):
    task_id, sample_id = _seed_sample(db)
    created = _create_review(client, testuser_token_headers, task_id, sample_id)
    repeated = _create_review(client, testuser_token_headers, task_id, sample_id)
    assert created.status_code == 201, created.text
    assert repeated.status_code == 201, repeated.text
    payload = created.json()["data"]
    assert "project_id" not in payload
    review_id = payload["id"]
    assert repeated.json()["data"]["id"] == review_id

    assert (
        client.post(
            f"/api/v1/annotation-reviews/{review_id}/transition",
            json={"target_status": "IN_REVIEW"},
            headers=testuser_token_headers,
        ).status_code
        == 200
    )
    rejected_without_reason = client.post(
        f"/api/v1/annotation-reviews/{review_id}/transition",
        json={"target_status": "REJECTED"},
        headers=testuser_token_headers,
    )
    assert rejected_without_reason.status_code == 422
    arbitration = client.post(
        f"/api/v1/annotation-reviews/{review_id}/transition",
        json={"target_status": "ARBITRATION", "reason": "标注冲突"},
        headers=testuser_token_headers,
    )
    assert arbitration.status_code == 200, arbitration.text
    denied = client.post(
        f"/api/v1/annotation-reviews/{review_id}/transition",
        json={
            "target_status": "ARBITRATED",
            "arbitration_decision": "APPROVED",
            "reason": "专家确认",
        },
        headers=testuser_token_headers,
    )
    assert denied.status_code == 403
    final = client.post(
        f"/api/v1/annotation-reviews/{review_id}/transition",
        json={
            "target_status": "ARBITRATED",
            "arbitration_decision": "APPROVED",
            "reason": "管理人员确认",
        },
        headers=testadmin_token_headers,
    )
    assert final.status_code == 200, final.text
    assert final.json()["data"]["review_status"] == "ARBITRATED"


def test_annotation_review_access_follows_task_collaboration(
    client, db, testuser_token_headers
):
    task_id, sample_id = _seed_sample(db)
    collaborator = _create_user(db, "review-collaborator@example.com")
    outsider = _create_user(db, "review-outsider@example.com")
    db.add(TaskCollaborator(task_id=task_id, user_id=collaborator.id))
    db.commit()
    created = _create_review(client, testuser_token_headers, task_id, sample_id)
    review_id = created.json()["data"]["id"]

    collaborator_headers = _login(client, collaborator.username)
    outsider_headers = _login(client, outsider.username)
    assert (
        client.get(
            f"/api/v1/annotation-reviews/{review_id}", headers=collaborator_headers
        ).status_code
        == 200
    )
    assert (
        client.get("/api/v1/annotation-reviews", headers=collaborator_headers).json()[
            "meta_data"
        ]["total"]
        == 1
    )
    assert (
        client.get(
            f"/api/v1/annotation-reviews/{review_id}", headers=outsider_headers
        ).status_code
        == 403
    )
    assert (
        client.get("/api/v1/annotation-reviews", headers=outsider_headers).json()[
            "meta_data"
        ]["total"]
        == 0
    )


def test_annotation_review_rejects_sample_from_another_task(
    client, db, testuser_token_headers
):
    first_task_id, _ = _seed_sample(db)
    _, second_sample_id = _seed_sample(db)
    response = _create_review(
        client, testuser_token_headers, first_task_id, second_sample_id
    )
    assert response.status_code == 404, response.text


def test_annotation_review_openapi_contains_no_project_parameter(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/annotation-reviews"]["get"]
    assert all(item["name"] != "project_id" for item in operation.get("parameters", []))


def test_annotation_review_search_matches_task_and_ids_without_like_wildcards(
    client, db, testuser_token_headers
):
    task_id, sample_id = _seed_sample(db)
    created = _create_review(client, testuser_token_headers, task_id, sample_id)
    assert created.status_code == 201, created.text
    base = "/api/v1/annotation-reviews"

    for keyword in ("审核任务", str(task_id), str(sample_id), "FORMAL"):
        response = client.get(
            base,
            params={"q": keyword, "page": 0, "size": 20},
            headers=testuser_token_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["meta_data"]["total"] == 1

    # User input is a literal contains search. SQL LIKE metacharacters must
    # not turn an arbitrary query into a full-table wildcard.
    for keyword in ("%", "_"):
        response = client.get(
            base,
            params={"q": keyword, "page": 0, "size": 20},
            headers=testuser_token_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["meta_data"]["total"] == 0
