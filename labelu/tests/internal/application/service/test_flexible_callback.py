import json

from sqlalchemy.orm import Session

from labelu.internal.application.service import flexible_callback
from labelu.internal.application.service import strategy_evolution as strategy_service
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.strategy_task_integration import StrategyTaskIntegration
from labelu.internal.domain.models.workflow import WorkflowInstance, WorkflowOutbox


def _row(db: Session, *, external_task_id: str) -> StrategyTaskIntegration:
    with begin_transaction(db):
        item = StrategyTaskIntegration(
            external_task_id=external_task_id,
            schema_version="v1",
            task_name=external_task_id,
            task_type="threshold_experiment",
            dataset_s3_uri="s3://bucket/prefix",
            dataset_image_prefix="images",
            metadata_key="metadata.json",
            bucket="bucket",
            prefix="prefix",
            labeling_required=False,
            labeling_labels_json="[]",
            target_json="{}",
            experiment_json="{}",
            import_status="COMPLETED",
            platform_status="succeeded",
            outbound_callback_url="http://callback.test/platform-callbacks",
            outbound_callback_status="pending",
            created_by=1,
            updated_by=1,
        )
        db.add(item)
        db.flush()
        db.refresh(item)
        return item


def _global_outbox(db: Session, *, key: str) -> tuple[WorkflowInstance, WorkflowOutbox]:
    with begin_transaction(db):
        workflow = WorkflowInstance(
            # The persistent pytest schema still has the historical NOT NULL
            # compatibility column.  Scope isolation is asserted by
            # ``scope_key``; this sentinel is never used for ownership.
            project_id=0,
            scope_key="global",
            workflow_type="third_party_callback",
            status="PENDING",
            idempotency_key=key,
            payload_json=json.dumps(
                {
                    "callback_url": "http://callback.test/platform-callbacks",
                    "callback_event_id": key,
                    "payload": {"callback_event_id": key, "ok": True},
                },
                sort_keys=True,
            ),
            current_step="deliver",
            max_attempts=1,
            created_by=1,
            owner_id=1,
            callback_url="http://callback.test/platform-callbacks",
        )
        db.add(workflow)
        db.flush()
        outbox = WorkflowOutbox(
            workflow_id=workflow.id,
            event_type="7gai.workflow.callback.dispatch.v1",
            aggregate_type="third_party_callback",
            aggregate_id=key,
            idempotency_key=f"{key}:dispatch",
            payload_json=workflow.payload_json,
            status="PENDING",
            max_attempts=1,
        )
        db.add(outbox)
        db.flush()
        db.refresh(workflow)
        db.refresh(outbox)
        return workflow, outbox


class _Response:
    def raise_for_status(self):
        return None


def test_unscoped_callback_keeps_legacy_projection_without_workflow(db: Session, monkeypatch):
    row = _row(db, external_task_id="callback-bridge-success")
    monkeypatch.setattr(strategy_service.settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "")
    monkeypatch.setattr(strategy_service.settings, "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS", True)
    monkeypatch.setattr(strategy_service.requests, "post", lambda *args, **kwargs: _Response())

    assert strategy_service._send_outbound_callback(db, row=row, result_payload={"ok": True}) == (
        "succeeded",
        None,
    )
    db.expire_all()
    assert row.outbound_callback_status == "succeeded"
    assert db.query(WorkflowInstance).count() == 0

    # Historical integrations without an explicit scope stay on the legacy
    # callback projection; no fake project or global workflow is invented.
    ref = flexible_callback.ensure_callback_workflow(
        db,
        row=row,
        callback_url=row.outbound_callback_url,
        payload={"callback_event_id": f"strategy-auto-{row.id}"},
        request_kwargs={"json": {"callback_event_id": f"strategy-auto-{row.id}"}},
        max_attempts=3,
    )
    assert ref is None
    assert db.query(WorkflowInstance).count() == 0


def test_callback_failure_is_dead_lettered_in_unified_workflow(db: Session, monkeypatch):
    row = _row(db, external_task_id="callback-bridge-dead")
    monkeypatch.setattr(strategy_service.settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "")
    monkeypatch.setattr(strategy_service.settings, "STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS", True)
    monkeypatch.setattr(strategy_service.settings, "STRATEGY_EVOLUTION_CALLBACK_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(strategy_service.requests, "post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))

    assert strategy_service._send_outbound_callback(db, row=row, result_payload={"ok": True})[0] == "failed"
    db.expire_all()
    assert row.outbound_callback_status == "failed"
    assert db.query(WorkflowInstance).count() == 0


def test_historical_unscoped_callback_does_not_invent_project(db: Session, monkeypatch):
    row = _row(db, external_task_id="callback-bridge-legacy")
    assert flexible_callback.ensure_callback_workflow(
        db,
        row=row,
        callback_url=row.outbound_callback_url,
        payload={"callback_event_id": "legacy"},
        request_kwargs={"json": {}},
        max_attempts=3,
    ) is None
    assert db.query(WorkflowInstance).count() == 0


def test_callback_dispatcher_delivers_once_and_replays_success(db: Session, monkeypatch):
    workflow, outbox = _global_outbox(db, key="callback-dispatch")
    calls = []

    class Response:
        def raise_for_status(self):
            return None

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return Response()

    delivered = flexible_callback.dispatch_outbox(db, outbox_id=outbox.id, http_post=post)
    assert delivered["status"] == "SUCCEEDED"
    assert len(calls) == 1
    replay = flexible_callback.dispatch_outbox(db, outbox_id=outbox.id, http_post=post)
    assert replay["replayed"] is True
    assert len(calls) == 1


def test_callback_dispatcher_moves_failed_delivery_to_dead_letter(db: Session):
    _, outbox = _global_outbox(db, key="callback-dispatch-dead")

    def post(*args, **kwargs):
        raise RuntimeError("receiver down")

    failed = flexible_callback.dispatch_outbox(db, outbox_id=outbox.id, http_post=post)
    assert failed["status"] == "DEAD_LETTER"
    assert failed["dead_letter"] is True
