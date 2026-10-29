from __future__ import annotations

import json

import pytest

from labelu.internal.application.service.training_worker_runtime import (
    ContainerRuntimePolicy,
    MemoryResultReporter,
    SafeContainerRunner,
    WorkerRetryPolicy,
    dispatch_with_retry,
    process_worker_command_event,
    process_worker_outbox_batch,
    redact_text,
)
from labelu.internal.common.training_worker import (
    PlaceholderTrainingWorker,
    make_command,
    result_envelope,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.auto_training import AutoTrainingOutbox
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _command(stage="train"):
    return make_command(
        adapter_key="classification",
        stage=stage,
        request={
            "dataset_version_id": 1,
            "model_lineage": {
                "project_id": 7,
                "dataset_version_id": 1,
                "manifest": {"sha256": "abc"},
            },
        },
        command_id="runtime-test-1",
        container_image="registry.local/classifier:1",
    )


def test_result_envelope_preserves_request_model_lineage():
    result = result_envelope(
        _command(), status="SUCCEEDED", worker_id="runtime-worker"
    )
    assert result["model_lineage"] == {
        "project_id": 7,
        "dataset_version_id": 1,
        "manifest": {"sha256": "abc"},
    }


def test_make_command_canonicalizes_nested_execution_mode():
    command = make_command(
        adapter_key="classification",
        stage="train",
        request={"execution_mode": "local", "dataset_version_id": 1},
        execution_mode="container",
        command_id="runtime-mode-contract-1",
        container_image="registry.local/classifier:1",
    )
    assert command["execution_mode"] == "container"
    assert command["request"]["execution_mode"] == "container"


def test_container_worker_preserves_worker_model_lineage_override():
    from labelu.internal.common.training_worker import ContainerTrainingWorker

    worker = ContainerTrainingWorker(
        worker_id="container-test",
        runner=lambda _argv: {
            "status": "SUCCEEDED",
            "model_lineage": {"project_id": 8, "source": "worker"},
        },
    )
    result = worker.execute(_command())
    assert result["model_lineage"] == {"project_id": 8, "source": "worker"}


def test_container_worker_normalizes_progress_in_result_envelope():
    from labelu.internal.common.training_worker import ContainerTrainingWorker

    worker = ContainerTrainingWorker(
        worker_id="container-progress",
        runner=lambda _argv: {"status": "PROCESSING", "progress": 37.5},
    )
    result = worker.execute(_command())
    assert result["progress"] == 37.5


def test_container_policy_allowlist_and_restricted_argv():
    policy = ContainerRuntimePolicy(
        allowed_images=("registry.local/classifier:1",),
        timeout_seconds=42,
        cpus=2,
        memory_mb=512,
    )
    runner = SafeContainerRunner(policy=policy, executable="podman")
    argv = runner.build_argv(_command())
    assert argv[:8] == [
        "podman",
        "run",
        "--rm",
        "--network",
        "none",
        "--cpus",
        "2",
        "--memory",
    ]
    assert "--read-only" in argv
    assert "--cap-drop" in argv
    assert "registry.local/classifier:1" in argv


def test_container_policy_rejects_unknown_image_and_invalid_limits():
    try:
        SafeContainerRunner(
            policy=ContainerRuntimePolicy(allowed_images=("safe:1",))
        ).build_argv(_command())
    except PermissionError as exc:
        assert "not allowlisted" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown image accepted")
    try:
        ContainerRuntimePolicy(
            allowed_images=("safe:1",), network="internet"
        ).validate()
    except ValueError as exc:
        assert "network" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid network accepted")


def test_runner_redacts_sensitive_output():
    assert "<redacted>" in redact_text("token=abc password:secret")
    assert "<redacted>" in redact_text('{"token": "abc", "password": "secret"}')
    seen = []

    def process(argv, timeout):
        seen.append((argv, timeout))
        return {"status": "SUCCEEDED", "message": "token=secret", "metrics": {"ok": 1}}

    runner = SafeContainerRunner(
        policy=ContainerRuntimePolicy(allowed_images=("registry.local/classifier:1",)),
        process_runner=process,
    )
    result = runner.execute(_command())
    assert result["status"] == "SUCCEEDED"
    assert "<redacted>" in result["message"]
    assert seen[0][1] == 3600


def test_dispatch_retries_then_reports_dead_letter():
    class FailingWorker:
        worker_id = "failing"

        def __init__(self):
            self.calls = 0

        def execute(self, command):
            self.calls += 1
            raise RuntimeError("password=bad")

    worker = FailingWorker()
    reporter = MemoryResultReporter()
    delays = []
    result = dispatch_with_retry(
        _command("evaluate"),
        worker=worker,
        reporter=reporter,
        retry_policy=WorkerRetryPolicy(max_attempts=3, base_delay_seconds=0.1),
        sleep=delays.append,
    )
    assert result["status"] == "DEAD_LETTER"
    assert worker.calls == 3
    assert delays == [0.1, 0.2]
    assert reporter.results == []
    assert "<redacted>" in result["message"]


def test_dispatch_fencing_rejects_stale_worker_before_report():
    reporter = MemoryResultReporter()
    result = dispatch_with_retry(
        _command("validate_runtime"),
        worker=PlaceholderTrainingWorker(worker_id="worker-a"),
        reporter=reporter,
        fencing_token=7,
        fencing_check=lambda token: False,
    )
    assert result["status"] == "FENCING_REJECTED"
    assert reporter.results == []


def test_dispatch_reads_fencing_token_from_command_and_checks_before_and_after():
    command = make_command(
        adapter_key="classification",
        stage="validate_runtime",
        request={"dataset_version_id": 1},
        command_id="fenced-command",
        fencing_token=8,
    )
    checks = []

    def check(token):
        checks.append(token)
        return len(checks) == 1

    reporter = MemoryResultReporter()
    result = dispatch_with_retry(
        command,
        worker=PlaceholderTrainingWorker(),
        reporter=reporter,
        fencing_check=check,
    )
    assert result["status"] == "FENCING_REJECTED"
    assert checks == [8, 8]
    assert reporter.results == []


def test_worker_outbox_command_result_loop_is_idempotent_and_persists_job(db):
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=1,
            created_by=1,
            status="PENDING",
            source_type="container",
            job_type="classification",
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)
        command = make_command(
            adapter_key="classification",
            stage="train",
            request={"dataset_version_id": 1},
            command_id="resident-command-1",
            container_image="registry.local/classifier:1",
        )
        payload = {"job_id": job_id, "command": command}
        db.add_all(
            [
                AutoTrainingOutbox(
                    event_name="training.worker.command.v1",
                    aggregate_type="yolo_train_job",
                    aggregate_id=str(job_id),
                    payload_json=json.dumps(payload),
                    idempotency_key="resident-command-event-a",
                    status="PENDING",
                ),
                AutoTrainingOutbox(
                    event_name="training.worker.command.v1",
                    aggregate_type="yolo_train_job",
                    payload_json=json.dumps(payload),
                    aggregate_id=str(job_id),
                    idempotency_key="resident-command-event-b",
                    status="PENDING",
                ),
            ]
        )

    class Worker:
        worker_id = "resident-worker"

        def execute(self, received):
            return result_envelope(
                received,
                status="SUCCEEDED",
                worker_id=self.worker_id,
                metrics={"accuracy": 0.9},
            )

    result = process_worker_outbox_batch(
        db,
        worker=Worker(),
        owner="resident-worker",
        lease_seconds=60,
        limit=20,
    )

    assert result.commands.claimed == 2
    assert result.commands.processed == 2
    assert result.results.claimed == 1
    assert result.results.processed == 1
    row = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert row.status == "COMPLETED"
    events = (
        db.query(AutoTrainingOutbox)
        .filter_by(event_name="training.worker.result.v1")
        .all()
    )
    assert len(events) == 1


def test_worker_command_rejects_cross_project_routing(db):
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=1,
            project_id=7,
            created_by=1,
            status="PENDING",
            source_type="container",
            job_type="classification",
        )
        db.add(job)
        db.flush()
        row = AutoTrainingOutbox(
            event_name="training.worker.command.v1",
            aggregate_type="yolo_train_job",
            aggregate_id=str(job.id),
            payload_json=json.dumps(
                {
                    "job_id": job.id,
                    "project_id": 8,
                    "command": _command(),
                }
            ),
            idempotency_key="cross-project-command",
            status="PROCESSING",
        )
        db.add(row)
        db.flush()

    class Worker:
        worker_id = "must-not-run"

        def execute(self, _command):
            raise AssertionError("cross-project command reached worker")

    with pytest.raises(ValueError, match="project_id"):
        process_worker_command_event(db, row, worker=Worker())


def test_phase_one_worker_claims_only_manual_lineage_and_preserves_auto_events(
    db, monkeypatch
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)
    with begin_transaction(db):
        jobs = [
            YoloTrainJob(
                task_id=1,
                created_by=1,
                status="PENDING",
                source_type="container",
                job_type="classification",
                queue_source="MANUAL",
            ),
            YoloTrainJob(
                task_id=1,
                created_by=1,
                status="PENDING",
                source_type="container",
                job_type="classification",
                queue_source="AUTO",
            ),
            YoloTrainJob(
                task_id=1,
                created_by=1,
                status="PENDING",
                source_type="container",
                job_type="classification",
                auto_training_plan_id=901,
            ),
            YoloTrainJob(
                task_id=1,
                created_by=1,
                status="PENDING",
                source_type="container",
                job_type="classification",
                auto_training_round_id=902,
            ),
        ]
        db.add_all(jobs)
        db.flush()
        manual_id = int(jobs[0].id)
        automatic_ids = [int(job.id) for job in jobs[1:]]
        command_rows = []
        for job in jobs:
            command = make_command(
                adapter_key="classification",
                stage="train",
                request={"dataset_version_id": 1},
                command_id=f"phase-one-command-{job.id}",
                container_image="registry.local/classifier:1",
            )
            row = AutoTrainingOutbox(
                event_name="training.worker.command.v1",
                aggregate_type="yolo_train_job",
                aggregate_id=str(job.id),
                payload_json=json.dumps({"job_id": job.id, "command": command}),
                idempotency_key=f"phase-one-command-event-{job.id}",
                status="PENDING",
            )
            db.add(row)
            command_rows.append(row)
        blocked_result = AutoTrainingOutbox(
            event_name="training.worker.result.v1",
            aggregate_type="yolo_train_job",
            aggregate_id=str(automatic_ids[0]),
            payload_json=json.dumps(
                {
                    "job_id": automatic_ids[0],
                    "command_id": "historical-auto-result",
                    "status": "SUCCEEDED",
                }
            ),
            idempotency_key="phase-one-historical-auto-result",
            status="PENDING",
        )
        db.add(blocked_result)
        db.flush()
        automatic_event_ids = [int(row.id) for row in command_rows[1:]] + [
            int(blocked_result.id)
        ]

    executed: list[str] = []

    class Worker:
        worker_id = "phase-one-manual-worker"

        def execute(self, command):
            executed.append(str(command["command_id"]))
            return result_envelope(
                command,
                status="SUCCEEDED",
                worker_id=self.worker_id,
            )

    result = process_worker_outbox_batch(
        db,
        worker=Worker(),
        owner="phase-one-worker",
        lease_seconds=60,
        limit=20,
    )

    assert result.commands.claimed == 1
    assert result.commands.processed == 1
    assert result.results.claimed == 1
    assert result.results.processed == 1
    assert executed == [f"phase-one-command-{manual_id}"]
    blocked_rows = (
        db.query(AutoTrainingOutbox)
        .filter(AutoTrainingOutbox.id.in_(automatic_event_ids))
        .all()
    )
    assert len(blocked_rows) == 4
    assert all(row.status == "PENDING" for row in blocked_rows)
    assert all(row.lease_owner is None for row in blocked_rows)
    assert all(row.lease_expires_at is None for row in blocked_rows)
    assert (
        db.query(AutoTrainingOutbox)
        .filter(
            AutoTrainingOutbox.event_name == "training.worker.result.v1",
            AutoTrainingOutbox.aggregate_id.in_([str(value) for value in automatic_ids]),
        )
        .count()
        == 1
    )
    assert db.query(YoloTrainJob).filter_by(id=manual_id).one().status == "COMPLETED"
    assert all(
        db.query(YoloTrainJob).filter_by(id=job_id).one().status == "PENDING"
        for job_id in automatic_ids
    )
