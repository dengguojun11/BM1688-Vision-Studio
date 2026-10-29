import json

from labelu.internal.common.config import settings
from labelu.internal.common.yolo_training_metrics import training_stop_info


def _summary(*, actual_epoch: int, best_epoch: int) -> str:
    return json.dumps(
        {
            "final_metrics": {"epoch": actual_epoch, "map50_95": 0.51},
            "best_epoch_metrics": {"epoch": best_epoch, "map50_95": 0.56},
        }
    )


def test_training_stop_info_identifies_ultralytics_early_stopping():
    info = training_stop_info(
        job_id=323,
        status="COMPLETED",
        configured_epochs=100,
        hyperparams={"patience": 20},
        weights_path=None,
        metrics_summary=_summary(actual_epoch=28, best_epoch=8),
    )

    assert info == {
        "configured_epochs": 100,
        "actual_epochs": 28,
        "best_epoch": 8,
        "patience": 20,
        "early_stopped": True,
        "stop_reason": "early_stopping",
        "stop_reason_summary": "正常早停：实际 28/100 轮，最佳第 8 轮，连续 20 轮未提升",
    }


def test_training_stop_info_does_not_label_full_run_as_early_stopped():
    info = training_stop_info(
        job_id=324,
        status="COMPLETED",
        configured_epochs=100,
        hyperparams={"patience": 30},
        weights_path=None,
        metrics_summary=_summary(actual_epoch=100, best_epoch=70),
    )

    assert info["early_stopped"] is False
    assert info["stop_reason"] is None
    assert info["stop_reason_summary"] is None


def test_training_stop_info_reads_historical_patience_from_args_yaml(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    train_dir = tmp_path / "yolo_train" / "job-292" / "train"
    weights = train_dir / "weights" / "best.pt"
    weights.parent.mkdir(parents=True)
    weights.write_bytes(b"weights")
    (train_dir / "args.yaml").write_text("epochs: 50\npatience: 15\n", encoding="utf-8")

    info = training_stop_info(
        job_id=292,
        status="COMPLETED",
        configured_epochs=50,
        hyperparams={},
        weights_path=str(weights),
        metrics_summary=_summary(actual_epoch=25, best_epoch=10),
    )

    assert info["patience"] == 15
    assert info["early_stopped"] is True
