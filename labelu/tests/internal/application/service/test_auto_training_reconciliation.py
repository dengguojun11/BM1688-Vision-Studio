from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from labelu.internal.application.service.auto_training_reconciliation import (
    _create_plan_for_training_ready_task,
    discover_training_ready_plans,
    handle_outbox_event,
    reconcile_plan_owned_evaluation_datasets,
    reconcile_task_for_plan,
)
from labelu.internal.application.service.auto_training_plan import create_review
from labelu.internal.application.service.auto_training_outbox import (
    process_outbox_batch,
)
from labelu.internal.application.service.auto_training_manifest import (
    persist_round_manifest,
    verify_round_manifest,
)
from labelu.internal.application.service.auto_training_dataset import (
    load_frozen_sample_proxies,
)
from labelu.internal.application.service.auto_training_scheduler import (
    AutoTrainingActiveRoundConflict,
    create_frozen_round,
    materialize_training_job,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.label_contract import annotation_sha256
from labelu.internal.domain.models.annotation_contract import (
    SampleAnnotationScope,
    TaskAnnotationContract,
)
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.auto_training import (
    AutoTrainingOutbox,
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
    AutoTrainingRoundSample,
    AutoTrainingSamplePool,
    AutoTrainingTaskScope,
    SampleHumanReview,
)
from labelu.internal.domain.models.auto_training_governance import (
    TrainingEvaluationDataset,
    TrainingEvaluationSample,
)
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.sample_edit_log import SampleEditLog
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.common.error_code import LabelUException


@pytest.fixture(autouse=True)
def _enable_reconciliation_switches(monkeypatch):
    monkeypatch.setenv("LABELU_AUTO_TRAINING_POOL_ENABLED", "true")
    monkeypatch.setenv("LABELU_AUTO_TRAINING_SCHEDULER_ENABLED", "true")
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)


from labelu.internal.domain.models.training_task import TrainingTask
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _annotation(index: int) -> dict:
    return {
        "file": {"width": 1280, "height": 720},
        "result": {
            "rectTool": {
                "result": [
                    {
                        "id": f"rect-{index}",
                        "label": "vehicle",
                        "x": 10,
                        "y": 20,
                        "width": 100,
                        "height": 80,
                    }
                ]
            }
        },
    }


def _seed_plan(db, *, name: str, include_history: bool = True):
    config = {
        "data_admission": {
            "min_eligible_samples_per_task": 50,
            "include_historical_data": include_history,
            "history_task_ids": [],
            "history_confirmed": include_history,
        },
        "trigger_policy": {
            "min_new_samples": 1,
            "min_growth_ratio": 0.0,
            "max_wait_hours": 24,
        },
    }
    plan = AutoTrainingPlan(
        owner_id=1,
        name=name,
        series_type="yolo_detection",
        dataset_category_id=8,
        label_set_signature="a" * 64,
        class_order_signature="b" * 64,
        status="ACTIVE",
        version=1,
        created_by=1,
        updated_by=1,
    )
    db.add(plan)
    db.flush()
    version = AutoTrainingPlanVersion(
        plan_id=plan.id,
        version_no=1,
        status="APPROVED",
        config_json=json.dumps(config),
        config_sha256=hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
        missing_fields_json="[]",
        validation_errors_json="[]",
        created_by=1,
        approved_by=1,
    )
    db.add(version)
    db.flush()
    plan.current_version_id = version.id
    return plan


def _seed_task(db, tmp_path: Path, *, count: int, source_type: str = "internal"):
    task = Task(
        name=f"task-{source_type}-{count}",
        media_type="IMAGE",
        status="INPROGRESS",
        created_by=1,
        updated_by=1,
        dataset_category_id=8,
        source_type=source_type,
    )
    db.add(task)
    db.flush()
    contract = TaskAnnotationContract(
        task_id=task.id,
        revision=1,
        annotation_kind="bbox",
        label_ids_json="[1]",
        labels_snapshot_json="[]",
        label_set_signature="a" * 64,
        class_order_signature="b" * 64,
        status="active",
        created_by=1,
    )
    db.add(contract)
    db.flush()
    task.current_annotation_contract_id = contract.id
    samples = []
    for index in range(count):
        relative = f"auto-training-test/{task.id}-{index}.jpg"
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"image-{task.id}-{index}".encode())
        attachment = TaskAttachment(
            filename=target.name,
            path=relative,
            task_id=task.id,
            created_by=1,
            updated_by=1,
        )
        db.add(attachment)
        db.flush()
        annotation = _annotation(index)
        sample = TaskSample(
            inner_id=index + 1,
            task_id=task.id,
            file_id=attachment.id,
            created_by=1,
            updated_by=1,
            state="DONE",
            annotated_count=1,
            data=json.dumps(annotation),
        )
        db.add(sample)
        db.flush()
        db.add(
            SampleAnnotationScope(
                sample_id=sample.id,
                contract_id=contract.id,
                completion_status="complete",
                annotation_sha256=annotation_sha256(annotation),
                reviewed_by=1,
            )
        )
        samples.append(sample)
    db.flush()
    return task, contract, samples


def test_single_task_threshold_and_round_evaluation_event(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="threshold-plan")
        task, _contract, samples = _seed_task(db, tmp_path, count=49)
        summary = reconcile_task_for_plan(db, plan=plan, task=task)
        assert summary.eligible_sample_count == 49
        assert summary.qualified is False

    scope = (
        db.query(AutoTrainingTaskScope)
        .filter_by(plan_id=plan.id, task_id=task.id)
        .one()
    )
    assert scope.reason_code == "TASK_MINIMUM_NOT_REACHED"
    assert (
        db.query(AutoTrainingOutbox)
        .filter_by(event_name="auto.plan.round.evaluate.v1")
        .count()
        == 0
    )

    with begin_transaction(db):
        relative = f"auto-training-test/{task.id}-49.jpg"
        target = tmp_path / relative
        target.write_bytes(b"image-50")
        attachment = TaskAttachment(
            filename=target.name,
            path=relative,
            task_id=task.id,
            created_by=1,
            updated_by=1,
        )
        db.add(attachment)
        db.flush()
        annotation = _annotation(49)
        sample = TaskSample(
            inner_id=50,
            task_id=task.id,
            file_id=attachment.id,
            created_by=1,
            updated_by=1,
            state="DONE",
            annotated_count=1,
            data=json.dumps(annotation),
        )
        db.add(sample)
        db.flush()
        db.add(
            SampleAnnotationScope(
                sample_id=sample.id,
                contract_id=task.current_annotation_contract_id,
                completion_status="complete",
                annotation_sha256=annotation_sha256(annotation),
                reviewed_by=1,
            )
        )
        db.flush()
        summary = reconcile_task_for_plan(db, plan=plan, task=task)
        assert summary.eligible_sample_count == 50
        assert summary.qualified is True

    assert (
        db.query(AutoTrainingOutbox)
        .filter_by(event_name="auto.plan.round.evaluate.v1")
        .count()
        == 1
    )


def test_training_ready_dataset_creates_one_default_plan_and_five_drafts(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        db.add(
            DatasetCategory(
                id=8,
                category_key="garbage_street",
                name="垃圾街道",
                status="active",
            )
        )
        db.flush()
        task, contract, _samples = _seed_task(db, tmp_path, count=1)
        task.training_ready = True
        db.add(task)
        db.flush()
        first = _create_plan_for_training_ready_task(db, task=task)
        second = _create_plan_for_training_ready_task(db, task=task)

    assert first is not None
    assert second is not None
    assert first["id"] == second["id"]
    assert first["name"] == "垃圾街道"
    assert first["dataset_category_name"] == "垃圾街道"
    assert first["status"] == "DRAFT_INCOMPLETE"
    admission = first["current_version"]["config"]["data_admission"]
    assert admission["history_task_ids"] == [task.id]
    datasets = db.query(TrainingEvaluationDataset).filter_by(plan_id=first["id"]).all()
    assert len(datasets) == 5
    assert {row.status for row in datasets} == {"DRAFT"}
    assert first["label_set_signature"] == contract.label_set_signature


def test_discovery_backfills_ready_tasks_without_runtime_switches(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        task, _contract, _samples = _seed_task(db, tmp_path, count=1)
        task.training_ready = True
        db.add(task)

    assert discover_training_ready_plans(db) == 1
    assert discover_training_ready_plans(db) == 0
    plan = db.query(AutoTrainingPlan).one()
    assert plan.status == "DRAFT_INCOMPLETE"
    assert db.query(TrainingEvaluationDataset).filter_by(plan_id=plan.id).count() == 5


def test_discovery_keeps_different_class_orders_in_separate_plans(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        db.add(
            DatasetCategory(
                id=8,
                category_key="garbage_street",
                name="垃圾街道",
                status="active",
            )
        )
        db.flush()
        first, _first_contract, _samples = _seed_task(db, tmp_path, count=1)
        second, second_contract, _samples = _seed_task(db, tmp_path, count=2)
        first.training_ready = True
        second.training_ready = True
        second_contract.class_order_signature = "c" * 64
        db.add_all([first, second, second_contract])

    assert discover_training_ready_plans(db) == 2
    plans = db.query(AutoTrainingPlan).order_by(AutoTrainingPlan.id).all()
    assert len(plans) == 2
    assert [plan.name for plan in plans] == ["垃圾街道", "垃圾街道（2）"]
    assert {plan.class_order_signature for plan in plans} == {"b" * 64, "c" * 64}
    assert db.query(TrainingEvaluationDataset).count() == 10


def test_reconciliation_replaces_and_approves_generated_plan_drafts(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        task, _contract, _samples = _seed_task(db, tmp_path, count=50)
        task.training_ready = True
        db.add(task)
        created = _create_plan_for_training_ready_task(db, task=task)
        datasets = (
            db.query(TrainingEvaluationDataset)
            .filter_by(plan_id=int(created["id"]))
            .order_by(TrainingEvaluationDataset.id)
            .all()
        )
        dataset_ids = [row.id for row in datasets]
        approved_id = datasets[0].id
        for row in datasets[1:]:
            row.status = "DRAFT"
            row.approved_by = None
            row.approved_at = None

    approved_count = reconcile_plan_owned_evaluation_datasets(db)
    approval_states = [
        (row.id, row.dataset_kind, row.status)
        for row in db.query(TrainingEvaluationDataset)
        .filter(TrainingEvaluationDataset.id.in_(dataset_ids))
        .order_by(TrainingEvaluationDataset.id)
        .all()
    ]
    assert approved_count == 4, approval_states
    assert reconcile_plan_owned_evaluation_datasets(db) == 0
    refreshed = (
        db.query(TrainingEvaluationDataset)
        .filter(TrainingEvaluationDataset.id.in_(dataset_ids))
        .order_by(TrainingEvaluationDataset.id)
        .all()
    )
    assert [row.id for row in refreshed] == dataset_ids
    assert db.query(TrainingEvaluationDataset).filter_by(plan_id=int(created["id"])).count() == 5
    assert refreshed[0].id == approved_id
    assert {row.status for row in refreshed} == {"APPROVED"}
    assert all(row.approved_by == task.created_by for row in refreshed)
    assert all(row.sample_count > 0 for row in refreshed)


def test_training_ready_outbox_event_is_idempotent(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "labelu.internal.common.auto_training_runtime.load_auto_training_switches",
        lambda: {"observe_only": False, "pool_enabled": False},
    )
    with begin_transaction(db):
        task, contract, _samples = _seed_task(db, tmp_path, count=1)
        task.training_ready = True
        db.add(task)
        db.flush()
        event = AutoTrainingOutbox(
            event_name="task.training_ready.v1",
            aggregate_type="task",
            aggregate_id=str(task.id),
            payload_json=json.dumps(
                {
                    "task_id": task.id,
                    "dataset_category_id": task.dataset_category_id,
                    "label_set_signature": contract.label_set_signature,
                    "class_order_signature": contract.class_order_signature,
                }
            ),
            idempotency_key=f"ready-task-{task.id}",
            status="PENDING",
        )
        db.add(event)
        db.flush()
        handle_outbox_event(db, event)
        handle_outbox_event(db, event)

    assert db.query(AutoTrainingPlan).count() == 1
    assert db.query(TrainingEvaluationDataset).count() == 5


def test_bound_evaluation_samples_are_excluded_from_training_pool(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="evaluation-isolation-plan")
        task, _contract, samples = _seed_task(db, tmp_path, count=50)
        dataset = TrainingEvaluationDataset(
            owner_id=1,
            name="reserved-validation",
            dataset_kind="VALIDATION",
            series_type="yolo_detection",
            dataset_category_id=plan.dataset_category_id,
            label_set_signature=plan.label_set_signature,
            version_no=1,
            status="APPROVED",
            manifest_rel_path="evaluation/reserved.json",
            manifest_sha256="f" * 64,
            sample_count=1,
            grouping_policy_json="{}",
            created_by=1,
            approved_by=1,
        )
        db.add(dataset)
        db.flush()
        db.add(
            TrainingEvaluationSample(
                evaluation_dataset_id=dataset.id,
                task_id=task.id,
                sample_id=samples[0].id,
                sample_signature="1" * 64,
                annotation_sha256="2" * 64,
                image_sha256="3" * 64,
                split_role="validation",
            )
        )
        version = (
            db.query(AutoTrainingPlanVersion)
            .filter_by(id=plan.current_version_id)
            .one()
        )
        config = json.loads(version.config_json)
        config["evaluation_binding"] = {"validation_dataset_id": dataset.id}
        version.config_json = json.dumps(config)
        db.flush()

        summary = reconcile_task_for_plan(db, plan=plan, task=task)

        assert summary.qualified is False
        assert summary.eligible_sample_count == 49
        reserved = (
            db.query(AutoTrainingSamplePool)
            .filter_by(plan_id=plan.id, sample_id=samples[0].id)
            .one()
        )
        assert reserved.pool_status == "EXCLUDED"
        assert reserved.reason_code == "EVALUATION_RESERVED"


def test_third_party_samples_require_current_per_image_review(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="third-party-plan")
        task, _contract, samples = _seed_task(
            db, tmp_path, count=50, source_type="third_party"
        )
        first = reconcile_task_for_plan(db, plan=plan, task=task)
        assert first.eligible_sample_count == 0
        assert first.pending_review_count == 50
        for sample in samples:
            scope = db.query(SampleAnnotationScope).filter_by(sample_id=sample.id).one()
            db.add(
                SampleEditLog(
                    task_id=task.id,
                    sample_id=sample.id,
                    user_id=1,
                    action="annotation_edit",
                    previous_state="NEW",
                    new_state="DONE",
                    previous_data="{}",
                    new_data=sample.data,
                )
            )
            db.add(
                SampleHumanReview(
                    task_id=task.id,
                    sample_id=sample.id,
                    annotation_sha256=scope.annotation_sha256,
                    review_status="APPROVED",
                    reviewed_by=1,
                )
            )
        db.flush()
        approved = reconcile_task_for_plan(db, plan=plan, task=task)
        assert approved.qualified is True
        assert approved.eligible_sample_count == 50

    with begin_transaction(db):
        changed = samples[0]
        changed_annotation = _annotation(999)
        changed.data = json.dumps(changed_annotation)
        scope = db.query(SampleAnnotationScope).filter_by(sample_id=changed.id).one()
        scope.annotation_sha256 = annotation_sha256(changed_annotation)
        stale = reconcile_task_for_plan(db, plan=plan, task=task)
        assert stale.qualified is False
        assert stale.eligible_sample_count == 49
        assert stale.pending_review_count == 1
        pool = (
            db.query(AutoTrainingSamplePool)
            .filter_by(plan_id=plan.id, sample_id=changed.id)
            .one()
        )
        assert pool.reason_code == "HUMAN_REVIEW_STALE"


def test_plan_approval_scans_history_and_future_sample_events_reconcile(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="event-plan", include_history=True)
        historical, _contract, _samples = _seed_task(db, tmp_path, count=50)
        version = (
            db.query(AutoTrainingPlanVersion)
            .filter_by(id=plan.current_version_id)
            .one()
        )
        config = json.loads(version.config_json)
        config["data_admission"]["history_task_ids"] = [historical.id]
        version.config_json = json.dumps(config)
        event = AutoTrainingOutbox(
            event_name="auto.plan.config.approved.v1",
            aggregate_type="auto_training_plan",
            aggregate_id=str(plan.id),
            payload_json=json.dumps({"plan_id": plan.id}),
            idempotency_key=f"history-scan-{plan.id}",
            status="PENDING",
        )
        db.add(event)

    processed = process_outbox_batch(
        db, owner="test-worker", handler=handle_outbox_event, limit=20
    )
    assert processed.processed == 1
    assert (
        db.query(AutoTrainingTaskScope)
        .filter_by(plan_id=plan.id, task_id=historical.id)
        .count()
        == 1
    )

    with begin_transaction(db):
        future, _contract, future_samples = _seed_task(db, tmp_path, count=50)
        db.add(
            AutoTrainingOutbox(
                event_name="sample.annotation.changed.v1",
                aggregate_type="task_sample",
                aggregate_id=str(future_samples[0].id),
                payload_json=json.dumps(
                    {"task_id": future.id, "sample_id": future_samples[0].id}
                ),
                idempotency_key=f"future-sample-{future_samples[0].id}",
                status="PENDING",
            )
        )

    processed = process_outbox_batch(
        db, owner="test-worker", handler=handle_outbox_event, limit=20
    )
    assert processed.processed >= 1
    assert (
        db.query(AutoTrainingTaskScope)
        .filter_by(plan_id=plan.id, task_id=future.id)
        .count()
        == 1
    )


def test_duplicate_outbox_delivery_does_not_duplicate_pool_rows(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="idempotent-event-plan")
        task, _contract, samples = _seed_task(db, tmp_path, count=50)
        payload = json.dumps({"task_id": task.id, "sample_id": samples[0].id})
        db.add_all(
            [
                AutoTrainingOutbox(
                    event_name="sample.annotation.changed.v1",
                    aggregate_type="task_sample",
                    aggregate_id=str(samples[0].id),
                    payload_json=payload,
                    idempotency_key="duplicate-delivery-a",
                    status="PENDING",
                ),
                AutoTrainingOutbox(
                    event_name="sample.annotation.changed.v1",
                    aggregate_type="task_sample",
                    aggregate_id=str(samples[0].id),
                    payload_json=payload,
                    idempotency_key="duplicate-delivery-b",
                    status="PENDING",
                ),
            ]
        )

    result = process_outbox_batch(
        db, owner="test-worker", handler=handle_outbox_event, limit=20
    )
    assert result.processed == 2
    assert db.query(AutoTrainingSamplePool).filter_by(plan_id=plan.id).count() == 50


def test_frozen_round_manifest_is_immutable_and_sha_verified(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="frozen-round-plan")
        task, _contract, _samples = _seed_task(db, tmp_path, count=50)
        reconcile_task_for_plan(db, plan=plan, task=task)
        round_row = create_frozen_round(db, plan_id=plan.id, trigger_type="AUTO")
        round_id = round_row.id
        manifest_path = verify_round_manifest(round_row)
        assert manifest_path.is_file()
        assert round_row.eligible_sample_count == 50
        assert round_row.train_count + round_row.val_count + round_row.test_count == 50

    frozen_ids = [
        row.sample_id
        for row in db.query(AutoTrainingRoundSample)
        .filter_by(round_id=round_id)
        .order_by(AutoTrainingRoundSample.sample_id)
        .all()
    ]
    with begin_transaction(db):
        extra = AutoTrainingSamplePool(
            plan_id=plan.id,
            task_id=task.id,
            sample_id=999999,
            pool_status="ELIGIBLE",
            annotation_sha256="a" * 64,
            image_sha256="b" * 64,
            sample_signature="c" * 64,
            label_contract_signature=plan.label_set_signature,
        )
        db.add(extra)
    assert [
        row.sample_id
        for row in db.query(AutoTrainingRoundSample)
        .filter_by(round_id=round_id)
        .order_by(AutoTrainingRoundSample.sample_id)
        .all()
    ] == frozen_ids
    assert verify_round_manifest(
        db.query(AutoTrainingRound).filter_by(id=round_id).one()
    ).is_file()


def test_round_manifest_failed_replace_cleans_temporary_file(tmp_path, monkeypatch):
    from labelu.internal.application.service import auto_training_manifest

    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    round_row = AutoTrainingRound(plan_id=41, id=9)

    def fail_replace(_source, _target):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(auto_training_manifest.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        persist_round_manifest(round_row, payload={"samples": []})

    manifest_dir = tmp_path / "auto_training" / "plan-41" / "round-9"
    assert not (manifest_dir / "manifest.json.tmp").exists()
    assert not (manifest_dir / "manifest.json").exists()


def test_frozen_sample_loader_uses_annotation_snapshot_and_exact_membership(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="frozen-loader-plan")
        task, _contract, samples = _seed_task(db, tmp_path, count=50)
        reconcile_task_for_plan(db, plan=plan, task=task)
        round_row = create_frozen_round(db, plan_id=plan.id, trigger_type="AUTO")
        round_id = round_row.id
        original_data = samples[0].data
        original_sample_id = samples[0].id

    with begin_transaction(db):
        samples[0].data = json.dumps(_annotation(9999))

    frozen, forced_splits = load_frozen_sample_proxies(db, round_id=round_id)
    first = next(row for row in frozen if row.id == original_sample_id)
    assert json.loads(first.data) == json.loads(original_data)
    assert len(frozen) == 50
    assert set(forced_splits) == {row.id for row in frozen}


def test_same_execution_signature_cannot_create_a_second_round(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="duplicate-round-plan")
        task, _contract, _samples = _seed_task(db, tmp_path, count=50)
        reconcile_task_for_plan(db, plan=plan, task=task)
        first = create_frozen_round(db, plan_id=plan.id, trigger_type="AUTO")
        first_id = first.id
    with begin_transaction(db):
        plan = db.query(AutoTrainingPlan).filter_by(id=plan.id).one()
        plan.active_round_id = None
        plan.status = "ACTIVE"
        duplicate = create_frozen_round(db, plan_id=plan.id, trigger_type="AUTO")
        assert duplicate.id == first_id
    assert db.query(AutoTrainingRound).filter_by(plan_id=plan.id).count() == 1


def test_frozen_round_cas_conflict_does_not_write_orphan_manifest(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="cas-conflict-round-plan")
        task, _contract, _samples = _seed_task(db, tmp_path, count=50)
        reconcile_task_for_plan(db, plan=plan, task=task)
        plan.version += 1
        stale_version = plan.version - 1
        plan_id = plan.id

    with pytest.raises(AutoTrainingActiveRoundConflict):
        with begin_transaction(db):
            create_frozen_round(
                db,
                plan_id=plan_id,
                trigger_type="AUTO",
                expected_plan_version=stale_version,
            )

    assert db.query(AutoTrainingRound).filter_by(plan_id=plan_id).count() == 0
    manifest_root = tmp_path / "auto_training" / f"plan-{plan_id}"
    assert not manifest_root.exists()


def test_all_training_series_materialize_lineage_without_starting_gpu(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    for index, series in enumerate(
        ("yolo_detection", "plate_detection", "plate_recognition"), start=1
    ):
        with begin_transaction(db):
            plan = _seed_plan(db, name=f"materialize-{series}")
            plan.series_type = series
            task, _contract, _samples = _seed_task(db, tmp_path, count=50)
            reconcile_task_for_plan(db, plan=plan, task=task)
            round_row = create_frozen_round(db, plan_id=plan.id, trigger_type="AUTO")
            training_task, job = materialize_training_job(db, round_id=round_row.id)
            assert training_task.auto_training_plan_id == plan.id
            assert training_task.auto_training_round_id == round_row.id
            assert training_task.source_mode == "AUTO"
            assert job.auto_training_plan_id == plan.id
            assert job.auto_training_round_id == round_row.id
            assert job.job_type == series
            assert job.queue_source == "AUTO"
            assert job.status == "PENDING"
            assert round_row.state == "QUEUED"
            assert (
                db.query(AutoTrainingOutbox)
                .filter_by(
                    event_name="auto.round.train.dispatch.v1",
                    aggregate_id=str(round_row.id),
                )
                .count()
                == 1
            )
        assert (
            db.query(TrainingTask)
            .filter_by(auto_training_round_id=round_row.id)
            .count()
            == 1
        )
        assert (
            db.query(YoloTrainJob)
            .filter_by(auto_training_round_id=round_row.id)
            .count()
            == 1
        )


def test_third_party_review_requires_real_human_annotation_edit(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        task, _contract, samples = _seed_task(
            db, tmp_path, count=1, source_type="third_party"
        )
        sample = samples[0]
        user = db.query(User).filter(User.id == 1).one()
        current_hash = annotation_sha256(json.loads(sample.data))
        with pytest.raises(LabelUException) as exc_info:
            create_review(
                db,
                user=user,
                sample_id=sample.id,
                annotation_sha256=current_hash,
                review_status="APPROVED",
                comment=None,
            )
        assert exc_info.value.code == 57105
        db.add(
            SampleEditLog(
                task_id=task.id,
                sample_id=sample.id,
                user_id=user.id,
                action="annotation_edit",
                previous_state="NEW",
                new_state="DONE",
                previous_data="{}",
                new_data=sample.data,
            )
        )
        db.flush()
        review = create_review(
            db,
            user=user,
            sample_id=sample.id,
            annotation_sha256=current_hash,
            review_status="APPROVED",
            comment=None,
        )
        assert review["review_status"] == "APPROVED"
