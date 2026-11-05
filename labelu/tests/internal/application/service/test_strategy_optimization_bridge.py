from __future__ import annotations

import json
from datetime import datetime

from labelu.internal.application.service import (
    strategy_optimization_bridge as bridge,
)
from labelu.internal.application.service import strategy_evolution as strategy_service
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.third_party_optimization import OptimizationRequest

_MODEL_TARGET = json.dumps(
    {
        "device_type": "normal",
        "event_type": "illegalParkingofNonMotorVehicle",
        "model_id": "591",
        "scene_type": "common_ai_alarm",
        "strategy_id": "strategy_873542d03c5f41dbaf1a11c369a37b80",
    },
    ensure_ascii=False,
)
_DATA_WINDOW = json.dumps(
    {"start_time": "2026-10-15 00:00:00", "end_time": "2026-10-16 00:00:00"}
)
_EXPERIMENT = json.dumps(
    {
        "capability_issue_type": "high_score_false_positive",
        "dataset_requirements": {
            "need_hard_negative_samples": True,
            "need_high_score_fp_samples": True,
            "target_sample_count": 300,
        },
        "symptoms": ["high_score_false_positive_rate_high"],
    },
    ensure_ascii=False,
)


def _integration(
    db,
    *,
    external_task_id: str,
    task_type: str = "model_capability",
    platform_status: str = "waiting_for_model",
    automation_stage: str = "training",
) -> StrategyTaskIntegration:
    with begin_transaction(db):
        row = StrategyTaskIntegration(
            external_task_id=external_task_id,
            schema_version="v1",
            task_name="非机动车违停高评分误报模型优化",
            task_type=task_type,
            source_type="third_party",
            source_system="strategy_evolution",
            dataset_s3_uri="",
            dataset_image_prefix="",
            metadata_key="",
            bucket="",
            prefix="",
            labeling_required=False,
            labeling_labels_json="[]",
            target_json=_MODEL_TARGET,
            data_window_json=_DATA_WINDOW,
            experiment_json=_EXPERIMENT,
            request_payload_json="{}",
            request_sha256="a" * 64,
            import_status="PENDING",
            platform_status=platform_status,
            automation_source="strategy_evolution",
            automation_stage=automation_stage,
            automation_status="running",
            outbound_callback_url=(
                "http://10.10.11.6:8080/api/v1/strategy-evolution/platform-callbacks"
            ),
            created_by=1,
            updated_by=1,
        )
        db.add(row)
        db.flush()
        db.refresh(row)
        return row


def _requests(db) -> list[OptimizationRequest]:
    db.expire_all()
    return db.query(OptimizationRequest).order_by(OptimizationRequest.id).all()


def test_bridge_projects_waiting_model_task_into_optimization_request(db):
    row = _integration(db, external_task_id="strategy-bridge-model-1")

    projected = bridge.bridge_strategy_task_to_optimization_request(
        db=db, external_task_id="strategy-bridge-model-1"
    )

    assert projected is not None
    requests = _requests(db)
    assert len(requests) == 1
    request = requests[0]
    assert request.external_request_id == row.external_task_id
    assert request.source_system == "strategy_evolution"
    assert request.request_type == "model_optimization"
    assert request.strategy_external_task_id == row.external_task_id
    assert request.status == "WAITING"
    assert request.stage == "WAITING_FOR_MODEL"
    assert request.external_model_id == "591"
    assert request.event_label == "illegalParkingofNonMotorVehicle"
    assert request.event_time_from == datetime(2026, 10, 15, 0, 0, 0)
    assert request.event_time_to == datetime(2026, 10, 16, 0, 0, 0)
    assert request.min_sample_count == 300
    assert request.callback_url == row.outbound_callback_url
    assert request.package_ids_json == "[]"
    scene = json.loads(request.scene_json or "{}")
    assert scene["capability_issue_type"] == "high_score_false_positive"
    assert scene["strategy_id"] == "strategy_873542d03c5f41dbaf1a11c369a37b80"


def test_bridge_is_idempotent_for_repeated_accepts(db):
    row = _integration(db, external_task_id="strategy-bridge-model-2")

    first = bridge.bridge_strategy_task_to_optimization_request(
        db=db, external_task_id=row.external_task_id
    )
    second = bridge.bridge_strategy_task_to_optimization_request(
        db=db, external_task_id=row.external_task_id
    )

    assert first is not None and second is not None
    assert first.id == second.id
    assert len(_requests(db)) == 1


def test_bridge_does_not_duplicate_optimization_pipeline_request(db):
    row = _integration(db, external_task_id="strategy-bridge-model-3")
    with begin_transaction(db):
        existing = OptimizationRequest(
            external_request_id="optreq-3",
            source_system="third_party",
            request_type="model_optimization",
            status="RUNNING",
            stage="TRAINING",
            strategy_external_task_id=row.external_task_id,
        )
        db.add(existing)
        db.flush()
        existing_id = int(existing.id)

    projected = bridge.bridge_strategy_task_to_optimization_request(
        db=db, external_task_id=row.external_task_id
    )

    assert projected is not None
    assert int(projected.id) == existing_id
    assert len(_requests(db)) == 1


def test_bridge_maps_published_task_to_succeeded_request(db):
    row = _integration(
        db,
        external_task_id="strategy-bridge-model-4",
        platform_status="published",
        automation_stage="publish",
    )

    projected = bridge.bridge_strategy_task_to_optimization_request(
        db=db, external_task_id=row.external_task_id
    )

    assert projected is not None
    assert projected.status == "SUCCEEDED"
    assert projected.stage == "COMPLETED"
    assert projected.finished_at is not None


def test_bridge_ignores_unknown_task(db):
    assert (
        bridge.bridge_strategy_task_to_optimization_request(
            db=db, external_task_id="strategy-bridge-missing"
        )
        is None
    )
    assert len(_requests(db)) == 0


def test_model_training_intake_projects_optimization_request(client, db, monkeypatch):
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED", True)
    monkeypatch.setattr(
        settings, "STRATEGY_EVOLUTION_SERVICE_TOKEN", "service-test-token"
    )
    monkeypatch.setattr(settings, "STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET", "")
    monkeypatch.setattr(
        strategy_service,
        "_training_weights_command_fields",
        lambda db, model_id: (None, {"model_id": model_id, "reason": "test"}),
    )

    response = client.post(
        "/internal/v1/strategy-evolution/model-trainings",
        headers={"X-7GAI-Service-Token": "service-test-token"},
        json={
            "schema_version": "v1",
            "external_task_id": "strategy-task-bridge-router-1",
            "task_name": "非机动车违停高评分误报模型优化",
            "task_type": "model_capability",
            "target": {
                "device_type": "normal",
                "event_type": "illegalParkingofNonMotorVehicle",
                "model_id": "591",
                "scene_type": "common_ai_alarm",
                "strategy_id": "strategy-1",
            },
            "data_window": {
                "start_time": "2026-10-15 00:00:00",
                "end_time": "2026-10-16 00:00:00",
            },
            "callback_url": (
                "http://10.10.11.6:8080/api/v1/strategy-evolution/platform-callbacks"
            ),
            "experiment": {"capability_issue_type": "high_score_false_positive"},
        },
    )

    assert response.status_code == 202, response.text
    requests = _requests(db)
    assert len(requests) == 1
    assert requests[0].external_request_id == "strategy-task-bridge-router-1"
    assert requests[0].status == "WAITING"
    assert requests[0].stage == "WAITING_FOR_MODEL"
