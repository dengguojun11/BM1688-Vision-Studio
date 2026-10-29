import json

import pytest

from labelu.internal.adapter.persistence import crud_pre_annotation
from labelu.internal.application.service import pre_annotation as pre_annotation_svc
from labelu.internal.application.service import sample as sample_svc
from labelu.internal.application.service import vlx_seek_preannotate as svc
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation


def test_vlx_boxes_are_filtered_and_kept_in_pixel_coordinates():
    config = {"tools": [{"tool": "rectTool", "config": {"attributes": [{"key": "人", "value": "person"}, {"key": "车", "value": "car"}]}}]}
    payload = {"boxes": [
        {"label": "person", "xmin": 100, "ymin": 50, "xmax": 500, "ymax": 250},
        {"label": "未配置", "xmin": 0, "ymin": 0, "xmax": 10, "ymax": 10},
    ]}
    result = svc._build_payload("a.jpg", config, payload, 1000, 500, 12)
    rect = result["annotations"]["rectTool"]["result"][0]
    assert rect["label"] == "person"
    assert rect["x"] == 100
    assert rect["y"] == 50
    assert rect["width"] == 400
    assert rect["height"] == 200
    assert result["meta"]["source_type"] == svc.VLX_PRE_META
    assert result["meta"]["width"] == 1000
    assert result["meta"]["height"] == 500
    assert result["meta"]["overwrite_existing_annotations"] is True
    assert result["meta"]["apply_to_canvas_overwrite"] is True
    assert result["meta"]["include_geometry_tools"] is True


def test_vlx_empty_detection_keeps_empty_annotations():
    config = {"tools": [{"tool": "rectTool", "config": {"attributes": [{"key": "人", "value": "person"}]}}]}
    result = svc._build_payload("a.jpg", config, {"boxes": []}, 100, 100, 1)
    assert result["annotations"] == {}


def test_rex_payload_uses_distinct_source_metadata_and_model():
    config = {"tools": [{"tool": "rectTool", "config": {"attributes": [{"key": "杯子", "value": "cup"}]}}]}
    result = svc._build_payload(
        "cafe.jpg", config,
        {"boxes": [{"label": "cup", "xmin": 10, "ymin": 20, "xmax": 40, "ymax": 60}]},
        100, 100, 25, provider="rex",
    )
    assert result["meta"]["source_type"] == svc.REX_PRE_META
    assert result["meta"]["provider"] == "rex-omni"
    assert result["meta"]["model"] == svc.settings.REX_OMNI_MODEL
    assert result["annotations"]["rectTool"]["result"][0]["label"] == "cup"


def test_rex_uses_chinese_category_and_maps_it_back_to_task_label():
    config = {
        "tools": [{
            "tool": "rectTool",
            "config": {"attributes": [
                {"key": "电动车", "value": "nonvehicle_electricbike"},
            ]},
        }],
    }
    query, lang = svc._detection_query(config, provider="rex")
    assert query == "电动车"
    assert lang == "zh"

    result = svc._build_payload(
        "street.jpg",
        config,
        {
            "boxes": [{
                "label": query,
                "xmin": 10,
                "ymin": 20,
                "xmax": 80,
                "ymax": 90,
            }],
            "raw_answer": "model output",
        },
        100,
        100,
        50,
        provider="rex",
    )
    rect = result["annotations"]["rectTool"]["result"][0]
    assert rect["label"] == "nonvehicle_electricbike"
    assert result["meta"]["model_categories"] == ["electric scooter or e-bike"]
    assert result["meta"]["raw_box_count"] == 1
    assert result["meta"]["accepted_box_count"] == 1
    assert result["meta"]["rejected_box_count"] == 0


def test_rex_multilabel_query_uses_platform_keys_for_request_metadata():
    config = {
        "tools": [{
            "tool": "rectTool",
            "config": {"attributes": [
                {"key": "蔬菜摊", "value": "streetVendor_vegetablet"},
                {"key": "餐饮摊贩", "value": "streetVendor_catering"},
            ]},
        }],
    }
    query, lang = svc._detection_query(config, provider="rex")
    assert query == "蔬菜摊; 餐饮摊贩"
    assert lang == "zh"


def test_rex_is_a_generated_canvas_overwrite_source():
    data = json.dumps({
        "meta": {
            "source_type": svc.REX_PRE_META,
            "apply_to_canvas_overwrite": True,
        },
        "annotations": {},
    })
    assert svc.REX_PRE_META in crud_pre_annotation.GENERATED_PRE_ANNOTATION_SOURCE_TYPES
    assert pre_annotation_svc._is_canvas_overwrite_candidate(data) is True
    assert sample_svc._is_vlx_preannotation_data(data) is True


def test_vlx_uses_task_class_contract_instead_of_all_rect_labels():
    config = {
        "tools": [{
            "tool": "rectTool",
            "config": {"attributes": [
                {"key": "bike", "value": "nonvehicle_electricbike"},
                {"key": "car", "value": "vehicle_car"},
            ]},
        }],
        "consistent_pool": {
            "dataset_category_key": "illegalParking",
            "class_names": ["nonvehicle_electricbike"],
        },
    }
    assert [item["value"] for item in svc._target_labels(config)] == ["nonvehicle_electricbike"]
    query, lang = svc._detection_query(config)
    assert "illegally parked" in query
    assert "electric scooter or e-bike" in query
    assert lang == "en"

    result = svc._build_payload(
        "a.jpg", config,
        {"boxes": [{"label": query, "xmin": 100, "ymin": 50, "xmax": 500, "ymax": 250}]},
        1000, 500, 12,
    )
    rects = result["annotations"]["rectTool"]["result"]
    assert len(rects) == 1
    assert rects[0]["label"] == "nonvehicle_electricbike"
    assert result["meta"]["target_labels"] == ["nonvehicle_electricbike"]


def test_selected_labels_limit_query_and_platform_output():
    config = {
        "tools": [{
            "tool": "rectTool",
            "config": {"attributes": [
                {"key": "行人", "value": "person"},
                {"key": "汽车", "value": "car"},
            ]},
        }],
    }

    query, lang = svc._detection_query(config, target_labels=["person"])
    assert query == "行人"
    assert lang == "zh"

    result = svc._build_payload(
        "street.jpg",
        config,
        {"boxes": [
            {"label": "行人", "xmin": 10, "ymin": 20, "xmax": 40, "ymax": 60},
            {"label": "汽车", "xmin": 50, "ymin": 20, "xmax": 90, "ymax": 60},
            {"label": "unknown", "xmin": 1, "ymin": 1, "xmax": 9, "ymax": 9},
        ]},
        100,
        100,
        25,
        target_labels=["person"],
    )
    rects = result["annotations"]["rectTool"]["result"]
    assert [rect["label"] for rect in rects] == ["person"]
    assert result["meta"]["target_labels"] == ["person"]
    assert result["meta"]["accepted_box_count"] == 1
    assert result["meta"]["rejected_box_count"] == 2

    with pytest.raises(LabelUException, match="不属于当前任务"):
        svc._selected_target_labels(config, ["not-a-task-label"])


def test_rex_geometry_preannotation_preserves_independent_global_annotations():
    global_row = TaskPreAnnotation(
        id=1,
        data=json.dumps({
            "annotations": {
                "tagTool": {
                    "toolName": "tagTool",
                    "result": [{"id": "weather", "value": "rain"}],
                },
                "textTool": {
                    "toolName": "textTool",
                    "result": [{"id": "note", "value": "keep me"}],
                },
            },
            "config": {
                "tagTool": [{"value": "rain", "key": "雨天"}],
                "textTool": [],
            },
            "meta": {"source_type": "ai_generated"},
        }),
    )
    rex_row = TaskPreAnnotation(
        id=2,
        data=json.dumps({
            "annotations": {
                "rectTool": {
                    "toolName": "rectTool",
                    "result": [{"id": "rex-box", "label": "person"}],
                },
            },
            "config": {"rectTool": [{"value": "person", "key": "行人"}]},
            "meta": {
                "source_type": svc.REX_PRE_META,
                "apply_to_canvas_overwrite": True,
                "preserve_global_annotations": True,
            },
        }),
    )

    selected = pre_annotation_svc.select_canvas_pre_annotation([global_row, rex_row])
    assert len(selected) == 1
    assert selected[0].id == rex_row.id
    merged = json.loads(selected[0].data)
    assert merged["annotations"]["rectTool"]["result"][0]["id"] == "rex-box"
    assert merged["annotations"]["tagTool"]["result"][0]["value"] == "rain"
    assert merged["annotations"]["textTool"]["result"][0]["value"] == "keep me"
    assert merged["config"]["tagTool"][0]["value"] == "rain"
