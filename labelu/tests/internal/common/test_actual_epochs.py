import json

from labelu.internal.common.yolo_training_metrics import actual_epochs_for_train_job


def test_actual_epochs_from_metrics_summary():
    summary = json.dumps(
        {"final_metrics": {"epoch": 42, "map50": 0.5, "map50_95": 0.3}}
    )
    assert (
        actual_epochs_for_train_job(
            job_id=1,
            weights_path=None,
            metrics_summary=summary,
        )
        == 42
    )


def test_actual_epochs_missing_returns_none():
    assert (
        actual_epochs_for_train_job(
            job_id=999999,
            weights_path=None,
            metrics_summary=None,
        )
        is None
    )
