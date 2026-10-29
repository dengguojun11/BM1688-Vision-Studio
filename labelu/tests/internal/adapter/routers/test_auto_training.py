from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest
from fastapi.testclient import TestClient

from labelu.internal.application.service.auto_training_reconciliation import (
    reconcile_task_for_plan,
)
from labelu.internal.application.service import auto_training_plan as auto_training_plan_service
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.config import settings
from labelu.tests.internal.application.service.test_auto_training_reconciliation import (
    _seed_plan,
    _seed_task,
)
from labelu.internal.domain.models.auto_training_governance import (
    QualityPolicyApproval,
    TrainingEvaluationDataset,
    TrainingEvaluationSample,
)
from labelu.tests.internal.application.service.test_auto_training_lifecycle import (
    _seed as _seed_lifecycle,
)
from labelu.tests.internal.application.service.test_auto_training_bm1688_edge_flow import (
    _seed_pipeline,
)
from labelu.internal.application.service.auto_training_pipeline_runtime import (
    materialize_bm1688_export,
    synchronize_bm1688_export,
    synchronize_edge_validation_job,
)
from labelu.internal.domain.models.auto_training import (
    AutoTrainingEvaluationRun,
    AutoTrainingRound,
)
from labelu.internal.domain.models.annotation_contract import TaskAnnotationContract
from labelu.internal.domain.models.project import Project
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob


BASE = f"{settings.API_V1_STR}/auto-training-plans"


@pytest.fixture(autouse=True)
def _enable_auto_training_plan_writes_for_existing_contract_tests(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)


def _payload(name: str = "违停自动训练") -> dict:
    return {
        "name": name,
        "series_type": "yolo_detection",
        "dataset_category_id": 1,
        "label_set_signature": "a" * 64,
        "class_order_signature": "b" * 64,
        "config": {
            "auto_stop": {"overall_recall_threshold": 0.95},
            "data_admission": {
                "min_eligible_samples_per_task": 50,
                "include_historical_data": False,
            },
            "trigger_policy": {
                "min_new_samples": 10,
                "min_growth_ratio": 0.1,
                "max_wait_hours": 24,
                "weak_label_min_samples": 5,
            },
            "training_policy": {
                "max_auto_tuning_rounds": 0,
                "max_no_improvement_rounds": 3,
                "max_technical_failures": 3,
                "cooldown_hours": 1,
                "priority": 100,
                "daily_gpu_budget_minutes": 240,
                "weekly_gpu_budget_minutes": 1200,
            },
            "quality_policy_binding": {
                "policy_name": "industry-v1",
                "version": 1,
                "signature": "c" * 64,
                "approval_id": 1,
            },
            "evaluation_binding": {
                "validation_dataset_id": 1,
                "benchmark_dataset_id": 2,
                "sealed_acceptance_dataset_id": 3,
                "edge_validation_dataset_id": 5,
            },
            "bm1688_policy": {
                "enabled": True,
                "compile_final_quality_candidate": True,
                "allow_manual_diagnostic_compile": True,
                "calibration_dataset_id": 4,
                "max_pt_to_bmodel_metric_drop": 0.02,
                "technical_retry_limit": 2,
            },
            "edge_validation_policy": {
                "enabled": True,
                "device_pool": ["bm1688-lab"],
                "mode": "run_and_eval",
                "minimum_valid_inferences": 100,
                "max_p95_latency_ms": 80,
                "max_error_rate": 0.01,
                "timeout_seconds": 7200,
                "technical_retry_limit": 2,
            },
            "retention_policy": {"days": 180},
            "callback_policy": {"enabled": False},
        },
    }


def _create(client: TestClient, headers, name="违停自动训练") -> dict:
    response = client.post(BASE, headers=headers, json=_payload(name))
    assert response.status_code == 201, response.text
    return response.json()["data"]


def test_project_runtime_summary_is_project_scoped_and_exposes_latest_iteration(
    client, db, testadmin_token_headers
):
    project_a = Project(
        owner_id=1,
        name="summary-project-a",
        status="ACTIVE",
        created_by=1,
        updated_by=1,
    )
    project_b = Project(
        owner_id=1,
        name="summary-project-b",
        status="ACTIVE",
        created_by=1,
        updated_by=1,
    )
    db.add_all([project_a, project_b])
    db.flush()

    plan_a = _seed_plan(db, name="summary-plan-a")
    plan_a.project_id = project_a.id
    plan_b = _seed_plan(db, name="summary-plan-b")
    plan_b.project_id = project_b.id
    db.flush()
    db.add(
        AutoTrainingRound(
            plan_id=plan_a.id,
            plan_version_id=plan_a.current_version_id,
            round_no=3,
            state="TRAINING",
            trigger_type="AUTO",
            execution_signature="summary-project-a-round-3",
            eligible_sample_count=12,
            train_count=8,
            val_count=2,
            test_count=2,
        )
    )
    # The HTTP dependency uses a separate test session, so make this fixture
    # visible to the client before asserting the read-only route.
    db.commit()

    response_a = client.get(
        f"{BASE}/summary?project_id={project_a.id}",
        headers=testadmin_token_headers,
    )
    assert response_a.status_code == 200, response_a.text
    summary_a = response_a.json()["data"]
    assert summary_a["project_id"] == project_a.id
    assert summary_a["plan_count"] == 1
    assert summary_a["round_count"] == 1
    assert summary_a["active_plan_count"] == 1
    assert summary_a["latest_iteration"]["round_no"] == 3
    assert summary_a["latest_iteration"]["state"] == "TRAINING"
    assert [row["id"] for row in summary_a["plans"]] == [plan_a.id]
    assert summary_a["recent_iterations"][0]["plan_id"] == plan_a.id

    response_b = client.get(
        f"{BASE}/summary?project_id={project_b.id}",
        headers=testadmin_token_headers,
    )
    assert response_b.status_code == 200, response_b.text
    summary_b = response_b.json()["data"]
    assert summary_b["plan_count"] == 1
    assert summary_b["round_count"] == 0
    assert [row["id"] for row in summary_b["plans"]] == [plan_b.id]
    assert summary_b["latest_iteration"] is None


def test_project_runtime_summary_rejects_unknown_project(client, testadmin_token_headers):
    response = client.get(f"{BASE}/summary?project_id=999999", headers=testadmin_token_headers)
    assert response.status_code == 404


def test_project_runtime_summary_includes_project_scoped_evaluation_reports(
    client, db, testadmin_token_headers
):
    project_a = Project(
        owner_id=1,
        name="evaluation-report-project-a",
        status="ACTIVE",
        created_by=1,
        updated_by=1,
    )
    project_b = Project(
        owner_id=1,
        name="evaluation-report-project-b",
        status="ACTIVE",
        created_by=1,
        updated_by=1,
    )
    db.add_all([project_a, project_b])
    db.flush()

    plan_a = _seed_plan(db, name="evaluation-report-plan-a")
    plan_a.project_id = project_a.id
    plan_b = _seed_plan(db, name="evaluation-report-plan-b")
    plan_b.project_id = project_b.id
    db.flush()

    round_a = AutoTrainingRound(
        plan_id=plan_a.id,
        plan_version_id=plan_a.current_version_id,
        round_no=2,
        state="QUALITY_PASSED",
        trigger_type="AUTO",
        execution_signature="evaluation-report-round-a",
    )
    db.add(round_a)
    db.flush()
    evaluation_a = AutoTrainingEvaluationRun(
        round_id=round_a.id,
        policy_signature="q" * 64,
        evaluation_binding_signature="e" * 64,
        status="COMPLETED",
        overall_metrics_json=json.dumps({"overall_recall": 0.9876}),
        decision_code="AUTO_QUALITY_REACHED",
    )
    db.add(evaluation_a)
    db.commit()

    response_a = client.get(
        f"{BASE}/summary?project_id={project_a.id}",
        headers=testadmin_token_headers,
    )
    assert response_a.status_code == 200, response_a.text
    reports_a = response_a.json()["data"]["evaluation_reports"]
    assert len(reports_a) == 1
    assert reports_a[0]["id"] == evaluation_a.id
    assert reports_a[0]["title"] == "evaluation-report-plan-a / 第2轮评估"
    assert reports_a[0]["summary_level"] == "AUTO_QUALITY_REACHED"
    assert reports_a[0]["training_quality_status"] == "AUTO_QUALITY_REACHED"
    assert reports_a[0]["generalization_quality_status"] == "PENDING"
    assert reports_a[0]["conversion_quality_status"] == "PENDING"
    assert reports_a[0]["edge_usability_status"] == "PENDING"
    assert reports_a[0]["release_readiness"] == "BLOCKED"
    assert "尚未完成" in reports_a[0]["quality_gate_reason"]
    assert reports_a[0]["summary_brief"] == "整体召回率 0.9876"
    assert reports_a[0]["plan_id"] == plan_a.id
    assert reports_a[0]["round_id"] == round_a.id

    response_b = client.get(
        f"{BASE}/summary?project_id={project_b.id}",
        headers=testadmin_token_headers,
    )
    assert response_b.status_code == 200, response_b.text
    assert response_b.json()["data"]["evaluation_reports"] == []


def _seed_representative_task(
    db,
    *,
    user_id: int,
    name: str,
    label_signature: str = "a" * 64,
    class_signature: str = "b" * 64,
    category_id: int = 1,
) -> Task:
    task = Task(
        name=name,
        media_type="IMAGE",
        status="INPROGRESS",
        created_by=user_id,
        updated_by=user_id,
        dataset_category_id=category_id,
    )
    db.add(task)
    db.flush()
    contract = TaskAnnotationContract(
        task_id=task.id,
        revision=1,
        annotation_kind="bbox",
        label_ids_json="[1]",
        labels_snapshot_json="[]",
        label_set_signature=label_signature,
        class_order_signature=class_signature,
        status="active",
        created_by=user_id,
    )
    db.add(contract)
    db.flush()
    task.current_annotation_contract_id = contract.id
    db.add(task)
    return task


def _seed_approval_dependencies(db) -> dict[str, int]:
    with begin_transaction(db):
        approval = QualityPolicyApproval(
            policy_name="industry-v1",
            policy_version=1,
            policy_signature="c" * 64,
            scope_signature="s" * 64,
            status="APPROVED",
            policy_snapshot_json="{}",
            submitted_by=1,
            approved_by=1,
        )
        db.add(approval)
        db.flush()
        ids = {"approval_id": approval.id}
        for index, (key, kind) in enumerate((
            ("validation_dataset_id", "VALIDATION"),
            ("benchmark_dataset_id", "BENCHMARK"),
            ("sealed_acceptance_dataset_id", "SEALED_ACCEPTANCE"),
            ("calibration_dataset_id", "BM1688_CALIBRATION"),
            ("edge_validation_dataset_id", "EDGE_VALIDATION"),
        ), start=1):
            row = TrainingEvaluationDataset(
                owner_id=1,
                name=f"approved-{kind.lower()}",
                dataset_kind=kind,
                series_type="yolo_detection",
                dataset_category_id=1,
                label_set_signature="a" * 64,
                version_no=1,
                status="APPROVED",
                manifest_rel_path=f"evaluation/{kind.lower()}.json",
                manifest_sha256=(str(index) * 64),
                sample_count=100,
                grouping_policy_json="{}",
                created_by=1,
                approved_by=1,
            )
            db.add(row)
            db.flush()
            ids[key] = row.id
    return ids


def test_create_plan_accepts_one_or_multiple_matching_representative_tasks(
    client: TestClient, testuser_token_headers, db
):
    user = db.query(User).filter(User.username == "test@example.com").one()
    with begin_transaction(db):
        first = _seed_representative_task(
            db, user_id=user.id, name="auto-plan-first"
        )
        second = _seed_representative_task(
            db, user_id=user.id, name="auto-plan-second"
        )
        db.flush()

    payload = _payload("批量代表任务计划")
    payload["config"]["data_admission"] = {
        "min_eligible_samples_per_task": 50,
        "include_historical_data": True,
        "history_task_ids": [first.id, first.id, second.id],
        "history_confirmed": True,
    }
    response = client.post(BASE, headers=testuser_token_headers, json=payload)

    assert response.status_code == 201, response.text
    admission = response.json()["data"]["current_version"]["config"][
        "data_admission"
    ]
    assert admission["history_task_ids"] == [first.id, second.id]


def test_create_plan_rejects_representative_task_with_mismatched_contract(
    client: TestClient, testuser_token_headers, db
):
    user = db.query(User).filter(User.username == "test@example.com").one()
    with begin_transaction(db):
        matching = _seed_representative_task(
            db, user_id=user.id, name="auto-plan-matching"
        )
        mismatched = _seed_representative_task(
            db,
            user_id=user.id,
            name="auto-plan-mismatched",
            label_signature="d" * 64,
        )
        db.flush()

    payload = _payload("不兼容代表任务计划")
    payload["config"]["data_admission"] = {
        "min_eligible_samples_per_task": 50,
        "include_historical_data": True,
        "history_task_ids": [matching.id, mismatched.id],
        "history_confirmed": True,
    }
    response = client.post(BASE, headers=testuser_token_headers, json=payload)

    assert response.status_code == 422
    assert f"代表任务 #{mismatched.id} 的标签契约与计划不一致" in response.text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", f"{BASE}/preview"),
        ("POST", BASE),
        ("GET", BASE),
        ("GET", f"{BASE}/1"),
        ("PATCH", f"{BASE}/1"),
        ("POST", f"{BASE}/1/submit"),
        ("POST", f"{BASE}/1/approve"),
        ("POST", f"{BASE}/1/pause"),
        ("POST", f"{BASE}/1/resume"),
        ("POST", f"{BASE}/1/archive"),
        ("GET", f"{BASE}/1/versions"),
        ("GET", f"{BASE}/1/audit-events"),
        ("POST", f"{BASE}/1/scan-preview"),
        ("GET", f"{BASE}/1/scan-preview/1"),
        ("GET", f"{BASE}/1/task-scopes"),
        ("GET", f"{BASE}/1/sample-pool"),
        ("POST", f"{BASE}/1/reconcile"),
        ("GET", f"{BASE}/1/rounds"),
        ("POST", f"{BASE}/1/rounds/preview"),
        ("POST", f"{BASE}/1/rounds"),
        ("GET", f"{settings.API_V1_STR}/auto-training-rounds/1"),
        ("GET", f"{settings.API_V1_STR}/auto-training-rounds/1/samples"),
        ("GET", f"{settings.API_V1_STR}/auto-training-rounds/1/quality"),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/cancel"),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/retry-stage"),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/promote-to-bm1688"),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/start-edge-validation"),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/quality-evidence"),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/publish"),
        ("POST", f"{settings.API_V1_STR}/samples/1/training-review"),
        ("POST", f"{settings.API_V1_STR}/training-reviews/1/invalidate"),
        ("POST", f"{settings.API_V1_STR}/training-evaluation-datasets"),
        ("POST", f"{settings.API_V1_STR}/training-evaluation-datasets/auto-generate"),
        ("GET", f"{settings.API_V1_STR}/training-evaluation-datasets"),
        ("PATCH", f"{settings.API_V1_STR}/training-evaluation-datasets/1"),
        ("POST", f"{settings.API_V1_STR}/training-evaluation-datasets/1/submit"),
        ("POST", f"{settings.API_V1_STR}/training-evaluation-datasets/1/approve"),
        ("GET", f"{settings.API_V1_STR}/training-evaluation-datasets/1/samples"),
        ("PUT", f"{settings.API_V1_STR}/training-evaluation-datasets/1/samples"),
        ("POST", f"{settings.API_V1_STR}/yolo-quality-policies/industry-v1/submit-approval"),
        ("POST", f"{settings.API_V1_STR}/yolo-quality-policies/industry-v1/approve"),
        ("GET", f"{settings.API_V1_STR}/quality-policy-approvals"),
        ("GET", f"{settings.API_V1_STR}/auto-training-evaluation-jobs"),
        ("POST", f"{settings.API_V1_STR}/auto-training-evaluation-jobs/1/claim"),
        ("GET", f"{settings.API_V1_STR}/auto-training-settings"),
        ("PATCH", f"{settings.API_V1_STR}/auto-training-settings"),
        ("POST", f"{settings.API_V1_STR}/quality-policy-approvals/submit-current"),
    ],
)
def test_all_routes_require_authentication(client: TestClient, method, path):
    assert client.request(method, path, json={}).status_code == 401


@pytest.mark.parametrize("series", ["unknown", "", "plate_ocr_only"])
def test_create_rejects_unknown_series(client, testuser_token_headers, series):
    payload = _payload()
    payload["series_type"] = series

    response = client.post(BASE, headers=testuser_token_headers, json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize("minimum", [-1, 0, 49])
def test_preview_reports_minimum_sample_violation(
    client, testuser_token_headers, minimum
):
    payload = _payload()
    payload["config"]["data_admission"][
        "min_eligible_samples_per_task"
    ] = minimum

    response = client.post(
        f"{BASE}/preview", headers=testuser_token_headers, json=payload
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["config_complete"] is False
    assert "data_admission.min_eligible_samples_per_task" in data["missing_fields"]


@pytest.mark.parametrize("threshold", [0.0, 0.9499, 0.9501, 1.0])
def test_preview_rejects_non_authoritative_stop_threshold(
    client, testuser_token_headers, threshold
):
    payload = _payload()
    payload["config"]["auto_stop"]["overall_recall_threshold"] = threshold

    response = client.post(
        f"{BASE}/preview", headers=testuser_token_headers, json=payload
    )

    assert response.status_code == 200
    assert response.json()["data"]["config_complete"] is False


def test_plan_owner_lifecycle_and_optimistic_locking(
    client, db, testuser_token_headers, testadmin_token_headers, monkeypatch
):
    from labelu.internal.common import edge_device_registry

    monkeypatch.setattr(
        edge_device_registry,
        "list_device_records",
        lambda: [{"id": "bm1688-lab", "enabled": True}],
    )
    dependencies = _seed_approval_dependencies(db)
    payload = _payload()
    payload["config"]["quality_policy_binding"]["approval_id"] = dependencies[
        "approval_id"
    ]
    payload["config"]["evaluation_binding"].update(
        {
            key: dependencies[key]
            for key in (
                "validation_dataset_id",
                "benchmark_dataset_id",
                "sealed_acceptance_dataset_id",
                "edge_validation_dataset_id",
            )
        }
    )
    payload["config"]["bm1688_policy"]["calibration_dataset_id"] = dependencies[
        "calibration_dataset_id"
    ]
    response = client.post(BASE, headers=testuser_token_headers, json=payload)
    assert response.status_code == 201, response.text
    created = response.json()["data"]
    plan_id = created["id"]
    assert created["status"] == "DRAFT_COMPLETE"
    assert created["version"] == 1

    duplicate = client.post(BASE, headers=testuser_token_headers, json=_payload())
    assert duplicate.status_code == 409

    listing = client.get(
        f"{BASE}?page=0&size=1", headers=testuser_token_headers
    )
    assert listing.status_code == 200
    assert listing.json()["meta_data"]["total"] >= 1

    patch = client.patch(
        f"{BASE}/{plan_id}",
        headers=testuser_token_headers,
        json={
            "expected_version": 1,
            "name": "违停自动训练-v2",
            "config": {
                **payload["config"],
                "trigger_policy": {
                    **payload["config"]["trigger_policy"],
                    "min_new_samples": 11,
                },
            },
        },
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["data"]["version"] == 2

    stale = client.patch(
        f"{BASE}/{plan_id}",
        headers=testuser_token_headers,
        json={
            "expected_version": 1,
            "name": "stale-write",
            "config": _payload()["config"],
        },
    )
    assert stale.status_code == 409

    submitted = client.post(
        f"{BASE}/{plan_id}/submit",
        headers=testuser_token_headers,
        json={"expected_version": 2, "reason": "申请启用"},
    )
    assert submitted.status_code == 200
    assert submitted.json()["data"]["status"] == "PENDING_APPROVAL"

    denied = client.post(
        f"{BASE}/{plan_id}/approve",
        headers=testuser_token_headers,
        json={"expected_version": 3, "reason": "owner cannot approve"},
    )
    assert denied.status_code == 403

    approved = client.post(
        f"{BASE}/{plan_id}/approve",
        headers=testadmin_token_headers,
        json={"expected_version": 3, "reason": "策略和评估集已核验"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["status"] == "ACTIVE"

    paused = client.post(
        f"{BASE}/{plan_id}/pause",
        headers=testuser_token_headers,
        json={"expected_version": 4, "reason": "资源维护"},
    )
    assert paused.status_code == 200
    resumed = client.post(
        f"{BASE}/{plan_id}/resume",
        headers=testuser_token_headers,
        json={"expected_version": 5, "reason": "资源恢复"},
    )
    assert resumed.status_code == 200

    audit = client.get(
        f"{BASE}/{plan_id}/audit-events", headers=testuser_token_headers
    )
    assert audit.status_code == 200
    assert len(audit.json()["data"]) >= 5


def test_plan_name_only_edit_keeps_single_config_version(
    client, testuser_token_headers
):
    created = _create(client, testuser_token_headers, name="验收计划")

    response = client.patch(
        f"{BASE}/{created['id']}",
        headers=testuser_token_headers,
        json={
            "expected_version": 1,
            "name": "非机动车违停自动迭代训练计划",
            "config": _payload()["config"],
            "change_reason": "名称改为中文",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["name"] == "非机动车违停自动迭代训练计划"
    assert data["version"] == 2
    assert data["current_version_no"] == 1
    versions = client.get(
        f"{BASE}/{created['id']}/versions", headers=testuser_token_headers
    )
    assert versions.status_code == 200
    assert len(versions.json()["data"]) == 1


def test_runtime_settings_require_admin_and_enforce_stage_dependencies(
    client,
    testuser_token_headers,
    testadmin_token_headers,
    monkeypatch,
):
    state = dict(auto_training_plan_service.AUTO_TRAINING_SWITCH_DEFAULTS)
    monkeypatch.setattr(
        auto_training_plan_service,
        "load_auto_training_switches",
        lambda: dict(state),
    )
    monkeypatch.setattr(
        auto_training_plan_service,
        "write_group_values",
        lambda _group, values, **_kwargs: state.update(values) or True,
    )
    payload = {**state, "schema_ready": True, "reason": "迁移已验收"}

    current = client.get(
        f"{settings.API_V1_STR}/auto-training-settings",
        headers=testuser_token_headers,
    )
    assert current.status_code == 200
    assert current.json()["data"]["values"] == state
    assert current.json()["data"]["can_edit"] is False
    assert current.json()["data"]["dependencies"]["train_enabled"] == [
        "scheduler_enabled"
    ]

    denied = client.patch(
        f"{settings.API_V1_STR}/auto-training-settings",
        headers=testuser_token_headers,
        json=payload,
    )
    assert denied.status_code == 403

    invalid = client.patch(
        f"{settings.API_V1_STR}/auto-training-settings",
        headers=testadmin_token_headers,
        json={**payload, "train_enabled": True},
    )
    assert invalid.status_code == 422
    assert "自动训练需要先开启：自动调度" in invalid.text

    saved = client.patch(
        f"{settings.API_V1_STR}/auto-training-settings",
        headers=testadmin_token_headers,
        json=payload,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["values"]["schema_ready"] is True


def test_runtime_settings_preserve_locked_values_and_save_unlocked_switches(
    client,
    testadmin_token_headers,
    monkeypatch,
):
    state = dict(auto_training_plan_service.AUTO_TRAINING_SWITCH_DEFAULTS)
    state["schema_ready"] = True
    monkeypatch.setenv("LABELU_AUTO_TRAINING_SCHEMA_READY", "true")
    monkeypatch.setattr(
        auto_training_plan_service,
        "load_auto_training_switches",
        lambda: dict(state),
    )
    monkeypatch.setattr(
        auto_training_plan_service,
        "write_group_values",
        lambda _group, values, **_kwargs: state.update(values) or True,
    )

    response = client.patch(
        f"{settings.API_V1_STR}/auto-training-settings",
        headers=testadmin_token_headers,
        json={
            **state,
            "schema_ready": False,
            "observe_only": True,
            "reason": "切换为观察模式",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["values"]["schema_ready"] is True
    assert data["values"]["observe_only"] is True
    assert data["locked_keys"] == ["schema_ready"]


def test_submit_current_quality_policy_is_idempotent_and_admin_can_approve(
    client,
    testuser_token_headers,
    testadmin_token_headers,
    monkeypatch,
):
    from types import SimpleNamespace

    from labelu.internal.application.response.yolo_quality_policy import (
        YoloQualityPolicyResponse,
    )
    from labelu.internal.application.service import yolo_quality_policy

    policy = YoloQualityPolicyResponse(
        policy_name="nonmotor-industry-v1",
        scene_name="非机动车违停",
        rect_preset_slug="nonmotor",
        label_ids=[1],
        model_family="yolov11",
        annotation_kind="bbox",
        label_set_signature="a" * 64,
        status="active",
        source="manual",
        version=3,
    )
    monkeypatch.setattr(
        yolo_quality_policy,
        "list_quality_policies_bundle",
        lambda: SimpleNamespace(policies=[policy]),
    )
    url = f"{settings.API_V1_STR}/quality-policy-approvals/submit-current"
    payload = {
        "policy_name": policy.policy_name,
        "reason": "自动训练计划使用当前质量标准",
    }

    submitted = client.post(url, headers=testuser_token_headers, json=payload)
    assert submitted.status_code == 200, submitted.text
    first = submitted.json()["data"]
    assert first["policy_version"] == 3
    assert first["status"] == "PENDING_APPROVAL"

    replay = client.post(url, headers=testuser_token_headers, json=payload)
    assert replay.status_code == 200
    assert replay.json()["data"]["id"] == first["id"]

    approved = client.post(
        f"{settings.API_V1_STR}/yolo-quality-policies/{policy.policy_name}/approve",
        headers=testadmin_token_headers,
        json={"reason": "策略范围和阈值已核验"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["status"] == "APPROVED"


@pytest.mark.parametrize(
    ("device_pool", "plan_name"),
    [
        (["bm1688-local-acceptance"], "legacy-edge-device-plan"),
        ([], "automatic-edge-device-plan"),
    ],
)
def test_plan_approval_uses_global_edge_device_pool(
    client,
    db,
    testuser_token_headers,
    testadmin_token_headers,
    device_pool,
    plan_name,
):
    dependencies = _seed_approval_dependencies(db)
    payload = _payload(name=plan_name)
    payload["config"]["edge_validation_policy"]["device_pool"] = device_pool
    payload["config"]["quality_policy_binding"]["approval_id"] = dependencies[
        "approval_id"
    ]
    payload["config"]["evaluation_binding"].update(
        {
            key: dependencies[key]
            for key in (
                "validation_dataset_id",
                "benchmark_dataset_id",
                "sealed_acceptance_dataset_id",
                "edge_validation_dataset_id",
            )
        }
    )
    payload["config"]["bm1688_policy"]["calibration_dataset_id"] = dependencies[
        "calibration_dataset_id"
    ]
    created = client.post(BASE, headers=testuser_token_headers, json=payload)
    assert created.status_code == 201, created.text
    plan = created.json()["data"]
    submitted = client.post(
        f"{BASE}/{plan['id']}/submit",
        headers=testuser_token_headers,
        json={"expected_version": plan["version"], "reason": "提交审批"},
    )
    assert submitted.status_code == 200

    approved = client.post(
        f"{BASE}/{plan['id']}/approve",
        headers=testadmin_token_headers,
        json={"expected_version": submitted.json()["data"]["version"]},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["data"]["status"] == "ACTIVE"


def test_paging_limits_and_not_found(client, testuser_token_headers):
    for query in ("?page=-1", "?size=0", "?size=101"):
        assert client.get(f"{BASE}{query}", headers=testuser_token_headers).status_code == 422
    for path in (
        f"{BASE}/1/sample-pool",
        f"{settings.API_V1_STR}/auto-training-rounds/1/samples",
    ):
        for query in ("?page=-1", "?size=0", "?size=101"):
            assert client.get(f"{path}{query}", headers=testuser_token_headers).status_code == 422
    assert client.get(f"{BASE}/999999", headers=testuser_token_headers).status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("PATCH", f"{BASE}/1", {"expected_version": 0, "config": {}}),
        ("POST", f"{BASE}/1/submit", {"expected_version": 1, "reason": ""}),
        ("POST", f"{BASE}/1/approve", {"expected_version": 0, "reason": "approve"}),
        ("POST", f"{BASE}/1/pause", {"expected_version": 1, "reason": " "}),
        ("POST", f"{BASE}/1/resume", {"expected_version": 1, "reason": "x" * 2001}),
        ("POST", f"{BASE}/1/archive", {"expected_version": 1}),
        ("POST", f"{BASE}/1/rounds", {"reason": ""}),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/cancel", {"reason": ""}),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/retry-stage", {"stage": "UNKNOWN", "reason": "retry", "expected_state": "TRAIN_FAILED"}),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/promote-to-bm1688", {"reason": ""}),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/start-edge-validation", {"reason": ""}),
        ("POST", f"{settings.API_V1_STR}/auto-training-rounds/1/publish", {"expected_state": "PUBLISH_READY", "confirmation": "YES", "reason": "publish"}),
        ("POST", f"{settings.API_V1_STR}/samples/1/training-review", {"annotation_sha256": "short", "review_status": "UNKNOWN"}),
        ("POST", f"{settings.API_V1_STR}/training-reviews/1/invalidate", {"reason": ""}),
        ("POST", f"{settings.API_V1_STR}/training-evaluation-datasets", {"name": "invalid", "dataset_kind": "UNKNOWN", "series_type": "yolo_detection", "dataset_category_id": 0, "label_set_signature": "short", "grouping_policy": {}}),
        ("PATCH", f"{settings.API_V1_STR}/training-evaluation-datasets/1", {"sample_count": -1}),
        ("POST", f"{settings.API_V1_STR}/training-evaluation-datasets/1/submit", {"reason": ""}),
        ("POST", f"{settings.API_V1_STR}/training-evaluation-datasets/1/approve", {"reason": ""}),
        ("PUT", f"{settings.API_V1_STR}/training-evaluation-datasets/1/samples", {"samples": []}),
        ("POST", f"{settings.API_V1_STR}/yolo-quality-policies/industry-v1/submit-approval", {"policy_version": 0, "policy_signature": "short", "scope_signature": "short", "policy_snapshot": {}, "reason": ""}),
        ("POST", f"{settings.API_V1_STR}/yolo-quality-policies/industry-v1/approve", {"reason": ""}),
    ],
)
def test_mutating_routes_reject_invalid_boundary_payloads(
    client, testuser_token_headers, method, path, payload
):
    response = client.request(method, path, headers=testuser_token_headers, json=payload)
    assert response.status_code == 422, response.text


def test_draft_plan_core_contract_fields_are_editable(
    client, testuser_token_headers
):
    created = _create(client, testuser_token_headers, name="editable-contract-plan")
    response = client.patch(
        f"{BASE}/{created['id']}",
        headers=testuser_token_headers,
        json={
            "expected_version": 1,
            "name": "editable-contract-plan-v2",
            "series_type": "plate_detection",
            "dataset_category_id": 9,
            "label_set_signature": "d" * 64,
            "class_order_signature": "e" * 64,
            "config": _payload()["config"],
            "change_reason": "switch to approved plate contract",
        },
    )

    assert response.status_code == 200, response.text
    detail = response.json()["data"]
    assert detail["series_type"] == "plate_detection"
    assert detail["dataset_category_id"] == 9
    assert detail["label_set_signature"] == "d" * 64
    assert detail["class_order_signature"] == "e" * 64


def test_plan_detail_reports_live_technical_switches(
    client, monkeypatch, testuser_token_headers
):
    created = _create(client, testuser_token_headers, name="technical-switch-plan")
    monkeypatch.setenv("LABELU_AUTO_TRAINING_SCHEMA_READY", "true")
    monkeypatch.setenv("LABELU_AUTO_TRAINING_OBSERVE_ONLY", "false")
    monkeypatch.setenv("LABELU_AUTO_TRAINING_TRAIN_ENABLED", "true")
    monkeypatch.setenv("LABELU_AUTO_TRAINING_BM1688_ENABLED", "true")
    monkeypatch.setenv("LABELU_AUTO_TRAINING_EDGE_VALIDATION_ENABLED", "false")

    response = client.get(
        f"{BASE}/{created['id']}", headers=testuser_token_headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["technical_stage_flags"] == {
        "schema_ready": True,
        "observe_only": False,
        "outbox_enabled": False,
        "pool_enabled": False,
            "scheduler_enabled": False,
            "train_enabled": True,
            "bm1688_enabled": True,
            "edge_validation_enabled": False,
            "worker_command_enabled": False,
        }


def test_create_round_api_uses_frozen_manifest_and_blocks_duplicate_active_round(
    client, db, tmp_path, monkeypatch, testuser_token_headers
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="api-frozen-round-plan")
        task, _contract, _samples = _seed_task(db, tmp_path, count=50)
        reconcile_task_for_plan(db, plan=plan, task=task)
        plan_id = plan.id

    first = client.post(
        f"{BASE}/{plan_id}/rounds",
        headers=testuser_token_headers,
        json={"reason": "人工确认本轮数据"},
    )
    assert first.status_code == 202, first.text
    created = first.json()["data"]
    assert created["eligible_sample_count"] == 50
    assert created["manifest_rel_path"].endswith("manifest.json")
    assert len(created["manifest_sha256"]) == 64
    assert created["train_count"] + created["val_count"] + created["test_count"] == 50

    duplicate = client.post(
        f"{BASE}/{plan_id}/rounds",
        headers=testuser_token_headers,
        json={"reason": "重复点击"},
    )
    assert duplicate.status_code == 409


def test_admin_only_diagnostic_compile(
    client, db, testuser_token_headers, testadmin_token_headers
):
    from labelu.internal.common.db import begin_transaction
    from labelu.internal.domain.models.auto_training import AutoTrainingPlan, AutoTrainingRound

    created = _create(client, testuser_token_headers, name="车牌诊断计划")
    plan_id = created["id"]
    with begin_transaction(db):
        plan = db.query(AutoTrainingPlan).filter(AutoTrainingPlan.id == plan_id).one()
        row = AutoTrainingRound(
            plan_id=plan.id,
            plan_version_id=plan.current_version_id,
            round_no=1,
            state="QUALITY_FAILED",
            trigger_type="MANUAL",
            trigger_snapshot_json="{}",
            execution_signature="d" * 64,
            stage_status_json="{}",
            stage_errors_json="{}",
            retry_count_json="{}",
        )
        db.add(row)
        db.flush()
        round_id = row.id

    denied = client.post(
        f"{settings.API_V1_STR}/auto-training-rounds/{round_id}/promote-to-bm1688",
        headers=testuser_token_headers,
        json={"reason": "ordinary user"},
    )
    assert denied.status_code == 403

    missing_reason = client.post(
        f"{settings.API_V1_STR}/auto-training-rounds/{round_id}/promote-to-bm1688",
        headers=testadmin_token_headers,
        json={"reason": ""},
    )
    assert missing_reason.status_code == 422

    accepted = client.post(
        f"{settings.API_V1_STR}/auto-training-rounds/{round_id}/promote-to-bm1688",
        headers=testadmin_token_headers,
        json={"reason": "验证量化兼容性"},
    )
    assert accepted.status_code == 202
    assert accepted.json()["data"]["origin"] == "MANUAL_DIAGNOSTIC"


def test_quality_evidence_endpoint_is_admin_only_validated_and_idempotent(
    client, db, testuser_token_headers, testadmin_token_headers
):
    _plan_id, round_id, job_id = _seed_lifecycle(db)
    with begin_transaction(db):
        from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
        from labelu.internal.application.service.auto_training_lifecycle import (
            synchronize_training_job,
        )

        job = db.query(YoloTrainJob).filter_by(id=job_id).one()
        job.status = "COMPLETED"
        job.weights_path = "runs/auto/best.pt"
        synchronize_training_job(db, job_id=job_id)
        round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
        evaluation = AutoTrainingEvaluationRun(
            round_id=round_id,
            policy_signature="q" * 64,
            evaluation_binding_signature="e" * 64,
            status="PENDING",
            request_manifest_rel_path="auto-training/evaluation/request.json",
            request_manifest_sha256="a" * 64,
        )
        db.add(evaluation)
        db.flush()
        round_row.evaluation_run_id = evaluation.id
        evaluation_run_id = evaluation.id
        evaluation_request_sha256 = evaluation.request_manifest_sha256

    url = f"{settings.API_V1_STR}/auto-training-rounds/{round_id}/quality-evidence"
    payload = {
        "expected_state": "PT_EVALUATING",
        "evaluation_run_id": evaluation_run_id,
        "evaluation_request_sha256": evaluation_request_sha256,
        "overall_metrics": {"overall_recall": 0.95},
        "per_label_metrics": {"vehicle": {"recall": 0.81}},
        "plate_component_metrics": {},
        "champion_comparison": {},
        "evidence_manifest_rel_path": "auto-training/evidence/round.json",
        "evidence_manifest_sha256": "e" * 64,
    }
    denied = client.post(url, headers=testuser_token_headers, json=payload)
    assert denied.status_code == 403

    invalid = client.post(
        url,
        headers=testadmin_token_headers,
        json={**payload, "overall_metrics": {"overall_recall": 1.0001}},
    )
    assert invalid.status_code == 422

    accepted = client.post(url, headers=testadmin_token_headers, json=payload)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["data"]["decision_code"] == "AUTO_QUALITY_REACHED"
    from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
    import json

    persisted_job = db.query(YoloTrainJob).filter_by(id=job_id).one()
    assert json.loads(persisted_job.metrics_summary)["auto_training_evaluation"][
        "evidence_manifest_sha256"
    ] == "e" * 64

    replay = client.post(url, headers=testadmin_token_headers, json=payload)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["changed"] is False

    conflict = client.post(
        url,
        headers=testadmin_token_headers,
        json={**payload, "evidence_manifest_sha256": "f" * 64},
    )
    assert conflict.status_code == 409


@pytest.mark.parametrize(
    "overall_metrics",
    [
        {},
        {"overall_recall": None},
        {"overall_recall": "not-a-number"},
        {"overall_recall": "NaN"},
        {"overall_recall": "Infinity"},
        {"overall_recall": -0.0001},
        {"overall_recall": 1.0001},
        {"overall_recall": True},
    ],
)
def test_quality_evidence_rejects_missing_or_non_finite_recall(
    client, testadmin_token_headers, overall_metrics
):
    payload = {
        "expected_state": "PT_EVALUATING",
        "evaluation_run_id": 1,
        "evaluation_request_sha256": "a" * 64,
        "overall_metrics": overall_metrics,
        "per_label_metrics": {},
        "evidence_manifest_rel_path": "auto-training/evidence/round.json",
        "evidence_manifest_sha256": "e" * 64,
    }

    response = client.post(
        f"{settings.API_V1_STR}/auto-training-rounds/1/quality-evidence",
        headers=testadmin_token_headers,
        json=payload,
    )

    assert response.status_code == 422


def test_evaluation_dataset_samples_are_frozen_by_the_platform(
    client, db, tmp_path, monkeypatch, testuser_token_headers, testadmin_token_headers
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        first_task, _first_contract, first_samples = _seed_task(db, tmp_path, count=2)
        second_task, _second_contract, second_samples = _seed_task(db, tmp_path, count=2)

    created = client.post(
        f"{settings.API_V1_STR}/training-evaluation-datasets",
        headers=testuser_token_headers,
        json={
            "name": "multi-task-edge-evaluation",
            "dataset_kind": "EDGE_VALIDATION",
            "series_type": "yolo_detection",
            "dataset_category_id": 8,
            "label_set_signature": "a" * 64,
            "grouping_policy": {"identity_isolation": True},
        },
    )
    assert created.status_code == 201, created.text
    dataset_id = created.json()["data"]["id"]

    replaced = client.put(
        f"{settings.API_V1_STR}/training-evaluation-datasets/{dataset_id}/samples",
        headers=testuser_token_headers,
        json={
            "samples": [
                {
                    "task_id": first_task.id,
                    "sample_id": first_samples[0].id,
                    "split_role": "test",
                    "identity_group_key": "vehicle-1",
                    "capture_group_key": "camera-a",
                },
                {
                    "task_id": first_task.id,
                    "sample_id": first_samples[1].id,
                    "split_role": "test",
                    "identity_group_key": "vehicle-2",
                    "capture_group_key": "camera-a",
                },
                {
                    "task_id": second_task.id,
                    "sample_id": second_samples[0].id,
                    "split_role": "test",
                    "identity_group_key": "vehicle-3",
                    "capture_group_key": "camera-b",
                },
                {
                    "task_id": second_task.id,
                    "sample_id": second_samples[1].id,
                    "split_role": "test",
                    "identity_group_key": "vehicle-4",
                    "capture_group_key": "camera-b",
                },
            ]
        },
    )
    assert replaced.status_code == 200, replaced.text
    frozen = replaced.json()["data"]
    assert frozen["sample_count"] == 4
    assert len(frozen["manifest_sha256"]) == 64
    assert (tmp_path / frozen["manifest_rel_path"]).is_file()
    assert db.query(TrainingEvaluationSample).filter_by(
        evaluation_dataset_id=dataset_id
    ).count() == 4

    submitted = client.post(
        f"{settings.API_V1_STR}/training-evaluation-datasets/{dataset_id}/submit",
        headers=testuser_token_headers,
        json={"reason": "frozen samples verified"},
    )
    assert submitted.status_code == 200, submitted.text
    approved = client.post(
        f"{settings.API_V1_STR}/training-evaluation-datasets/{dataset_id}/approve",
        headers=testadmin_token_headers,
        json={"reason": "approved for automatic evaluation"},
    )
    assert approved.status_code == 200, approved.text

    stale_edit = client.put(
        f"{settings.API_V1_STR}/training-evaluation-datasets/{dataset_id}/samples",
        headers=testuser_token_headers,
        json={
            "samples": [
                {
                    "task_id": first_task.id,
                    "sample_id": first_samples[0].id,
                    "split_role": "test",
                }
            ]
        },
    )
    assert stale_edit.status_code == 409


def test_auto_generate_evaluation_datasets_splits_and_reuses_approved_bundle(
    client, db, tmp_path, monkeypatch, testuser_token_headers, testadmin_token_headers
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        plan = _seed_plan(db, name="auto-evaluation-pool")
        task, _contract, samples = _seed_task(db, tmp_path, count=50)
        reconcile_task_for_plan(db, plan=plan, task=task)

    payload = {
        "plan_id": plan.id,
        "series_type": "yolo_detection",
        "dataset_category_id": 8,
        "label_set_signature": "a" * 64,
        "name_prefix": "shared-auto-evaluation",
        "grouping_policy": {
            "identity_isolation": True,
            "capture_isolation": True,
        },
    }
    generated = client.post(
        f"{settings.API_V1_STR}/training-evaluation-datasets/auto-generate",
        headers=testuser_token_headers,
        json=payload,
    )
    assert generated.status_code == 201, generated.text
    result = generated.json()["data"]
    assert result["reused"] is False
    assert result["candidate_count"] == 50
    assert result["isolation_level"] == "attachment"
    assert {row["dataset_kind"] for row in result["datasets"]} == {
        "VALIDATION",
        "BENCHMARK",
        "SEALED_ACCEPTANCE",
        "BM1688_CALIBRATION",
        "EDGE_VALIDATION",
    }
    assert sum(row["sample_count"] for row in result["datasets"]) == 50

    dataset_ids = [int(row["id"]) for row in result["datasets"]]
    frozen_sample_ids = [
        int(sample_id)
        for (sample_id,) in db.query(TrainingEvaluationSample.sample_id)
        .filter(TrainingEvaluationSample.evaluation_dataset_id.in_(dataset_ids))
        .all()
    ]
    assert len(frozen_sample_ids) == 50
    assert len(set(frozen_sample_ids)) == 50
    for row in result["datasets"]:
        assert row["status"] == "APPROVED"
        assert row["approved_by"] == row["owner_id"]
        assert (tmp_path / row["manifest_rel_path"]).is_file()

    reused = client.post(
        f"{settings.API_V1_STR}/training-evaluation-datasets/auto-generate",
        headers=testuser_token_headers,
        json=payload,
    )
    assert reused.status_code == 201, reused.text
    reused_result = reused.json()["data"]
    assert reused_result["reused"] is True
    assert reused_result["bundle_signature"] == result["bundle_signature"]
    assert [row["id"] for row in reused_result["datasets"]] == dataset_ids

    changed_path = tmp_path / samples[0].file.path
    changed_path.write_bytes(b"changed-evaluation-source-image")
    with begin_transaction(db):
        reconcile_task_for_plan(db, plan=plan, task=task)
    regenerated = client.post(
        f"{settings.API_V1_STR}/training-evaluation-datasets/auto-generate",
        headers=testuser_token_headers,
        json=payload,
    )
    assert regenerated.status_code == 201, regenerated.text
    regenerated_result = regenerated.json()["data"]
    assert regenerated_result["reused"] is False
    assert regenerated_result["source_signature"] != result["source_signature"]
    assert regenerated_result["bundle_signature"] != result["bundle_signature"]


def test_auto_evaluation_partition_is_order_independent_and_keeps_linked_groups_together():
    candidates = [
        {
            "task_id": task_id,
            "sample_id": sample_id,
            "file_id": sample_id,
            "sample_signature": f"sample-{sample_id}",
            "annotation_sha256": f"annotation-{sample_id}",
            "image_sha256": f"image-{sample_id}",
            "duplicate_group_id": 17 if sample_id in {1, 2} else 0,
            "capture_group_key": "capture-a" if sample_id in {2, 3} else None,
        }
        for task_id, sample_id in (
            (1, 1),
            (2, 2),
            (3, 3),
            (4, 4),
            (5, 5),
            (6, 6),
            (7, 7),
            (8, 8),
            (9, 9),
        )
    ]
    forward, forward_source, forward_level = (
        auto_training_plan_service._partition_auto_evaluation_candidates(candidates)
    )
    reverse, reverse_source, reverse_level = (
        auto_training_plan_service._partition_auto_evaluation_candidates(
            list(reversed(candidates))
        )
    )
    normalize = lambda partitions: {
        kind: sorted(item["sample_id"] for item in rows)
        for kind, rows in partitions.items()
    }
    assert normalize(forward) == normalize(reverse)
    assert forward_source == reverse_source
    assert forward_level == reverse_level == "source_task"
    assigned = {
        sample_id: kind
        for kind, rows in forward.items()
        for sample_id in (item["sample_id"] for item in rows)
    }
    assert assigned[1] == assigned[2] == assigned[3]


def test_create_plan_auto_creates_and_approves_plan_owned_evaluations(
    client, db, tmp_path, monkeypatch, testuser_token_headers
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    with begin_transaction(db):
        task, _contract, _samples = _seed_task(db, tmp_path, count=50)

    first_payload = _payload("plan-owned-evaluation-1")
    first_payload["dataset_category_id"] = 8
    first_payload["config"]["evaluation_binding"] = {}
    first_payload["config"]["bm1688_policy"]["calibration_dataset_id"] = 0
    first = client.post(BASE, headers=testuser_token_headers, json=first_payload)
    assert first.status_code == 201, first.text
    first_plan = first.json()["data"]
    first_rows = (
        db.query(TrainingEvaluationDataset)
        .filter_by(plan_id=first_plan["id"])
        .all()
    )
    assert len(first_rows) == 5
    assert {row.status for row in first_rows} == {"APPROVED"}
    assert all(row.approved_by == row.owner_id for row in first_rows)
    assert all((tmp_path / row.manifest_rel_path).is_file() for row in first_rows)
    assert sum(row.sample_count for row in first_rows) == 50
    first_kinds = {row.dataset_kind: row.id for row in first_rows}
    first_config = first_plan["current_version"]["config"]
    assert first_config["evaluation_binding"]["validation_dataset_id"] == first_kinds[
        "VALIDATION"
    ]
    assert first_config["bm1688_policy"]["calibration_dataset_id"] == first_kinds[
        "BM1688_CALIBRATION"
    ]

    second_payload = _payload("plan-owned-evaluation-2")
    second_payload["dataset_category_id"] = 8
    second_payload["config"]["evaluation_binding"] = {}
    second_payload["config"]["bm1688_policy"]["calibration_dataset_id"] = 0
    second = client.post(BASE, headers=testuser_token_headers, json=second_payload)
    assert second.status_code == 201, second.text
    second_ids = {
        row.id
        for row in db.query(TrainingEvaluationDataset)
        .filter_by(plan_id=second.json()["data"]["id"])
        .all()
    }
    assert len(second_ids) == 5
    first_ids = set(first_kinds.values())
    assert first_ids.isdisjoint(second_ids)


def test_publish_ready_endpoint_requires_admin_confirmation_and_is_idempotent(
    client, db, testuser_token_headers, testadmin_token_headers, monkeypatch
):
    from types import SimpleNamespace

    calls = []

    async def _start_publish_job(_db, _user, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(job_id="publish-api-1", status="PROCESSING")

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_aicm_publish.start_publish_job",
        _start_publish_job,
    )
    plan_id, round_id, _job_id = _seed_pipeline(db)
    with begin_transaction(db):
        export = materialize_bm1688_export(
            db, round_id=round_id, origin="AUTO_QUALITY", reason="quality reached"
        )
        export.status = "COMPLETED"
        export.bmodel_rel_path = "exports/model.bmodel"
        export.quality_gate_json = '{"passed": true}'
        synchronize_bm1688_export(db, export_id=export.id)
        edge = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=export.id,
            status="COMPLETED",
            mode="run_and_eval",
            result_json='{"all_gates_passed": true}',
            auto_training_plan_id=plan_id,
            auto_training_round_id=round_id,
            execution_signature="p" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(edge)
        db.flush()
        db.query(AutoTrainingRound).filter_by(id=round_id).one().edge_validation_job_id = edge.id
        synchronize_edge_validation_job(db, edge_job_id=edge.id)

    url = f"{settings.API_V1_STR}/auto-training-rounds/{round_id}/publish"
    body = {
        "expected_state": "PUBLISH_READY",
        "confirmation": "PUBLISH",
        "reason": "approved for formal publication",
    }
    assert client.post(url, headers=testuser_token_headers, json=body).status_code == 403
    assert client.post(
        url,
        headers=testadmin_token_headers,
        json={**body, "confirmation": "YES"},
    ).status_code == 422

    published = client.post(url, headers=testadmin_token_headers, json=body)
    assert published.status_code == 202, published.text
    assert published.json()["data"]["action"] == "PUBLISHING"
    assert published.json()["data"]["publish_job_id"] == "publish-api-1"
    assert calls == [
        {
            "export_id": db.query(AutoTrainingRound).filter_by(id=round_id).one().bm1688_export_id,
            "environment_id": None,
                "keep_template_cache": False,
                "bmodel_precision": None,
                "bmodel_rel_path": None,
                "publish_mode": "manual",
            }
        ]

    replay = client.post(url, headers=testadmin_token_headers, json=body)
    assert replay.status_code == 202, replay.text
    assert replay.json()["data"]["changed"] is False
    assert len(calls) == 1


def _seed_evaluation_run(db, *, status="PENDING") -> int:
    _plan_id, round_id, _job_id = _seed_lifecycle(db)
    with begin_transaction(db):
        row = AutoTrainingEvaluationRun(
            round_id=round_id,
            policy_signature="q" * 64,
            evaluation_binding_signature="e" * 64,
            status=status,
            request_manifest_rel_path="auto-training/evaluation/request.json",
            request_manifest_sha256="a" * 64,
        )
        db.add(row)
        db.flush()
        run_id = row.id
    return run_id


def test_evaluation_jobs_api_requires_authentication_and_admin(
    client, testuser_token_headers
):
    base = f"{settings.API_V1_STR}/auto-training-evaluation-jobs"
    assert client.get(base).status_code == 401
    assert client.post(f"{base}/1/claim", json={"worker_id": "worker-a"}).status_code == 401
    assert client.get(base, headers=testuser_token_headers).status_code == 403
    assert client.post(
        f"{base}/1/claim",
        headers=testuser_token_headers,
        json={"worker_id": "worker-a"},
    ).status_code == 403


@pytest.mark.parametrize(
    "query",
    [
        "?statuses=UNKNOWN",
        "?statuses=",
        "?limit=0",
        "?limit=101",
    ],
)
def test_evaluation_jobs_api_rejects_invalid_filters(
    client, testadmin_token_headers, query
):
    response = client.get(
        f"{settings.API_V1_STR}/auto-training-evaluation-jobs{query}",
        headers=testadmin_token_headers,
    )
    assert response.status_code == 422


def test_evaluation_jobs_api_filters_and_claims_with_finite_leases(
    client, db, testadmin_token_headers
):
    run_id = _seed_evaluation_run(db)
    base = f"{settings.API_V1_STR}/auto-training-evaluation-jobs"

    listing = client.get(
        f"{base}?statuses=PENDING&limit=1", headers=testadmin_token_headers
    )
    assert listing.status_code == 200, listing.text
    assert [item["id"] for item in listing.json()["data"]] == [run_id]

    claimed = client.post(
        f"{base}/{run_id}/claim",
        headers=testadmin_token_headers,
        json={"worker_id": " worker-a ", "lease_seconds": 30},
    )
    assert claimed.status_code == 200, claimed.text
    claimed_data = claimed.json()["data"]
    assert claimed_data["status"] == "PROCESSING"
    assert claimed_data["evaluator_worker_id"] == "worker-a"
    first_claimed_at = claimed_data["evaluator_claimed_at"]

    replay = client.post(
        f"{base}/{run_id}/claim",
        headers=testadmin_token_headers,
        json={"worker_id": "worker-a", "lease_seconds": 60},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["evaluator_claimed_at"] == first_claimed_at

    conflict = client.post(
        f"{base}/{run_id}/claim",
        headers=testadmin_token_headers,
        json={"worker_id": "worker-b", "lease_seconds": 60},
    )
    assert conflict.status_code == 409

    with begin_transaction(db):
        row = db.query(AutoTrainingEvaluationRun).filter_by(id=run_id).one()
        row.evaluator_lease_expires_at = datetime.now() - timedelta(seconds=1)

    takeover = client.post(
        f"{base}/{run_id}/claim",
        headers=testadmin_token_headers,
        json={"worker_id": "worker-b", "lease_seconds": 60},
    )
    assert takeover.status_code == 200, takeover.text
    assert takeover.json()["data"]["evaluator_worker_id"] == "worker-b"


@pytest.mark.parametrize(
    "payload",
    [
        {"worker_id": ""},
        {"worker_id": "worker-a", "lease_seconds": 29},
        {"worker_id": "worker-a", "lease_seconds": 3601},
    ],
)
def test_evaluation_job_claim_rejects_invalid_payload(
    client, testadmin_token_headers, payload
):
    response = client.post(
        f"{settings.API_V1_STR}/auto-training-evaluation-jobs/1/claim",
        headers=testadmin_token_headers,
        json=payload,
    )
    assert response.status_code == 422


def test_evaluation_job_claim_handles_missing_and_terminal_runs(
    client, db, testadmin_token_headers
):
    base = f"{settings.API_V1_STR}/auto-training-evaluation-jobs"
    missing = client.post(
        f"{base}/999999/claim",
        headers=testadmin_token_headers,
        json={"worker_id": "worker-a"},
    )
    assert missing.status_code == 404

    run_id = _seed_evaluation_run(db, status="COMPLETED")
    completed = client.post(
        f"{base}/{run_id}/claim",
        headers=testadmin_token_headers,
        json={"worker_id": "worker-a"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["data"]["status"] == "COMPLETED"
    assert completed.json()["data"]["evaluator_worker_id"] is None

    persisted = db.query(AutoTrainingEvaluationRun).filter_by(id=run_id).one()
    assert persisted.status == "COMPLETED"
    assert persisted.evaluator_worker_id is None


def test_phase_one_openapi_exposes_history_reads_and_hides_write_controls(client):
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        f"{settings.API_V1_STR}/auto-training-settings": {"get", "patch"},
        f"{settings.API_V1_STR}/auto-training-plans/preview": {"post"},
        f"{settings.API_V1_STR}/auto-training-plans": {"get", "post"},
        f"{settings.API_V1_STR}/auto-training-plans/summary": {"get"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}": {"get", "patch"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/submit": {"post"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/approve": {"post"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/pause": {"post"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/resume": {"post"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/archive": {"post"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/versions": {"get"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/audit-events": {"get"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/scan-preview": {"post"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/scan-preview/{{scan_job_id}}": {"get"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/task-scopes": {"get"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/sample-pool": {"get"},
        f"{settings.API_V1_STR}/samples/{{sample_id}}/training-review": {"post"},
        f"{settings.API_V1_STR}/training-reviews/{{review_id}}/invalidate": {"post"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/reconcile": {"post"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/rounds": {"get", "post"},
        f"{settings.API_V1_STR}/auto-training-plans/{{plan_id}}/rounds/preview": {"post"},
        f"{settings.API_V1_STR}/auto-training-rounds/{{round_id}}": {"get"},
        f"{settings.API_V1_STR}/auto-training-rounds/{{round_id}}/samples": {"get"},
        f"{settings.API_V1_STR}/auto-training-rounds/{{round_id}}/quality": {"get"},
        f"{settings.API_V1_STR}/auto-training-rounds/{{round_id}}/cancel": {"post"},
        f"{settings.API_V1_STR}/auto-training-rounds/{{round_id}}/retry-stage": {"post"},
        f"{settings.API_V1_STR}/auto-training-rounds/{{round_id}}/promote-to-bm1688": {"post"},
        f"{settings.API_V1_STR}/auto-training-rounds/{{round_id}}/start-edge-validation": {"post"},
        f"{settings.API_V1_STR}/auto-training-rounds/{{round_id}}/quality-evidence": {"post"},
        f"{settings.API_V1_STR}/auto-training-rounds/{{round_id}}/publish": {"post"},
        f"{settings.API_V1_STR}/training-evaluation-datasets": {"get", "post"},
        f"{settings.API_V1_STR}/training-evaluation-datasets/auto-generate": {"post"},
        f"{settings.API_V1_STR}/training-evaluation-datasets/{{dataset_id}}": {"patch"},
        f"{settings.API_V1_STR}/training-evaluation-datasets/{{dataset_id}}/submit": {"post"},
        f"{settings.API_V1_STR}/training-evaluation-datasets/{{dataset_id}}/approve": {"post"},
        f"{settings.API_V1_STR}/training-evaluation-datasets/{{dataset_id}}/samples": {"get", "put"},
        f"{settings.API_V1_STR}/yolo-quality-policies/{{policy_name}}/submit-approval": {"post"},
        f"{settings.API_V1_STR}/yolo-quality-policies/{{policy_name}}/approve": {"post"},
        f"{settings.API_V1_STR}/quality-policy-approvals/submit-current": {"post"},
        f"{settings.API_V1_STR}/quality-policy-approvals": {"get"},
        f"{settings.API_V1_STR}/auto-training-evaluation-jobs": {"get"},
        f"{settings.API_V1_STR}/auto-training-evaluation-jobs/{{evaluation_run_id}}/claim": {"post"},
    }

    for path, methods in expected.items():
        allowed_reads = methods & {"get", "head", "options"}
        exposed_methods = set(paths.get(path, {}))
        if allowed_reads:
            assert path in paths, path
            assert allowed_reads <= exposed_methods, path
        assert not ((methods - allowed_reads) & exposed_methods), path
