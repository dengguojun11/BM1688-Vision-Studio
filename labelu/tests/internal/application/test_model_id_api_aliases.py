from labelu.internal.application.command.experiment_dataset_import import (
    ExperimentDatasetTargetPayload,
)
from labelu.internal.application.command.strategy_evolution import (
    StrategyEvolutionTargetPayload,
)
from labelu.internal.application.response.experiment_dataset_import import (
    ExperimentDatasetImportListItemResponse,
)
from labelu.internal.application.service.experiment_dataset_import import (
    _externalize_model_id_keys as externalize_dataset_model_ids,
)
from labelu.internal.application.service.strategy_evolution import (
    _externalize_model_id_keys as externalize_strategy_model_ids,
)


def test_strategy_target_accepts_camel_and_legacy_model_id_keys():
    common = {
        "device_type": "normal_camera",
        "event_type": "electrombileWithoutHelmet",
        "scene_type": "default",
        "strategy_id": "strategy-1",
    }

    camel = StrategyEvolutionTargetPayload(modelId="537", **common)
    legacy = StrategyEvolutionTargetPayload(model_id="537", **common)

    assert camel.model_id == "537"
    assert legacy.model_id == "537"
    assert camel.model_dump(by_alias=True)["modelId"] == "537"


def test_dataset_target_accepts_camel_and_legacy_model_id_keys():
    assert ExperimentDatasetTargetPayload(modelId="537").model_id == "537"
    assert ExperimentDatasetTargetPayload(model_id="537").model_id == "537"


def test_expert_feedback_responses_serialize_model_id_as_model_id():
    response = ExperimentDatasetImportListItemResponse(
        package_id="package-1",
        import_job_id=1,
        status="COMPLETED",
        dataset_date="2026-07-24",
        event_type="electrombileWithoutHelmet",
        model_id="537",
    )

    payload = response.model_dump(by_alias=True)
    assert payload["modelId"] == "537"
    assert "model_id" not in payload


def test_external_response_payloads_rename_nested_model_ids():
    source = {"target": {"model_id": "537"}, "source_model": {"model_id": "537"}}

    assert externalize_dataset_model_ids(source)["target"]["modelId"] == "537"
    strategy_payload = externalize_strategy_model_ids(source)
    assert strategy_payload["source_model"]["modelId"] == "537"
