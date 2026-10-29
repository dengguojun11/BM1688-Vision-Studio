from __future__ import annotations

import json

from labelu.internal.application.service.auto_training_events import (
    enqueue_auto_training_event,
)
from labelu.internal.application.service.auto_training_outbox import (
    process_outbox_batch,
)
from labelu.internal.application.service.auto_training_reconciliation import (
    handle_outbox_event,
)
from labelu.internal.application.service.training_worker_persistence import (
    persist_training_worker_result,
)
from labelu.internal.application.service.training_worker_runtime import (
    OutboxResultReporter,
    process_worker_outbox_batch,
)
from labelu.internal.common.training_worker import (
    PlaceholderTrainingWorker,
    make_command,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.auto_training import AutoTrainingOutbox
from labelu.internal.domain.models.algorithm import AlgorithmDefinition, AlgorithmGroup
from labelu.internal.domain.models.dataset import Dataset, DatasetVersion
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _job(db, *, job_type="classification"):
    with begin_transaction(db):
        row = YoloTrainJob(
            task_id=1,
            created_by=1,
            status="PENDING",
            source_type="container",
            job_type=job_type,
        )
        db.add(row)
        db.flush()
        return int(row.id)


def test_worker_result_persistence_is_idempotent_and_maps_status(db):
    job_id = _job(db)
    result = {
        "protocol_version": "7gai.training.v1",
        "command_id": "persist-1",
        "adapter_key": "classification",
        "stage": "train",
        "status": "SUCCEEDED",
        "worker_id": "worker-a",
        "metrics": {"accuracy": 0.91},
    }
    with begin_transaction(db):
        assert persist_training_worker_result(db, job_id=job_id, result=result) is True
    row = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert row.status == "COMPLETED"
    assert json.loads(row.metrics_summary)["accuracy"] == 0.91
    with begin_transaction(db):
        assert persist_training_worker_result(db, job_id=job_id, result=result) is False


def test_worker_result_adapter_mismatch_cannot_mutate_job(db):
    job_id = _job(db, job_type="classification")
    result = {
        "protocol_version": "7gai.training.v1",
        "command_id": "wrong-adapter-1",
        "adapter_key": "segmentation",
        "stage": "train",
        "status": "SUCCEEDED",
        "worker_id": "untrusted-worker",
    }
    with begin_transaction(db):
        assert persist_training_worker_result(db, job_id=job_id, result=result) is False
    row = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert row.status == "PENDING"
    assert row.remote_status_json is None


def test_worker_result_requires_protocol_stage_and_worker_identity(db):
    job_id = _job(db)
    malformed = {
        "command_id": "malformed-result-1",
        "adapter_key": "classification",
        "status": "SUCCEEDED",
    }
    with begin_transaction(db):
        try:
            persist_training_worker_result(db, job_id=job_id, result=malformed)
        except ValueError as exc:
            assert "protocol" in str(exc)
        else:  # pragma: no cover - assertion guard
            raise AssertionError("malformed worker result was accepted")
    row = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert row.status == "PENDING"


def test_worker_result_defers_model_materialization_without_explicit_lineage(db):
    job_id = _job(db)
    result = {
        "protocol_version": "7gai.training.v1",
        "command_id": "lineage-deferred-1",
        "adapter_key": "classification",
        "stage": "export",
        "status": "SUCCEEDED",
        "worker_id": "worker-lineage",
        "model_lineage": {"display_name": "incomplete lineage"},
        "artifacts": [{"artifact_type": "onnx", "path": "model.onnx"}],
    }
    with begin_transaction(db):
        assert persist_training_worker_result(db, job_id=job_id, result=result) is True
    row = db.query(YoloTrainJob).filter_by(id=job_id).one()
    state = json.loads(row.remote_status_json)
    assert state["model_materialization"]["reason"] == "MODEL_LINEAGE_INCOMPLETE"
    assert "model_version_id" not in state["model_materialization"]


def test_successful_worker_result_materializes_model_lineage_artifact_and_gates(db):
    with begin_transaction(db):
        group = AlgorithmGroup(group_key="integration", name="Integration")
        db.add(group)
        db.flush()
        algorithm = AlgorithmDefinition(
            algorithm_key="integration_classifier",
            name="Integration classifier",
            task_type="classification",
            algorithm_group_id=group.id,
            training_adapter="classification",
            evaluation_adapter="classification",
            status="ACTIVE",
        )
        dataset = Dataset(
            owner_id=1,
            name="Frozen integration dataset",
            status="ACTIVE",
            created_by=1,
            updated_by=1,
        )
        db.add_all([algorithm, dataset])
        db.flush()
        dataset_version = DatasetVersion(
            dataset_id=dataset.id,
            version_no=1,
            status="FROZEN",
            manifest_rel_path="manifests/integration-v1.json",
            manifest_sha256="a" * 64,
            sample_count=12,
            quality_status="PASS",
            created_by=1,
            frozen_by=1,
        )
        job = YoloTrainJob(
            created_by=1,
            status="PENDING",
            source_type="container",
            job_type="classification",
        )
        db.add_all([dataset_version, job])
        db.flush()
        job_id = int(job.id)
        algorithm_id = int(algorithm.id)
        dataset_version_id = int(dataset_version.id)

    evidence = [
        {"gate_type": gate, "status": "PASS", "revision": 1}
        for gate in ("training", "generalization", "conversion", "edge", "release")
    ]
    result = {
        "protocol_version": "7gai.training.v1",
        "command_id": "lineage-materialize-1",
        "adapter_key": "classification",
        "stage": "export",
        "status": "SUCCEEDED",
        "worker_id": "integration-worker",
        "metrics": {"accuracy": 0.94},
        "model_lineage": {
            "algorithm_id": algorithm_id,
            "dataset_version_id": dataset_version_id,
            "display_name": "Integration classifier v1",
            "chip_type": "CPU",
            "precision": "FP32",
            "manifest": {"protocol": "7gai.model.v1"},
        },
        "artifacts": [
            {
                "artifact_type": "onnx",
                "path": "artifacts/integration.onnx",
                "sha256": "b" * 64,
                "size_bytes": 1024,
                "status": "READY",
            }
        ],
        "quality_evidence": evidence,
    }

    with begin_transaction(db):
        assert persist_training_worker_result(db, job_id=job_id, result=result) is True

    from labelu.internal.domain.models.model_version import (
        ModelArtifact,
        ModelQualityEvidence,
        ModelVersion,
    )

    job_state = json.loads(
        db.query(YoloTrainJob).filter_by(id=job_id).one().remote_status_json
    )
    model = db.query(ModelVersion).filter_by(training_job_id=job_id).one_or_none()
    assert model is not None, job_state
    assert model.algorithm_id == algorithm_id
    assert model.dataset_version_id == dataset_version_id
    assert model.status == "CANDIDATE"
    assert db.query(ModelArtifact).filter_by(model_version_id=model.id).count() == 1
    assert (
        db.query(ModelQualityEvidence)
        .filter_by(model_version_id=model.id, status="PASS")
        .count()
        == 5
    )
    state = json.loads(
        db.query(YoloTrainJob).filter_by(id=job_id).one().remote_status_json
    )
    assert state["model_materialization"]["status"] == "MATERIALIZED"
    assert state["model_materialization"]["model_version_id"] == model.id


def test_model_lineage_requires_frozen_dataset_version(db):
    with begin_transaction(db):
        group = AlgorithmGroup(group_key="lineage-integrity", name="Lineage integrity")
        db.add(group)
        db.flush()
        algorithm = AlgorithmDefinition(
            algorithm_key="lineage_integrity_classifier",
            name="Lineage integrity classifier",
            task_type="classification",
            algorithm_group_id=group.id,
            training_adapter="classification",
            evaluation_adapter="classification",
            status="ACTIVE",
        )
        dataset = Dataset(
            owner_id=1,
            name="unfrozen dataset",
            status="ACTIVE",
            created_by=1,
            updated_by=1,
        )
        db.add_all([algorithm, dataset])
        db.flush()
        version = DatasetVersion(
            dataset_id=dataset.id,
            version_no=1,
            status="INGESTING",
            manifest_rel_path="manifests/unfrozen.json",
            manifest_sha256="c" * 64,
            sample_count=1,
            created_by=1,
        )
        job = YoloTrainJob(
            created_by=1,
            status="PENDING",
            source_type="container",
            job_type="classification",
        )
        db.add_all([version, job])
        db.flush()
        job_id, algorithm_id, version_id = int(job.id), int(algorithm.id), int(version.id)
    result = {
        "protocol_version": "7gai.training.v1",
        "command_id": "foreign-dataset-1",
        "adapter_key": "classification",
        "stage": "export",
        "status": "SUCCEEDED",
        "worker_id": "worker-integrity",
        "model_lineage": {
            "algorithm_id": algorithm_id,
            "dataset_version_id": version_id,
        },
    }
    with begin_transaction(db):
        assert persist_training_worker_result(db, job_id=job_id, result=result) is True
    row = db.query(YoloTrainJob).filter_by(id=job_id).one()
    state = json.loads(row.remote_status_json)
    assert state["model_materialization"]["reason"] == "DATASET_VERSION_NOT_FROZEN"


def test_yolo_result_is_left_to_legacy_worker(db):
    job_id = _job(db, job_type="yolo_detection")
    result = {"command_id": "legacy-1", "status": "SUCCEEDED"}
    with begin_transaction(db):
        assert persist_training_worker_result(db, job_id=job_id, result=result) is False
    assert db.query(YoloTrainJob).filter_by(id=job_id).one().status == "PENDING"


def test_outbox_result_reporter_and_handler_form_idempotent_bridge(db):
    job_id = _job(db)
    result = {
        "protocol_version": "7gai.training.v1",
        "command_id": "outbox-1",
        "adapter_key": "classification",
        "stage": "evaluate",
        "status": "FAILED",
        "worker_id": "worker-b",
        "message": "container failed",
    }
    with begin_transaction(db):
        reporter = OutboxResultReporter(db=db, job_id=job_id)
        reporter.report(result, fencing_token=3)
        reporter.report(result, fencing_token=3)
    rows = (
        db.query(AutoTrainingOutbox)
        .filter_by(event_name="training.worker.result.v1")
        .all()
    )
    assert len(rows) == 1
    batch = process_outbox_batch(
        db,
        owner="worker-result-consumer",
        handler=handle_outbox_event,
        event_names=["training.worker.result.v1"],
    )
    assert batch.claimed == 1
    assert batch.processed == 1
    assert db.query(YoloTrainJob).filter_by(id=job_id).one().status == "FAILED"
    # Replaying the same event is a no-op because command_id is recorded.
    with begin_transaction(db):
        assert persist_training_worker_result(db, job_id=job_id, result=result) is False


def test_late_worker_result_cannot_regress_terminal_job_or_fencing_token(db):
    job_id = _job(db)
    completed = {
        "protocol_version": "7gai.training.v1",
        "command_id": "terminal-1",
        "adapter_key": "classification",
        "stage": "train",
        "status": "SUCCEEDED",
        "worker_id": "worker-new",
        "fencing_token": 9,
    }
    with begin_transaction(db):
        assert (
            persist_training_worker_result(db, job_id=job_id, result=completed) is True
        )

    stale = dict(completed)
    stale.update(command_id="terminal-stale", status="PROCESSING", fencing_token=8)
    with begin_transaction(db):
        assert persist_training_worker_result(db, job_id=job_id, result=stale) is False

    row = db.query(YoloTrainJob).filter_by(id=job_id).one()
    state = json.loads(row.remote_status_json)
    assert row.status == "COMPLETED"
    assert state["worker_fencing_token"] == 9
    assert "terminal-stale" not in state["worker_result_command_ids"]


def test_result_outbox_rejects_stale_fencing_token_before_job_mutation(db):
    job_id = _job(db)
    result = {
        "protocol_version": "7gai.training.v1",
        "command_id": "stale-outbox-1",
        "adapter_key": "classification",
        "stage": "train",
        "status": "SUCCEEDED",
        "worker_id": "old-worker",
        "fencing_token": 4,
        "metrics": {"accuracy": 0.1},
    }
    with begin_transaction(db):
        db.add(
            AutoTrainingOutbox(
                event_name="training.worker.result.v1",
                aggregate_type="yolo_train_job",
                aggregate_id=str(job_id),
                payload_json=json.dumps({"job_id": job_id, **result}),
                idempotency_key="training-worker-stale-result-1",
                status="PENDING",
            )
        )

    batch = process_worker_outbox_batch(
        db,
        worker=PlaceholderTrainingWorker(worker_id="result-consumer"),
        owner="result-consumer",
        fencing_check=lambda token: False,
    )

    assert batch.results.claimed == 1
    assert batch.results.processed == 1
    row = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert row.status == "PENDING"
    assert row.remote_status_json is None


def test_fenced_worker_progress_is_persisted_and_stale_progress_is_ignored(db):
    job_id = _job(db)
    current = {
        "protocol_version": "7gai.training.v1",
        "command_id": "progress-current",
        "adapter_key": "classification",
        "stage": "train",
        "status": "PROCESSING",
        "worker_id": "worker-current",
        "fencing_token": 7,
        "progress": 37.5,
    }
    with begin_transaction(db):
        assert persist_training_worker_result(
            db, job_id=job_id, result=current, fencing_check=lambda token: token == 7
        ) is True

    row = db.query(YoloTrainJob).filter_by(id=job_id).one()
    state = json.loads(row.remote_status_json)
    assert state["worker_progress"] == 37.5

    stale = dict(current)
    stale.update(command_id="progress-stale", fencing_token=6, progress=99)
    with begin_transaction(db):
        assert persist_training_worker_result(
            db, job_id=job_id, result=stale, fencing_check=lambda _token: False
        ) is False

    row = db.query(YoloTrainJob).filter_by(id=job_id).one()
    state = json.loads(row.remote_status_json)
    assert state["worker_progress"] == 37.5
    assert "progress-stale" not in state["worker_result_command_ids"]


def test_worker_command_outbox_is_consumed_and_result_is_applied_once(db):
    job_id = _job(db)
    command = make_command(
        adapter_key="classification",
        stage="train",
        request={"dataset_version_id": 1},
        command_id="command-bridge-1",
        container_image="registry.local/classifier:1",
    )
    with begin_transaction(db):
        enqueue_auto_training_event(
            db,
            event_name="training.worker.command.v1",
            aggregate_type="yolo_train_job",
            aggregate_id=str(job_id),
            payload={"job_id": job_id, "command": command},
            idempotency_key=f"training-worker-command:{job_id}:{command['command_id']}",
        )

    result = process_worker_outbox_batch(
        db,
        worker=PlaceholderTrainingWorker(worker_id="bridge-worker"),
        owner="bridge-worker-owner",
        fencing_check=lambda token: True,
    )
    assert result.commands.claimed == 1
    assert result.commands.processed == 1
    assert result.results.claimed == 1
    assert result.results.processed == 1
    updated = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert updated.status == "PROCESSING"
    payload = json.loads(updated.remote_status_json)
    assert payload["worker_result"]["command_id"] == "command-bridge-1"

    replay = process_worker_outbox_batch(
        db,
        worker=PlaceholderTrainingWorker(worker_id="bridge-worker"),
        owner="bridge-worker-owner",
        fencing_check=lambda token: True,
    )
    assert replay.commands.claimed == 0
    assert replay.results.claimed == 0
