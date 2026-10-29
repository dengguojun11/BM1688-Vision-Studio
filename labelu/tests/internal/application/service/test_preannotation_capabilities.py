import json
from types import SimpleNamespace

import pytest

from labelu.internal.common.db import begin_transaction
from labelu.internal.common import model_access_config
from labelu.internal.domain.models.tagtool_classification_model import (
    TagtoolClassificationModel,
)


_LABEL_MAP = {
    "sceneType": ["urbanArterial"],
    "lighting": ["normalDaylight"],
    "weather": ["sunny"],
    "quality": ["clear"],
    "angle": ["topView"],
    "source": ["cameraCapture"],
}


def _task_config():
    return {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "目标", "value": "target"}]},
            },
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "天气",
                            "value": "weather",
                            "type": "enum",
                            "options": [{"key": "晴", "value": "sunny"}],
                        }
                    ]
                },
            },
        ]
    }


def test_capabilities_hide_legacy_env_and_use_local_compatible_tagtool(
    client, testuser_token_headers, db, monkeypatch
):
    from labelu.internal.domain.models.task import MediaType, Task, TaskStatus

    with begin_transaction(db):
        task = Task(
            name="capabilities",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.CONFIGURED.value,
            config=json.dumps(_task_config()),
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        db.add(
            TagtoolClassificationModel(
                user_id=1,
                display_name="local-compatible",
                category="tags",
                original_filename="tagtool.pt",
                weights_rel_path="tagtool/tagtool.pt",
                label_map_json=json.dumps(_LABEL_MAP),
            )
        )
        task_id = task.id

    monkeypatch.setattr(model_access_config, "list_records", lambda: [])
    monkeypatch.setattr(model_access_config, "resolve_model_integration", lambda key: None)
    response = client.get(
        f"/api/v1/tasks/{task_id}/preannotation-capabilities",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200
    items = {item["key"]: item for item in response.json()["data"]["items"]}
    assert items["ai_global_tag"]["visible"] is False
    assert items["tagtool"]["visible"] is True
    assert items["tagtool"]["model_count"] == 1
    assert items["yolo"]["visible"] is False
    assert items["json_upload"]["visible"] is True


def test_capabilities_require_tested_admin_provider_and_use_yolo_intersection(
    client, testuser_token_headers, db, monkeypatch
):
    from labelu.internal.application.service import yolo_preannotate
    from labelu.internal.domain.models.task import MediaType, Task, TaskStatus

    with begin_transaction(db):
        task = Task(
            name="capabilities-yolo",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.CONFIGURED.value,
            config=json.dumps(_task_config()),
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        task_id = task.id

    tested = {
        "id": "vision-ready",
        "provider_key": "vision_llm",
        "enabled": True,
        "last_check_status": "PASSED",
        "endpoint": "http://127.0.0.1:18081",
        "model": "local-vision",
    }
    monkeypatch.setattr(model_access_config, "list_records", lambda: [tested])
    monkeypatch.setattr(
        model_access_config,
        "resolve_model_integration",
        lambda key: tested if key == "vision_llm" else None,
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "list_models_for_task",
        lambda **kwargs: SimpleNamespace(
            items=[
                SimpleNamespace(
                    dict=lambda: {
                        "kind": "trained",
                        "id": 7,
                        "matched_labels": ["target"],
                        "missing_task_labels": [],
                        "extra_model_labels": [],
                    }
                )
            ]
        ),
    )

    response = client.get(
        f"/api/v1/tasks/{task_id}/preannotation-capabilities",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200
    items = {item["key"]: item for item in response.json()["data"]["items"]}
    assert items["ai_global_tag"]["visible"] is True
    assert items["ai_global_tag"]["config_id"] == "vision-ready"
    assert items["yolo"]["available"] is True
    assert items["yolo"]["model_count"] == 1
    # A vision_llm record does not implicitly expose TagTool at the same URL.
    assert items["tagtool"]["visible"] is False


def test_capabilities_hide_tagtool_when_it_reuses_vision_endpoint(
    client, testuser_token_headers, db, monkeypatch
):
    from labelu.internal.domain.models.task import MediaType, Task, TaskStatus

    with begin_transaction(db):
        task = Task(
            name="capabilities-duplicate-provider",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.CONFIGURED.value,
            config=json.dumps(_task_config()),
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        task_id = task.id

    vision = {
        "id": "vision-ready",
        "provider_key": "vision_llm",
        "enabled": True,
        "last_check_status": "PASSED",
        "endpoint": "http://127.0.0.1:18081/v1/",
        "model": "local-vision",
    }
    tagtool = {
        "id": "tagtool-alias",
        "provider_key": "tagtool",
        "enabled": True,
        "last_check_status": "PASSED",
        "endpoint": "http://127.0.0.1:18081/v1",
        "model": "local-vision",
    }
    monkeypatch.setattr(model_access_config, "list_records", lambda: [vision, tagtool])
    monkeypatch.setattr(
        model_access_config,
        "resolve_model_integration",
        lambda key: vision if key == "vision_llm" else tagtool if key == "tagtool" else None,
    )

    response = client.get(
        f"/api/v1/tasks/{task_id}/preannotation-capabilities",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200
    items = {item["key"]: item for item in response.json()["data"]["items"]}
    assert items["ai_global_tag"]["visible"] is True
    assert items["tagtool"]["visible"] is False
    assert items["tagtool"]["reason"] == "no_compatible_model"


def _create_visual_task(db):
    from labelu.internal.domain.models.task import MediaType, Task, TaskStatus

    with begin_transaction(db):
        task = Task(
            name="capabilities-coding-plan",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.CONFIGURED.value,
            config=json.dumps(_task_config()),
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        return task.id


def _install_records(monkeypatch, records):
    monkeypatch.setattr(model_access_config, "list_records", lambda: records)
    monkeypatch.setattr(
        model_access_config,
        "resolve_model_integration",
        lambda key: next(
            (
                row
                for row in records
                if row["provider_key"] == key
                and row.get("enabled", True)
                and row.get("last_check_status") == "PASSED"
            ),
            None,
        ),
    )


def _coding_record(**overrides):
    return {
        "id": "coding-ready",
        "provider_key": "volcengine",
        "enabled": True,
        "last_check_status": "PASSED",
        "endpoint": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "model": "doubao-vision",
        **overrides,
    }


@pytest.mark.parametrize("include_legacy", [False, True])
def test_capabilities_use_managed_coding_plan_before_legacy_models(
    client, testuser_token_headers, db, monkeypatch, include_legacy
):
    task_id = _create_visual_task(db)
    coding = _coding_record()
    records = [coding]
    if include_legacy:
        records.append(
            {
                "id": "retired-tagtool",
                "provider_key": "tagtool",
                "enabled": True,
                "last_check_status": "PASSED",
                "endpoint": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "model": "qwen-vl",
            }
        )
    _install_records(monkeypatch, records)

    response = client.get(
        f"/api/v1/tasks/{task_id}/preannotation-capabilities",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200
    items = {item["key"]: item for item in response.json()["data"]["items"]}
    assert items["ai_global_tag"]["visible"] is True
    assert items["ai_global_tag"]["available"] is True
    assert items["ai_global_tag"]["config_id"] == "coding-ready"
    assert items["ai_global_tag"]["endpoint"] == coding["endpoint"]
    assert items["tagtool"]["visible"] is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"enabled": False},
        {"last_check_status": "FAILED"},
        {"last_check_status": "NOT_CHECKED"},
    ],
)
def test_capabilities_unavailable_coding_plan_blocks_legacy_visual_fallback(
    client, testuser_token_headers, db, monkeypatch, overrides
):
    task_id = _create_visual_task(db)
    _install_records(
        monkeypatch,
        [
            _coding_record(**overrides),
            {
                "id": "old-vision",
                "provider_key": "vision_llm",
                "enabled": True,
                "last_check_status": "PASSED",
                "endpoint": "http://local.test/v1",
                "model": "local-vision",
            },
        ],
    )

    response = client.get(
        f"/api/v1/tasks/{task_id}/preannotation-capabilities",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200
    item = next(
        item
        for item in response.json()["data"]["items"]
        if item["key"] == "ai_global_tag"
    )
    assert item["visible"] is False
    assert item["available"] is False
    assert item["reason"] == "provider_not_ready"


def test_capabilities_hide_retired_qwen_even_if_old_check_passed(
    client, testuser_token_headers, db, monkeypatch
):
    task_id = _create_visual_task(db)
    _install_records(
        monkeypatch,
        [
            {
                "id": "old-qwen",
                "provider_key": "tagtool",
                "enabled": True,
                "last_check_status": "PASSED",
                "endpoint": "https://proxy.test/v1",
                "model": "qwen-vl",
            }
        ],
    )

    response = client.get(
        f"/api/v1/tasks/{task_id}/preannotation-capabilities",
        headers=testuser_token_headers,
    )

    assert response.status_code == 200
    items = {item["key"]: item for item in response.json()["data"]["items"]}
    assert items["ai_global_tag"]["visible"] is False
    assert items["ai_global_tag"]["available"] is False
    assert items["tagtool"]["visible"] is False
