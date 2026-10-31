import json
import hashlib

from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.label_contract import (
    class_order_signature,
    label_set_signature,
)
from labelu.internal.common.system_dataset_category import (
    CONSISTENT_POOL_CATEGORY_DESCRIPTION,
    CONSISTENT_POOL_CATEGORY_KEY,
    CONSISTENT_POOL_CATEGORY_NAME,
    CONSISTENT_POOL_CATEGORY_SORT_ORDER,
)
from labelu.internal.application.service.annotation_contract import create_contract
from labelu.internal.application.service.annotation_prompt import (
    resolve_published_prompt_for_task,
)
from labelu.internal.domain.models.annotation_contract import TaskAnnotationContract
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel, DatasetCategory
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.user import User


def _create_label(client, headers, *, key: str, value: str, color: str | None = None, attributes=None):
    if color is None:
        color = "#" + hashlib.sha256(value.encode()).hexdigest()[:6]
    return client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=headers,
        json={
            "label_key": key,
            "label_value": value,
            "annotation_kind": "bbox",
            "color": color,
            "attributes": attributes or [],
        },
    )


def test_taxonomy_write_requires_admin_and_enforces_normalized_uniqueness(
    client, testuser_token_headers, testadmin_token_headers, db
):
    denied = _create_label(
        client,
        testuser_token_headers,
        key="Ordinary user label",
        value="ordinary_user_label",
    )
    assert denied.status_code == 403

    created = _create_label(
        client,
        testadmin_token_headers,
        key="Vehicle",
        value="SystemVehicle",
    )
    assert created.status_code == 201, created.text
    label = created.json()["data"]
    assert label["version"] == 1

    duplicate = _create_label(
        client,
        testadmin_token_headers,
        key="Duplicate",
        value="systemvehicle",
    )
    assert duplicate.status_code == 409, duplicate.text

    updated = client.patch(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels/{label['id']}",
        headers=testadmin_token_headers,
        json={"version": 1, "label_value": "system_vehicle_v2", "color": "#ff0000"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["version"] == 2
    assert updated.json()["data"]["label_value"] == "system_vehicle_v2"
    assert (
        db.query(AnnotationLabel)
        .filter(AnnotationLabel.id == label["id"])
        .one()
        .normalized_label_value
        == "system_vehicle_v2"
    )

    duplicate_value = _create_label(
        client,
        testadmin_token_headers,
        key="Second vehicle",
        value="second_vehicle",
    )
    assert duplicate_value.status_code == 201, duplicate_value.text
    duplicate_update = client.patch(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels/{duplicate_value.json()['data']['id']}",
        headers=testadmin_token_headers,
        json={"version": 1, "label_value": "system_vehicle_v2"},
    )
    assert duplicate_update.status_code == 409, duplicate_update.text

    invalid_update = client.patch(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels/{duplicate_value.json()['data']['id']}",
        headers=testadmin_token_headers,
        json={"version": 1, "label_value": "中文值"},
    )
    assert invalid_update.status_code == 422, invalid_update.text

    stale = client.patch(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels/{label['id']}",
        headers=testadmin_token_headers,
        json={"version": 1, "label_value": "system_vehicle_v3", "color": "#00ff00"},
    )
    assert stale.status_code == 409, stale.text


def test_taxonomy_can_create_and_filter_polygon_labels(
    client, testadmin_token_headers, db
):
    created = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=testadmin_token_headers,
        json={
            "label_key": "车牌定位点",
            "label_value": "platePoint",
            "annotation_kind": "polygon",
            "color": "#123456",
        },
    )
    assert created.status_code == 201, created.text
    payload = created.json()["data"]
    assert payload["annotation_kind"] == "polygon"
    assert (
        db.query(AnnotationLabel)
        .filter(AnnotationLabel.id == payload["id"])
        .one()
        .annotation_kind
        == "polygon"
    )

    listed = client.get(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=testadmin_token_headers,
        params={"annotation_kind": "polygon"},
    )
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == payload["id"] for item in listed.json()["data"])

    invalid = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=testadmin_token_headers,
        json={
            "label_key": "无效几何",
            "label_value": "invalidGeometry",
            "annotation_kind": "line",
            "color": "#654321",
        },
    )
    assert invalid.status_code == 422, invalid.text


def test_label_semantic_update_creates_new_dataset_contract_revision(
    client, testadmin_token_headers, db
):
    created = _create_label(
        client,
        testadmin_token_headers,
        key="行人",
        value="pedestrian",
    )
    assert created.status_code == 201, created.text
    label = created.json()["data"]

    dataset = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={"name": "标签同步数据集", "label_ids": [label["id"]]},
    )
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["data"]["id"]

    # Keep a task bound to the old revision. The taxonomy update must move its
    # current task contract together with the dataset contract.
    dataset_row = db.query(Dataset).filter(Dataset.id == dataset_id).one()
    admin = db.query(User).filter(User.username == "admin@123.com").one()
    task = Task(
        name="标签同步任务",
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.CONFIGURED.value,
        created_by=admin.id,
        updated_by=admin.id,
        dataset_id=dataset_id,
        dataset_label_contract_id=dataset_row.dataset_label_contract_id,
        task_label_scope_mode="FULL",
        config=json.dumps(
            {
                "tools": [
                    {
                        "tool": "rectTool",
                        "config": {
                            "attributes": [
                                {
                                    "labelId": label["id"],
                                    "key": "行人",
                                    "value": "pedestrian",
                                }
                            ]
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
    )
    db.add(task)
    db.flush()
    create_contract(
        db,
        task=task,
        label_ids=[label["id"]],
        created_by=admin.id,
        dataset_label_contract_id=dataset_row.dataset_label_contract_id,
        label_scope_mode="FULL",
    )
    db.commit()

    before = client.get(
        f"{settings.API_V1_STR}/datasets/{dataset_id}/label-contracts/current",
        headers=testadmin_token_headers,
    )
    assert before.status_code == 200, before.text
    before_revision = before.json()["data"]["revision"]
    assert before_revision == 1
    assert before.json()["data"]["labels"][0]["label_value"] == "pedestrian"

    changed = client.patch(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels/{label['id']}",
        headers=testadmin_token_headers,
        json={"version": label["version"], "label_value": "person"},
    )
    assert changed.status_code == 200, changed.text

    current = client.get(
        f"{settings.API_V1_STR}/datasets/{dataset_id}/label-contracts/current",
        headers=testadmin_token_headers,
    )
    assert current.status_code == 200, current.text
    current_payload = current.json()["data"]
    assert current_payload["revision"] == 2
    assert current_payload["labels"][0]["label_key"] == "行人"
    assert current_payload["labels"][0]["label_value"] == "person"

    db.expire_all()
    refreshed_task = db.query(Task).filter(Task.id == task.id).one()
    refreshed_task_contract = (
        db.query(TaskAnnotationContract)
        .filter_by(id=refreshed_task.current_annotation_contract_id)
        .one()
    )
    assert refreshed_task.dataset_label_contract_id == current_payload["id"]
    assert json.loads(refreshed_task_contract.labels_snapshot_json)[0]["label_value"] == "person"

    # Dataset management reads must converge to the same current snapshot;
    # they must not keep exposing the old immutable revision through a stale
    # pointer.
    dataset_view = client.get(
        f"{settings.API_V1_STR}/datasets/{dataset_id}",
        headers=testadmin_token_headers,
    )
    assert dataset_view.status_code == 200, dataset_view.text
    assert dataset_view.json()["data"]["dataset_labels"][0]["label_value"] == "person"
    dataset_list = client.get(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
    )
    assert dataset_list.status_code == 200, dataset_list.text
    listed = next(
        row for row in dataset_list.json()["data"] if row["id"] == dataset_id
    )
    assert listed["dataset_labels"][0]["label_value"] == "person"

    # A cached revision in the prompt UI is transparently redirected to the
    # current revision for generation; historical contract rows remain intact.
    stale_preview = client.post(
        f"{settings.API_V1_STR}/datasets/{dataset_id}/label-contracts/{before_revision}/ai-prompts/preview",
        headers=testadmin_token_headers,
        json={"operation": "bbox_detection", "provider_key": "volcengine"},
    )
    assert stale_preview.status_code == 200, stale_preview.text
    assert stale_preview.json()["data"]["contract_revision"] == 2
    assert "person" in stale_preview.json()["data"]["generated_prompt"]
    assert "pedestrian" not in stale_preview.json()["data"]["generated_prompt"]

    saved = client.post(
        f"{settings.API_V1_STR}/datasets/{dataset_id}/label-contracts/{current_payload['revision']}/ai-prompts",
        headers=testadmin_token_headers,
        json={
            "operation": "bbox_detection",
            "provider_key": "volcengine",
            "effective_prompt": stale_preview.json()["data"]["generated_prompt"],
            "expected_label_set_signature": current_payload["label_set_signature"],
            "expected_class_order_signature": current_payload["class_order_signature"],
        },
    )
    assert saved.status_code == 201, saved.text
    saved_prompt = saved.json()["data"]
    published = client.post(
        f"{settings.API_V1_STR}/datasets/{dataset_id}/label-contracts/{current_payload['revision']}/ai-prompts/{saved_prompt['id']}/publish",
        headers=testadmin_token_headers,
        json={"expected_prompt_sha256": saved_prompt["prompt_sha256"]},
    )
    assert published.status_code == 200, published.text

    db.expire_all()
    runtime_task = db.query(Task).filter(Task.id == task.id).one()
    resolved = resolve_published_prompt_for_task(db, runtime_task)
    assert resolved is not None
    assert "person" in resolved["effective_prompt"]
    assert "pedestrian" not in resolved["effective_prompt"]

    preview = client.post(
        f"{settings.API_V1_STR}/datasets/{dataset_id}/label-contracts/{current_payload['revision']}/ai-prompts/preview",
        headers=testadmin_token_headers,
        json={"operation": "bbox_detection", "provider_key": "volcengine"},
    )
    assert preview.status_code == 200, preview.text
    generated_prompt = preview.json()["data"]["generated_prompt"]
    assert "person" in generated_prompt
    assert "pedestrian" not in generated_prompt

    history = client.get(
        f"{settings.API_V1_STR}/datasets/{dataset_id}/label-contracts",
        headers=testadmin_token_headers,
    )
    assert history.status_code == 200, history.text
    history_payload = history.json()["data"]
    assert [item["revision"] for item in history_payload] == [2, 1]
    assert history_payload[1]["labels"][0]["label_value"] == "pedestrian"


def test_one_canonical_label_can_be_reused_by_multiple_presets(
    client, testadmin_token_headers, db
):
    created = _create_label(
        client,
        testadmin_token_headers,
        key="Shared person",
        value="shared_person",
    )
    assert created.status_code == 201, created.text
    label_id = created.json()["data"]["id"]

    preset_ids = []
    for index in (1, 2):
        response = client.post(
            f"{settings.API_V1_STR}/annotation-taxonomy/label-presets",
            headers=testadmin_token_headers,
            json={
                "slug": f"sharedPreset{index}",
                "name": f"Shared preset {index}",
                "label_ids": [label_id],
            },
        )
        assert response.status_code == 201, response.text
        payload = response.json()["data"]
        preset_ids.append(payload["id"])
        assert payload["label_ids"] == [label_id]
        assert payload["items"][0]["label_id"] == label_id

    assert preset_ids[0] != preset_ids[1]
    assert (
        db.query(AnnotationLabel)
        .filter(AnnotationLabel.normalized_label_value == "shared_person")
        .count()
        == 1
    )


def test_root_dataset_category_api_is_independent_from_labels(
    client, testadmin_token_headers
):
    created = client.post(
        f"{settings.API_V1_STR}/dataset-categories",
        headers=testadmin_token_headers,
        json={
            "category_key": "road_safety",
            "name": "Road safety",
            "description": "Independent dataset grouping",
        },
    )
    assert created.status_code == 201, created.text
    category = created.json()["data"]
    assert category["reference_count"] == 0

    updated = client.patch(
        f"{settings.API_V1_STR}/dataset-categories/{category['id']}",
        headers=testadmin_token_headers,
        json={"version": category["version"], "name": "Road operations"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["name"] == "Road operations"

    listed = client.get(
        f"{settings.API_V1_STR}/dataset-categories",
        headers=testadmin_token_headers,
    )
    assert listed.status_code == 200, listed.text
    assert any(row["category_key"] == "road_safety" for row in listed.json()["data"])


def test_consistent_pool_category_is_admin_only_and_system_managed(
    client, testuser_token_headers, testadmin_token_headers, db
):
    with begin_transaction(db):
        admin = db.query(User).filter(User.username == "admin@123.com").one()
        category = DatasetCategory(
            category_key=CONSISTENT_POOL_CATEGORY_KEY,
            name=CONSISTENT_POOL_CATEGORY_NAME,
            description=CONSISTENT_POOL_CATEGORY_DESCRIPTION,
            sort_order=CONSISTENT_POOL_CATEGORY_SORT_ORDER,
            status="active",
            version=1,
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add(category)
        db.flush()
        category_id = int(category.id)

    admin_list = client.get(
        f"{settings.API_V1_STR}/dataset-categories",
        headers=testadmin_token_headers,
        params={"q": CONSISTENT_POOL_CATEGORY_KEY},
    )
    assert admin_list.status_code == 200, admin_list.text
    assert admin_list.json()["meta_data"]["total"] == 1
    assert admin_list.json()["data"][0]["is_system"] is True

    regular_list = client.get(
        f"{settings.API_V1_STR}/dataset-categories",
        headers=testuser_token_headers,
        params={"q": CONSISTENT_POOL_CATEGORY_KEY},
    )
    assert regular_list.status_code == 200, regular_list.text
    assert regular_list.json()["meta_data"]["total"] == 0
    assert regular_list.json()["data"] == []

    admin_summary = client.get(
        f"{settings.API_V1_STR}/tasks/dataset-category-summary",
        headers=testadmin_token_headers,
    )
    assert admin_summary.status_code == 200, admin_summary.text
    assert any(
        row["category_key"] == CONSISTENT_POOL_CATEGORY_KEY
        for row in admin_summary.json()["data"]["categories"]
    )

    regular_summary = client.get(
        f"{settings.API_V1_STR}/tasks/dataset-category-summary",
        headers=testuser_token_headers,
    )
    assert regular_summary.status_code == 200, regular_summary.text
    assert all(
        row["category_key"] != CONSISTENT_POOL_CATEGORY_KEY
        for row in regular_summary.json()["data"]["categories"]
    )

    updated = client.patch(
        f"{settings.API_V1_STR}/dataset-categories/{category_id}",
        headers=testadmin_token_headers,
        json={"version": 1, "name": "不可修改"},
    )
    assert updated.status_code == 409, updated.text

    deleted = client.delete(
        f"{settings.API_V1_STR}/dataset-categories/{category_id}",
        headers=testadmin_token_headers,
        params={"version": 1},
    )
    assert deleted.status_code == 409, deleted.text


def test_task_direct_labels_create_revisioned_contract_without_legacy_preset_field(
    client, testuser_token_headers, testadmin_token_headers, db
):
    first = _create_label(
        client,
        testadmin_token_headers,
        key="First",
        value="contract_first",
    ).json()["data"]
    second = _create_label(
        client,
        testadmin_token_headers,
        key="Second",
        value="contract_second",
    ).json()["data"]

    task_response = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "direct label contract", "media_type": "IMAGE"},
    )
    assert task_response.status_code == 201, task_response.text
    task_id = task_response.json()["data"]["id"]

    applied = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation/apply-labels",
        headers=testuser_token_headers,
        json={"label_ids": [second["id"], first["id"]]},
    )
    assert applied.status_code == 200, applied.text
    task = applied.json()["data"]
    assert task["annotation_contract_revision"] == 1
    config = json.loads(task["config"])
    assert "defaultRectPresetId" not in config
    rect = next(item for item in config["tools"] if item["tool"] == "rectTool")
    assert [item["labelId"] for item in rect["config"]["attributes"]] == [
        second["id"],
        first["id"],
    ]

    conflict = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation-contracts",
        headers=testuser_token_headers,
        json={"label_ids": [first["id"]], "expected_contract_revision": 99},
    )
    assert conflict.status_code == 409, conflict.text

    with begin_transaction(db):
        db.add(
            TaskSample(
                task_id=task_id,
                inner_id=1,
                created_by=1,
                state=SampleState.DONE.value,
                data=json.dumps({"result": "{}"}),
            )
        )

    revised = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation-contracts",
        headers=testuser_token_headers,
        json={
            "label_ids": [first["id"]],
            "expected_contract_revision": 1,
        },
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["data"]["revision"] == 2
    assert revised.json()["data"]["status"] == "review_required"

    history = client.get(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation-contracts",
        headers=testuser_token_headers,
    )
    assert [row["revision"] for row in history.json()["data"]] == [2, 1]


def test_task_direct_labels_follow_polygon_tool_when_rect_tool_is_absent(
    client, testuser_token_headers, testadmin_token_headers
):
    label = _create_label(
        client,
        testadmin_token_headers,
        key="Polygon label",
        value="polygon_label",
    ).json()["data"]
    task = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "polygon label contract", "media_type": "IMAGE"},
    ).json()["data"]
    polygon_config = {
        "tools": [
            {
                "tool": "polygonTool",
                "config": {"lineType": 0, "attributes": []},
            }
        ]
    }
    persisted_rect_config = {
        "tools": [{"tool": "rectTool", "config": {"attributes": []}}]
    }
    configured = client.patch(
        f"{settings.API_V1_STR}/tasks/{task['id']}",
        headers=testuser_token_headers,
        json={"config": json.dumps(persisted_rect_config)},
    )
    assert configured.status_code == 200, configured.text

    applied = client.post(
        f"{settings.API_V1_STR}/tasks/{task['id']}/annotation/apply-labels",
        headers=testuser_token_headers,
        json={"label_ids": [label["id"]], "config": json.dumps(polygon_config)},
    )
    assert applied.status_code == 200, applied.text
    config = json.loads(applied.json()["data"]["config"])
    assert [tool["tool"] for tool in config["tools"]] == ["polygonTool"]
    assert config["tools"][0]["config"]["lineType"] == 0
    assert config["tools"][0]["config"]["attributes"][0]["labelId"] == label["id"]

    loaded = client.get(
        f"{settings.API_V1_STR}/tasks/{task['id']}",
        headers=testuser_token_headers,
    )
    assert loaded.status_code == 200, loaded.text
    loaded_config = json.loads(loaded.json()["data"]["config"])
    geometry_tools = [
        tool
        for tool in loaded_config["tools"]
        if tool["tool"] in {"rectTool", "polygonTool", "pointTool", "lineTool", "cuboidTool"}
    ]
    assert [tool["tool"] for tool in geometry_tools] == ["polygonTool"]
    assert geometry_tools[0]["config"]["attributes"][0]["labelId"] == label["id"]


def test_task_direct_labels_target_active_polygon_when_rect_tool_also_exists(
    client, testuser_token_headers, testadmin_token_headers
):
    rect_label = _create_label(
        client,
        testadmin_token_headers,
        key="Rectangle label",
        value="rectangle_label",
    ).json()["data"]
    polygon_label = _create_label(
        client,
        testadmin_token_headers,
        key="Active polygon label",
        value="active_polygon_label",
    ).json()["data"]
    task = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "active polygon label contract", "media_type": "IMAGE"},
    ).json()["data"]
    config = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {
                    "attributes": [
                        {
                            "key": rect_label["label_key"],
                            "value": rect_label["label_value"],
                            "color": rect_label["color"],
                            "labelId": rect_label["id"],
                        }
                    ]
                },
            },
            {
                "tool": "polygonTool",
                "config": {"lineType": 0, "attributes": []},
            },
        ]
    }

    applied = client.post(
        f"{settings.API_V1_STR}/tasks/{task['id']}/annotation/apply-labels",
        headers=testuser_token_headers,
        json={
            "label_ids": [polygon_label["id"]],
            "config": json.dumps(config),
            "tool_name": "polygonTool",
        },
    )
    assert applied.status_code == 200, applied.text
    applied_config = json.loads(applied.json()["data"]["config"])
    rect_tool = next(tool for tool in applied_config["tools"] if tool["tool"] == "rectTool")
    polygon_tool = next(
        tool for tool in applied_config["tools"] if tool["tool"] == "polygonTool"
    )
    assert [item["labelId"] for item in rect_tool["config"]["attributes"]] == [
        rect_label["id"]
    ]
    assert [item["labelId"] for item in polygon_tool["config"]["attributes"]] == [
        polygon_label["id"]
    ]

    contract = client.get(
        f"{settings.API_V1_STR}/tasks/{task['id']}/annotation-contract",
        headers=testuser_token_headers,
    )
    assert contract.status_code == 200, contract.text
    assert contract.json()["data"]["label_ids"] == [
        rect_label["id"],
        polygon_label["id"],
    ]


def test_dual_signatures_distinguish_set_semantics_from_class_order():
    assert label_set_signature([9, 3, 9]) == label_set_signature([3, 9])
    assert class_order_signature(["a", "b"]) != class_order_signature(["b", "a"])


def test_runtime_template_requires_model_set_when_active_and_supports_versioned_update(
    client, testadmin_token_headers
):
    active_without_model = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/runtime-templates",
        headers=testadmin_token_headers,
        json={
            "template_key": "runtime_contract_invalid",
            "name": "Invalid active runtime",
            "environment_id": "env-test",
            "runtime_profile": "yolov11_detection",
            "interface_version": 1,
            "status": "active",
        },
    )
    assert active_without_model.status_code == 422, active_without_model.text

    created = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/runtime-templates",
        headers=testadmin_token_headers,
        json={
            "template_key": "runtime_contract_update",
            "name": "Runtime draft",
            "environment_id": "env-test",
            "runtime_profile": "yolov11_detection",
            "interface_version": 1,
            "status": "disabled",
        },
    )
    assert created.status_code == 201, created.text
    row = created.json()["data"]

    payload = {
        "version": row["version"],
        "template_key": row["template_key"],
        "name": "Runtime ready",
        "environment_id": row["environment_id"],
        "runtime_profile": row["runtime_profile"],
        "interface_version": row["interface_version"],
        "aicm_model_set_id": "901",
        "expected_model_alg_type": "yoloV11",
        "capability_contract": {"supported_job_types": ["yolo_detection"]},
        "is_default": True,
        "status": "active",
    }
    updated = client.patch(
        f"{settings.API_V1_STR}/annotation-taxonomy/runtime-templates/{row['id']}",
        headers=testadmin_token_headers,
        json=payload,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["aicm_model_set_id"] == "901"
    assert updated.json()["data"]["version"] == row["version"] + 1

    stale = client.patch(
        f"{settings.API_V1_STR}/annotation-taxonomy/runtime-templates/{row['id']}",
        headers=testadmin_token_headers,
        json=payload,
    )
    assert stale.status_code == 409, stale.text


def test_label_list_supports_server_pagination_search_and_filters(
    client, testadmin_token_headers
):
    for index in range(5):
        created = _create_label(
            client,
            testadmin_token_headers,
            key=f"分页标签 {index}",
            value=f"paged_label_{index}",
        )
        assert created.status_code == 201, created.text

    listed = client.get(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=testadmin_token_headers,
        params={"page": 1, "size": 2, "q": "paged_label", "status": "active"},
    )
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert payload["meta_data"] == {"total": 5, "page": 1, "size": 2}
    assert [row["label_value"] for row in payload["data"]] == [
        "paged_label_2",
        "paged_label_3",
    ]


def test_label_reference_count_counts_distinct_contracts(
    client, testadmin_token_headers
):
    """A label contributes once per contract, even across contract revisions."""
    first = _create_label(
        client,
        testadmin_token_headers,
        key="契约引用一",
        value="contract_reference_one",
    )
    second = _create_label(
        client,
        testadmin_token_headers,
        key="契约引用二",
        value="contract_reference_two",
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_id = first.json()["data"]["id"]
    second_id = second.json()["data"]["id"]

    dataset = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={"name": "契约引用数据集", "label_ids": [first_id]},
    )
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["data"]["id"]
    dataset_revision = client.post(
        f"{settings.API_V1_STR}/datasets/{dataset_id}/label-contracts",
        headers=testadmin_token_headers,
        json={"label_ids": [first_id, second_id]},
    )
    assert dataset_revision.status_code == 201, dataset_revision.text

    task = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testadmin_token_headers,
        json={
            "name": "契约引用任务",
            "media_type": "IMAGE",
            "dataset_id": dataset_id,
        },
    )
    assert task.status_code == 201, task.text
    task_id = task.json()["data"]["id"]
    task_contract = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation/apply-labels",
        headers=testadmin_token_headers,
        json={"label_ids": [first_id], "scope_mode": "SUBSET"},
    )
    assert task_contract.status_code == 200, task_contract.text
    task_revision = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation/apply-labels",
        headers=testadmin_token_headers,
        json={"label_ids": [first_id, second_id], "scope_mode": "FULL"},
    )
    assert task_revision.status_code == 200, task_revision.text

    listed = client.get(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=testadmin_token_headers,
        params={"q": "contract_reference"},
    )
    assert listed.status_code == 200, listed.text
    counts = {
        row["label_value"]: row["reference_count"] for row in listed.json()["data"]
    }
    # The first label is in two dataset contracts.  Both task revisions point
    # at the current dataset contract, so they do not add two extra counts.
    # The second label is only in the current dataset contract.
    assert counts == {"contract_reference_one": 2, "contract_reference_two": 1}


def test_label_color_is_globally_unique(client, testadmin_token_headers):
    first = _create_label(
        client,
        testadmin_token_headers,
        key="颜色一",
        value="unique_color_one",
        color="#Ab12Cd",
    )
    assert first.status_code == 201, first.text
    assert first.json()["data"]["color"] == "#ab12cd"

    duplicate = _create_label(
        client,
        testadmin_token_headers,
        key="颜色二",
        value="unique_color_two",
        color="#AB12CD",
    )
    assert duplicate.status_code == 409, duplicate.text


def test_label_color_is_auto_assigned_when_omitted(client, testadmin_token_headers):
    payload = {
        "label_key": "自动颜色一",
        "label_value": "auto_color_one",
        "annotation_kind": "bbox",
        "attributes": [],
    }
    first = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=testadmin_token_headers,
        json=payload,
    )
    payload["label_key"] = "自动颜色二"
    payload["label_value"] = "auto_color_two"
    second = client.post(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels",
        headers=testadmin_token_headers,
        json=payload,
    )
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    first_color = first.json()["data"]["color"]
    second_color = second.json()["data"]["color"]
    assert first_color.startswith("#") and len(first_color) == 7
    assert second_color.startswith("#") and len(second_color) == 7
    assert first_color != second_color


def test_label_attributes_are_validated_and_frozen_into_task_contract(
    client, testuser_token_headers, testadmin_token_headers
):
    attributes = [
        {
            "key": "车牌号",
            "value": "plateNumber",
            "type": "string",
            "required": True,
            "maxLength": 20,
            "stringType": "text",
            "defaultValue": "",
            "regexp": "",
        },
        {
            "key": "车牌颜色",
            "value": "plateColor",
            "type": "enum",
            "required": True,
            "options": [
                {"key": "蓝色", "value": "blue", "isDefault": True},
                {"key": "绿色", "value": "green", "isDefault": False},
            ],
        },
    ]
    created = _create_label(
        client,
        testadmin_token_headers,
        key="属性车牌",
        value="attribute_plate",
        attributes=attributes,
    )
    assert created.status_code == 201, created.text
    label = created.json()["data"]
    assert label["attributes"] == attributes

    task_response = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "attribute contract", "media_type": "IMAGE"},
    )
    task_id = task_response.json()["data"]["id"]
    applied = client.post(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation/apply-labels",
        headers=testuser_token_headers,
        json={"label_ids": [label["id"]]},
    )
    assert applied.status_code == 200, applied.text
    config = json.loads(applied.json()["data"]["config"])
    rect_label = next(
        tool for tool in config["tools"] if tool["tool"] == "rectTool"
    )["config"]["attributes"][0]
    assert rect_label["attributes"] == attributes

    history = client.get(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation-contracts",
        headers=testuser_token_headers,
    ).json()["data"]
    assert history[0]["labels"][0]["attributes"] == attributes

    changed = client.patch(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels/{label['id']}",
        headers=testadmin_token_headers,
        json={"version": label["version"], "attributes": []},
    )
    assert changed.status_code == 200, changed.text
    unchanged_history = client.get(
        f"{settings.API_V1_STR}/tasks/{task_id}/annotation-contracts",
        headers=testuser_token_headers,
    ).json()["data"]
    assert unchanged_history[0]["labels"][0]["attributes"] == attributes


def test_label_attribute_rejects_duplicate_option_values(
    client, testadmin_token_headers
):
    response = _create_label(
        client,
        testadmin_token_headers,
        key="非法属性",
        value="invalid_attribute_options",
        attributes=[
            {
                "key": "状态",
                "value": "state",
                "type": "enum",
                "options": [
                    {"key": "一", "value": "same"},
                    {"key": "二", "value": "same"},
                ],
            }
        ],
    )
    assert response.status_code == 422, response.text


def test_label_color_update_syncs_task_config_and_contract_snapshot(
    client, testuser_token_headers, testadmin_token_headers
):
    created = _create_label(
        client,
        testadmin_token_headers,
        key="颜色同步",
        value="color_sync_label",
        color="#112233",
    )
    label = created.json()["data"]
    task = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testuser_token_headers,
        json={"name": "color sync task", "media_type": "IMAGE"},
    ).json()["data"]
    applied = client.post(
        f"{settings.API_V1_STR}/tasks/{task['id']}/annotation/apply-labels",
        headers=testuser_token_headers,
        json={"label_ids": [label["id"]]},
    )
    assert applied.status_code == 200, applied.text

    changed = client.patch(
        f"{settings.API_V1_STR}/annotation-taxonomy/labels/{label['id']}",
        headers=testadmin_token_headers,
        json={"version": label["version"], "color": "#445566"},
    )
    assert changed.status_code == 200, changed.text

    task_after = client.get(
        f"{settings.API_V1_STR}/tasks/{task['id']}", headers=testuser_token_headers
    ).json()["data"]
    config = json.loads(task_after["config"])
    rect = next(tool for tool in config["tools"] if tool["tool"] == "rectTool")
    assert rect["config"]["attributes"][0]["color"] == "#445566"
    history = client.get(
        f"{settings.API_V1_STR}/tasks/{task['id']}/annotation-contracts",
        headers=testuser_token_headers,
    ).json()["data"]
    assert history[0]["labels"][0]["color"] == "#445566"


def test_dataset_category_pagination_search_and_delete_guard(
    client, testadmin_token_headers
):
    categories = []
    for index in range(3):
        response = client.post(
            f"{settings.API_V1_STR}/dataset-categories",
            headers=testadmin_token_headers,
            json={"category_key": f"paged_category_{index}", "name": f"Paged {index}"},
        )
        assert response.status_code == 201, response.text
        categories.append(response.json()["data"])
    listed = client.get(
        f"{settings.API_V1_STR}/dataset-categories",
        headers=testadmin_token_headers,
        params={"page": 1, "size": 2, "q": "paged_category"},
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["meta_data"] == {"total": 3, "page": 1, "size": 1}
    empty_page = client.get(
        f"{settings.API_V1_STR}/dataset-categories",
        headers=testadmin_token_headers,
        params={"page": 2, "size": 2, "q": "paged_category"},
    )
    assert empty_page.status_code == 200, empty_page.text
    assert empty_page.json()["data"] == []
    assert empty_page.json()["meta_data"] == {"total": 3, "page": 2, "size": 0}
    deleted = client.delete(
        f"{settings.API_V1_STR}/dataset-categories/{categories[0]['id']}",
        headers=testadmin_token_headers,
        params={"version": categories[0]["version"]},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["status"] == "disabled"
