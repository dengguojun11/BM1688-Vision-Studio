from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import sessionmaker

from labelu.internal.application.service import auto_training_watchdog as service
from labelu.internal.application.service.auto_training_outbox import OutboxBatchResult
from labelu.internal.application.service.scheduler_lease import SchedulerLeaseToken
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.auto_training import (
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
)


@pytest.fixture(autouse=True)
def _enable_auto_training_plan_writes_for_existing_watchdog_tests(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)


class _Query:
    def __init__(self, values):
        self.values = values

    def filter(self, *_args):
        return self

    def filter_by(self, **_kwargs):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def all(self):
        return [(value,) for value in self.values]

    def one(self):
        return self.values


class _Session:
    def __init__(self, query_values):
        self.query_values = iter(query_values)
        self.closed = False

    def query(self, *_args):
        return _Query(next(self.query_values))

    def close(self):
        self.closed = True

    def rollback(self):
        pass


class _PagedQuery:
    def __init__(self, values):
        self.values = values

    def filter(self, *_args):
        return self

    def order_by(self, *_args):
        return self

    def limit(self, *_args):
        return self

    def all(self):
        return [(value,) for value in self.values]


class _PagedSession:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.queries = 0

    def get_bind(self):
        return object()

    def query(self, *_args):
        self.queries += 1
        return _PagedQuery(next(self.pages))


@pytest.fixture(autouse=True)
def _stub_plan_discovery(monkeypatch):
    monkeypatch.setattr(service, "discover_training_ready_plans", lambda _db: 0)
    monkeypatch.setattr(
        service, "reconcile_plan_owned_evaluation_datasets", lambda _db: 0
    )


def test_watchdog_discovers_draft_plans_when_runtime_switches_are_disabled(monkeypatch):
    session = _Session([])
    monkeypatch.setattr(
        service, "load_auto_training_switches", lambda: {"schema_ready": False}
    )
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(service, "discover_training_ready_plans", lambda _db: 2)
    monkeypatch.setattr(
        service, "reconcile_plan_owned_evaluation_datasets", lambda _db: 5
    )

    result = service.run_auto_training_watchdog_cycle(owner="test")

    assert result.enabled is False
    assert result.draft_plans_created == 2
    assert result.evaluation_datasets_approved == 5
    assert session.closed is True


def test_watchdog_cycle_processes_due_work_and_synchronizes_each_pipeline(monkeypatch):
    session = _Session([[11, 12], [21], [31, 32], []])
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(service, "begin_transaction", lambda _db: nullcontext())
    monkeypatch.setattr(
        service,
        "load_auto_training_switches",
        lambda: {
            "schema_ready": True,
            "outbox_enabled": True,
            "scheduler_enabled": True,
            "observe_only": False,
        },
    )
    monkeypatch.setattr(
        service,
        "process_outbox_batch",
        lambda *_args, **_kwargs: OutboxBatchResult(claimed=2, processed=2),
    )
    monkeypatch.setattr(
        service, "evaluate_due_plans", lambda *_args, **_kwargs: [1, 2, 3]
    )
    training = []
    bm1688 = []
    edge = []
    monkeypatch.setattr(
        service,
        "synchronize_training_job",
        lambda _db, *, job_id: training.append(job_id),
    )
    monkeypatch.setattr(
        service,
        "synchronize_bm1688_export",
        lambda _db, *, export_id: bm1688.append(export_id),
    )
    monkeypatch.setattr(
        service,
        "synchronize_edge_validation_job",
        lambda _db, *, edge_job_id: edge.append(edge_job_id),
    )

    result = service.run_auto_training_watchdog_cycle(owner="test")

    assert result.outbox.processed == 2
    assert result.due_plans == 3
    assert training == [11, 12]
    assert bm1688 == [21]
    assert edge == [31, 32]
    assert session.closed is True


def test_watchdog_id_batches_use_keyset_pagination_for_real_sessions():
    session = _PagedSession([[1, 2], [3], []])
    batches = list(
        service._iter_watchdog_id_batches(
            session,
            AutoTrainingPlan,
            filters=(),
            batch_size=2,
        )
    )
    assert batches == [[1, 2], [3]]
    assert session.queries == 2


def test_watchdog_injects_outbox_heartbeat_for_real_database_sessions(monkeypatch):
    session = _Session([[], [], [], []])
    session.get_bind = lambda: object()
    token = SchedulerLeaseToken(
        "auto_training_watchdog",
        "watchdog-heartbeat-test",
        9,
        datetime.now() + timedelta(seconds=30),
    )
    captured = {}
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        service,
        "load_auto_training_switches",
        lambda: {
            "schema_ready": True,
            "outbox_enabled": True,
            "scheduler_enabled": False,
        },
    )
    monkeypatch.setattr(service, "acquire_scheduler_lease", lambda *a, **k: token)
    monkeypatch.setattr(
        service,
        "renew_scheduler_lease",
        lambda _db, current, **_kwargs: current,
    )

    def process(_db, **kwargs):
        captured.update(kwargs)
        return OutboxBatchResult(claimed=0)

    monkeypatch.setattr(service, "process_outbox_batch", process)
    result = service.run_auto_training_watchdog_cycle(owner="watchdog-heartbeat-test")

    assert result.leader_acquired is True
    assert captured["lease_seconds"] == 60
    assert callable(captured["heartbeat"])
    assert session.closed is True


def test_watchdog_renews_scheduler_lease_between_pipeline_steps(monkeypatch):
    session = _Session([[], [], [], []])
    session.get_bind = lambda: object()
    token = SchedulerLeaseToken(
        "auto_training_watchdog",
        "watchdog-test",
        7,
        datetime.now() + timedelta(seconds=30),
    )
    renewals = []
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        service,
        "load_auto_training_switches",
        lambda: {
            "schema_ready": True,
            "outbox_enabled": False,
            "scheduler_enabled": False,
        },
    )
    monkeypatch.setattr(service, "acquire_scheduler_lease", lambda *a, **k: token)
    monkeypatch.setattr(
        service,
        "renew_scheduler_lease",
        lambda _db, current, **_kwargs: renewals.append(current) or current,
    )

    result = service.run_auto_training_watchdog_cycle(owner="watchdog-test")

    assert result.leader_acquired is True
    assert result.fencing_token == 7
    assert len(renewals) >= 2


def test_watchdog_stops_when_lease_renewal_is_lost(monkeypatch):
    session = _Session([[], [], [], []])
    session.get_bind = lambda: object()
    token = SchedulerLeaseToken(
        "auto_training_watchdog",
        "watchdog-test",
        8,
        datetime.now() + timedelta(seconds=30),
    )
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        service,
        "load_auto_training_switches",
        lambda: {
            "schema_ready": True,
            "outbox_enabled": False,
            "scheduler_enabled": False,
        },
    )
    monkeypatch.setattr(service, "acquire_scheduler_lease", lambda *a, **k: token)
    monkeypatch.setattr(service, "renew_scheduler_lease", lambda *_a, **_k: None)
    discovered = []
    monkeypatch.setattr(
        service,
        "discover_training_ready_plans",
        lambda _db: discovered.append(True) or 1,
    )

    result = service.run_auto_training_watchdog_cycle(owner="watchdog-test")

    assert result.leader_acquired is False
    assert discovered == []


def test_watchdog_terminal_sync_errors_do_not_prevent_other_jobs(monkeypatch):
    session = _Session([[11, 12], [], [], []])
    monkeypatch.setattr(service, "SessionLocal", lambda: session)
    monkeypatch.setattr(service, "begin_transaction", lambda _db: nullcontext())
    monkeypatch.setattr(
        service, "load_auto_training_switches", lambda: {"schema_ready": True}
    )
    seen = []

    def sync(_db, *, job_id):
        seen.append(job_id)
        if job_id == 11:
            raise ValueError("stale terminal state")

    monkeypatch.setattr(service, "synchronize_training_job", sync)

    result = service.run_auto_training_watchdog_cycle(owner="test")

    assert seen == [11, 12]
    assert result.training_jobs == 2


def test_watchdog_publish_sync_failure_isolated_per_round(db, monkeypatch):
    round_ids = []
    with begin_transaction(db):
        for index in (1, 2):
            plan = AutoTrainingPlan(
                owner_id=1,
                name=f"watchdog-publish-{index}",
                series_type="yolo_detection",
                dataset_category_id=1,
                label_set_signature=str(index) * 64,
                class_order_signature=str(index + 2) * 64,
                status="AUTO_QUALITY_REACHED",
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
                config_json="{}",
                config_sha256=str(index + 4) * 64,
                missing_fields_json="[]",
                validation_errors_json="[]",
                created_by=1,
            )
            db.add(version)
            db.flush()
            plan.current_version_id = version.id
            round_row = AutoTrainingRound(
                plan_id=plan.id,
                plan_version_id=version.id,
                round_no=1,
                state="PUBLISHING",
                trigger_type="AUTO",
                trigger_snapshot_json="{}",
                execution_signature=str(index + 6) * 64,
                stage_status_json="{}",
                stage_errors_json="{}",
                retry_count_json="{}",
                publish_job_id=f"publish-watchdog-{index}",
                publish_status="PROCESSING",
            )
            db.add(round_row)
            db.flush()
            round_ids.append(round_row.id)

    watchdog_session_factory = sessionmaker(autoflush=False, bind=db.get_bind())
    monkeypatch.setattr(service, "SessionLocal", watchdog_session_factory)
    monkeypatch.setattr(
        service,
        "load_auto_training_switches",
        lambda: {
            "schema_ready": True,
            "outbox_enabled": False,
            "scheduler_enabled": False,
        },
    )
    monkeypatch.setattr(
        service,
        "load_aicm_publish_job",
        lambda job_id: {"job_id": job_id, "status": "PROCESSING"},
    )
    seen = []

    def synchronize(_db, *, round_id, job_payload):
        seen.append((round_id, job_payload["job_id"]))
        if round_id == round_ids[0]:
            raise ValueError("first publication is corrupt")

    monkeypatch.setattr(service, "synchronize_aicm_publish_job", synchronize)

    result = service.run_auto_training_watchdog_cycle(owner="watchdog-test")

    assert result.publish_jobs == 2
    assert seen == [
        (round_ids[0], "publish-watchdog-1"),
        (round_ids[1], "publish-watchdog-2"),
    ]
