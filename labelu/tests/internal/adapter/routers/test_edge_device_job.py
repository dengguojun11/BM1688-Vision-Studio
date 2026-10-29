from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.adapter.persistence import crud_export_job
from labelu.internal.adapter.persistence import crud_task
from labelu.internal.adapter.persistence import crud_user
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.edge_device_job import EdgeDeviceJob


def test_edge_device_job_lifecycle(
    client: TestClient, testadmin_token_headers: dict, db: Session
) -> None:
    testuser_token_headers = testadmin_token_headers
    current_user = crud_user.get_user_by_username(db=db, username="test@example.com")
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name="edge task",
                description="description",
                tips="tips",
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        export_job = crud_export_job.create(
            db=db,
            task_id=task.id,
            user_id=current_user.id,
            export_type="JSON",
            sample_ids=[1],
        )
        crud_export_job.update_status(
            db=db,
            job=export_job,
            status="COMPLETED",
            processed_count=1,
            file_path="edge/export.json",
        )

    before_count = db.query(EdgeDeviceJob).count()

    create_resp = client.post(
        f"{settings.API_V1_STR}/edge-device-jobs",
        headers=testuser_token_headers,
        json={"export_job_id": export_job.id, "payload": {"kind": "bm1688"}},
    )
    assert create_resp.status_code == 403
    assert db.query(EdgeDeviceJob).count() == before_count

    pending_result_resp = client.post(
        f"{settings.API_V1_STR}/edge-device-jobs/999999/result",
        headers=testuser_token_headers,
        json={
            "device_id": "bm1688-box-01",
            "status": "COMPLETED",
            "result": {"labels": 3},
        },
    )
    assert pending_result_resp.status_code == 403

    claim_resp = client.post(
        f"{settings.API_V1_STR}/edge-device-jobs/claim",
        headers=testuser_token_headers,
        json={"device_id": "bm1688-box-01"},
    )
    assert claim_resp.status_code == 403
    assert db.query(EdgeDeviceJob).count() == before_count

    wrong_device_resp = client.post(
        f"{settings.API_V1_STR}/edge-device-jobs/999999/result",
        headers=testuser_token_headers,
        json={
            "device_id": "bm1688-box-02",
            "status": "COMPLETED",
            "result": {"labels": 3},
        },
    )
    assert wrong_device_resp.status_code == 403

    complete_resp = client.post(
        f"{settings.API_V1_STR}/edge-device-jobs/999999/result",
        headers=testuser_token_headers,
        json={
            "device_id": "bm1688-box-01",
            "status": "COMPLETED",
            "result": {"labels": 3},
        },
    )
    assert complete_resp.status_code == 403
    assert db.query(EdgeDeviceJob).count() == before_count

    duplicate_resp = client.post(
        f"{settings.API_V1_STR}/edge-device-jobs/999999/result",
        headers=testuser_token_headers,
        json={
            "device_id": "bm1688-box-01",
            "status": "FAILED",
            "result": {"labels": 0},
        },
    )
    assert duplicate_resp.status_code == 403


def test_edge_device_claim_empty_queue(
    client: TestClient, testadmin_token_headers: dict
) -> None:
    testuser_token_headers = testadmin_token_headers
    resp = client.post(
        f"{settings.API_V1_STR}/edge-device-jobs/claim",
        headers=testuser_token_headers,
        json={"device_id": "bm1688-box-01"},
    )

    assert resp.status_code == 403
