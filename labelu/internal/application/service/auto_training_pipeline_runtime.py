from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.application.service.auto_training_events import (
    enqueue_auto_training_event,
)
from labelu.internal.application.service.auto_training_lifecycle import (
    LifecycleResult,
    _dumps,
    _loads,
    _stage_update,
)
from labelu.internal.common.config import settings
from labelu.internal.common.label_contract import annotation_sha256
from labelu.internal.domain.models.auto_training import (
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
)
from labelu.internal.domain.models.auto_training_governance import (
    TrainingEvaluationDataset,
    TrainingEvaluationSample,
)
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_edge_validation_job import YoloEdgeValidationJob
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.common.project_scope import ProjectScopeError, assert_project_chain, task_project_id


def _ensure_phase_one_writes_open() -> None:
    from labelu.internal.application.service.auto_training_plan import (
        ensure_auto_training_plan_writes_open,
    )

    ensure_auto_training_plan_writes_open()


def _execution_signature(round_id: int, stage: str, origin: str) -> str:
    raw = f"auto-training:{round_id}:{stage}:{origin}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_evaluation_samples(
    db: Session, *, rows: list[TrainingEvaluationSample]
) -> dict[int, int]:
    sample_ids = [int(row.sample_id) for row in rows]
    samples = {
        int(sample.id): sample
        for sample in db.query(TaskSample).filter(TaskSample.id.in_(sample_ids)).all()
    }
    task_by_sample: dict[int, int] = {}
    for row in rows:
        sample_id = int(row.sample_id)
        sample = samples.get(sample_id)
        if sample is None or sample.deleted_at is not None:
            raise ValueError(f"approved evaluation sample #{sample_id} no longer exists")
        if int(sample.task_id) != int(row.task_id):
            raise ValueError(f"approved evaluation sample #{sample_id} task membership changed")
        if str(sample.state or "") != SampleState.DONE.value:
            raise ValueError(f"approved evaluation sample #{sample_id} is no longer completed")
        try:
            annotation = json.loads(sample.data or "{}")
        except (TypeError, ValueError):
            annotation = {}
        if not isinstance(annotation, dict) or annotation_sha256(annotation) != row.annotation_sha256:
            raise ValueError(f"approved evaluation sample #{sample_id} annotation hash changed")
        attachment = getattr(sample, "file", None)
        from labelu.internal.application.service.attachment import (
            resolve_attachment_local_path,
        )

        absolute = resolve_attachment_local_path(attachment)
        if absolute is None:
            raise ValueError(f"approved evaluation sample #{sample_id} image is not locally readable")
        if _file_sha256(absolute) != row.image_sha256:
            raise ValueError(f"approved evaluation sample #{sample_id} image hash changed")
        task_by_sample[sample_id] = int(row.task_id)
    return task_by_sample


def _pipeline_retry_limit(version: AutoTrainingPlanVersion, stage: str) -> int:
    config = _loads(version.config_json, {})
    section = "bm1688_policy" if stage == "BM1688" else "edge_validation_policy"
    try:
        return max(1, int(config.get(section, {}).get("technical_retry_limit", 3)))
    except (TypeError, ValueError):
        return 3


def _derive_compile_artifact_gate(export: YoloBm1688Export) -> dict[str, Any]:
    existing = _loads(export.quality_gate_json, {})
    if existing.get("status") not in {None, "PENDING"} and isinstance(
        existing.get("passed"), bool
    ):
        return existing
    report = _loads(export.quality_report, {})
    if not report:
        return existing
    compile_deploy = report.get("compile_deploy")
    layout = report.get("sophon_layout")
    artifacts = report.get("artifacts")
    compile_exit_code = (
        compile_deploy.get("exit_code") if isinstance(compile_deploy, dict) else None
    )
    layout_passed = layout.get("passed") if isinstance(layout, dict) else None
    has_bmodel = bool(str(export.bmodel_rel_path or "").strip())
    if isinstance(artifacts, dict):
        bmodels = artifacts.get("bmodels")
        platforms = artifacts.get("platforms")
        reported_bmodel = bool(isinstance(bmodels, list) and bmodels)
        if isinstance(platforms, list):
            reported_bmodel = reported_bmodel or any(
                isinstance(platform, dict) and bool(platform.get("files"))
                for platform in platforms
            )
        has_bmodel = has_bmodel and reported_bmodel
    blocking_reasons = []
    if compile_exit_code not in (None, 0):
        blocking_reasons.append("BM1688_COMPILE_EXIT_NONZERO")
    if layout_passed is False:
        blocking_reasons.append("BM1688_LAYOUT_INVALID")
    if not has_bmodel:
        blocking_reasons.append("BM1688_ARTIFACT_MISSING")
    if layout_passed is None:
        return existing
    gate = {
        "status": "PASSED" if not blocking_reasons else "FAILED",
        "passed": not blocking_reasons,
        "scope": "compile_artifact_integrity",
        "compile_exit_code": compile_exit_code,
        "layout_passed": layout_passed,
        "blocking_reasons": blocking_reasons,
        "note": "BModel inference quality remains gated by the approved edge validation stage",
    }
    export.quality_gate_json = _dumps(gate)
    return gate


def materialize_bm1688_export(
    db: Session, *, round_id: int, origin: str, reason: str | None
) -> YoloBm1688Export:
    _ensure_phase_one_writes_open()
    if origin not in {"AUTO_QUALITY", "MANUAL_DIAGNOSTIC"}:
        raise ValueError("unsupported BM1688 pipeline origin")
    # Serialize stage materialization on the round row.  The lookup below is
    # intentionally not the only guard: the database unique index is the
    # final protection if a backend does not honor row locks.
    round_row = (
        db.query(AutoTrainingRound)
        .filter_by(id=round_id)
        .with_for_update()
        .one()
    )
    plan = db.query(AutoTrainingPlan).filter_by(id=round_row.plan_id).one()
    existing = (
        db.query(YoloBm1688Export)
        .filter_by(auto_training_round_id=round_id)
        .first()
    )
    if existing is not None:
        if existing.pipeline_origin != origin:
            raise ValueError("round already has a BM1688 export with another origin")
        return existing
    allowed = (
        {"AUTO_QUALITY_REACHED", "BM1688_QUEUED"}
        if origin == "AUTO_QUALITY"
        else {"QUALITY_FAILED", "MANUAL_BM1688_QUEUED"}
    )
    if round_row.state not in allowed:
        raise ValueError(f"round state {round_row.state} cannot create BM1688 export")
    job = db.query(YoloTrainJob).filter_by(id=round_row.yolo_train_job_id).one()
    try:
        assert_project_chain(
            [getattr(plan, "project_id", None), task_project_id(db, job.task_id), getattr(job, "project_id", None)],
            context="auto-training BM1688 export",
        )
    except ProjectScopeError as exc:
        raise ValueError("auto-training BM1688 export project scope is inconsistent") from exc
    if job.status != "COMPLETED" or not str(job.weights_path or "").strip():
        raise ValueError("completed PT weights are required before BM1688 compilation")
    execution_signature = _execution_signature(round_id, "bm1688", origin)
    export = YoloBm1688Export(
        user_id=plan.owner_id,
        task_id=job.task_id,
        yolo_train_job_id=job.id,
        source_type="auto_training",
        source_system="labelu_auto_training",
        status="PENDING",
        imgsz=int(job.imgsz or 640),
        auto_training_plan_id=plan.id,
        auto_training_round_id=round_id,
        execution_signature=execution_signature,
        pipeline_origin=origin,
        quality_gate_json=_dumps(
            {"passed": False, "status": "PENDING", "reason": reason or ""}
        ),
    )
    db.add(export)
    db.flush()
    round_row.bm1688_export_id = export.id
    round_row.state = (
        "BM1688_QUEUED" if origin == "AUTO_QUALITY" else "MANUAL_BM1688_QUEUED"
    )
    _stage_update(round_row, stage="BM1688", status="QUEUED")
    enqueue_auto_training_event(
        db,
        event_name="auto.round.bm1688.execute.v1",
        aggregate_type="auto_training_round",
        aggregate_id=str(round_id),
        payload={
            "plan_id": plan.id,
            "round_id": round_id,
            "export_id": export.id,
            "origin": origin,
        },
        idempotency_key=f"round:{round_id}:bm1688-execute",
    )
    db.flush()
    return export


def synchronize_bm1688_export(
    db: Session, *, export_id: int, now: datetime | None = None
) -> LifecycleResult:
    _ensure_phase_one_writes_open()
    current_time = now or datetime.now()
    export = db.query(YoloBm1688Export).filter_by(id=export_id).one()
    if export.auto_training_round_id is None:
        raise ValueError("BM1688 export is not managed by auto training")
    round_row = (
        db.query(AutoTrainingRound)
        .filter_by(id=int(export.auto_training_round_id))
        .one()
    )
    plan = db.query(AutoTrainingPlan).filter_by(id=round_row.plan_id).one()
    version = (
        db.query(AutoTrainingPlanVersion)
        .filter_by(id=round_row.plan_version_id)
        .one()
    )
    diagnostic = export.pipeline_origin == "MANUAL_DIAGNOSTIC"
    if round_row.edge_validation_job_id is not None or round_row.state in {
        "EDGE_VALIDATION_QUEUED",
        "EDGE_VALIDATING",
        "EDGE_VALIDATION_FAILED",
        "PUBLISH_READY",
        "PUBLISHED",
        "MANUAL_EDGE_VALIDATING",
        "DIAGNOSTIC_COMPLETED",
        "DIAGNOSTIC_FAILED",
    }:
        return LifecycleResult(False, "UNCHANGED", round_row.id)

    if export.status in {"PENDING", "PROCESSING", "ONNX_READY"}:
        target = "MANUAL_BM1688_COMPILING" if diagnostic else "BM1688_COMPILING"
        if round_row.state == target:
            return LifecycleResult(False, "UNCHANGED", round_row.id)
        round_row.state = target
        _stage_update(round_row, stage="BM1688", status=export.status)
        db.flush()
        return LifecycleResult(True, "BM1688_COMPILING", round_row.id)

    if export.status == "FAILED":
        retries = _loads(round_row.retry_count_json, {})
        failures = int(retries.get("BM1688", 0)) + 1
        retries["BM1688"] = failures
        round_row.retry_count_json = _dumps(retries)
        limit = _pipeline_retry_limit(version, "BM1688")
        if failures < limit:
            export.status = "PENDING"
            export.error_message = None
            round_row.state = (
                "MANUAL_BM1688_QUEUED" if diagnostic else "BM1688_QUEUED"
            )
            _stage_update(round_row, stage="BM1688", status="RETRY_QUEUED")
            enqueue_auto_training_event(
                db,
                event_name="auto.round.bm1688.execute.v1",
                aggregate_type="auto_training_round",
                aggregate_id=str(round_row.id),
                payload={
                    "round_id": round_row.id,
                    "export_id": export.id,
                    "origin": export.pipeline_origin,
                    "retry_no": failures,
                },
                idempotency_key=f"round:{round_row.id}:bm1688-execute:retry:{failures}",
            )
            db.flush()
            return LifecycleResult(True, "RETRY_QUEUED", round_row.id)
        round_row.state = "DIAGNOSTIC_FAILED" if diagnostic else "BM1688_FAILED"
        round_row.completed_at = current_time
        _stage_update(
            round_row,
            stage="BM1688",
            status="FAILED",
            error=export.error_message or "BM1688 compilation failed",
        )
        if plan.active_round_id == round_row.id:
            plan.active_round_id = None
        if not diagnostic:
            plan.status = "PAUSED_SAFETY"
            plan.status_reason_code = "BM1688_TECHNICAL_FAILURE_LIMIT_REACHED"
            plan.version += 1
        db.flush()
        return LifecycleResult(True, "SAFETY_PAUSED", round_row.id)

    if export.status != "COMPLETED":
        return LifecycleResult(False, "UNCHANGED", round_row.id)
    round_row.state = (
        "MANUAL_BMODEL_EVALUATING" if diagnostic else "BMODEL_EVALUATING"
    )
    _stage_update(round_row, stage="BM1688", status="COMPLETED")
    gate = _derive_compile_artifact_gate(export)
    if gate.get("status") == "PENDING" or "passed" not in gate:
        db.flush()
        return LifecycleResult(True, "WAITING_BMODEL_QUALITY_GATE", round_row.id)
    if gate.get("passed") is not True:
        round_row.state = "DIAGNOSTIC_FAILED" if diagnostic else "BMODEL_QUALITY_FAILED"
        round_row.completed_at = current_time
        _stage_update(round_row, stage="BMODEL_QUALITY", status="FAILED")
        if plan.active_round_id == round_row.id:
            plan.active_round_id = None
        db.flush()
        return LifecycleResult(True, "BMODEL_QUALITY_FAILED", round_row.id)

    _stage_update(round_row, stage="BMODEL_QUALITY", status="COMPLETED")
    round_row.state = "MANUAL_EDGE_VALIDATING" if diagnostic else "EDGE_VALIDATION_QUEUED"
    enqueue_auto_training_event(
        db,
        event_name="auto.round.edge.requested.v1",
        aggregate_type="auto_training_round",
        aggregate_id=str(round_row.id),
        payload={
            "plan_id": plan.id,
            "round_id": round_row.id,
            "export_id": export.id,
            "origin": export.pipeline_origin,
            "reason": "BM1688 quality gate passed",
        },
        idempotency_key=f"round:{round_row.id}:edge",
    )
    db.flush()
    return LifecycleResult(True, "EDGE_VALIDATION_QUEUED", round_row.id)


def materialize_edge_validation_job(
    db: Session, *, round_id: int, export_id: int, origin: str
) -> YoloEdgeValidationJob:
    _ensure_phase_one_writes_open()
    if origin not in {"AUTO_QUALITY", "MANUAL_DIAGNOSTIC"}:
        raise ValueError("unsupported edge validation origin")
    # Keep BM1688 and edge materialization serialized with the round state
    # transition so parallel workers reuse the existing stage row.
    round_row = (
        db.query(AutoTrainingRound)
        .filter_by(id=round_id)
        .with_for_update()
        .one()
    )
    plan = db.query(AutoTrainingPlan).filter_by(id=round_row.plan_id).one()
    export = db.query(YoloBm1688Export).filter_by(id=export_id).one()
    try:
        assert_project_chain(
            [getattr(plan, "project_id", None), task_project_id(db, export.task_id)],
            context="auto-training edge validation",
        )
    except ProjectScopeError as exc:
        raise ValueError("auto-training edge validation project scope is inconsistent") from exc
    if export.auto_training_round_id != round_id or export.pipeline_origin != origin:
        raise ValueError("edge validation lineage does not match BM1688 export")
    existing = (
        db.query(YoloEdgeValidationJob)
        .filter_by(auto_training_round_id=round_id)
        .first()
    )
    if existing is not None:
        if existing.pipeline_origin != origin:
            raise ValueError("round already has an edge job with another origin")
        return existing
    if export.status != "COMPLETED":
        raise ValueError("completed BM1688 export is required")
    gate = _loads(export.quality_gate_json, {})
    if gate.get("passed") is not True:
        raise ValueError("BM1688 quality gate must pass before edge validation")
    version = (
        db.query(AutoTrainingPlanVersion)
        .filter_by(id=round_row.plan_version_id)
        .one()
    )
    config = _loads(version.config_json, {})
    edge_policy = config.get("edge_validation_policy", {})
    binding = config.get("evaluation_binding", {})
    dataset_id = edge_policy.get("evaluation_dataset_id") or binding.get(
        "edge_validation_dataset_id"
    )
    if not dataset_id:
        raise ValueError("approved edge validation dataset is not configured")
    dataset = (
        db.query(TrainingEvaluationDataset)
        .filter_by(id=int(dataset_id), status="APPROVED")
        .one_or_none()
    )
    if dataset is None:
        raise ValueError("edge validation dataset is not approved")
    if (
        dataset.series_type != plan.series_type
        or dataset.dataset_category_id != plan.dataset_category_id
        or dataset.label_set_signature != plan.label_set_signature
    ):
        raise ValueError("edge validation dataset scope does not match plan")
    sample_rows = (
        db.query(TrainingEvaluationSample)
        .filter_by(evaluation_dataset_id=dataset.id)
        .order_by(TrainingEvaluationSample.id)
        .all()
    )
    maximum = int(edge_policy.get("max_images") or 50)
    sample_rows = sample_rows[: max(1, maximum)]
    minimum = int(edge_policy.get("minimum_valid_inferences") or 1)
    if len(sample_rows) < minimum:
        raise ValueError("edge validation dataset has insufficient samples")
    sample_task_ids = _validate_evaluation_samples(db, rows=sample_rows)
    task_ids = set(sample_task_ids.values())
    sample_ids = [int(item.sample_id) for item in sample_rows]
    thresholds = edge_policy.get("thresholds") or {}
    edge = YoloEdgeValidationJob(
        user_id=plan.owner_id,
        task_id=min(task_ids),
        export_id=export.id,
        source_type="auto_training",
        source_system="labelu_auto_training",
        status="PENDING",
        mode=str(edge_policy.get("mode") or "run_and_eval"),
        threshold_source="auto_training_plan",
        sample_ids_json=_dumps(sample_ids),
        thresholds_json=_dumps(thresholds),
        result_json=_dumps(
            {
                "request": {
                    "sample_ids": sample_ids,
                    "sample_task_ids": {
                        str(sample_id): source_task_id
                        for sample_id, source_task_id in sample_task_ids.items()
                    },
                    "task_sample_groups": {
                        str(source_task_id): [
                            sample_id
                            for sample_id, task_id_value in sample_task_ids.items()
                            if task_id_value == source_task_id
                        ]
                        for source_task_id in sorted(task_ids)
                    },
                    "evaluation_dataset_id": dataset.id,
                    "evaluation_manifest_sha256": dataset.manifest_sha256,
                    "dataset_signature": round_row.dataset_signature,
                    "attachment_ids": [],
                }
            }
        ),
        image_count=len(sample_ids),
        labeled_image_count=len(sample_ids),
        unlabeled_image_count=0,
        report_summary=f"自动迭代边端验证，共 {len(sample_ids)} 张批准样本",
        auto_training_plan_id=plan.id,
        auto_training_round_id=round_id,
        execution_signature=_execution_signature(round_id, "edge", origin),
        pipeline_origin=origin,
    )
    db.add(edge)
    db.flush()
    round_row.edge_validation_job_id = edge.id
    round_row.state = "MANUAL_EDGE_VALIDATING" if origin == "MANUAL_DIAGNOSTIC" else "EDGE_VALIDATION_QUEUED"
    _stage_update(round_row, stage="EDGE_VALIDATION", status="QUEUED")
    enqueue_auto_training_event(
        db,
        event_name="auto.round.edge.execute.v1",
        aggregate_type="auto_training_round",
        aggregate_id=str(round_id),
        payload={
            "plan_id": plan.id,
            "round_id": round_id,
            "edge_job_id": edge.id,
            "origin": origin,
        },
        idempotency_key=f"round:{round_id}:edge-execute",
    )
    db.flush()
    return edge


def synchronize_edge_validation_job(
    db: Session, *, edge_job_id: int, now: datetime | None = None
) -> LifecycleResult:
    _ensure_phase_one_writes_open()
    current_time = now or datetime.now()
    edge = db.query(YoloEdgeValidationJob).filter_by(id=edge_job_id).one()
    if edge.auto_training_round_id is None:
        raise ValueError("edge validation job is not managed by auto training")
    round_row = (
        db.query(AutoTrainingRound)
        .filter_by(id=int(edge.auto_training_round_id))
        .one()
    )
    plan = db.query(AutoTrainingPlan).filter_by(id=round_row.plan_id).one()
    diagnostic = edge.pipeline_origin == "MANUAL_DIAGNOSTIC"
    if round_row.state in {"PUBLISHED", "DIAGNOSTIC_COMPLETED", "DIAGNOSTIC_FAILED"}:
        return LifecycleResult(False, "UNCHANGED", round_row.id)
    if edge.status in {"PENDING", "QUEUED", "PROCESSING"}:
        target = "MANUAL_EDGE_VALIDATING" if diagnostic else "EDGE_VALIDATING"
        if round_row.state == target:
            return LifecycleResult(False, "UNCHANGED", round_row.id)
        round_row.state = target
        _stage_update(round_row, stage="EDGE_VALIDATION", status=edge.status)
        db.flush()
        return LifecycleResult(True, "EDGE_VALIDATING", round_row.id)

    result = _loads(edge.result_json, {})
    summary = result.get("summary", {}) if isinstance(result.get("summary"), dict) else {}
    gates_passed = result.get("all_gates_passed") is True or summary.get("all_gates_passed") is True
    completed = edge.status == "COMPLETED" and gates_passed
    if diagnostic:
        round_row.state = "DIAGNOSTIC_COMPLETED" if completed else "DIAGNOSTIC_FAILED"
        action = round_row.state
    else:
        round_row.state = "PUBLISH_READY" if completed else "EDGE_VALIDATION_FAILED"
        action = round_row.state
    round_row.completed_at = current_time
    _stage_update(
        round_row,
        stage="EDGE_VALIDATION",
        status="COMPLETED" if completed else "FAILED",
        error=None if completed else edge.error_message or "edge validation gates failed",
    )
    if diagnostic or not completed:
        if plan.active_round_id == round_row.id:
            plan.active_round_id = None
    db.flush()
    return LifecycleResult(True, action, round_row.id)


def _assert_publishable_round(
    db: Session, *, round_row: AutoTrainingRound
) -> tuple[YoloBm1688Export, YoloEdgeValidationJob]:
    export = db.query(YoloBm1688Export).filter_by(id=round_row.bm1688_export_id).one()
    edge = db.query(YoloEdgeValidationJob).filter_by(id=round_row.edge_validation_job_id).one()
    if export.pipeline_origin != "AUTO_QUALITY" or edge.pipeline_origin != "AUTO_QUALITY":
        raise ValueError("diagnostic pipeline can never be published")
    if export.status != "COMPLETED" or edge.status != "COMPLETED":
        raise ValueError("all artifact and edge jobs must be completed")
    edge_result = _loads(edge.result_json, {})
    if edge_result.get("all_gates_passed") is not True and (
        not isinstance(edge_result.get("summary"), dict)
        or edge_result["summary"].get("all_gates_passed") is not True
    ):
        raise ValueError("edge validation gates are not complete")
    return export, edge


def register_aicm_publish_job(
    db: Session,
    *,
    round_id: int,
    actor_id: int,
    actor_is_admin: bool,
    reason: str,
    publish_job_id: str,
) -> LifecycleResult:
    _ensure_phase_one_writes_open()
    if not actor_is_admin:
        raise PermissionError("formal model publication is admin-only")
    if not reason or not reason.strip():
        raise ValueError("formal model publication requires a reason")
    if not publish_job_id or not publish_job_id.strip():
        raise ValueError("AICM publish job id is required")
    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    normalized_job_id = publish_job_id.strip()
    if round_row.state == "PUBLISHED":
        return LifecycleResult(False, "UNCHANGED", round_id)
    if round_row.state == "PUBLISHING":
        if round_row.publish_job_id == normalized_job_id:
            return LifecycleResult(False, "UNCHANGED", round_id)
        raise ValueError("round already has an active AICM publish job")
    if round_row.state not in {"PUBLISH_READY", "PUBLISH_FAILED"}:
        raise ValueError("only a publish-ready or failed publication can start publishing")
    _assert_publishable_round(db, round_row=round_row)
    round_row.state = "PUBLISHING"
    round_row.publish_job_id = normalized_job_id
    round_row.publish_status = "PROCESSING"
    round_row.publish_requested_by = actor_id
    round_row.publish_reason = reason.strip()
    round_row.publish_error = None
    round_row.publish_requested_at = datetime.now()
    _stage_update(round_row, stage="PUBLISH", status="PROCESSING")
    db.flush()
    return LifecycleResult(True, "PUBLISHING", round_id)


def mark_round_published(
    db: Session,
    *,
    round_id: int,
    actor_id: int,
    actor_is_admin: bool,
    reason: str,
) -> LifecycleResult:
    _ensure_phase_one_writes_open()
    if not actor_is_admin:
        raise PermissionError("formal model publication is admin-only")
    if not reason or not reason.strip():
        raise ValueError("formal model publication requires a reason")
    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    if round_row.state != "PUBLISHING" or round_row.publish_status != "COMPLETED":
        raise ValueError("round can be published only after the AICM publish job succeeds")
    if not round_row.published_model_id:
        raise ValueError("AICM publish success is missing the published model id")
    export, _edge = _assert_publishable_round(db, round_row=round_row)
    plan = db.query(AutoTrainingPlan).filter_by(id=round_row.plan_id).one()
    round_row.state = "PUBLISHED"
    round_row.completed_at = datetime.now()
    round_row.published_at = round_row.completed_at
    plan.champion_job_id = round_row.yolo_train_job_id
    plan.champion_export_id = export.id
    plan.active_round_id = None
    plan.status = "AUTO_QUALITY_REACHED"
    plan.status_reason_code = "PUBLISHED_BY_ADMIN"
    plan.status_reason_json = _dumps(
        {"round_id": round_id, "actor_id": actor_id, "reason": reason.strip()}
    )
    plan.version += 1
    _stage_update(round_row, stage="PUBLISH", status="COMPLETED")
    db.flush()
    return LifecycleResult(True, "PUBLISHED", round_id)


def synchronize_aicm_publish_job(
    db: Session,
    *,
    round_id: int,
    job_payload: dict[str, Any],
) -> LifecycleResult:
    _ensure_phase_one_writes_open()
    round_row = db.query(AutoTrainingRound).filter_by(id=round_id).one()
    if round_row.state == "PUBLISHED":
        return LifecycleResult(False, "UNCHANGED", round_id)
    job_id = str(job_payload.get("job_id") or "").strip()
    if not round_row.publish_job_id or job_id != round_row.publish_job_id:
        raise ValueError("AICM publish job lineage does not match the round")
    if round_row.state != "PUBLISHING":
        return LifecycleResult(False, "UNCHANGED", round_id)
    status = str(job_payload.get("status") or "").strip().upper()
    if status in {"", "PENDING", "PROCESSING"}:
        round_row.publish_status = status or "PROCESSING"
        db.flush()
        return LifecycleResult(False, "UNCHANGED", round_id)
    result = job_payload.get("result") if isinstance(job_payload.get("result"), dict) else {}
    if status != "COMPLETED" or result.get("ok") is not True:
        message = str(
            job_payload.get("error_message")
            or result.get("error_message")
            or "AICM publish job failed"
        )
        round_row.state = "PUBLISH_FAILED"
        round_row.publish_status = status or "FAILED"
        round_row.publish_error = message[:4000]
        _stage_update(round_row, stage="PUBLISH", status="FAILED", error=message)
        db.flush()
        return LifecycleResult(True, "PUBLISH_FAILED", round_id)
    try:
        published_model_id = int(result.get("model_set_id") or result.get("published_model_id"))
    except (TypeError, ValueError):
        published_model_id = 0
    if published_model_id <= 0:
        raise ValueError("AICM publish result is missing model_set_id")
    round_row.publish_status = "COMPLETED"
    round_row.published_model_id = published_model_id
    round_row.publish_error = None
    return mark_round_published(
        db,
        round_id=round_id,
        actor_id=round_row.publish_requested_by or 0,
        actor_is_admin=True,
        reason=round_row.publish_reason or "AICM publication completed",
    )
