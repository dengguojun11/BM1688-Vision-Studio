from __future__ import annotations

import json

import pytest

from labelu.internal.application.service.auto_training_evaluator import (
    claim_evaluation_run,
    list_evaluation_runs,
    materialize_evaluation_request,
)
from labelu.internal.application.service.auto_training_events import (
    enqueue_auto_training_event,
)
from labelu.internal.application.service.auto_training_outbox import (
    process_outbox_batch,
)
from labelu.internal.application.service.auto_training_plan import (
    replace_evaluation_samples,
)
from labelu.internal.application.service.auto_training_reconciliation import (
    handle_outbox_event,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.auto_training import (
    AutoTrainingEvaluationRun,
    AutoTrainingOutbox,
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
)
from labelu.internal.domain.models.auto_training_governance import (
    TrainingEvaluationDataset,
)
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.tests.internal.application.service.test_auto_training_lifecycle import (
    _seed as _seed_lifecycle,
)
from labelu.tests.internal.application.service.test_auto_training_reconciliation import (
    _seed_task,
)


@pytest.fixture(autouse=True)
def _enable_auto_training_writes_for_evaluator_contract_tests(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)


def _seed_approved_dataset(db, *, user, task, sample, kind: str, name: str):
    row = TrainingEvaluationDataset(
        owner_id=user.id,
        name=name,
        dataset_kind=kind,
        series_type="yolo_detection",
        dataset_category_id=8,
        label_set_signature="a" * 64,
        version_no=1,
        status="DRAFT",
        manifest_rel_path=f"draft/{name}.json",
        manifest_sha256=(name[0] * 64),
        sample_count=0,
        grouping_policy_json="{}",
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    replace_evaluation_samples(
        db,
        user=user,
        dataset_id=row.id,
        samples=[
            {
                "task_id": task.id,
                "sample_id": sample.id,
                "split_role": "test",
                "identity_group_key": name,
                "capture_group_key": "camera-a",
            }
        ],
    )
    row.status = "APPROVED"
    row.approved_by = user.id
    return row


def test_materializes_one_trusted_request_from_approved_frozen_datasets(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    plan_id, round_id, job_id = _seed_lifecycle(db)
    with begin_transaction(db):
        user = db.query(User).filter_by(id=1).one()
        db.query(AutoTrainingPlan).filter_by(id=plan_id).one().dataset_category_id = 8
        db.query(AutoTrainingRound).filter_by(id=round_id).one().state = "PT_EVALUATING"
        job = db.query(YoloTrainJob).filter_by(id=job_id).one()
        job.status = "COMPLETED"
        job.weights_path = "runs/auto/best.pt"
        task, _contract, samples = _seed_task(db, tmp_path, count=1)
        validation = _seed_approved_dataset(
            db, user=user, task=task, sample=samples[0], kind="VALIDATION", name="validation"
        )
        benchmark = _seed_approved_dataset(
            db, user=user, task=task, sample=samples[0], kind="BENCHMARK", name="benchmark"
        )
        sealed = _seed_approved_dataset(
            db, user=user, task=task, sample=samples[0], kind="SEALED_ACCEPTANCE", name="sealed"
        )
        version = (
            db.query(AutoTrainingPlanVersion)
            .filter_by(plan_id=plan_id)
            .one()
        )
        config = json.loads(version.config_json)
        config["evaluation_binding"] = {
            "validation_dataset_id": validation.id,
            "benchmark_dataset_id": benchmark.id,
            "sealed_acceptance_dataset_id": sealed.id,
            "signature": "e" * 64,
        }
        version.config_json = json.dumps(config)

        first = materialize_evaluation_request(db, round_id=round_id, job_id=job_id)
        second = materialize_evaluation_request(db, round_id=round_id, job_id=job_id)

    assert first.id == second.id
    assert first.status == "PENDING"
    assert len(first.request_manifest_sha256) == 64
    request_path = tmp_path / first.request_manifest_rel_path
    assert request_path.is_file()
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert payload["series_type"] == "yolo_detection"
    assert {item["dataset_kind"] for item in payload["datasets"]} == {
        "VALIDATION",
        "BENCHMARK",
        "SEALED_ACCEPTANCE",
    }


def test_trusted_request_fails_closed_when_frozen_sample_changes(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    plan_id, round_id, job_id = _seed_lifecycle(db)
    with begin_transaction(db):
        user = db.query(User).filter_by(id=1).one()
        db.query(AutoTrainingPlan).filter_by(id=plan_id).one().dataset_category_id = 8
        db.query(AutoTrainingRound).filter_by(id=round_id).one().state = "PT_EVALUATING"
        job = db.query(YoloTrainJob).filter_by(id=job_id).one()
        job.status = "COMPLETED"
        job.weights_path = "runs/auto/best.pt"
        task, _contract, samples = _seed_task(db, tmp_path, count=1)
        datasets = []
        for kind, name in (
            ("VALIDATION", "validation-stale"),
            ("BENCHMARK", "benchmark-stale"),
            ("SEALED_ACCEPTANCE", "sealed-stale"),
        ):
            datasets.append(
                _seed_approved_dataset(
                    db, user=user, task=task, sample=samples[0], kind=kind, name=name
                )
            )
        version = db.query(AutoTrainingPlanVersion).filter_by(plan_id=plan_id).one()
        config = json.loads(version.config_json)
        config["evaluation_binding"] = {
            "validation_dataset_id": datasets[0].id,
            "benchmark_dataset_id": datasets[1].id,
            "sealed_acceptance_dataset_id": datasets[2].id,
            "signature": "e" * 64,
        }
        version.config_json = json.dumps(config)
        samples[0].data = json.dumps({"result": {"rectTool": {"result": []}}})

        with pytest.raises(ValueError, match="annotation hash changed"):
            materialize_evaluation_request(db, round_id=round_id, job_id=job_id)


def test_evaluation_worker_claim_is_leased_and_restart_idempotent(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        row = AutoTrainingEvaluationRun(
            round_id=999,
            policy_signature="q" * 64,
            evaluation_binding_signature="e" * 64,
            status="PENDING",
            request_manifest_rel_path="evaluation/request.json",
            request_manifest_sha256="a" * 64,
        )
        db.add(row)
        db.flush()
        run_id = row.id

    with begin_transaction(db):
        claimed = claim_evaluation_run(
            db, run_id=run_id, worker_id="worker-a", lease_seconds=60
        )
    assert claimed.status == "PROCESSING"
    assert claimed.evaluator_worker_id == "worker-a"

    with begin_transaction(db):
        replay = claim_evaluation_run(
            db, run_id=run_id, worker_id="worker-a", lease_seconds=60
        )
    assert replay.id == run_id

    with pytest.raises(ValueError, match="leased by another worker"):
        with begin_transaction(db):
            claim_evaluation_run(
                db, run_id=run_id, worker_id="worker-b", lease_seconds=60
            )

    pending = list_evaluation_runs(db, statuses={"PENDING", "PROCESSING"}, limit=10)
    assert [item.id for item in pending] == [run_id]


def test_phase_one_gate_blocks_evaluation_claim_before_database_access(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)

    with pytest.raises(LabelUException) as exc_info:
        claim_evaluation_run(
            object(), run_id=1, worker_id="blocked-worker", lease_seconds=60
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == 57122


def test_quality_evaluation_outbox_materializes_once_across_restarts(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    monkeypatch.setenv("LABELU_AUTO_TRAINING_OBSERVE_ONLY", "false")
    plan_id, round_id, job_id = _seed_lifecycle(db)
    with begin_transaction(db):
        user = db.query(User).filter_by(id=1).one()
        db.query(AutoTrainingPlan).filter_by(id=plan_id).one().dataset_category_id = 8
        db.query(AutoTrainingRound).filter_by(id=round_id).one().state = "PT_EVALUATING"
        job = db.query(YoloTrainJob).filter_by(id=job_id).one()
        job.status = "COMPLETED"
        job.weights_path = "runs/auto/best.pt"
        task, _contract, samples = _seed_task(db, tmp_path, count=1)
        datasets = []
        for kind, name in (
            ("VALIDATION", "validation-outbox"),
            ("BENCHMARK", "benchmark-outbox"),
            ("SEALED_ACCEPTANCE", "sealed-outbox"),
        ):
            datasets.append(
                _seed_approved_dataset(
                    db, user=user, task=task, sample=samples[0], kind=kind, name=name
                )
            )
        version = db.query(AutoTrainingPlanVersion).filter_by(plan_id=plan_id).one()
        config = json.loads(version.config_json)
        config["evaluation_binding"] = {
            "validation_dataset_id": datasets[0].id,
            "benchmark_dataset_id": datasets[1].id,
            "sealed_acceptance_dataset_id": datasets[2].id,
            "signature": "e" * 64,
        }
        version.config_json = json.dumps(config)
        event = enqueue_auto_training_event(
            db,
            event_name="auto.round.quality.evaluate.v1",
            aggregate_type="auto_training_round",
            aggregate_id=str(round_id),
            payload={"round_id": round_id, "job_id": job_id},
            idempotency_key=f"round:{round_id}:quality-evaluate-test",
        )
        event_id = event.id

    first = process_outbox_batch(
        db,
        owner="evaluation-worker-a",
        handler=handle_outbox_event,
        limit=10,
    )
    assert first.claimed == 1
    assert first.processed == 1
    evaluation = db.query(AutoTrainingEvaluationRun).filter_by(round_id=round_id).one()
    assert evaluation.status == "PENDING"
    assert (tmp_path / evaluation.request_manifest_rel_path).is_file()
    assert db.query(AutoTrainingOutbox).filter_by(id=event_id).one().status == "PROCESSED"

    db.rollback()
    db.expunge_all()
    replay = process_outbox_batch(
        db,
        owner="evaluation-worker-b",
        handler=handle_outbox_event,
        limit=10,
    )
    assert replay.claimed == 0
    assert db.query(AutoTrainingEvaluationRun).filter_by(round_id=round_id).count() == 1
