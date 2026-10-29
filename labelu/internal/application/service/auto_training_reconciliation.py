from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import inspect, or_
from sqlalchemy.orm import Session

from labelu.internal.application.service.auto_training_eligibility import (
    EligibilityCandidate,
    evaluate_task_candidates,
)
from labelu.internal.application.service.auto_training_events import (
    enqueue_auto_training_event,
)
from labelu.internal.application.service.auto_training_plan import (
    auto_generate_evaluation_datasets,
    create_plan,
    default_plan_config_for_task,
    ensure_auto_training_plan_writes_open,
    find_plan_for_contract,
    infer_series_type_for_task,
    plan_payload,
    transition_evaluation_dataset,
)
from labelu.internal.application.service.yolo_dataset import _parse_sample_annotation
from labelu.internal.application.service.training_review_gate import (
    current_approved_review,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.label_contract import annotation_sha256
from labelu.internal.common.task_access import user_has_task_access
from labelu.internal.domain.models.annotation_contract import (
    SampleAnnotationScope,
    TaskAnnotationContract,
)
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.auto_training import (
    AutoTrainingOutbox,
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingSamplePool,
    AutoTrainingTaskScope,
    SampleHumanReview,
)
from labelu.internal.domain.models.auto_training_governance import (
    SampleAssetFingerprint,
    TrainingDataGrant,
    TrainingDataWithdrawal,
    TrainingEvaluationDataset,
    TrainingEvaluationSample,
)
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User

RECONCILABLE_PLAN_STATUSES = {
    "ACTIVE",
    "WAITING_FOR_DATA",
    "WAITING_FOR_REVIEW",
    "WAITING_FOR_COVERAGE",
    "ROUND_ACTIVE",
    "AUTO_QUALITY_REACHED",
}


@dataclass(frozen=True)
class TaskReconcileSummary:
    plan_id: int
    task_id: int
    matched: bool
    qualified: bool
    eligible_sample_count: int
    pending_review_count: int
    excluded_sample_count: int


def _loads(raw: str | None, default: Any):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _file_sha256(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _annotation_valid(parsed: tuple[dict[str, Any], dict[str, Any]] | None) -> bool:
    if parsed is None:
        return False
    _outer, result = parsed
    for rect in (result.get("rectTool") or {}).get("result") or []:
        if not isinstance(rect, dict):
            return False
        try:
            x = float(rect.get("x"))
            y = float(rect.get("y"))
            width = float(rect.get("width"))
            height = float(rect.get("height"))
        except (TypeError, ValueError):
            return False
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            return False
    return True


def _task_contract(db: Session, task: Task) -> TaskAnnotationContract | None:
    if not task.current_annotation_contract_id:
        return None
    return (
        db.query(TaskAnnotationContract)
        .filter(TaskAnnotationContract.id == task.current_annotation_contract_id)
        .first()
    )


def task_matches_plan(db: Session, *, plan: AutoTrainingPlan, task: Task) -> bool:
    if task.deleted_at is not None or task.media_type != "IMAGE":
        return False
    if task.dataset_category_id != plan.dataset_category_id:
        return False
    contract = _task_contract(db, task)
    return bool(
        contract
        and contract.label_set_signature == plan.label_set_signature
        and contract.class_order_signature == plan.class_order_signature
        and contract.status in {"active", "approved"}
    )


def _authorized(
    db: Session, *, plan: AutoTrainingPlan, task: Task, sample_id: int
) -> tuple[bool, int | None]:
    if task.created_by == plan.owner_id:
        return True, None
    now = datetime.now()
    grants = (
        db.query(TrainingDataGrant)
        .filter(
            TrainingDataGrant.grantee_plan_id == plan.id,
            TrainingDataGrant.status == "APPROVED",
            or_(
                TrainingDataGrant.valid_from.is_(None),
                TrainingDataGrant.valid_from <= now,
            ),
            or_(
                TrainingDataGrant.valid_to.is_(None), TrainingDataGrant.valid_to >= now
            ),
        )
        .all()
    )
    for grant in grants:
        scope_id = str(grant.grant_scope_id)
        if (grant.grant_scope_type == "TASK" and scope_id == str(task.id)) or (
            grant.grant_scope_type == "SAMPLE" and scope_id == str(sample_id)
        ):
            return True, grant.id
    return False, None


def _withdrawn(db: Session, *, task_id: int, sample_id: int) -> bool:
    return (
        db.query(TrainingDataWithdrawal.id)
        .filter(
            TrainingDataWithdrawal.task_id == task_id,
            TrainingDataWithdrawal.status.in_(("REQUESTED", "APPROVED", "ACTIVE")),
            or_(
                TrainingDataWithdrawal.sample_id.is_(None),
                TrainingDataWithdrawal.sample_id == sample_id,
            ),
        )
        .first()
        is not None
    )


def _duplicate_info(
    db: Session, *, sample_id: int
) -> tuple[bool, int | None, str | None]:
    fingerprint = (
        db.query(SampleAssetFingerprint)
        .filter(SampleAssetFingerprint.sample_id == sample_id)
        .order_by(SampleAssetFingerprint.id.desc())
        .first()
    )
    if fingerprint is None:
        return False, None, None
    if fingerprint.duplicate_group_id is None:
        return False, None, fingerprint.image_sha256
    from labelu.internal.domain.models.auto_training_governance import (
        SampleDuplicateGroup,
    )

    group = (
        db.query(SampleDuplicateGroup)
        .filter_by(id=fingerprint.duplicate_group_id)
        .first()
    )
    is_duplicate = bool(
        group and group.status == "ACTIVE" and group.canonical_sample_id != sample_id
    )
    return is_duplicate, fingerprint.duplicate_group_id, fingerprint.image_sha256


def _pool_signature(
    *,
    sample_id: int,
    image_sha: str | None,
    annotation_sha: str | None,
    contract_sha: str,
) -> str:
    payload = f"{sample_id}:{image_sha or ''}:{annotation_sha or ''}:{contract_sha}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _reserved_evaluation_sample_ids(db: Session, *, config: dict[str, Any]) -> set[int]:
    evaluation = config.get("evaluation_binding", {})
    configured = [
        evaluation.get("validation_dataset_id"),
        evaluation.get("benchmark_dataset_id"),
        evaluation.get("sealed_acceptance_dataset_id"),
        evaluation.get("edge_validation_dataset_id"),
        config.get("bm1688_policy", {}).get("calibration_dataset_id"),
    ]
    dataset_ids: list[int] = []
    for value in configured:
        try:
            parsed = int(value or 0)
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            dataset_ids.append(parsed)
    if not dataset_ids:
        return set()
    return {
        int(sample_id)
        for (sample_id,) in db.query(TrainingEvaluationSample.sample_id)
        .filter(TrainingEvaluationSample.evaluation_dataset_id.in_(dataset_ids))
        .all()
    }


def reconcile_task_for_plan(
    db: Session, *, plan: AutoTrainingPlan, task: Task
) -> TaskReconcileSummary:
    ensure_auto_training_plan_writes_open()
    contract = _task_contract(db, task)
    if not task_matches_plan(db, plan=plan, task=task) or contract is None:
        return TaskReconcileSummary(plan.id, task.id, False, False, 0, 0, 0)
    version = (
        db.query(AutoTrainingPlanVersion).filter_by(id=plan.current_version_id).first()
    )
    config = _loads(version.config_json if version else None, {})
    minimum = int(
        config.get("data_admission", {}).get("min_eligible_samples_per_task", 50)
    )
    reserved_sample_ids = _reserved_evaluation_sample_ids(db, config=config)
    rows = (
        db.query(TaskSample, SampleAnnotationScope)
        .outerjoin(
            SampleAnnotationScope,
            SampleAnnotationScope.sample_id == TaskSample.id,
        )
        .filter(TaskSample.task_id == task.id)
        .order_by(TaskSample.id)
        .all()
    )
    candidates: list[EligibilityCandidate] = []
    materialized: dict[int, dict[str, Any]] = {}
    for sample, scope in rows:
        parsed = _parse_sample_annotation(sample)
        outer = parsed[0] if parsed else None
        current_annotation_sha = annotation_sha256(outer) if outer is not None else None
        contract_matches = bool(
            scope
            and scope.contract_id == contract.id
            and scope.completion_status == "complete"
            and scope.annotation_sha256 == current_annotation_sha
        )
        file_path = None
        if sample.file and sample.file.path:
            file_path = Path(settings.MEDIA_ROOT) / str(sample.file.path).lstrip("/\\")
        image_sha = _file_sha256(file_path)
        duplicate, duplicate_group_id, fingerprint_sha = _duplicate_info(
            db, sample_id=sample.id
        )
        image_sha = fingerprint_sha or image_sha
        authorized, grant_id = _authorized(
            db, plan=plan, task=task, sample_id=sample.id
        )
        review = None
        review_hash = None
        source_type = "internal"
        if str(task.source_type or "internal") == "third_party":
            source_type = "third_party_auto"
            if current_annotation_sha:
                review = current_approved_review(db, sample=sample)
                if review is None:
                    previous_review = (
                        db.query(SampleHumanReview)
                        .filter(
                            SampleHumanReview.sample_id == sample.id,
                            SampleHumanReview.review_status == "APPROVED",
                            SampleHumanReview.invalidated_at.is_(None),
                        )
                        .order_by(
                            SampleHumanReview.reviewed_at.desc(),
                            SampleHumanReview.id.desc(),
                        )
                        .first()
                    )
                    if (
                        previous_review
                        and previous_review.annotation_sha256 != current_annotation_sha
                    ):
                        review_hash = previous_review.annotation_sha256
        candidates.append(
            EligibilityCandidate(
                sample_id=sample.id,
                task_id=task.id,
                state=str(sample.state or ""),
                deleted=sample.deleted_at is not None,
                readable=image_sha is not None,
                annotation_valid=_annotation_valid(parsed),
                contract_matches=contract_matches,
                authorized=authorized,
                withdrawn=_withdrawn(db, task_id=task.id, sample_id=sample.id),
                duplicate=duplicate,
                source_type=source_type,
                annotation_sha256=current_annotation_sha or "",
                reviewed_sha256=(review.annotation_sha256 if review else review_hash),
                reserved_for_evaluation=sample.id in reserved_sample_ids,
            )
        )
        materialized[sample.id] = {
            "annotation_sha256": current_annotation_sha,
            "image_sha256": image_sha,
            "sample_signature": _pool_signature(
                sample_id=sample.id,
                image_sha=image_sha,
                annotation_sha=current_annotation_sha,
                contract_sha=contract.label_set_signature,
            ),
            "review_id": review.id if review else None,
            "grant_id": grant_id,
            "duplicate_group_id": duplicate_group_id,
        }
    result = evaluate_task_candidates(candidates, min_task_samples=minimum)
    decisions = {item.sample_id: item for item in result.decisions}
    now = datetime.now()
    pending_review = excluded = 0
    pool_changed = False
    for sample, _scope in rows:
        decision = decisions[sample.id]
        if decision.reason_code in {"HUMAN_REVIEW_REQUIRED", "HUMAN_REVIEW_STALE"}:
            pending_review += 1
        elif not decision.eligible:
            excluded += 1
        pool = (
            db.query(AutoTrainingSamplePool)
            .filter_by(plan_id=plan.id, sample_id=sample.id)
            .first()
        )
        if pool is None:
            pool = AutoTrainingSamplePool(
                plan_id=plan.id, task_id=task.id, sample_id=sample.id
            )
            db.add(pool)
            before_pool = None
        else:
            before_pool = (
                pool.pool_status,
                pool.reason_code,
                pool.annotation_sha256,
                pool.image_sha256,
                pool.sample_signature,
                pool.human_review_id,
                pool.data_grant_id,
                pool.duplicate_group_id,
            )
        details = materialized[sample.id]
        pool.pool_status = "ELIGIBLE" if decision.eligible else "EXCLUDED"
        pool.reason_code = decision.reason_code
        pool.annotation_sha256 = details["annotation_sha256"]
        pool.image_sha256 = details["image_sha256"]
        pool.sample_signature = details["sample_signature"]
        pool.label_contract_signature = contract.label_set_signature
        pool.human_review_id = details["review_id"]
        pool.data_grant_id = details["grant_id"]
        pool.duplicate_group_id = details["duplicate_group_id"]
        pool.eligible_since = pool.eligible_since or now if decision.eligible else None
        pool.updated_at = now
        after_pool = (
            pool.pool_status,
            pool.reason_code,
            pool.annotation_sha256,
            pool.image_sha256,
            pool.sample_signature,
            pool.human_review_id,
            pool.data_grant_id,
            pool.duplicate_group_id,
        )
        pool_changed = pool_changed or before_pool != after_pool
    scope_row = (
        db.query(AutoTrainingTaskScope)
        .filter_by(plan_id=plan.id, task_id=task.id)
        .first()
    )
    if scope_row is None:
        scope_row = AutoTrainingTaskScope(plan_id=plan.id, task_id=task.id)
        db.add(scope_row)
    eligible_count = sum(1 for item in result.decisions if item.eligible)
    scope_row.scope_status = "QUALIFIED" if result.qualified else "WATCHING"
    scope_row.inclusion_source = scope_row.inclusion_source or "FUTURE_MATCH"
    scope_row.eligible_sample_count = eligible_count
    scope_row.pending_review_count = pending_review
    scope_row.excluded_sample_count = excluded
    scope_row.contract_signature = contract.label_set_signature
    scope_row.reason_code = (
        "QUALIFIED"
        if result.qualified
        else "HUMAN_REVIEW_REQUIRED" if pending_review else "TASK_MINIMUM_NOT_REACHED"
    )
    scope_row.updated_at = now
    if pool_changed:
        plan.last_qualified_change_at = now
        max_wait_hours = float(
            config.get("trigger_policy", {}).get("max_wait_hours") or 0
        )
        if max_wait_hours > 0 and plan.next_evaluation_at is None:
            plan.next_evaluation_at = now + timedelta(hours=max_wait_hours)
    if plan.active_round_id is None and plan.status not in {
        "AUTO_QUALITY_REACHED",
        "PAUSED_MANUAL",
        "PAUSED_SAFETY",
        "DISABLED",
        "ARCHIVED",
    }:
        plan.status = (
            "ACTIVE"
            if result.qualified
            else "WAITING_FOR_REVIEW" if pending_review else "WAITING_FOR_COVERAGE"
        )
    db.flush()
    if (
        result.qualified
        and plan.status != "AUTO_QUALITY_REACHED"
        and plan.active_round_id is None
    ):
        signature = hashlib.sha256(
            "|".join(
                sorted(
                    materialized[item.sample_id]["sample_signature"]
                    for item in result.decisions
                    if item.eligible
                )
            ).encode("utf-8")
        ).hexdigest()
        enqueue_auto_training_event(
            db,
            event_name="auto.plan.round.evaluate.v1",
            aggregate_type="auto_training_plan",
            aggregate_id=str(plan.id),
            payload={
                "plan_id": plan.id,
                "task_id": task.id,
                "dataset_signature": signature,
            },
            idempotency_key=f"plan:{plan.id}:round-evaluate:{signature}",
        )
    return TaskReconcileSummary(
        plan.id,
        task.id,
        True,
        result.qualified,
        eligible_count,
        pending_review,
        excluded,
    )


def _candidate_plans(db: Session, task: Task) -> list[AutoTrainingPlan]:
    return (
        db.query(AutoTrainingPlan)
        .filter(
            AutoTrainingPlan.deleted_at.is_(None),
            AutoTrainingPlan.status.in_(RECONCILABLE_PLAN_STATUSES),
            AutoTrainingPlan.dataset_category_id == task.dataset_category_id,
        )
        .all()
    )


def _historical_tasks(db: Session, plan: AutoTrainingPlan) -> list[Task]:
    version = (
        db.query(AutoTrainingPlanVersion).filter_by(id=plan.current_version_id).first()
    )
    config = _loads(version.config_json if version else None, {})
    admission = config.get("data_admission", {})
    if not admission.get("include_historical_data"):
        return []
    query = db.query(Task).filter(Task.deleted_at.is_(None))
    task_ids = [int(value) for value in admission.get("history_task_ids") or []]
    if task_ids:
        query = query.filter(Task.id.in_(task_ids))
    elif admission.get("history_started_at"):
        query = query.filter(
            Task.created_at >= datetime.fromisoformat(admission["history_started_at"])
        )
    return query.order_by(Task.id).all()


def _create_plan_for_training_ready_task(
    db: Session, *, task: Task
) -> dict[str, Any] | None:
    """Create one editable plan for a newly ready dataset contract."""
    if (
        not task.training_ready
        or task.media_type != "IMAGE"
        or not task.dataset_category_id
        or not task.created_by
    ):
        return None
    contract = _task_contract(db, task)
    if contract is None or contract.status not in {"active", "approved"}:
        return None

    owner = db.query(User).filter(User.id == int(task.created_by)).first()
    if owner is None:
        return None
    series_type = infer_series_type_for_task(task)
    existing = find_plan_for_contract(
        db,
        owner_id=owner.id,
        series_type=series_type,
        dataset_category_id=int(task.dataset_category_id),
        label_set_signature=contract.label_set_signature,
        class_order_signature=contract.class_order_signature,
    )
    if existing is not None:
        return plan_payload(db, existing, owner, detail=True)

    task_ids: list[int] = []
    candidates = (
        db.query(Task)
        .filter(
            Task.deleted_at.is_(None),
            Task.media_type == "IMAGE",
            Task.created_by == owner.id,
            Task.dataset_category_id == int(task.dataset_category_id),
            Task.training_ready.is_(True),
        )
        .order_by(Task.id.asc())
        .all()
    )
    for candidate in candidates:
        if not user_has_task_access(owner, candidate):
            continue
        candidate_contract = _task_contract(db, candidate)
        if (
            candidate_contract is None
            or candidate_contract.status not in {"active", "approved"}
            or candidate_contract.label_set_signature != contract.label_set_signature
            or candidate_contract.class_order_signature
            != contract.class_order_signature
            or infer_series_type_for_task(candidate) != series_type
        ):
            continue
        task_ids.append(int(candidate.id))
    if int(task.id) not in task_ids:
        task_ids.append(int(task.id))

    category = (
        db.query(DatasetCategory)
        .filter(
            DatasetCategory.id == int(task.dataset_category_id),
            DatasetCategory.deleted_at.is_(None),
        )
        .first()
    )
    category_name = (category.name or "").strip() if category else ""
    if not category_name:
        category_name = f"数据集分类{int(task.dataset_category_id)}"
    existing_names = {
        str(name)
        for (name,) in db.query(AutoTrainingPlan.name)
        .filter(
            AutoTrainingPlan.owner_id == owner.id,
            AutoTrainingPlan.dataset_category_id == int(task.dataset_category_id),
            AutoTrainingPlan.deleted_at.is_(None),
        )
        .all()
    }
    plan_name = category_name
    suffix = 2
    while plan_name in existing_names:
        plan_name = f"{category_name}（{suffix}）"
        suffix += 1

    plan_data = {
        "name": plan_name,
        "series_type": series_type,
        "dataset_category_id": int(task.dataset_category_id),
        "label_set_signature": contract.label_set_signature,
        "class_order_signature": contract.class_order_signature,
        "config": default_plan_config_for_task(task_ids),
    }
    return create_plan(
        db,
        user=owner,
        data=plan_data,
        change_reason="created automatically from a training-ready dataset",
    )


def discover_training_ready_plans(db: Session, *, limit: int | None = None) -> int:
    """Create missing editable plans for historical and future ready image tasks."""
    ensure_auto_training_plan_writes_open()
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")

    required_tables = {
        "auto_training_plan",
        "auto_training_plan_version",
        "task_annotation_contract",
        "training_evaluation_dataset",
        "training_evaluation_sample",
    }
    inspector = inspect(db.get_bind())
    if not all(inspector.has_table(name) for name in required_tables):
        return 0

    candidates = (
        db.query(Task.id)
        .filter(
            Task.deleted_at.is_(None),
            Task.media_type == "IMAGE",
            Task.training_ready.is_(True),
            Task.dataset_category_id.isnot(None),
            Task.created_by.isnot(None),
        )
        .order_by(Task.id.asc())
    )
    if limit is not None:
        candidates = candidates.limit(limit)
    candidate_ids = [int(task_id) for (task_id,) in candidates.all()]
    db.rollback()

    created = 0
    for task_id in candidate_ids:
        try:
            with begin_transaction(db):
                task = db.query(Task).filter_by(id=task_id).one_or_none()
                if task is None:
                    continue
                contract = _task_contract(db, task)
                owner = (
                    db.query(User).filter(User.id == int(task.created_by)).first()
                    if task.created_by
                    else None
                )
                if (
                    contract is None
                    or contract.status not in {"active", "approved"}
                    or owner is None
                ):
                    continue
                existing = find_plan_for_contract(
                    db,
                    owner_id=owner.id,
                    series_type=infer_series_type_for_task(task),
                    dataset_category_id=int(task.dataset_category_id),
                    label_set_signature=contract.label_set_signature,
                    class_order_signature=contract.class_order_signature,
                )
                result = _create_plan_for_training_ready_task(db, task=task)
                if existing is None and result is not None:
                    created += 1
        except Exception:
            db.rollback()
            logger.exception(
                "automatic training plan discovery failed for task {}", task_id
            )
    return created


def reconcile_plan_owned_evaluation_datasets(
    db: Session, *, limit: int | None = None
) -> int:
    """Refresh and approve generated plan-owned evaluation dataset drafts."""
    ensure_auto_training_plan_writes_open()
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    inspector = inspect(db.get_bind())
    if not inspector.has_table("training_evaluation_dataset"):
        return 0

    query = (
        db.query(TrainingEvaluationDataset.plan_id)
        .filter(
            TrainingEvaluationDataset.plan_id.isnot(None),
            TrainingEvaluationDataset.status.in_(("DRAFT", "PENDING_APPROVAL")),
        )
        .distinct()
        .order_by(TrainingEvaluationDataset.plan_id.asc())
    )
    if limit is not None:
        query = query.limit(limit)
    plan_ids = [int(plan_id) for (plan_id,) in query.all()]
    db.rollback()

    approved = 0
    for plan_id in plan_ids:
        try:
            with begin_transaction(db):
                plan = db.query(AutoTrainingPlan).filter_by(id=plan_id).one_or_none()
                owner = (
                    db.query(User).filter(User.id == int(plan.owner_id)).first()
                    if plan is not None
                    else None
                )
                if plan is None or owner is None:
                    continue
                rows = (
                    db.query(TrainingEvaluationDataset)
                    .filter(
                        TrainingEvaluationDataset.plan_id == plan_id,
                        TrainingEvaluationDataset.status.in_(
                            ("DRAFT", "PENDING_APPROVAL")
                        ),
                    )
                    .order_by(TrainingEvaluationDataset.id.asc())
                    .all()
                )
                generated = [
                    row
                    for row in rows
                    if _loads(row.grouping_policy_json, {}).get("auto_generation")
                ]
                if not generated:
                    continue
                before = (
                    db.query(TrainingEvaluationDataset)
                    .filter(
                        TrainingEvaluationDataset.plan_id == plan_id,
                        TrainingEvaluationDataset.status == "APPROVED",
                    )
                    .count()
                )
                if any(row.status == "DRAFT" for row in generated):
                    try:
                        auto_generate_evaluation_datasets(
                            db,
                            user=owner,
                            data={
                                "plan_id": plan_id,
                                "series_type": plan.series_type,
                                "dataset_category_id": int(plan.dataset_category_id),
                                "label_set_signature": plan.label_set_signature,
                                "grouping_policy": {
                                    "identity_isolation": True,
                                    "capture_isolation": True,
                                },
                                "name_prefix": f"plan-{plan_id}-evaluation",
                            },
                        )
                    except LabelUException:
                        for row in generated:
                            if row.status != "DRAFT":
                                continue
                            _auto_transition_generated_evaluation_dataset(
                                db, user=owner, row=row
                            )
                for row in generated:
                    if row.status == "PENDING_APPROVAL":
                        transition_evaluation_dataset(
                            db,
                            user=owner,
                            dataset_id=int(row.id),
                            target="APPROVED",
                            reason="system completed plan evaluation approval",
                        )
                db.flush()
                after = (
                    db.query(TrainingEvaluationDataset)
                    .filter(
                        TrainingEvaluationDataset.plan_id == plan_id,
                        TrainingEvaluationDataset.status == "APPROVED",
                    )
                    .count()
                )
                approved += max(0, after - before)
        except Exception:
            db.rollback()
            logger.exception(
                "automatic evaluation dataset approval failed for plan {}", plan_id
            )
    return approved


def _auto_transition_generated_evaluation_dataset(
    db: Session, *, user: User, row: TrainingEvaluationDataset
) -> None:
    transition_evaluation_dataset(
        db,
        user=user,
        dataset_id=int(row.id),
        target="PENDING_APPROVAL",
        reason="system accepted the generated evaluation manifest",
    )
    transition_evaluation_dataset(
        db,
        user=user,
        dataset_id=int(row.id),
        target="APPROVED",
        reason="system completed plan evaluation approval",
    )


def handle_outbox_event(
    db: Session,
    row: AutoTrainingOutbox,
    *,
    fencing_check=None,
) -> None:
    payload = _loads(row.payload_json, {})
    from labelu.internal.common.auto_training_runtime import (
        load_auto_training_switches,
    )
    from labelu.internal.application.service.auto_training_outbox import (
        OutboxDeferredError,
    )

    switches = load_auto_training_switches()
    if row.event_name == "training.worker.result.v1":
        from labelu.internal.application.service.training_worker_persistence import (
            persist_training_worker_result,
        )

        persist_training_worker_result(
            db,
            job_id=int(payload["job_id"]),
            result=payload,
            fencing_check=fencing_check,
        )
        return
    if switches.get("observe_only", False):
        raise OutboxDeferredError(
            "auto training is in observe-only mode", delay_seconds=60
        )
    if row.event_name == "auto.plan.config.approved.v1":
        if not switches.get("pool_enabled", False):
            raise OutboxDeferredError(
                "auto training pool is disabled", delay_seconds=60
            )
        plan = db.query(AutoTrainingPlan).filter_by(id=int(payload["plan_id"])).one()
        for task in _historical_tasks(db, plan):
            reconcile_task_for_plan(db, plan=plan, task=task)
        return
    if row.event_name == "task.training_ready.v1":
        task = db.query(Task).filter_by(id=int(payload["task_id"])).one()
        _create_plan_for_training_ready_task(db, task=task)
        return
    if row.event_name in {
        "sample.annotation.changed.v1",
        "sample.review.changed.v1",
        "task.created.v1",
    }:
        if not switches.get("pool_enabled", False):
            raise OutboxDeferredError(
                "auto training pool is disabled", delay_seconds=60
            )
        task_id = int(payload["task_id"])
        task = db.query(Task).filter_by(id=task_id).one()
        for plan in _candidate_plans(db, task):
            reconcile_task_for_plan(db, plan=plan, task=task)
        return
    if row.event_name == "auto.plan.reconcile.requested.v1":
        if not switches.get("pool_enabled", False):
            raise OutboxDeferredError(
                "auto training pool is disabled", delay_seconds=60
            )
        plan = db.query(AutoTrainingPlan).filter_by(id=int(payload["plan_id"])).one()
        tasks = db.query(Task).filter(Task.deleted_at.is_(None)).order_by(Task.id).all()
        for task in tasks:
            reconcile_task_for_plan(db, plan=plan, task=task)
        return
    if row.event_name == "auto.plan.round.evaluate.v1":
        if not switches.get("scheduler_enabled", False):
            raise OutboxDeferredError(
                "auto training scheduler is disabled", delay_seconds=60
            )
        from labelu.internal.application.service.auto_training_scheduler import (
            evaluate_plan_trigger,
        )

        evaluate_plan_trigger(db, plan_id=int(payload["plan_id"]))
        return
    if row.event_name == "auto.plan.scan.preview.requested.v1":
        return
    if row.event_name == "auto.round.stage.completed.v1":
        return
    if row.event_name == "auto.round.train.dispatch.v1":
        from labelu.internal.application.service.auto_training_dispatch import (
            dispatch_training_job,
        )

        dispatch_training_job(db, job_id=int(payload["job_id"]))
        return
    if row.event_name == "auto.round.quality.evaluate.v1":
        from labelu.internal.application.service.auto_training_evaluator import (
            materialize_evaluation_request,
        )

        materialize_evaluation_request(
            db,
            round_id=int(payload["round_id"]),
            job_id=int(payload["job_id"]),
        )
        return
    if row.event_name == "auto.round.bm1688.requested.v1":
        if not switches.get("bm1688_enabled", False):
            raise OutboxDeferredError("automatic BM1688 is disabled", delay_seconds=60)
        from labelu.internal.application.service.auto_training_pipeline_runtime import (
            materialize_bm1688_export,
        )

        materialize_bm1688_export(
            db,
            round_id=int(payload["round_id"]),
            origin=str(payload["origin"]),
            reason=payload.get("reason"),
        )
        return
    if row.event_name == "auto.round.bm1688.execute.v1":
        from labelu.internal.application.service.auto_training_dispatch import (
            dispatch_bm1688_export,
        )

        dispatch_bm1688_export(db, export_id=int(payload["export_id"]))
        return
    if row.event_name == "auto.round.edge.requested.v1":
        if not switches.get("edge_validation_enabled", False):
            raise OutboxDeferredError(
                "automatic edge validation is disabled", delay_seconds=60
            )
        from labelu.internal.application.service.auto_training_pipeline_runtime import (
            materialize_edge_validation_job,
        )

        materialize_edge_validation_job(
            db,
            round_id=int(payload["round_id"]),
            export_id=int(payload["export_id"]),
            origin=str(payload["origin"]),
        )
        return
    if row.event_name == "auto.round.edge.execute.v1":
        from labelu.internal.application.service.auto_training_dispatch import (
            dispatch_edge_validation_job,
        )

        dispatch_edge_validation_job(db, edge_job_id=int(payload["edge_job_id"]))
        return
    raise ValueError(f"unsupported auto training event: {row.event_name}")
