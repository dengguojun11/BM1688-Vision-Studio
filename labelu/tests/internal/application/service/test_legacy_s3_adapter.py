from labelu.internal.application.command.strategy_task_integration import (
    CreateStrategyTaskIntegrationCommand,
)
from labelu.internal.application.service import experiment_dataset_import as import_service
from labelu.internal.application.service import strategy_task_integration as strategy_service
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import LabelUException


def _command(**overrides):
    payload = {
        "external_task_id": "legacy-bridge-1",
        "task_name": "旧策略回流",
        "task_type": "threshold_experiment",
        "dataset": {
            "s3_uri": "s3://bucket/strategy/legacy-bridge-1/",
            "image_prefix": "images/",
            "metadata_key": "metadata.json",
        },
        "labeling": {"labels": ["Helmet", "helmet"]},
        "target": {
            "device_type": "BM1688",
            "event_type": "helmet",
            "model_id": "aicm-1001",
            "scene_type": "road",
            "strategy_id": "strategy-1",
        },
        "experiment": {
            "data_window": {"start_time": "2026-09-01", "end_time": "2026-09-02"}
        },
    }
    payload.update(overrides)
    return CreateStrategyTaskIntegrationCommand.model_validate(payload)


def test_legacy_command_maps_to_stable_async_import_contract():
    translated = strategy_service._legacy_to_experiment_command(_command())

    assert translated.package_id.startswith("legacy:")
    assert translated.source_external_task_id == "legacy-bridge-1"
    assert translated.dataset.bucket == "bucket"
    assert translated.dataset.prefix == "strategy/legacy-bridge-1/"
    assert translated.dataset.image_prefix == "strategy/legacy-bridge-1/images/"
    assert translated.dataset.metadata_key == "strategy/legacy-bridge-1/metadata.json"
    assert translated.target.model_id == "aicm-1001"
    assert translated.dataset_date == "2026-09-01"
    assert translated.window_end == "2026-09-02"
    assert translated.labeling.labels == ["helmet"]


def test_legacy_missing_window_is_deterministic_unknown_sentinel():
    translated = strategy_service._legacy_to_experiment_command(_command(experiment={}))

    assert translated.dataset_date == "unknown"
    assert translated.window_start == "unknown"
    assert translated.window_end == "unknown"


def test_legacy_external_task_id_does_not_split_aggregation_key():
    first = strategy_service._legacy_to_experiment_command(
        _command(external_task_id="legacy-task-a")
    )
    second = strategy_service._legacy_to_experiment_command(
        _command(external_task_id="legacy-task-b")
    )

    first_key, first_identity = import_service._aggregation_key(first)
    second_key, second_identity = import_service._aggregation_key(second)

    assert first_key == second_key
    assert first_identity == second_identity
    assert first.source_external_task_id == "legacy-task-a"
    assert second.source_external_task_id == "legacy-task-b"


def test_new_metadata_parser_accepts_legacy_images_and_flat_boxes():
    metadata = {
        "images": [
            {
                "image": "images/helmet.jpg",
                "width": 1920,
                "height": 1080,
                "target_boxes": [
                    {"label": "helmet", "x": 10, "y": 20, "width": 30, "height": 40}
                ],
            }
        ]
    }

    samples = import_service._metadata_samples(metadata)
    assert import_service._sample_image_name(samples[0]) == "helmet.jpg"
    boxes = import_service._sample_boxes(samples[0])
    assert boxes[0]["class_name"] == "helmet"
    assert (boxes[0]["x1"], boxes[0]["y1"], boxes[0]["x2"], boxes[0]["y2"]) == (
        10,
        20,
        40,
        60,
    )


def test_new_metadata_parser_accepts_root_sample_array():
    samples = import_service._metadata_samples([{"file_name": "a.jpg"}])
    assert len(samples) == 1
    assert import_service._sample_image_name(samples[0]) == "a.jpg"


def test_legacy_label_case_is_normalized_for_new_worker_contract():
    sample = import_service._metadata_samples(
        [{"image_name": "a.jpg", "target_boxes": [{"label": "Helmet", "x": 1, "y": 2, "width": 3, "height": 4}]}]
    )[0]
    assert import_service._sample_boxes(sample)[0]["class_name"] == "helmet"


def test_retired_legacy_synchronous_import_is_fail_closed():
    """No internal caller may restore the pre-Worker S3 side effects."""
    command = _command()

    try:
        import asyncio

        asyncio.run(strategy_service._legacy_direct_create(None, cmd=command))
    except LabelUException as exc:
        assert exc.status_code == 410
        assert "统一第三方回流 Worker" in str(exc)
    else:
        raise AssertionError("retired synchronous import must fail closed")


def test_import_scheduler_fails_closed_when_worker_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED", False)
    try:
        import_service.schedule_import_job(123)
    except RuntimeError as exc:
        assert "dedicated worker" in str(exc)
    else:
        raise AssertionError("disabled worker must not fall back to Web execution")
