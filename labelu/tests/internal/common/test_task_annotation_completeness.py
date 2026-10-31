"""task_annotation_completeness 单元测试。"""

import json
from types import SimpleNamespace

from labelu.internal.common.task_annotation_completeness import (
    audit_one_sample,
    audit_task_samples,
    list_required_global_tag_keys,
    sample_geometry_ok,
    sample_global_tags_complete,
    sample_has_valid_rect,
)
from labelu.internal.domain.models.sample import SampleState


def _task_config_with_tags_and_rect():
    return {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "场景",
                            "value": "scene",
                            "type": "enum",
                            "options": [{"key": "A", "value": "A"}],
                        }
                    ]
                },
            },
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "目标", "value": "obj"}]},
            },
        ]
    }


def _task_config_with_tags_and_polygon():
    return {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "场景",
                            "value": "scene",
                            "type": "enum",
                            "options": [{"key": "A", "value": "A"}],
                        }
                    ]
                },
            },
            {
                "tool": "polygonTool",
                "config": {
                    "lowerLimitPointNum": 5,
                    "upperLimitPointNum": 10,
                    "attributes": [{"key": "目标", "value": "poly"}],
                },
            },
        ]
    }


def _sample(data_result: dict, *, sample_id: int = 1):
    outer = {"result": json.dumps(data_result, ensure_ascii=False)}
    return SimpleNamespace(
        id=sample_id,
        inner_id=1,
        data=json.dumps(outer, ensure_ascii=False),
        file=SimpleNamespace(filename="a.jpg"),
    )


def _sized_result(*, tag_value: str = "A", rect: dict | None = None):
    return {
        "width": 100,
        "height": 80,
        "tagTool": {"result": [{"value": {"scene": tag_value}}]},
        "rectTool": {
            "result": [
                rect or {"x": 10, "y": 10, "width": 12, "height": 8, "label": "obj"}
            ]
        },
    }


def _sized_polygon_result(*, tag_value: str = "A", points=None):
    return {
        "width": 100,
        "height": 80,
        "tagTool": {"result": [{"value": {"scene": tag_value}}]},
        "polygonTool": {
            "result": [
                {
                    "label": "poly",
                    "points": points
                    or [
                        {"x": 10, "y": 10},
                        {"x": 20, "y": 10},
                        {"x": 25, "y": 20},
                        {"x": 20, "y": 30},
                        {"x": 10, "y": 30},
                    ],
                }
            ]
        },
    }


def _plate_detection_config():
    return {
        "defaultRectPresetId": "plateDetection",
        "tools": [
            {
                "tool": "rectTool",
                "config": {
                    "attributes": [{"key": "车牌", "value": "doubleEP"}]
                },
            },
            {
                "tool": "polygonTool",
                "config": {
                    "lowerLimitPointNum": 4,
                    "upperLimitPointNum": 4,
                    "attributes": [{"key": "关键点", "value": "platePoint"}],
                },
            },
        ],
    }


def _plate_detection_result(*, rect=True, polygon=True):
    result = {"width": 100, "height": 80}
    if rect:
        result["rectTool"] = {
            "result": [
                {
                    "x": 10,
                    "y": 10,
                    "width": 80,
                    "height": 30,
                    "label": "doubleEP",
                }
            ]
        }
    if polygon:
        result["polygonTool"] = {
            "result": [
                {
                    "label": "platePoint",
                    "points": [
                        {"x": 12, "y": 12},
                        {"x": 88, "y": 12},
                        {"x": 88, "y": 38},
                        {"x": 12, "y": 38},
                    ],
                }
            ]
        }
    return result


def test_list_required_global_tag_keys_from_tag_tool():
    keys = list_required_global_tag_keys(_task_config_with_tags_and_rect())
    assert keys == ["scene"]


def test_sample_global_tags_complete():
    ok, missing = sample_global_tags_complete(
        {"tagTool": {"result": [{"value": {"scene": "A"}}]}},
        ["scene"],
    )
    assert ok is True
    assert missing == []

    ok2, missing2 = sample_global_tags_complete(
        {"tagTool": {"result": [{"value": {}}]}},
        ["scene"],
    )
    assert ok2 is False
    assert missing2 == ["scene"]

    invalid, invalid_missing = sample_global_tags_complete(
        {"tagTool": {"result": [{"value": {"scene": "UNKNOWN"}}]}},
        ["scene"],
        allowed_options={"scene": {"A"}},
    )
    assert invalid is False
    assert invalid_missing == ["scene"]


def test_sample_has_valid_rect_requires_positive_size():
    assert (
        sample_has_valid_rect(
            {"rectTool": {"result": [{"width": 10, "height": 0, "label": "obj"}]}},
            ["obj"],
        )
        is False
    )
    assert (
        sample_has_valid_rect(
            {"rectTool": {"result": [{"width": 10, "height": 5, "label": "obj"}]}},
            ["obj"],
        )
        is True
    )


def test_audit_one_sample_ok():
    cfg = _task_config_with_tags_and_rect()
    sample = _sample(_sized_result())
    row = audit_one_sample(sample, cfg)
    assert row["ok"] is True
    assert row["sample_quality_state"] == SampleState.DONE.value


def test_audit_one_polygon_sample_uses_configured_polygon_tool():
    cfg = _task_config_with_tags_and_polygon()
    row = audit_one_sample(_sample(_sized_polygon_result()), cfg)
    assert row["ok"] is True
    assert row["geometry_annotation_ok"] is True
    assert row["geometry_tool"] == "polygonTool"
    assert row["rect_ok"] is True
    assert row["sample_quality_state"] == SampleState.DONE.value


def test_plate_detection_polygon_accepts_legacy_duplicate_closing_point():
    cfg = _plate_detection_config()
    result = _plate_detection_result()
    result["polygonTool"]["result"][0]["points"].append({"x": 12, "y": 12})

    row = audit_one_sample(_sample(result), cfg)

    assert row["ok"] is True
    assert row["geometry_annotation_ok"] is True


def test_plate_detection_requires_rectangle_and_four_corners():
    cfg = _plate_detection_config()

    only_rect = audit_one_sample(
        _sample(_plate_detection_result(rect=True, polygon=False)), cfg
    )
    assert only_rect["ok"] is False
    assert only_rect["geometry_annotation_ok"] is False
    assert only_rect["geometry_tool"] == "polygonTool"

    only_polygon = audit_one_sample(
        _sample(_plate_detection_result(rect=False, polygon=True)), cfg
    )
    assert only_polygon["ok"] is False
    assert only_polygon["geometry_annotation_ok"] is False
    assert only_polygon["geometry_tool"] == "rectTool"

    complete = audit_one_sample(
        _sample(_plate_detection_result(rect=True, polygon=True)), cfg
    )
    assert complete["ok"] is True
    assert complete["geometry_annotation_ok"] is True
    assert complete["geometry_tool"] == "rectTool"


def test_plate_detection_does_not_require_recognition_attributes():
    cfg = _plate_detection_config()
    result = _plate_detection_result()
    result["rectTool"]["result"][0]["attributes"] = {}

    row = audit_one_sample(_sample(result), cfg)

    assert row["ok"] is True


def test_audit_one_polygon_sample_missing_geometry_is_incomplete():
    cfg = _task_config_with_tags_and_polygon()
    result = {
        "width": 100,
        "height": 80,
        "tagTool": {"result": [{"value": {"scene": "A"}}]},
    }
    row = audit_one_sample(_sample(result), cfg)
    assert row["ok"] is False
    assert row["geometry_annotation_ok"] is False
    assert row["geometry_tool"] == "polygonTool"
    assert row["sample_quality_state"] == SampleState.INCOMPLETE.value


def test_audit_one_polygon_sample_accepts_explicit_negative():
    cfg = _task_config_with_tags_and_polygon()
    result = {
        "width": 100,
        "height": 80,
        "tagTool": {"result": [{"value": {"scene": "A"}}]},
    }
    sample = SimpleNamespace(
        id=1,
        inner_id=1,
        data=json.dumps(
            {
                "result": json.dumps(result, ensure_ascii=False),
                "training_sample_type": "negative",
            },
            ensure_ascii=False,
        ),
        file=SimpleNamespace(filename="negative.jpg"),
    )
    row = audit_one_sample(sample, cfg)
    assert row["ok"] is True
    assert row["geometry_tool"] == "negative"


def test_audit_one_polygon_sample_out_of_bounds_is_abnormal():
    cfg = _task_config_with_tags_and_polygon()
    result = _sized_polygon_result(
        points=[
            {"x": 10, "y": 10},
            {"x": 20, "y": 10},
            {"x": 25, "y": 20},
            {"x": 20, "y": 30},
            {"x": 101, "y": 30},
        ]
    )
    row = audit_one_sample(_sample(result), cfg)
    assert row["geometry_annotation_ok"] is True
    assert row["geometry_ok"] is False
    assert row["sample_quality_state"] == SampleState.ABNORMAL.value


def test_audit_one_sample_missing_tag_is_incomplete():
    cfg = _task_config_with_tags_and_rect()
    sample = _sample(_sized_result(tag_value=""))
    row = audit_one_sample(sample, cfg)
    assert row["ok"] is False
    assert row["tags_ok"] is False
    assert row["geometry_ok"] is True
    assert row["sample_quality_state"] == SampleState.INCOMPLETE.value


def test_audit_one_sample_empty_rect_is_incomplete():
    cfg = _task_config_with_tags_and_rect()
    sample = _sample({**_sized_result(), "rectTool": {"result": []}})
    row = audit_one_sample(sample, cfg)
    assert row["rect_ok"] is False
    assert row["geometry_ok"] is True
    assert row["sample_quality_state"] == SampleState.INCOMPLETE.value


def test_audit_one_sample_out_of_bounds_is_abnormal():
    cfg = _task_config_with_tags_and_rect()
    sample = _sample(
        _sized_result(rect={"x": 95, "y": 10, "width": 12, "height": 8, "label": "obj"})
    )
    row = audit_one_sample(sample, cfg)
    assert row["rect_ok"] is True
    assert row["geometry_ok"] is False
    assert row["sample_quality_state"] == SampleState.ABNORMAL.value


def test_sample_geometry_ok_reports_missing_dimensions_as_abnormal_when_geometry_exists():
    result = {
        "tagTool": {"result": [{"value": {"scene": "A"}}]},
        "rectTool": {
            "result": [{"x": 0, "y": 0, "width": 1, "height": 1, "label": "obj"}]
        },
    }
    ok, reason = sample_geometry_ok(_sample(result), result)
    assert ok is False
    assert reason == "missing image width or height"


def test_audit_task_samples_all_must_pass():
    cfg = _task_config_with_tags_and_rect()
    good = _sample(_sized_result(), sample_id=1)
    bad = _sample({"rectTool": {"result": []}}, sample_id=2)
    audit = audit_task_samples(cfg, [good, bad])
    assert audit["training_ready"] is False
    assert audit["passed_samples"] == 1
    assert audit["total_samples"] == 2


def test_audit_task_samples_reports_generic_geometry_counter_for_polygon_task():
    cfg = _task_config_with_tags_and_polygon()
    good = _sample(_sized_polygon_result(), sample_id=1)
    missing = _sample(
        {
            "width": 100,
            "height": 80,
            "tagTool": {"result": [{"value": {"scene": "A"}}]},
        },
        sample_id=2,
    )
    audit = audit_task_samples(cfg, [good, missing])
    assert audit["training_ready"] is False
    assert audit["passed_samples"] == 1
    assert audit["failed_missing_geometry_count"] == 1
    assert audit["failed_missing_rect_count"] == 0


def test_skipped_sample_counts_incomplete_but_does_not_block_training_ready():
    cfg = _task_config_with_tags_and_rect()
    done = _sample(_sized_result(), sample_id=1)
    skipped = _sample({"rectTool": {"result": []}}, sample_id=2)
    skipped.state = SampleState.SKIPPED.value
    audit = audit_task_samples(cfg, [done, skipped])
    assert audit["training_ready"] is True
    assert audit["failed_incomplete_count"] == 1
    assert audit["failed_samples_count"] == 0
