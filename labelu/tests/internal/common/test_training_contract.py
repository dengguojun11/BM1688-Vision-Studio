from labelu.internal.common.training_contract import (
    branch_identity_contract,
    branch_identity_sha256,
    build_selection_contract,
    canonical_selection_contract,
    selection_contract_json,
    selection_contract_sha256,
)
from labelu.internal.domain.models.model_series import ModelSeries, ModelVariant
from labelu.internal.domain.models.model_version import ModelVersion
from labelu.internal.domain.models.training_task import TrainingTask
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def test_selection_contract_hash_is_order_insensitive_for_set_like_fields():
    first = build_selection_contract(
        dataset_id=12,
        mode="label",
        task_ids=[102, 101],
        selected_label_ids=[7, 3],
        sample_conditions={"weather": ["rain", "rain"], "time": ["day"]},
        annotation_contract={"class_order": ["car", "person"]},
        recipe={"training_recipe_key": "yolo_detection_v1"},
    )
    second = build_selection_contract(
        dataset_id=12,
        mode="label",
        task_ids=[101, 102],
        selected_label_ids=[3, 7],
        sample_conditions={"time": ["day"], "weather": ["rain"]},
        annotation_contract={"class_order": ["car", "person"]},
        recipe={"training_recipe_key": "yolo_detection_v1"},
    )

    assert selection_contract_sha256(first) == selection_contract_sha256(second)
    assert selection_contract_json(first) == selection_contract_json(second)
    assert first["global_conditions"] == [
        {"dimension": "time", "values": ["day"]},
        {"dimension": "weather", "values": ["rain"]},
    ]


def test_class_order_remains_part_of_branch_identity():
    base = build_selection_contract(
        dataset_id=12,
        mode="label",
        selected_label_ids=[3, 7],
        annotation_contract={"class_order": ["car", "person"]},
    )
    reordered = canonical_selection_contract(
        {
            **base,
            "annotation_contract": {"class_order": ["person", "car"]},
        }
    )
    assert selection_contract_sha256(base) != selection_contract_sha256(reordered)


def test_snapshot_refresh_keeps_branch_identity_but_changes_evidence_hash():
    first = build_selection_contract(
        dataset_id=12,
        dataset_ids=[12, 19],
        dataset_version_id=44,
        mode="label",
        task_ids=[101],
        selected_label_ids=[3, 7],
        sample_conditions={"weather": ["rain"]},
        annotation_contract={"class_order": ["car", "person"]},
        recipe={"training_recipe_key": "yolo_detection_v1"},
    )
    refreshed = build_selection_contract(
        dataset_id=12,
        dataset_ids=[12, 19],
        dataset_version_id=45,
        mode="label",
        task_ids=[101],
        selected_label_ids=[3, 7],
        sample_conditions={"weather": ["rain"]},
        annotation_contract={"class_order": ["car", "person"]},
        recipe={"training_recipe_key": "yolo_detection_v1"},
    )

    assert selection_contract_sha256(first) != selection_contract_sha256(refreshed)
    assert branch_identity_sha256(first) == branch_identity_sha256(refreshed)
    assert "dataset_version_id" not in branch_identity_contract(first)["dataset_scope"]


def test_series_contract_columns_are_registered_on_lineage_models():
    assert hasattr(ModelSeries, "variants")
    assert hasattr(ModelVariant, "series_id")
    for model in (ModelVersion, YoloTrainJob, TrainingTask):
        assert hasattr(model, "series_id")
        assert hasattr(model, "variant_id")
    assert hasattr(YoloTrainJob, "selection_contract_json")
    assert hasattr(YoloTrainJob, "parent_model_version_id")

