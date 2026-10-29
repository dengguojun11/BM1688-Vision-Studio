"""Compatibility bridge for strategy callbacks and the unified workflow model.

The strategy-evolution integration predates the 7GAI workflow tables and still
keeps a small callback projection on ``StrategyTaskIntegration``.  This module
is the bridge during migration: the projection remains readable by old clients,
while every project-scoped callback gets one durable workflow and one outbox
event keyed by the callback event id.  Transport is intentionally injected by
the caller so the existing HTTP contract remains unchanged until the worker
cutover.
"""

from __future__ import annotations

import hashlib
import json
import socket
from datetime import datetime, timedelta
from typing import Any

import requests
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_strategy_task_integration, crud_workflow
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.event_catalog import validate_event_name
from labelu.internal.common.service_account import ensure_open_system_user
from labelu.internal.domain.models.strategy_task_integration import StrategyTaskIntegration
from labelu.internal.domain.models.workflow import WorkflowEvent, WorkflowInstance, WorkflowOutbox, WorkflowStep


CALLBACK_WORKFLOW_TYPE = "third_party_callback"
CALLBACK_STEP = "deliver"
CALLBACK_EVENT_TYPE = "7gai.workflow.callback.dispatch.v1"
CALLBACK_SUCCESS_EVENT = "7gai.workflow.callback.succeeded.v1"
CALLBACK_FAILED_EVENT = "7gai.workflow.callback.failed.v1"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _event(
    db: Session,
    workflow: WorkflowInstance,
    event_type: str,
    *,
    actor_id: int | None = None,
    old: str | None = None,
    new: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        WorkflowEvent(
            workflow_id=workflow.id,
            event_type=validate_event_name(event_type, require_registered=True),
            from_status=old,
            to_status=new,
            step_key=CALLBACK_STEP,
            payload_json=_json(payload or {}),
            actor_id=actor_id,
        )
    )


def _callback_event_id(row: StrategyTaskIntegration) -> str:
    return str(
        getattr(row, "outbound_callback_event_id", None)
        or f"strategy-auto-{int(row.id)}"
    ).strip()


def _workflow_key(row: StrategyTaskIntegration, callback_event_id: str) -> str:
    # Include the integration id and event id.  The latter is stable across
    # retries and makes a replay create no second delivery receipt.
    return f"strategy-callback:{int(row.id)}:{callback_event_id}"


def _body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def ensure_callback_workflow(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    callback_url: str,
    payload: dict[str, Any],
    request_kwargs: dict[str, Any],
    max_attempts: int,
) -> tuple[WorkflowInstance, WorkflowOutbox] | None:
    """Create or load the canonical callback workflow and its outbox row.

    ``None`` is returned for historical integrations without a project.  Such
    rows cannot be safely assigned to a tenant by inference; the old callback
    projection remains the compatibility path until explicit data governance
    resolves ownership.
    """

    project_id = getattr(row, "project_id", None)
    if project_id is None:
        return None

    callback_event_id = _callback_event_id(row)
    key = _workflow_key(row, callback_event_id)
    workflow = crud_workflow.get_by_key(db, key)
    if workflow is None:
        actor_id = getattr(row, "created_by", None)
        if actor_id is None:
            actor = ensure_open_system_user(db)
            actor_id = int(actor.id) if actor is not None else None
        if actor_id is None:
            # WorkflowInstance.created_by is required.  Keep the legacy path
            # available if the installation has not bootstrapped a service user.
            return None
        now = datetime.now()
        body = request_kwargs.get("data")
        if not isinstance(body, bytes):
            body = _json(payload).encode("utf-8")
        headers = request_kwargs.get("headers") or {}
        snapshot = {
            "project_id": int(project_id),
            "external_task_id": str(row.external_task_id),
            "callback_event_id": callback_event_id,
            "callback_url": str(callback_url),
            "payload": payload,
            "request_body_sha256": _body_sha256(body),
            "headers": {str(k): str(v) for k, v in headers.items()},
        }
        with begin_transaction(db):
            workflow = crud_workflow.create(
                db,
                WorkflowInstance(
                    project_id=int(project_id),
                    workflow_type=CALLBACK_WORKFLOW_TYPE,
                    status="PENDING",
                    idempotency_key=key,
                    payload_json=_json(snapshot),
                    current_step=CALLBACK_STEP,
                    max_attempts=max(1, int(max_attempts)),
                    created_by=int(actor_id),
                    owner_id=int(actor_id),
                    callback_url=str(callback_url),
                    # This is the delivery receipt, never the HMAC secret.
                    callback_signature=str(headers.get("X-LabelU-Signature") or "") or None,
                    next_run_at=now,
                ),
            )
            step = crud_workflow.add_step(
                db,
                WorkflowStep(
                    workflow_id=workflow.id,
                    step_key=CALLBACK_STEP,
                    sequence=0,
                    max_attempts=max(1, int(max_attempts)),
                    available_at=now,
                    input_json=_json(snapshot),
                ),
            )
            _event(
                db,
                workflow,
                "7gai.workflow.created.v1",
                actor_id=int(actor_id),
                new="PENDING",
                payload={"callback_event_id": callback_event_id},
            )
            outbox = crud_workflow.add_outbox(
                db,
                WorkflowOutbox(
                    workflow_id=workflow.id,
                    event_type=validate_event_name(CALLBACK_EVENT_TYPE, require_registered=True),
                    aggregate_type="third_party_callback",
                    aggregate_id=str(row.external_task_id),
                    idempotency_key=f"{key}:dispatch",
                    payload_json=_json(snapshot),
                    status="PENDING",
                    max_attempts=max(1, int(max_attempts)),
                    available_at=now,
                ),
            )
            # The FK is nullable for old installations and allows a gradual
            # migration without changing the external strategy response.
            crud_strategy_task_integration.update(
                db=db,
                row=row,
                obj_in={
                    StrategyTaskIntegration.callback_workflow_id.key: int(workflow.id),
                    StrategyTaskIntegration.outbound_callback_event_id.key: callback_event_id,
                },
            )
            db.flush()
        return workflow, outbox

    outbox = (
        db.query(WorkflowOutbox)
        .filter(
            WorkflowOutbox.workflow_id == int(workflow.id),
            WorkflowOutbox.idempotency_key == f"{key}:dispatch",
        )
        .first()
    )
    if outbox is None:
        # A partially migrated row is repaired idempotently under the same key.
        now = datetime.now()
        with begin_transaction(db):
            outbox = crud_workflow.add_outbox(
                db,
                WorkflowOutbox(
                    workflow_id=workflow.id,
                    event_type=validate_event_name(CALLBACK_EVENT_TYPE, require_registered=True),
                    aggregate_type="third_party_callback",
                    aggregate_id=str(row.external_task_id),
                    idempotency_key=f"{key}:dispatch",
                    payload_json=_json({"callback_url": callback_url, "payload": payload}),
                    status="PENDING",
                    max_attempts=max(1, int(max_attempts)),
                    available_at=now,
                ),
            )
    return workflow, outbox


def mark_callback_attempt(
    db: Session,
    *,
    workflow: WorkflowInstance,
    outbox: WorkflowOutbox,
) -> int:
    """Claim the inline transport attempt and return its one-based number."""

    now = datetime.now()
    with begin_transaction(db):
        outbox.attempts = int(outbox.attempts or 0) + 1
        outbox.status = "RUNNING"
        outbox.available_at = None
        outbox.lease_owner = None
        outbox.lease_expires_at = None
        workflow.status = "RUNNING"
        workflow.attempts = int(workflow.attempts or 0) + 1
        workflow.current_step = CALLBACK_STEP
        workflow.next_run_at = None
        workflow.updated_at = now
        step = next((item for item in workflow.steps if item.step_key == CALLBACK_STEP), None)
        if step is not None:
            step.status = "RUNNING"
            step.attempts = int(step.attempts or 0) + 1
            step.started_at = now
            step.last_error = None
        return int(outbox.attempts)


def mark_callback_succeeded(
    db: Session,
    *,
    workflow: WorkflowInstance,
    outbox: WorkflowOutbox,
    payload: dict[str, Any] | None = None,
) -> None:
    now = datetime.now()
    with begin_transaction(db):
        old = workflow.status
        workflow.status = "SUCCEEDED"
        workflow.current_step = None
        workflow.last_error = None
        workflow.next_run_at = None
        workflow.completed_at = now
        workflow.updated_at = now
        outbox.status = "SUCCEEDED"
        outbox.processed_at = now
        outbox.updated_at = now
        step = next((item for item in workflow.steps if item.step_key == CALLBACK_STEP), None)
        if step is not None:
            step.status = "SUCCEEDED"
            step.output_json = _json(payload or {})
            step.finished_at = now
        _event(db, workflow, CALLBACK_SUCCESS_EVENT, old=old, new="SUCCEEDED", payload=payload)


def mark_callback_failed(
    db: Session,
    *,
    workflow: WorkflowInstance,
    outbox: WorkflowOutbox,
    error: str,
    base_delay_seconds: int,
    max_delay_seconds: int,
) -> bool:
    """Persist retry/dead-letter state.  Return True when dead-lettered."""

    now = datetime.now()
    attempts = int(outbox.attempts or 0)
    dead = attempts >= int(outbox.max_attempts or workflow.max_attempts or 1)
    delay = min(
        int(max_delay_seconds),
        int(base_delay_seconds) * (2 ** max(0, attempts - 1)),
    )
    with begin_transaction(db):
        old = workflow.status
        outbox.last_error = str(error or "callback delivery failed")[:4000]
        outbox.updated_at = now
        workflow.last_error = outbox.last_error
        workflow.updated_at = now
        if dead:
            outbox.status = "DEAD_LETTER"
            outbox.available_at = None
            workflow.status = "DEAD_LETTER"
            workflow.completed_at = now
            workflow.next_run_at = None
        else:
            outbox.status = "RETRY"
            outbox.available_at = now + timedelta(seconds=max(0, delay))
            workflow.status = "WAITING"
            workflow.next_run_at = outbox.available_at
        step = next((item for item in workflow.steps if item.step_key == CALLBACK_STEP), None)
        if step is not None:
            step.status = "DEAD_LETTER" if dead else "PENDING"
            step.last_error = outbox.last_error
            step.available_at = outbox.available_at
            step.finished_at = now if dead else None
        _event(
            db,
            workflow,
            CALLBACK_FAILED_EVENT,
            old=old,
            new=workflow.status,
            payload={"attempts": attempts, "error": outbox.last_error, "dead_letter": dead},
        )
    return dead


def _snapshot(outbox: WorkflowOutbox) -> dict[str, Any]:
    try:
        value = json.loads(outbox.payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def dispatch_outbox(
    db: Session,
    *,
    outbox_id: int,
    http_post: Any = requests.post,
    base_delay_seconds: int = 30,
    max_delay_seconds: int = 3600,
) -> dict[str, Any]:
    """Deliver one callback outbox row through an injected HTTP client.

    Delivery is idempotent: already successful rows are returned unchanged;
    every request carries the persisted callback event id so receivers can
    deduplicate a retry.  Transport is injected for deterministic tests and
    for replacing ``requests`` with a queue worker later.
    """

    outbox = crud_workflow.get_outbox(db, outbox_id)
    if outbox is None:
        raise ValueError("Outbox 事件不存在")
    workflow = crud_workflow.get(db, outbox.workflow_id)
    if workflow is None:
        raise ValueError("工作流不存在")
    if outbox.status == "SUCCEEDED":
        return {"id": int(outbox.id), "status": outbox.status, "attempts": int(outbox.attempts or 0), "replayed": True}
    snapshot = _snapshot(outbox)
    callback_url = str(snapshot.get("callback_url") or workflow.callback_url or "").strip()
    if not callback_url:
        mark_callback_attempt(db, workflow=workflow, outbox=outbox)
        mark_callback_failed(db, workflow=workflow, outbox=outbox, error="第三方回调地址未配置", base_delay_seconds=base_delay_seconds, max_delay_seconds=max_delay_seconds)
        return {"id": int(outbox.id), "status": outbox.status, "attempts": int(outbox.attempts or 0), "error": outbox.last_error}
    payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else snapshot
    body = _json(payload).encode("utf-8")
    headers = {str(k): str(v) for k, v in (snapshot.get("headers") or {}).items()}
    event_id = str(snapshot.get("callback_event_id") or payload.get("callback_event_id") or f"workflow-{workflow.id}")
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("X-LabelU-Event-Id", event_id)
    try:
        attempt = mark_callback_attempt(db, workflow=workflow, outbox=outbox)
        response = http_post(callback_url, data=body, headers=headers, timeout=30)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
    except Exception as exc:
        dead = mark_callback_failed(db, workflow=workflow, outbox=outbox, error=str(exc), base_delay_seconds=base_delay_seconds, max_delay_seconds=max_delay_seconds)
        return {"id": int(outbox.id), "status": outbox.status, "attempts": int(outbox.attempts or 0), "dead_letter": dead, "error": outbox.last_error}
    result = {"delivered": True, "event_id": event_id, "attempt": attempt, "adapter": "http"}
    mark_callback_succeeded(db, workflow=workflow, outbox=outbox, payload=result)
    return {"id": int(outbox.id), "status": outbox.status, "attempts": int(outbox.attempts or 0), "result": result}


def dispatch_pending(
    db: Session,
    *,
    limit: int = 50,
    http_post: Any = requests.post,
    base_delay_seconds: int = 30,
    max_delay_seconds: int = 3600,
) -> list[dict[str, Any]]:
    """Dispatch due callback rows; intended for a scheduler/worker tick."""

    now = datetime.now()
    rows = (
        db.query(WorkflowOutbox)
        .filter(
            WorkflowOutbox.event_type == CALLBACK_EVENT_TYPE,
            WorkflowOutbox.status.in_({"PENDING", "RETRY"}),
            (WorkflowOutbox.available_at.is_(None) | (WorkflowOutbox.available_at <= now)),
        )
        .order_by(WorkflowOutbox.id.asc())
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    return [dispatch_outbox(db, outbox_id=int(row.id), http_post=http_post, base_delay_seconds=base_delay_seconds, max_delay_seconds=max_delay_seconds) for row in rows]
