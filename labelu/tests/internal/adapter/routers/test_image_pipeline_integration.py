"""图像导入流水线端到端：上传 → 暂存特征 → 批量建样本 → 元数据 → 清理暂存。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.common.image_pipeline import staging
from labelu.tests.utils.utils import random_lower_string


@pytest.fixture
def image_pipeline_on(monkeypatch):
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_ENABLED", True)
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_CONFIG_PATH", "")
    monkeypatch.setattr(settings, "IMAGE_PIPELINE_REJECT_INVALID_IMAGE", True)


def test_image_pipeline_e2e_upload_samples_metadata_and_staging_cleanup(
    client: TestClient,
    testuser_token_headers: dict,
    db: Session,
    image_pipeline_on,
):
    pytest.importorskip("cv2")
    pytest.importorskip("sklearn")

    task_resp = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "pipeline task", "description": "", "tips": ""},
    )
    assert task_resp.status_code in (200, 201)
    task_id = task_resp.json()["data"]["id"]

    png = Path("labelu/tests/data/test.png")
    assert png.is_file()
    uniq = random_lower_string()[:8]
    names = [f"pipe_{uniq}_{i:04d}.png" for i in range(1, 5)]
    file_ids: list[int] = []

    for name in names:
        with png.open("rb") as f:
            up = client.post(
                f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
                headers=testuser_token_headers,
                files={"file": (name, f, "image/png")},
            )
        assert up.status_code == 201, up.text
        att_id = up.json()["data"]["id"]
        file_ids.append(att_id)
        assert staging.staging_path(task_id, att_id).is_file(), (
            f"staging feature missing for attachment {att_id}"
        )

    sample_payload = [{"file_id": fid, "data": {}} for fid in file_ids]
    create_resp = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/samples",
        headers=testuser_token_headers,
        json=sample_payload,
    )
    assert create_resp.status_code == 201, create_resp.text
    sample_ids = create_resp.json()["data"]["ids"]
    assert len(sample_ids) == len(file_ids)

    for sid in sample_ids:
        detail = client.get(
            f"{settings.API_V1_STR}/tasks/{task_id}/samples/{sid}",
            headers=testuser_token_headers,
        )
        assert detail.status_code == 200
        data = detail.json()["data"].get("data") or {}
        if isinstance(data, str):
            data = json.loads(data)
        pipe = data.get("image_pipeline") or {}
        assert pipe.get("scene_id"), f"sample {sid} missing scene_id: {pipe}"
        assert "is_outlier" in pipe

    for att_id in file_ids:
        assert not staging.staging_path(task_id, att_id).is_file(), (
            f"staging not cleaned for attachment {att_id}"
        )

    staging_root = Path(settings.MEDIA_ROOT) / ".image_pipeline_staging" / str(task_id)
    if staging_root.exists():
        assert not any(staging_root.iterdir()), "staging dir should be empty or removed"


def test_invalid_image_rejected_when_pipeline_enabled(
    client: TestClient,
    testuser_token_headers: dict,
    image_pipeline_on,
):
    pytest.importorskip("cv2")

    task_resp = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "bad image task", "description": "", "tips": ""},
    )
    task_id = task_resp.json()["data"]["id"]

    up = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/attachments",
        headers=testuser_token_headers,
        files={"file": ("not_image.png", b"not-a-png-content", "image/png")},
    )
    assert up.status_code == 400
    assert up.json().get("err_code") == 51003
