"""YOLO 预标注：从拉框标签推断 tagTool 全局枚举预填。"""

from labelu.internal.application.service.yolo_preannotate import (
    _build_rect_payload,
    _build_detection_label_aliases,
    _detections_to_rect_results,
    _infer_tag_tool_results_from_rect_labels,
    _plate_detections_to_annotation_results,
)


def test_generic_person_detection_is_written_as_task_pedestrian_only_when_configured():
    class Boxes:
        xyxy = [[10, 20, 40, 80], [50, 60, 90, 100]]
        cls = [0, 1]
        conf = [0.92, 0.81]

        def __len__(self):
            return 2

    aliases = _build_detection_label_aliases(
        ["person", "person_electricbike"],
        {"pedestrian", "person_electricbike"},
    )
    rects = _detections_to_rect_results(
        Boxes(),
        {0: "person", 1: "person_electricbike"},
        {"pedestrian", "person_electricbike"},
        label_aliases=aliases,
    )
    assert [item["label"] for item in rects] == [
        "pedestrian",
        "person_electricbike",
    ]


def test_generic_person_detection_is_still_filtered_without_pedestrian_label():
    aliases = _build_detection_label_aliases(["person"], {"person_electricbike"})
    assert aliases == {}


def test_infer_tag_when_rect_label_matches_option_value():
    task_cfg = {
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
                        },
                    ]
                },
            }
        ]
    }
    rects = [{"label": "sunny", "id": "1", "order": 0, "x": 0, "y": 0, "width": 1, "height": 1}]
    items = _infer_tag_tool_results_from_rect_labels(rects, task_cfg)
    assert len(items) == 1
    assert items[0]["type"] == "tag"
    assert items[0]["value"] == {"weather": ["sunny"]}


def test_infer_tag_prefers_more_boxes():
    task_cfg = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "类",
                            "value": "cls",
                            "type": "enum",
                            "options": [
                                {"key": "A", "value": "a"},
                                {"key": "B", "value": "b"},
                            ],
                        },
                    ]
                },
            }
        ]
    }
    rects = [
        {"label": "b", "id": "1", "order": 0, "x": 0, "y": 0, "width": 1, "height": 1},
        {"label": "b", "id": "2", "order": 1, "x": 1, "y": 1, "width": 1, "height": 1},
        {"label": "a", "id": "3", "order": 2, "x": 2, "y": 2, "width": 1, "height": 1},
    ]
    items = _infer_tag_tool_results_from_rect_labels(rects, task_cfg)
    assert items[0]["value"] == {"cls": ["b"]}


def test_infer_tag_empty_when_no_enum_match():
    task_cfg = {
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
                        },
                    ]
                },
            }
        ]
    }
    rects = [{"label": "rain", "id": "1", "order": 0, "x": 0, "y": 0, "width": 1, "height": 1}]
    assert _infer_tag_tool_results_from_rect_labels(rects, task_cfg) == []


def test_build_payload_includes_tag_tool():
    task_cfg = {
        "tools": [
            {
                "tool": "rectTool",
                "config": {"attributes": [{"key": "X", "value": "dog"}]},
            },
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {
                            "key": "动物",
                            "value": "animal",
                            "type": "enum",
                            "options": [{"key": "狗", "value": "dog"}],
                        },
                    ]
                },
            },
        ]
    }
    rects = [{"label": "dog", "id": "1", "order": 0, "x": 0, "y": 0, "width": 10, "height": 10}]
    payload = _build_rect_payload(
        sample_name="x.jpg",
        task_cfg_merged=task_cfg,
        rect_results=rects,
        model_kind="imported",
        model_id=1,
        weights_name="w.pt",
    )
    ann = payload["annotations"]
    assert "rectTool" in ann
    assert "tagTool" in ann
    assert ann["tagTool"]["result"][0]["type"] == "tag"
    assert ann["tagTool"]["result"][0]["value"] == {"animal": ["dog"]}


def test_detection_payload_does_not_add_polygon_tool():
    payload = _build_rect_payload(
        sample_name="x.jpg",
        task_cfg_merged={
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {"attributes": [{"key": "dog", "value": "dog"}]},
                }
            ]
        },
        rect_results=[
            {
                "label": "dog",
                "id": "1",
                "order": 0,
                "x": 0,
                "y": 0,
                "width": 1,
                "height": 1,
            }
        ],
        model_kind="trained",
        model_id=1,
        weights_name="best.pt",
    )

    assert "polygonTool" not in payload["annotations"]
    assert payload["meta"]["job_type"] == "yolo_detection"
    assert payload["meta"]["include_geometry_tools"] is True


def test_detection_payload_does_not_write_rects_for_polygon_only_task():
    payload = _build_rect_payload(
        sample_name="x.jpg",
        task_cfg_merged={
            "tools": [
                {
                    "tool": "polygonTool",
                    "config": {"attributes": [{"key": "dog", "value": "dog"}]},
                }
            ]
        },
        rect_results=[
            {
                "label": "dog",
                "id": "1",
                "order": 0,
                "x": 0,
                "y": 0,
                "width": 1,
                "height": 1,
            }
        ],
        model_kind="trained",
        model_id=1,
        weights_name="best.pt",
    )

    assert payload["annotations"] == {}


def test_user_requested_yolo_payload_marks_geometry_only_canvas_overwrite():
    payload = _build_rect_payload(
        sample_name="x.jpg",
        task_cfg_merged={"tools": []},
        rect_results=[],
        model_kind="imported",
        model_id=1,
        weights_name="best.pt",
        apply_to_canvas_overwrite=True,
    )

    assert payload["annotations"] == {}
    assert payload["meta"] == {
        "source_type": "yolo_generated",
        "model_kind": "imported",
        "model_id": 1,
        "weights_file": "best.pt",
        "job_type": "yolo_detection",
        "include_geometry_tools": True,
        "apply_to_canvas_overwrite": True,
        "overwrite_existing_annotations": True,
        "preserve_global_annotations": True,
    }


def test_plate_detection_builds_four_point_polygon_without_plate_attributes():
    rects, polygons = _plate_detections_to_annotation_results(
        [
            {
                "confidence": 0.9236,
                "x1": 10,
                "y1": 20,
                "x2": 110,
                "y2": 70,
                "corners": [
                    {"x": 12, "y": 22, "confidence": 0.99},
                    {"x": 108, "y": 21, "confidence": 0.98},
                    {"x": 109, "y": 68, "confidence": 0.97},
                    {"x": 11, "y": 69, "confidence": 0.96},
                ],
                "plate_number": "H27465",
                "color": "white",
                "plate_type": "whitePlate",
            }
        ],
        rect_label="doubleEP",
        polygon_label="platePoint",
        detection_only=True,
    )

    assert len(rects) == 1
    assert rects[0]["label"] == "doubleEP"
    assert "attributes" not in rects[0]
    assert len(polygons) == 1
    assert polygons[0]["label"] == "platePoint"
    assert len(polygons[0]["points"]) == 4
    assert polygons[0]["points"] == [
        {"x": 12.0, "y": 22.0},
        {"x": 108.0, "y": 21.0},
        {"x": 109.0, "y": 68.0},
        {"x": 11.0, "y": 69.0},
    ]


def test_plate_recognition_keeps_legacy_closed_polygon_and_attributes():
    rects, polygons = _plate_detections_to_annotation_results(
        [
            {
                "confidence": 0.9236,
                "x1": 10,
                "y1": 20,
                "x2": 110,
                "y2": 70,
                "corners": [
                    {"x": 12, "y": 22},
                    {"x": 108, "y": 21},
                    {"x": 109, "y": 68},
                    {"x": 11, "y": 69},
                ],
                "plate_number": "H27465",
                "color": "white",
                "plate_type": "whitePlate",
            }
        ],
        rect_label="doubleEP",
        polygon_label="platePoint",
    )

    assert rects[0]["attributes"]["plateNumber"] == "H27465"
    assert len(polygons[0]["points"]) == 5
    assert polygons[0]["points"][0] == polygons[0]["points"][-1]


def test_plate_detection_without_four_corners_keeps_rect_only():
    rects, polygons = _plate_detections_to_annotation_results(
        [
            {
                "confidence": 0.8,
                "x1": 0,
                "y1": 0,
                "x2": 10,
                "y2": 5,
                "corners": [],
            }
        ],
        rect_label="doubleEP",
        polygon_label="platePoint",
    )

    assert len(rects) == 1
    assert polygons == []


def test_merge_ai_tag_into_yolo_when_yolo_has_no_tag_tool():
    from labelu.internal.application.service.yolo_preannotate import merge_ai_tag_tool_results_into_yolo_payload

    payload = {
        "sample_name": "a.jpg",
        "annotations": {
            "rectTool": {
                "toolName": "rectTool",
                "result": [
                    {
                        "id": "r1",
                        "order": 0,
                        "label": "dog",
                        "visible": True,
                        "x": 0,
                        "y": 0,
                        "width": 1,
                        "height": 1,
                    }
                ],
            }
        },
        "config": {},
        "meta": {},
    }
    ai_items = [
        {
            "type": "tag",
            "value": {"weather": ["sunny"]},
        }
    ]
    merge_ai_tag_tool_results_into_yolo_payload(payload, ai_items)
    assert payload["annotations"]["rectTool"]["result"][0]["label"] == "dog"
    dim_keys: list[str] = []
    for x in payload["annotations"]["tagTool"]["result"]:
        dim_keys.extend(list((x.get("value") or {}).keys()))
    assert "weather" in dim_keys


def test_merge_ai_tag_overrides_yolo_infer_for_same_field():
    from labelu.internal.application.service.yolo_preannotate import merge_ai_tag_tool_results_into_yolo_payload

    payload = {
        "annotations": {
            "tagTool": {
                "toolName": "tagTool",
                "result": [{"type": "tag", "value": {"weather": ["rain"]}}],
            }
        }
    }
    ai_items = [{"type": "tag", "value": {"weather": ["sunny"]}}]
    merge_ai_tag_tool_results_into_yolo_payload(payload, ai_items)
    found = None
    for it in payload["annotations"]["tagTool"]["result"]:
        v = it.get("value") or {}
        if "weather" in v:
            found = v["weather"]
            break
    assert found == ["sunny"]
