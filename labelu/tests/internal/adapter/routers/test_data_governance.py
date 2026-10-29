import hashlib

from fastapi.testclient import TestClient

from labelu.internal.common.config import settings


def _task(client: TestClient, headers: dict[str, str]) -> int:
    response = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=headers,
        json={"name": "治理上传任务", "description": "", "tips": ""},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]["id"]


def test_chunked_upload_completes_and_deduplicates(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    task_id = _task(client, testuser_token_headers)
    payload = b"7gai-governance-media" * 20000
    digest = hashlib.sha256(payload).hexdigest()
    created = client.post(
        f"{settings.API_V1_STR}/data-governance/upload-sessions",
        headers=testuser_token_headers,
        json={
            "task_id": task_id,
            "filename": "sample.bin",
            "total_size": len(payload),
            "chunk_size": 256 * 1024,
            "sha256": digest,
        },
    )
    assert created.status_code == 201, created.text
    session = created.json()["data"]
    session_id = session["session_id"]
    chunk_size = session["chunk_size"]
    for chunk_no, start in enumerate(range(0, len(payload), chunk_size)):
        response = client.put(
            f"{settings.API_V1_STR}/data-governance/upload-sessions/{session_id}/chunks/{chunk_no}",
            headers=testuser_token_headers,
            files={"file": ("chunk", payload[start : start + chunk_size], "application/octet-stream")},
        )
        assert response.status_code == 200, response.text
    completed = client.post(
        f"{settings.API_V1_STR}/data-governance/upload-sessions/{session_id}/complete",
        headers=testuser_token_headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["data"]["status"] == "COMPLETED"
    assert completed.json()["data"]["scan_status"] == "NOT_CONFIGURED"
    attachment_id = completed.json()["data"]["attachment_id"]

    duplicate = client.post(
        f"{settings.API_V1_STR}/data-governance/upload-sessions",
        headers=testuser_token_headers,
        json={"task_id": task_id, "filename": "renamed.bin", "total_size": len(payload), "chunk_size": 256 * 1024},
    )
    duplicate_id = duplicate.json()["data"]["session_id"]
    for chunk_no, start in enumerate(range(0, len(payload), chunk_size)):
        response = client.put(
            f"{settings.API_V1_STR}/data-governance/upload-sessions/{duplicate_id}/chunks/{chunk_no}",
            headers=testuser_token_headers,
            files={"file": ("chunk", payload[start : start + chunk_size], "application/octet-stream")},
        )
        assert response.status_code == 200, response.text
    deduped = client.post(
        f"{settings.API_V1_STR}/data-governance/upload-sessions/{duplicate_id}/complete",
        headers=testuser_token_headers,
    )
    assert deduped.status_code == 200, deduped.text
    assert deduped.json()["data"]["status"] == "DUPLICATE"
    assert deduped.json()["data"]["attachment_id"] == attachment_id


def test_dataset_version_validation_endpoint(
    client: TestClient, testuser_token_headers: dict[str, str]
):
    dataset = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testuser_token_headers,
        json={"name": "校验数据集"},
    )
    assert dataset.status_code == 201, dataset.text
    dataset_payload = dataset.json()["data"]
    assert "project_id" not in dataset_payload
    dataset_id = dataset_payload["id"]
    version = client.post(
        f"{settings.API_V1_STR}/datasets/{dataset_id}/versions",
        headers=testuser_token_headers,
        json={"manifest_rel_path": "missing.json", "manifest_sha256": "Z" * 64, "sample_count": 0},
    )
    assert version.status_code == 201, version.text
    version_id = version.json()["data"]["id"]
    result = client.get(
        f"{settings.API_V1_STR}/dataset-versions/{version_id}/validate",
        headers=testuser_token_headers,
    )
    assert result.status_code == 200, result.text
    assert result.json()["data"]["valid"] is False
    assert result.json()["data"]["checks"]["manifest_sha256_format"] is False
