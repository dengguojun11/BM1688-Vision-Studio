from __future__ import annotations

import json
import multiprocessing
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from labelu.internal.application.service.auto_training_dispatch import (
    _claim_edge_validation_dispatch,
    _claim_training_dispatch,
    _claim_bm1688_dispatch,
    claim_bm1688_recovery,
    claim_edge_validation_recovery,
    dispatch_bm1688_export,
    dispatch_edge_validation_job,
    dispatch_training_job,
    enqueue_training_worker_command,
    release_bm1688_recovery,
    release_edge_validation_recovery,
)
from labelu.internal.application.service.auto_training_dispatch import (
    _edge_pack_signature,
    _replace_edge_pack_staging,
)
from labelu.internal.domain.models.auto_training import (
    AutoTrainingOutbox,
    AutoTrainingPlan,
)
from labelu.internal.application.service.auto_training_outbox import OutboxDeferredError
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob
from labelu.internal.domain.models.task import Task


def _edge_dispatch_claim_process(db_path: str, start_event, ready_queue, result_queue):
    from labelu.internal.application.service.auto_training_dispatch import (
        _claim_edge_validation_dispatch,
    )

    engine = create_engine(
        f"sqlite:///{Path(db_path).resolve().as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    factory = sessionmaker(bind=engine, autoflush=False)
    try:
        ready_queue.put(os.getpid())
        if not start_event.wait(timeout=20):
            raise RuntimeError("claim start barrier timed out")
        with factory() as db:
            with db.begin():
                claimed, owner = _claim_edge_validation_dispatch(
                    db, edge_job_id=1, now=datetime.now()
                )
            result_queue.put({"pid": os.getpid(), "claimed": claimed, "owner": owner})
    except Exception as exc:  # pragma: no cover - surfaced through parent assertions
        result_queue.put({"pid": os.getpid(), "error": repr(exc)})
    finally:
        engine.dispose()


def _prepare_edge_claim_db(path: Path) -> None:
    engine = create_engine(f"sqlite:///{path.resolve().as_posix()}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE yolo_edge_validation_job (
                        id INTEGER PRIMARY KEY,
                        status VARCHAR(32) NOT NULL,
                        auto_training_round_id INTEGER,
                        pipeline_origin VARCHAR(32),
                        queue_lease_owner VARCHAR(128),
                        queue_lease_expires_at DATETIME,
                        execution_lease_owner VARCHAR(128),
                        execution_lease_expires_at DATETIME,
                        execution_fencing_token INTEGER NOT NULL DEFAULT 0,
                        updated_at DATETIME
                    )
                    """
                )
            )
            connection.execute(
                text(
                    "INSERT INTO yolo_edge_validation_job "
                    "(id,status,auto_training_round_id,pipeline_origin) "
                    "VALUES (1,'PENDING',7,'AUTO_QUALITY')"
                )
            )
    finally:
        engine.dispose()


def test_dispatch_is_deferred_while_train_switch_is_disabled(db):
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=1,
            created_by=1,
            status="PENDING",
            source_type="auto_training",
            queue_source="AUTO",
            auto_training_plan_id=1,
            auto_training_round_id=1,
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        job_id = job.id

    with pytest.raises(OutboxDeferredError):
        dispatch_training_job(
            db,
            job_id=job_id,
            switches={"train_enabled": False},
            submit=lambda *_args, **_kwargs: pytest.fail("must not submit"),
        )


def test_dispatch_submits_auto_job_once_when_enabled(db):
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=9,
            created_by=1,
            status="PENDING",
            source_type="auto_training",
            queue_source="AUTO",
            auto_training_plan_id=1,
            auto_training_round_id=1,
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        job_id = job.id

    calls = []
    assert dispatch_training_job(
        db,
        job_id=job_id,
        switches={"train_enabled": True},
        submit=lambda jid, tid, _cmd: calls.append((jid, tid)),
    )
    assert calls == [(job_id, 9)]


def test_dispatch_claims_auto_job_and_blocks_second_dispatcher(db):
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=9,
            created_by=1,
            status="PENDING",
            source_type="auto_training",
            queue_source="AUTO",
            auto_training_plan_id=1,
            auto_training_round_id=77,
            model_weights="yolo11n.pt",
            queue_lease_expires_at=datetime.now() - timedelta(seconds=1),
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    calls = []
    assert dispatch_training_job(
        db,
        job_id=job_id,
        switches={"train_enabled": True},
        submit=lambda jid, tid, _cmd: calls.append((jid, tid)),
    )
    assert not dispatch_training_job(
        db,
        job_id=job_id,
        switches={"train_enabled": True},
        submit=lambda *_args: calls.append(("duplicate",)),
    )
    assert calls == [(job_id, 9)]


def test_dispatch_releases_claim_when_submit_fails(db):
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=9,
            created_by=1,
            status="PENDING",
            source_type="auto_training",
            queue_source="AUTO",
            auto_training_plan_id=1,
            auto_training_round_id=78,
            model_weights="yolo11n.pt",
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    def fail_submit(*_args):
        raise RuntimeError("submit failed")

    with pytest.raises(RuntimeError, match="submit failed"):
        dispatch_training_job(
            db,
            job_id=job_id,
            switches={"train_enabled": True},
            submit=fail_submit,
        )
    released = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert released.queue_lease_owner is None
    assert released.queue_lease_expires_at <= datetime.now()


def test_training_dispatch_fails_closed_on_owned_queue_lease_without_expiry(db):
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=9,
            created_by=1,
            status="PENDING",
            source_type="auto_training",
            queue_source="AUTO",
            auto_training_plan_id=1,
            auto_training_round_id=79,
            model_weights="yolo11n.pt",
            queue_lease_owner="orphaned-worker",
            queue_lease_expires_at=None,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    claimed, _owner = _claim_training_dispatch(
        db, job_id=job_id, now=datetime.now()
    )
    assert claimed is False
    current = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert current.queue_lease_owner == "orphaned-worker"


def test_bm1688_dispatch_fails_closed_on_owned_execution_lease_without_expiry(db):
    with begin_transaction(db):
        row = YoloBm1688Export(
            user_id=1,
            task_id=1,
            yolo_train_job_id=1,
            status="PENDING",
            auto_training_plan_id=1,
            auto_training_round_id=105,
            execution_signature="malformed-bm-lease",
            pipeline_origin="AUTO_QUALITY",
            execution_lease_owner="orphaned-compiler",
            execution_lease_expires_at=None,
        )
        db.add(row)
        db.flush()
        export_id = int(row.id)

    claimed, _owner = _claim_bm1688_dispatch(
        db, export_id=export_id, now=datetime.now()
    )
    assert claimed is False
    recovery_claimed, _recovery_owner = claim_bm1688_recovery(
        db, export_id=export_id, status="PENDING", now=datetime.now()
    )
    assert recovery_claimed is False
    current = db.query(YoloBm1688Export).filter_by(id=export_id).one()
    assert current.queue_lease_owner is None


def test_edge_dispatch_fails_closed_on_owned_queue_lease_without_expiry(db):
    with begin_transaction(db):
        row = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=1,
            status="PENDING",
            mode="run_only",
            auto_training_plan_id=1,
            auto_training_round_id=106,
            execution_signature="malformed-edge-lease",
            pipeline_origin="AUTO_QUALITY",
            queue_lease_owner="orphaned-edge-worker",
            queue_lease_expires_at=None,
        )
        db.add(row)
        db.flush()
        edge_job_id = int(row.id)

    claimed, _owner = _claim_edge_validation_dispatch(
        db, edge_job_id=edge_job_id, now=datetime.now()
    )
    assert claimed is False
    recovery_claimed, _recovery_owner = claim_edge_validation_recovery(
        db, edge_job_id=edge_job_id, status="PENDING", now=datetime.now()
    )
    assert recovery_claimed is False
    current = db.query(YoloEdgeValidationJob).filter_by(id=edge_job_id).one()
    assert current.queue_lease_owner == "orphaned-edge-worker"


def test_non_yolo_dispatch_publishes_idempotent_worker_command(db, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_algorithm.get_definition",
        lambda *_args, **_kwargs: SimpleNamespace(id=42),
    )
    with begin_transaction(db):
        plan = AutoTrainingPlan(
            project_id=7,
            owner_id=1,
            name="classification-plan",
            series_type="classification",
            dataset_category_id=1,
            label_set_signature="a" * 64,
            class_order_signature="b" * 64,
            source_dataset_version_id=9,
            status="ACTIVE",
            created_by=1,
            updated_by=1,
        )
        db.add(plan)
        db.flush()
        job = YoloTrainJob(
            task_id=9,
            project_id=7,
            created_by=1,
            status="PENDING",
            source_type="auto_training",
            job_type="classification",
            auto_training_plan_id=plan.id,
            auto_training_round_id=1,
            model_weights="classifier.pt",
            hyperparams_json='{"container_image":"registry.local/classifier:1"}',
            execution_signature="c" * 64,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    switches = {"train_enabled": True, "worker_command_enabled": True}
    assert dispatch_training_job(db, job_id=job_id, switches=switches)
    assert dispatch_training_job(db, job_id=job_id, switches=switches)
    events = (
        db.query(AutoTrainingOutbox)
        .filter_by(event_name="training.worker.command.v1")
        .all()
    )
    assert len(events) == 1
    payload = json.loads(events[0].payload_json)
    assert payload["job_id"] == job_id
    assert payload["project_id"] == 7
    assert payload["command"]["adapter_key"] == "classification"
    assert payload["command"]["container"]["image"] == "registry.local/classifier:1"
    assert payload["command"]["request"]["model_lineage"] == {
        "project_id": 7,
        "algorithm_id": 42,
        "dataset_version_id": 9,
        "display_name": f"classification training job #{job_id}",
        "chip_type": None,
        "precision": None,
        "manifest": {},
    }


def test_manual_unified_job_publishes_model_lineage_from_dataset_binding(db, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_algorithm.get_definition",
        lambda *_args, **_kwargs: SimpleNamespace(id=43, algorithm_key="classification"),
    )
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=19,
            project_id=7,
            created_by=1,
            status="PENDING",
            source_type="internal",
            job_type="classification",
            model_weights="classifier.pt",
            hyperparams_json='{"dataset_version_id": 12, "container_image": "registry.local/classifier:1"}',
            execution_signature="m" * 64,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    enqueue_training_worker_command(db, job=db.query(YoloTrainJob).filter_by(id=job_id).one())
    event = (
        db.query(AutoTrainingOutbox)
        .filter_by(event_name="training.worker.command.v1")
        .one()
    )
    payload = json.loads(event.payload_json)
    assert payload["command"]["request"]["model_lineage"] == {
        "project_id": 7,
        "algorithm_id": 43,
        "dataset_version_id": 12,
        "display_name": f"classification training job #{job_id}",
        "chip_type": None,
        "precision": None,
        "manifest": {},
    }


def test_iteration_dispatch_preserves_the_real_parent_model_version(db, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_algorithm.get_definition",
        lambda *_args, **_kwargs: SimpleNamespace(id=43, algorithm_key="yolo_detection"),
    )
    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_model_version.get_by_training_job",
        lambda _db, job_id: SimpleNamespace(id=88, algorithm_id=43) if job_id == 77 else None,
    )
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=19,
            project_id=7,
            created_by=1,
            status="PENDING",
            source_type="internal",
            job_type="yolo_detection",
            model_weights="trained:77",
            hyperparams_json='{"dataset_version_id": 12}',
            execution_signature="i" * 64,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    enqueue_training_worker_command(db, job=db.query(YoloTrainJob).filter_by(id=job_id).one())
    event = db.query(AutoTrainingOutbox).filter_by(event_name="training.worker.command.v1").one()
    payload = json.loads(event.payload_json)
    assert payload["command"]["request"]["model_lineage"]["parent_model_version_id"] == 88


def test_shared_yolo_adapter_preserves_product_algorithm_lineage(db, monkeypatch):
    """A product capability must not collapse to the shared YOLO adapter key."""
    from types import SimpleNamespace

    seen_keys = []

    def lookup(_db, key):
        seen_keys.append(key)
        if key == "street_litter_detection":
            return SimpleNamespace(
                id=44,
                algorithm_key=key,
                training_adapter="yolo_detection",
            )
        return None

    monkeypatch.setattr(
        "labelu.internal.adapter.persistence.crud_algorithm.get_definition", lookup
    )
    with begin_transaction(db):
        job = YoloTrainJob(
            task_id=29,
            project_id=8,
            created_by=1,
            status="PENDING",
            source_type="internal",
            job_type="yolo_detection",
            model_weights="yolo11n.pt",
            hyperparams_json=json.dumps(
                {
                    "algorithm_key": "street_litter_detection",
                    "dataset_version_id": 14,
                }
            ),
            execution_signature="p" * 64,
        )
        db.add(job)
        db.flush()
        job_id = int(job.id)

    enqueue_training_worker_command(db, job=db.query(YoloTrainJob).filter_by(id=job_id).one())
    event = (
        db.query(AutoTrainingOutbox)
        .filter_by(event_name="training.worker.command.v1")
        .one()
    )
    payload = json.loads(event.payload_json)
    assert seen_keys == ["street_litter_detection"]
    assert payload["command"]["request"]["model_lineage"]["algorithm_id"] == 44


def test_bm1688_dispatch_honors_switch_and_submits_once(db):
    with begin_transaction(db):
        row = YoloBm1688Export(
            user_id=1,
            task_id=1,
            yolo_train_job_id=1,
            status="PENDING",
            auto_training_plan_id=1,
            auto_training_round_id=101,
            execution_signature="b" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(row)
        db.flush()
        export_id = row.id

    with pytest.raises(OutboxDeferredError):
        dispatch_bm1688_export(
            db,
            export_id=export_id,
            switches={"bm1688_enabled": False},
            submit=lambda *_args: pytest.fail("must not submit"),
        )

    calls = []
    assert dispatch_bm1688_export(
        db,
        export_id=export_id,
        switches={"bm1688_enabled": True},
        submit=lambda value: calls.append(value),
    )
    assert calls == [export_id]
    assert (
        db.query(YoloBm1688Export).filter_by(id=export_id).one().queue_lease_owner
    )
    assert not dispatch_bm1688_export(
        db,
        export_id=export_id,
        switches={"bm1688_enabled": True},
        submit=lambda *_args: pytest.fail("active dispatch lease must block duplicate"),
    )


def test_bm1688_dispatch_defers_to_dedicated_worker_without_claiming(db, monkeypatch):
    with begin_transaction(db):
        row = YoloBm1688Export(
            user_id=1,
            task_id=1,
            yolo_train_job_id=1,
            status="PENDING",
            auto_training_plan_id=1,
            auto_training_round_id=104,
            execution_signature="d" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(row)
        db.flush()
        export_id = int(row.id)

    monkeypatch.setenv("LABELU_BM1688_DEDICATED_WORKER_ENABLED", "true")
    with pytest.raises(OutboxDeferredError):
        dispatch_bm1688_export(
            db,
            export_id=export_id,
            switches={"bm1688_enabled": True},
            submit=lambda *_args: pytest.fail("dedicated worker must own execution"),
        )
    current = db.query(YoloBm1688Export).filter_by(id=export_id).one()
    assert current.queue_lease_owner is None


def test_bm1688_dispatch_releases_claim_when_submit_fails(db):
    with begin_transaction(db):
        row = YoloBm1688Export(
            user_id=1,
            task_id=1,
            yolo_train_job_id=1,
            status="PENDING",
            auto_training_plan_id=1,
            auto_training_round_id=103,
            execution_signature="e" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(row)
        db.flush()
        export_id = int(row.id)

    with pytest.raises(RuntimeError, match="submit failed"):
        dispatch_bm1688_export(
            db,
            export_id=export_id,
            switches={"bm1688_enabled": True},
            submit=lambda *_args: (_ for _ in ()).throw(RuntimeError("submit failed")),
        )
    row = db.query(YoloBm1688Export).filter_by(id=export_id).one()
    assert row.queue_lease_owner is None
    assert row.queue_lease_expires_at <= datetime.now()


def test_edge_dispatch_honors_observe_only_and_submits_once(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.auto_training_dispatch._prepare_edge_validation_pack",
        lambda **_kwargs: True,
    )
    with begin_transaction(db):
        export = YoloBm1688Export(
            user_id=1,
            task_id=1,
            yolo_train_job_id=1,
            status="COMPLETED",
            auto_training_plan_id=1,
            auto_training_round_id=102,
            execution_signature="c" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(export)
        db.flush()
        row = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=export.id,
            status="PENDING",
            mode="run_and_eval",
            auto_training_plan_id=1,
            auto_training_round_id=102,
            execution_signature="d" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(row)
        db.flush()
        edge_job_id = row.id

    with pytest.raises(OutboxDeferredError):
        dispatch_edge_validation_job(
            db,
            edge_job_id=edge_job_id,
            switches={"observe_only": True, "edge_validation_enabled": True},
            submit=lambda *_args: pytest.fail("must not submit"),
        )

    calls = []
    assert dispatch_edge_validation_job(
        db,
        edge_job_id=edge_job_id,
        switches={"edge_validation_enabled": True},
        submit=lambda value: calls.append(value),
    )
    assert calls == [edge_job_id]
    assert (
        db.query(YoloEdgeValidationJob).filter_by(id=edge_job_id).one().queue_lease_owner
    )
    assert not dispatch_edge_validation_job(
        db,
        edge_job_id=edge_job_id,
        switches={"edge_validation_enabled": True},
        submit=lambda *_args: pytest.fail("active dispatch lease must block duplicate"),
    )


def test_edge_dispatch_releases_claim_when_submit_fails(db, monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.application.service.auto_training_dispatch._prepare_edge_validation_pack",
        lambda **_kwargs: True,
    )
    with begin_transaction(db):
        export = YoloBm1688Export(
            user_id=1,
            task_id=1,
            yolo_train_job_id=1,
            status="COMPLETED",
            auto_training_plan_id=1,
            auto_training_round_id=104,
            execution_signature="f" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(export)
        db.flush()
        row = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=export.id,
            status="PENDING",
            mode="run_and_eval",
            auto_training_plan_id=1,
            auto_training_round_id=104,
            execution_signature="g" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(row)
        db.flush()
        edge_job_id = int(row.id)

    with pytest.raises(RuntimeError, match="submit failed"):
        dispatch_edge_validation_job(
            db,
            edge_job_id=edge_job_id,
            switches={"edge_validation_enabled": True},
            submit=lambda *_args: (_ for _ in ()).throw(RuntimeError("submit failed")),
        )
    row = db.query(YoloEdgeValidationJob).filter_by(id=edge_job_id).one()
    assert row.queue_lease_owner is None
    assert row.queue_lease_expires_at <= datetime.now()


def _create_auto_edge_job_for_pack_test(db) -> int:
    with begin_transaction(db):
        task = Task(
            name="auto edge pack task",
            config=json.dumps({"tools": []}),
            media_type="IMAGE",
            created_by=1,
            updated_by=1,
        )
        db.add(task)
        db.flush()
        export = YoloBm1688Export(
            user_id=1,
            task_id=task.id,
            yolo_train_job_id=1,
            status="COMPLETED",
            auto_training_plan_id=1,
            auto_training_round_id=901,
            execution_signature="p" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(export)
        db.flush()
        row = YoloEdgeValidationJob(
            user_id=1,
            task_id=task.id,
            export_id=export.id,
            status="PENDING",
            mode="run_only",
            sample_ids_json="[]",
            result_json=json.dumps({"request": {"sample_ids": []}}),
            auto_training_plan_id=1,
            auto_training_round_id=901,
            execution_signature="q" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(row)
        db.flush()
        return int(row.id)


def _fake_edge_pack(db, *, root_override, **_kwargs):
    root = Path(root_override)
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    return {
        "job_root": str(root),
        "eval_data_root": str(root / "eval_data"),
        "manifest_path": str(root / "manifest.json"),
        "copied_count": 1,
        "labeled_count": 1,
        "unlabeled_count": 0,
        "skipped_count": 0,
    }


def test_edge_pack_commits_ready_before_post_commit_submit(db, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_pack.pack_selected_samples_for_job",
        _fake_edge_pack,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_pack._export_dir_for_row",
        lambda export_id: tmp_path / f"export-{export_id}",
    )
    job_id = _create_auto_edge_job_for_pack_test(db)
    observed = []

    def post_commit_submit(value):
        from labelu.internal.common.db import session_scope

        with session_scope() as verify_db:
            row = verify_db.query(YoloEdgeValidationJob).filter_by(id=value).one()
            payload = json.loads(row.result_json or "{}")
            observed.append(
                (
                    payload["pack"]["state"],
                    row.queue_lease_owner,
                    row.status,
                )
            )

    assert dispatch_edge_validation_job(
        db,
        edge_job_id=job_id,
        switches={"edge_validation_enabled": True},
        submit=post_commit_submit,
    )
    assert observed == [("READY", None, "PENDING")]
    export_root = next(tmp_path.glob("export-*"))
    final_roots = list(
        (export_root / "manual_edge_validation" / f"job-{job_id}" / "packs").glob(
            "*/manifest.json"
        )
    )
    assert len(final_roots) == 1


def test_edge_pack_failure_releases_claim_and_removes_staging(db, monkeypatch, tmp_path):
    def failing_pack(db, *, root_override, **_kwargs):
        root = Path(root_override)
        root.mkdir(parents=True, exist_ok=True)
        (root / "partial.txt").write_text("partial", encoding="utf-8")
        raise RuntimeError("pack failed")

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_pack.pack_selected_samples_for_job",
        failing_pack,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_pack._export_dir_for_row",
        lambda export_id: tmp_path / f"export-{export_id}",
    )
    job_id = _create_auto_edge_job_for_pack_test(db)
    with pytest.raises(RuntimeError, match="pack failed"):
        dispatch_edge_validation_job(
            db,
            edge_job_id=job_id,
            switches={"edge_validation_enabled": True},
            submit=lambda _value: pytest.fail("must not submit after pack failure"),
        )
    db.expire_all()
    row = db.query(YoloEdgeValidationJob).filter_by(id=job_id).one()
    assert row.queue_lease_owner is None
    assert not list(tmp_path.rglob("*.pack-*"))


def test_edge_pack_retry_reuses_ready_pack_without_rebuilding(db, monkeypatch, tmp_path):
    calls = []

    def counting_pack(db, *, root_override, **kwargs):
        calls.append(1)
        return _fake_edge_pack(db, root_override=root_override, **kwargs)

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_pack.pack_selected_samples_for_job",
        counting_pack,
    )
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_pack._export_dir_for_row",
        lambda export_id: tmp_path / f"export-{export_id}",
    )
    job_id = _create_auto_edge_job_for_pack_test(db)
    for _ in range(2):
        assert dispatch_edge_validation_job(
            db,
            edge_job_id=job_id,
            switches={"edge_validation_enabled": True},
            submit=lambda _value: None,
        )
    assert calls == [1]


def test_two_processes_only_one_edge_dispatch_claim_succeeds(tmp_path):
    db_path = tmp_path / "edge-dispatch-claim.sqlite"
    _prepare_edge_claim_db(db_path)
    context = multiprocessing.get_context("spawn")
    ready_queue = context.Queue()
    result_queue = context.Queue()
    start_event = context.Event()
    processes = [
        context.Process(
            target=_edge_dispatch_claim_process,
            args=(str(db_path), start_event, ready_queue, result_queue),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    assert len({ready_queue.get(timeout=20) for _ in processes}) == 2
    start_event.set()
    results = [result_queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0
    assert all("error" not in result for result in results), results
    assert sum(bool(result["claimed"]) for result in results) == 1

    engine = create_engine(f"sqlite:///{db_path.resolve().as_posix()}")
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT queue_lease_owner FROM yolo_edge_validation_job "
                    "WHERE id=1"
                )
            ).one()
            assert row[0]
    finally:
        engine.dispose()


def test_pack_directory_publish_uses_one_immutable_rename(tmp_path):
    final = tmp_path / "job-1" / "packs" / "signature"
    staging = tmp_path / ".job-1.pack-new"
    staging.mkdir()
    (staging / "manifest.json").write_text("new", encoding="utf-8")

    _replace_edge_pack_staging(staging=staging, final=final)

    assert (final / "manifest.json").read_text(encoding="utf-8") == "new"


def test_pack_directory_publish_retries_transient_windows_lock(tmp_path, monkeypatch):
    final = tmp_path / "job-1" / "packs" / "signature"
    staging = tmp_path / ".job-1.pack-new"
    staging.mkdir()
    (staging / "manifest.json").write_text("new", encoding="utf-8")
    original_replace = Path.replace
    attempts = {"count": 0}

    def locked_once(source, destination):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise PermissionError(32, "sharing violation")
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", locked_once)
    _replace_edge_pack_staging(staging=staging, final=final)

    assert attempts["count"] == 2
    assert (final / "manifest.json").read_text(encoding="utf-8") == "new"


def test_pack_directory_publish_reuses_complete_concurrent_target(tmp_path):
    final = tmp_path / "job-1" / "packs" / "signature"
    staging = tmp_path / ".job-1.pack-new"
    staging.mkdir()
    (staging / "manifest.json").write_text("new", encoding="utf-8")
    final.mkdir(parents=True)
    (final / "manifest.json").write_text("already-published", encoding="utf-8")

    _replace_edge_pack_staging(staging=staging, final=final)

    assert (final / "manifest.json").read_text(encoding="utf-8") == "already-published"
    assert not staging.exists()


def test_pack_signature_changes_when_annotation_or_sample_time_changes():
    base = dict(
        export_id=1,
        job_id=2,
        sample_ids=[3],
        sample_task_ids={3: 4},
        attachment_ids=[],
        task_config_json="{}",
    )
    first = _edge_pack_signature(
        **base, source_evidence=[{"sample_id": 3, "annotation_sha256": "a", "sample_updated_at": "t1"}]
    )
    changed_annotation = _edge_pack_signature(
        **base, source_evidence=[{"sample_id": 3, "annotation_sha256": "b", "sample_updated_at": "t1"}]
    )
    changed_time = _edge_pack_signature(
        **base, source_evidence=[{"sample_id": 3, "annotation_sha256": "a", "sample_updated_at": "t2"}]
    )
    assert first != changed_annotation
    assert first != changed_time


def test_pack_db_commit_failure_restores_previous_valid_package(db, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_pack._export_dir_for_row",
        lambda export_id: tmp_path / f"export-{export_id}",
    )
    job_id = _create_auto_edge_job_for_pack_test(db)
    export_id = int(db.get(YoloEdgeValidationJob, job_id).export_id)
    export_root = tmp_path / f"export-{export_id}"
    final = export_root / "manual_edge_validation" / f"job-{job_id}"
    final.mkdir(parents=True)
    (final / "manifest.json").write_text("old-valid", encoding="utf-8")

    def pack_and_lose_lease(db, *, root_override, **_kwargs):
        from labelu.internal.common.db import begin_transaction, session_scope

        root = Path(root_override)
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text("new", encoding="utf-8")
        with session_scope() as other_db:
            with begin_transaction(other_db):
                other_db.query(YoloEdgeValidationJob).filter_by(id=job_id).update(
                    {
                        "queue_lease_owner": "replacement-owner",
                        "queue_lease_expires_at": datetime.now() + timedelta(minutes=5),
                    },
                    synchronize_session=False,
                )
        return {
            "job_root": str(root),
            "eval_data_root": str(root / "eval_data"),
            "manifest_path": str(root / "manifest.json"),
            "copied_count": 1,
            "labeled_count": 1,
            "unlabeled_count": 0,
        }

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_edge_validation_pack.pack_selected_samples_for_job",
        pack_and_lose_lease,
    )
    with pytest.raises(RuntimeError, match="lease lost"):
        dispatch_edge_validation_job(
            db,
            edge_job_id=job_id,
            switches={"edge_validation_enabled": True},
            submit=lambda _value: pytest.fail("must not submit after lease loss"),
        )
    assert (final / "manifest.json").read_text(encoding="utf-8") == "old-valid"


def test_bm1688_recovery_lease_allows_one_owner_and_expired_takeover(db):
    with begin_transaction(db):
        row = YoloBm1688Export(
            user_id=1,
            task_id=1,
            yolo_train_job_id=1,
            status="PROCESSING",
            auto_training_plan_id=1,
            auto_training_round_id=501,
            execution_signature="r" * 64,
            pipeline_origin="AUTO_QUALITY",
            queue_lease_expires_at=datetime.now() - timedelta(seconds=1),
        )
        db.add(row)
        db.flush()
        export_id = int(row.id)

    now = datetime.now()
    claimed, owner = claim_bm1688_recovery(
        db, export_id=export_id, status="PROCESSING", now=now
    )
    assert claimed is True
    assert owner.startswith("bm1688-recovery:")
    duplicate, _ = claim_bm1688_recovery(
        db, export_id=export_id, status="PROCESSING", now=now
    )
    assert duplicate is False

    release_bm1688_recovery(db, export_id=export_id, owner=owner)
    reclaimed, second_owner = claim_bm1688_recovery(
        db, export_id=export_id, status="PROCESSING", now=datetime.now()
    )
    assert reclaimed is True
    assert second_owner != owner


def test_bm1688_recovery_claim_skips_active_execution_lease(db):
    now = datetime.now()
    with begin_transaction(db):
        row = YoloBm1688Export(
            user_id=1,
            task_id=1,
            yolo_train_job_id=1,
            status="PROCESSING",
            auto_training_plan_id=1,
            auto_training_round_id=503,
            execution_signature="u" * 64,
            pipeline_origin="AUTO_QUALITY",
            execution_lease_owner="compiler-a",
            execution_lease_expires_at=now + timedelta(minutes=5),
        )
        db.add(row)
        db.flush()
        export_id = int(row.id)

    claimed, _ = claim_bm1688_recovery(
        db, export_id=export_id, status="PROCESSING", now=now
    )
    assert claimed is False


def test_edge_recovery_lease_allows_one_owner_and_expired_takeover(db):
    with begin_transaction(db):
        export = YoloBm1688Export(
            user_id=1,
            task_id=1,
            yolo_train_job_id=1,
            status="COMPLETED",
            auto_training_plan_id=1,
            auto_training_round_id=502,
            execution_signature="s" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(export)
        db.flush()
        row = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=export.id,
            status="PROCESSING",
            mode="run_and_eval",
            auto_training_plan_id=1,
            auto_training_round_id=502,
            execution_signature="t" * 64,
            pipeline_origin="AUTO_QUALITY",
            queue_lease_expires_at=datetime.now() - timedelta(seconds=1),
        )
        db.add(row)
        db.flush()
        edge_job_id = int(row.id)

    now = datetime.now()
    claimed, owner = claim_edge_validation_recovery(
        db, edge_job_id=edge_job_id, status="PROCESSING", now=now
    )
    assert claimed is True
    assert owner.startswith("edge-recovery:")
    duplicate, _ = claim_edge_validation_recovery(
        db, edge_job_id=edge_job_id, status="PROCESSING", now=now
    )
    assert duplicate is False

    release_edge_validation_recovery(db, edge_job_id=edge_job_id, owner=owner)
    reclaimed, second_owner = claim_edge_validation_recovery(
        db, edge_job_id=edge_job_id, status="PROCESSING", now=datetime.now()
    )
    assert reclaimed is True
    assert second_owner != owner


def test_edge_recovery_claim_skips_active_execution_lease(db):
    now = datetime.now()
    with begin_transaction(db):
        export = YoloBm1688Export(
            user_id=1,
            task_id=1,
            yolo_train_job_id=1,
            status="COMPLETED",
            auto_training_plan_id=1,
            auto_training_round_id=504,
            execution_signature="v" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(export)
        db.flush()
        row = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=export.id,
            status="PROCESSING",
            mode="run_and_eval",
            auto_training_plan_id=1,
            auto_training_round_id=504,
            execution_signature="w" * 64,
            pipeline_origin="AUTO_QUALITY",
            execution_lease_owner="edge-worker-a",
            execution_lease_expires_at=now + timedelta(minutes=5),
        )
        db.add(row)
        db.flush()
        edge_job_id = int(row.id)

    claimed, _ = claim_edge_validation_recovery(
        db, edge_job_id=edge_job_id, status="PROCESSING", now=now
    )
    assert claimed is False
