"""训练过程日志：results.csv 末行格式化。"""
from pathlib import Path
from types import SimpleNamespace

from labelu.internal.common.yolo_training_metrics import (
    build_ai_provider_metric_rows,
    format_results_csv_epoch_log_line,
)


def test_default_provider_metrics_only_include_active_volcengine():
    rows = build_ai_provider_metric_rows({})

    assert [row["provider_key"] for row in rows] == ["volcengine"]
    assert rows[0]["has_report"] is False


def test_provider_metrics_preserve_existing_qwen_history():
    rows = build_ai_provider_metric_rows({
        "qwen_plus": SimpleNamespace(body="历史评估报告", error_message=None),
    })

    assert [row["provider_key"] for row in rows] == ["volcengine", "qwen_plus"]
    assert rows[1]["provider_label"] == "通义千问 Plus"
    assert rows[1]["has_report"] is True


def test_format_results_csv_epoch_log_line(tmp_path):
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    (train_dir / "results.csv").write_text(
        "epoch,metrics/mAP50(B),metrics/mAP50-95(B),metrics/precision(B),metrics/recall(B)\n"
        "1,0.5,0.4,0.6,0.7\n"
        "2,0.8687,0.8687,0.85,0.82\n",
        encoding="utf-8",
    )
    line = format_results_csv_epoch_log_line(train_dir, target_epochs=100)
    assert line is not None
    assert "epoch=2/100" in line
    assert "mAP50=0.8687" in line
    assert "mAP50-95=0.8687" in line


def test_format_results_csv_epoch_log_line_missing_file(tmp_path):
    assert format_results_csv_epoch_log_line(tmp_path / "train") is None
