from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.security import get_password_hash
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.media_upload import MediaUploadSession
from labelu.internal.domain.models.task import Task


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


def test_ingestion_job_requires_idempotency_key(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    response = client.post(
        "/api/v1/ingestion-jobs",
        json={"source_type": "UPLOAD"},
        headers=testuser_token_headers,
    )
    assert response.status_code == 422


def test_ingestion_job_is_idempotent_and_assets_are_deduplicated(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    payload = {
        "source_type": "S3",
        "source_snapshot": {"bucket": "private", "secret": "must-not-leak"},
        "selection": {"prefix": "camera/"},
    }
    first = client.post(
        "/api/v1/ingestion-jobs",
        json=payload,
        headers={**testuser_token_headers, "Idempotency-Key": "ingest:test:1"},
    )
    assert first.status_code == 201, first.text
    job = first.json()["data"]
    assert job["status"] == "QUEUED"
    assert job["source_snapshot"]["secret"] == "[REDACTED]"
    replay = client.post(
        "/api/v1/ingestion-jobs",
        json=payload,
        headers={**testuser_token_headers, "Idempotency-Key": "ingest:test:1"},
    )
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == job["id"]

    asset_payload = {
        "asset_uri": "s3://internal/a.jpg",
        "media_type": "image",
        "content_type": "image/jpeg",
        "size_bytes": 123,
        "sha256": "a" * 64,
        "scan_status": "PASSED",
        "metadata": {"camera": "A"},
    }
    asset = client.post(
        f"/api/v1/ingestion-jobs/{job['id']}/assets",
        json=asset_payload,
        headers=testuser_token_headers,
    )
    assert asset.status_code == 201, asset.text
    asset_id = asset.json()["data"]["id"]
    assert asset.json()["data"]["object_uri"] == "s3://internal/a.jpg"
    duplicate = client.post(
        f"/api/v1/ingestion-jobs/{job['id']}/assets",
        json=asset_payload,
        headers=testuser_token_headers,
    )
    assert duplicate.status_code == 201
    assert duplicate.json()["data"]["id"] == asset_id

    listed = client.get(
        "/api/v1/media-assets",
        params={"scan_status": "PASSED"},
        headers=testuser_token_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["meta_data"]["total"] == 1
    assert listed.json()["data"][0]["asset_uri"] == "s3://internal/a.jpg"


def test_ingestion_and_assets_enforce_creator_isolation(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    other = _new_user(db, "ingestion-isolated@example.com")
    other_headers = _login(client, other.username)
    created = client.post(
        "/api/v1/ingestion-jobs",
        json={"source_type": "UPLOAD"},
        headers={**testuser_token_headers, "Idempotency-Key": "isolation:1"},
    )
    assert created.status_code == 201
    job_id = created.json()["data"]["id"]

    assert client.get(f"/api/v1/ingestion-jobs/{job_id}", headers=other_headers).status_code == 403
    listed = client.get("/api/v1/ingestion-jobs", headers=other_headers)
    assert listed.status_code == 200
    assert listed.json()["meta_data"]["total"] == 0


def test_ingestion_job_cancel_is_creator_scoped_and_terminal(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    other = _new_user(db, "ingestion-cancel-isolated@example.com")
    other_headers = _login(client, other.username)
    created = client.post(
        "/api/v1/ingestion-jobs",
        json={"source_type": "UPLOAD"},
        headers={**testuser_token_headers, "Idempotency-Key": "cancel:1"},
    )
    assert created.status_code == 201, created.text
    job_id = created.json()["data"]["id"]

    assert client.post(
        f"/api/v1/ingestion-jobs/{job_id}/cancel", headers=other_headers
    ).status_code == 403

    cancelled = client.post(
        f"/api/v1/ingestion-jobs/{job_id}/cancel",
        headers=testuser_token_headers,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["status"] == "CANCELLED"
    assert cancelled.json()["data"]["completed_at"] is not None

    repeated = client.post(
        f"/api/v1/ingestion-jobs/{job_id}/cancel",
        headers=testuser_token_headers,
    )
    assert repeated.status_code == 409, repeated.text
    listed = client.get("/api/v1/media-assets", headers=other_headers)
    assert listed.status_code == 200
    assert listed.json()["meta_data"]["total"] == 0
    assert client.post(
        f"/api/v1/ingestion-jobs/{job_id}/assets",
        json={
            "object_uri": "file:///private.jpg",
            "media_type": "IMAGE",
            "sha256": "b" * 64,
        },
        headers=other_headers,
    ).status_code == 403


def test_completed_upload_session_bridges_to_creator_asset_once(
    client: TestClient,
    testuser_token_headers: dict[str, str],
    db: Session,
):
    job = client.post(
        "/api/v1/ingestion-jobs",
        json={"source_type": "UPLOAD"},
        headers={**testuser_token_headers, "Idempotency-Key": "upload-bridge:1"},
    ).json()["data"]
    user = db.query(User).filter(User.username == "test@example.com").first()
    assert user is not None
    task = Task(name="桥接任务", media_type="IMAGE", created_by=user.id, updated_by=user.id)
    db.add(task)
    db.flush()
    attachment = TaskAttachment(
        filename="bridge.jpg", url="/api/v1/attachment/bridge.jpg", path="uploads/bridge.jpg",
        task_id=task.id, created_by=user.id, updated_by=user.id, sha256="c" * 64,
        size_bytes=12, content_type="image/jpeg",
    )
    db.add(attachment)
    db.flush()
    session = MediaUploadSession(
        session_id="bridge-session-1", task_id=task.id, filename="bridge.jpg", total_size=12,
        chunk_size=1024 * 1024, total_chunks=1, received_chunks_json="[0]", sha256="c" * 64,
        status="COMPLETED", scan_status="PASSED", attachment_id=attachment.id, created_by=user.id,
    )
    db.add(session)
    db.commit()

    endpoint = f"/api/v1/ingestion-jobs/{job['id']}/assets/from-upload-session"
    first = client.post(endpoint, json={"upload_session_id": session.session_id}, headers=testuser_token_headers)
    assert first.status_code == 201, first.text
    asset_id = first.json()["data"]["id"]
    assert "project_id" not in first.json()["data"]
    replay = client.post(endpoint, json={"upload_session_id": session.session_id}, headers=testuser_token_headers)
    assert replay.status_code == 201
    assert replay.json()["data"]["id"] == asset_id
    assert client.get("/api/v1/media-assets", headers=testuser_token_headers).json()["meta_data"]["total"] == 1
    filtered = client.get(
        "/api/v1/media-assets?q=bridge.jpg",
        headers=testuser_token_headers,
    )
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["meta_data"]["total"] == 1
