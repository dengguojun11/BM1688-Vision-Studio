import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_user
from labelu.internal.adapter.persistence import crud_model_version
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.application.service.training_iteration import (
    build_iteration_preview,
    build_previous_version_comparison,
    resolve_auto_iteration_weights,
)
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.config import settings
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.yolo_eval_report import YoloEvalReport
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _task(db: Session, name: str = "同系列训练") -> Task:
    row = Task(
        name=name,
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
    )
    db.add(row)
    db.flush()
    return row


def _job(
    db: Session,
    task: Task,
    *,
    label_signature: str = "labels-a",
    order_signature: str = "order-a",
    category: str = "yolo_detection",
    status: str = "COMPLETED",
    weights_path: str | None = "runs/best.pt",
    materialize_weights: bool = True,
    metrics: dict | None = None,
) -> YoloTrainJob:
    row = YoloTrainJob(
        task_id=task.id,
        created_by=1,
        model_weights="yolo11n.pt",
        weights_path=weights_path,
        epochs=50,
        imgsz=640,
        category=category,
        job_type="yolo_detection",
        status=status,
        training_recipe_key="yolov11_detection",
        training_recipe_version=1,
        label_set_signature=label_signature,
        class_order_signature=order_signature,
        metrics_summary=(
            json.dumps({"best_pt_val_metrics": metrics})
            if metrics is not None
            else None
        ),
    )
    db.add(row)
    db.flush()
    if materialize_weights and weights_path:
        path = Path(weights_path)
        if not path.is_absolute():
            path = Path(settings.MEDIA_ROOT) / path
        media_root = Path(settings.MEDIA_ROOT).resolve()
        resolved = path.resolve()
        if str(resolved).startswith(str(media_root)):
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_bytes(b"pytest-placeholder-weights")
    return row


def _auto_command() -> CreateYoloTrainJobCommand:
    return CreateYoloTrainJobCommand(
        weights_source="auto",
        model_weights="yolo11n.pt",
        training_recipe_key="yolov11_detection",
        training_recipe_version=1,
        category="yolo_detection",
    )


def test_auto_iteration_uses_latest_compatible_completed_weights(db: Session):
    task = _task(db)
    compatible = _job(db, task)
    _job(db, task, label_signature="labels-b", order_signature="order-b")
    _job(db, task, status="FAILED")
    db.commit()

    resolved = resolve_auto_iteration_weights(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert resolved.weights_source == "trained"
    assert resolved.weights_ref_id == compatible.id
    assert resolved.model_weights == f"trained:{compatible.id}"


def test_auto_iteration_prefers_parent_with_matching_sample_conditions(
    db: Session,
):
    task = _task(db, "全局条件迭代")
    matching = _job(db, task)
    matching.sample_conditions_json = json.dumps({"weather": ["rain"]})
    newer_unfiltered = _job(db, task)
    newer_unfiltered.sample_conditions_json = json.dumps({"weather": ["sunny"]})
    db.commit()

    command = _auto_command().copy(update={"sample_conditions": {"weather": ["rain"]}})
    resolved = resolve_auto_iteration_weights(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=command,
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert resolved.weights_source == "trained"
    assert resolved.weights_ref_id == matching.id


def test_auto_iteration_skips_completed_parent_when_checkpoint_was_deleted(
    db: Session,
):
    task = _task(db, "缺失权重候选")
    older = _job(db, task, weights_path="runs/iteration-older/best.pt")
    newer = _job(db, task, weights_path="runs/iteration-newer/best.pt")
    Path(settings.MEDIA_ROOT, newer.weights_path).unlink()
    db.commit()

    resolved = resolve_auto_iteration_weights(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert resolved.weights_source == "trained"
    assert resolved.weights_ref_id == older.id


def test_auto_iteration_scans_compatible_parent_beyond_previous_row_limit(
    db: Session,
):
    task = _task(db, "完整扫描历史父模型")
    compatible = _job(db, task, weights_path="runs/full-scan/compatible.pt")
    for index in range(205):
        _job(
            db,
            task,
            label_signature=f"labels-{index}",
            order_signature=f"order-{index}",
            weights_path=f"runs/full-scan/incompatible-{index}.pt",
        )
    db.commit()

    resolved = resolve_auto_iteration_weights(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert resolved.weights_source == "trained"
    assert resolved.weights_ref_id == compatible.id


def test_iteration_preview_rejects_parent_with_missing_checkpoint_as_conflict(
    db: Session,
):
    task = _task(db, "父模型权重已删除")
    parent = _job(
        db,
        task,
        weights_path="runs/missing-parent/best.pt",
        materialize_weights=False,
    )
    db.commit()

    with pytest.raises(LabelUException) as error:
        build_iteration_preview(
            db,
            current_user=crud_user.get(db, 1),
            task_id=task.id,
            cmd=_auto_command(),
            parent_job_id=parent.id,
        )

    assert error.value.status_code == 409
    assert "权重文件不存在" in error.value.msg


def test_auto_iteration_matches_legacy_recipe_alias_when_request_is_canonical(
    db: Session,
):
    task = _task(db, "历史配方别名")
    parent = _job(db, task)
    db.commit()

    command = _auto_command().copy(
        update={"training_recipe_key": "yolo_detection_v1"}
    )
    resolved = resolve_auto_iteration_weights(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=command,
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert resolved.weights_source == "trained"
    assert resolved.weights_ref_id == parent.id


def test_auto_iteration_reads_recipe_from_legacy_nested_manifest(db: Session):
    task = _task(db, "嵌套配方血缘")
    parent = _job(db, task)
    parent.training_recipe_key = None
    parent.training_recipe_version = None
    parent.hyperparams_json = json.dumps(
        {
            "dataset_manifest": {
                "training_recipe_key": "yolov11_detection",
                "training_recipe_version": 1,
            }
        }
    )
    db.commit()

    command = _auto_command().copy(
        update={"training_recipe_key": "yolo_detection_v1"}
    )
    preview = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=command,
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert preview.weights_source == "trained"
    assert preview.weights_ref_id == parent.id
    assert preview.parent is not None
    assert preview.parent.training_recipe_key == "yolov11_detection"
    assert preview.parent.training_recipe_version == 1


def test_explicit_iteration_accepts_canonical_request_for_legacy_parent(
    db: Session,
):
    task = _task(db, "显式别名父模型")
    parent = _job(db, task)
    db.commit()

    command = _auto_command().copy(
        update={
            "weights_source": "trained",
            "weights_ref_id": parent.id,
            "training_recipe_key": "yolo_detection_v1",
        }
    )
    preview = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=command,
        parent_job_id=parent.id,
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert preview.weights_source == "trained"
    assert preview.weights_ref_id == parent.id


def test_auto_iteration_accepts_canonical_training_task_category(db: Session):
    task = _task(db, "人体识别")
    parent = _job(db, task, category="人体识别-v1")
    db.commit()

    resolved = resolve_auto_iteration_weights(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert resolved.weights_source == "trained"
    assert resolved.weights_ref_id == parent.id


def test_auto_iteration_prefers_same_task_over_newer_cross_task_model(db: Session):
    task = _task(db, "当前任务")
    other_task = _task(db, "同标签其他任务")
    same_task_job = _job(db, task)
    _job(db, other_task)
    db.commit()

    resolved = resolve_auto_iteration_weights(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert resolved.weights_ref_id == same_task_job.id


def test_auto_iteration_does_not_bypass_consistent_pool_for_configured_superuser(
    db: Session, monkeypatch
):
    """Configured service superusers cannot inherit admin-only pool models."""

    task = _task(db, "当前任务")
    pool_task = Task(
        name="管理员专属一致池",
        media_type="IMAGE",
        status="FINISHED",
        created_by=1,
        updated_by=1,
        source_type="consistent_pool",
        config=json.dumps(
            {"consistent_pool": {"kind": "train_filtered_consistent"}}
        ),
    )
    db.add(pool_task)
    db.flush()
    visible_job = _job(db, task, weights_path="runs/visible-parent/best.pt")
    _job(db, pool_task, weights_path="runs/admin-pool-parent/best.pt")
    db.commit()

    current_user = crud_user.get(db, 1)
    assert current_user is not None
    monkeypatch.setattr(
        settings,
        "TASK_SUPERUSER_USERNAMES",
        str(current_user.username),
    )

    resolved = resolve_auto_iteration_weights(
        db,
        current_user=current_user,
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert resolved.weights_source == "trained"
    assert resolved.weights_ref_id == visible_job.id


def test_auto_iteration_marks_cross_task_fallback_scope(db: Session):
    task = _task(db, "当前任务")
    other_task = _task(db, "同契约历史任务")
    other_job = _job(db, other_task)
    db.commit()

    preview = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert preview.weights_ref_id == other_job.id
    assert preview.selection_scope == "cross_task_fallback"


def test_auto_iteration_falls_back_without_compatible_contract(db: Session):
    task = _task(db)
    _job(db, task, label_signature="labels-a", order_signature="order-a")
    db.commit()

    resolved = resolve_auto_iteration_weights(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-b",
        expected_class_order_signature="order-b",
    )

    assert resolved.weights_source == "preset"
    assert resolved.weights_ref_id is None
    assert resolved.model_weights == "yolo11n.pt"


def test_auto_iteration_does_not_change_explicit_weight_source(db: Session):
    task = _task(db)
    command = CreateYoloTrainJobCommand(
        weights_source="preset", model_weights="yolo11s.pt"
    )

    resolved = resolve_auto_iteration_weights(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=command,
    )

    assert resolved is command


def test_previous_version_comparison_uses_real_parent_and_marks_tradeoff(db: Session):
    task = _task(db)
    previous = _job(
        db,
        task,
        metrics={"map50_95": 0.4, "map50": 0.7, "precision": 0.8, "recall": 0.75},
    )
    previous.dataset_content_signature = "dataset-v1"
    current = _job(
        db,
        task,
        metrics={"map50_95": 0.45, "map50": 0.72, "precision": 0.81, "recall": 0.7},
    )
    current.model_weights = f"trained:{previous.id}"
    current.dataset_content_signature = "dataset-v2"
    db.commit()

    comparison = build_previous_version_comparison(db, current)

    assert comparison.available is True
    assert comparison.previous_job_id == previous.id
    assert comparison.status == "mixed"
    assert comparison.same_dataset is False
    assert round(comparison.metrics[0].delta or 0, 10) == 0.05
    assert "数据快照不同" in comparison.summary
    assert "存在取舍" in comparison.summary


def test_first_version_comparison_has_an_explicit_empty_state(db: Session):
    task = _task(db)
    current = _job(db, task)
    db.commit()

    comparison = build_previous_version_comparison(db, current)

    assert comparison.available is False
    assert comparison.status == "no_previous_version"
    assert comparison.previous_job_id is None
    assert comparison.metrics == []


def test_iteration_preview_returns_real_parent_and_actionable_ai_params(db: Session):
    task = _task(db, "人体识别")
    parent = _job(db, task)
    report = YoloEvalReport(
        yolo_train_job_id=parent.id,
        provider_key="volcengine",
        report_kind="next_iteration",
        body="下一轮建议",
        suggested_params=json.dumps(
            {
                "recommended_train_params": {
                    "epochs": 20,
                    "imgsz": 640,
                    "lr0": 0.0001,
                    "amp": False,
                    "model_weights": "must-not-overwrite.pt",
                    "device": "cuda:7",
                    "notes": "仅保留训练参数",
                }
            }
        ),
    )
    db.add(report)
    db.commit()

    preview = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert preview.weights_source == "trained"
    assert preview.weights_ref_id == parent.id
    assert preview.parent.display_name == "人体识别"
    assert preview.parent.weights_filename == "best.pt"
    assert preview.ai_report_id == report.id
    assert preview.ai_provider_key == "volcengine"
    assert preview.suggested_params["epochs"] == 20
    assert preview.suggested_params["imgsz"] == 640
    assert preview.suggested_params["lr0"] == 0.0001
    assert preview.suggested_params["amp"] is False
    assert "model_weights" not in preview.suggested_params
    assert "device" not in preview.suggested_params
    assert "notes" not in preview.suggested_params

    manual = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        parent_job_id=parent.id,
    )
    assert manual.weights_ref_id == parent.id
    assert manual.ai_report_id == report.id


def test_iteration_preview_reads_dataset_version_from_frozen_manifest_without_model(
    db: Session,
):
    task = _task(db, "历史作业")
    parent = _job(db, task)
    parent.dataset_manifest_json = json.dumps(
        {
            "dataset_version_id": 42,
            "version_no": 7,
            "dataset_content_signature": "dataset-content-7",
        }
    )
    db.commit()

    preview = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert preview.parent is not None
    assert preview.parent.dataset_version_id == 42
    assert preview.parent.dataset_version_no == 7
    assert preview.parent.dataset_content_signature == "dataset-content-7"


def test_iteration_preview_exposes_parent_logical_dataset_identity(db: Session):
    dataset = Dataset(
        owner_id=1,
        created_by=1,
        updated_by=1,
        name="父模型逻辑数据集",
    )
    db.add(dataset)
    db.flush()
    task = _task(db, "带数据集的历史作业")
    task.dataset_id = dataset.id
    parent = _job(db, task)
    db.commit()

    preview = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert preview.parent is not None
    assert preview.parent.dataset_id == dataset.id
    assert preview.parent.dataset_name == "父模型逻辑数据集"
    assert preview.parent.job_id == parent.id


def test_auto_iteration_reads_legacy_nested_manifest_signatures(db: Session):
    task = _task(db, "历史嵌套血缘")
    parent = _job(db, task, label_signature=None, order_signature=None)
    parent.hyperparams_json = json.dumps(
        {
            "dataset_manifest": {
                "label_set_signature": "labels-a",
                "class_order_signature": "order-a",
            }
        }
    )
    db.commit()

    preview = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert preview.weights_source == "trained"
    assert preview.weights_ref_id == parent.id
    assert preview.parent is not None
    assert preview.parent.label_set_signature == "labels-a"
    assert preview.parent.class_order_signature == "order-a"


def test_iteration_preview_merges_partial_top_level_manifest_with_nested_snapshot(
    db: Session,
):
    task = _task(db, "部分数据快照")
    parent = _job(db, task, label_signature=None, order_signature=None)
    parent.dataset_manifest_json = json.dumps(
        {"dataset_content_signature": "content-top-level"}
    )
    parent.hyperparams_json = json.dumps(
        {
            "dataset_manifest": {
                "dataset_version_id": 42,
                "version_no": 7,
                "manifest_sha256": "manifest-nested",
                "label_set_signature": "labels-a",
                "class_order_signature": "order-a",
            }
        }
    )
    db.commit()

    preview = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert preview.parent is not None
    assert preview.parent.dataset_version_id == 42
    assert preview.parent.dataset_version_no == 7
    assert preview.parent.dataset_content_signature == "content-top-level"


def test_iteration_preview_tolerates_legacy_model_without_dataset_version_no(
    db: Session, monkeypatch: pytest.MonkeyPatch
):
    """Historical ORM mappings may not expose the denormalized version number."""

    task = _task(db, "旧模型映射")
    parent = _job(db, task)
    db.commit()

    # The live model_version table only stores dataset_version_id.  Older
    # deployments therefore return an object without dataset_version_no;
    # preview must fall back to the frozen job manifest instead of crashing.
    legacy_model = SimpleNamespace(
        id=17,
        version_no=4,
        branch_version_no=2,
        display_name="旧模型映射 · v4",
        dataset_version_id=42,
        model_key="legacy:model:4",
        status="CANDIDATE",
        manifest_json=None,
    )
    monkeypatch.setattr(
        crud_model_version,
        "get_by_training_job",
        lambda _db, _job_id: legacy_model,
    )
    parent.dataset_manifest_json = json.dumps({"version_no": 9})
    db.commit()

    preview = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        source_task_ids=[task.id],
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert preview.parent is not None
    assert preview.parent.model_version_id == 17
    assert preview.parent.branch_version_no == 2
    assert preview.parent.dataset_version_id == 42
    assert preview.parent.dataset_version_no == 9


def test_explicit_iteration_validates_legacy_nested_manifest_signatures(db: Session):
    task = _task(db, "显式历史嵌套血缘")
    parent = _job(db, task, label_signature=None, order_signature=None)
    parent.hyperparams_json = json.dumps(
        {
            "dataset_manifest": {
                "label_set_signature": "labels-a",
                "class_order_signature": "order-a",
            }
        }
    )
    db.commit()

    preview = build_iteration_preview(
        db,
        current_user=crud_user.get(db, 1),
        task_id=task.id,
        cmd=_auto_command(),
        parent_job_id=parent.id,
        expected_label_set_signature="labels-a",
        expected_class_order_signature="order-a",
    )

    assert preview.weights_source == "trained"
    assert preview.weights_ref_id == parent.id


def test_iteration_preview_rejects_explicit_parent_with_incompatible_recipe(
    db: Session,
):
    task = _task(db, "配方不兼容")
    parent = _job(db, task)
    db.commit()

    command = _auto_command().copy(update={"training_recipe_key": "other_recipe"})

    with pytest.raises(LabelUException) as error:
        build_iteration_preview(
            db,
            current_user=crud_user.get(db, 1),
            task_id=task.id,
            cmd=command,
            parent_job_id=parent.id,
        )

    assert error.value.status_code == 400
    assert "训练配方" in str(error.value)


def test_iteration_preview_rejects_explicit_parent_with_incompatible_label_contract(
    db: Session,
):
    task = _task(db, "标签契约不兼容")
    parent = _job(db, task)
    db.commit()

    with pytest.raises(LabelUException) as error:
        build_iteration_preview(
            db,
            current_user=crud_user.get(db, 1),
            task_id=task.id,
            cmd=_auto_command(),
            parent_job_id=parent.id,
            expected_label_set_signature="labels-other",
            expected_class_order_signature="order-other",
        )

    assert error.value.status_code == 400
    assert "标签集合" in str(error.value)


def test_iteration_preview_rejects_explicit_parent_with_incompatible_job_type(
    db: Session,
):
    task = _task(db, "作业类型不兼容")
    parent = _job(db, task)
    parent.job_type = "plate_recognition"
    db.commit()

    with pytest.raises(LabelUException) as error:
        build_iteration_preview(
            db,
            current_user=crud_user.get(db, 1),
            task_id=task.id,
            cmd=_auto_command(),
            parent_job_id=parent.id,
        )

    assert error.value.status_code == 400
    assert "不可用于迭代训练" in str(error.value)
