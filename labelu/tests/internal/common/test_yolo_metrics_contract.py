from __future__ import annotations

import json

from labelu.internal.common.yolo_training_metrics import (
    authoritative_val_metrics_from_summary,
    auto_training_evaluation_metrics_from_summary,
    metrics_contract_from_summary,
    parse_results_csv_best_metrics,
    parse_results_csv_final_metrics,
    parse_results_csv_history,
)
from labelu.internal.common.yolo_ultralytics_split_val import (
    merge_test_val_into_metrics_summary,
    merge_val_per_class_into_metrics_summary,
)


def test_results_csv_keeps_last_epoch_and_selects_best_epoch(tmp_path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    (train_dir / "results.csv").write_text(
        "\n".join(
            [
                "epoch,metrics/mAP50(B),metrics/mAP50-95(B),metrics/precision(B),metrics/recall(B)",
                "47,0.71,0.48784,0.75,0.66",
                "48,0.73,0.50151,0.77,0.68",
                "70,0.69,0.45467,0.72,0.62",
            ]
        ),
        encoding="utf-8",
    )

    best = parse_results_csv_best_metrics(train_dir)
    last = parse_results_csv_final_metrics(train_dir)

    assert best is not None
    assert best["epoch"] == 48
    assert best["map50_95"] == 0.50151
    assert best["selection_metric"] == "map50_95"
    assert last is not None
    assert last["epoch"] == 70
    assert last["map50_95"] == 0.45467


def test_results_csv_history_contains_losses_and_derived_fitness(tmp_path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    (train_dir / "results.csv").write_text(
        "\n".join(
            [
                "epoch,time,train/box_loss,train/cls_loss,train/dfl_loss,metrics/precision(B),metrics/recall(B),metrics/mAP50(B),metrics/mAP50-95(B),val/box_loss,val/cls_loss,val/dfl_loss,lr/pg0",
                "1,12.5,1.2,0.8,0.9,0.7,0.6,0.8,0.5,1.1,0.9,0.95,0.001",
                "2,25.0,1.0,0.6,0.8,0.75,0.65,0.82,0.52,1.0,0.7,0.85,0.0008",
            ]
        ),
        encoding="utf-8",
    )

    history = parse_results_csv_history(train_dir)

    assert [item["epoch"] for item in history] == [1, 2]
    assert history[0]["train_box_loss"] == 1.2
    assert history[1]["val_dfl_loss"] == 0.85
    assert history[1]["fitness"] == 0.55
    assert history[1]["learning_rate"] == 0.0008


def test_metrics_contract_prefers_best_pt_and_keeps_legacy_fallback() -> None:
    versioned = json.dumps(
        {
            "metrics_contract_version": 2,
            "final_metrics": {"map50_95": 0.45},
            "last_epoch_metrics": {"epoch": 70, "map50_95": 0.45},
            "best_epoch_metrics": {"epoch": 48, "map50_95": 0.49},
            "best_pt_val_metrics": {
                "status": "ok",
                "weights": "best.pt",
                "map50_95": 0.48,
            },
        }
    )

    contract = metrics_contract_from_summary(versioned)

    assert contract["version"] == 2
    assert contract["authoritative_source"] == "best_pt_val_metrics"
    assert contract["authoritative_val_metrics"]["map50_95"] == 0.48
    assert authoritative_val_metrics_from_summary(versioned)["map50_95"] == 0.48
    assert contract["legacy_fallback"] is False

    legacy = json.dumps({"final_metrics": {"map50_95": 0.41}})
    legacy_contract = metrics_contract_from_summary(legacy)
    assert legacy_contract["version"] == 1
    assert legacy_contract["authoritative_source"] == "final_metrics"
    assert legacy_contract["legacy_fallback"] is True


def test_historical_metrics_contract_uses_results_csv_best_epoch(tmp_path) -> None:
    train_dir = tmp_path / "train"
    train_dir.mkdir()
    (train_dir / "results.csv").write_text(
        "\n".join(
            [
                "epoch,metrics/mAP50(B),metrics/mAP50-95(B),metrics/precision(B),metrics/recall(B)",
                "48,0.73,0.48784,0.77,0.68",
                "70,0.69,0.45467,0.72,0.62",
            ]
        ),
        encoding="utf-8",
    )
    legacy = json.dumps({"final_metrics": {"epoch": 70, "map50_95": 0.45467}})

    contract = metrics_contract_from_summary(legacy, train_dir=train_dir)

    assert contract["authoritative_source"] == "results_csv_best_epoch"
    assert contract["authoritative_val_metrics"]["epoch"] == 48
    assert contract["authoritative_val_metrics"]["map50_95"] == 0.48784
    assert contract["best_epoch_metrics"]["epoch"] == 48
    assert contract["last_epoch_metrics"]["epoch"] == 70
    assert contract["legacy_fallback"] is True


def test_metrics_contract_uses_auto_training_overall_recall_as_last_quality_fallback() -> None:
    raw = json.dumps(
        {
            "auto_training_evaluation": {
                "overall_metrics": {"overall_recall": 0.9876},
                "decision_preview": {"overall_recall_threshold": 0.95},
            }
        }
    )

    evidence = auto_training_evaluation_metrics_from_summary(raw)
    contract = metrics_contract_from_summary(raw)

    assert evidence is not None
    assert evidence["overall_recall"] == 0.9876
    assert evidence["overall_recall_threshold"] == 0.95
    assert contract["authoritative_source"] == "auto_training_evaluation"
    assert contract["authoritative_val_metrics"]["overall_recall"] == 0.9876
    assert authoritative_val_metrics_from_summary(raw)["overall_recall"] == 0.9876


def test_metrics_contract_rejects_invalid_auto_training_overall_recall() -> None:
    for value in (None, "not-a-number", "NaN", "Infinity", -0.01, 1.01, True):
        raw = json.dumps(
            {"auto_training_evaluation": {"overall_metrics": {"overall_recall": value}}}
        )
        contract = metrics_contract_from_summary(raw)
        assert contract["authoritative_source"] == "none"
        assert contract["authoritative_val_metrics"] is None


def test_metric_merges_never_truncate_json() -> None:
    large_summary = json.dumps({"notes": "x" * 9000})
    merged_val = merge_val_per_class_into_metrics_summary(
        large_summary,
        {
            "status": "ok",
            "split": "val",
            "map50": 0.8,
            "map50_95": 0.5,
            "per_class_metrics": [{"label": "person", "recall": 0.7}],
        },
    )
    merged_test = merge_test_val_into_metrics_summary(
        merged_val,
        {"status": "ok", "split": "test", "map50_95": 0.4},
    )

    parsed = json.loads(merged_test)
    assert len(merged_test) > 8000
    assert parsed["best_pt_val_metrics"]["weights"] == "best.pt"
    assert parsed["test_val_metrics"]["map50_95"] == 0.4
