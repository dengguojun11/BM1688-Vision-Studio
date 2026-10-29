import json

from labelu.internal.common.pre_annotation_sample_repair import (
    convert_uploaded_pre_annotation_to_sample_result,
    count_annotation_items,
    count_out_of_bounds_rects,
    detect_systematic_rect_scaling_issue,
)


def test_convert_uploaded_json_pre_annotation_to_sample_result():
    payload = {
        "id": 1,
        "result": {
            "width": 1920,
            "height": 1080,
            "rotate": 0,
            "annotations": [
                {
                    "toolName": "rectTool",
                    "result": [{"id": "a", "x": 10, "y": 20, "width": 30, "height": 40}],
                },
                {
                    "toolName": "tagTool",
                    "result": [{"id": "b", "value": {"scene": ["market"]}}],
                },
            ],
        },
    }

    result = convert_uploaded_pre_annotation_to_sample_result(payload)

    assert result["width"] == 1920
    assert result["height"] == 1080
    assert result["rectTool"]["result"][0]["x"] == 10
    assert result["tagTool"]["result"][0]["value"] == {"scene": ["market"]}


def test_convert_uploaded_jsonl_pre_annotation_to_sample_result():
    payload = {
        "sample_name": "demo.jpg",
        "meta_data": {"width": 100, "height": 50, "rotate": 90},
        "annotations": {
            "rectTool": {
                "toolName": "rectTool",
                "result": [{"id": "a", "x": 1, "y": 2, "width": 3, "height": 4}],
            }
        },
    }

    result = convert_uploaded_pre_annotation_to_sample_result(payload)

    assert result == {
        "width": 100,
        "height": 50,
        "rotate": 90,
        "rectTool": {
            "toolName": "rectTool",
            "result": [{"id": "a", "x": 1, "y": 2, "width": 3, "height": 4}],
        },
    }


def test_count_helpers_work_on_sample_result_json():
    payload = {
        "width": 100,
        "height": 50,
        "rectTool": {
            "toolName": "rectTool",
            "result": [
                {"id": "ok", "x": 1, "y": 2, "width": 3, "height": 4},
                {"id": "bad", "x": 95, "y": 40, "width": 10, "height": 20},
            ],
        },
        "tagTool": {
            "toolName": "tagTool",
            "result": [{"id": "tag-1", "value": {"scene": ["market"]}}],
        },
    }

    assert count_annotation_items(json.dumps(payload, ensure_ascii=False)) == 3
    assert count_out_of_bounds_rects(payload) == 1


def test_detect_systematic_rect_scaling_issue_flags_scaled_rects():
    expected = {
        "width": 1920,
        "height": 1080,
        "rectTool": {
            "toolName": "rectTool",
            "result": [
                {"id": "a", "x": 100, "y": 200, "width": 300, "height": 100, "label": "demo"},
                {"id": "b", "x": 900, "y": 400, "width": 240, "height": 160, "label": "demo"},
                {"id": "c", "x": 1200, "y": 700, "width": 120, "height": 90, "label": "demo"},
            ],
        },
    }
    current = {
        "width": 1920,
        "height": 1080,
        "rectTool": {
            "toolName": "rectTool",
            "result": [
                {"id": "a", "x": 75, "y": 100, "width": 225, "height": 50, "label": "demo"},
                {"id": "b", "x": 675, "y": 200, "width": 180, "height": 80, "label": "demo"},
                {"id": "c", "x": 900, "y": 350, "width": 90, "height": 45, "label": "demo"},
            ],
        },
    }

    detected = detect_systematic_rect_scaling_issue(current, expected)

    assert detected["suspicious"] is True
    assert detected["reasons"] == ["systematic_rect_scaling"]
    assert detected["ids_equal"] is True
    assert detected["shared_rect_count"] == 3
    assert detected["unscaled_match_ratio"] == 1.0
    assert detected["direct_match_ratio"] == 0.0


def test_detect_systematic_rect_scaling_issue_ignores_manual_count_changes():
    expected = {
        "width": 1920,
        "height": 1080,
        "rectTool": {
            "toolName": "rectTool",
            "result": [
                {"id": "a", "x": 100, "y": 200, "width": 300, "height": 100, "label": "demo"},
                {"id": "b", "x": 900, "y": 400, "width": 240, "height": 160, "label": "demo"},
            ],
        },
    }
    current = {
        "width": 1920,
        "height": 1080,
        "rectTool": {
            "toolName": "rectTool",
            "result": [
                {"id": "a", "x": 100, "y": 200, "width": 300, "height": 100, "label": "demo"},
            ],
        },
    }

    detected = detect_systematic_rect_scaling_issue(current, expected)

    assert detected["suspicious"] is False
    assert detected["ids_equal"] is False
    assert "rect_id_set_changed" in detected["reasons"]
