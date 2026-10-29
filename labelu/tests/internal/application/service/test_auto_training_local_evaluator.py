from __future__ import annotations

import json

import pytest

from labelu.internal.application.service.auto_training_evaluator import (
    materialize_evaluation_request,
)
from labelu.internal.application.service.auto_training_local_evaluator import (
    LocalEvaluationInputError,
    run_local_simulated_evaluation,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.auto_training import (
    AutoTrainingEvaluationRun,
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
)
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.tests.internal.application.service.test_auto_training_evaluator import (
    _seed_approved_dataset,
)
from labelu.tests.internal.application.service.test_auto_training_lifecycle import (
    _seed as _seed_lifecycle,
)
from labelu.tests.internal.application.service.test_auto_training_reconciliation import (
    _seed_task,
)


@pytest.fixture(autouse=True)
def _enable_phase_two_evaluator_contract(monkeypatch):
    """Run the local evaluator service contract in its explicit phase-two sandbox.

    The production default remains fail-closed.  This fixture only permits the
    evaluator's existing lifecycle setup to exercise the service implementation;
    the phase-one gate itself is covered by ``test_auto_training_phase_one_gate``.
    """
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)


def _prepare_evaluation(db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    plan_id, round_id, job_id = _seed_lifecycle(db)
    with begin_transaction(db):
        user = db.query(User).filter_by(id=1).one()
        plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
        plan.dataset_category_id = 8
        round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
        round_row.state = "PT_EVALUATING"
        job = db.query(YoloTrainJob).filter_by(id=job_id).one()
        job.status = "COMPLETED"
        job.weights_path = "runs/auto/best.pt"
        task, _contract, samples = _seed_task(db, tmp_path, count=1)
        datasets = []
        for kind, name in (
            ("VALIDATION", "local-validation"),
            ("BENCHMARK", "local-benchmark"),
            ("SEALED_ACCEPTANCE", "local-sealed"),
        ):
            datasets.append(
                _seed_approved_dataset(
                    db,
                    user=user,
                    task=task,
                    sample=samples[0],
                    kind=kind,
                    name=name,
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
        evaluation = materialize_evaluation_request(
            db, round_id=round_id, job_id=job_id
        )
    return plan_id, round_id, job_id, evaluation.id


def test_local_evaluator_reaches_quality_gate_and_is_idempotent(
    db, tmp_path, monkeypatch
):
    plan_id, round_id, _job_id, evaluation_id = _prepare_evaluation(
        db, tmp_path, monkeypatch
    )

    with begin_transaction(db):
        result = run_local_simulated_evaluation(
            db,
            evaluation_run_id=evaluation_id,
            metrics={
                "overall_metrics": {"overall_recall": 0.98},
                "per_label_metrics": {"vehicle": {"recall": 0.97}},
            },
        )

    assert result["local_simulation"] is True
    assert result["decision_code"] == "AUTO_QUALITY_REACHED"
    evidence = tmp_path / result["evidence_manifest_rel_path"]
    assert evidence.is_file()
    assert len(result["evidence_manifest_sha256"]) == 64
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    assert payload["local_simulation"] is True
    assert payload["decision_preview"]["code"] == "AUTO_QUALITY_REACHED"

    replay = run_local_simulated_evaluation(
        db, evaluation_run_id=evaluation_id, metrics={"overall_metrics": {"overall_recall": 0.1}}
    )
    assert replay["changed"] is False
    assert replay["decision_code"] == "AUTO_QUALITY_REACHED"
    assert db.query(AutoTrainingEvaluationRun).filter_by(id=evaluation_id).one().status == "COMPLETED"
    assert db.query(AutoTrainingPlan).filter_by(id=plan_id).one().status == "AUTO_QUALITY_REACHED"
    assert db.query(AutoTrainingRound).filter_by(id=round_id).one().decision_code == "AUTO_QUALITY_REACHED"


def test_local_evaluator_default_fixture_fails_quality_safely(db, tmp_path, monkeypatch):
    plan_id, round_id, _job_id, evaluation_id = _prepare_evaluation(
        db, tmp_path, monkeypatch
    )

    with begin_transaction(db):
        result = run_local_simulated_evaluation(db, evaluation_run_id=evaluation_id)

    assert result["local_simulation"] is True
    assert result["decision_code"] == "QUALITY_FAILED"
    assert db.query(AutoTrainingPlan).filter_by(id=plan_id).one().status == "WAITING_FOR_DATA"
    assert db.query(AutoTrainingRound).filter_by(id=round_id).one().decision_code == "QUALITY_FAILED"


def test_local_evaluator_rejects_tampered_request_manifest(db, tmp_path, monkeypatch):
    _plan_id, _round_id, _job_id, evaluation_id = _prepare_evaluation(
        db, tmp_path, monkeypatch
    )
    row = db.query(AutoTrainingEvaluationRun).filter_by(id=evaluation_id).one()
    manifest = tmp_path / row.request_manifest_rel_path
    manifest.write_text("{}", encoding="utf-8")

    with pytest.raises(LocalEvaluationInputError, match="sha256 does not match"):
        with begin_transaction(db):
            run_local_simulated_evaluation(db, evaluation_run_id=evaluation_id)
