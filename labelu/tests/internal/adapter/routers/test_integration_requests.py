from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import uuid4

from labelu.internal.domain.models.experiment_dataset_import import ExperimentDatasetImport
from labelu.internal.domain.models.workflow import WorkflowOutbox
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.common.config import settings
from labelu.internal.domain.models.third_party_call_audit import ThirdPartyCallAudit
from labelu.internal.domain.models.strategy_task_integration import StrategyTaskIntegration
from labelu.internal.application.service import integration_requests as integration_service


def _request_body(**overrides):
    now = datetime.now().replace(microsecond=0)
    body = {
        "external_request_id": f"req-{uuid4().hex[:12]}",
        "source_system": "integration-test",
        "request_type": "model_optimization",
        "external_model_id": "model-001",
        "event_label": "person",
        "min_sample_count": 2,
        "event_time_from": (now - timedelta(days=1)).isoformat(),
        "event_time_to": now.isoformat(),
        "images": [
            {
                "external_image_id": f"img-{uuid4().hex[:8]}",
                "task_id": 101,
                "sample_id": 1001,
                "event_time": now.isoformat(),
            },
            {
                "external_image_id": f"img-{uuid4().hex[:8]}",
                "task_id": 102,
                "sample_id": 1002,
                "event_time": now.isoformat(),
            },
        ],
    }
    body.update(overrides)
    return body


def test_create_request_tracks_coverage_and_query_by_image(
    client, testuser_token_headers
):
    body = _request_body()
    response = client.post(
        "/api/v2/integrations/requests",
        json=body,
        headers=testuser_token_headers,
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["external_request_id"] == body["external_request_id"]
    assert data["stage"] == "DATASET_CREATED"
    assert data["status"] == "RUNNING"
    assert data["coverage"]["requested"] == 2
    assert data["coverage"]["available"] == 2
    assert data["coverage"]["pending"] == 0
    assert data["coverage"]["complete"] is True
    assert data["coverage"]["task_ids"] == [101, 102]

    image_id = body["images"][0]["external_image_id"]
    by_image = client.get(
        f"/api/v2/integrations/requests/by-image/{image_id}",
        headers=testuser_token_headers,
    )
    assert by_image.status_code == 200
    matches = by_image.json()["data"]
    assert len(matches) == 1
    assert matches[0]["id"] == data["id"]

    detail = client.get(
        f"/api/v2/integrations/requests/{data['id']}",
        headers=testuser_token_headers,
    )
    assert detail.status_code == 200
    timeline = detail.json()["data"]["timeline"]
    assert timeline
    assert any(event["event_type"] == "request.created" for event in timeline)


def test_create_request_is_idempotent_and_rejects_type_change(
    client, testuser_token_headers
):
    body = _request_body()
    first = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert second.status_code == 201
    assert second.json()["data"]["id"] == first.json()["data"]["id"]

    changed = dict(body)
    changed["request_type"] = "scene_training"
    changed["scene"] = {"lighting": "evening"}
    conflict = client.post(
        "/api/v2/integrations/requests", json=changed, headers=testuser_token_headers
    )
    assert conflict.status_code == 409


def test_threshold_request_waits_for_unknown_data_and_can_run_partial(
    client, testuser_token_headers
):
    now = datetime.now().replace(microsecond=0)
    base = {
        "source_system": "integration-test",
        "request_type": "threshold_experiment",
        "external_model_id": "model-threshold",
        "event_label": "vehicle",
        "min_sample_count": 1,
        "event_time_from": (now - timedelta(hours=1)).isoformat(),
        "event_time_to": now.isoformat(),
        "images": [
            {"external_image_id": f"unknown-{uuid4().hex[:8]}"},
        ],
    }
    strict = client.post(
        "/api/v2/integrations/threshold-experiments",
        json={**base, "external_request_id": f"thr-{uuid4().hex[:8]}"},
        headers=testuser_token_headers,
    )
    assert strict.status_code == 201, strict.text
    strict_data = strict.json()["data"]
    assert strict_data["stage"] == "WAITING_FOR_DATA"
    assert strict_data["coverage"]["unknown"] == 1
    assert strict_data["coverage"]["complete"] is False

    partial = client.post(
        "/api/v2/integrations/threshold-experiments",
        json={
            **base,
            "external_request_id": f"thr-partial-{uuid4().hex[:8]}",
            "allow_partial": True,
            "images": [
                {
                    "external_image_id": f"partial-{uuid4().hex[:8]}",
                    "task_id": 201,
                    "sample_id": 2001,
                    "event_time": now.isoformat(),
                },
                {"external_image_id": f"partial-unknown-{uuid4().hex[:8]}"},
            ],
        },
        headers=testuser_token_headers,
    )
    assert partial.status_code == 201, partial.text
    partial_data = partial.json()["data"]
    assert partial_data["stage"] == "DATA_READY"
    assert partial_data["coverage"]["partial"] is True


def test_scene_training_requires_scene_and_accepts_valid_scene(
    client, testuser_token_headers
):
    invalid = client.post(
        "/api/v2/integrations/scene-trainings",
        json={
            "external_request_id": f"scene-invalid-{uuid4().hex[:8]}",
            "source_system": "integration-test",
            "external_model_id": "model-001",
            "event_label": "person",
            "images": [],
        },
        headers=testuser_token_headers,
    )
    assert invalid.status_code == 422

    valid = client.post(
        "/api/v2/integrations/scene-trainings",
        json={
            "external_request_id": f"scene-{uuid4().hex[:8]}",
            "source_system": "integration-test",
            "external_model_id": "model-001",
            "event_label": "person",
            "scene": {"lighting": "evening"},
            "images": [],
        },
        headers=testuser_token_headers,
    )
    assert valid.status_code == 201, valid.text
    data = valid.json()["data"]
    assert data["request_type"] == "scene_training"
    assert data["scene"] == {"lighting": "evening"}


def test_list_summary_availability_and_cancel(client, testuser_token_headers):
    body = _request_body()
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert created.status_code == 201
    request_id = created.json()["data"]["id"]

    listed = client.get(
        "/api/v2/integrations/requests",
        params={"source_system": "integration-test", "page": 0, "size": 10},
        headers=testuser_token_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["meta_data"]["total"] >= 1

    summary = client.get(
        "/api/v2/integrations/requests/summary", headers=testuser_token_headers
    )
    assert summary.status_code == 200
    assert "total" in summary.json()["data"]

    availability = client.get(
        "/api/v2/integrations/data-availability",
        params={"model_id": body["external_model_id"]},
        headers=testuser_token_headers,
    )
    assert availability.status_code == 200
    assert availability.json()["data"]["requested"] >= 2

    cancelled = client.post(
        f"/api/v2/integrations/requests/{request_id}/cancel",
        headers=testuser_token_headers,
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "CANCELLED"


def test_publish_decision_requires_admin_for_approval(
    client, testuser_token_headers, testadmin_token_headers
):
    body = _request_body()
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    request_id = created.json()["data"]["id"]
    decision = client.post(
        f"/api/v2/integrations/requests/{request_id}/publish-decision",
        json={
            "candidate_model_id": 11,
            "baseline_model_id": 10,
            "candidate_export_id": 21,
            "metrics": {"f1": 0.81, "baseline_f1": 0.75},
            "auto_publish_eligible": False,
            "decision": "MANUAL_PENDING",
            "reason": "need manual review",
        },
        headers=testuser_token_headers,
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["data"]["stage"] == "WAITING_MANUAL_PUBLISH"

    denied = client.post(
        f"/api/v2/integrations/requests/{request_id}/publish-decision/approve",
        json={"decision": "APPROVED", "reason": "ok"},
        headers=testuser_token_headers,
    )
    assert denied.status_code == 403

    approved = client.post(
        f"/api/v2/integrations/requests/{request_id}/publish-decision/approve",
        json={"decision": "APPROVED", "reason": "ok"},
        headers=testadmin_token_headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["stage"] == "PUBLISHING"


def test_bind_packages_and_query_package(client, db, testuser_token_headers):
    package_id = f"pkg-{uuid4().hex[:10]}"
    db.add(
        ExperimentDatasetImport(
            package_id=package_id,
            dataset_type="hard_negative",
            dataset_date="2026-11-01",
            source_payload_json="{}",
            formal_task_ids_json="[301]",
            status="COMPLETED",
            declared_sample_count=1,
            valid_sample_count=1,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
    )
    db.commit()

    body = _request_body(images=[], package_ids=[package_id])
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["coverage"]["requested"] == 1
    assert data["coverage"]["available"] == 1
    assert data["coverage"]["complete"] is True
    assert 301 in data["coverage"]["task_ids"]

    by_package = client.get(
        f"/api/v2/integrations/requests/by-package/{package_id}",
        headers=testuser_token_headers,
    )
    assert by_package.status_code == 200
    assert any(item["id"] == data["id"] for item in by_package.json()["data"])


def test_integration_requests_require_auth(client):
    response = client.get("/api/v2/integrations/requests")
    assert response.status_code == 401


def test_create_request_rejects_invalid_window_and_callback(
    client, testuser_token_headers
):
    now = datetime.now().replace(microsecond=0)
    invalid_window = _request_body(
        event_time_from=now.isoformat(),
        event_time_to=(now - timedelta(hours=1)).isoformat(),
    )
    response = client.post(
        "/api/v2/integrations/requests",
        json=invalid_window,
        headers=testuser_token_headers,
    )
    assert response.status_code == 422

    invalid_callback = _request_body(callback_url="ftp://example.com/callback")
    response = client.post(
        "/api/v2/integrations/requests",
        json=invalid_callback,
        headers=testuser_token_headers,
    )
    assert response.status_code == 422


def test_threshold_requires_model_event_and_window(client, testuser_token_headers):
    response = client.post(
        "/api/v2/integrations/threshold-experiments",
        json={
            "external_request_id": f"thr-invalid-{uuid4().hex[:8]}",
            "source_system": "integration-test",
        },
        headers=testuser_token_headers,
    )
    assert response.status_code == 422


def test_unknown_image_and_missing_request_are_handled(
    client, testuser_token_headers
):
    unknown = client.get(
        "/api/v2/integrations/requests/by-image/not-exist-image",
        headers=testuser_token_headers,
    )
    assert unknown.status_code == 200
    assert unknown.json()["data"] == []

    missing = client.get(
        "/api/v2/integrations/requests/999999",
        headers=testuser_token_headers,
    )
    assert missing.status_code == 404


def test_cancel_is_idempotent_and_rejects_later_binding(
    client, testuser_token_headers
):
    body = _request_body()
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    request_id = created.json()["data"]["id"]
    first = client.post(
        f"/api/v2/integrations/requests/{request_id}/cancel",
        headers=testuser_token_headers,
    )
    second = client.post(
        f"/api/v2/integrations/requests/{request_id}/cancel",
        headers=testuser_token_headers,
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["data"]["status"] == "CANCELLED"

    bind = client.post(
        f"/api/v2/integrations/requests/{request_id}/packages",
        json={"package_ids": ["late-package"]},
        headers=testuser_token_headers,
    )
    assert bind.status_code == 409


def test_approve_without_decision_returns_conflict(
    client, testadmin_token_headers, testuser_token_headers
):
    body = _request_body()
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    request_id = created.json()["data"]["id"]
    response = client.post(
        f"/api/v2/integrations/requests/{request_id}/publish-decision/approve",
        json={"decision": "APPROVED", "reason": "ok"},
        headers=testadmin_token_headers,
    )
    assert response.status_code == 409


def test_strategy_snapshot_maps_to_request_stage_and_auto_decision(
    client, db, testuser_token_headers
):
    body = _request_body()
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    request_id = created.json()["data"]["id"]
    external_task_id = f"optreq-test-{uuid4().hex[:8]}"
    db.add(
        StrategyTaskIntegration(
            external_task_id=external_task_id,
            task_name="test",
            task_type="model_capability",
            source_system="integration-test",
            schema_version="v2",
            dataset_s3_uri="s3://bucket/test",
            dataset_image_prefix="images/",
            metadata_key="metadata.json",
            bucket="bucket",
            prefix="test",
            import_status="COMPLETED",
            platform_status="comparison_failed",
            automation_stage="comparison",
            automation_status="running",
            platform_result_json='{"candidate": {"f1": 0.7}}',
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
    )
    db.commit()
    from labelu.internal.domain.models.third_party_optimization import OptimizationRequest
    from labelu.internal.application.service import integration_requests as service

    request = db.get(OptimizationRequest, request_id)
    request.strategy_external_task_id = external_task_id
    db.commit()
    service.sync_strategy_flow(db, request)
    db.commit()
    db.expire_all()
    request = db.get(OptimizationRequest, request_id)
    assert request.stage == "WAITING_PUBLISH_DECISION"

    strategy = (
        db.query(StrategyTaskIntegration)
        .filter(StrategyTaskIntegration.external_task_id == external_task_id)
        .one()
    )
    strategy.platform_status = "published"
    strategy.automation_stage = "publish"
    strategy.automation_status = "succeeded"
    db.commit()
    request = db.get(OptimizationRequest, request_id)
    service.sync_strategy_flow(db, request)
    db.commit()
    db.expire_all()
    request = db.get(OptimizationRequest, request_id)
    assert request.stage == "COMPLETED"
    assert request.status == "SUCCEEDED"
    decision = service._get_decision(db, request_id)
    assert decision is not None
    assert decision.decision == "AUTO"


def test_callback_history_endpoint_returns_outbox(
    client, testuser_token_headers
):
    body = _request_body(callback_url="https://example.com/callback")
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    request_id = created.json()["data"]["id"]
    response = client.get(
        f"/api/v2/integrations/requests/{request_id}/callbacks",
        headers=testuser_token_headers,
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["callbacks"]
    assert data["callbacks"][0]["event_type"] == "7gai.workflow.callback.dispatch.v1"


def test_manual_publish_requires_export_evidence(client, testuser_token_headers, testadmin_token_headers):
    body = _request_body()
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    request_id = created.json()["data"]["id"]
    client.post(
        f"/api/v2/integrations/requests/{request_id}/publish-decision",
        json={"decision": "MANUAL_PENDING", "reason": "review"},
        headers=testuser_token_headers,
    )
    client.post(
        f"/api/v2/integrations/requests/{request_id}/publish-decision/approve",
        json={"decision": "APPROVED", "reason": "approved"},
        headers=testadmin_token_headers,
    )
    response = client.post(
        f"/api/v2/integrations/requests/{request_id}/publish",
        headers=testadmin_token_headers,
    )
    assert response.status_code == 409
    assert "导出" in response.json()["msg"]


def test_request_detail_reports_annotation_review_progress(
    client, db, testuser_token_headers
):
    task_id = 901
    db.add_all(
        [
            TaskSample(task_id=task_id, inner_id=1, state="DONE", data="{}"),
            TaskSample(task_id=task_id, inner_id=2, state="NEW", data="{}"),
        ]
    )
    db.commit()
    body = _request_body(
        images=[
            {
                "external_image_id": f"review-img-{uuid4().hex[:8]}",
                "task_id": task_id,
                "sample_id": 9001,
            }
        ],
        min_sample_count=1,
    )
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert created.status_code == 201, created.text
    detail = client.get(
        f"/api/v2/integrations/requests/{created.json()['data']['id']}",
        headers=testuser_token_headers,
    ).json()["data"]
    assert detail["review"]["total"] == 2
    assert detail["review"]["done"] == 1
    assert detail["review"]["pending"] == 1


def test_accumulation_config_requires_admin_for_update(
    client, db, testuser_token_headers, testadmin_token_headers
):
    default = client.get(
        "/api/v1/integration-settings/accumulation",
        headers=testuser_token_headers,
    )
    assert default.status_code == 200
    assert default.json()["data"]["min_sample_count"] >= 1

    denied = client.put(
        "/api/v1/integration-settings/accumulation",
        json={"min_sample_count": 8},
        headers=testuser_token_headers,
    )
    assert denied.status_code == 403

    updated = client.put(
        "/api/v1/integration-settings/accumulation",
        json={"min_sample_count": 8},
        headers=testadmin_token_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["min_sample_count"] == 8
    assert updated.json()["data"]["source"] == "database"

    from labelu.internal.application.service.experiment_dataset_import import (
        _accumulation_min_sample_count,
    )

    assert _accumulation_min_sample_count(db) == 8


def test_stream_snapshot_payload_contains_rows_and_summary(
    client, db, testuser_token_headers
):
    body = _request_body()
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert created.status_code == 201
    snapshot = integration_service.stream_snapshot(db, size=10)
    assert snapshot["total"] >= 1
    assert any(row["external_request_id"] == body["external_request_id"] for row in snapshot["rows"])
    assert snapshot["summary"]["total"] >= 1
    assert snapshot["generated_at"]


def test_manual_publish_job_completion_updates_request(
    client, db, testuser_token_headers, monkeypatch
):
    body = _request_body()
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    request_id = created.json()["data"]["id"]
    from labelu.internal.domain.models.third_party_optimization import (
        IntegrationPublishDecision,
        OptimizationRequest,
    )

    request = db.get(OptimizationRequest, request_id)
    request.stage = "PUBLISHING"
    request.status = "RUNNING"
    request.result_json = '{"job_id": "publish-test-job"}'
    db.add(
        IntegrationPublishDecision(
            request_id=request_id,
            decision="APPROVED",
            auto_publish_eligible=False,
            candidate_export_id=1,
        )
    )
    db.commit()
    import labelu.internal.common.aicm_publish_job as aicm_job

    monkeypatch.setattr(
        aicm_job,
        "load_job",
        lambda job_id: {"status": "completed", "current_step": "finish"},
    )
    assert integration_service.sync_publish_jobs(db) >= 1
    db.expire_all()
    request = db.get(OptimizationRequest, request_id)
    assert request.stage == "COMPLETED"
    assert request.status == "SUCCEEDED"


def test_alerts_report_stale_and_deadline_requests(
    client, db, testuser_token_headers
):
    body = _request_body()
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    request_id = created.json()["data"]["id"]
    from labelu.internal.domain.models.third_party_optimization import OptimizationRequest

    row = db.get(OptimizationRequest, request_id)
    row.status = "WAITING"
    row.stage = "WAITING_FOR_MODEL"
    row.updated_at = datetime.now() - timedelta(hours=2)
    row.deadline_at = datetime.now() - timedelta(hours=1)
    db.commit()
    response = client.get(
        "/api/v2/integrations/requests/alerts",
        params={"stale_minutes": 30},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["counts"]["deadline"] >= 1
    assert data["counts"]["stale"] >= 1
    assert any(item["id"] == request_id for item in data["deadline"])


def test_data_availability_with_no_matching_data_returns_zero(
    client, testuser_token_headers
):
    response = client.get(
        "/api/v2/integrations/data-availability",
        params={"model_id": f"missing-{uuid4().hex[:8]}"},
        headers=testuser_token_headers,
    )
    assert response.status_code == 200
    assert response.json()["data"]["requested"] == 0
    assert response.json()["data"]["available"] == 0
    assert response.json()["data"]["complete"] is False


def test_callback_outbox_is_signed_when_secret_configured(
    client, db, testuser_token_headers, monkeypatch
):
    monkeypatch.setattr(
        settings,
        "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET",
        "callback-test-secret",
        raising=False,
    )
    body = _request_body(callback_url="https://example.com/callback")
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert created.status_code == 201
    request_id = created.json()["data"]["id"]
    db.expire_all()
    row = (
        db.query(WorkflowOutbox)
        .filter(WorkflowOutbox.aggregate_id == str(request_id))
        .order_by(WorkflowOutbox.id.asc())
        .first()
    )
    assert row is not None
    snapshot = json.loads(row.payload_json)
    assert snapshot["headers"]["X-LabelU-Timestamp"]
    assert snapshot["headers"]["X-LabelU-Signature"].startswith("sha256=")


def test_callbacks_are_queued_in_shared_workflow_outbox(
    client, db, testuser_token_headers
):
    body = _request_body(callback_url="https://example.com/callback")
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["data"]["id"]

    db.expire_all()
    rows = (
        db.query(WorkflowOutbox)
        .filter(WorkflowOutbox.aggregate_type == "optimization_request")
        .all()
    )
    assert rows
    assert all(row.status == "PENDING" for row in rows)
    payload = rows[0].payload_json
    assert "schema_version" in payload
    assert "v2" in payload
    assert str(request_id) in rows[0].aggregate_id


def test_package_metadata_maps_external_images_for_image_query(
    client, db, testuser_token_headers
):
    package_id = f"meta-pkg-{uuid4().hex[:10]}"
    external_image_id = f"meta-img-{uuid4().hex[:8]}"
    db.add(
        ExperimentDatasetImport(
            package_id=package_id,
            dataset_type="hard_negative",
            dataset_date="2026-11-01",
            source_system="integration-test",
            event_type="person",
            model_id="model-meta",
            source_payload_json=(
                '{"target":{"event_type":"person","model_id":"model-meta"},'
                '"window_start":"2026-10-01T00:00:00Z","window_end":"2026-10-31T23:59:59Z"}'
            ),
            metadata_json=json.dumps(
                {
                    "samples": [
                        {
                            "external_image_id": external_image_id,
                            "external_event_id": "evt-meta-1",
                            "event_time": "2026-10-12T10:00:00Z",
                            "scene": {"lighting": "evening"},
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            formal_task_ids_json="[]",
            status="COMPLETED",
            declared_sample_count=1,
            valid_sample_count=1,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
    )
    db.commit()
    body = _request_body(
        images=[],
        package_ids=[package_id],
        external_model_id="model-meta",
        event_label="person",
    )
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["data"]["id"]
    matched = integration_service.refresh_requests_for_package(db, package_id)
    assert matched >= 1
    by_image = client.get(
        f"/api/v2/integrations/requests/by-image/{external_image_id}",
        headers=testuser_token_headers,
    )
    assert by_image.status_code == 200
    assert any(item["id"] == request_id for item in by_image.json()["data"])


def test_scene_training_filters_images_by_scene(
    client, testuser_token_headers
):
    now = datetime.now().replace(microsecond=0)
    body = {
        "external_request_id": f"scene-filter-{uuid4().hex[:8]}",
        "source_system": "integration-test",
        "request_type": "scene_training",
        "external_model_id": "model-scene",
        "event_label": "person",
        "scene": {"lighting": "evening"},
        "min_sample_count": 1,
        "images": [
            {
                "external_image_id": f"evening-{uuid4().hex[:8]}",
                "task_id": 501,
                "sample_id": 5001,
                "event_time": now.isoformat(),
                "scene": {"lighting": "evening"},
            },
            {
                "external_image_id": f"morning-{uuid4().hex[:8]}",
                "task_id": 502,
                "sample_id": 5002,
                "event_time": now.isoformat(),
                "scene": {"lighting": "morning"},
            },
        ],
    }
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["coverage"]["requested"] == 1
    assert data["coverage"]["available"] == 1
    assert data["stage"] == "DATASET_CREATED"


def test_new_package_auto_binds_to_compatible_request(
    client, db, testuser_token_headers
):
    body = _request_body(images=[], package_ids=[])
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    assert created.status_code == 201
    request_id = created.json()["data"]["id"]

    package_id = f"auto-pkg-{uuid4().hex[:10]}"
    db.add(
        ExperimentDatasetImport(
            package_id=package_id,
            dataset_type="hard_negative",
            dataset_date="2026-11-01",
            source_system="integration-test",
            event_type=body["event_label"],
            model_id=body["external_model_id"],
            source_payload_json=(
                '{"target":{"event_type":"person","model_id":"model-001"},'
                '"window_start":"2026-10-01T00:00:00Z","window_end":"2026-10-31T23:59:59Z"}'
            ),
            formal_task_ids_json="[401]",
            status="COMPLETED",
            declared_sample_count=1,
            valid_sample_count=1,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
    )
    db.commit()

    matched = integration_service.refresh_requests_for_package(db, package_id)
    assert matched >= 1
    db.expire_all()
    detail = client.get(
        f"/api/v2/integrations/requests/{request_id}",
        headers=testuser_token_headers,
    ).json()["data"]
    assert package_id in detail["package_ids"]
    assert detail["coverage"]["available"] >= 1


def test_service_token_call_is_audited(
    client, db, monkeypatch
):
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "svc-audit-token", raising=False)
    body = _request_body()
    response = client.post(
        "/api/v2/integrations/requests",
        json=body,
        headers={"X-7GAI-Service-Token": "svc-audit-token"},
    )
    assert response.status_code == 201, response.text
    db.expire_all()
    row = (
        db.query(ThirdPartyCallAudit)
        .filter(ThirdPartyCallAudit.external_task_id == body["external_request_id"])
        .one_or_none()
    )
    assert row is not None
    assert row.operation == "integration_request"
    assert row.response_status == 201


def test_service_token_can_create_request(
    client, monkeypatch
):
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "svc-test-token", raising=False)
    body = _request_body()
    response = client.post(
        "/api/v2/integrations/requests",
        json=body,
        headers={"X-7GAI-Service-Token": "svc-test-token"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["data"]["external_request_id"] == body["external_request_id"]


def test_refresh_and_start_pipeline_are_safe_without_package(
    client, testuser_token_headers
):
    body = _request_body()
    created = client.post(
        "/api/v2/integrations/requests", json=body, headers=testuser_token_headers
    )
    request_id = created.json()["data"]["id"]

    refreshed = client.post(
        f"/api/v2/integrations/requests/{request_id}/refresh",
        headers=testuser_token_headers,
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["data"]["id"] == request_id

    started = client.post(
        f"/api/v2/integrations/requests/{request_id}/start-pipeline",
        headers=testuser_token_headers,
    )
    assert started.status_code == 200, started.text
    payload = started.json()["data"]
    assert payload["started"] is False
    assert payload["reason"] == "package_or_source_not_ready"


