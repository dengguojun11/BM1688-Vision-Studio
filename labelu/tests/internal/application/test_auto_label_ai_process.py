"""AI 目标检测坐标空间换算、畸形返回容错与过程留痕。"""

import json
from types import SimpleNamespace

import pytest

from labelu.internal.application.service.auto_label import (
    NORMALIZED_COORDINATE_SPACE,
    PIXEL_COORDINATE_SPACE,
    _ai_global_tags_merge_failure,
    _detect_coordinate_space,
    _normalize_results,
)
from labelu.internal.common.config import settings


def _task_config():
    return {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "人员", "value": "person"}]},
            }
        ]
    }


def _trace(**overrides):
    trace = {
        "provider_label": "ark-coding-plan",
        "endpoint_host": "ark.cn-beijing.volces.com",
        "model": "doubao-seed-2-1-pro",
        "request_protocol": "responses",
        "instruction": "FINAL PROMPT",
        "model_image_width": 2560,
        "model_image_height": 1440,
        "original_image_width": 3840,
        "original_image_height": 2160,
    }
    trace.update(overrides)
    return trace


def test_normalized_0_1000_boxes_are_converted_to_original_pixels():
    out = _normalize_results(
        {
            "_geometry_scale": [1.5, 1.5],
            "_image_size": [3840, 2160],
            "_raw_text": '{"results":[...]}',
            "_trace": _trace(),
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 600, "y": 700, "width": 300, "height": 200},
                }
            ],
        },
        "demo.jpg",
        _task_config(),
        include_geometry_tools=True,
        image_size=[3840, 2160],
    )

    assert out["meta"]["coordinate_space"] == NORMALIZED_COORDINATE_SPACE
    item = out["annotations"]["rectTool"]["result"][0]
    assert item["x"] == pytest.approx(2304.0)
    assert item["y"] == pytest.approx(1512.0)
    assert item["width"] == pytest.approx(1152.0)
    assert item["height"] == pytest.approx(432.0)
    assert out["meta"]["coordinate_scale"] == [3.84, 2.16]


def test_pixel_space_boxes_are_not_misread_as_normalized():
    out = _normalize_results(
        {
            "_geometry_scale": [1.5, 1.5],
            "_image_size": [3840, 2160],
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 1500, "y": 900, "width": 200, "height": 200},
                }
            ],
        },
        "demo.jpg",
        _task_config(),
        include_geometry_tools=True,
        image_size=[3840, 2160],
    )

    assert out["meta"]["coordinate_space"] == PIXEL_COORDINATE_SPACE
    item = out["annotations"]["rectTool"]["result"][0]
    assert item["x"] == pytest.approx(2250.0)
    assert item["width"] == pytest.approx(300.0)


def test_small_cluster_keeps_pixel_behavior(monkeypatch):
    monkeypatch.setattr(settings, "AI_BBOX_COORDINATE_SPACE", "auto")
    assert (
        _detect_coordinate_space(
            model_width=2560,
            model_height=1440,
            extent=(120.0, 80.0),
        )
        == PIXEL_COORDINATE_SPACE
    )


def test_coordinate_space_override_forces_pixels(monkeypatch):
    monkeypatch.setattr(settings, "AI_BBOX_COORDINATE_SPACE", "pixels")
    assert (
        _detect_coordinate_space(
            model_width=2560,
            model_height=1440,
            extent=(990.0, 980.0),
        )
        == PIXEL_COORDINATE_SPACE
    )


def test_malformed_y_key_is_recovered_instead_of_dropped():
    out = _normalize_results(
        {
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 58, "128": 118, "width": 142, "height": 236},
                }
            ]
        },
        "demo.jpg",
        _task_config(),
        include_geometry_tools=True,
    )

    items = out["annotations"]["rectTool"]["result"]
    assert len(items) == 1
    assert items[0]["y"] == pytest.approx(118.0)
    assert out["meta"]["dropped_items"] == []
    assert len(out["meta"]["repaired_items"]) == 1
    assert "128" in out["meta"]["repaired_items"][0]["reason"]
    assert "Recovered 1 malformed geometry result" in out["meta"]["warning_message"]


def test_ai_process_meta_captures_prompt_and_raw_response():
    out = _normalize_results(
        {
            "_geometry_scale": [1.5, 1.5],
            "_image_size": [3840, 2160],
            "_raw_text": '{"results":[]}',
            "_trace": _trace(),
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 600, "y": 700, "width": 300, "height": 200},
                }
            ],
        },
        "demo.jpg",
        _task_config(),
        include_geometry_tools=True,
        image_size=[3840, 2160],
    )

    process = out["meta"]["ai_process"]
    assert process["instruction"] == "FINAL PROMPT"
    assert process["raw_response_text"] == '{"results":[]}'
    assert process["model"] == "doubao-seed-2-1-pro"
    assert process["endpoint_host"] == "ark.cn-beijing.volces.com"
    assert process["request_protocol"] == "responses"
    assert process["model_image_width"] == 2560
    assert process["image_width"] == 3840
    assert process["box_count"] == 1


def test_global_tag_normalization_skips_ai_process_meta():
    out = _normalize_results(
        {"results": []},
        "demo.jpg",
        _task_config(),
        include_geometry_tools=False,
    )

    assert "ai_process" not in out["meta"]

def test_normalized_rect_width_that_is_really_right_edge_is_repaired():
    out = _normalize_results(
        {
            "_geometry_scale": [1.5, 1.5],
            "_image_size": [3840, 2160],
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 566, "y": 0, "width": 638, "height": 108},
                }
            ],
        },
        "demo.jpg",
        _task_config(),
        include_geometry_tools=True,
        image_size=[3840, 2160],
    )

    assert out["meta"]["coordinate_space"] == NORMALIZED_COORDINATE_SPACE
    item = out["annotations"]["rectTool"]["result"][0]
    assert item["x"] == pytest.approx(2173.44)
    assert item["width"] == pytest.approx((638 - 566) * 3.84)
    assert len(out["meta"]["repaired_items"]) == 1
    assert "x2" in out["meta"]["repaired_items"][0]["reason"]


def test_normalized_size_width_convention_is_preserved():
    out = _normalize_results(
        {
            "_geometry_scale": [1.5, 1.5],
            "_image_size": [3840, 2160],
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 899, "y": 341, "width": 64, "height": 121},
                },
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 600, "y": 300, "width": 200, "height": 150},
                },
            ],
        },
        "demo.jpg",
        _task_config(),
        include_geometry_tools=True,
        image_size=[3840, 2160],
    )

    assert out["meta"]["width_convention"] == "size"
    items = out["annotations"]["rectTool"]["result"]
    assert items[0]["width"] == pytest.approx(64 * 3.84)
    assert items[1]["width"] == pytest.approx(200 * 3.84)


def test_normalized_edge_response_converts_every_box_consistently():
    out = _normalize_results(
        {
            "_geometry_scale": [1.5, 1.5],
            "_image_size": [3840, 2160],
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 58, "y": 100, "width": 140, "height": 140},
                },
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 900, "y": 100, "width": 950, "height": 140},
                },
            ],
        },
        "demo.jpg",
        _task_config(),
        include_geometry_tools=True,
        image_size=[3840, 2160],
    )

    assert out["meta"]["width_convention"] == "right_edge"
    items = out["annotations"]["rectTool"]["result"]
    assert items[0]["width"] == pytest.approx((140 - 58) * 3.84)
    assert items[1]["width"] == pytest.approx((950 - 900) * 3.84)
    assert len(out["meta"]["repaired_items"]) == 2


def test_normalized_height_bottom_edge_convention():
    out = _normalize_results(
        {
            "_geometry_scale": [1.5, 1.5],
            "_image_size": [3840, 2160],
            "results": [
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 100, "y": 700, "width": 200, "height": 900},
                },
                {
                    "toolName": "rectTool",
                    "label": "person",
                    "result": {"x": 100, "y": 100, "width": 200, "height": 200},
                },
            ],
        },
        "demo.jpg",
        _task_config(),
        include_geometry_tools=True,
        image_size=[3840, 2160],
    )

    assert out["meta"]["height_convention"] == "bottom_edge"
    items = out["annotations"]["rectTool"]["result"]
    assert items[0]["height"] == pytest.approx((900 - 700) * 2.16)
    assert items[1]["height"] == pytest.approx((200 - 100) * 2.16)


def test_preannotation_kind_distinguishes_geometry_and_global_tags():
    from types import SimpleNamespace

    from labelu.internal.application.service.auto_label import (
        GLOBAL_TAG_PREANNOTATION_KIND,
        GEOMETRY_PREANNOTATION_KIND,
        _ai_preannotations_for_mode,
        _preannotation_kind,
    )

    geometry_row = SimpleNamespace(
        data=json.dumps(
            {
                "annotations": {
                    "rectTool": {
                        "toolName": "rectTool",
                        "result": [{"label": "person", "x": 1, "y": 2, "width": 3, "height": 4}],
                    }
                },
                "meta": {"source_type": "ai_generated"},
            }
        )
    )
    tag_row = SimpleNamespace(
        data=json.dumps(
            {
                "annotations": {"tagTool": {"toolName": "tagTool", "result": [{"value": {"sceneType": ["road"]}}]}},
                "meta": {"source_type": "ai_generated"},
            }
        )
    )
    legacy_geometry = SimpleNamespace(
        data=json.dumps(
            {
                "annotations": {"rectTool": {"toolName": "rectTool", "result": [{"label": "person"}]}},
                "meta": {"source_type": "ai_generated"},
            }
        )
    )

    assert _preannotation_kind(geometry_row) == GEOMETRY_PREANNOTATION_KIND
    assert _preannotation_kind(tag_row) == GLOBAL_TAG_PREANNOTATION_KIND
    assert _preannotation_kind(legacy_geometry) == GEOMETRY_PREANNOTATION_KIND

    assert _ai_preannotations_for_mode([geometry_row, tag_row], include_geometry_tools=True) == [geometry_row]
    assert _ai_preannotations_for_mode([geometry_row, tag_row], include_geometry_tools=False) == [tag_row]


def _tag_only_task_config():
    return {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "场景类型",
                            "value": "sceneType",
                            "type": "enum",
                            "options": [{"key": "城市主干道", "value": "urbanArterial"}],
                        }
                    ]
                },
            }
        ]
    }


def _tag_sample(values):
    return SimpleNamespace(
        id=1,
        inner_id=1,
        file=None,
        data=json.dumps(
            {"result": {"tagTool": {"toolName": "tagTool", "result": [{"value": values}]}}}
        ),
    )


def test_ai_tag_noop_merge_is_success_when_tags_already_complete():
    assert (
        _ai_global_tags_merge_failure(
            changed=False,
            include_geometry_tools=False,
            sample=_tag_sample({"sceneType": ["urbanArterial"]}),
            task_config=_tag_only_task_config(),
        )
        is False
    )


def test_ai_tag_noop_merge_is_failure_when_tags_missing():
    assert (
        _ai_global_tags_merge_failure(
            changed=False,
            include_geometry_tools=False,
            sample=_tag_sample({}),
            task_config=_tag_only_task_config(),
        )
        is True
    )


def test_ai_tag_noop_merge_never_fails_in_geometry_mode():
    assert (
        _ai_global_tags_merge_failure(
            changed=False,
            include_geometry_tools=True,
            sample=_tag_sample({}),
            task_config=_tag_only_task_config(),
        )
        is False
    )
