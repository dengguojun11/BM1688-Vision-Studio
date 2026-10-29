import asyncio

from labelu.internal.application.service import strategy_evolution as strategy_service
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyPlatformCallbackReceipt,
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.workflow import WorkflowInstance


def _integration(db, *, external_task_id: str):
    with begin_transaction(db):
        row = StrategyTaskIntegration(
            external_task_id=external_task_id,
            schema_version="v1",
            task_name=external_task_id,
            task_type="threshold_experiment",
            dataset_s3_uri="",
            dataset_image_prefix="",
            metadata_key="",
            bucket="",
            prefix="",
            labeling_required=False,
            labeling_labels_json="[]",
            target_json="{}",
            experiment_json="{}",
            import_status="PENDING",
            platform_status="accepted",
            created_by=1,
            updated_by=1,
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        return row


def test_platform_callback_is_idempotent_without_project_workflow(db):
    row = _integration(db, external_task_id="strategy-callback-1")
    payload = {
        "schema_version": "v1",
        "callback_event_id": "callback-event-1",
        "external_task_id": row.external_task_id,
        "task_type": "threshold_experiment",
        "status": "succeeded",
        "result": {"ok": True},
    }

    first = asyncio.run(
        strategy_service._handle_platform_callback_once(db, payload=payload)
    )
    second = asyncio.run(
        strategy_service._handle_platform_callback_once(db, payload=payload)
    )

    assert first.status == "succeeded"
    assert second.status == "succeeded"
    assert db.query(StrategyPlatformCallbackReceipt).filter(
        StrategyPlatformCallbackReceipt.callback_event_id == "callback-event-1"
    ).count() == 1
    assert db.query(WorkflowInstance).count() == 0


def test_historical_callback_without_project_still_keeps_receipt(db):
    row = _integration(db, external_task_id="strategy-callback-legacy")
    payload = {
        "schema_version": "v1",
        "callback_event_id": "callback-legacy-1",
        "external_task_id": row.external_task_id,
        "task_type": "threshold_experiment",
        "status": "succeeded",
    }

    result = asyncio.run(
        strategy_service._handle_platform_callback_once(db, payload=payload)
    )

    assert result.status == "succeeded"
    assert db.query(StrategyPlatformCallbackReceipt).count() == 1
    assert db.query(WorkflowInstance).count() == 0
