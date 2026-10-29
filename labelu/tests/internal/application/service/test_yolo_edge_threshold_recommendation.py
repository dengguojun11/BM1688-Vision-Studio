from labelu.internal.application.service import (
    yolo_edge_threshold_recommendation as svc,
)


def test_parse_ai_threshold_payload_from_json_block() -> None:
    body = """
```json
{
  "recommended_thresholds": {
    "conf_thres": 0.31,
    "nms_thres": 0.68,
    "match_iou_thres": 0.52,
    "reason": "适度放宽置信度以提升召回",
    "confidence_level": "high"
  }
}
```
"""
    payload = svc._parse_ai_threshold_payload(body)
    assert payload is not None
    assert payload["conf_thres"] == 0.31
    assert payload["confidence_level"] == "high"


def test_validate_ai_thresholds_clamps_values() -> None:
    row = svc.validate_ai_thresholds(
        {
            "conf_thres": 0.99,
            "nms_thres": 0.12,
            "match_iou_thres": 0.91,
            "reason": "测试",
            "confidence_level": "HIGH",
        },
        labeled_image_count=3,
        provider_key="qwen_plus",
    )
    assert row.conf_thres == 0.60
    assert row.nms_thres == 0.30
    assert row.match_iou_thres == 0.70
    assert row.source == "ai_recommended"
    assert row.confidence_level == "high"
    assert row.provider_key == "qwen_plus"


def test_validate_ai_thresholds_without_labels_forces_default_match_iou() -> None:
    row = svc.validate_ai_thresholds(
        {
            "conf_thres": 0.24,
            "nms_thres": 0.61,
            "match_iou_thres": 0.63,
            "reason": "图片无标注",
            "confidence_level": "medium",
        },
        labeled_image_count=0,
        provider_key="volcengine",
    )
    assert row.conf_thres == 0.24
    assert row.nms_thres == 0.61
    assert row.match_iou_thres == svc._default_thresholds().match_iou_thres
    assert "无标注" in (row.reason or "")


def test_load_attachments_images_jpeg_skips_missing_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(svc.settings, "MEDIA_ROOT", str(tmp_path))
    valid_path = tmp_path / "upload" / "valid.png"
    valid_path.parent.mkdir(parents=True, exist_ok=True)

    from PIL import Image

    Image.new("RGB", (32, 24), color="red").save(valid_path)
    rows = [
        type("AttachmentRow", (), {"id": 1, "path": "upload/valid.png"})(),
        type("AttachmentRow", (), {"id": 2, "path": "upload/missing.png"})(),
    ]

    images = svc._load_attachments_images_jpeg(attachments=rows, limit=4)
    assert len(images) == 1
