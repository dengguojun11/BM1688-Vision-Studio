from __future__ import annotations

from labelu.internal.common.plate_recognition_profile import (
    DEFAULT_PLATE_RECOGNITION_CONFIG,
    audit_plate_sample,
    normalize_four_corners,
    normalize_plate_recognition_sample_data,
    normalize_plate_recognition_task_config,
    task_config_matches_profile,
)


def _task_config():
    return {
        "tools": [
            {
                "tool": "rectTool",
                "config": {
                    "attributes": [
                        {
                            "key": "双层车牌",
                            "value": "doubleEP",
                            "attributes": [
                                {"key": "车牌号码", "value": "plateNumber"},
                                {"key": "车牌颜色", "value": "color"},
                                {"key": "车牌类型", "value": " plateType"},
                            ],
                        }
                    ]
                },
            },
            {
                "tool": "polygonTool",
                "config": {"attributes": [{"key": "关键点", "value": " platePoint"}]},
            },
        ]
    }


def test_task_config_matches_plate_profile_with_historical_whitespace():
    assert task_config_matches_profile(_task_config(), DEFAULT_PLATE_RECOGNITION_CONFIG)


def test_normalize_plate_task_config_writes_complete_profile_and_keeps_other_tools():
    original = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {
                    "showConfirm": True,
                    "attributes": [{"key": "旧标签", "value": "doebleEP"}],
                },
            },
            {"tool": "tagTool", "config": {"attributes": [{"value": "scene"}]}},
        ]
    }

    normalized = normalize_plate_recognition_task_config(original)

    assert original["tools"][0]["config"]["attributes"][0]["value"] == "doebleEP"
    assert normalized["defaultRectPresetId"] == "plateRecognition"
    assert normalized["tools"][1] == original["tools"][1]
    rect = next(item for item in normalized["tools"] if item["tool"] == "rectTool")
    rect_label = rect["config"]["attributes"][0]
    assert rect["config"]["showConfirm"] is True
    assert rect_label["value"] == "doubleEP"
    assert [item["value"] for item in rect_label["attributes"]] == [
        "plateNumber",
        "color",
        "plateType",
    ]
    polygon = next(
        item for item in normalized["tools"] if item["tool"] == "polygonTool"
    )
    assert polygon["config"]["attributes"][0]["value"] == "platePoint"
    assert task_config_matches_profile(normalized)


def test_normalize_plate_sample_repairs_known_tokens_without_fabricating_values():
    sample_data = {
        "result": {
            "rectTool": {
                "result": [
                    {
                        "label": "doebleEP",
                        "attributes": {
                            "plateNumber": "A12345",
                            "color": " green ",
                            " plateType": [" greenPlate"],
                        },
                    },
                    {"label": "doubleEP"},
                ]
            },
            "polygonTool": {"result": [{"label": " platePoint"}]},
        }
    }

    normalized, stats = normalize_plate_recognition_sample_data(sample_data)

    rects = normalized["result"]["rectTool"]["result"]
    assert rects[0]["label"] == "doubleEP"
    assert rects[0]["attributes"] == {
        "plateNumber": "A12345",
        "color": "green",
        "plateType": ["greenPlate"],
    }
    assert "attributes" not in rects[1]
    assert normalized["result"]["polygonTool"]["result"][0]["label"] == "platePoint"
    assert stats == {
        "rect_labels": 1,
        "polygon_labels": 1,
        "attribute_keys": 1,
        "attribute_values": 2,
    }


def test_normalize_plate_sample_leaves_clean_json_string_unchanged():
    sample_data = {"result": '{"rectTool":{"result":[{"label":"doubleEP"}]}}'}

    normalized, stats = normalize_plate_recognition_sample_data(sample_data)

    assert normalized == sample_data
    assert not any(stats.values())


def test_normalize_four_corners_removes_closing_point_and_orders_clockwise():
    points = [
        {"x": 12, "y": 10},
        {"x": 92, "y": 8},
        {"x": 90, "y": 40},
        {"x": 10, "y": 42},
        {"x": 12.4, "y": 10.2},
    ]
    corners = normalize_four_corners(points, rect_bounds=(0, 0, 100, 50))
    assert corners == ((12.0, 10.0), (92.0, 8.0), (90.0, 40.0), (10.0, 42.0))


def test_audit_plate_sample_pairs_polygon_and_normalizes_attributes():
    result = {
        "rectTool": {
            "result": [
                {
                    "label": " doubleEP ",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 50,
                    "attributes": {
                        "plateNumber": " 7670X5 ",
                        "color": [" white "],
                        " plateType": " whitePlate ",
                    },
                }
            ]
        },
        "polygonTool": {
            "result": [
                {
                    "label": " platePoint",
                    "points": [
                        {"x": 10, "y": 10},
                        {"x": 90, "y": 8},
                        {"x": 92, "y": 40},
                        {"x": 9, "y": 42},
                        {"x": 10.5, "y": 10.2},
                    ],
                }
            ]
        },
    }
    audit = audit_plate_sample(result)
    assert audit.kind == "positive"
    assert len(audit.instances) == 1
    assert audit.instances[0].plate_number == "7670X5"
    assert audit.instances[0].color == "white"
    assert audit.instances[0].plate_type == "whitePlate"
    assert len(audit.instances[0].corners) == 4


def test_audit_plate_sample_treats_no_plate_geometry_as_negative():
    audit = audit_plate_sample(
        {"tagTool": {"result": [{"value": {"scene": ["road"]}}]}}
    )
    assert audit.kind == "negative"
    assert audit.usable is True


def test_audit_plate_sample_rejects_one_sided_geometry():
    audit = audit_plate_sample(
        {
            "rectTool": {
                "result": [
                    {"label": "doubleEP", "x": 1, "y": 2, "width": 30, "height": 10}
                ]
            }
        }
    )
    assert audit.kind == "invalid"
    assert "both rectangle and polygon" in audit.errors[0]


def test_audit_plate_sample_keeps_geometry_when_attributes_are_missing():
    audit = audit_plate_sample(
        {
            "rectTool": {
                "result": [
                    {
                        "label": "doubleEP",
                        "x": 0,
                        "y": 0,
                        "width": 100,
                        "height": 50,
                    }
                ]
            },
            "polygonTool": {
                "result": [
                    {
                        "label": "platePoint",
                        "points": [
                            {"x": 10, "y": 10},
                            {"x": 90, "y": 10},
                            {"x": 90, "y": 40},
                            {"x": 10, "y": 40},
                            {"x": 10, "y": 10},
                        ],
                    }
                ]
            },
        }
    )

    assert audit.kind == "positive"
    assert audit.usable is True
    assert len(audit.instances) == 1
    assert audit.instances[0].plate_number == ""
    assert "missing attributes" in audit.errors[0]
