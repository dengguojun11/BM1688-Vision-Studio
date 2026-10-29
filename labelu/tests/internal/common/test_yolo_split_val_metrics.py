"""测试集指标解析与 val/test 对比。"""
from labelu.internal.common.yolo_training_metrics import (
    build_val_test_comparison,
    dataset_counts_from_metrics_summary,
    read_test_val_metrics_from_summary,
    resolve_job_sample_counts,
)


def test_dataset_counts_from_metrics_summary():
    raw = '{"dataset": {"train": 10, "val": 3, "test": 2}}'
    ds = dataset_counts_from_metrics_summary(raw)
    assert ds["train"] == 10
    assert ds["val"] == 3
    assert ds["test"] == 2


def test_resolve_job_sample_counts_from_db_and_metrics():
    raw = '{"dataset": {"train": 10, "val": 3, "test": 2}}'
    sc = resolve_job_sample_counts(
        job_id=999999,
        metrics_summary=raw,
        train_sample_count=10,
        val_sample_count=3,
    )
    assert sc["train"] == 10
    assert sc["val"] == 3
    assert sc["test"] == 2
    assert sc["total"] == 15


def test_test_val_metrics_from_metrics_summary():
    raw = '{"test_val_metrics": {"status": "ok", "map50": 0.93, "map50_95": 0.72}}'
    tvm = read_test_val_metrics_from_summary(raw)
    assert tvm["status"] == "ok"
    assert tvm["map50"] == 0.93


def test_build_val_test_comparison_delta():
    cmp = build_val_test_comparison(
        {"map50": 0.928, "map50_95": 0.65},
        {"map50": 0.933, "map50_95": 0.68},
    )
    assert cmp is not None
    assert abs(cmp["delta_map50"] - 0.005) < 1e-6
    assert "mAP50" in (cmp.get("summary_line") or "")
