from __future__ import annotations

import json

import pytest

from labelu.internal.application.service.auto_training_pipeline import (
    PipelineConflict,
    PipelineOrigin,
    advance_after_bmodel,
    advance_after_edge_validation,
    create_bm1688_request,
)
from labelu.internal.application.service.auto_training_state_machine import RoundStatus
from labelu.internal.application.service.auto_training_pipeline_runtime import (
    materialize_bm1688_export,
    materialize_edge_validation_job,
    mark_round_published,
    register_aicm_publish_job,
    synchronize_aicm_publish_job,
    synchronize_bm1688_export,
    synchronize_edge_validation_job,
)
from labelu.internal.application.service.auto_training_plan import (
    replace_evaluation_samples,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.auto_training import (
    AutoTrainingOutbox,
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
)
from labelu.internal.domain.models.auto_training_governance import (
    TrainingEvaluationDataset,
)
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.tests.internal.application.service.test_auto_training_reconciliation import (
    _seed_task,
)


@pytest.fixture(autouse=True)
def _enable_auto_training_plan_writes_for_legacy_contract_tests(monkeypatch):
    monkeypatch.setattr(settings, "AUTO_TRAINING_PLAN_WRITES_ENABLED", True)


def test_quality_reached_creates_one_automatic_bm1688_request():
    created = []

    def factory(**payload):
        created.append(payload)
        return {"id": 7, **payload}

    first = create_bm1688_request(
        round_id=3,
        round_status=RoundStatus.AUTO_QUALITY_REACHED,
        actor_is_admin=False,
        reason=None,
        existing_execution_signatures=set(),
        factory=factory,
    )
    second = create_bm1688_request(
        round_id=3,
        round_status=RoundStatus.AUTO_QUALITY_REACHED,
        actor_is_admin=False,
        reason=None,
        existing_execution_signatures={"round:3:bm1688"},
        factory=factory,
    )

    assert first["origin"] == PipelineOrigin.AUTO_QUALITY.value
    assert second is None
    assert len(created) == 1


def test_below_threshold_manual_compile_is_admin_only_and_requires_reason():
    with pytest.raises(PipelineConflict):
        create_bm1688_request(
            round_id=4,
            round_status=RoundStatus.QUALITY_FAILED,
            actor_is_admin=False,
            reason="debug",
            existing_execution_signatures=set(),
            factory=lambda **payload: payload,
        )
    with pytest.raises(PipelineConflict):
        create_bm1688_request(
            round_id=4,
            round_status=RoundStatus.QUALITY_FAILED,
            actor_is_admin=True,
            reason=" ",
            existing_execution_signatures=set(),
            factory=lambda **payload: payload,
        )

    result = create_bm1688_request(
        round_id=4,
        round_status=RoundStatus.QUALITY_FAILED,
        actor_is_admin=True,
        reason="检查量化兼容性",
        existing_execution_signatures=set(),
        factory=lambda **payload: payload,
    )

    assert result["origin"] == PipelineOrigin.MANUAL_DIAGNOSTIC.value


def test_diagnostic_success_can_run_edge_but_never_publish_ready():
    status = advance_after_bmodel(
        origin=PipelineOrigin.MANUAL_DIAGNOSTIC,
        compile_succeeded=True,
        quality_gate_passed=True,
    )
    assert status == RoundStatus.MANUAL_EDGE_VALIDATING

    completed = advance_after_edge_validation(
        origin=PipelineOrigin.MANUAL_DIAGNOSTIC,
        status="COMPLETED",
        all_gates_passed=True,
    )
    assert completed == RoundStatus.DIAGNOSTIC_COMPLETED


@pytest.mark.parametrize("edge_status", ["FAILED", "PARTIAL_COMPLETED"])
def test_partial_or_failed_edge_result_blocks_publish(edge_status):
    result = advance_after_edge_validation(
        origin=PipelineOrigin.AUTO_QUALITY,
        status=edge_status,
        all_gates_passed=True,
    )

    assert result == RoundStatus.EDGE_VALIDATION_FAILED


def test_quality_path_full_edge_success_becomes_publish_ready():
    assert (
        advance_after_edge_validation(
            origin=PipelineOrigin.AUTO_QUALITY,
            status="COMPLETED",
            all_gates_passed=True,
        )
        == RoundStatus.PUBLISH_READY
    )


def _seed_pipeline(db, *, state="AUTO_QUALITY_REACHED"):
    with begin_transaction(db):
        plan = AutoTrainingPlan(
            owner_id=1,
            name=f"pipeline-{state.lower()}",
            series_type="yolo_detection",
            dataset_category_id=1,
            label_set_signature="a" * 64,
            class_order_signature="b" * 64,
            status=("AUTO_QUALITY_REACHED" if state == "AUTO_QUALITY_REACHED" else "WAITING_FOR_DATA"),
            version=1,
            created_by=1,
            updated_by=1,
        )
        db.add(plan)
        db.flush()
        version = AutoTrainingPlanVersion(
            plan_id=plan.id,
            version_no=1,
            status="APPROVED",
            config_json=json.dumps(
                {
                    "bm1688_policy": {"technical_retry_limit": 2},
                    "edge_validation_policy": {"technical_retry_limit": 2},
                }
            ),
            config_sha256="c" * 64,
            missing_fields_json="[]",
            validation_errors_json="[]",
            created_by=1,
        )
        db.add(version)
        db.flush()
        plan.current_version_id = version.id
        round_row = AutoTrainingRound(
            plan_id=plan.id,
            plan_version_id=version.id,
            round_no=1,
            state=state,
            trigger_type="AUTO",
            trigger_snapshot_json="{}",
            dataset_signature="d" * 64,
            execution_signature="e" * 64,
            stage_status_json="{}",
            stage_errors_json="{}",
            retry_count_json="{}",
        )
        db.add(round_row)
        db.flush()
        if state == "AUTO_QUALITY_REACHED":
            plan.active_round_id = round_row.id
        job = YoloTrainJob(
            task_id=1,
            created_by=1,
            status="COMPLETED",
            weights_path="runs/auto/best.pt",
            imgsz=640,
            source_type="auto_training",
            auto_training_plan_id=plan.id,
            auto_training_round_id=round_row.id,
            execution_signature="f" * 64,
        )
        db.add(job)
        db.flush()
        round_row.yolo_train_job_id = job.id
        return plan.id, round_row.id, job.id


def test_persistent_quality_export_is_unique_and_queues_execution(db):
    plan_id, round_id, job_id = _seed_pipeline(db)

    with begin_transaction(db):
        first = materialize_bm1688_export(
            db, round_id=round_id, origin="AUTO_QUALITY", reason="quality reached"
        )
        second = materialize_bm1688_export(
            db, round_id=round_id, origin="AUTO_QUALITY", reason="duplicate"
        )

    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    assert first.id == second.id
    assert first.yolo_train_job_id == job_id
    assert first.auto_training_plan_id == plan_id
    assert first.pipeline_origin == "AUTO_QUALITY"
    assert round_row.bm1688_export_id == first.id
    assert round_row.state == "BM1688_QUEUED"
    assert db.query(AutoTrainingOutbox).filter_by(
        event_name="auto.round.bm1688.execute.v1"
    ).count() == 1


def test_completed_bmodel_waits_for_gate_then_queues_edge(db):
    _plan_id, round_id, _job_id = _seed_pipeline(db)
    with begin_transaction(db):
        export = materialize_bm1688_export(
            db, round_id=round_id, origin="AUTO_QUALITY", reason="quality reached"
        )
        export.status = "COMPLETED"
        export.bmodel_rel_path = "exports/model.bmodel"
        export.quality_gate_json = json.dumps({"passed": True})
        export_id = export.id

    with begin_transaction(db):
        result = synchronize_bm1688_export(db, export_id=export_id)

    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    assert result.action == "EDGE_VALIDATION_QUEUED"
    assert round_row.state == "EDGE_VALIDATION_QUEUED"
    assert db.query(AutoTrainingOutbox).filter_by(
        event_name="auto.round.edge.requested.v1"
    ).count() == 1


def test_completed_bmodel_derives_integrity_gate_from_quality_report(db):
    _plan_id, round_id, _job_id = _seed_pipeline(db)
    with begin_transaction(db):
        export = materialize_bm1688_export(
            db, round_id=round_id, origin="AUTO_QUALITY", reason="quality reached"
        )
        export.status = "COMPLETED"
        export.bmodel_rel_path = "models/BM1688/model_int8.bmodel"
        export.quality_report = json.dumps(
            {
                "compile_deploy": {"exit_code": 0},
                "sophon_layout": {"passed": True},
                "artifacts": {
                    "bmodels": [
                        {"path": "models/BM1688/model_int8.bmodel", "bytes": 1024}
                    ]
                },
            }
        )
        export_id = export.id

    with begin_transaction(db):
        result = synchronize_bm1688_export(db, export_id=export_id)

    refreshed = db.query(YoloBm1688Export).filter_by(id=export_id).one()
    gate = json.loads(refreshed.quality_gate_json)
    assert result.action == "EDGE_VALIDATION_QUEUED"
    assert gate["passed"] is True
    assert gate["scope"] == "compile_artifact_integrity"


def test_completed_bmodel_fails_closed_when_quality_report_layout_fails(db):
    _plan_id, round_id, _job_id = _seed_pipeline(db)
    with begin_transaction(db):
        export = materialize_bm1688_export(
            db, round_id=round_id, origin="AUTO_QUALITY", reason="quality reached"
        )
        export.status = "COMPLETED"
        export.bmodel_rel_path = "models/BM1688/model_int8.bmodel"
        export.quality_report = json.dumps(
            {
                "compile_deploy": {"exit_code": 0},
                "sophon_layout": {"passed": False},
                "artifacts": {"bmodels": [{"path": "model.bmodel", "bytes": 1024}]},
            }
        )
        export_id = export.id

    with begin_transaction(db):
        result = synchronize_bm1688_export(db, export_id=export_id)

    assert result.action == "BMODEL_QUALITY_FAILED"
    assert db.query(AutoTrainingRound).filter_by(id=round_id).one().state == "BMODEL_QUALITY_FAILED"


def _seed_multitask_edge_materialization(db, tmp_path):
    plan_id, round_id, _job_id = _seed_pipeline(db)
    with begin_transaction(db):
        user = db.query(User).filter_by(id=1).one()
        plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
        plan.dataset_category_id = 8
        first_task, _first_contract, first_samples = _seed_task(
            db, tmp_path, count=1
        )
        second_task, _second_contract, second_samples = _seed_task(
            db, tmp_path, count=1
        )
        dataset = TrainingEvaluationDataset(
            owner_id=user.id,
            name="multi-task-edge-dataset",
            dataset_kind="EDGE_VALIDATION",
            series_type="yolo_detection",
            dataset_category_id=8,
            label_set_signature="a" * 64,
            version_no=1,
            status="DRAFT",
            manifest_rel_path="draft/edge.json",
            manifest_sha256="0" * 64,
            sample_count=0,
            grouping_policy_json="{}",
            created_by=user.id,
        )
        db.add(dataset)
        db.flush()
        replace_evaluation_samples(
            db,
            user=user,
            dataset_id=dataset.id,
            samples=[
                {
                    "task_id": first_task.id,
                    "sample_id": first_samples[0].id,
                    "split_role": "test",
                },
                {
                    "task_id": second_task.id,
                    "sample_id": second_samples[0].id,
                    "split_role": "test",
                },
            ],
        )
        dataset.status = "APPROVED"
        dataset.approved_by = user.id
        version = db.query(AutoTrainingPlanVersion).filter_by(
            id=plan.current_version_id
        ).one()
        config = json.loads(version.config_json)
        config["evaluation_binding"] = {
            "edge_validation_dataset_id": dataset.id,
        }
        config["edge_validation_policy"] = {
            "mode": "run_and_eval",
            "minimum_valid_inferences": 2,
            "max_images": 10,
            "thresholds": {"max_error_rate": 0.01},
            "technical_retry_limit": 2,
        }
        version.config_json = json.dumps(config)
        export = materialize_bm1688_export(
            db, round_id=round_id, origin="AUTO_QUALITY", reason="quality reached"
        )
        export.status = "COMPLETED"
        export.bmodel_rel_path = "exports/model.bmodel"
        export.quality_gate_json = json.dumps({"passed": True})
        result = {
            "plan_id": plan_id,
            "round_id": round_id,
            "export_id": export.id,
            "dataset_id": dataset.id,
            "first_task_id": first_task.id,
            "second_task_id": second_task.id,
            "first_sample_id": first_samples[0].id,
            "second_sample_id": second_samples[0].id,
            "first_image_path": tmp_path / first_samples[0].file.path,
        }
    return result


def test_edge_job_materialization_preserves_multi_task_frozen_lineage(
    db, tmp_path, monkeypatch
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    seeded = _seed_multitask_edge_materialization(db, tmp_path)

    with begin_transaction(db):
        edge = materialize_edge_validation_job(
            db,
            round_id=seeded["round_id"],
            export_id=seeded["export_id"],
            origin="AUTO_QUALITY",
        )
        edge_id = edge.id

    persisted = db.query(YoloEdgeValidationJob).filter_by(id=edge_id).one()
    request = json.loads(persisted.result_json)["request"]
    assert request["sample_task_ids"] == {
        str(seeded["first_sample_id"]): seeded["first_task_id"],
        str(seeded["second_sample_id"]): seeded["second_task_id"],
    }
    assert request["task_sample_groups"] == {
        str(seeded["first_task_id"]): [seeded["first_sample_id"]],
        str(seeded["second_task_id"]): [seeded["second_sample_id"]],
    }
    dataset = db.query(TrainingEvaluationDataset).filter_by(
        id=seeded["dataset_id"]
    ).one()
    assert request["evaluation_dataset_id"] == dataset.id
    assert request["evaluation_manifest_sha256"] == dataset.manifest_sha256
    assert persisted.task_id == min(
        seeded["first_task_id"], seeded["second_task_id"]
    )


@pytest.mark.parametrize("mutation", ["task_membership", "image_hash"])
def test_edge_job_materialization_fails_closed_when_frozen_lineage_changes(
    db, tmp_path, monkeypatch, mutation
):
    monkeypatch.setattr(settings, "MEDIA_ROOT", str(tmp_path))
    seeded = _seed_multitask_edge_materialization(db, tmp_path)
    if mutation == "task_membership":
        with begin_transaction(db):
            db.query(TaskSample).filter_by(id=seeded["first_sample_id"]).one().task_id = (
                seeded["second_task_id"]
            )
        expected = "task membership changed"
    else:
        seeded["first_image_path"].write_bytes(b"tampered-image")
        expected = "image hash changed"

    with pytest.raises(ValueError, match=expected):
        with begin_transaction(db):
            materialize_edge_validation_job(
                db,
                round_id=seeded["round_id"],
                export_id=seeded["export_id"],
                origin="AUTO_QUALITY",
            )


def test_diagnostic_edge_success_never_becomes_publish_ready(db):
    plan_id, round_id, _job_id = _seed_pipeline(db, state="QUALITY_FAILED")
    with begin_transaction(db):
        export = materialize_bm1688_export(
            db,
            round_id=round_id,
            origin="MANUAL_DIAGNOSTIC",
            reason="check compatibility",
        )
        export.status = "COMPLETED"
        export.bmodel_rel_path = "exports/diagnostic.bmodel"
        export.quality_gate_json = json.dumps({"passed": True})
        synchronize_bm1688_export(db, export_id=export.id)
        edge = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=export.id,
            status="COMPLETED",
            mode="run_and_eval",
            result_json=json.dumps({"all_gates_passed": True}),
            auto_training_plan_id=plan_id,
            auto_training_round_id=round_id,
            execution_signature="g" * 64,
            pipeline_origin="MANUAL_DIAGNOSTIC",
        )
        db.add(edge)
        db.flush()
        db.query(AutoTrainingRound).filter_by(id=round_id).one().edge_validation_job_id = edge.id
        edge_id = edge.id

    with begin_transaction(db):
        result = synchronize_edge_validation_job(db, edge_job_id=edge_id)

    assert result.action == "DIAGNOSTIC_COMPLETED"
    assert db.query(AutoTrainingRound).filter_by(id=round_id).one().state == "DIAGNOSTIC_COMPLETED"
    with pytest.raises(ValueError, match="AICM publish job succeeds"):
        with begin_transaction(db):
            mark_round_published(
                db,
                round_id=round_id,
                actor_id=1,
                actor_is_admin=True,
                reason="must remain diagnostic",
            )


def test_publish_ready_requires_real_aicm_success_before_champion_changes(db):
    plan_id, round_id, job_id = _seed_pipeline(db)
    with begin_transaction(db):
        export = materialize_bm1688_export(
            db, round_id=round_id, origin="AUTO_QUALITY", reason="quality reached"
        )
        export.status = "COMPLETED"
        export.bmodel_rel_path = "exports/model.bmodel"
        export.quality_gate_json = json.dumps({"passed": True})
        synchronize_bm1688_export(db, export_id=export.id)
        edge = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=export.id,
            status="COMPLETED",
            mode="run_and_eval",
            result_json=json.dumps({"all_gates_passed": True}),
            auto_training_plan_id=plan_id,
            auto_training_round_id=round_id,
            execution_signature="h" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(edge)
        db.flush()
        db.query(AutoTrainingRound).filter_by(id=round_id).one().edge_validation_job_id = edge.id
        synchronize_edge_validation_job(db, edge_job_id=edge.id)

    with pytest.raises(PermissionError):
        with begin_transaction(db):
            mark_round_published(
                db,
                round_id=round_id,
                actor_id=1,
                actor_is_admin=False,
                reason="publish",
            )

    with begin_transaction(db):
        requested = register_aicm_publish_job(
            db,
            round_id=round_id,
            actor_id=1,
            actor_is_admin=True,
            reason="approved for production",
            publish_job_id="publish-job-1",
        )

    plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    assert requested.action == "PUBLISHING"
    assert round_row.state == "PUBLISHING"
    assert round_row.publish_job_id == "publish-job-1"
    assert plan.champion_job_id is None
    assert plan.champion_export_id is None

    with begin_transaction(db):
        waiting = synchronize_aicm_publish_job(
            db,
            round_id=round_id,
            job_payload={"job_id": "publish-job-1", "status": "PROCESSING"},
        )
    assert waiting.changed is False
    assert db.query(AutoTrainingRound).filter_by(id=round_id).one().state == "PUBLISHING"

    with begin_transaction(db):
        completed = synchronize_aicm_publish_job(
            db,
            round_id=round_id,
            job_payload={
                "job_id": "publish-job-1",
                "status": "COMPLETED",
                "result": {"ok": True, "model_set_id": 4321},
            },
        )

    plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    assert completed.action == "PUBLISHED"
    assert round_row.state == "PUBLISHED"
    assert round_row.published_model_id == 4321
    assert plan.champion_job_id == job_id
    assert plan.champion_export_id is not None
    assert plan.status == "AUTO_QUALITY_REACHED"
    assert plan.active_round_id is None


def test_failed_aicm_publish_never_replaces_champion_and_can_be_retried(db):
    plan_id, round_id, _job_id = _seed_pipeline(db)
    with begin_transaction(db):
        export = materialize_bm1688_export(
            db, round_id=round_id, origin="AUTO_QUALITY", reason="quality reached"
        )
        export.status = "COMPLETED"
        export.bmodel_rel_path = "exports/model.bmodel"
        export.quality_gate_json = json.dumps({"passed": True})
        synchronize_bm1688_export(db, export_id=export.id)
        edge = YoloEdgeValidationJob(
            user_id=1,
            task_id=1,
            export_id=export.id,
            status="COMPLETED",
            mode="run_and_eval",
            result_json=json.dumps({"all_gates_passed": True}),
            auto_training_plan_id=plan_id,
            auto_training_round_id=round_id,
            execution_signature="i" * 64,
            pipeline_origin="AUTO_QUALITY",
        )
        db.add(edge)
        db.flush()
        db.query(AutoTrainingRound).filter_by(id=round_id).one().edge_validation_job_id = edge.id
        synchronize_edge_validation_job(db, edge_job_id=edge.id)
        register_aicm_publish_job(
            db,
            round_id=round_id,
            actor_id=1,
            actor_is_admin=True,
            reason="first publish",
            publish_job_id="publish-failed-1",
        )

    with begin_transaction(db):
        failed = synchronize_aicm_publish_job(
            db,
            round_id=round_id,
            job_payload={
                "job_id": "publish-failed-1",
                "status": "FAILED",
                "error_message": "remote rejected package",
            },
        )

    plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one()
    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    assert failed.action == "PUBLISH_FAILED"
    assert round_row.state == "PUBLISH_FAILED"
    assert "remote rejected package" in round_row.publish_error
    assert plan.champion_job_id is None
    assert plan.champion_export_id is None
    assert plan.active_round_id == round_id

    with begin_transaction(db):
        retried = register_aicm_publish_job(
            db,
            round_id=round_id,
            actor_id=1,
            actor_is_admin=True,
            reason="retry after config repair",
            publish_job_id="publish-retry-2",
        )
    assert retried.action == "PUBLISHING"
    refreshed = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    assert refreshed.publish_job_id == "publish-retry-2"
    assert refreshed.publish_error is None
