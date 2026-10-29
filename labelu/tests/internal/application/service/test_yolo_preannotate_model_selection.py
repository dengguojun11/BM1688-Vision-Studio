from types import SimpleNamespace
import json
import sys

import pytest

from labelu.internal.application.service import yolo_preannotate
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.application.response.yolo_train import YoloTrainingValMetrics


def _task(name, preset, labels):
    return SimpleNamespace(
        id=name,
        name=name,
        config=json.dumps({
            "defaultRectPresetId": preset,
            "tools": [
                {
                    "tool": "rectTool",
                    "config": {
                        "attributes": [{"key": label, "value": label} for label in labels]
                    },
                }
            ],
        }),
    )


def test_list_models_for_task_returns_only_matching_models(monkeypatch):
    current_task = _task(10, "vehicle", ["car", "truck"])
    other_task = _task(11, "person", ["person"])
    user = SimpleNamespace(id=7)
    imported_match = SimpleNamespace(
        id=3, display_name="vehicle-import", category="vehicle"
    )
    imported_other = SimpleNamespace(
        id=4, display_name="person-import", category="person"
    )
    trained_match = SimpleNamespace(
        id=8, model_weights="vehicle.pt", imgsz=640
    )
    trained_other = SimpleNamespace(
        id=9, model_weights="person.pt", imgsz=640
    )

    monkeypatch.setattr(yolo_preannotate.crud_task, "get", lambda **_: current_task)
    monkeypatch.setattr(yolo_preannotate, "_assert_task_access", lambda *_: None)
    monkeypatch.setattr(yolo_preannotate, "is_task_superuser", lambda _: False)
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_imported_model,
        "public_owner_ids",
        lambda _: [],
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_imported_model,
        "public_owner_ids",
        lambda _: [],
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_imported_model,
        "list_for_user",
        lambda *args, **kwargs: ([imported_match, imported_other], 2),
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "_imported_model_label_signature",
        lambda row: ("car", "truck") if row.id == 3 else ("person",),
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_train_job,
        "list_completed_models_for_user",
        lambda *args, **kwargs: (
            [(trained_match, current_task), (trained_other, other_task)],
            2,
        ),
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "_training_val_metrics_for_job",
        lambda job: YoloTrainingValMetrics(
            map50_95=0.8123,
            map50=0.9345,
            precision=0.88,
            recall=0.79,
            fitness=0.821,
        ),
    )

    result = yolo_preannotate.list_models_for_task(
        db=object(), task_id=10, current_user=user
    )

    assert result.task_preset_slug == "vehicle"
    assert result.task_labels == ["car", "truck"]
    assert [(item.kind, item.id) for item in result.items] == [
        ("trained", 8),
        ("imported", 3),
    ]
    assert all(item.labels == ["car", "truck"] for item in result.items)
    assert result.items[0].recommended is True
    assert result.items[1].recommended is False
    assert result.items[0].training_val_metrics.map50_95 == 0.8123
    assert result.items[0].recommended_conf == 0.85
    assert result.items[1].training_val_metrics is None
    assert result.items[1].recommended_conf is None


@pytest.mark.parametrize(
    ("map50_95", "expected"),
    [(0.815, 0.85), (0.80, 0.80), (0.02, 0.05), (1.0, 1.0)],
)
def test_recommended_conf_rounds_up_to_conf_step(map50_95, expected):
    metrics = YoloTrainingValMetrics(map50_95=map50_95)
    assert yolo_preannotate._recommended_conf_for_metrics(metrics) == expected


def test_recommended_conf_is_missing_without_map_metric():
    assert yolo_preannotate._recommended_conf_for_metrics(None) is None
    assert yolo_preannotate._recommended_conf_for_metrics(YoloTrainingValMetrics()) is None


def test_imported_checkpoint_imgsz_prefers_wrapped_training_args(monkeypatch):
    checkpoint_model = SimpleNamespace(
        model=SimpleNamespace(args={"imgsz": 1280}),
        overrides={"imgsz": 640},
    )
    monkeypatch.setitem(
        sys.modules,
        "ultralytics",
        SimpleNamespace(YOLO=lambda _: checkpoint_model),
    )

    assert yolo_preannotate._model_imgsz_from_checkpoint("model-418.pt") == 1280


def test_imported_checkpoint_imgsz_falls_back_for_missing_or_invalid_metadata(monkeypatch):
    checkpoint_model = SimpleNamespace(
        model=SimpleNamespace(args={"imgsz": "invalid"}),
        overrides={},
    )
    monkeypatch.setitem(
        sys.modules,
        "ultralytics",
        SimpleNamespace(YOLO=lambda _: checkpoint_model),
    )

    assert yolo_preannotate._model_imgsz_from_checkpoint("legacy.pt") == 640
    assert yolo_preannotate._imported_model_imgsz(
        SimpleNamespace(id=3, weights_rel_path=None)
    ) == 640


def test_models_are_sorted_by_recommended_confidence(monkeypatch):
    current_task = _task(10, "vehicle", ["car"])
    user = SimpleNamespace(id=7)
    jobs = [
        SimpleNamespace(id=1, model_weights="low.pt", imgsz=640),
        SimpleNamespace(id=2, model_weights="high.pt", imgsz=640),
    ]
    monkeypatch.setattr(yolo_preannotate.crud_task, "get", lambda **_: current_task)
    monkeypatch.setattr(yolo_preannotate, "_assert_task_access", lambda *_: None)
    monkeypatch.setattr(yolo_preannotate, "is_task_superuser", lambda _: False)
    monkeypatch.setattr(yolo_preannotate.crud_yolo_imported_model, "public_owner_ids", lambda _: [])
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_imported_model,
        "list_for_user",
        lambda *args, **kwargs: ([], 0),
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_train_job,
        "list_completed_models_for_user",
        lambda *args, **kwargs: ([(job, current_task) for job in jobs], 2),
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "_training_val_metrics_for_job",
        lambda job: YoloTrainingValMetrics(map50_95=0.61 if job.id == 1 else 0.91),
    )

    result = yolo_preannotate.list_models_for_task(
        db=object(), task_id=10, current_user=user
    )

    assert [(item.id, item.recommended_conf) for item in result.items] == [
        (2, 0.95),
        (1, 0.65),
    ]


def test_model_signature_mismatch_is_rejected():
    task = _task(10, "vehicle", ["car", "truck"])

    with pytest.raises(LabelUException) as exc_info:
        yolo_preannotate._assert_model_matches_task(
            task,
            {"labels": ["person"]},
        )

    assert exc_info.value.code == ErrorCode.CODE_57033_YOLO_PREANNOTATE_MODEL_MISMATCH.value[0]


def test_model_signature_match_is_allowed():
    task = _task(10, "vehicle", ["car", "truck"])

    yolo_preannotate._assert_model_matches_task(
        task,
        {"labels": ["car", "truck"]},
    )


def test_model_with_any_shared_label_is_allowed_for_manual_preannotation():
    task = _task(10, "vehicle", ["car", "truck"])

    yolo_preannotate._assert_model_matches_task(
        task,
        {"labels": ["truck", "bus"]},
    )


def test_generic_person_model_label_maps_to_pedestrian_task_label():
    task_contract = {
        "ordered_labels": ["pedestrian", "person_electricbike"],
        "labels": ("pedestrian", "person_electricbike"),
    }

    matched, missing, extra = yolo_preannotate._model_task_label_coverage(
        task_contract,
        ("person", "person_electricbike"),
    )

    assert matched == ["pedestrian", "person_electricbike"]
    assert missing == []
    assert extra == []
    assert yolo_preannotate._build_detection_label_aliases(
        ["person", "person_electricbike"],
        {"pedestrian", "person_electricbike"},
    ) == {"person": "pedestrian"}


def test_generic_person_model_label_matches_task_pedestrian_alias():
    task = _task(10, "person", ["pedestrian", "person_electricbike"])

    yolo_preannotate._assert_model_matches_task(
        task,
        {"labels": ["person", "person_electricbike"]},
    )

    matched, missing, extra = yolo_preannotate._model_task_label_coverage(
        {"ordered_labels": ["pedestrian", "person_electricbike"]},
        ("person", "person_electricbike"),
    )
    assert matched == ["pedestrian", "person_electricbike"]
    assert missing == []
    assert extra == []


def test_list_models_for_task_includes_partial_label_model(monkeypatch):
    current_task = _task(10, "vehicle", ["car", "truck"])
    user = SimpleNamespace(id=7)
    imported_partial = SimpleNamespace(
        id=5, display_name="mixed-import", category="vehicle"
    )

    monkeypatch.setattr(yolo_preannotate.crud_task, "get", lambda **_: current_task)
    monkeypatch.setattr(yolo_preannotate, "_assert_task_access", lambda *_: None)
    monkeypatch.setattr(yolo_preannotate, "is_task_superuser", lambda _: False)
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_imported_model,
        "public_owner_ids",
        lambda _: [],
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_imported_model,
        "list_for_user",
        lambda *args, **kwargs: ([imported_partial], 1),
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "_imported_model_label_signature",
        lambda _: ("truck", "bus"),
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_train_job,
        "list_completed_models_for_user",
        lambda *args, **kwargs: ([], 0),
    )

    result = yolo_preannotate.list_models_for_task(
        db=object(), task_id=10, current_user=user
    )

    assert [(item.kind, item.id) for item in result.items] == [("imported", 5)]
    assert result.items[0].matched_labels == ["truck"]
    assert result.items[0].missing_task_labels == ["car"]
    assert result.items[0].extra_model_labels == ["bus"]


def test_resolve_auto_candidate_uses_frozen_training_labels(monkeypatch):
    current_task = _task(10, "current", ["helmet"])
    source_task = _task(11, "source", ["person"])
    user = SimpleNamespace(id=7)
    job = SimpleNamespace(
        id=21,
        task_id=11,
        status="COMPLETED",
        job_type="yolo_detection",
        weights_path="frozen.pt",
        imgsz=640,
        class_names_json=json.dumps(["helmet"]),
    )

    monkeypatch.setattr(yolo_preannotate, "is_task_superuser", lambda _: False)
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_train_job,
        "list_completed_models_for_user",
        lambda *args, **kwargs: ([(job, source_task)], 1),
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "resolve_weights_path",
        lambda value: value,
    )

    candidate = yolo_preannotate.resolve_auto_candidate_for_task(
        db=object(),
        task=current_task,
        current_user=user,
    )

    assert candidate is not None
    assert candidate["model_id"] == 21
    assert candidate["rect_labels"] == ["helmet"]


def test_resolve_auto_candidate_accepts_partial_label_overlap(monkeypatch):
    current_task = _task(10, "vehicle", ["car", "truck"])
    source_task = _task(11, "source", ["car"])
    user = SimpleNamespace(id=7)
    job = SimpleNamespace(
        id=22,
        task_id=11,
        status="COMPLETED",
        job_type="yolo_detection",
        weights_path="partial.pt",
        imgsz=640,
        class_names_json=json.dumps(["car"]),
        label_set_signature=None,
        class_order_signature=None,
    )

    monkeypatch.setattr(yolo_preannotate, "is_task_superuser", lambda _: False)
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_train_job,
        "list_completed_models_for_user",
        lambda *args, **kwargs: ([(job, source_task)], 1),
    )
    monkeypatch.setattr(yolo_preannotate, "resolve_weights_path", lambda value: value)

    candidate = yolo_preannotate.resolve_auto_candidate_for_task(
        db=object(),
        task=current_task,
        current_user=user,
    )

    assert candidate is not None
    assert candidate["model_id"] == 22
    assert candidate["rect_labels"] == ["car"]


def test_resolve_auto_candidate_imported_uses_checkpoint_imgsz(monkeypatch):
    current_task = _task(10, "person", ["person"])
    user = SimpleNamespace(id=7)
    imported = SimpleNamespace(
        id=418,
        weights_rel_path="model-418.pt",
        display_name="task-335-job-418-best",
        category="person",
    )

    monkeypatch.setattr(yolo_preannotate, "is_task_superuser", lambda _: False)
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_imported_model,
        "public_owner_ids",
        lambda _: [],
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_train_job,
        "list_completed_models_for_user",
        lambda *args, **kwargs: ([], 0),
    )
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_imported_model,
        "list_for_user",
        lambda *args, **kwargs: ([imported], 1),
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "_imported_model_label_signature",
        lambda row: ("person",),
    )
    monkeypatch.setattr(yolo_preannotate, "_imported_model_imgsz", lambda row: 1280)
    monkeypatch.setattr(yolo_preannotate, "resolve_weights_path", lambda value: value)

    candidate = yolo_preannotate.resolve_auto_candidate_for_task(
        db=object(),
        task=current_task,
        current_user=user,
    )

    assert candidate is not None
    assert candidate["model_id"] == 418
    assert candidate["imgsz"] == 1280


def test_resolve_auto_candidate_ignores_preset_and_prefers_quality(monkeypatch):
    current_task = _task(10, "current-preset", ["helmet"])
    source_same_preset = _task(11, "current-preset", ["helmet"])
    source_other_preset = _task(12, "other-preset", ["helmet"])
    user = SimpleNamespace(id=7)
    low = SimpleNamespace(
        id=31,
        weights_path="low.pt",
        imgsz=640,
        job_type="yolo_detection",
        class_names_json=json.dumps(["helmet"]),
        label_set_signature=None,
        class_order_signature=None,
    )
    high = SimpleNamespace(
        id=32,
        weights_path="high.pt",
        imgsz=640,
        job_type="yolo_detection",
        class_names_json=json.dumps(["helmet"]),
        label_set_signature=None,
        class_order_signature=None,
    )

    monkeypatch.setattr(yolo_preannotate, "is_task_superuser", lambda _: False)
    monkeypatch.setattr(
        yolo_preannotate.crud_yolo_train_job,
        "list_completed_models_for_user",
        lambda *args, **kwargs: ([(low, source_same_preset), (high, source_other_preset)], 2),
    )
    monkeypatch.setattr(yolo_preannotate, "resolve_weights_path", lambda value: value)
    monkeypatch.setattr(
        yolo_preannotate,
        "_training_val_metrics_for_job",
        lambda job: YoloTrainingValMetrics(map50_95=0.45 if job.id == 31 else 0.90),
    )

    candidate = yolo_preannotate.resolve_auto_candidate_for_task(
        db=object(),
        task=current_task,
        current_user=user,
    )

    assert candidate is not None
    assert candidate["model_id"] == 32


def test_published_model_resolution_rejects_direct_export_fallback(monkeypatch):
    from labelu.internal.application.service import strategy_evolution

    monkeypatch.setattr(
        strategy_evolution,
        "_resolve_export_id_from_model_id",
        lambda db, model_id: (
            77,
            {"model_id": model_id, "resolved_by": "direct_export_id"},
        ),
    )

    result = yolo_preannotate.resolve_published_model(object(), "77")

    assert result["ready"] is False
    assert result["reason"] == "published_model_mapping_missing"
    assert result["export_id"] is None


def test_requested_candidate_keeps_external_published_model_binding(monkeypatch):
    task = _task(10, "vehicle", ["car"])
    user = SimpleNamespace(id=7)
    monkeypatch.setattr(
        yolo_preannotate,
        "resolve_published_model",
        lambda db, published_model_id: {
            "ready": True,
            "model_id": published_model_id,
            "source_train_job_id": 21,
            "export_id": 99,
            "weights_path": "published-541.pt",
            "imgsz": 960,
            "labels": ["car"],
            "job_type": "yolo_detection",
        },
    )
    monkeypatch.setattr(
        yolo_preannotate,
        "_task_contract_descriptor",
        lambda db, task: {
            "labels": ("car",),
            "ordered_labels": ["car"],
            "label_set_signature": None,
            "class_order_signature": None,
        },
    )
    monkeypatch.setattr(yolo_preannotate, "resolve_weights_path", lambda value: value)

    candidate = yolo_preannotate.resolve_requested_candidate_for_task(
        db=object(),
        task=task,
        current_user=user,
        published_model_id="541",
    )

    assert candidate is not None
    assert candidate["model_id"] == 21
    assert candidate["published_model_id"] == "541"
    assert candidate["export_id"] == 99
    assert candidate["weights_path"] == "published-541.pt"
    assert candidate["imgsz"] == 960
