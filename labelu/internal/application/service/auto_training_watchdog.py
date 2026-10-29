from __future__ import annotations

from dataclasses import dataclass
import os
import socket
import threading
import time
from typing import Iterable, Iterator

from loguru import logger
from sqlalchemy.exc import OperationalError

from labelu.internal.application.service.auto_training_lifecycle import (
    synchronize_training_job,
)
from labelu.internal.application.service.auto_training_outbox import (
    OutboxBatchResult,
    process_outbox_batch,
    renew_record_lease,
)
from labelu.internal.application.service.auto_training_pipeline_runtime import (
    synchronize_aicm_publish_job,
    synchronize_bm1688_export,
    synchronize_edge_validation_job,
)
from labelu.internal.common.aicm_publish_job import load_job as load_aicm_publish_job
from labelu.internal.application.service.auto_training_reconciliation import (
    discover_training_ready_plans,
    handle_outbox_event,
    reconcile_plan_owned_evaluation_datasets,
)
from labelu.internal.application.service.auto_training_scheduler import (
    evaluate_due_plans,
)
from labelu.internal.common.auto_training_runtime import load_auto_training_switches
from labelu.internal.common.config import settings
from labelu.internal.common.db import SessionLocal, begin_transaction
from labelu.internal.application.service.scheduler_lease import (
    acquire_scheduler_lease,
    renew_scheduler_lease,
    release_scheduler_lease,
)
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.domain.models.auto_training import AutoTrainingRound

# Worker command/result events belong to the dedicated worker process.  The
# scheduler must never claim them, otherwise a watchdog restart could mark a
# command processed without executing it.
WATCHDOG_OUTBOX_EVENT_NAMES = (
    "auto.plan.config.approved.v1",
    "task.training_ready.v1",
    "sample.annotation.changed.v1",
    "sample.review.changed.v1",
    "task.created.v1",
    "auto.plan.reconcile.requested.v1",
    "auto.plan.round.evaluate.v1",
    "auto.plan.scan.preview.requested.v1",
    "auto.round.stage.completed.v1",
    "auto.round.train.dispatch.v1",
    "auto.round.quality.evaluate.v1",
    "auto.round.bm1688.requested.v1",
    "auto.round.bm1688.execute.v1",
    "auto.round.edge.requested.v1",
    "auto.round.edge.execute.v1",
)


@dataclass(frozen=True)
class WatchdogCycleResult:
    enabled: bool
    draft_plans_created: int = 0
    evaluation_datasets_approved: int = 0
    outbox: OutboxBatchResult = OutboxBatchResult()
    due_plans: int = 0
    training_jobs: int = 0
    bm1688_exports: int = 0
    edge_jobs: int = 0
    publish_jobs: int = 0
    leader_acquired: bool = True
    fencing_token: int | None = None


def _iter_watchdog_id_batches(
    db,
    model,
    *,
    filters: Iterable[object],
    batch_size: int,
) -> Iterator[list[int]]:
    """Yield bounded primary-key pages for long-running watchdog scans.

    The production SQLAlchemy session uses keyset pagination so the watchdog
    does not materialize every historical auto-training row at once. Small
    query doubles used by unit tests do not expose ``get_bind`` and retain the
    previous one-query behavior.
    """
    size = max(1, int(batch_size))
    base_filters = tuple(filters)
    if not hasattr(db, "get_bind"):
        rows = db.query(model.id).filter(*base_filters).all()
        ids = [int(row[0]) for row in rows]
        if ids:
            yield ids
        return

    last_id = 0
    while True:
        query = db.query(model.id).filter(*base_filters)
        if last_id:
            query = query.filter(model.id > last_id)
        rows = (
            query.order_by(model.id.asc())
            .limit(size)
            .all()
        )
        ids = [int(row[0]) for row in rows]
        if not ids:
            return
        yield ids
        last_id = ids[-1]
        if len(ids) < size:
            return


def run_auto_training_watchdog_cycle(
    *, owner: str | None = None
) -> WatchdogCycleResult:
    if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
        return WatchdogCycleResult(enabled=False)
    switches = load_auto_training_switches()
    db = SessionLocal()
    scheduler_owner = owner or f"{socket.gethostname()}:{os.getpid()}"
    lease_token = None
    lease_seconds = 0
    lease_lost = False
    draft_plans_created = 0
    evaluation_datasets_approved = 0
    training_processed = 0
    exports_processed = 0
    edges_processed = 0
    publishes_processed = 0
    try:
        if not switches.get("schema_ready", False):
            # Schema bootstrap mode intentionally keeps the legacy discovery
            # probes; no scheduler lease table is required yet.
            evaluation_datasets_approved = reconcile_plan_owned_evaluation_datasets(db)
            draft_plans_created = discover_training_ready_plans(db)
            return WatchdogCycleResult(
                enabled=False,
                draft_plans_created=draft_plans_created,
                evaluation_datasets_approved=evaluation_datasets_approved,
            )
        # Test doubles and read-only diagnostics may provide a lightweight
        # session without get_bind; retain the previous behavior for those
        # callers while real SQLAlchemy sessions always use DB leadership.
        if hasattr(db, "get_bind") and switches.get("scheduler_leader_enabled", True):
            lease_seconds = max(
                5,
                int(os.environ.get("LABELU_AUTO_TRAINING_LEASE_SECONDS", "30")),
            )
            try:
                lease_token = acquire_scheduler_lease(
                    db,
                    owner=scheduler_owner,
                    lease_seconds=lease_seconds,
                )
            except OperationalError:
                # A rolling deployment may start the process before the
                # migration creating scheduler_lease has completed.  Keep
                # the watchdog inert and retry on the next interval.
                logger.exception("scheduler lease table is unavailable")
                return WatchdogCycleResult(
                    enabled=True,
                    draft_plans_created=draft_plans_created,
                    evaluation_datasets_approved=evaluation_datasets_approved,
                    leader_acquired=False,
                )
            if lease_token is None:
                return WatchdogCycleResult(
                    enabled=True,
                    draft_plans_created=0,
                    evaluation_datasets_approved=0,
                    leader_acquired=False,
                )

        def _lease_guard() -> bool:
            """Renew the leader lease before every externally visible step.

            A lost fencing token must stop this cycle immediately; continuing
            to synchronize rows after takeover would let a stale watchdog
            produce duplicate side effects.
            """
            nonlocal lease_token
            nonlocal lease_lost
            if lease_token is None:
                return True
            renewed = renew_scheduler_lease(
                db, lease_token, lease_seconds=max(5, lease_seconds)
            )
            if renewed is None:
                logger.warning(
                    "auto training watchdog lost scheduler lease; aborting cycle"
                )
                lease_lost = True
                lease_token = None
                return False
            lease_token = renewed
            return True

        if not _lease_guard():
            return WatchdogCycleResult(
                enabled=True,
                leader_acquired=False,
            )
        evaluation_datasets_approved = reconcile_plan_owned_evaluation_datasets(db)
        if not _lease_guard():
            return WatchdogCycleResult(enabled=True, leader_acquired=False)
        draft_plans_created = discover_training_ready_plans(db)
        outbox = OutboxBatchResult()
        if switches.get("outbox_enabled", False):
            if not _lease_guard():
                return WatchdogCycleResult(enabled=True, leader_acquired=False)

            outbox_lease_seconds = max(60, lease_seconds or 0)

            def _outbox_heartbeat(record_id: int, current_owner: str) -> bool:
                """Renew an Outbox row from a short-lived DB session.

                The watchdog session may be inside the handler transaction and
                is not thread-safe. A separate session keeps renewal
                independent from handler work that may run for several minutes.
                Lightweight test sessions do not expose a bind and retain the
                existing bounded test behavior.
                """
                try:
                    from sqlalchemy.orm import sessionmaker

                    bind = db.get_bind()
                except (AttributeError, RuntimeError):
                    return True
                heartbeat_db = sessionmaker(autoflush=False, bind=bind)()
                try:
                    with begin_transaction(heartbeat_db):
                        return renew_record_lease(
                            heartbeat_db,
                            record_id=record_id,
                            owner=current_owner,
                            lease_seconds=outbox_lease_seconds,
                        )
                finally:
                    heartbeat_db.close()

            outbox = process_outbox_batch(
                db,
                owner=scheduler_owner,
                handler=handle_outbox_event,
                limit=20,
                lease_seconds=outbox_lease_seconds,
                event_names=WATCHDOG_OUTBOX_EVENT_NAMES,
                heartbeat=(
                    _outbox_heartbeat if hasattr(db, "get_bind") else None
                ),
            )
        due_count = 0
        if switches.get("scheduler_enabled", False) and not switches.get(
            "observe_only", False
        ):
            if not _lease_guard():
                return WatchdogCycleResult(enabled=True, leader_acquired=False)
            with begin_transaction(db):
                due_count = len(evaluate_due_plans(db, limit=20))

        sync_batch_size = int(
            getattr(settings, "AUTO_TRAINING_WATCHDOG_SYNC_BATCH_SIZE", 20)
        )
        training_batches = _iter_watchdog_id_batches(
            db,
            YoloTrainJob,
            filters=(YoloTrainJob.auto_training_round_id.isnot(None),),
            batch_size=sync_batch_size,
        )
        for training_ids in training_batches:
            if not _lease_guard():
                break
            for job_id in training_ids:
                try:
                    if not _lease_guard():
                        break
                    training_processed += 1
                    with begin_transaction(db):
                        synchronize_training_job(db, job_id=job_id)
                except Exception:
                    db.rollback()
                    logger.exception(
                        "auto training watchdog failed to synchronize training job {}",
                        job_id,
                    )

        export_batches = _iter_watchdog_id_batches(
            db,
            YoloBm1688Export,
            filters=(YoloBm1688Export.auto_training_round_id.isnot(None),),
            batch_size=sync_batch_size,
        )
        for export_ids in export_batches:
            if not _lease_guard():
                break
            for export_id in export_ids:
                try:
                    if not _lease_guard():
                        break
                    exports_processed += 1
                    with begin_transaction(db):
                        synchronize_bm1688_export(db, export_id=export_id)
                except Exception:
                    db.rollback()
                    logger.exception(
                        "auto training watchdog failed to synchronize BM1688 export {}",
                        export_id,
                    )

        edge_batches = _iter_watchdog_id_batches(
            db,
            YoloEdgeValidationJob,
            filters=(YoloEdgeValidationJob.auto_training_round_id.isnot(None),),
            batch_size=sync_batch_size,
        )
        for edge_ids in edge_batches:
            if not _lease_guard():
                break
            for edge_id in edge_ids:
                try:
                    if not _lease_guard():
                        break
                    edges_processed += 1
                    with begin_transaction(db):
                        synchronize_edge_validation_job(db, edge_job_id=edge_id)
                except Exception:
                    db.rollback()
                    logger.exception(
                        "auto training watchdog failed to synchronize edge job {}", edge_id
                    )

        publish_batches = _iter_watchdog_id_batches(
            db,
            AutoTrainingRound,
            filters=(
                AutoTrainingRound.state == "PUBLISHING",
                AutoTrainingRound.publish_job_id.isnot(None),
            ),
            batch_size=sync_batch_size,
        )
        for publish_round_ids in publish_batches:
            if not _lease_guard():
                break
            for round_id in publish_round_ids:
                try:
                    if not _lease_guard():
                        break
                    publishes_processed += 1
                    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
                    payload = load_aicm_publish_job(str(round_row.publish_job_id or ""))
                    if payload is None:
                        continue
                    with begin_transaction(db):
                        synchronize_aicm_publish_job(
                            db,
                            round_id=round_id,
                            job_payload=payload,
                        )
                except Exception:
                    db.rollback()
                    logger.exception(
                        "auto training watchdog failed to synchronize AICM publish round {}",
                        round_id,
                    )
        return WatchdogCycleResult(
            enabled=True,
            draft_plans_created=draft_plans_created,
            evaluation_datasets_approved=evaluation_datasets_approved,
            outbox=outbox,
            due_plans=due_count,
            training_jobs=training_processed,
            bm1688_exports=exports_processed,
            edge_jobs=edges_processed,
            publish_jobs=publishes_processed,
            leader_acquired=not lease_lost,
            fencing_token=(lease_token.fencing_token if lease_token else None),
        )
    finally:
        if lease_token is not None:
            try:
                release_scheduler_lease(db, lease_token)
            except Exception:
                logger.exception("failed to release auto training scheduler lease")
        db.close()


_WATCHDOG_LOCK = threading.Lock()
_WATCHDOG_STARTED = False


def schedule_auto_training_watchdog() -> None:
    if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
        logger.info("auto training plan writes are closed; watchdog not started")
        return
    raw_enabled = os.environ.get("LABELU_AUTO_TRAINING_API_WATCHDOG_ENABLED")
    if not getattr(settings, "AUTO_TRAINING_API_WATCHDOG_ENABLED", True) or (
        raw_enabled is not None
        and raw_enabled.strip().lower()
        in {
            "0",
            "false",
            "no",
            "off",
        }
    ):
        logger.info("auto training API watchdog disabled; use dedicated scheduler")
        return
    global _WATCHDOG_STARTED
    with _WATCHDOG_LOCK:
        if _WATCHDOG_STARTED:
            return
        _WATCHDOG_STARTED = True

    interval = max(
        5, int(os.environ.get("LABELU_AUTO_TRAINING_WATCHDOG_SECONDS", "15"))
    )

    def loop() -> None:
        while True:
            time.sleep(interval)
            try:
                run_auto_training_watchdog_cycle()
            except Exception:
                logger.exception("auto training watchdog cycle failed")

    threading.Thread(
        target=loop,
        name="auto-training-watchdog",
        daemon=True,
    ).start()
