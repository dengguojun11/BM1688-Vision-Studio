from __future__ import annotations

import json

from labelu.internal.application.service.yolo_train_evaluation import (
    _dataset_composition_evidence,
    _metrics_summary_for_prompt,
    _structured_metrics_evidence,
)


def test_dataset_composition_evidence_uses_exported_counts(tmp_path) -> None:
    train_dir = tmp_path / "train"
    reports_dir = tmp_path / "reports"
    train_dir.mkdir()
    reports_dir.mkdir()
    (reports_dir / "export_summary.json").write_text(
        json.dumps(
            {
                "sample_composition": {
                    "positive_images": 80,
                    "negative_images": 20,
                    "negative_ratio": 0.2,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    evidence = _dataset_composition_evidence(train_dir)

    assert '"positive_images": 80' in evidence
    assert '"negative_images": 20' in evidence
    assert '"negative_ratio": 0.2' in evidence


def test_dataset_composition_evidence_forbids_guessing_when_missing(tmp_path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()

    evidence = _dataset_composition_evidence(train_dir)

    assert "禁止断言负样本偏少或偏多" in evidence
    assert "当前证据不足" in evidence


def test_structured_metrics_evidence_keeps_split_and_class_boundaries() -> None:
    evidence = _structured_metrics_evidence(
        json.dumps(
            {
                "dataset": {"train": 10, "val": 4, "test": 3},
                "classes": ["person", "helmet"],
                "class_instance_counts": [
                    {"index": 0, "label": "person", "train_instances": 8},
                    {"index": 1, "label": "helmet", "train_instances": 2},
                ],
                "final_metrics": {"map50": 0.8, "map50_95": 0.4},
                "last_epoch_metrics": {
                    "epoch": 70,
                    "map50": 0.8,
                    "map50_95": 0.4,
                },
                "best_epoch_metrics": {
                    "epoch": 48,
                    "map50": 0.85,
                    "map50_95": 0.5,
                },
                "best_pt_val_metrics": {
                    "status": "ok",
                    "weights": "best.pt",
                    "map50": 0.75,
                    "map50_95": 0.45,
                },
                "test_val_metrics": {"map50": 0.7, "map50_95": 0.3},
                "internal_only": "omit",
            },
            ensure_ascii=False,
        )
    )

    parsed = json.loads(evidence)
    assert parsed["dataset"] == {"train": 10, "val": 4, "test": 3}
    assert parsed["classes"] == ["person", "helmet"]
    assert parsed["metric_semantics"]["authoritative_source"] == ("best_pt_val_metrics")
    assert parsed["metric_semantics"]["legacy_fallback"] is False
    assert parsed["val_test_comparison"]["map50"]["val"] == 0.75
    assert parsed["val_test_comparison"]["map50"]["test_minus_val"] == -0.05
    assert "internal_only" in parsed["available_fields"]


def test_structured_metrics_evidence_uses_historical_best_epoch(tmp_path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    (train_dir / "results.csv").write_text(
        "\n".join(
            [
                "epoch,metrics/mAP50(B),metrics/mAP50-95(B),metrics/precision(B),metrics/recall(B)",
                "48,0.7679,0.48784,0.9057,0.5779",
                "70,0.6828,0.45467,0.8926,0.5227",
            ]
        ),
        encoding="utf-8",
    )

    evidence = _structured_metrics_evidence(
        json.dumps({"final_metrics": {"epoch": 70, "map50_95": 0.45467}}),
        train_dir=train_dir,
    )

    parsed = json.loads(evidence)
    assert parsed["metric_semantics"]["authoritative_source"] == (
        "results_csv_best_epoch"
    )
    assert parsed["authoritative_val_metrics"]["epoch"] == 48
    assert parsed["last_epoch_metrics"]["epoch"] == 70
    assert "尚未独立复验" in parsed["metric_semantics"]["note"]


def test_structured_metrics_evidence_supports_auto_training_recall_fallback() -> None:
    evidence = _structured_metrics_evidence(
        json.dumps(
            {
                "auto_training_evaluation": {
                    "overall_metrics": {"overall_recall": 0.9876}
                }
            },
            ensure_ascii=False,
        )
    )

    parsed = json.loads(evidence)
    assert parsed["metric_semantics"]["authoritative_source"] == (
        "auto_training_evaluation"
    )
    assert parsed["authoritative_val_metrics"]["overall_recall"] == 0.9876
    assert "整体召回率" in parsed["metric_semantics"]["note"]


def test_oversized_metrics_summary_is_replaced_with_valid_json() -> None:
    raw = json.dumps({"notes": "x" * 31_000})

    prompt_block = _metrics_summary_for_prompt(
        raw,
        structured_evidence=json.dumps({"parse_status": "ok"}),
    )

    parsed = json.loads(prompt_block)
    assert parsed["parse_status"] == "omitted_oversize"
    assert parsed["original_chars"] > 30_000
