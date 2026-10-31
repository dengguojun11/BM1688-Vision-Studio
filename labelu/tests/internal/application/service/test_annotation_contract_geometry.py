from types import SimpleNamespace
import json

from labelu.internal.common.config import settings
from labelu.internal.application.service.annotation_contract import (
    contract_annotation_kind,
    materialize_dataset_contract_task_config,
    task_config_from_label_snapshot,
)


def _mixed_snapshot():
    return [
        {
            "label_id": 11,
            "label_key": "车牌框",
            "label_value": "doubleEP",
            "color": "#ff6600",
            "annotation_kind": "bbox",
            "attributes": [],
        },
        {
            "label_id": 12,
            "label_key": "车牌四角点",
            "label_value": "platePoint",
            "color": "#00aaee",
            "annotation_kind": "polygon",
            "attributes": [],
        },
    ]


def test_mixed_snapshot_materializes_rect_and_four_point_polygon_tools():
    config = task_config_from_label_snapshot(_mixed_snapshot())

    assert [tool["tool"] for tool in config["tools"]] == [
        "rectTool",
        "polygonTool",
    ]
    rect = config["tools"][0]["config"]
    polygon = config["tools"][1]["config"]
    assert [item["value"] for item in rect["attributes"]] == ["doubleEP"]
    assert [item["value"] for item in polygon["attributes"]] == ["platePoint"]
    assert polygon["lowerLimitPointNum"] == 4
    assert polygon["upperLimitPointNum"] == 4
    assert config["defaultRectPresetId"] == "plateDetection"
    assert contract_annotation_kind(_mixed_snapshot()) == "bbox"


def test_polygon_only_snapshot_keeps_polygon_contract_kind():
    snapshot = [_mixed_snapshot()[1]]
    config = task_config_from_label_snapshot(snapshot)

    assert [tool["tool"] for tool in config["tools"]] == ["polygonTool"]
    assert contract_annotation_kind(snapshot) == "polygon"


def test_subset_snapshot_removes_geometry_tool_outside_contract():
    base_config = task_config_from_label_snapshot(_mixed_snapshot())
    subset_config = task_config_from_label_snapshot(
        [_mixed_snapshot()[0]],
        base_config=base_config,
    )

    assert [tool["tool"] for tool in subset_config["tools"]] == ["rectTool"]
    assert [item["value"] for item in subset_config["tools"][0]["config"]["attributes"]] == [
        "doubleEP"
    ]
    assert "defaultRectPresetId" not in subset_config


def test_materialize_does_not_overwrite_existing_task_config():
    contract = SimpleNamespace(labels_snapshot_json="[]")
    task = SimpleNamespace(
        config=(
            '{"tools":[{"tool":"rectTool","config":'
            '{"attributes":[{"value":"existing"}]}}]}'
        ),
        media_type="IMAGE",
    )

    assert materialize_dataset_contract_task_config(task, contract) is False
    assert task.config == (
        '{"tools":[{"tool":"rectTool","config":'
        '{"attributes":[{"value":"existing"}]}}]}'
    )


def test_task_created_from_mixed_dataset_contract_gets_both_geometry_tools(
    client, testadmin_token_headers
):
    def create_label(key, value, kind, color):
        response = client.post(
            f"{settings.API_V1_STR}/annotation-taxonomy/labels",
            headers=testadmin_token_headers,
            json={
                "label_key": key,
                "label_value": value,
                "annotation_kind": kind,
                "color": color,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["data"]["id"]

    rect_id = create_label("车牌框", "doubleEP", "bbox", "#ff6600")
    point_id = create_label("车牌四角点", "platePoint", "polygon", "#00aaee")
    dataset = client.post(
        f"{settings.API_V1_STR}/datasets",
        headers=testadmin_token_headers,
        json={"name": "混合几何契约数据集", "label_ids": [rect_id, point_id]},
    )
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["data"]["id"]

    task = client.post(
        f"{settings.API_V1_STR}/tasks",
        headers=testadmin_token_headers,
        json={"name": "混合几何契约任务", "media_type": "IMAGE", "dataset_id": dataset_id},
    )
    assert task.status_code == 201, task.text
    config = json.loads(task.json()["data"]["config"])
    assert [tool["tool"] for tool in config["tools"]] == [
        "rectTool",
        "polygonTool",
    ]
    polygon = config["tools"][1]["config"]
    assert polygon["lowerLimitPointNum"] == 4
    assert polygon["upperLimitPointNum"] == 4

    applied = client.post(
        f"{settings.API_V1_STR}/tasks/{task.json()['data']['id']}/annotation/apply-labels",
        headers=testadmin_token_headers,
        json={
            "label_ids": [rect_id, point_id],
            "scope_mode": "FULL",
        },
    )
    assert applied.status_code == 200, applied.text
    applied_config = json.loads(applied.json()["data"]["config"])
    assert {tool["tool"] for tool in applied_config["tools"]} >= {
        "rectTool",
        "polygonTool",
    }
    applied_polygon = next(
        tool["config"]
        for tool in applied_config["tools"]
        if tool.get("tool") == "polygonTool"
    )
    assert [item["value"] for item in applied_polygon["attributes"]] == [
        "platePoint"
    ]
    assert applied_polygon["lowerLimitPointNum"] == 4
    assert applied_polygon["upperLimitPointNum"] == 4

    contract = client.get(
        f"{settings.API_V1_STR}/tasks/{task.json()['data']['id']}/annotation-contract",
        headers=testadmin_token_headers,
    )
    assert contract.status_code == 200, contract.text
    contract_labels = contract.json()["data"]["labels"]
    assert [item["annotation_kind"] for item in contract_labels] == [
        "bbox",
        "polygon",
    ]
