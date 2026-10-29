from typing import List

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from labelu.internal.application.command.auto_training import (
    AutoTrainingActionCommand,
    AutoTrainingApproveCommand,
    AutoTrainingRuntimeSettingsCommand,
    AutoTrainingRoundCreateCommand,
    AutoGenerateEvaluationDatasetsCommand,
    CreateAutoTrainingPlanCommand,
    EvaluationDatasetCommand,
    EvaluationRunClaimCommand,
    PatchAutoTrainingPlanCommand,
    PatchEvaluationDatasetCommand,
    PreviewAutoTrainingPlanCommand,
    PublishAutoTrainingRoundCommand,
    QualityEvidenceCommand,
    QualityPolicyApprovalCommand,
    ReasonCommand,
    ReplaceEvaluationDatasetSamplesCommand,
    RetryAutoTrainingStageCommand,
    SubmitCurrentQualityPolicyApprovalCommand,
    TrainingReviewCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.service import auto_training_plan as service
from labelu.internal.application.service.auto_training_evaluator import (
    EVALUATION_RUN_STATUSES,
    EvaluationRunConflictError,
    EvaluationRunNotFoundError,
    claim_evaluation_run,
    evaluation_run_payload,
    list_evaluation_runs,
)
from labelu.internal.application.service.auto_training_state_machine import PlanStatus
from labelu.internal.common import db as db_module
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User
from labelu.internal.common.task_access import is_task_superuser


def require_auto_training_plan_writes_open(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> None:
    """Keep all phase-one automatic-training control surfaces read-only."""
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        if (
            not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED
            and not is_task_superuser(current_user)
        ):
            raise LabelUException(
                ErrorCode.CODE_30001_NO_PERMISSION,
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return
    service.ensure_auto_training_plan_writes_open()


_READ_ONLY_DEPENDENCY = [Depends(require_auto_training_plan_writes_open)]


plans_router = APIRouter(
    prefix="/auto-training-plans",
    tags=["auto_training"],
    dependencies=_READ_ONLY_DEPENDENCY,
)
rounds_router = APIRouter(
    prefix="/auto-training-rounds",
    tags=["auto_training"],
    dependencies=_READ_ONLY_DEPENDENCY,
)
reviews_router = APIRouter(
    tags=["auto_training"], dependencies=_READ_ONLY_DEPENDENCY
)
evaluation_router = APIRouter(
    prefix="/training-evaluation-datasets",
    tags=["auto_training"],
    dependencies=_READ_ONLY_DEPENDENCY,
)
quality_approvals_router = APIRouter(
    tags=["auto_training"], dependencies=_READ_ONLY_DEPENDENCY
)
evaluation_jobs_router = APIRouter(
    prefix="/auto-training-evaluation-jobs",
    tags=["auto_training"],
    dependencies=_READ_ONLY_DEPENDENCY,
)
settings_router = APIRouter(
    prefix="/auto-training-settings",
    tags=["auto_training"],
    dependencies=_READ_ONLY_DEPENDENCY,
)


@settings_router.get("", response_model=OkResp[dict])
def get_runtime_settings(
    current_user: User = Depends(get_current_user),
):
    return OkResp[dict](data=service.runtime_settings_payload(user=current_user))


@settings_router.patch("", response_model=OkResp[dict])
def patch_runtime_settings(
    cmd: AutoTrainingRuntimeSettingsCommand,
    current_user: User = Depends(get_current_user),
):
    values = cmd.dict(exclude={"reason"})
    data = service.update_runtime_settings(
        user=current_user,
        values=values,
        reason=cmd.reason,
    )
    return OkResp[dict](data=data)


@plans_router.post("/preview", response_model=OkResp[dict])
def preview_plan(cmd: PreviewAutoTrainingPlanCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    service.validate_selected_task_scope(db, user=current_user, data=cmd.dict())
    return OkResp[dict](data=service.preview_plan(cmd.config))


@plans_router.post("", response_model=OkResp[dict], status_code=status.HTTP_201_CREATED)
def create_plan(cmd: CreateAutoTrainingPlanCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.create_plan(db, user=current_user, data=cmd.dict(exclude={"change_reason"}), change_reason=cmd.change_reason)
    return OkResp[dict](data=data)


@plans_router.get("", response_model=OkRespWithMeta[List[dict]])
def list_plans(
    q: str | None = Query(
        None,
        max_length=128,
        description="按计划名称、系列或状态模糊搜索",
    ),
    search: str | None = Query(
        None,
        max_length=128,
        include_in_schema=False,
        description="兼容旧客户端的搜索参数",
    ),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    project_id: int | None = Query(None, gt=0, description="当前7GAI项目 ID"),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    normalized_query = str(q if q is not None else search or "").strip() or None
    data, total = service.list_plans(
        db,
        user=current_user,
        page=page,
        size=size,
        project_id=project_id,
        q=normalized_query,
    )
    return OkRespWithMeta[List[dict]](data=data, meta_data=MetaData(total=total, page=page, size=len(data)))


@plans_router.get("/summary", response_model=OkResp[dict])
def project_runtime_summary(
    project_id: int = Query(..., gt=0, description="当前7GAI项目 ID"),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the project-scoped automatic-training health and latest round."""
    return OkResp[dict](
        data=service.project_runtime_summary(
            db, user=current_user, project_id=project_id
        )
    )


@plans_router.get("/{plan_id}", response_model=OkResp[dict])
def get_plan(plan_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[dict](data=service.get_plan_detail(db, user=current_user, plan_id=plan_id))


@plans_router.patch("/{plan_id}", response_model=OkResp[dict])
def patch_plan(plan_id: int, cmd: PatchAutoTrainingPlanCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.patch_plan(
            db,
            user=current_user,
            plan_id=plan_id,
            expected_version=cmd.expected_version,
            name=cmd.name,
            series_type=cmd.series_type,
            dataset_category_id=cmd.dataset_category_id,
            label_set_signature=cmd.label_set_signature,
            class_order_signature=cmd.class_order_signature,
            config=cmd.config,
            reason=cmd.change_reason,
        )
    return OkResp[dict](data=data)


def _transition(db: Session, current_user: User, plan_id: int, cmd: AutoTrainingActionCommand, target: PlanStatus, *, admin_required=False):
    with begin_transaction(db):
        return service.transition_plan(db, user=current_user, plan_id=plan_id, expected_version=cmd.expected_version, target=target, reason=cmd.reason, acknowledge_quality_reached=cmd.acknowledge_quality_reached, admin_required=admin_required)


@plans_router.post("/{plan_id}/submit", response_model=OkResp[dict])
def submit_plan(plan_id: int, cmd: AutoTrainingActionCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[dict](data=_transition(db, current_user, plan_id, cmd, PlanStatus.PENDING_APPROVAL))


@plans_router.post("/{plan_id}/approve", response_model=OkResp[dict])
def approve_plan(plan_id: int, cmd: AutoTrainingApproveCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    action = AutoTrainingActionCommand(
        expected_version=cmd.expected_version,
        reason=cmd.reason or "管理员批准自动训练计划",
    )
    return OkResp[dict](data=_transition(db, current_user, plan_id, action, PlanStatus.ACTIVE, admin_required=True))


@plans_router.post("/{plan_id}/pause", response_model=OkResp[dict])
def pause_plan(plan_id: int, cmd: AutoTrainingActionCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[dict](data=_transition(db, current_user, plan_id, cmd, PlanStatus.PAUSED_MANUAL))


@plans_router.post("/{plan_id}/resume", response_model=OkResp[dict])
def resume_plan(plan_id: int, cmd: AutoTrainingActionCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[dict](data=_transition(db, current_user, plan_id, cmd, PlanStatus.ACTIVE))


@plans_router.post("/{plan_id}/archive", response_model=OkResp[dict])
def archive_plan(plan_id: int, cmd: AutoTrainingActionCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.archive_plan(db, user=current_user, plan_id=plan_id, expected_version=cmd.expected_version, reason=cmd.reason)
    return OkResp[dict](data=data)


@plans_router.get("/{plan_id}/versions", response_model=OkResp[List[dict]])
def list_versions(plan_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[List[dict]](data=service.list_versions(db, user=current_user, plan_id=plan_id))


@plans_router.get("/{plan_id}/audit-events", response_model=OkResp[List[dict]])
def list_audits(plan_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[List[dict]](data=service.list_audits(db, user=current_user, plan_id=plan_id))


@plans_router.post("/{plan_id}/scan-preview", response_model=OkResp[dict], status_code=status.HTTP_202_ACCEPTED)
def create_scan_preview(plan_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.create_scan_preview(db, user=current_user, plan_id=plan_id)
    return OkResp[dict](data=data)


@plans_router.get("/{plan_id}/scan-preview/{scan_job_id}", response_model=OkResp[dict])
def get_scan_preview(plan_id: int, scan_job_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[dict](data=service.get_scan_preview(db, user=current_user, plan_id=plan_id, scan_job_id=scan_job_id))


@plans_router.get("/{plan_id}/task-scopes", response_model=OkResp[List[dict]])
def list_task_scopes(plan_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[List[dict]](data=service.list_task_scopes(db, user=current_user, plan_id=plan_id))


@plans_router.get("/{plan_id}/sample-pool", response_model=OkRespWithMeta[List[dict]])
def list_sample_pool(plan_id: int, page: int = Query(0, ge=0), size: int = Query(20, ge=1, le=100), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    data, total = service.list_sample_pool(db, user=current_user, plan_id=plan_id, page=page, size=size)
    return OkRespWithMeta[List[dict]](data=data, meta_data=MetaData(total=total, page=page, size=len(data)))


@plans_router.post("/{plan_id}/reconcile", response_model=OkResp[dict], status_code=status.HTTP_202_ACCEPTED)
def reconcile(plan_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.request_reconcile(db, user=current_user, plan_id=plan_id)
    return OkResp[dict](data=data)


@plans_router.get("/{plan_id}/rounds", response_model=OkResp[List[dict]])
def list_rounds(plan_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[List[dict]](data=service.list_rounds(db, user=current_user, plan_id=plan_id))


@plans_router.post("/{plan_id}/rounds/preview", response_model=OkResp[dict])
def preview_round(plan_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[dict](data=service.preview_round(db, user=current_user, plan_id=plan_id))


@plans_router.post("/{plan_id}/rounds", response_model=OkResp[dict], status_code=status.HTTP_202_ACCEPTED)
def create_round(plan_id: int, cmd: AutoTrainingRoundCreateCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.create_round(db, user=current_user, plan_id=plan_id, reason=cmd.reason)
    return OkResp[dict](data=data)


@rounds_router.get("/{round_id}", response_model=OkResp[dict])
def get_round(round_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[dict](data=service.get_round(db, user=current_user, round_id=round_id))


@rounds_router.get("/{round_id}/samples", response_model=OkRespWithMeta[List[dict]])
def list_round_samples(round_id: int, page: int = Query(0, ge=0), size: int = Query(20, ge=1, le=100), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    data, total = service.list_round_samples(db, user=current_user, round_id=round_id, page=page, size=size)
    return OkRespWithMeta[List[dict]](data=data, meta_data=MetaData(total=total, page=page, size=len(data)))


@rounds_router.get("/{round_id}/quality", response_model=OkResp[dict])
def get_round_quality(round_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[dict](data=service.get_round_quality(db, user=current_user, round_id=round_id))


@rounds_router.post("/{round_id}/cancel", response_model=OkResp[dict], status_code=status.HTTP_202_ACCEPTED)
def cancel_round(round_id: int, cmd: ReasonCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.cancel_round(db, user=current_user, round_id=round_id, reason=cmd.reason)
    return OkResp[dict](data=data)


@rounds_router.post("/{round_id}/retry-stage", response_model=OkResp[dict], status_code=status.HTTP_202_ACCEPTED)
def retry_stage(round_id: int, cmd: RetryAutoTrainingStageCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.retry_stage(db, user=current_user, round_id=round_id, stage=cmd.stage, reason=cmd.reason, expected_state=cmd.expected_state)
    return OkResp[dict](data=data)


@rounds_router.post("/{round_id}/promote-to-bm1688", response_model=OkResp[dict], status_code=status.HTTP_202_ACCEPTED)
def promote_to_bm1688(round_id: int, cmd: ReasonCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.promote_to_bm1688(db, user=current_user, round_id=round_id, reason=cmd.reason)
    return OkResp[dict](data=data)


@rounds_router.post("/{round_id}/start-edge-validation", response_model=OkResp[dict], status_code=status.HTTP_202_ACCEPTED)
def start_edge_validation(round_id: int, cmd: ReasonCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.start_edge_validation(db, user=current_user, round_id=round_id, reason=cmd.reason)
    return OkResp[dict](data=data)


@rounds_router.post("/{round_id}/quality-evidence", response_model=OkResp[dict])
def submit_quality_evidence(round_id: int, cmd: QualityEvidenceCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.submit_quality_evidence(
            db,
            user=current_user,
            round_id=round_id,
            expected_state=cmd.expected_state,
            evaluation_run_id=cmd.evaluation_run_id,
            evaluation_request_sha256=cmd.evaluation_request_sha256,
            overall_metrics=cmd.overall_metrics,
            per_label_metrics=cmd.per_label_metrics,
            plate_component_metrics=cmd.plate_component_metrics,
            champion_comparison=cmd.champion_comparison,
            evidence_manifest_rel_path=cmd.evidence_manifest_rel_path,
            evidence_manifest_sha256=cmd.evidence_manifest_sha256,
        )
    return OkResp[dict](data=data)


@rounds_router.post("/{round_id}/publish", response_model=OkResp[dict], status_code=status.HTTP_202_ACCEPTED)
async def publish_round(round_id: int, cmd: PublishAutoTrainingRoundCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = await service.publish_round(
            db,
            user=current_user,
            round_id=round_id,
            expected_state=cmd.expected_state,
            reason=cmd.reason,
            environment_id=cmd.environment_id,
            keep_template_cache=cmd.keep_template_cache,
            bmodel_precision=cmd.bmodel_precision,
            bmodel_rel_path=cmd.bmodel_rel_path,
        )
    return OkResp[dict](data=data)


@reviews_router.post("/samples/{sample_id}/training-review", response_model=OkResp[dict], status_code=status.HTTP_201_CREATED)
def create_review(sample_id: int, cmd: TrainingReviewCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.create_review(db, user=current_user, sample_id=sample_id, annotation_sha256=cmd.annotation_sha256, review_status=cmd.review_status, comment=cmd.comment)
    return OkResp[dict](data=data)


@reviews_router.post("/training-reviews/{review_id}/invalidate", response_model=OkResp[dict])
def invalidate_review(review_id: int, cmd: ReasonCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.invalidate_review(db, user=current_user, review_id=review_id, reason=cmd.reason)
    return OkResp[dict](data=data)


@evaluation_router.post("", response_model=OkResp[dict], status_code=status.HTTP_201_CREATED)
def create_evaluation_dataset(cmd: EvaluationDatasetCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.create_evaluation_dataset(db, user=current_user, data=cmd.dict())
    return OkResp[dict](data=data)


@evaluation_router.post("/auto-generate", response_model=OkResp[dict], status_code=status.HTTP_201_CREATED)
def auto_generate_evaluation_datasets(
    cmd: AutoGenerateEvaluationDatasetsCommand,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db):
        data = service.auto_generate_evaluation_datasets(
            db,
            user=current_user,
            data=cmd.dict(),
        )
    return OkResp[dict](data=data)


@evaluation_router.get("", response_model=OkRespWithMeta[List[dict]])
def list_evaluation_datasets(
    q: str | None = Query(None, max_length=128, description="按评测集名称、用途、系列、分类或状态搜索"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = service.list_evaluation_datasets(
        db, user=current_user, page=page, size=size, q=q
    )
    return OkRespWithMeta[List[dict]](
        data=data, meta_data=MetaData(total=total, page=page, size=len(data))
    )


@evaluation_router.patch("/{dataset_id}", response_model=OkResp[dict])
def patch_evaluation_dataset(dataset_id: int, cmd: PatchEvaluationDatasetCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.patch_evaluation_dataset(db, user=current_user, dataset_id=dataset_id, data=cmd.dict(exclude_unset=True))
    return OkResp[dict](data=data)


@evaluation_router.post("/{dataset_id}/submit", response_model=OkResp[dict])
def submit_evaluation_dataset(dataset_id: int, cmd: ReasonCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.transition_evaluation_dataset(db, user=current_user, dataset_id=dataset_id, target="PENDING_APPROVAL", reason=cmd.reason)
    return OkResp[dict](data=data)


@evaluation_router.post("/{dataset_id}/approve", response_model=OkResp[dict])
def approve_evaluation_dataset(dataset_id: int, cmd: ReasonCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.transition_evaluation_dataset(db, user=current_user, dataset_id=dataset_id, target="APPROVED", reason=cmd.reason, admin_required=True)
    return OkResp[dict](data=data)


@evaluation_router.get("/{dataset_id}/samples", response_model=OkResp[List[dict]])
def list_evaluation_samples(dataset_id: int, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[List[dict]](data=service.list_evaluation_samples(db, user=current_user, dataset_id=dataset_id))


@evaluation_router.put("/{dataset_id}/samples", response_model=OkResp[dict])
def replace_evaluation_samples(dataset_id: int, cmd: ReplaceEvaluationDatasetSamplesCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.replace_evaluation_samples(
            db,
            user=current_user,
            dataset_id=dataset_id,
            samples=[item.dict() for item in cmd.samples],
        )
    return OkResp[dict](data=data)


@evaluation_jobs_router.get("", response_model=OkResp[List[dict]])
def list_evaluation_jobs(statuses: str = Query("PENDING,PROCESSING"), limit: int = Query(20, ge=1, le=100), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    if not is_task_superuser(current_user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    active_statuses = {item.strip().upper() for item in statuses.split(",") if item.strip()}
    if not active_statuses or not active_statuses <= EVALUATION_RUN_STATUSES:
        raise LabelUException(
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=422,
            message="statuses contains an unsupported evaluation status",
        )
    rows = list_evaluation_runs(db, statuses=active_statuses, limit=limit)
    return OkResp[List[dict]](data=[evaluation_run_payload(row) for row in rows])


@evaluation_jobs_router.post("/{evaluation_run_id}/claim", response_model=OkResp[dict])
def claim_evaluation_job(evaluation_run_id: int, cmd: EvaluationRunClaimCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    if not is_task_superuser(current_user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    try:
        with begin_transaction(db):
            row = claim_evaluation_run(
                db,
                run_id=evaluation_run_id,
                worker_id=cmd.worker_id,
                lease_seconds=cmd.lease_seconds,
            )
            data = evaluation_run_payload(row)
    except EvaluationRunNotFoundError as exc:
        raise LabelUException(
            ErrorCode.CODE_57120_AUTO_TRAIN_EVALUATION_RUN_NOT_FOUND,
            status_code=404,
            message=str(exc),
        ) from exc
    except EvaluationRunConflictError as exc:
        raise LabelUException(
            ErrorCode.CODE_57121_AUTO_TRAIN_EVALUATION_RUN_CONFLICT,
            status_code=409,
            message=str(exc),
        ) from exc
    return OkResp[dict](data=data)


@quality_approvals_router.post("/yolo-quality-policies/{policy_name}/submit-approval", response_model=OkResp[dict])
def submit_quality_policy(policy_name: str, cmd: QualityPolicyApprovalCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.submit_quality_policy(db, user=current_user, policy_name=policy_name, data=cmd.dict())
    return OkResp[dict](data=data)


@quality_approvals_router.post(
    "/quality-policy-approvals/submit-current", response_model=OkResp[dict]
)
def submit_current_quality_policy(
    cmd: SubmitCurrentQualityPolicyApprovalCommand,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    with begin_transaction(db):
        data = service.submit_current_quality_policy(
            db,
            user=current_user,
            policy_name=cmd.policy_name,
            reason=cmd.reason,
        )
    return OkResp[dict](data=data)


@quality_approvals_router.post("/yolo-quality-policies/{policy_name}/approve", response_model=OkResp[dict])
def approve_quality_policy(policy_name: str, cmd: ReasonCommand, db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    with begin_transaction(db):
        data = service.approve_quality_policy(db, user=current_user, policy_name=policy_name, reason=cmd.reason)
    return OkResp[dict](data=data)


@quality_approvals_router.get(
    "/quality-policy-approvals", response_model=OkRespWithMeta[List[dict]]
)
def list_quality_approvals(
    q: str | None = Query(None, max_length=128, description="按策略、场景、版本、状态或审批原因搜索"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    view: str = Query("history", pattern="^(history|workspace)$"),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    if view == "workspace":
        data, total = service.list_quality_approval_workspace(
            db, user=current_user, page=page, size=size, q=q
        )
    else:
        data, total = service.list_quality_approvals(
            db, user=current_user, page=page, size=size, q=q
        )
    return OkRespWithMeta[List[dict]](
        data=data, meta_data=MetaData(total=total, page=page, size=len(data))
    )


def _hide_phase_one_write_routes_from_openapi() -> None:
    """Keep disabled phase-two controls out of the phase-one API contract."""
    if settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
        return
    read_methods = {"GET", "HEAD", "OPTIONS"}
    for router in (
        plans_router,
        rounds_router,
        reviews_router,
        evaluation_router,
        quality_approvals_router,
        evaluation_jobs_router,
        settings_router,
    ):
        for route in router.routes:
            methods = set(getattr(route, "methods", set()) or set())
            if methods and not methods.issubset(read_methods):
                route.include_in_schema = False


_hide_phase_one_write_routes_from_openapi()
