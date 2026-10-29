"""应用拉框预设后任务 config 中 rectTool 结构与前端 Kit 对齐。"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.plate_recognition_profile import (
    DEFAULT_PLATE_RECOGNITION_CONFIG,
    PLATE_RECOGNITION_PROFILE,
    normalize_plate_recognition_task_config,
)
from labelu.internal.domain.models.annotation_taxonomy import (
    GlobalTagDimension,
    GlobalTagOption,
    LabelPreset,
    LabelPresetItem,
)


def test_apply_label_preset_writes_rect_config(
    client: TestClient, testuser_token_headers: dict, db: Session
) -> None:
    with begin_transaction(db):
        p = LabelPreset(
            slug="pytest_apply_rect",
            name="Pytest 预设",
            description="",
            sort_order=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(p)
        db.flush()
        db.add(
            LabelPresetItem(
                preset_id=p.id,
                label_key="中文类",
                label_value="cls_en",
                color="#E74C3C",
                sort_order=0,
                created_at=datetime.now(),
            )
        )
        dimension = GlobalTagDimension(
            field_key="pytest_scene",
            name_cn="Scene",
            sort_order=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(dimension)
        db.flush()
        db.add(
            GlobalTagOption(
                dimension_id=dimension.id,
                name_cn="Road",
                value_en="road",
                sort_order=0,
                created_at=datetime.now(),
            )
        )
        preset_id = p.id

    tr = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "preset task", "media_type": "IMAGE"},
    )
    assert tr.status_code == 201
    task_id = tr.json()["data"]["id"]

    ar = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation/apply-label-preset",
        headers=testuser_token_headers,
        json={"preset_id": preset_id},
    )
    assert ar.status_code == 200

    applied_cfg = json.loads(ar.json()["data"]["config"])
    tag_tool = next(t for t in applied_cfg["tools"] if t.get("tool") == "tagTool")
    assert tag_tool["config"]["attributes"][0]["value"] == "pytest_scene"
    assert any(
        item.get("presetDbId") == preset_id
        for item in applied_cfg["rectLabelPresets"]
    )

    gr = client.get(
        f"{settings.API_V1_STR}/tasks/{task_id}",
        headers=testuser_token_headers,
    )
    assert gr.status_code == 200
    assert gr.headers.get("cache-control", "").lower().find("no-store") >= 0

    cfg = json.loads(gr.json()["data"]["config"])
    rect = next(t for t in cfg["tools"] if t.get("tool") == "rectTool")
    rc = rect["config"]
    assert rc.get("minHeight") == 1
    assert rc.get("minWidth") == 1
    attrs = rc.get("attributes") or []
    assert len(attrs) == 1
    assert attrs[0]["key"] == "中文类"
    assert attrs[0]["value"] == "cls_en"
    assert attrs[0]["color"] == "#E74C3C"
    assert "defaultRectPresetId" not in cfg
    assert gr.json()["data"]["annotation_contract_revision"] == 1
    assert gr.json()["data"]["current_annotation_contract_id"] is not None


def test_apply_plate_label_preset_is_blocked_for_new_task(
    client: TestClient, testuser_token_headers: dict, db: Session
) -> None:
    with begin_transaction(db):
        preset = LabelPreset(
            slug="plateRecognition",
            name="双层电动车牌识别",
            description="",
            training_profile=PLATE_RECOGNITION_PROFILE,
            training_profile_config=json.dumps(
                DEFAULT_PLATE_RECOGNITION_CONFIG, ensure_ascii=False
            ),
            sort_order=0,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(preset)
        db.flush()
        db.add(
            LabelPresetItem(
                preset_id=preset.id,
                label_key="双层车牌",
                label_value="doubleEP",
                color="#ff6600",
                sort_order=0,
                created_at=datetime.now(),
            )
        )
        preset_id = preset.id

    task_response = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "plate preset task", "media_type": "IMAGE"},
    )
    assert task_response.status_code == 201
    task_id = task_response.json()["data"]["id"]

    apply_response = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation/apply-label-preset",
        headers=testuser_token_headers,
        json={"preset_id": preset_id},
    )
    assert apply_response.status_code == 409
    assert "只支持矩形框" in apply_response.json().get("msg", "")


def test_patch_new_task_cannot_introduce_legacy_plate_polygon_config(
    client: TestClient, testuser_token_headers: dict, db: Session
) -> None:
    task_response = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "new plate rectangle task", "media_type": "IMAGE"},
    )
    assert task_response.status_code == 201
    task_id = task_response.json()["data"]["id"]

    config = normalize_plate_recognition_task_config({})
    patch_response = client.patch(
        f"{settings.API_V1_STR}/tasks/{task_id}",
        headers=testuser_token_headers,
        json={
            "name": "new plate rectangle task",
            "media_type": "IMAGE",
            "config": json.dumps(config, ensure_ascii=False),
        },
    )
    assert patch_response.status_code == 409
    assert "只支持矩形框" in patch_response.json().get("msg", "")
