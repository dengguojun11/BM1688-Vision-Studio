from labelu.internal.application.service.yolo_dataset import build_yolo_rect_class_names


def test_rect_classes_ignore_tagtool():
    cfg = {
        "tools": [
            {
                "tool": "tagTool",
                "config": {
                    "attributes": [
                        {"key": "场景", "value": "scene", "type": "enum", "options": []}
                    ]
                },
            },
            {
                "tool": "rectTool",
                "config": {
                    "attributes": [
                        {"key": "摊", "value": "stall"},
                    ]
                },
            },
        ]
    }
    names = build_yolo_rect_class_names(cfg)
    assert names == ["stall"]


def test_rect_classes_empty_without_rect_tool():
    cfg = {"tools": [{"tool": "polygonTool", "config": {"attributes": [{"value": "x"}]}}]}
    assert build_yolo_rect_class_names(cfg) == []
