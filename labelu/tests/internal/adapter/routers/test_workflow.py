from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from labelu.internal.adapter.persistence import crud_workflow
from labelu.internal.application.service import workflow as workflow_service
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.workflow import (
    WorkflowEvent,
    WorkflowInstance,
    WorkflowOutbox,
    WorkflowStep,
)

RETIRED_ERROR = {
    "msg": "该能力已下线，请使用任务、训练、BM1688 编译、真机验证和模型发布主流程",
    "err_code": 30004,
}


class _DatabaseAccessForbidden:
    """Raise immediately if a retired service touches its database argument."""

    def __getattribute__(self, name):
        raise AssertionError(f"退役入口不应访问数据库：{name}")


def _assert_retired(call) -> None:
    with pytest.raises(LabelUException) as captured:
        call()
    assert captured.value.status_code == 403
    assert captured.value.code == RETIRED_ERROR["err_code"]
    assert captured.value.msg == RETIRED_ERROR["msg"]


def _workflow_row_counts(db) -> dict[type, int]:
    return {
        model: db.query(model).count()
        for model in (WorkflowInstance, WorkflowStep, WorkflowEvent, WorkflowOutbox)
    }


def test_retired_workflow_services_fail_before_database_access():
    db = _DatabaseAccessForbidden()
    async_calls = (
        lambda: workflow_service.create(db, None, None),
        lambda: workflow_service.transition(db, 1, None, None),
        lambda: workflow_service.replay(db, 1, None),
    )
    for call in async_calls:
        _assert_retired(lambda call=call: asyncio.run(call()))

    _assert_retired(lambda: workflow_service.execute_step(db, 1, "复核", None))


def test_retired_workflow_http_writes_are_uniform_and_have_no_side_effects(
    client, db, testuser_token_headers
):
    before = _workflow_row_counts(db)
    calls = (
        (
            "/api/v1/workflows",
            {
                "project_id": 1,
                "workflow_type": "expert_feedback",
                "idempotency_key": "phase-one-retired",
                "steps": ["review"],
            },
        ),
        ("/api/v1/workflows/1/transition", {"status": "RUNNING"}),
        ("/api/v1/workflows/1/steps/review/execute", {}),
        ("/api/v1/workflows/1/replay", {}),
    )

    for path, payload in calls:
        response = client.post(path, json=payload, headers=testuser_token_headers)
        assert response.status_code == 403, (path, response.text)
        assert response.json() == RETIRED_ERROR

    db.expire_all()
    assert _workflow_row_counts(db) == before


def test_phase_one_openapi_only_exposes_workflow_outbox_operations(client):
    paths = client.app.openapi()["paths"]
    workflow_paths = {path for path in paths if path.startswith("/api/v1/workflows")}
    assert workflow_paths == {
        "/api/v1/workflows/outbox",
        "/api/v1/workflows/outbox/{outbox_id}/retry",
        "/api/v1/workflows/outbox/{outbox_id}/dispatch",
    }
    assert set(paths["/api/v1/workflows/outbox"]) == {"get"}
    assert set(paths["/api/v1/workflows/outbox/{outbox_id}/retry"]) == {"post"}
    assert set(paths["/api/v1/workflows/outbox/{outbox_id}/dispatch"]) == {"post"}


def test_workflow_event_catalog_accepts_registered_legacy_and_canonical(monkeypatch):
    captured = []
    monkeypatch.setattr(
        crud_workflow, "add_event", lambda _db, event: captured.append(event)
    )
    row = SimpleNamespace(id=1)

    workflow_service._event(None, row, "workflow.transition", None)
    workflow_service._event(None, row, "7gai.workflow.transition.v1", None)

    assert [event.event_type for event in captured] == [
        "workflow.transition",
        "7gai.workflow.transition.v1",
    ]


def test_workflow_event_catalog_rejects_unknown_without_writing(monkeypatch):
    inserted = []
    monkeypatch.setattr(
        crud_workflow, "add_event", lambda _db, event: inserted.append(event)
    )

    with pytest.raises(ValueError, match="compatibility allow-list"):
        workflow_service._event(None, SimpleNamespace(id=1), "workflow.unknown", None)

    assert inserted == []


def test_workflow_crud_search_filters_before_pagination(db):
    rows = [
        WorkflowInstance(
            scope_key="global",
            workflow_type="expert_feedback",
            status="PENDING",
            idempotency_key="feedback-needle",
            created_by=1,
        ),
        WorkflowInstance(
            scope_key="global",
            workflow_type="training",
            status="FAILED",
            idempotency_key="other-workflow",
            created_by=1,
        ),
    ]
    db.add_all(rows)
    db.commit()

    result, total = crud_workflow.list_by_scope(
        db, "global", page=0, size=1, search="needle"
    )

    assert total == 1
    assert [row.idempotency_key for row in result] == ["feedback-needle"]


def test_workflow_list_type_filter_applies_before_pagination(
    client, db, testadmin_token_headers
):
    rows = [
        WorkflowInstance(
            scope_key="global",
            workflow_type="expert_feedback",
            status="PENDING",
            idempotency_key="expert-old",
            created_by=1,
        ),
        WorkflowInstance(
            scope_key="global",
            workflow_type="third_party_callback",
            status="PENDING",
            idempotency_key="callback-between",
            created_by=1,
        ),
        WorkflowInstance(
            scope_key="global",
            workflow_type="expert_feedback",
            status="RUNNING",
            idempotency_key="expert-new",
            created_by=1,
        ),
    ]
    db.add_all(rows)
    db.commit()

    first_page = client.get(
        "/api/v1/workflows",
        params={"workflow_type": "expert_feedback", "page": 0, "size": 1},
        headers=testadmin_token_headers,
    )
    second_page = client.get(
        "/api/v1/workflows",
        params={"workflow_type": "expert_feedback", "page": 1, "size": 1},
        headers=testadmin_token_headers,
    )

    assert first_page.status_code == 200, first_page.text
    assert second_page.status_code == 200, second_page.text
    first_body = first_page.json()
    second_body = second_page.json()
    assert first_body["meta_data"]["total"] == 2
    assert second_body["meta_data"]["total"] == 2
    assert first_body["data"][0]["workflow_type"] == "expert_feedback"
    assert second_body["data"][0]["workflow_type"] == "expert_feedback"
    assert {
        first_body["data"][0]["idempotency_key"],
        second_body["data"][0]["idempotency_key"],
    } == {"expert-old", "expert-new"}


def test_workflow_scope_owner_filter_is_applied_before_pagination(db):
    rows = [
        WorkflowInstance(
            scope_key="global",
            workflow_type="expert_feedback",
            status="PENDING",
            idempotency_key="hidden-first",
            created_by=999,
        ),
        WorkflowInstance(
            scope_key="global",
            workflow_type="expert_feedback",
            status="PENDING",
            idempotency_key="visible-second",
            created_by=1,
        ),
    ]
    db.add_all(rows)
    db.commit()

    result, total = crud_workflow.list_by_scope(
        db, "global", page=0, size=1, created_by=1
    )

    assert total == 1
    assert [row.idempotency_key for row in result] == ["visible-second"]
