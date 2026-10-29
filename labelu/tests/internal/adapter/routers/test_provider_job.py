from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.security import get_password_hash
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.provider_job import ProviderJob, PredictionSet
from labelu.internal.domain.models.prediction_review import (
    PredictionSetProjection,
    PredictionSetReviewAudit,
)
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User


def _new_user(db: Session, username: str) -> User:
    row = db.query(User).filter(User.username == username).first()
    if row:
        return row
    row = User(username=username, hashed_password=get_password_hash("User@123"))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/users/login", json={"username": username, "password": "User@123"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": response.json()["data"]["token"]}


def _asset(client: TestClient, headers: dict[str, str], key: str, *, passed=True) -> int:
    job = client.post(
        "/api/v1/ingestion-jobs",
        json={"source_type": "UPLOAD"},
        headers={**headers, "Idempotency-Key": f"asset:{key}"},
    )
    assert job.status_code == 201, job.text
    response = client.post(
        f"/api/v1/ingestion-jobs/{job.json()['data']['id']}/assets",
        json={
            "object_uri": f"file:///{key}.jpg",
            "media_type": "IMAGE",
            "sha256": "a" * 64,
            "scan_status": "PASSED" if passed else "PENDING",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["id"]


def _seed_prediction_for_review(db: Session, user_id: int, *, payload=None):
    task = Task(
        name="review-task",
        media_type="IMAGE",
        status="CONFIGURED",
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(task)
    db.flush()
    sample = TaskSample(
        task_id=task.id,
        inner_id=7,
        created_by=user_id,
        updated_by=user_id,
        data='{"formal":{"kept":true}}',
        state="DONE",
    )
    db.add(sample)
    db.flush()
    job = ProviderJob(
        provider_key="yolo",
        provider_version="1",
        model_version_id=None,
        input_asset_ids_json="[]",
        contract_version="prediction.v1",
        request_signature=f"review-job:{task.id}",
        status="SUCCEEDED",
        total_count=1,
        completed_count=1,
        created_by=user_id,
        updated_by=user_id,
    )
    db.add(job)
    db.flush()
    prediction = PredictionSet(
        provider_job_id=job.id,
        sample_id=sample.id,
        version=1,
        annotation_contract_version="annotation_contract.v1",
        prediction_schema_version="prediction.v1",
        payload_uri="s3://prediction/review.json",
        payload_sha256="a" * 64,
        payload_json=json.dumps(payload or {"annotations": {"rectTool": {"result": []}}}),
        status="COMPLETED",
        review_status="PENDING",
        source="AI",
        item_count=0,
        created_by=user_id,
    )
    db.add(prediction)
    db.commit()
    db.refresh(prediction)
    return task.id, sample.id, prediction.id


def test_provider_job_idempotency_prediction_set_and_cancel(client: TestClient, testuser_token_headers: dict[str, str]):
    asset_id = _asset(client, testuser_token_headers, "provider-a")
    payload = {"provider_key": "yolo", "input_asset_ids": [asset_id]}
    first = client.post("/api/v1/provider-jobs", json=payload, headers={**testuser_token_headers, "Idempotency-Key": "provider:req:1"})
    assert first.status_code == 201, first.text
    job = first.json()["data"]
    assert "project_id" not in job
    replay = client.post("/api/v1/provider-jobs", json=payload, headers={**testuser_token_headers, "Idempotency-Key": "provider:req:1"})
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == job["id"]
    result = client.post(f"/api/v1/provider-jobs/{job['id']}/prediction-sets", json={"payload_uri": "s3://predictions/1.json", "payload_sha256": "d" * 64}, headers={**testuser_token_headers, "Idempotency-Key": "provider:result:1"})
    assert result.status_code == 201, result.text
    assert result.json()["data"]["source"] == "AI"
    listed = client.get("/api/v1/prediction-sets", headers=testuser_token_headers)
    assert listed.status_code == 200
    assert listed.json()["meta_data"]["total"] == 1


def test_provider_job_search_matches_job_metadata_and_escapes_like_wildcards(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    asset_id = _asset(client, testuser_token_headers, "provider-search")
    headers = {
        **testuser_token_headers,
        "Idempotency-Key": "provider:search:job",
    }
    created = client.post(
        "/api/v1/provider-jobs",
        json={"provider_key": "yolo", "input_asset_ids": [asset_id]},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["data"]["id"]

    for keyword in (str(job_id), "yolo", "provider:search:job", "QUEUED"):
        response = client.get(
            "/api/v1/provider-jobs",
            params={"q": keyword, "page": 0, "size": 20},
            headers=testuser_token_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["meta_data"]["total"] == 1

    wildcard = client.get(
        "/api/v1/provider-jobs",
        params={"q": "%", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    assert wildcard.status_code == 200, wildcard.text
    assert wildcard.json()["meta_data"]["total"] == 0


def test_prediction_set_search_matches_provider_metadata_and_escapes_like_wildcards(
    client: TestClient, testuser_token_headers: dict[str, str], db: Session
):
    user = db.query(User).filter(User.username == "test@example.com").one()
    _, sample_id, prediction_id = _seed_prediction_for_review(db, user.id)
    base = "/api/v1/prediction-sets"

    for keyword in ("yolo", str(sample_id), str(prediction_id), "PENDING"):
        response = client.get(
            base,
            params={"q": keyword, "page": 0, "size": 20},
            headers=testuser_token_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["meta_data"]["total"] == 1

    for keyword in ("%", "_"):
        response = client.get(
            base,
            params={"q": keyword, "page": 0, "size": 20},
            headers=testuser_token_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["meta_data"]["total"] == 0


def test_prediction_set_idempotency_rejects_conflicting_retry(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    asset_id = _asset(client, testuser_token_headers, "provider-conflict")
    job = client.post(
        "/api/v1/provider-jobs",
        json={"provider_key": "yolo", "input_asset_ids": [asset_id]},
        headers={**testuser_token_headers, "Idempotency-Key": "provider:conflict:job"},
    )
    assert job.status_code == 201, job.text
    url = f"/api/v1/provider-jobs/{job.json()['data']['id']}/prediction-sets"
    headers = {**testuser_token_headers, "Idempotency-Key": "provider:conflict:result"}
    first = client.post(url, json={"payload": {"items": [{"label": "person"}]}}, headers=headers)
    assert first.status_code == 201, first.text
    replay = client.post(url, json={"payload": {"items": [{"label": "car"}]}}, headers=headers)
    assert replay.status_code == 409, replay.text


def test_provider_job_requires_idempotency_key(client: TestClient, testuser_token_headers: dict[str, str]):
    asset_id = _asset(client, testuser_token_headers, "provider-b")
    response = client.post("/api/v1/provider-jobs", json={"provider_key": "yolo", "input_asset_ids": [asset_id]}, headers=testuser_token_headers)
    assert response.status_code == 422


def test_prediction_set_cannot_bypass_review_state_machine(client: TestClient, testuser_token_headers: dict[str, str]):
    asset_id = _asset(client, testuser_token_headers, "provider-c")
    job = client.post("/api/v1/provider-jobs", json={"provider_key": "yolo", "input_asset_ids": [asset_id]}, headers={**testuser_token_headers, "Idempotency-Key": "review-gate:job"})
    assert job.status_code == 201, job.text
    response = client.post(f"/api/v1/provider-jobs/{job.json()['data']['id']}/prediction-sets", json={"payload": {"annotations": {}}, "review_status": "ACCEPTED"}, headers={**testuser_token_headers, "Idempotency-Key": "review-gate:result"})
    assert response.status_code == 409, response.text


def test_prediction_set_accepts_inline_payload_without_uri(client: TestClient, testuser_token_headers: dict[str, str]):
    asset_id = _asset(client, testuser_token_headers, "provider-d")
    job = client.post("/api/v1/provider-jobs", json={"provider_key": "yolo", "input_asset_ids": [asset_id]}, headers={**testuser_token_headers, "Idempotency-Key": "provider:inline:job"})
    assert job.status_code == 201, job.text
    result = client.post(f"/api/v1/provider-jobs/{job.json()['data']['id']}/prediction-sets", json={"payload": {"annotations": {"rectTool": {"result": []}}}}, headers={**testuser_token_headers, "Idempotency-Key": "provider:inline:result"})
    assert result.status_code == 201, result.text


def test_provider_job_rejects_unscanned_asset_and_enforces_creator_isolation(client: TestClient, testuser_token_headers: dict[str, str], db: Session):
    asset_id = _asset(client, testuser_token_headers, "provider-e", passed=False)
    response = client.post("/api/v1/provider-jobs", json={"provider_key": "yolo", "input_asset_ids": [asset_id]}, headers={**testuser_token_headers, "Idempotency-Key": "provider:pending:req"})
    assert response.status_code == 409
    other = _new_user(db, "provider-isolated@example.com")
    other_headers = _login(client, other.username)
    assert client.get("/api/v1/provider-jobs", headers=other_headers).json()["meta_data"]["total"] == 0


def test_prediction_set_rejects_sample_from_another_creator(client: TestClient, testuser_token_headers: dict[str, str], db: Session):
    asset_id = _asset(client, testuser_token_headers, "provider-f")
    job = client.post("/api/v1/provider-jobs", json={"provider_key": "yolo", "input_asset_ids": [asset_id]}, headers={**testuser_token_headers, "Idempotency-Key": "provider:sample:isolation"})
    assert job.status_code == 201, job.text
    other = _new_user(db, "provider-sample-other@example.com")
    task = Task(name="other-task", media_type="IMAGE", status="DRAFT", created_by=other.id, updated_by=other.id)
    db.add(task)
    db.flush()
    sample = TaskSample(task_id=task.id, created_by=other.id, updated_by=other.id)
    db.add(sample)
    db.commit()
    response = client.post(f"/api/v1/provider-jobs/{job.json()['data']['id']}/prediction-sets", json={"sample_id": sample.id, "payload": {"items": []}}, headers={**testuser_token_headers, "Idempotency-Key": "provider:sample:isolation:result"})
    assert response.status_code == 403, response.text


def test_prediction_set_review_and_legacy_projection_is_idempotent_and_non_destructive(client: TestClient, testuser_token_headers: dict[str, str], db: Session):
    user = db.query(User).filter(User.username == "test@example.com").one()
    _, sample_id, set_id = _seed_prediction_for_review(db, user.id)
    before = db.query(TaskSample).filter(TaskSample.id == sample_id).one().data
    assert client.post(f"/api/v1/prediction-sets/{set_id}/review", json={"target_status": "IN_REVIEW"}, headers={**testuser_token_headers, "Idempotency-Key": "prediction-review:test:1"}).status_code == 200
    accepted = client.post(f"/api/v1/prediction-sets/{set_id}/review", json={"target_status": "ACCEPTED"}, headers={**testuser_token_headers, "Idempotency-Key": "prediction-review:test:2"})
    assert accepted.status_code == 200, accepted.text
    replay = client.post(f"/api/v1/prediction-sets/{set_id}/review", json={"target_status": "ACCEPTED"}, headers={**testuser_token_headers, "Idempotency-Key": "prediction-review:test:2"})
    assert replay.status_code == 200
    assert db.query(PredictionSetReviewAudit).filter(PredictionSetReviewAudit.prediction_set_id == set_id).count() == 2
    projected = client.post(f"/api/v1/prediction-sets/{set_id}/project-legacy", headers={**testuser_token_headers, "Idempotency-Key": "prediction-projection:test:1"})
    assert projected.status_code == 201, projected.text
    projection = projected.json()["data"]
    assert "project_id" not in projection
    replay_projection = client.post(f"/api/v1/prediction-sets/{set_id}/project-legacy", headers={**testuser_token_headers, "Idempotency-Key": "prediction-projection:test:1"})
    assert replay_projection.status_code == 201
    assert replay_projection.json()["data"]["id"] == projection["id"]
    assert db.query(TaskPreAnnotation).filter(TaskPreAnnotation.id == projection["pre_annotation_id"]).count() == 1
    assert db.query(TaskSample).filter(TaskSample.id == sample_id).one().data == before
    assert db.query(PredictionSetProjection).filter(PredictionSetProjection.prediction_set_id == set_id).count() == 1


def test_prediction_set_projection_requires_acceptance_and_inline_payload(client: TestClient, testuser_token_headers: dict[str, str], db: Session):
    user = db.query(User).filter(User.username == "test@example.com").one()
    _, _, set_id = _seed_prediction_for_review(db, user.id)
    row = db.query(PredictionSet).filter(PredictionSet.id == set_id).one()
    row.payload_json = None
    db.commit()
    assert client.post(f"/api/v1/prediction-sets/{set_id}/review", json={"target_status": "IN_REVIEW"}, headers=testuser_token_headers).status_code == 200
    assert client.post(f"/api/v1/prediction-sets/{set_id}/review", json={"target_status": "ACCEPTED"}, headers=testuser_token_headers).status_code == 200
    response = client.post(f"/api/v1/prediction-sets/{set_id}/project-legacy", headers=testuser_token_headers)
    assert response.status_code == 409, response.text


def test_prediction_set_review_is_creator_isolated(client: TestClient, testuser_token_headers: dict[str, str], db: Session):
    user = db.query(User).filter(User.username == "test@example.com").one()
    _, _, set_id = _seed_prediction_for_review(db, user.id)
    other = _new_user(db, "prediction-review-isolated@example.com")
    other_headers = _login(client, other.username)
    response = client.get(f"/api/v1/prediction-sets/{set_id}", headers=other_headers)
    assert response.status_code == 403, response.text
