from __future__ import annotations

import asyncio

import pytest

from labelu.internal.application.service import auto_training_plan
from labelu.internal.application.service import auto_training_pipeline_runtime
from labelu.internal.application.service import auto_training_reconciliation
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import LabelUException
from labelu.internal.application.command.auto_training import (
    CreateAutoTrainingPlanCommand,
    PreviewAutoTrainingPlanCommand,
)


WRITE_SERVICE_CALLS = {
    "preview plan": lambda: auto_training_plan.preview_plan({}),
    "create plan": lambda: auto_training_plan.create_plan(
        object(), user=object(), data={}, change_reason=None
    ),
    "patch plan": lambda: auto_training_plan.patch_plan(
        object(), user=object(), plan_id=1, expected_version=1, name=None,
        series_type=None, dataset_category_id=None, label_set_signature=None,
        class_order_signature=None, config={}, reason=None,
    ),
    "transition plan": lambda: auto_training_plan.transition_plan(
        object(), user=object(), plan_id=1, expected_version=1,
        target=None, reason="blocked",
    ),
    "archive plan": lambda: auto_training_plan.archive_plan(
        object(), user=object(), plan_id=1, expected_version=1, reason="blocked"
    ),
    "scan preview": lambda: auto_training_plan.create_scan_preview(
        object(), user=object(), plan_id=1
    ),
    "reconcile": lambda: auto_training_plan.request_reconcile(
        object(), user=object(), plan_id=1
    ),
    "create review": lambda: auto_training_plan.create_review(
        object(), user=object(), sample_id=1, annotation_sha256="a" * 64,
        review_status="APPROVED", comment=None,
    ),
    "invalidate review": lambda: auto_training_plan.invalidate_review(
        object(), user=object(), review_id=1, reason="blocked"
    ),
    "reserve active round": lambda: auto_training_plan.reserve_active_round(
        object(), plan_id=1, round_id=1, expected_plan_version=1
    ),
    "preview round": lambda: auto_training_plan.preview_round(
        object(), user=object(), plan_id=1
    ),
    "create round": lambda: auto_training_plan.create_round(
        object(), user=object(), plan_id=1, reason="blocked"
    ),
    "cancel round": lambda: auto_training_plan.cancel_round(
        object(), user=object(), round_id=1, reason="blocked"
    ),
    "retry stage": lambda: auto_training_plan.retry_stage(
        object(), user=object(), round_id=1, stage="TRAIN", reason="blocked",
        expected_state="TRAIN_FAILED",
    ),
    "promote bmodel": lambda: auto_training_plan.promote_to_bm1688(
        object(), user=object(), round_id=1, reason="blocked"
    ),
    "start edge validation": lambda: auto_training_plan.start_edge_validation(
        object(), user=object(), round_id=1, reason="blocked"
    ),
    "submit evidence": lambda: auto_training_plan.submit_quality_evidence(
        object(), user=object(), round_id=1, expected_state="EVALUATING",
        evaluation_run_id=1, evaluation_request_sha256="a" * 64,
        overall_metrics={}, per_label_metrics={}, plate_component_metrics={},
        champion_comparison={}, evidence_manifest_rel_path="evidence.json",
        evidence_manifest_sha256="b" * 64,
    ),
    "publish round": lambda: asyncio.run(auto_training_plan.publish_round(
        object(), user=object(), round_id=1, expected_state="PUBLISH_READY",
        reason="blocked",
    )),
    "generate evaluation data": lambda: auto_training_plan.auto_generate_evaluation_datasets(
        object(), user=object(), data={}
    ),
    "create evaluation data": lambda: auto_training_plan.create_evaluation_dataset(
        object(), user=object(), data={}
    ),
    "patch evaluation data": lambda: auto_training_plan.patch_evaluation_dataset(
        object(), user=object(), dataset_id=1, data={}
    ),
    "transition evaluation data": lambda: auto_training_plan.transition_evaluation_dataset(
        object(), user=object(), dataset_id=1, target="APPROVED", reason="blocked"
    ),
    "replace evaluation samples": lambda: auto_training_plan.replace_evaluation_samples(
        object(), user=object(), dataset_id=1, samples=[]
    ),
    "submit quality policy": lambda: auto_training_plan.submit_quality_policy(
        object(), user=object(), policy_name="default", data={}
    ),
    "approve quality policy": lambda: auto_training_plan.approve_quality_policy(
        object(), user=object(), policy_name="default", reason="blocked"
    ),
    "submit current quality policy": lambda: auto_training_plan.submit_current_quality_policy(
        object(), user=object(), policy_name="default", reason="blocked"
    ),
    "update settings": lambda: auto_training_plan.update_runtime_settings(
        user=object(), values={}, reason="blocked"
    ),
}


def test_workflow_plan_creation_bypass_is_blocked_before_database_access(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)

    with pytest.raises(LabelUException) as exc_info:
        auto_training_plan.create_plan_from_frozen_dataset_version(
            object(),
            user=object(),
            dataset_version_id=1,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == 57122


@pytest.mark.parametrize("command_type", [CreateAutoTrainingPlanCommand, PreviewAutoTrainingPlanCommand])
def test_new_auto_training_plan_commands_reject_project_scope(command_type):
    with pytest.raises(ValueError, match="不支持项目维度"):
        command_type(
            project_id=7,
            name="一期计划",
            series_type="yolo_detection",
            dataset_category_id=1,
            label_set_signature="a" * 64,
            class_order_signature="b" * 64,
            config={},
        )


@pytest.mark.parametrize(
    "invoke",
    WRITE_SERVICE_CALLS.values(),
    ids=WRITE_SERVICE_CALLS.keys(),
)
def test_every_public_write_service_is_blocked_before_database_access(
    monkeypatch,
    invoke,
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)

    with pytest.raises(LabelUException) as exc_info:
        invoke()

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == 57122


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: auto_training_reconciliation.reconcile_task_for_plan(
            object(), plan=object(), task=object()
        ),
        lambda: auto_training_reconciliation.discover_training_ready_plans(object()),
        lambda: auto_training_reconciliation.reconcile_plan_owned_evaluation_datasets(
            object()
        ),
    ],
    ids=["sample pool reconciliation", "plan discovery", "evaluation governance"],
)
def test_reconciliation_write_services_are_blocked_before_database_access(
    monkeypatch,
    invoke,
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)

    with pytest.raises(LabelUException) as exc_info:
        invoke()

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == 57122


@pytest.mark.parametrize(
    "invoke",
    [
        lambda: auto_training_pipeline_runtime.materialize_bm1688_export(
            object(), round_id=1, origin="AUTO_QUALITY", reason=None
        ),
        lambda: auto_training_pipeline_runtime.synchronize_bm1688_export(
            object(), export_id=1
        ),
        lambda: auto_training_pipeline_runtime.materialize_edge_validation_job(
            object(), round_id=1, export_id=1, origin="AUTO_QUALITY"
        ),
        lambda: auto_training_pipeline_runtime.synchronize_edge_validation_job(
            object(), edge_job_id=1
        ),
        lambda: auto_training_pipeline_runtime.register_aicm_publish_job(
            object(), round_id=1, actor_id=1, actor_is_admin=True,
            reason="blocked", publish_job_id="job-1",
        ),
        lambda: auto_training_pipeline_runtime.mark_round_published(
            object(), round_id=1, actor_id=1, actor_is_admin=True,
            reason="blocked",
        ),
        lambda: auto_training_pipeline_runtime.synchronize_aicm_publish_job(
            object(), round_id=1, job_payload={}
        ),
    ],
    ids=[
        "materialize bmodel", "synchronize bmodel", "materialize edge",
        "synchronize edge", "register publish", "mark published",
        "synchronize publish",
    ],
)
def test_pipeline_write_services_are_blocked_before_database_access(
    monkeypatch,
    invoke,
):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", False)

    with pytest.raises(LabelUException) as exc_info:
        invoke()

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == 57122
