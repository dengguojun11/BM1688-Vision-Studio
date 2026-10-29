import multiprocessing as mp
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from labelu.internal.adapter.persistence import crud_experiment_dataset_import
from labelu.internal.application.service import experiment_dataset_import_worker as worker
from labelu.internal.domain.models.experiment_dataset_import import ExperimentDatasetImport


def _claim_import_process(db_path: str, owner: str, output) -> None:
    """跨进程调用真实回流领取 CRUD；不模拟 Worker 内存锁。"""
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": 90},
    )
    factory = sessionmaker(bind=engine)
    try:
        with factory() as db:
            with db.begin():
                claimed = crud_experiment_dataset_import.claim_pending(
                    db,
                    owner=owner,
                    limit=1,
                    lease_seconds=120,
                    now=datetime(2026, 9, 20, 10, 0, 0),
                )
            output.put(
                {
                    "owner": owner,
                    "claimed": claimed,
                }
            )
    except Exception as exc:  # pragma: no cover - parent reports child failures
        output.put({"owner": owner, "error": repr(exc)})
    finally:
        engine.dispose()


def _factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'experiment-import-worker.sqlite'}")
    ExperimentDatasetImport.__table__.create(engine)
    return engine, sessionmaker(bind=engine)


def test_claim_is_fenced_and_stale_lease_can_be_taken_over(tmp_path):
    engine, factory = _factory(tmp_path)
    with factory() as db:
        row = ExperimentDatasetImport(
            package_id="pkg-1",
            source_type="third_party",
            dataset_type="problem",
            dataset_date="2026-09-19",
            event_type="helmet",
            model_id="534",
            source_payload_json="{}",
            formal_task_ids_json="[]",
            status="PENDING",
        )
        db.add(row)
        db.commit()
        with db.begin():
            first = crud_experiment_dataset_import.claim_pending(
                db, owner="worker-a", limit=1, lease_seconds=60
            )
        assert first and first[0][1] == 1
        with db.begin():
            second = crud_experiment_dataset_import.claim_pending(
                db, owner="worker-b", limit=1, lease_seconds=60
            )
        assert second == []
        row.worker_lease_expires_at = datetime.now() - timedelta(seconds=1)
        db.commit()
        with db.begin():
            takeover = crud_experiment_dataset_import.claim_pending(
                db, owner="worker-b", limit=1, lease_seconds=60
            )
        assert takeover and takeover[0][1] == 2
        assert crud_experiment_dataset_import.release_worker_lease(
            db,
            import_job_id=row.id,
            owner="worker-a",
            fencing_token=1,
        ) is False
        db.commit()
        with db.begin():
            assert crud_experiment_dataset_import.renew_worker_lease(
                db,
                import_job_id=row.id,
                owner="worker-b",
                fencing_token=2,
                lease_seconds=60,
            ) is True
        with db.begin():
            assert crud_experiment_dataset_import.renew_worker_lease(
                db,
                import_job_id=row.id,
                owner="worker-a",
                fencing_token=1,
                lease_seconds=60,
            ) is False
    engine.dispose()


def test_claim_prioritizes_new_packages_over_requeued_waiting_rows(tmp_path):
    """A retrying WAITING row must not starve a newly accepted package."""
    engine, factory = _factory(tmp_path)
    current = datetime(2026, 9, 20, 10, 0, 0)
    with factory() as db:
        old_waiting = ExperimentDatasetImport(
            package_id="pkg-requeued-waiting",
            source_type="third_party",
            dataset_type="problem",
            dataset_date="2026-09-20",
            event_type="helmet",
            model_id="534",
            source_payload_json="{}",
            formal_task_ids_json="[]",
            status="PENDING",
            attempt_count=2,
            updated_at=current,
        )
        new_package = ExperimentDatasetImport(
            package_id="pkg-new-pending",
            source_type="third_party",
            dataset_type="problem",
            dataset_date="2026-09-20",
            event_type="helmet",
            model_id="534",
            source_payload_json="{}",
            formal_task_ids_json="[]",
            status="PENDING",
            attempt_count=0,
            updated_at=current,
        )
        db.add_all([old_waiting, new_package])
        db.commit()

        with db.begin():
            claimed = crud_experiment_dataset_import.claim_pending(
                db,
                owner="worker-fair",
                limit=1,
                lease_seconds=60,
                now=current,
            )

        assert claimed == [(int(new_package.id), 1)]
    engine.dispose()


def test_expired_worker_generation_cannot_write_before_takeover(tmp_path):
    engine, factory = _factory(tmp_path)
    with factory() as db:
        row = ExperimentDatasetImport(
            package_id="pkg-expired-write",
            source_type="third_party",
            dataset_type="problem",
            dataset_date="2026-09-20",
            event_type="helmet",
            model_id="534",
            source_payload_json="{}",
            formal_task_ids_json="[]",
            status="PENDING",
        )
        db.add(row)
        db.commit()
        with db.begin():
            claimed = crud_experiment_dataset_import.claim_pending(
                db, owner="worker-a", limit=1, lease_seconds=60
            )
        assert claimed and claimed[0][1] == 1
        row.worker_lease_expires_at = datetime.now() - timedelta(seconds=1)
        db.commit()

        with db.begin():
            assert (
                crud_experiment_dataset_import.update_by_worker_lease(
                    db,
                    import_job_id=row.id,
                    owner="worker-a",
                    fencing_token=1,
                    obj_in={ExperimentDatasetImport.status.key: "COMPLETED"},
                )
                is False
            )
        db.expire_all()
        current = db.query(ExperimentDatasetImport).one()
        assert current.status == "PENDING"
    engine.dispose()


def test_worker_disabled_is_fail_closed(monkeypatch):
    monkeypatch.setattr(worker.settings, "EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED", False)
    result = worker.run_import_worker_once(lambda: None)
    assert result.enabled is False


def test_worker_executes_claimed_job(monkeypatch):
    class FakeDb:
        def close(self):
            return None

    claimed = []
    monkeypatch.setattr(worker.settings, "EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(worker, "dedicated_worker_ready", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(
        worker.crud_experiment_dataset_import,
        "requeue_waiting",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        worker.crud_experiment_dataset_import,
        "claim_pending",
        lambda *args, **kwargs: [(7, 3)],
    )
    monkeypatch.setattr(worker, "run_import_job_now", lambda *args, **kwargs: claimed.append((args, kwargs)))
    monkeypatch.setattr(worker, "begin_transaction", lambda db: _NoopContext())
    result = worker.run_import_worker_once(lambda: FakeDb(), owner="w", limit=1)
    assert result.claimed == 1
    assert claimed == [((7,), {"lease_owner": "w", "fencing_token": 3})]


def test_worker_continues_after_one_job_failure(monkeypatch):
    class FakeDb:
        def close(self):
            return None

    monkeypatch.setattr(worker.settings, "EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(worker, "dedicated_worker_ready", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(
        worker.crud_experiment_dataset_import,
        "requeue_waiting",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        worker.crud_experiment_dataset_import,
        "claim_pending",
        lambda *args, **kwargs: [(7, 3), (8, 4)],
    )
    monkeypatch.setattr(worker, "begin_transaction", lambda db: _NoopContext())
    calls = []

    def run_job(job_id, **kwargs):
        calls.append((job_id, kwargs))
        if job_id == 7:
            raise RuntimeError("simulated S3 timeout")

    monkeypatch.setattr(worker, "run_import_job_now", run_job)
    result = worker.run_import_worker_once(lambda: FakeDb(), owner="w", limit=2)
    assert result.claimed == 2
    assert result.executed == 1
    assert result.failed == 1
    assert [item[0] for item in calls] == [7, 8]


def test_worker_does_not_claim_when_programmatic_readiness_fails(monkeypatch):
    class FakeDb:
        def close(self):
            return None

    monkeypatch.setattr(worker.settings, "EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(
        worker,
        "dedicated_worker_ready",
        lambda *args, **kwargs: (False, "数据库缺少 Worker 租约字段"),
    )
    claimed = []
    monkeypatch.setattr(
        worker.crud_experiment_dataset_import,
        "claim_pending",
        lambda *args, **kwargs: claimed.append(True),
    )
    monkeypatch.setattr(
        worker.crud_experiment_dataset_import,
        "requeue_waiting",
        lambda *args, **kwargs: claimed.append(True),
    )

    result = worker.run_import_worker_once(lambda: FakeDb(), owner="w", limit=1)

    assert result.enabled is True
    assert result.ready is False
    assert result.reason == "数据库缺少 Worker 租约字段"
    assert claimed == []


def test_waiting_import_is_requeued_after_cooldown(tmp_path):
    engine, factory = _factory(tmp_path)
    old = datetime(2026, 9, 20, 9, 0, 0)
    current = datetime(2026, 9, 20, 10, 0, 0)
    with factory() as db:
        db.add(
            ExperimentDatasetImport(
                package_id="pkg-waiting-requeue",
                source_type="third_party",
                dataset_type="problem",
                dataset_date="2026-09-20",
                event_type="helmet",
                model_id="534",
                source_payload_json="{}",
                formal_task_ids_json="[]",
                status="WAITING_FOR_MODEL",
                updated_at=old,
            )
        )
        db.commit()
        with db.begin():
            requeued = crud_experiment_dataset_import.requeue_waiting(
                db,
                min_age_seconds=30,
                now=current,
            )
        assert requeued
        row = db.query(ExperimentDatasetImport).one()
        assert row.status == "PENDING"
        assert row.last_error is None
        assert row.worker_lease_owner is None
    engine.dispose()


def test_worker_requeues_waiting_rows_before_claim(monkeypatch):
    class FakeDb:
        def close(self):
            return None

    monkeypatch.setattr(worker.settings, "EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(worker, "dedicated_worker_ready", lambda *args, **kwargs: (True, "ok"))
    requeue_kwargs = []

    def requeue_waiting(*args, **kwargs):
        requeue_kwargs.append(kwargs)
        return [12]

    monkeypatch.setattr(
        worker.crud_experiment_dataset_import,
        "requeue_waiting",
        requeue_waiting,
    )
    monkeypatch.setattr(
        worker.crud_experiment_dataset_import,
        "claim_pending",
        lambda *args, **kwargs: [(7, 3)],
    )
    monkeypatch.setattr(worker, "begin_transaction", lambda db: _NoopContext())
    monkeypatch.setattr(worker, "run_import_job_now", lambda *args, **kwargs: None)
    result = worker.run_import_worker_once(lambda: FakeDb(), owner="w", limit=1)
    assert result.requeued == 1
    assert result.claimed == 1
    assert requeue_kwargs == [
        {
            "limit": 1,
            "status": "WAITING_FOR_SAMPLES",
            "min_age_seconds": worker.settings.EXPERIMENT_DATASET_WAITING_REQUEUE_SECONDS,
            "now": requeue_kwargs[0]["now"],
        }
    ]


def test_worker_does_not_periodically_requeue_missing_model_or_category(monkeypatch):
    """External prerequisites are woken explicitly, not retried every scan."""
    class FakeDb:
        def close(self):
            return None

    monkeypatch.setattr(worker.settings, "EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(worker, "dedicated_worker_ready", lambda *args, **kwargs: (True, "ok"))
    calls = []

    def requeue_waiting(*args, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(worker.crud_experiment_dataset_import, "requeue_waiting", requeue_waiting)
    monkeypatch.setattr(worker.crud_experiment_dataset_import, "claim_pending", lambda *args, **kwargs: [])
    monkeypatch.setattr(worker, "begin_transaction", lambda db: _NoopContext())

    result = worker.run_import_worker_once(lambda: FakeDb(), owner="w", limit=1)

    assert result.requeued == 0
    assert calls and calls[0]["status"] == "WAITING_FOR_SAMPLES"


def test_worker_preflight_requires_lease_columns(tmp_path, monkeypatch):
    _engine, factory = _factory(tmp_path)
    monkeypatch.setattr(worker.settings, "EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED", True)
    ok, reason = worker.dedicated_worker_ready(factory)
    assert ok is True
    assert reason == "ok"


@pytest.mark.parametrize("concurrency", [2, 10, 20, 50])
def test_s3_import_claim_has_one_winner_at_local_concurrency_levels(
    tmp_path, concurrency
):
    """本地独立进程验证回流领取的 2/10/20/50 竞争边界。"""
    engine, factory = _factory(tmp_path)
    db_path = str(tmp_path / "experiment-import-worker.sqlite")
    with factory() as db:
        db.add(
            ExperimentDatasetImport(
                package_id="pkg-concurrency",
                source_type="third_party",
                dataset_type="problem",
                dataset_date="2026-09-20",
                event_type="helmet",
                model_id="534",
                source_payload_json="{}",
                formal_task_ids_json="[]",
                status="PENDING",
            )
        )
        db.commit()

    context = mp.get_context("spawn")
    output = context.Queue()
    processes = [
        context.Process(
            target=_claim_import_process,
            args=(db_path, f"s3-worker-{index}", output),
        )
        for index in range(concurrency)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=120)
            assert not process.is_alive()
            assert process.exitcode == 0

        records = [output.get(timeout=30) for _ in processes]
        assert all("error" not in record for record in records), records
        winners = [record for record in records if record["claimed"]]
        assert len(winners) == 1
        assert len(winners[0]["claimed"]) == 1
        with factory() as db:
            row = db.query(ExperimentDatasetImport).one()
            assert row.worker_lease_owner == winners[0]["owner"]
            assert row.worker_fencing_token == 1
            assert row.attempt_count == 1
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        engine.dispose()


def test_worker_main_preflight_returns_config_error_when_schema_missing(monkeypatch):
    monkeypatch.setattr(worker.settings, "EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED", True)
    monkeypatch.setattr(worker, "dedicated_worker_ready", lambda: (False, "missing lease"))
    assert worker.main(["--preflight"]) == worker.WORKER_CONFIG_EXIT_CODE


class _NoopContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
