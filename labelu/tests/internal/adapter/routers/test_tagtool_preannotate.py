import json
import sys

import pytest
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.tagtool_classification_model import (
    TagtoolClassificationModel,
)


LABEL_MAP = {
    "sceneType": ["urbanArterial"],
    "lighting": ["normalDaylight"],
    "weather": ["sunny"],
    "quality": ["clear"],
    "angle": ["topView"],
    "source": ["cameraCapture"],
}


def test_list_tagtool_classification_models(client, testuser_token_headers, db):
    with begin_transaction(db):
        db.add(
            TagtoolClassificationModel(
                user_id=2,
                display_name="TagTool ResNet18 20260515 best",
                category="全局标签 / ResNet18",
                original_filename="best.pt",
                weights_rel_path="tagtool_cls/imported/user-1/best.pt",
                label_map_json=json.dumps(LABEL_MAP),
                file_size=123,
            )
        )

    resp = client.get(
        "/api/v1/tagtool-classification-models",
        headers=testuser_token_headers,
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data[0]["display_name"] == "TagTool ResNet18 20260515 best"
    assert data[0]["label_heads"] == list(LABEL_MAP.keys())


def test_list_tagtool_classification_models_supports_unified_search(
    client, testuser_token_headers, db
):
    with begin_transaction(db):
        db.add(
            TagtoolClassificationModel(
                user_id=2,
                display_name="Searchable TagTool model",
                category="global-search",
                original_filename="searchable.pt",
                weights_rel_path="tagtool_cls/imported/user-1/searchable.pt",
                label_map_json=json.dumps(LABEL_MAP),
                file_size=123,
            )
        )

    for keyword in ("Searchable", "global-search", "searchable.pt"):
        response = client.get(
            "/api/v1/tagtool-classification-models",
            params={"q": keyword, "page": 0, "size": 20},
            headers=testuser_token_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["meta_data"]["total"] == 1

    wildcard = client.get(
        "/api/v1/tagtool-classification-models",
        params={"q": "%", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )
    assert wildcard.status_code == 200, wildcard.text
    assert wildcard.json()["meta_data"]["total"] == 0


def test_task_tagtool_preannotate_models(client, testuser_token_headers, db):
    from labelu.internal.domain.models.task import MediaType, Task, TaskStatus

    cfg = {
        "tools": [
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
            }
        ]
    }
    with begin_transaction(db):
        task = Task(
            name="demo",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.CONFIGURED.value,
            config=json.dumps(cfg),
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        db.add(
            TagtoolClassificationModel(
                user_id=2,
                display_name="TagTool ResNet18",
                category="全局标签",
                original_filename="best.pt",
                weights_rel_path="tagtool_cls/imported/user-1/best.pt",
                label_map_json=json.dumps(LABEL_MAP),
                file_size=123,
            )
        )

    resp = client.get(
        f"/api/v1/tasks/{task.id}/tagtool_preannotate/models",
        headers=testuser_token_headers,
    )

    assert resp.status_code == 200
    items = resp.json()["data"]["items"]
    assert items[0]["kind"] == "imported"
    assert items[0]["label_heads"] == list(LABEL_MAP.keys())


def test_task_tagtool_preannotate_models_support_search_and_pagination(
    client, testuser_token_headers, db
):
    from labelu.internal.domain.models.task import MediaType, Task, TaskStatus

    cfg = {
        "tools": [
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
            }
        ]
    }
    with begin_transaction(db):
        task = Task(
            name="tagtool-search-task",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.CONFIGURED.value,
            config=json.dumps(cfg),
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        db.add_all(
            [
                TagtoolClassificationModel(
                    user_id=2,
                    display_name="needle-tag-model",
                    category="global-search",
                    original_filename="needle.pt",
                    weights_rel_path="tagtool_cls/imported/user-1/needle.pt",
                    label_map_json=json.dumps(LABEL_MAP),
                    file_size=123,
                ),
                TagtoolClassificationModel(
                    user_id=2,
                    display_name="other-tag-model",
                    category="other",
                    original_filename="other.pt",
                    weights_rel_path="tagtool_cls/imported/user-1/other.pt",
                    label_map_json=json.dumps(LABEL_MAP),
                    file_size=123,
                ),
            ]
        )

    response = client.get(
        f"/api/v1/tasks/{task.id}/tagtool_preannotate/models",
        params={"q": "needle", "page": 0, "size": 1},
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["total"] == 1
    assert payload["page"] == 0
    assert payload["size"] == 1
    assert payload["items"][0]["title"] == "needle-tag-model"


def test_task_tagtool_preannotate_jobs_support_search(
    client, testuser_token_headers, db
):
    from labelu.internal.domain.models.tagtool_preannotate_job import (
        TagtoolPreannotateJob,
    )
    from labelu.internal.domain.models.task import MediaType, Task, TaskStatus

    with begin_transaction(db):
        task = Task(
            name="tagtool-job-search-task",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.CONFIGURED.value,
            config=json.dumps({"tools": []}),
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        db.add_all(
            [
                TagtoolPreannotateJob(
                    task_id=task.id,
                    created_by=1,
                    status="FAILED",
                    model_kind="imported",
                    model_id=901,
                    error_message="needle failure",
                ),
                TagtoolPreannotateJob(
                    task_id=task.id,
                    created_by=1,
                    status="COMPLETED",
                    model_kind="imported",
                    model_id=902,
                ),
            ]
        )

    response = client.get(
        f"/api/v1/tasks/{task.id}/tagtool-preannotate-jobs",
        params={"q": "needle", "page": 0, "size": 20},
        headers=testuser_token_headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["meta_data"] == {"total": 1, "page": 0, "size": 1}
    assert payload["data"][0]["model_id"] == 901


def test_create_tagtool_preannotate_job_reports_missing_runtime_deps(
    client, testuser_token_headers, db, monkeypatch
):
    from labelu.internal.domain.models.attachment import TaskAttachment
    from labelu.internal.domain.models.sample import SampleState, TaskSample
    from labelu.internal.domain.models.task import MediaType, Task, TaskStatus

    cfg = {
        "tools": [
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
            }
        ]
    }
    with begin_transaction(db):
        task = Task(
            name="demo",
            media_type=MediaType.IMAGE.value,
            status=TaskStatus.CONFIGURED.value,
            config=json.dumps(cfg),
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        attachment = TaskAttachment(
            filename="demo.jpg",
            path="/demo.jpg",
            created_by=1,
            updated_by=1,
        )
        db.add(attachment)
        db.flush()
        sample = TaskSample(
            task_id=task.id,
            file_id=attachment.id,
            state=SampleState.NEW.value,
        )
        db.add(sample)
        model = TagtoolClassificationModel(
            user_id=1,
            display_name="TagTool ResNet18",
            category="全局标签",
            original_filename="best.pt",
            weights_rel_path="tagtool_cls/imported/user-1/best.pt",
            label_map_json=json.dumps(LABEL_MAP),
            file_size=123,
        )
        db.add(model)
        db.flush()
        task_id = task.id
        model_id = model.id

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "torchvision":
            raise ImportError("No module named 'torchvision'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    sys.modules.pop("torchvision", None)

    resp = client.post(
        f"/api/v1/tasks/{task_id}/tagtool_preannotate_job",
        headers=testuser_token_headers,
        json={
            "model_kind": "imported",
            "model_id": model_id,
            "sample_ids": [sample.id],
        },
    )

    assert resp.status_code == 501
    assert "torch 和 torchvision" in resp.json()["msg"]
