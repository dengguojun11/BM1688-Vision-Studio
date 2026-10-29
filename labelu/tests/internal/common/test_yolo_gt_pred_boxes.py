"""逐图 GT vs Pred 框匹配。"""
from labelu.internal.common.yolo_gt_pred_boxes import (
    BBox,
    classify_image_issues,
    load_yolo_label_file,
    match_boxes_per_class,
    parse_sample_id_from_stem,
    summarize_image_issues_cn,
)


def test_parse_sample_id_from_stem():
    assert parse_sample_id_from_stem("12345_foo_bar") == 12345
    assert parse_sample_id_from_stem("abc") is None


def test_match_boxes_tp_fp_fn(tmp_path):
    label = tmp_path / "a.txt"
    label.write_text("0 0.5 0.5 0.2 0.2\n1 0.2 0.2 0.1 0.1\n", encoding="utf-8")
    gt = load_yolo_label_file(label, img_w=100, img_h=100)
    preds = [
        BBox(0, 40, 40, 60, 60, score=0.9),
        BBox(0, 5, 5, 15, 15, score=0.8),
    ]
    m = match_boxes_per_class(preds, gt, class_count=2, iou_threshold=0.5)
    assert m["tp"] == 1
    assert m["fp"] == 1
    assert m["fn"] == 1
    assert classify_image_issues(m) == ["fn", "fp"]
    cn = summarize_image_issues_cn(m, ["a", "b"])
    assert "人工标注" in cn["summary"]
    assert "模型输出" in cn["summary"]
    assert "漏检" in cn["summary"]
    assert "误检" in cn["summary"]
    assert len(cn["details"]) >= 2


def test_strict_consistent_match_rejects_box_that_only_passes_iou_05():
    gt = [BBox(0, 0, 0, 10, 10)]
    preds = [BBox(0, 1, 1, 11, 11, score=0.9)]

    loose = match_boxes_per_class(preds, gt, class_count=1, iou_threshold=0.5)
    strict = match_boxes_per_class(preds, gt, class_count=1, iou_threshold=0.8)

    assert loose["tp"] == 1
    assert strict["tp"] == 0
    assert strict["fp"] == 1
    assert strict["fn"] == 1
