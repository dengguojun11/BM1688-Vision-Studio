from types import SimpleNamespace

from labelu.internal.application.service.auto_label import (
    _build_inference_payload,
    _normalize_results,
)


def _task_config():
    return {
        "tools": [
            {
                "tool": "rectTool",
                "config": {
                    "attributes": [
                        {"key": "旧类别", "value": "legacy_label"},
                    ]
                },
            },
            {
                "tool": "polygonTool",
                "config": {"attributes": []},
            },
        ]
    }


def _binding():
    return {
        "id": 7,
        "contract_revision": 2,
        "label_set_signature": "a" * 64,
        "class_order_signature": "b" * 64,
        "prompt_sha256": "c" * 64,
        "prompt_spec": {
            "operation": "bbox_detection",
            "labels": [
                {
                    "label_value": "current_label",
                    "label_key": "当前类别",
                }
            ],
            "constraints": {"allowed_tools": ["rectTool"]},
        },
    }


def test_contract_prompt_is_authoritative_during_result_normalization():
    normalized = _normalize_results(
        {
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "当前类别",
                    "result": {"x": 1, "y": 2, "width": 10, "height": 20},
                },
                {
                    "toolName": "rectTool",
                    "label": "legacy_label",
                    "result": {"x": 1, "y": 2, "width": 10, "height": 20},
                },
                {
                    "toolName": "polygonTool",
                    "label": "current_label",
                    "result": {
                        "points": [
                            {"x": 1, "y": 1},
                            {"x": 2, "y": 1},
                            {"x": 2, "y": 2},
                        ]
                    },
                },
            ]
        },
        "sample.jpg",
        _task_config(),
        include_geometry_tools=True,
        filter_by_labels=False,
        contract_prompt=_binding(),
    )

    results = normalized["annotations"]["rectTool"]["result"]
    assert [item["label"] for item in results] == ["current_label"]
    assert "polygonTool" not in normalized["annotations"]


def test_contract_prompt_can_supply_labels_when_legacy_task_config_is_empty():
    sample = SimpleNamespace(file=SimpleNamespace(path="/image.jpg"))
    task = SimpleNamespace(id=1, name="契约检测")
    payload = _build_inference_payload(
        sample,
        task,
        {},
        filter_by_labels=True,
        include_geometry_tools=True,
        prompt=None,
        contract_prompt=_binding(),
    )

    assert payload["labels"] == [
        {
            "name": "current_label",
            "display_name": "当前类别",
            "tool": "rectTool",
            "description": None,
            "attributes": [],
        }
    ]
    assert payload["constraints"]["allowed_tools"] == ["rectTool"]


def test_legacy_normalization_keeps_task_config_compatibility():
    normalized = _normalize_results(
        {
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "legacy_label",
                    "result": {"x": 1, "y": 2, "width": 10, "height": 20},
                }
            ]
        },
        "sample.jpg",
        _task_config(),
        include_geometry_tools=True,
        filter_by_labels=True,
    )

    assert normalized["annotations"]["rectTool"]["result"][0]["label"] == "legacy_label"
