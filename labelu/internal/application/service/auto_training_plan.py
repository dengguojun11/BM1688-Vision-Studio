from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from fastapi import status
from sqlalchemy import String, cast, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_auto_training
from labelu.internal.common.auto_training_runtime import (
    AUTO_TRAINING_SWITCH_DEFAULTS,
    load_auto_training_switches,
)
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.label_contract import (
    annotation_sha256 as canonical_annotation_sha256,
)
from labelu.internal.common.plate_recognition_profile import (
    task_config_has_plate_recognition_marker,
    task_config_matches_profile,
)
from labelu.internal.common.system_config_provider import write_group_values
from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.common.task_access import is_task_superuser, user_has_task_access
from labelu.internal.domain.models.auto_training import (
    AutoTrainingAuditEvent,
    AutoTrainingEvaluationRun,
    AutoTrainingOutbox,
    AutoTrainingPlan,
    AutoTrainingPlanVersion,
    AutoTrainingRound,
    AutoTrainingRoundSample,
    AutoTrainingSamplePool,
    AutoTrainingTaskScope,
    SampleHumanReview,
)
from labelu.internal.domain.models.auto_training_governance import (
    QualityPolicyApproval,
    SampleAssetFingerprint,
    TrainingEvaluationDataset,
    TrainingEvaluationSample,
)
from labelu.internal.domain.models.annotation_contract import (
    SampleAnnotationScope,
    TaskAnnotationContract,
)
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.project import Project
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob
from labelu.internal.application.service.training_review_gate import (
    has_human_annotation_edit,
    task_requires_human_review,
)

from .auto_training_state_machine import (
    AutoTrainingConflict,
    PlanStatus,
    RoundStatus,
    assert_plan_transition,
)


def ensure_auto_training_plan_writes_open() -> None:
    if not settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
        raise LabelUException(
            ErrorCode.CODE_57122_AUTO_TRAIN_PLAN_NOT_OPEN,
            status_code=status.HTTP_403_FORBIDDEN,
        )

SERIES_TYPES = {"yolo_detection", "plate_detection", "plate_recognition"}
AUTO_EVALUATION_KINDS = (
    "VALIDATION",
    "BENCHMARK",
    "SEALED_ACCEPTANCE",
    "BM1688_CALIBRATION",
    "EDGE_VALIDATION",
)
AUTO_EVALUATION_SPLIT_ROLES = {
    "VALIDATION": "validation",
    "BENCHMARK": "benchmark",
    "SEALED_ACCEPTANCE": "sealed",
    "BM1688_CALIBRATION": "calibration",
    "EDGE_VALIDATION": "edge_validation",
}
AUTO_EVALUATION_BINDING_KEYS = {
    "VALIDATION": "validation_dataset_id",
    "BENCHMARK": "benchmark_dataset_id",
    "SEALED_ACCEPTANCE": "sealed_acceptance_dataset_id",
    "EDGE_VALIDATION": "edge_validation_dataset_id",
    "BM1688_CALIBRATION": "calibration_dataset_id",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _sha(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    missing: list[str] = []
    errors: list[str] = []

    def require(path: str):
        node: Any = config
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                missing.append(path)
                return None
            node = node[part]
        return node

    minimum = require("data_admission.min_eligible_samples_per_task")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 50:
        if "data_admission.min_eligible_samples_per_task" not in missing:
            missing.append("data_admission.min_eligible_samples_per_task")
        errors.append("单任务最少合格图片数必须为不小于 50 的整数")
    include_history = require("data_admission.include_historical_data")
    if include_history:
        task_ids = config.get("data_admission", {}).get("history_task_ids")
        started_at = config.get("data_admission", {}).get("history_started_at")
        if not task_ids and not started_at:
            missing.append("data_admission.history_task_ids_or_started_at")
        if task_ids and (
            not isinstance(task_ids, list)
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
                for value in task_ids
            )
        ):
            errors.append("历史任务 ID 必须是正整数列表")
        if config.get("data_admission", {}).get("history_confirmed") is not True:
            missing.append("data_admission.history_confirmed")

    trigger_paths = [
        require("trigger_policy.min_new_samples"),
        require("trigger_policy.min_growth_ratio"),
        require("trigger_policy.max_wait_hours"),
    ]
    require("trigger_policy.weak_label_min_samples")
    if not any(
        isinstance(value, (int, float)) and value > 0 for value in trigger_paths
    ):
        errors.append("至少配置一种大于 0 的轮次触发条件")
    if any(isinstance(value, (int, float)) and value < 0 for value in trigger_paths):
        errors.append("轮次触发条件不能为负数")

    stop_threshold = require("auto_stop.overall_recall_threshold")
    if not isinstance(stop_threshold, (int, float)) or isinstance(stop_threshold, bool):
        errors.append("自动停止整体召回率阈值必须是数值")
    elif float(stop_threshold) != 0.95:
        errors.append("自动停止整体召回率阈值固定为 0.95")

    for path in (
        "training_policy.max_auto_tuning_rounds",
        "training_policy.max_no_improvement_rounds",
        "training_policy.max_technical_failures",
        "training_policy.cooldown_hours",
        "training_policy.priority",
        "training_policy.daily_gpu_budget_minutes",
        "training_policy.weekly_gpu_budget_minutes",
        "quality_policy_binding.policy_name",
        "quality_policy_binding.version",
        "quality_policy_binding.signature",
        "quality_policy_binding.approval_id",
        "evaluation_binding.validation_dataset_id",
        "evaluation_binding.benchmark_dataset_id",
        "evaluation_binding.sealed_acceptance_dataset_id",
        "evaluation_binding.edge_validation_dataset_id",
        "bm1688_policy.enabled",
        "bm1688_policy.compile_final_quality_candidate",
        "bm1688_policy.calibration_dataset_id",
        "bm1688_policy.max_pt_to_bmodel_metric_drop",
        "bm1688_policy.technical_retry_limit",
        "edge_validation_policy.enabled",
        "edge_validation_policy.device_pool",
        "edge_validation_policy.mode",
        "edge_validation_policy.minimum_valid_inferences",
        "edge_validation_policy.timeout_seconds",
        "edge_validation_policy.technical_retry_limit",
        "retention_policy",
        "callback_policy",
    ):
        require(path)
    if config.get("training_policy", {}).get("max_auto_tuning_rounds") != 0:
        errors.append("自动计划禁止在相同数据上自动调参或重复训练")
    if (
        config.get("bm1688_policy", {}).get("compile_final_quality_candidate")
        is not True
    ):
        errors.append("质量达标后必须自动创建 BM1688 编译")
    if config.get("bm1688_policy", {}).get("enabled") is not True:
        errors.append("完整自动迭代计划必须启用 BM1688 阶段")
    if (
        config.get("bm1688_policy", {}).get("allow_manual_diagnostic_compile")
        is not True
    ):
        errors.append("必须保留管理员诊断编译入口")
    if config.get("edge_validation_policy", {}).get("enabled") is not True:
        errors.append("完整自动迭代计划必须启用边端验证阶段")
    if config.get("edge_validation_policy", {}).get("mode") == "run_only":
        errors.append("run_only 仅可诊断，不能作为自动发布就绪路径")
    return {
        "config_complete": not missing and not errors,
        "missing_fields": sorted(set(missing)),
        "suggested_values": {
            "data_admission.min_eligible_samples_per_task": 50,
            "auto_stop.overall_recall_threshold": 0.95,
        },
        "validation_errors": errors,
        "affected_task_count": 0,
        "qualified_task_count": 0,
        "eligible_sample_count": 0,
        "pending_review_count": 0,
        "excluded_reason_counts": {},
        "estimated_gpu_minutes": None,
        "would_create_round": False,
        "blocking_reasons": sorted(set(missing)) + errors,
    }


def validate_selected_task_scope(
    db: Session, *, user: User, data: dict[str, Any]
) -> list[Task]:
    """Validate and normalize explicitly selected existing tasks for a plan."""
    config = data.get("config") if isinstance(data, dict) else None
    admission = config.get("data_admission") if isinstance(config, dict) else None
    if not isinstance(admission, dict) or not admission.get("include_historical_data"):
        return []
    raw_task_ids = admission.get("history_task_ids")
    if not raw_task_ids:
        return []
    if not isinstance(raw_task_ids, list) or any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in raw_task_ids
    ):
        raise LabelUException(
            ErrorCode.CODE_57101_AUTO_TRAIN_PLAN_CONFIG_INCOMPLETE,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="已选任务 ID 必须是正整数列表",
        )
    task_ids = list(dict.fromkeys(raw_task_ids))
    if len(task_ids) > 500:
        raise LabelUException(
            ErrorCode.CODE_57101_AUTO_TRAIN_PLAN_CONFIG_INCOMPLETE,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="单个自动训练计划最多选择 500 个代表任务",
        )
    admission["history_task_ids"] = task_ids
    rows = db.query(Task).filter(Task.id.in_(task_ids), Task.deleted_at.is_(None)).all()
    rows_by_id = {int(row.id): row for row in rows}
    missing_ids = [task_id for task_id in task_ids if task_id not in rows_by_id]
    if missing_ids:
        raise LabelUException(
            ErrorCode.CODE_57101_AUTO_TRAIN_PLAN_CONFIG_INCOMPLETE,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="代表任务不存在或已删除：" + ", ".join(map(str, missing_ids)),
        )
    ordered_rows = [rows_by_id[task_id] for task_id in task_ids]
    for task in ordered_rows:
        if not user_has_task_access(user, task):
            message = f"无权使用代表任务 #{task.id}"
        elif task.media_type != "IMAGE":
            message = f"代表任务 #{task.id} 不是图像任务"
        elif int(task.dataset_category_id or 0) != int(
            data.get("dataset_category_id") or 0
        ):
            message = f"代表任务 #{task.id} 的数据集分类与计划不一致"
        else:
            contract = (
                db.query(TaskAnnotationContract)
                .filter(
                    TaskAnnotationContract.id == task.current_annotation_contract_id,
                    TaskAnnotationContract.task_id == task.id,
                    TaskAnnotationContract.status.in_(("active", "approved")),
                )
                .first()
            )
            if contract is None:
                message = f"代表任务 #{task.id} 缺少有效标签契约"
            elif contract.label_set_signature != data.get(
                "label_set_signature"
            ) or contract.class_order_signature != data.get("class_order_signature"):
                message = f"代表任务 #{task.id} 的标签契约与计划不一致"
            else:
                message = ""
        if message:
            raise LabelUException(
                ErrorCode.CODE_57101_AUTO_TRAIN_PLAN_CONFIG_INCOMPLETE,
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message=message,
            )
    return ordered_rows


def preview_plan(config: dict[str, Any]) -> dict[str, Any]:
    ensure_auto_training_plan_writes_open()
    return validate_config(config)


def _raise_not_found():
    raise LabelUException(
        ErrorCode.CODE_57100_AUTO_TRAIN_PLAN_NOT_FOUND,
        status_code=status.HTTP_404_NOT_FOUND,
    )


def _raise_conflict(message: str):
    raise LabelUException(
        ErrorCode.CODE_57102_AUTO_TRAIN_PLAN_CONFLICT,
        status_code=status.HTTP_409_CONFLICT,
        message=message,
    )


def _project_access(
    db: Session, project_id: int | None, user: User, *, manage: bool = False
) -> bool:
    if not project_id:
        return False
    if is_task_superuser(user):
        return True
    from labelu.internal.adapter.persistence import crud_project

    project = crud_project.get(db, int(project_id))
    if project is None:
        return False
    if int(project.owner_id) == int(user.id):
        return True
    member = crud_project.get_member(db, int(project_id), int(user.id))
    if not member or member.status != "ACTIVE":
        return False
    return (not manage) or member.role in {"admin", "owner"}


def _assert_visible(
    db: Session, plan: AutoTrainingPlan | None, user: User
) -> AutoTrainingPlan:
    if plan is None:
        _raise_not_found()
    if (
        plan.owner_id != user.id
        and not is_task_superuser(user)
        and not _project_access(db, plan.project_id, user)
    ):
        _raise_not_found()
    return plan


def _assert_manage(
    db: Session, plan: AutoTrainingPlan | None, user: User
) -> AutoTrainingPlan:
    plan = _assert_visible(db, plan, user)
    # Legacy plans predate project ownership and remain manageable by their
    # recorded owner while the migration backfill is intentionally nullable.
    if plan.owner_id == user.id or is_task_superuser(user):
        return plan
    if not _project_access(db, plan.project_id, user, manage=True):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    return plan


def _assert_version(plan: AutoTrainingPlan, expected_version: int):
    if plan.version != expected_version:
        _raise_conflict(f"版本冲突，当前版本为 {plan.version}")


def _audit(
    db: Session,
    *,
    plan: AutoTrainingPlan,
    actor: User | None,
    action: str,
    before: dict | None,
    after: dict | None,
    reason: str | None,
    round_id: int | None = None,
):
    payload = {
        "plan_id": plan.id,
        "round_id": round_id,
        "actor_id": getattr(actor, "id", None),
        "action": action,
        "before": before,
        "after": after,
        "reason": reason,
        "version": plan.version,
        "created_at": datetime.now().isoformat(),
    }
    db.add(
        AutoTrainingAuditEvent(
            plan_id=plan.id,
            round_id=round_id,
            actor_type="USER" if actor else "SYSTEM",
            actor_id=getattr(actor, "id", None),
            action=action,
            before_json=_json(before) if before is not None else None,
            after_json=_json(after) if after is not None else None,
            reason=reason,
            event_sha256=_sha(payload),
        )
    )


def _enqueue(
    db: Session,
    *,
    event_name: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict,
    idempotency_key: str,
):
    existing = (
        db.query(AutoTrainingOutbox)
        .filter(AutoTrainingOutbox.idempotency_key == idempotency_key)
        .first()
    )
    if existing:
        return existing
    row = AutoTrainingOutbox(
        event_name=event_name,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        payload_json=_json(payload),
        idempotency_key=idempotency_key,
        status="PENDING",
        available_at=datetime.now(),
        max_attempts=5,
    )
    db.add(row)
    db.flush()
    return row


def _version_payload(row: AutoTrainingPlanVersion) -> dict:
    return {
        "id": row.id,
        "plan_id": row.plan_id,
        "version_no": row.version_no,
        "status": row.status,
        "config": _loads(row.config_json, {}),
        "config_sha256": row.config_sha256,
        "quality_policy_signature": row.quality_policy_signature,
        "evaluation_binding": _loads(row.evaluation_binding_json, {}),
        "missing_fields": _loads(row.missing_fields_json, []),
        "validation_errors": _loads(row.validation_errors_json, []),
        "change_reason": row.change_reason,
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "created_at": row.created_at,
        "approved_at": row.approved_at,
    }


def plan_payload(
    db: Session, plan: AutoTrainingPlan, user: User, *, detail=False
) -> dict:
    counts = (
        db.query(
            AutoTrainingTaskScope.scope_status,
            AutoTrainingTaskScope.eligible_sample_count,
            AutoTrainingTaskScope.pending_review_count,
        )
        .filter(AutoTrainingTaskScope.plan_id == plan.id)
        .all()
    )
    current = (
        crud_auto_training.get_version(db, plan.current_version_id)
        if plan.current_version_id
        else None
    )
    active_round = (
        db.query(AutoTrainingRound).filter_by(id=plan.active_round_id).first()
        if plan.active_round_id
        else None
    )
    dataset_category = (
        db.query(DatasetCategory)
        .filter(
            DatasetCategory.id == int(plan.dataset_category_id),
            DatasetCategory.deleted_at.is_(None),
        )
        .first()
    )
    payload = {
        "id": plan.id,
        "project_id": plan.project_id,
        "source_dataset_version_id": plan.source_dataset_version_id,
        "name": plan.name,
        "series_type": plan.series_type,
        "dataset_category_id": plan.dataset_category_id,
        "dataset_category_name": dataset_category.name if dataset_category else None,
        "status": plan.status,
        "status_reason_code": plan.status_reason_code,
        "current_version_no": current.version_no if current else plan.version,
        "qualified_task_count": sum(
            1 for state, _, _ in counts if state == "QUALIFIED"
        ),
        "eligible_sample_count": sum(value or 0 for _, value, _ in counts),
        "pending_review_count": sum(value or 0 for _, _, value in counts),
        "active_round_id": plan.active_round_id,
        "active_round_no": active_round.round_no if active_round else None,
        "execution_stage": active_round.state if active_round else None,
        "champion_job_id": plan.champion_job_id,
        "quality_gap_summary": _loads(plan.status_reason_json, {}),
        "next_evaluation_at": plan.next_evaluation_at,
        "updated_at": plan.updated_at,
        "version": plan.version,
        "permissions": {
            "can_edit": plan.owner_id == user.id or is_task_superuser(user),
            "can_submit": plan.owner_id == user.id or is_task_superuser(user),
            "can_approve": is_task_superuser(user),
            "can_pause": plan.owner_id == user.id or is_task_superuser(user),
            "can_resume": plan.owner_id == user.id or is_task_superuser(user),
            "can_create_round": plan.owner_id == user.id or is_task_superuser(user),
            "can_retry_stage": is_task_superuser(user),
            "can_publish": is_task_superuser(user),
        },
    }
    if detail:
        from labelu.internal.common.auto_training_runtime import (
            load_auto_training_switches,
        )

        last_round = (
            db.query(AutoTrainingRound)
            .filter_by(plan_id=plan.id)
            .order_by(AutoTrainingRound.round_no.desc(), AutoTrainingRound.id.desc())
            .first()
        )
        payload.update(
            {
                "label_set_signature": plan.label_set_signature,
                "class_order_signature": plan.class_order_signature,
                "current_version": _version_payload(current) if current else None,
                # Scheduler leader election is an internal process-governance
                # switch; keep it out of the plan's public technical flags so
                # the legacy response contract remains stable.
                "technical_stage_flags": {
                    key: value
                    for key, value in load_auto_training_switches().items()
                    if key != "scheduler_leader_enabled"
                },
                "last_round": round_payload(last_round) if last_round else None,
                "champion": None,
                "blocking_reasons": [],
                "created_by": plan.created_by,
                "created_at": plan.created_at,
            }
        )
    return payload


def _has_complete_evaluation_binding(config: dict[str, Any]) -> bool:
    evaluation = config.get("evaluation_binding") or {}
    required = {
        "validation_dataset_id",
        "benchmark_dataset_id",
        "sealed_acceptance_dataset_id",
        "edge_validation_dataset_id",
    }
    return bool(
        all(int(evaluation.get(key) or 0) > 0 for key in required)
        and int((config.get("bm1688_policy") or {}).get("calibration_dataset_id") or 0)
        > 0
    )


def infer_series_type_for_task(task: Task) -> str:
    """Map a ready image task to the default automatic-training series."""
    task_config = _loads(task.config, {})
    if task_config_matches_profile(task_config):
        return "plate_recognition"
    if task_config_has_plate_recognition_marker(task_config):
        return "plate_detection"
    return "yolo_detection"


def default_plan_config_for_task(task_ids: list[int]) -> dict[str, Any]:
    """Return an editable default config for a dataset-triggered plan."""
    return {
        "auto_stop": {"overall_recall_threshold": 0.95},
        "data_admission": {
            "min_eligible_samples_per_task": 50,
            "include_historical_data": True,
            "history_task_ids": [int(value) for value in task_ids],
            "history_confirmed": True,
        },
        "trigger_policy": {
            "min_new_samples": 50,
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
        "quality_policy_binding": {},
        "evaluation_binding": {},
        "bm1688_policy": {
            "enabled": True,
            "compile_final_quality_candidate": True,
            "allow_manual_diagnostic_compile": True,
            "calibration_dataset_id": 0,
            "max_pt_to_bmodel_metric_drop": 0.02,
            "technical_retry_limit": 2,
        },
        "edge_validation_policy": {
            "enabled": True,
            "device_pool": [],
            "mode": "run_and_eval",
            "minimum_valid_inferences": 100,
            "max_p95_latency_ms": 80,
            "max_error_rate": 0.01,
            "timeout_seconds": 7200,
            "technical_retry_limit": 2,
        },
        "retention_policy": {"days": 180},
        "callback_policy": {"enabled": False},
    }


def find_plan_for_contract(
    db: Session,
    *,
    owner_id: int,
    series_type: str,
    dataset_category_id: int,
    label_set_signature: str,
    class_order_signature: str,
) -> AutoTrainingPlan | None:
    """Find any non-deleted plan for the same dataset training contract."""
    return (
        db.query(AutoTrainingPlan)
        .filter(
            AutoTrainingPlan.owner_id == int(owner_id),
            AutoTrainingPlan.series_type == series_type,
            AutoTrainingPlan.dataset_category_id == int(dataset_category_id),
            AutoTrainingPlan.label_set_signature == label_set_signature,
            AutoTrainingPlan.class_order_signature == class_order_signature,
            AutoTrainingPlan.deleted_at.is_(None),
        )
        .order_by(AutoTrainingPlan.id.asc())
        .first()
    )


def create_plan(db: Session, *, user: User, data: dict, change_reason: str | None):
    ensure_auto_training_plan_writes_open()
    if data.get("project_id") and not _project_access(
        db, int(data["project_id"]), user, manage=True
    ):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    validate_selected_task_scope(db, user=user, data=data)
    config = _loads(_json(data["config"]), {})
    validation = validate_config(config)
    contract_snapshot = {
        "series_type": data["series_type"],
        "dataset_category_id": data["dataset_category_id"],
        "label_set_signature": data["label_set_signature"],
        "class_order_signature": data["class_order_signature"],
    }
    plan = AutoTrainingPlan(
        project_id=(int(data["project_id"]) if data.get("project_id") else None),
        owner_id=user.id,
        name=data["name"],
        series_type=data["series_type"],
        dataset_category_id=data["dataset_category_id"],
        label_set_signature=data["label_set_signature"],
        class_order_signature=data["class_order_signature"],
        status=(
            PlanStatus.DRAFT_COMPLETE.value
            if validation["config_complete"]
            else PlanStatus.DRAFT_INCOMPLETE.value
        ),
        version=1,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(plan)
    try:
        db.flush()
    except IntegrityError:
        _raise_conflict("同名自动训练计划已存在")
    if not _has_complete_evaluation_binding(config):
        generated = _auto_create_plan_evaluation_datasets(
            db,
            user=user,
            plan=plan,
            series_type=data["series_type"],
            dataset_category_id=int(data["dataset_category_id"]),
            label_set_signature=data["label_set_signature"],
        )
        binding = dict(config.get("evaluation_binding") or {})
        for kind, key in AUTO_EVALUATION_BINDING_KEYS.items():
            binding[key] = int(generated[kind]["id"])
        config["evaluation_binding"] = binding
        bm_policy = dict(config.get("bm1688_policy") or {})
        bm_policy["calibration_dataset_id"] = int(generated["BM1688_CALIBRATION"]["id"])
        config["bm1688_policy"] = bm_policy
        validation = validate_config(config)

    config_hash = _sha({"contract": contract_snapshot, "config": config})
    version = AutoTrainingPlanVersion(
        plan_id=plan.id,
        version_no=1,
        status="DRAFT",
        config_json=_json(config),
        config_sha256=config_hash,
        quality_policy_signature=config.get("quality_policy_binding", {}).get(
            "signature"
        ),
        label_contract_snapshot_json=_json(contract_snapshot),
        evaluation_binding_json=_json(config.get("evaluation_binding", {})),
        missing_fields_json=_json(validation["missing_fields"]),
        validation_errors_json=_json(validation["validation_errors"]),
        change_reason=change_reason,
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    plan.current_version_id = version.id
    _audit(
        db,
        plan=plan,
        actor=user,
        action="PLAN_CREATED",
        before=None,
        after={"status": plan.status},
        reason=change_reason,
    )
    return plan_payload(db, plan, user, detail=True)


def create_plan_from_frozen_dataset_version(
    db: Session, *, user: User, dataset_version_id: int
) -> dict[str, Any]:
    """Create an idempotent draft plan from a frozen dataset manifest.

    This is the hand-off used by flexible workflows.  It deliberately creates
    only a governed draft: approval, pool reconciliation, trigger evaluation,
    and training-job materialization remain in the existing auto-training
    state machine.
    """
    ensure_auto_training_plan_writes_open()
    from labelu.internal.adapter.persistence import crud_dataset, crud_project

    version = crud_dataset.get_version(db, int(dataset_version_id))
    if version is None:
        raise LabelUException(
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="数据集版本不存在",
        )
    project = crud_project.get(db, int(version.project_id))
    if project is None:
        raise LabelUException(
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="数据集版本所属项目不存在",
        )
    if not (
        is_task_superuser(user)
        or project.owner_id == user.id
        or (
            crud_project.get_member(db, project.id, user.id)
            and crud_project.get_member(db, project.id, user.id).status == "ACTIVE"
        )
    ):
        raise LabelUException(
            ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="无权使用该数据集版本",
        )
    if version.status != "FROZEN":
        raise LabelUException(
            ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="只有冻结的数据集版本可以创建训练计划",
        )
    existing = (
        db.query(AutoTrainingPlan)
        .filter(
            AutoTrainingPlan.source_dataset_version_id == int(version.id),
            AutoTrainingPlan.deleted_at.is_(None),
        )
        .first()
    )
    if existing is not None:
        return plan_payload(db, existing, user, detail=True)

    metadata = _loads(version.metadata_json, {})
    series_type = str(metadata.get("series_type") or "yolo_detection")
    if series_type not in SERIES_TYPES:
        series_type = "yolo_detection"
    category_id = int(metadata.get("dataset_category_id") or 0)
    category = (
        db.query(DatasetCategory)
        .filter(DatasetCategory.id == category_id, DatasetCategory.deleted_at.is_(None))
        .first()
        if category_id
        else None
    )
    if category is None:
        category = (
            db.query(DatasetCategory)
            .filter(
                DatasetCategory.category_key == "active_learning",
                DatasetCategory.deleted_at.is_(None),
            )
            .first()
        )
    if category is None:
        category = DatasetCategory(
            category_key="active_learning",
            name="Active Learning",
            description="边端难例回流数据集",
            status="active",
            created_by=user.id,
            updated_by=user.id,
        )
        db.add(category)
        db.flush()
    label_signature = str(
        version.label_set_signature
        or metadata.get("label_set_signature")
        or _sha({"dataset_version_id": int(version.id), "labels": []})
    )
    class_signature = str(
        version.class_order_signature
        or metadata.get("class_order_signature")
        or label_signature
    )
    config = default_plan_config_for_task([])
    config["data_admission"]["include_historical_data"] = False
    config["data_admission"].pop("history_task_ids", None)
    config["dataset_binding"] = {
        "dataset_version_id": int(version.id),
        "manifest_rel_path": version.manifest_rel_path,
        "manifest_sha256": version.manifest_sha256,
        "project_id": int(version.project_id),
    }
    plan = AutoTrainingPlan(
        project_id=int(version.project_id),
        source_dataset_version_id=int(version.id),
        owner_id=user.id,
        name=f"active-learning-dataset-{version.id}",
        series_type=series_type,
        dataset_category_id=int(category.id),
        label_set_signature=label_signature[:64],
        class_order_signature=class_signature[:64],
        status=PlanStatus.DRAFT_INCOMPLETE.value,
        version=1,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(plan)
    db.flush()
    plan_version = AutoTrainingPlanVersion(
        plan_id=plan.id,
        version_no=1,
        status="DRAFT",
        config_json=_json(config),
        config_sha256=_sha(config),
        label_contract_snapshot_json=_json(
            {
                "dataset_version_id": int(version.id),
                "label_set_signature": plan.label_set_signature,
                "class_order_signature": plan.class_order_signature,
            }
        ),
        missing_fields_json=_json(validate_config(config)["missing_fields"]),
        validation_errors_json=_json(validate_config(config)["validation_errors"]),
        change_reason="active learning frozen dataset hand-off",
        created_by=user.id,
    )
    db.add(plan_version)
    db.flush()
    plan.current_version_id = plan_version.id
    _audit(
        db,
        plan=plan,
        actor=user,
        action="PLAN_CREATED_FROM_DATASET_VERSION",
        before=None,
        after={"project_id": plan.project_id, "dataset_version_id": version.id},
        reason="active learning frozen dataset hand-off",
    )
    return plan_payload(db, plan, user, detail=True)


def get_plan_detail(db: Session, *, user: User, plan_id: int):
    plan = _assert_visible(db, crud_auto_training.get_plan(db, plan_id), user)
    return plan_payload(db, plan, user, detail=True)


def list_plans(
    db: Session,
    *,
    user: User,
    page: int,
    size: int,
    project_id: int | None = None,
    q: str | None = None,
):
    normalized_query = str(q or "").strip()
    if is_task_superuser(user):
        rows, total = crud_auto_training.list_plans(
            db,
            owner_id=None,
            page=page,
            size=size,
            project_id=project_id,
            search=normalized_query,
        )
    else:
        project_ids = [
            int(project.id)
            for project in db.query(Project).filter(Project.deleted_at.is_(None)).all()
            if _project_access(db, project.id, user)
        ]
        if project_id is not None:
            # Keep the selected workspace project as the authoritative scope
            # for embedded platform views instead of falling back to every
            # project visible to the user.
            project_ids = [
                int(project_id)
            ] if int(project_id) in project_ids else []
        query = db.query(AutoTrainingPlan).filter(AutoTrainingPlan.deleted_at.is_(None))
        query = query.filter(
            (AutoTrainingPlan.owner_id == int(user.id))
            | (
                AutoTrainingPlan.project_id.in_(project_ids)
                if project_ids
                else AutoTrainingPlan.id == -1
            )
        )
        if normalized_query:
            pattern = literal_like_pattern(normalized_query)
            query = query.filter(
                or_(
                    AutoTrainingPlan.name.ilike(pattern, escape=LIKE_ESCAPE),
                    AutoTrainingPlan.series_type.ilike(pattern, escape=LIKE_ESCAPE),
                    AutoTrainingPlan.status.ilike(pattern, escape=LIKE_ESCAPE),
                )
            )
        total = query.count()
        rows = (
            query.order_by(
                AutoTrainingPlan.updated_at.desc(), AutoTrainingPlan.id.desc()
            )
            .offset(page * size)
            .limit(size)
            .all()
        )
    return [plan_payload(db, row, user) for row in rows], total


def project_runtime_summary(db: Session, *, user: User, project_id: int) -> dict:
    """Return a read-only project-scoped view for the platform training dashboard.

    The legacy plan list already contains per-plan admission counters, but the
    platform also needs one bounded response that can render plan health and
    the most recent iteration without issuing one request per plan.  Reuse the
    canonical payload builders so status, permissions and quality-gap fields
    cannot drift between the list/detail and dashboard contracts.
    """
    project = (
        db.query(Project)
        .filter(Project.id == int(project_id), Project.deleted_at.is_(None))
        .first()
    )
    if project is None:
        raise LabelUException(
            ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
            message="项目不存在",
        )
    if not _project_access(db, int(project_id), user):
        raise LabelUException(
            ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="无权访问当前项目的自动训练摘要",
        )

    plans = (
        db.query(AutoTrainingPlan)
        .filter(
            AutoTrainingPlan.project_id == int(project_id),
            AutoTrainingPlan.deleted_at.is_(None),
        )
        .order_by(AutoTrainingPlan.updated_at.desc(), AutoTrainingPlan.id.desc())
        .all()
    )
    plan_ids = [int(plan.id) for plan in plans]
    rounds = (
        db.query(AutoTrainingRound)
        .filter(AutoTrainingRound.plan_id.in_(plan_ids))
        .order_by(AutoTrainingRound.updated_at.desc(), AutoTrainingRound.id.desc())
        .all()
        if plan_ids
        else []
    )

    latest_by_plan: dict[int, AutoTrainingRound] = {}
    for row in rounds:
        latest_by_plan.setdefault(int(row.plan_id), row)
    plan_payloads = [plan_payload(db, plan, user) for plan in plans]
    plan_summaries = [
        {
            "id": payload["id"],
            "name": payload["name"],
            "status": payload["status"],
            "active_round_id": payload["active_round_id"],
            "active_round_no": payload["active_round_no"],
            "execution_stage": payload["execution_stage"],
            "qualified_task_count": payload["qualified_task_count"],
            "eligible_sample_count": payload["eligible_sample_count"],
            "pending_review_count": payload["pending_review_count"],
            "quality_gap_summary": payload["quality_gap_summary"],
            "next_evaluation_at": payload["next_evaluation_at"],
            "updated_at": payload["updated_at"],
            "last_round": round_payload(latest_by_plan[payload["id"]])
            if payload["id"] in latest_by_plan
            else None,
        }
        for payload in plan_payloads
    ]
    recent_iterations = [
        {
            "plan_id": plan_summary["id"],
            "plan_name": plan_summary["name"],
            "plan_status": plan_summary["status"],
            "round": plan_summary["last_round"],
        }
        for plan_summary in plan_summaries
        if plan_summary["last_round"] is not None
    ]
    plan_by_id = {int(plan.id): plan for plan in plans}
    evaluation_runs = (
        db.query(AutoTrainingEvaluationRun)
        .join(AutoTrainingRound, AutoTrainingRound.id == AutoTrainingEvaluationRun.round_id)
        .filter(AutoTrainingRound.plan_id.in_(plan_ids))
        .order_by(AutoTrainingEvaluationRun.created_at.desc(), AutoTrainingEvaluationRun.id.desc())
        .limit(20)
        .all()
        if plan_ids
        else []
    )
    evaluation_reports = []
    for evaluation in evaluation_runs:
        round_row = next((row for row in rounds if row.id == evaluation.round_id), None)
        plan = plan_by_id.get(round_row.plan_id) if round_row else None
        metrics = _loads(evaluation.overall_metrics_json, {})
        recall = metrics.get("overall_recall") if isinstance(metrics, dict) else None
        # Automatic stopping and release readiness are different gates.  A
        # round may meet the configured recall threshold while still lacking
        # conversion, edge validation, or a published model.  Keep both
        # truths in the API so consumers cannot present AUTO_QUALITY_REACHED
        # as an implicit deployment approval.
        training_quality_status = evaluation.decision_code or evaluation.status
        stage_statuses = _loads(round_row.stage_status_json, {}) if round_row else {}
        edge_status = stage_statuses.get("EDGE_VALIDATION") or stage_statuses.get("edge_validation")
        conversion_status = stage_statuses.get("BM1688") or stage_statuses.get("bm1688")
        edge_usability_status = (
            "PASS"
            if round_row and round_row.edge_validation_job_id and str(edge_status).upper() in {"PASS", "PASSED", "COMPLETED", "SUCCEEDED"}
            else "PENDING"
        )
        conversion_quality_status = (
            "PASS"
            if round_row and round_row.bm1688_export_id and str(conversion_status).upper() in {"PASS", "PASSED", "COMPLETED", "SUCCEEDED"}
            else "PENDING"
        )
        release_readiness = (
            "PASS"
            if round_row and round_row.published_model_id and str(round_row.publish_status).upper() in {"PASS", "PASSED", "PUBLISHED", "COMPLETED", "SUCCEEDED"}
            else "BLOCKED"
        )
        evaluation_reports.append(
            {
                "id": evaluation.id,
                "title": f"{plan.name if plan else '自动训练'} / 第{round_row.round_no if round_row else '-'}轮评估",
                "task_name": plan.name if plan else None,
                "report_kind": "auto_training_evaluation",
                "summary_level": evaluation.decision_code or evaluation.status,
                "training_quality_status": training_quality_status,
                "generalization_quality_status": (
                    "PASS" if isinstance(metrics, dict) and metrics.get("generalization_pass") is True else "PENDING"
                ),
                "conversion_quality_status": conversion_quality_status,
                "edge_usability_status": edge_usability_status,
                "release_readiness": release_readiness,
                "release_gate_status": release_readiness,
                "quality_gate_reason": (
                    "训练评估达标，但尚未完成转换、边端验证或发布"
                    if training_quality_status == "AUTO_QUALITY_REACHED" and release_readiness != "PASS"
                    else None
                ),
                "summary_brief": (
                    f"整体召回率 {float(recall):.4f}"
                    if isinstance(recall, (int, float))
                    else "等待评估指标"
                ),
                "error_message": evaluation.error_message,
                "created_at": evaluation.created_at,
                "plan_id": plan.id if plan else None,
                "round_id": round_row.id if round_row else None,
            }
        )
    active_statuses = {
        PlanStatus.ACTIVE.value,
        PlanStatus.WAITING_FOR_DATA.value,
        PlanStatus.WAITING_FOR_REVIEW.value,
        PlanStatus.WAITING_FOR_COVERAGE.value,
        PlanStatus.ROUND_ACTIVE.value,
    }
    latest_round = rounds[0] if rounds else None
    timestamps = [
        value
        for value in [
            *(plan.updated_at for plan in plans),
            *(row.updated_at for row in rounds),
        ]
        if value is not None
    ]
    return {
        "project_id": int(project_id),
        "plan_count": len(plans),
        "plans_by_status": dict(sorted(Counter(plan.status for plan in plans).items())),
        "active_plan_count": sum(1 for plan in plans if plan.status in active_statuses),
        "active_round_count": sum(1 for plan in plans if plan.active_round_id is not None),
        "round_count": len(rounds),
        "latest_iteration": round_payload(latest_round) if latest_round else None,
        "recent_iterations": recent_iterations,
        "evaluation_reports": evaluation_reports,
        "plans": plan_summaries,
        "as_of": max(timestamps) if timestamps else None,
    }


def patch_plan(
    db: Session,
    *,
    user: User,
    plan_id: int,
    expected_version: int,
    name: str | None,
    series_type: str | None,
    dataset_category_id: int | None,
    label_set_signature: str | None,
    class_order_signature: str | None,
    config: dict,
    reason: str | None,
):
    ensure_auto_training_plan_writes_open()
    plan = _assert_manage(db, crud_auto_training.get_plan(db, plan_id), user)
    _assert_version(plan, expected_version)
    if plan.status not in {
        PlanStatus.DRAFT_COMPLETE.value,
        PlanStatus.DRAFT_INCOMPLETE.value,
        PlanStatus.PAUSED_MANUAL.value,
    }:
        _raise_conflict("当前状态不允许编辑配置")
    validate_selected_task_scope(
        db,
        user=user,
        data={
            "dataset_category_id": dataset_category_id or plan.dataset_category_id,
            "label_set_signature": label_set_signature or plan.label_set_signature,
            "class_order_signature": class_order_signature
            or plan.class_order_signature,
            "config": config,
        },
    )
    validation = validate_config(config)
    if not validation["config_complete"]:
        raise LabelUException(
            ErrorCode.CODE_57101_AUTO_TRAIN_PLAN_CONFIG_INCOMPLETE,
            status_code=422,
            message=_json(validation),
        )
    previous = {
        "name": plan.name,
        "series_type": plan.series_type,
        "dataset_category_id": plan.dataset_category_id,
        "label_set_signature": plan.label_set_signature,
        "class_order_signature": plan.class_order_signature,
        "version": plan.version,
    }
    next_name = name or plan.name
    duplicate_name = (
        db.query(AutoTrainingPlan.id)
        .filter(
            AutoTrainingPlan.owner_id == plan.owner_id,
            AutoTrainingPlan.name == next_name,
            AutoTrainingPlan.id != plan.id,
            AutoTrainingPlan.deleted_at.is_(None),
        )
        .first()
    )
    if duplicate_name:
        _raise_conflict("同名自动训练计划已存在")
    if name:
        plan.name = name
    if series_type is not None:
        plan.series_type = series_type
    if dataset_category_id is not None:
        plan.dataset_category_id = dataset_category_id
    if label_set_signature is not None:
        plan.label_set_signature = label_set_signature
    if class_order_signature is not None:
        plan.class_order_signature = class_order_signature
    contract_snapshot = {
        "series_type": plan.series_type,
        "dataset_category_id": plan.dataset_category_id,
        "label_set_signature": plan.label_set_signature,
        "class_order_signature": plan.class_order_signature,
    }
    config_hash = _sha({"contract": contract_snapshot, "config": config})
    duplicate = (
        db.query(AutoTrainingPlanVersion)
        .filter(
            AutoTrainingPlanVersion.plan_id == plan.id,
            AutoTrainingPlanVersion.config_sha256 == config_hash,
        )
        .first()
    )
    if duplicate:
        if plan.name == previous["name"]:
            return plan_payload(db, plan, user, detail=True)
        plan.version += 1
        plan.updated_by = user.id
        _audit(
            db,
            plan=plan,
            actor=user,
            action="PLAN_METADATA_UPDATED",
            before=previous,
            after={**contract_snapshot, "name": plan.name, "version": plan.version},
            reason=reason,
        )
        db.flush()
        return plan_payload(db, plan, user, detail=True)
    next_no = (
        max(
            (item.version_no for item in crud_auto_training.list_versions(db, plan.id)),
            default=0,
        )
        + 1
    )
    version = AutoTrainingPlanVersion(
        plan_id=plan.id,
        version_no=next_no,
        status="DRAFT",
        config_json=_json(config),
        config_sha256=config_hash,
        quality_policy_signature=config["quality_policy_binding"]["signature"],
        label_contract_snapshot_json=_json(contract_snapshot),
        evaluation_binding_json=_json(config["evaluation_binding"]),
        missing_fields_json="[]",
        validation_errors_json="[]",
        change_reason=reason,
        created_by=user.id,
    )
    db.add(version)
    db.flush()
    plan.current_version_id = version.id
    plan.version += 1
    plan.updated_by = user.id
    _audit(
        db,
        plan=plan,
        actor=user,
        action="PLAN_CONFIG_UPDATED",
        before=previous,
        after={**contract_snapshot, "name": plan.name, "version": plan.version},
        reason=reason,
    )
    return plan_payload(db, plan, user, detail=True)


def transition_plan(
    db: Session,
    *,
    user: User,
    plan_id: int,
    expected_version: int,
    target: PlanStatus,
    reason: str,
    acknowledge_quality_reached: bool = False,
    admin_required: bool = False,
):
    ensure_auto_training_plan_writes_open()
    plan = _assert_manage(db, crud_auto_training.get_plan(db, plan_id), user)
    _assert_version(plan, expected_version)
    if admin_required and not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    if plan.status == PlanStatus.AUTO_QUALITY_REACHED.value:
        if not acknowledge_quality_reached:
            raise LabelUException(
                ErrorCode.CODE_57118_AUTO_TRAIN_QUALITY_REACHED_ACK_REQUIRED,
                status_code=422,
            )
        if not is_task_superuser(user):
            raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    if target == PlanStatus.ACTIVE and plan.status == PlanStatus.PENDING_APPROVAL.value:
        gate_errors = _approval_gate_errors(db, plan=plan)
        if gate_errors:
            _raise_conflict(_json({"approval_gate_errors": gate_errors}))
    try:
        assert_plan_transition(
            plan.status, target, actor_is_admin=is_task_superuser(user), reason=reason
        )
    except AutoTrainingConflict as exc:
        raise LabelUException(
            ErrorCode.CODE_57110_AUTO_TRAIN_INVALID_TRANSITION,
            status_code=409,
            message=str(exc),
        ) from exc
    before = {"status": plan.status, "version": plan.version}
    plan.status = target.value
    plan.version += 1
    plan.updated_by = user.id
    _audit(
        db,
        plan=plan,
        actor=user,
        action=f"PLAN_{target.value}",
        before=before,
        after={"status": plan.status, "version": plan.version},
        reason=reason,
    )
    if target == PlanStatus.ACTIVE:
        version = crud_auto_training.get_version(db, plan.current_version_id)
        if version:
            version.status = "APPROVED"
            version.approved_by = user.id
            version.approved_at = datetime.now()
        _enqueue(
            db,
            event_name="auto.plan.config.approved.v1",
            aggregate_type="auto_training_plan",
            aggregate_id=str(plan.id),
            payload={
                "plan_id": plan.id,
                "plan_version_id": plan.current_version_id,
                "approved_by": user.id,
            },
            idempotency_key=f"plan-version:{plan.current_version_id}:approved",
        )
    return plan_payload(db, plan, user, detail=True)


def _approval_gate_errors(db: Session, *, plan: AutoTrainingPlan) -> list[str]:
    version = crud_auto_training.get_version(db, plan.current_version_id)
    if version is None:
        return ["PLAN_VERSION_NOT_FOUND"]
    config = _loads(version.config_json, {})
    validation = validate_config(config)
    errors = list(validation["blocking_reasons"])
    quality = config.get("quality_policy_binding", {})
    approval = None
    try:
        approval_id = int(quality.get("approval_id") or 0)
    except (TypeError, ValueError):
        approval_id = 0
    if approval_id:
        approval = db.query(QualityPolicyApproval).filter_by(id=approval_id).first()
    if approval is None or approval.status != "APPROVED":
        errors.append("QUALITY_POLICY_NOT_APPROVED")
    elif (
        approval.policy_name != quality.get("policy_name")
        or int(approval.policy_version) != int(quality.get("version") or 0)
        or approval.policy_signature != quality.get("signature")
    ):
        errors.append("QUALITY_POLICY_BINDING_MISMATCH")

    expected_datasets = {
        "validation_dataset_id": "VALIDATION",
        "benchmark_dataset_id": "BENCHMARK",
        "sealed_acceptance_dataset_id": "SEALED_ACCEPTANCE",
        "edge_validation_dataset_id": "EDGE_VALIDATION",
    }
    evaluation = config.get("evaluation_binding", {})
    expected_datasets["calibration_dataset_id"] = "BM1688_CALIBRATION"
    dataset_ids = dict(evaluation)
    dataset_ids["calibration_dataset_id"] = config.get("bm1688_policy", {}).get(
        "calibration_dataset_id"
    )
    for key, expected_kind in expected_datasets.items():
        try:
            dataset_id = int(dataset_ids.get(key) or 0)
        except (TypeError, ValueError):
            dataset_id = 0
        dataset = (
            db.query(TrainingEvaluationDataset).filter_by(id=dataset_id).first()
            if dataset_id
            else None
        )
        if dataset is None or dataset.status != "APPROVED":
            errors.append(f"{expected_kind}_DATASET_NOT_APPROVED")
            continue
        if dataset.dataset_kind != expected_kind:
            errors.append(f"{expected_kind}_DATASET_KIND_MISMATCH")
        if (
            dataset.series_type != plan.series_type
            or dataset.dataset_category_id != plan.dataset_category_id
            or dataset.label_set_signature != plan.label_set_signature
        ):
            errors.append(f"{expected_kind}_DATASET_SCOPE_MISMATCH")
        if int(dataset.sample_count or 0) <= 0:
            errors.append(f"{expected_kind}_DATASET_EMPTY")
    # device_pool is legacy plan metadata; dispatch selects from the global edge registry.
    return sorted(set(errors))


def archive_plan(
    db: Session, *, user: User, plan_id: int, expected_version: int, reason: str
):
    ensure_auto_training_plan_writes_open()
    plan = _assert_manage(db, crud_auto_training.get_plan(db, plan_id), user)
    _assert_version(plan, expected_version)
    if not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    if plan.active_round_id:
        raise LabelUException(
            ErrorCode.CODE_57103_AUTO_TRAIN_PLAN_ACTIVE_ROUND, status_code=409
        )
    before = plan.status
    plan.status = PlanStatus.ARCHIVED.value
    plan.version += 1
    _audit(
        db,
        plan=plan,
        actor=user,
        action="PLAN_ARCHIVED",
        before={"status": before},
        after={"status": plan.status},
        reason=reason,
    )
    return plan_payload(db, plan, user, detail=True)


def list_versions(db: Session, *, user: User, plan_id: int):
    _assert_visible(db, crud_auto_training.get_plan(db, plan_id), user)
    return [
        _version_payload(row) for row in crud_auto_training.list_versions(db, plan_id)
    ]


def list_audits(db: Session, *, user: User, plan_id: int):
    _assert_visible(db, crud_auto_training.get_plan(db, plan_id), user)
    return [
        {
            "id": row.id,
            "plan_id": row.plan_id,
            "round_id": row.round_id,
            "actor_type": row.actor_type,
            "actor_id": row.actor_id,
            "action": row.action,
            "before": _loads(row.before_json, None),
            "after": _loads(row.after_json, None),
            "reason": row.reason,
            "event_sha256": row.event_sha256,
            "created_at": row.created_at,
        }
        for row in crud_auto_training.list_audits(db, plan_id)
    ]


def create_scan_preview(db: Session, *, user: User, plan_id: int):
    ensure_auto_training_plan_writes_open()
    plan = _assert_manage(db, crud_auto_training.get_plan(db, plan_id), user)
    row = _enqueue(
        db,
        event_name="auto.plan.scan.preview.requested.v1",
        aggregate_type="auto_training_plan",
        aggregate_id=str(plan.id),
        payload={"plan_id": plan.id, "requested_by": user.id},
        idempotency_key=f"scan-preview:{plan.id}:{plan.version}",
    )
    return {"scan_job_id": row.id, "status": row.status}


def get_scan_preview(db: Session, *, user: User, plan_id: int, scan_job_id: int):
    _assert_visible(db, crud_auto_training.get_plan(db, plan_id), user)
    row = (
        db.query(AutoTrainingOutbox)
        .filter(
            AutoTrainingOutbox.id == scan_job_id,
            AutoTrainingOutbox.aggregate_id == str(plan_id),
        )
        .first()
    )
    if not row:
        raise LabelUException(
            ErrorCode.CODE_57116_AUTO_TRAIN_SCAN_JOB_NOT_FOUND, status_code=404
        )
    return {
        "scan_job_id": row.id,
        "status": row.status,
        "summary": _loads(row.payload_json, {}),
    }


def list_task_scopes(db: Session, *, user: User, plan_id: int):
    _assert_visible(db, crud_auto_training.get_plan(db, plan_id), user)
    rows = (
        db.query(AutoTrainingTaskScope)
        .filter(AutoTrainingTaskScope.plan_id == plan_id)
        .order_by(AutoTrainingTaskScope.task_id)
        .all()
    )
    return [
        {
            "id": row.id,
            "task_id": row.task_id,
            "scope_status": row.scope_status,
            "eligible_sample_count": row.eligible_sample_count,
            "pending_review_count": row.pending_review_count,
            "reason_code": row.reason_code,
        }
        for row in rows
    ]


def list_sample_pool(db: Session, *, user: User, plan_id: int, page: int, size: int):
    _assert_visible(db, crud_auto_training.get_plan(db, plan_id), user)
    query = db.query(AutoTrainingSamplePool).filter(
        AutoTrainingSamplePool.plan_id == plan_id
    )
    total = query.count()
    rows = (
        query.order_by(AutoTrainingSamplePool.id).offset(page * size).limit(size).all()
    )
    return [
        {
            "task_id": row.task_id,
            "sample_id": row.sample_id,
            "pool_status": row.pool_status,
            "reason_code": row.reason_code,
            "annotation_sha256": row.annotation_sha256,
            "image_sha256": row.image_sha256,
            "human_review_status": "APPROVED" if row.human_review_id else None,
            "eligible_since": row.eligible_since,
            "updated_at": row.updated_at,
        }
        for row in rows
    ], total


def request_reconcile(db: Session, *, user: User, plan_id: int):
    ensure_auto_training_plan_writes_open()
    if not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    plan = _assert_manage(db, crud_auto_training.get_plan(db, plan_id), user)
    row = _enqueue(
        db,
        event_name="auto.plan.reconcile.requested.v1",
        aggregate_type="auto_training_plan",
        aggregate_id=str(plan.id),
        payload={"plan_id": plan.id, "requested_by": user.id},
        idempotency_key=f"reconcile:{plan.id}:{plan.version}",
    )
    return {"reconcile_job_id": row.id, "status": row.status}


def create_review(
    db: Session,
    *,
    user: User,
    sample_id: int,
    annotation_sha256: str,
    review_status: str,
    comment: str | None,
):
    ensure_auto_training_plan_writes_open()
    if (
        user.is_service_account
        or user.account_type != "human"
        or not user.interactive_login_enabled
    ):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    sample = (
        db.query(TaskSample)
        .filter(TaskSample.id == sample_id, TaskSample.deleted_at.is_(None))
        .first()
    )
    if not sample:
        raise LabelUException(ErrorCode.CODE_55001_SAMPLE_NOT_FOUND, status_code=404)
    task = db.query(Task).filter(Task.id == sample.task_id).first()
    if not user_has_task_access(user, task):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    try:
        current_annotation = json.loads(sample.data or "{}")
    except (TypeError, ValueError):
        current_annotation = {}
    current_hash = canonical_annotation_sha256(current_annotation)
    if current_hash != annotation_sha256:
        raise LabelUException(
            ErrorCode.CODE_57114_AUTO_TRAIN_REVIEW_HASH_CONFLICT, status_code=409
        )
    if task_requires_human_review(task) and not has_human_annotation_edit(
        db, sample_id=int(sample.id), annotation_hash=current_hash
    ):
        raise LabelUException(
            ErrorCode.CODE_57105_AUTO_TRAIN_REVIEW_REQUIRED,
            status_code=409,
            message="审核前必须先由可交互的人工作出当前版本的实际标注修改",
        )
    row = SampleHumanReview(
        task_id=sample.task_id,
        sample_id=sample.id,
        annotation_sha256=current_hash,
        review_status=review_status,
        comment=comment,
        reviewed_by=user.id,
        reviewed_at=datetime.now(),
    )
    db.add(row)
    db.flush()
    _enqueue(
        db,
        event_name="sample.review.changed.v1",
        aggregate_type="task_sample",
        aggregate_id=str(sample.id),
        payload={
            "review_id": row.id,
            "task_id": sample.task_id,
            "sample_id": sample.id,
            "annotation_sha256": current_hash,
            "review_status": review_status,
            "reviewed_by": user.id,
        },
        idempotency_key=f"review:{row.id}:{review_status}",
    )
    return {
        "id": row.id,
        "sample_id": row.sample_id,
        "annotation_sha256": row.annotation_sha256,
        "review_status": row.review_status,
        "reviewed_by": row.reviewed_by,
        "reviewed_at": row.reviewed_at,
    }


def invalidate_review(db: Session, *, user: User, review_id: int, reason: str):
    ensure_auto_training_plan_writes_open()
    row = db.query(SampleHumanReview).filter(SampleHumanReview.id == review_id).first()
    if not row:
        raise LabelUException(ErrorCode.CODE_55001_SAMPLE_NOT_FOUND, status_code=404)
    if row.reviewed_by != user.id and not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    row.invalidated_by = user.id
    row.invalidated_at = datetime.now()
    row.invalidation_reason = reason
    _enqueue(
        db,
        event_name="sample.review.changed.v1",
        aggregate_type="task_sample",
        aggregate_id=str(row.sample_id),
        payload={
            "review_id": row.id,
            "task_id": row.task_id,
            "sample_id": row.sample_id,
            "annotation_sha256": row.annotation_sha256,
            "review_status": "INVALIDATED",
            "invalidated_by": user.id,
        },
        idempotency_key=f"review:{row.id}:invalidated",
    )
    return {
        "id": row.id,
        "invalidated_at": row.invalidated_at,
        "invalidation_reason": reason,
    }


def find_current_human_review(
    db: Session, *, sample_id: int, annotation_sha256: str
) -> SampleHumanReview | None:
    return (
        db.query(SampleHumanReview)
        .filter(
            SampleHumanReview.sample_id == sample_id,
            SampleHumanReview.annotation_sha256 == annotation_sha256,
            SampleHumanReview.review_status == "APPROVED",
            SampleHumanReview.invalidated_at.is_(None),
        )
        .order_by(SampleHumanReview.reviewed_at.desc(), SampleHumanReview.id.desc())
        .first()
    )


def reserve_active_round(
    db: Session,
    *,
    plan_id: int,
    round_id: int,
    expected_plan_version: int,
) -> bool:
    ensure_auto_training_plan_writes_open()
    updated = (
        db.query(AutoTrainingPlan)
        .filter(
            AutoTrainingPlan.id == plan_id,
            AutoTrainingPlan.deleted_at.is_(None),
            AutoTrainingPlan.active_round_id.is_(None),
            AutoTrainingPlan.version == expected_plan_version,
        )
        .update(
            {
                AutoTrainingPlan.active_round_id: round_id,
                AutoTrainingPlan.status: PlanStatus.ROUND_ACTIVE.value,
                AutoTrainingPlan.version: AutoTrainingPlan.version + 1,
                AutoTrainingPlan.updated_at: datetime.now(),
            },
            synchronize_session="fetch",
        )
    )
    return updated == 1


def round_payload(row: AutoTrainingRound, *, task_id: int | None = None) -> dict:
    return {
        "id": row.id,
        "plan_id": row.plan_id,
        "round_no": row.round_no,
        "state": row.state,
        "trigger_type": row.trigger_type,
        "plan_version_no": None,
        "dataset_signature": row.dataset_signature,
        "eligible_sample_count": row.eligible_sample_count,
        "train_count": row.train_count,
        "val_count": row.val_count,
        "test_count": row.test_count,
        "training_task_id": row.training_task_id,
        "yolo_train_job_id": row.yolo_train_job_id,
        # The legacy training detail API is scoped by annotation task id. Keep
        # this separate from training_task_id (the internal orchestration id).
        "task_id": task_id,
        "evaluation_run_id": row.evaluation_run_id,
        "bm1688_export_id": row.bm1688_export_id,
        "edge_validation_job_id": row.edge_validation_job_id,
        "publish_job_id": row.publish_job_id,
        "publish_status": row.publish_status,
        "published_model_id": row.published_model_id,
        "publish_error": row.publish_error,
        "publish_requested_at": row.publish_requested_at,
        "published_at": row.published_at,
        "decision_code": row.decision_code,
        "queued_at": row.queued_at,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "execution_signature": row.execution_signature,
        "manifest_rel_path": row.manifest_rel_path,
        "manifest_sha256": row.manifest_sha256,
        "stage_statuses": _loads(row.stage_status_json, {}),
        "stage_errors": _loads(row.stage_errors_json, {}),
        "retry_count": _loads(row.retry_count_json, {}),
    }


def preview_round(db: Session, *, user: User, plan_id: int):
    ensure_auto_training_plan_writes_open()
    plan = _assert_manage(db, crud_auto_training.get_plan(db, plan_id), user)
    version = crud_auto_training.get_version(db, plan.current_version_id)
    config = _loads(version.config_json, {}) if version else {}
    minimum = config.get("data_admission", {}).get("min_eligible_samples_per_task", 50)
    grouped = defaultdict(list)
    for row in (
        db.query(AutoTrainingSamplePool)
        .filter(
            AutoTrainingSamplePool.plan_id == plan.id,
            AutoTrainingSamplePool.pool_status == "ELIGIBLE",
        )
        .all()
    ):
        grouped[row.task_id].append(row)
    included = [
        row for rows in grouped.values() if len(rows) >= minimum for row in rows
    ]
    return {
        "would_create_round": bool(included) and not plan.active_round_id,
        "qualified_task_count": sum(
            1 for rows in grouped.values() if len(rows) >= minimum
        ),
        "eligible_sample_count": len(included),
        "blocking_reasons": (["ACTIVE_ROUND_EXISTS"] if plan.active_round_id else [])
        + (["NO_QUALIFIED_TASK"] if not included else []),
    }


def create_round(db: Session, *, user: User, plan_id: int, reason: str):
    ensure_auto_training_plan_writes_open()
    from .auto_training_scheduler import (
        AutoTrainingActiveRoundConflict,
        create_frozen_round,
    )

    plan = _assert_manage(db, crud_auto_training.get_plan(db, plan_id), user)
    if plan.status not in {
        PlanStatus.ACTIVE.value,
        PlanStatus.WAITING_FOR_DATA.value,
        PlanStatus.WAITING_FOR_REVIEW.value,
        PlanStatus.WAITING_FOR_COVERAGE.value,
    }:
        _raise_conflict("当前计划状态不允许创建轮次")
    if plan.active_round_id:
        raise LabelUException(
            ErrorCode.CODE_57103_AUTO_TRAIN_PLAN_ACTIVE_ROUND, status_code=409
        )
    preview = preview_round(db, user=user, plan_id=plan_id)
    if not preview["eligible_sample_count"]:
        raise LabelUException(
            ErrorCode.CODE_57104_AUTO_TRAIN_NO_QUALIFIED_TASK, status_code=409
        )
    try:
        round_row = create_frozen_round(
            db,
            plan_id=plan.id,
            trigger_type="MANUAL",
            trigger_snapshot={"reason": reason, "requested_by": user.id},
            expected_plan_version=plan.version,
        )
    except AutoTrainingActiveRoundConflict as exc:
        raise LabelUException(
            ErrorCode.CODE_57103_AUTO_TRAIN_PLAN_ACTIVE_ROUND,
            status_code=409,
            message=str(exc),
        ) from exc
    except ValueError as exc:
        if "no qualified" in str(exc):
            raise LabelUException(
                ErrorCode.CODE_57104_AUTO_TRAIN_NO_QUALIFIED_TASK,
                status_code=409,
                message=str(exc),
            ) from exc
        raise
    _enqueue(
        db,
        event_name="auto.round.stage.completed.v1",
        aggregate_type="auto_training_round",
        aggregate_id=str(round_row.id),
        payload={
            "plan_id": plan.id,
            "round_id": round_row.id,
            "stage": "SNAPSHOT",
            "status": "COMPLETED",
        },
        idempotency_key=f"round:{round_row.id}:stage:SNAPSHOT:1",
    )
    _audit(
        db,
        plan=plan,
        actor=user,
        action="ROUND_CREATED",
        before=None,
        after={
            "round_id": round_row.id,
            "dataset_signature": round_row.dataset_signature,
        },
        reason=reason,
        round_id=round_row.id,
    )
    return round_payload(round_row)


def get_round(db: Session, *, user: User, round_id: int):
    row = db.query(AutoTrainingRound).filter(AutoTrainingRound.id == round_id).first()
    if not row:
        _raise_not_found()
    _assert_visible(db, crud_auto_training.get_plan(db, row.plan_id), user)
    task_id = None
    if row.yolo_train_job_id:
        task_id = db.query(YoloTrainJob.task_id).filter(YoloTrainJob.id == row.yolo_train_job_id).scalar()
    return round_payload(row, task_id=int(task_id) if task_id else None)


def list_rounds(db: Session, *, user: User, plan_id: int):
    _assert_visible(db, crud_auto_training.get_plan(db, plan_id), user)
    rows = crud_auto_training.list_rounds(db, plan_id)
    job_ids = [int(row.yolo_train_job_id) for row in rows if row.yolo_train_job_id]
    task_by_job = {}
    if job_ids:
        task_by_job = {
            int(job.id): int(job.task_id)
            for job in db.query(YoloTrainJob).filter(YoloTrainJob.id.in_(job_ids)).all()
            if job.task_id
        }
    return [
        round_payload(row, task_id=task_by_job.get(int(row.yolo_train_job_id or 0)))
        for row in rows
    ]


def list_round_samples(db: Session, *, user: User, round_id: int, page: int, size: int):
    get_round(db, user=user, round_id=round_id)
    query = db.query(AutoTrainingRoundSample).filter(
        AutoTrainingRoundSample.round_id == round_id
    )
    total = query.count()
    rows = (
        query.order_by(AutoTrainingRoundSample.id).offset(page * size).limit(size).all()
    )
    return [
        {
            "task_id": row.task_id,
            "sample_id": row.sample_id,
            "split": row.split,
            "sample_signature": row.sample_signature,
            "annotation_sha256": row.annotation_sha256,
            "image_sha256": row.image_sha256,
        }
        for row in rows
    ], total


def get_round_quality(db: Session, *, user: User, round_id: int):
    get_round(db, user=user, round_id=round_id)
    row = (
        db.query(AutoTrainingEvaluationRun)
        .filter(AutoTrainingEvaluationRun.round_id == round_id)
        .first()
    )
    if not row:
        return {
            "round_id": round_id,
            "status": "PENDING",
            "overall_metrics": {},
            "per_label_metrics": {},
            "stop_gate_pass": False,
            "blocking_reasons": ["EVALUATION_NOT_COMPLETED"],
        }
    return {
        "round_id": round_id,
        "status": row.status,
        "policy_signature": row.policy_signature,
        "evaluation_binding_signature": row.evaluation_binding_signature,
        "overall_metrics": _loads(row.overall_metrics_json, {}),
        "per_label_metrics": _loads(row.per_label_metrics_json, {}),
        "plate_component_metrics": _loads(row.plate_component_metrics_json, {}),
        "champion_comparison": _loads(row.champion_comparison_json, {}),
        "evidence_manifest_sha256": row.evidence_manifest_sha256,
        "decision_code": row.decision_code,
        "stop_gate_pass": row.decision_code == "AUTO_QUALITY_REACHED",
        "blocking_reasons": _loads(row.decision_json, {}).get("blocking_reasons", []),
    }


def cancel_round(db: Session, *, user: User, round_id: int, reason: str):
    ensure_auto_training_plan_writes_open()
    row = db.query(AutoTrainingRound).filter(AutoTrainingRound.id == round_id).first()
    if not row:
        _raise_not_found()
    plan = _assert_manage(db, crud_auto_training.get_plan(db, row.plan_id), user)
    if row.state not in {
        RoundStatus.PREPARING.value,
        RoundStatus.SNAPSHOT_READY.value,
        RoundStatus.QUEUED.value,
        RoundStatus.TRAINING.value,
    }:
        _raise_conflict("当前轮次不在可取消阶段")
    row.state = RoundStatus.TRAIN_CANCELLED.value
    row.completed_at = datetime.now()
    plan.active_round_id = None
    plan.status = PlanStatus.WAITING_FOR_DATA.value
    plan.version += 1
    _audit(
        db,
        plan=plan,
        actor=user,
        action="ROUND_CANCELLED",
        before=None,
        after={"state": row.state},
        reason=reason,
        round_id=row.id,
    )
    return round_payload(row)


def retry_stage(
    db: Session,
    *,
    user: User,
    round_id: int,
    stage: str,
    reason: str,
    expected_state: str,
):
    ensure_auto_training_plan_writes_open()
    if not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    row = db.query(AutoTrainingRound).filter(AutoTrainingRound.id == round_id).first()
    if not row:
        _raise_not_found()
    if row.state != expected_state:
        _raise_conflict(f"轮次当前状态为 {row.state}")
    allowed = {
        "TRAIN": {RoundStatus.TRAIN_FAILED.value},
        "BM1688": {RoundStatus.BM1688_FAILED.value},
        "EDGE_VALIDATION": {RoundStatus.EDGE_VALIDATION_FAILED.value},
        "CALLBACK": {RoundStatus.PUBLISH_READY.value},
    }
    if row.state not in allowed[stage]:
        raise LabelUException(
            ErrorCode.CODE_57112_AUTO_TRAIN_STAGE_GATE_BLOCKED, status_code=409
        )
    event = _enqueue(
        db,
        event_name="auto.round.retry.requested.v1",
        aggregate_type="auto_training_round",
        aggregate_id=str(row.id),
        payload={"round_id": row.id, "stage": stage, "reason": reason},
        idempotency_key=f"round:{row.id}:retry:{stage}:{row.version}",
    )
    return {"retry_job_id": event.id, "status": event.status, "stage": stage}


def promote_to_bm1688(db: Session, *, user: User, round_id: int, reason: str):
    ensure_auto_training_plan_writes_open()
    if not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    row = db.query(AutoTrainingRound).filter(AutoTrainingRound.id == round_id).first()
    if not row:
        _raise_not_found()
    if row.state not in {
        RoundStatus.AUTO_QUALITY_REACHED.value,
        RoundStatus.PT_CANDIDATE.value,
        RoundStatus.QUALITY_FAILED.value,
    }:
        raise LabelUException(
            ErrorCode.CODE_57112_AUTO_TRAIN_STAGE_GATE_BLOCKED, status_code=409
        )
    origin = (
        "MANUAL_DIAGNOSTIC"
        if row.state == RoundStatus.QUALITY_FAILED.value
        else "AUTO_QUALITY"
    )
    event = _enqueue(
        db,
        event_name="auto.round.bm1688.requested.v1",
        aggregate_type="auto_training_round",
        aggregate_id=str(row.id),
        payload={"round_id": row.id, "origin": origin, "reason": reason},
        idempotency_key=f"round:{row.id}:bm1688",
    )
    row.state = (
        RoundStatus.MANUAL_BM1688_QUEUED.value
        if origin == "MANUAL_DIAGNOSTIC"
        else RoundStatus.BM1688_QUEUED.value
    )
    return {"job_id": event.id, "status": row.state, "origin": origin}


def start_edge_validation(db: Session, *, user: User, round_id: int, reason: str):
    ensure_auto_training_plan_writes_open()
    if not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    row = db.query(AutoTrainingRound).filter(AutoTrainingRound.id == round_id).first()
    if not row:
        _raise_not_found()
    if row.state not in {
        RoundStatus.BMODEL_EVALUATING.value,
        RoundStatus.MANUAL_BMODEL_EVALUATING.value,
    }:
        raise LabelUException(
            ErrorCode.CODE_57112_AUTO_TRAIN_STAGE_GATE_BLOCKED, status_code=409
        )
    origin = (
        "MANUAL_DIAGNOSTIC"
        if row.state == RoundStatus.MANUAL_BMODEL_EVALUATING.value
        else "AUTO_QUALITY"
    )
    event = _enqueue(
        db,
        event_name="auto.round.edge.requested.v1",
        aggregate_type="auto_training_round",
        aggregate_id=str(row.id),
        payload={"round_id": row.id, "origin": origin, "reason": reason},
        idempotency_key=f"round:{row.id}:edge",
    )
    return {"job_id": event.id, "status": event.status, "origin": origin}


def submit_quality_evidence(
    db: Session,
    *,
    user: User,
    round_id: int,
    expected_state: str,
    evaluation_run_id: int,
    evaluation_request_sha256: str,
    overall_metrics: dict[str, Any],
    per_label_metrics: dict[str, dict[str, Any]],
    plate_component_metrics: dict[str, Any],
    champion_comparison: dict[str, Any],
    evidence_manifest_rel_path: str,
    evidence_manifest_sha256: str,
):
    ensure_auto_training_plan_writes_open()
    if not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    row = db.query(AutoTrainingRound).filter_by(id=round_id).first()
    if not row:
        _raise_not_found()
    plan = crud_auto_training.get_plan(db, row.plan_id)
    existing = (
        db.query(AutoTrainingEvaluationRun)
        .filter(AutoTrainingEvaluationRun.round_id == round_id)
        .first()
    )
    if existing is not None and existing.status == "COMPLETED":
        if existing.evidence_manifest_sha256 == evidence_manifest_sha256:
            return {
                "changed": False,
                "action": "UNCHANGED",
                "round_id": round_id,
                "decision_code": existing.decision_code,
            }
        _raise_conflict("该轮次已冻结其他质量证据，不能覆盖")
    if row.state != expected_state:
        _raise_conflict(f"轮次当前状态为 {row.state}")

    evaluation_request = existing
    if evaluation_request is None or evaluation_request.id != evaluation_run_id:
        evaluation_request = (
            db.query(AutoTrainingEvaluationRun)
            .filter_by(id=evaluation_run_id, round_id=round_id)
            .first()
        )
    if evaluation_request is None:
        _raise_conflict("trusted evaluation request was not found")
    if row.evaluation_run_id != evaluation_request.id:
        _raise_conflict("trusted evaluation request lineage does not match the round")
    if evaluation_request.status not in {"PENDING", "PROCESSING"}:
        _raise_conflict("trusted evaluation request is not accepting evidence")
    if evaluation_request.request_manifest_sha256 != evaluation_request_sha256:
        _raise_conflict("trusted evaluation request sha256 does not match")

    job = (
        db.query(YoloTrainJob)
        .filter(
            YoloTrainJob.id == row.yolo_train_job_id,
            YoloTrainJob.auto_training_round_id == round_id,
            YoloTrainJob.status == "COMPLETED",
        )
        .first()
    )
    if job is None:
        _raise_conflict("轮次训练作业尚未完成，不能提交质量证据")
    summary = _loads(job.metrics_summary, {})
    summary["auto_training_evaluation"] = {
        "overall_metrics": overall_metrics,
        "per_label_metrics": per_label_metrics,
        "plate_component_metrics": plate_component_metrics,
        "champion_comparison": champion_comparison,
        "evidence_manifest_rel_path": evidence_manifest_rel_path,
        "evidence_manifest_sha256": evidence_manifest_sha256,
        "submitted_by": int(user.id),
        "submitted_at": datetime.now().isoformat(),
        "evaluation_run_id": evaluation_request.id,
        "evaluation_request_sha256": evaluation_request_sha256,
    }
    job.metrics_summary = _json(summary)

    from labelu.internal.application.service.auto_training_lifecycle import (
        record_quality_evaluation,
    )

    result = record_quality_evaluation(
        db,
        round_id=round_id,
        overall_metrics=overall_metrics,
        per_label_metrics=per_label_metrics,
        plate_component_metrics=plate_component_metrics,
        champion_comparison=champion_comparison,
        evidence_manifest_rel_path=evidence_manifest_rel_path,
        evidence_manifest_sha256=evidence_manifest_sha256,
    )
    _audit(
        db,
        plan=plan,
        actor=user,
        action="QUALITY_EVIDENCE_ACCEPTED",
        before={"state": expected_state},
        after={"state": row.state, "decision_code": result.decision_code},
        reason="trusted evaluator evidence submitted",
        round_id=round_id,
    )
    return {
        "changed": result.changed,
        "action": result.action,
        "round_id": round_id,
        "decision_code": result.decision_code,
    }


async def publish_round(
    db: Session,
    *,
    user: User,
    round_id: int,
    expected_state: str,
    reason: str,
    environment_id: str | None = None,
    keep_template_cache: bool = False,
    bmodel_precision: str | None = None,
    bmodel_rel_path: str | None = None,
):
    ensure_auto_training_plan_writes_open()
    if not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    row = db.query(AutoTrainingRound).filter_by(id=round_id).first()
    if not row:
        _raise_not_found()
    plan = crud_auto_training.get_plan(db, row.plan_id)
    if row.state == RoundStatus.PUBLISHED.value:
        return {
            "changed": False,
            "action": "UNCHANGED",
            "round_id": round_id,
            "decision_code": row.decision_code,
            "publish_job_id": row.publish_job_id,
        }
    if row.state == RoundStatus.PUBLISHING.value:
        return {
            "changed": False,
            "action": "UNCHANGED",
            "round_id": round_id,
            "decision_code": row.decision_code,
            "publish_job_id": row.publish_job_id,
            "publish_status": row.publish_status,
        }
    if row.state != expected_state:
        _raise_conflict(f"轮次当前状态为 {row.state}")

    from labelu.internal.application.service.auto_training_pipeline_runtime import (
        _assert_publishable_round,
        register_aicm_publish_job,
    )
    from labelu.internal.application.service import yolo_aicm_publish

    try:
        _assert_publishable_round(db, round_row=row)
        publish_job = await yolo_aicm_publish.start_publish_job(
            db,
            user,
            export_id=int(row.bm1688_export_id),
            environment_id=environment_id,
            keep_template_cache=keep_template_cache,
            bmodel_precision=bmodel_precision,
            bmodel_rel_path=bmodel_rel_path,
            publish_mode="manual",
        )
        result = register_aicm_publish_job(
            db,
            round_id=round_id,
            actor_id=int(user.id),
            actor_is_admin=True,
            reason=reason,
            publish_job_id=publish_job.job_id,
        )
    except ValueError as exc:
        _raise_conflict(str(exc))
    _audit(
        db,
        plan=plan,
        actor=user,
        action="ROUND_PUBLISH_REQUESTED",
        before={"state": expected_state},
        after={"state": row.state, "publish_job_id": row.publish_job_id},
        reason=reason,
        round_id=round_id,
    )
    return {
        "changed": result.changed,
        "action": result.action,
        "round_id": round_id,
        "decision_code": result.decision_code,
        "publish_job_id": row.publish_job_id,
        "publish_status": row.publish_status,
    }


def _auto_evaluation_candidates(
    db: Session,
    *,
    user: User,
    series_type: str,
    dataset_category_id: int,
    label_set_signature: str,
) -> list[dict[str, Any]]:
    """Build the shared pool directly from completed, contract-bound images."""
    task_contract_rows = (
        db.query(Task, TaskAnnotationContract)
        .join(
            TaskAnnotationContract,
            TaskAnnotationContract.id == Task.current_annotation_contract_id,
        )
        .filter(
            Task.deleted_at.is_(None),
            Task.media_type == "IMAGE",
            Task.dataset_category_id == dataset_category_id,
            TaskAnnotationContract.label_set_signature == label_set_signature,
            TaskAnnotationContract.status.in_(("active", "approved")),
        )
        .order_by(Task.id)
        .all()
    )
    visible_tasks = {
        int(task.id): (task, contract)
        for task, contract in task_contract_rows
        if user_has_task_access(user, task)
    }
    if not visible_tasks:
        return []

    rows = (
        db.query(TaskSample, SampleAnnotationScope)
        .outerjoin(
            SampleAnnotationScope,
            SampleAnnotationScope.sample_id == TaskSample.id,
        )
        .filter(TaskSample.task_id.in_(sorted(visible_tasks)))
        .order_by(TaskSample.task_id, TaskSample.id)
        .all()
    )
    sample_ids = [int(sample.id) for sample, _scope in rows]
    fingerprints: dict[int, SampleAssetFingerprint] = {}
    if sample_ids:
        for row in (
            db.query(SampleAssetFingerprint)
            .filter(SampleAssetFingerprint.sample_id.in_(sample_ids))
            .order_by(
                SampleAssetFingerprint.updated_at.desc(),
                SampleAssetFingerprint.id.desc(),
            )
            .all()
        ):
            fingerprints.setdefault(int(row.sample_id), row)

    candidates: list[dict[str, Any]] = []
    for sample, scope in rows:
        task_contract = visible_tasks.get(int(sample.task_id))
        if task_contract is None:
            continue
        _task, contract = task_contract
        if (
            sample.deleted_at is not None
            or sample.state != "DONE"
            or sample.file is None
        ):
            continue
        annotation = _loads(sample.data, None)
        if not isinstance(annotation, dict):
            continue
        annotation_digest = canonical_annotation_sha256(annotation)
        if (
            scope is None
            or int(scope.contract_id) != int(contract.id)
            or scope.completion_status != "complete"
            or scope.annotation_sha256 != annotation_digest
        ):
            continue
        result = annotation.get("result")
        if isinstance(result, str):
            result = _loads(result, None)
        if not isinstance(result, dict):
            continue
        annotation_valid = True
        for rect in (result.get("rectTool") or {}).get("result") or []:
            try:
                x = float(rect.get("x"))
                y = float(rect.get("y"))
                width = float(rect.get("width"))
                height = float(rect.get("height"))
            except (AttributeError, TypeError, ValueError):
                annotation_valid = False
                break
            if x < 0 or y < 0 or width <= 0 or height <= 0:
                annotation_valid = False
                break
        if not annotation_valid:
            continue
        from labelu.internal.application.service.attachment import (
            resolve_attachment_local_path,
        )

        source_path = resolve_attachment_local_path(sample.file)
        if source_path is None:
            continue
        image_digest = _file_sha256(source_path)
        fingerprint = fingerprints.get(int(sample.id))
        if fingerprint and fingerprint.image_sha256:
            if str(fingerprint.image_sha256) != image_digest:
                continue
            image_digest = str(fingerprint.image_sha256)
        sample_signature = _sha(
            {
                "task_id": int(sample.task_id),
                "sample_id": int(sample.id),
                "annotation_sha256": annotation_digest,
                "image_sha256": image_digest,
                "label_set_signature": label_set_signature,
            }
        )
        candidates.append(
            {
                "task_id": int(sample.task_id),
                "sample_id": int(sample.id),
                "file_id": int(sample.file_id or 0),
                "sample_signature": sample_signature,
                "annotation_sha256": annotation_digest,
                "image_sha256": image_digest,
                "duplicate_group_id": int(
                    (fingerprint.duplicate_group_id or 0) if fingerprint else 0
                ),
                "capture_group_key": (
                    str(fingerprint.capture_group_key).strip()
                    if fingerprint and fingerprint.capture_group_key
                    else None
                ),
            }
        )
    return sorted(candidates, key=lambda item: (item["task_id"], item["sample_id"]))


def _partition_auto_evaluation_candidates(
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], str, str]:
    """Deterministically assign source-isolated candidate groups to five datasets."""
    candidates = sorted(
        candidates,
        key=lambda item: (
            int(item["task_id"]),
            int(item["sample_id"]),
            str(item["sample_signature"]),
        ),
    )
    source_signature = _sha(
        [
            {
                "task_id": item["task_id"],
                "sample_id": item["sample_id"],
                "sample_signature": item["sample_signature"],
                "annotation_sha256": item["annotation_sha256"],
                "image_sha256": item["image_sha256"],
            }
            for item in candidates
        ]
    )
    task_ids = {int(item["task_id"]) for item in candidates}
    isolation_level = (
        "source_task" if len(task_ids) >= len(AUTO_EVALUATION_KINDS) else "attachment"
    )

    parents = list(range(len(candidates)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    token_owner: dict[str, int] = {}
    for index, item in enumerate(candidates):
        tokens = [f"image:{item['image_sha256']}"]
        if isolation_level == "source_task":
            tokens.append(f"task:{item['task_id']}")
        else:
            tokens.append(f"attachment:{item['file_id'] or item['sample_id']}")
        if item["duplicate_group_id"]:
            tokens.append(f"duplicate:{item['duplicate_group_id']}")
        if item.get("capture_group_key"):
            tokens.append(f"capture:{item['capture_group_key']}")
        for token in tokens:
            previous = token_owner.setdefault(token, index)
            union(index, previous)

    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, item in enumerate(candidates):
        groups[find(index)].append(item)
    ordered_groups = sorted(
        groups.values(),
        key=lambda group: _sha(
            {
                "source_signature": source_signature,
                "members": sorted(item["sample_signature"] for item in group),
            }
        ),
    )
    if len(ordered_groups) < len(AUTO_EVALUATION_KINDS):
        _raise_conflict(
            "eligible candidate pool does not contain enough isolated groups for five evaluation datasets"
        )

    partitions = {kind: [] for kind in AUTO_EVALUATION_KINDS}
    for index, group in enumerate(ordered_groups):
        kind = AUTO_EVALUATION_KINDS[index % len(AUTO_EVALUATION_KINDS)]
        partitions[kind].extend(group)
    return partitions, source_signature, isolation_level


def _reusable_auto_evaluation_bundle(
    db: Session,
    *,
    plan_id: int | None,
    series_type: str,
    dataset_category_id: int,
    label_set_signature: str,
    bundle_signature: str,
) -> list[TrainingEvaluationDataset]:
    if not plan_id:
        return []
    rows = (
        db.query(TrainingEvaluationDataset)
        .filter(
            TrainingEvaluationDataset.plan_id == int(plan_id),
            TrainingEvaluationDataset.series_type == series_type,
            TrainingEvaluationDataset.dataset_category_id == dataset_category_id,
            TrainingEvaluationDataset.label_set_signature == label_set_signature,
            TrainingEvaluationDataset.status == "APPROVED",
        )
        .order_by(TrainingEvaluationDataset.id.desc())
        .all()
    )
    selected: dict[str, TrainingEvaluationDataset] = {}
    for row in rows:
        generation = _loads(row.grouping_policy_json, {}).get("auto_generation", {})
        if generation.get("bundle_signature") == bundle_signature:
            selected.setdefault(row.dataset_kind, row)
    if set(selected) != set(AUTO_EVALUATION_KINDS):
        return []

    sample_ids: list[int] = []
    for row in selected.values():
        manifest_path = Path(settings.MEDIA_ROOT) / row.manifest_rel_path
        if (
            int(row.sample_count or 0) < 1
            or not manifest_path.is_file()
            or hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            != row.manifest_sha256
        ):
            return []
        row_sample_ids = [
            int(sample_id)
            for (sample_id,) in db.query(TrainingEvaluationSample.sample_id)
            .filter_by(evaluation_dataset_id=row.id)
            .all()
        ]
        if len(row_sample_ids) != int(row.sample_count):
            return []
        sample_ids.extend(row_sample_ids)
    if len(sample_ids) != len(set(sample_ids)):
        return []
    return [selected[kind] for kind in AUTO_EVALUATION_KINDS]


def _auto_generated_draft_for_kind(
    db: Session,
    *,
    plan_id: int | None,
    dataset_kind: str,
) -> TrainingEvaluationDataset | None:
    if not plan_id:
        return None
    rows = (
        db.query(TrainingEvaluationDataset)
        .filter(
            TrainingEvaluationDataset.plan_id == int(plan_id),
            TrainingEvaluationDataset.dataset_kind == dataset_kind,
            TrainingEvaluationDataset.status == "DRAFT",
        )
        .order_by(TrainingEvaluationDataset.id.desc())
        .all()
    )
    for row in rows:
        generation = _loads(row.grouping_policy_json, {}).get("auto_generation")
        if generation and int(generation.get("plan_id") or 0) == int(plan_id):
            return row
    return None


def _reusable_approved_auto_evaluation_for_kind(
    db: Session,
    *,
    plan_id: int | None,
    dataset_kind: str,
    series_type: str,
    dataset_category_id: int,
    label_set_signature: str,
    bundle_signature: str,
) -> TrainingEvaluationDataset | None:
    if not plan_id:
        return None
    rows = (
        db.query(TrainingEvaluationDataset)
        .filter(
            TrainingEvaluationDataset.plan_id == int(plan_id),
            TrainingEvaluationDataset.dataset_kind == dataset_kind,
            TrainingEvaluationDataset.series_type == series_type,
            TrainingEvaluationDataset.dataset_category_id == dataset_category_id,
            TrainingEvaluationDataset.label_set_signature == label_set_signature,
            TrainingEvaluationDataset.status == "APPROVED",
        )
        .order_by(TrainingEvaluationDataset.id.desc())
        .all()
    )
    for row in rows:
        generation = _loads(row.grouping_policy_json, {}).get("auto_generation", {})
        if generation.get("bundle_signature") != bundle_signature:
            continue
        manifest_path = Path(settings.MEDIA_ROOT) / row.manifest_rel_path
        if (
            int(row.sample_count or 0) < 1
            or not manifest_path.is_file()
            or hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            != row.manifest_sha256
        ):
            continue
        sample_count = (
            db.query(TrainingEvaluationSample)
            .filter_by(evaluation_dataset_id=row.id)
            .count()
        )
        if sample_count == int(row.sample_count):
            return row
    return None


def _auto_approve_plan_evaluation_dataset(
    db: Session,
    *,
    user: User,
    dataset_id: int,
) -> dict[str, Any]:
    pending = transition_evaluation_dataset(
        db,
        user=user,
        dataset_id=dataset_id,
        target="PENDING_APPROVAL",
        reason="system generated and froze the plan evaluation manifest",
    )
    return transition_evaluation_dataset(
        db,
        user=user,
        dataset_id=int(pending["id"]),
        target="APPROVED",
        reason="system approved the plan-owned generated evaluation dataset",
    )


def _auto_create_plan_evaluation_datasets(
    db: Session,
    *,
    user: User,
    plan: AutoTrainingPlan,
    series_type: str,
    dataset_category_id: int,
    label_set_signature: str,
) -> dict[str, dict[str, Any]]:
    request = {
        "plan_id": int(plan.id),
        "series_type": series_type,
        "dataset_category_id": int(dataset_category_id),
        "label_set_signature": label_set_signature,
        "grouping_policy": {
            "identity_isolation": True,
            "capture_isolation": True,
        },
        "name_prefix": f"plan-{int(plan.id)}-evaluation",
    }
    try:
        with db.begin_nested():
            generated = auto_generate_evaluation_datasets(db, user=user, data=request)
        return {row["dataset_kind"]: row for row in generated["datasets"]}
    except LabelUException:
        # A new plan still gets its five editable drafts when no eligible pool
        # exists yet; the normal approval gate will keep the plan unusable.
        generated = {}
        for kind in AUTO_EVALUATION_KINDS:
            row = create_evaluation_dataset(
                db,
                user=user,
                data={
                    "plan_id": int(plan.id),
                    "name": f"plan-{int(plan.id)}-evaluation-{kind.lower()}",
                    "dataset_kind": kind,
                    "series_type": series_type,
                    "dataset_category_id": int(dataset_category_id),
                    "label_set_signature": label_set_signature,
                    "sample_count": 0,
                    "grouping_policy": {
                        "identity_isolation": True,
                        "capture_isolation": True,
                        "auto_generation": {
                            "version": 1,
                            "plan_id": int(plan.id),
                            "pending_source_pool": True,
                        },
                    },
                },
            )
            generated[kind] = row
        return generated


def auto_generate_evaluation_datasets(db: Session, *, user: User, data: dict) -> dict:
    ensure_auto_training_plan_writes_open()
    plan_id = int(data["plan_id"]) if data.get("plan_id") else None
    if plan_id:
        plan = _assert_manage(db, crud_auto_training.get_plan(db, plan_id), user)
        if (
            plan.series_type != data["series_type"]
            or int(plan.dataset_category_id) != int(data["dataset_category_id"])
            or plan.label_set_signature != data["label_set_signature"]
        ):
            _raise_conflict("evaluation contract does not match the training plan")
    candidates = _auto_evaluation_candidates(
        db,
        user=user,
        series_type=data["series_type"],
        dataset_category_id=int(data["dataset_category_id"]),
        label_set_signature=data["label_set_signature"],
    )
    if not candidates:
        _raise_conflict("no eligible images are available for this evaluation contract")
    partitions, source_signature, isolation_level = (
        _partition_auto_evaluation_candidates(candidates)
    )
    supplied_policy = dict(data.get("grouping_policy") or {})
    supplied_policy.setdefault("identity_isolation", True)
    supplied_policy.setdefault("capture_isolation", True)
    split_signature = _sha(
        {
            "version": 1,
            "kinds": AUTO_EVALUATION_KINDS,
            "isolation_level": isolation_level,
            "grouping_policy": supplied_policy,
        }
    )
    bundle_signature = _sha(
        {
            "series_type": data["series_type"],
            "dataset_category_id": int(data["dataset_category_id"]),
            "label_set_signature": data["label_set_signature"],
            "source_signature": source_signature,
            "split_signature": split_signature,
        }
    )
    reusable = _reusable_auto_evaluation_bundle(
        db,
        plan_id=plan_id,
        series_type=data["series_type"],
        dataset_category_id=int(data["dataset_category_id"]),
        label_set_signature=data["label_set_signature"],
        bundle_signature=bundle_signature,
    )
    if reusable:
        return {
            "reused": True,
            "candidate_count": len(candidates),
            "source_signature": source_signature,
            "bundle_signature": bundle_signature,
            "isolation_level": isolation_level,
            "datasets": [evaluation_dataset_payload(row) for row in reusable],
        }

    prefix = str(data.get("name_prefix") or "").strip() or (
        f"{data['series_type']}-{int(data['dataset_category_id'])}"
    )
    generated: list[dict[str, Any]] = []
    for kind in AUTO_EVALUATION_KINDS:
        grouping_policy = {
            **supplied_policy,
            "auto_generation": {
                "version": 1,
                "plan_id": plan_id,
                "bundle_signature": bundle_signature,
                "source_signature": source_signature,
                "split_signature": split_signature,
                "isolation_level": isolation_level,
            },
        }
        approved = _reusable_approved_auto_evaluation_for_kind(
            db,
            plan_id=plan_id,
            dataset_kind=kind,
            series_type=data["series_type"],
            dataset_category_id=int(data["dataset_category_id"]),
            label_set_signature=data["label_set_signature"],
            bundle_signature=bundle_signature,
        )
        if approved is not None:
            generated.append(evaluation_dataset_payload(approved))
            continue
        draft = _auto_generated_draft_for_kind(
            db,
            plan_id=plan_id,
            dataset_kind=kind,
        )
        if draft is not None:
            draft.grouping_policy_json = _json(grouping_policy)
            created = evaluation_dataset_payload(draft)
        else:
            created = create_evaluation_dataset(
                db,
                user=user,
                data={
                    "name": f"{prefix[:88]}-{kind.lower()}",
                    "plan_id": plan_id,
                    "dataset_kind": kind,
                    "series_type": data["series_type"],
                    "dataset_category_id": int(data["dataset_category_id"]),
                    "label_set_signature": data["label_set_signature"],
                    "sample_count": 0,
                    "grouping_policy": grouping_policy,
                },
            )
        frozen = replace_evaluation_samples(
            db,
            user=user,
            dataset_id=int(created["id"]),
            samples=[
                {
                    "task_id": item["task_id"],
                    "sample_id": item["sample_id"],
                    "split_role": AUTO_EVALUATION_SPLIT_ROLES[kind],
                    "identity_group_key": (
                        f"duplicate:{item['duplicate_group_id']}"
                        if item["duplicate_group_id"]
                        else f"image:{item['image_sha256']}"
                    ),
                    "capture_group_key": (
                        item.get("capture_group_key") or f"task:{item['task_id']}"
                    ),
                }
                for item in partitions[kind]
            ],
        )
        if plan_id:
            frozen = _auto_approve_plan_evaluation_dataset(
                db,
                user=user,
                dataset_id=int(frozen["id"]),
            )
        generated.append(frozen)
    return {
        "reused": False,
        "candidate_count": len(candidates),
        "source_signature": source_signature,
        "bundle_signature": bundle_signature,
        "isolation_level": isolation_level,
        "datasets": generated,
    }


def create_evaluation_dataset(db: Session, *, user: User, data: dict):
    ensure_auto_training_plan_writes_open()
    version_no = (
        db.query(TrainingEvaluationDataset)
        .filter(
            TrainingEvaluationDataset.owner_id == user.id,
            TrainingEvaluationDataset.name == data["name"],
        )
        .count()
        + 1
    )
    draft_identity = {
        "owner_id": int(user.id),
        "name": data["name"],
        "version_no": version_no,
        "created_at": datetime.now().isoformat(),
    }
    row = TrainingEvaluationDataset(
        plan_id=int(data["plan_id"]) if data.get("plan_id") else None,
        owner_id=user.id,
        name=data["name"],
        dataset_kind=data["dataset_kind"],
        series_type=data["series_type"],
        dataset_category_id=data["dataset_category_id"],
        label_set_signature=data["label_set_signature"],
        version_no=version_no,
        status="DRAFT",
        manifest_rel_path=data.get("manifest_rel_path")
        or f"training_evaluation/draft-{_sha(draft_identity)}.json",
        manifest_sha256=data.get("manifest_sha256") or _sha(draft_identity),
        sample_count=int(data.get("sample_count") or 0),
        grouping_policy_json=_json(data["grouping_policy"]),
        created_by=user.id,
    )
    db.add(row)
    db.flush()
    return evaluation_dataset_payload(row)


def evaluation_dataset_payload(row: TrainingEvaluationDataset):
    return {
        "id": row.id,
        "plan_id": row.plan_id,
        "owner_id": row.owner_id,
        "name": row.name,
        "dataset_kind": row.dataset_kind,
        "series_type": row.series_type,
        "dataset_category_id": row.dataset_category_id,
        "label_set_signature": row.label_set_signature,
        "version_no": row.version_no,
        "status": row.status,
        "manifest_rel_path": row.manifest_rel_path,
        "manifest_sha256": row.manifest_sha256,
        "sample_count": row.sample_count,
        "grouping_policy": _loads(row.grouping_policy_json, {}),
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "created_at": row.created_at,
        "approved_at": row.approved_at,
    }


def list_evaluation_datasets(
    db: Session,
    *,
    user: User,
    page: int = 0,
    size: int = 20,
    q: str | None = None,
):
    """Return the visible evaluation datasets for one server-side page.

    The legacy training page used to fetch every dataset and filter it in the
    browser.  Keep the ownership rule here, then apply the search and slice in
    SQL so the response remains bounded as evaluation history grows.
    """
    page = max(int(page), 0)
    size = max(int(size), 1)
    query = db.query(TrainingEvaluationDataset).outerjoin(
        DatasetCategory,
        DatasetCategory.id == TrainingEvaluationDataset.dataset_category_id,
    )
    if not is_task_superuser(user):
        query = query.filter(TrainingEvaluationDataset.owner_id == user.id)

    keyword = str(q or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        query = query.filter(
            or_(
                cast(TrainingEvaluationDataset.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TrainingEvaluationDataset.name.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingEvaluationDataset.dataset_kind.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TrainingEvaluationDataset.series_type.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(TrainingEvaluationDataset.dataset_category_id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                DatasetCategory.category_key.ilike(pattern, escape=LIKE_ESCAPE),
                DatasetCategory.name.ilike(pattern, escape=LIKE_ESCAPE),
                DatasetCategory.description.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingEvaluationDataset.label_set_signature.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(TrainingEvaluationDataset.version_no, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(TrainingEvaluationDataset.sample_count, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                TrainingEvaluationDataset.status.ilike(pattern, escape=LIKE_ESCAPE),
                TrainingEvaluationDataset.manifest_sha256.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )

    total = int(query.count())
    rows = (
        query.order_by(TrainingEvaluationDataset.id.desc())
        .offset(page * size)
        .limit(size)
        .all()
    )
    return [
        evaluation_dataset_payload(row)
        for row in rows
    ], total


def patch_evaluation_dataset(db: Session, *, user: User, dataset_id: int, data: dict):
    ensure_auto_training_plan_writes_open()
    row = (
        db.query(TrainingEvaluationDataset)
        .filter(TrainingEvaluationDataset.id == dataset_id)
        .first()
    )
    if not row or (row.owner_id != user.id and not is_task_superuser(user)):
        _raise_not_found()
    if row.status != "DRAFT":
        _raise_conflict("仅草稿评估集可编辑")
    for key in ("manifest_rel_path", "manifest_sha256", "sample_count"):
        if data.get(key) is not None:
            setattr(row, key, data[key])
    if data.get("grouping_policy") is not None:
        row.grouping_policy_json = _json(data["grouping_policy"])
    return evaluation_dataset_payload(row)


def transition_evaluation_dataset(
    db: Session,
    *,
    user: User,
    dataset_id: int,
    target: str,
    reason: str,
    admin_required=False,
):
    ensure_auto_training_plan_writes_open()
    row = (
        db.query(TrainingEvaluationDataset)
        .filter(TrainingEvaluationDataset.id == dataset_id)
        .first()
    )
    if not row or (row.owner_id != user.id and not is_task_superuser(user)):
        _raise_not_found()
    if admin_required and not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    expected = "PENDING_APPROVAL" if target == "APPROVED" else "DRAFT"
    if row.status != expected:
        _raise_conflict(f"评估集当前状态为 {row.status}")
    if target in {"PENDING_APPROVAL", "APPROVED"}:
        sample_count = (
            db.query(TrainingEvaluationSample)
            .filter_by(evaluation_dataset_id=row.id)
            .count()
        )
        manifest_path = Path(settings.MEDIA_ROOT) / row.manifest_rel_path
        if sample_count < 1 or sample_count != row.sample_count:
            _raise_conflict("evaluation dataset samples are not frozen")
        if not manifest_path.is_file():
            _raise_conflict("evaluation dataset manifest is missing")
        if (
            hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            != row.manifest_sha256
        ):
            _raise_conflict("evaluation dataset manifest hash mismatch")
    row.status = target
    if target == "APPROVED":
        row.approved_by = user.id
        row.approved_at = datetime.now()
    return evaluation_dataset_payload(row)


def list_evaluation_samples(db: Session, *, user: User, dataset_id: int):
    row = (
        db.query(TrainingEvaluationDataset)
        .filter(TrainingEvaluationDataset.id == dataset_id)
        .first()
    )
    if not row or (row.owner_id != user.id and not is_task_superuser(user)):
        _raise_not_found()
    samples = (
        db.query(TrainingEvaluationSample)
        .filter(TrainingEvaluationSample.evaluation_dataset_id == dataset_id)
        .all()
    )
    return [
        {
            "id": item.id,
            "task_id": item.task_id,
            "sample_id": item.sample_id,
            "split_role": item.split_role,
            "sample_signature": item.sample_signature,
            "annotation_sha256": item.annotation_sha256,
            "image_sha256": item.image_sha256,
            "identity_group_key": item.identity_group_key,
            "capture_group_key": item.capture_group_key,
        }
        for item in samples
    ]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_evaluation_grouping(
    samples: list[dict[str, Any]], policy: dict[str, Any]
) -> None:
    for field, enabled_key in (
        ("identity_group_key", "identity_isolation"),
        ("capture_group_key", "capture_isolation"),
    ):
        if policy.get(enabled_key) is not True:
            continue
        roles_by_group: dict[str, set[str]] = defaultdict(set)
        for item in samples:
            key = str(item.get(field) or "").strip()
            if key:
                roles_by_group[key].add(str(item["split_role"]))
        conflicts = sorted(
            key for key, roles in roles_by_group.items() if len(roles) > 1
        )
        if conflicts:
            _raise_conflict(f"{field} crosses split roles: {conflicts[:10]}")


def replace_evaluation_samples(
    db: Session,
    *,
    user: User,
    dataset_id: int,
    samples: list[dict[str, Any]],
):
    ensure_auto_training_plan_writes_open()
    row = db.query(TrainingEvaluationDataset).filter_by(id=dataset_id).first()
    if not row or (row.owner_id != user.id and not is_task_superuser(user)):
        _raise_not_found()
    if row.status != "DRAFT":
        _raise_conflict("only draft evaluation datasets can be edited")
    if not samples:
        _raise_conflict("evaluation dataset must contain at least one sample")
    sample_ids = [int(item["sample_id"]) for item in samples]
    if len(sample_ids) != len(set(sample_ids)):
        _raise_conflict("evaluation dataset contains duplicate sample ids")
    _validate_evaluation_grouping(samples, _loads(row.grouping_policy_json, {}))

    current_samples = {
        int(item.id): item
        for item in db.query(TaskSample).filter(TaskSample.id.in_(sample_ids)).all()
    }
    task_ids = sorted({int(item["task_id"]) for item in samples})
    tasks = {
        int(item.id): item
        for item in db.query(Task).filter(Task.id.in_(task_ids)).all()
    }
    contract_ids = [
        int(task.current_annotation_contract_id)
        for task in tasks.values()
        if task.current_annotation_contract_id
    ]
    contracts = {
        int(item.id): item
        for item in db.query(TaskAnnotationContract)
        .filter(TaskAnnotationContract.id.in_(contract_ids or [-1]))
        .all()
    }

    frozen: list[dict[str, Any]] = []
    for requested in samples:
        sample_id = int(requested["sample_id"])
        task_id = int(requested["task_id"])
        sample = current_samples.get(sample_id)
        task = tasks.get(task_id)
        if sample is None or task is None or int(sample.task_id) != task_id:
            _raise_conflict(f"sample #{sample_id} does not belong to task #{task_id}")
        if (
            sample.deleted_at is not None
            or sample.state != "DONE"
            or sample.file is None
        ):
            _raise_conflict(f"sample #{sample_id} is not a readable completed image")
        if int(task.dataset_category_id or 0) != int(row.dataset_category_id):
            _raise_conflict(f"task #{task_id} dataset category does not match")
        contract = contracts.get(int(task.current_annotation_contract_id or 0))
        if contract is None or contract.status not in {"active", "approved"}:
            _raise_conflict(f"task #{task_id} has no active annotation contract")
        if contract.label_set_signature != row.label_set_signature:
            _raise_conflict(f"task #{task_id} label contract does not match")
        annotation = _loads(sample.data, None)
        if not isinstance(annotation, dict):
            _raise_conflict(f"sample #{sample_id} annotation is invalid")
        from labelu.internal.application.service.attachment import (
            resolve_attachment_local_path,
        )

        source_path = resolve_attachment_local_path(sample.file)
        if source_path is None:
            _raise_conflict(f"sample #{sample_id} image is missing")
        annotation_digest = canonical_annotation_sha256(annotation)
        image_digest = _file_sha256(source_path)
        frozen.append(
            {
                "task_id": task_id,
                "sample_id": sample_id,
                "split_role": str(requested["split_role"]),
                "sample_signature": _sha(
                    {
                        "task_id": task_id,
                        "sample_id": sample_id,
                        "annotation_sha256": annotation_digest,
                        "image_sha256": image_digest,
                    }
                ),
                "annotation_sha256": annotation_digest,
                "image_sha256": image_digest,
                "identity_group_key": requested.get("identity_group_key"),
                "capture_group_key": requested.get("capture_group_key"),
            }
        )

    manifest = {
        "format": "labelu_training_evaluation_dataset_v1",
        "dataset_id": int(row.id),
        "dataset_kind": row.dataset_kind,
        "series_type": row.series_type,
        "dataset_category_id": int(row.dataset_category_id),
        "label_set_signature": row.label_set_signature,
        "version_no": int(row.version_no),
        "grouping_policy": _loads(row.grouping_policy_json, {}),
        "samples": sorted(
            frozen, key=lambda item: (item["task_id"], item["sample_id"])
        ),
    }
    content = _json(manifest).encode("utf-8")
    relative = Path("training_evaluation") / f"dataset-{row.id}" / "manifest.json"
    target = Path(settings.MEDIA_ROOT) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, target)

    db.query(TrainingEvaluationSample).filter_by(evaluation_dataset_id=row.id).delete(
        synchronize_session=False
    )
    for item in frozen:
        db.add(TrainingEvaluationSample(evaluation_dataset_id=row.id, **item))
    row.manifest_rel_path = relative.as_posix()
    row.manifest_sha256 = hashlib.sha256(content).hexdigest()
    row.sample_count = len(frozen)
    db.flush()
    return evaluation_dataset_payload(row)


def submit_quality_policy(db: Session, *, user: User, policy_name: str, data: dict):
    ensure_auto_training_plan_writes_open()
    row = QualityPolicyApproval(
        policy_name=policy_name,
        policy_version=data["policy_version"],
        policy_signature=data["policy_signature"],
        scope_signature=data["scope_signature"],
        status="PENDING_APPROVAL",
        policy_snapshot_json=_json(data["policy_snapshot"]),
        submitted_by=user.id,
        submission_reason=data["reason"],
        submitted_at=datetime.now(),
    )
    db.add(row)
    db.flush()
    return quality_approval_payload(row)


def approve_quality_policy(db: Session, *, user: User, policy_name: str, reason: str):
    ensure_auto_training_plan_writes_open()
    if not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    row = (
        db.query(QualityPolicyApproval)
        .filter(
            QualityPolicyApproval.policy_name == policy_name,
            QualityPolicyApproval.status == "PENDING_APPROVAL",
        )
        .order_by(QualityPolicyApproval.id.desc())
        .first()
    )
    if not row:
        _raise_not_found()
    row.status = "APPROVED"
    row.approved_by = user.id
    row.approved_at = datetime.now()
    row.decision_reason = reason
    return quality_approval_payload(row)


def quality_approval_payload(row: QualityPolicyApproval):
    return {
        "id": row.id,
        "policy_name": row.policy_name,
        "policy_version": row.policy_version,
        "policy_signature": row.policy_signature,
        "scope_signature": row.scope_signature,
        "status": row.status,
        "submitted_by": row.submitted_by,
        "approved_by": row.approved_by,
        "submission_reason": row.submission_reason,
        "decision_reason": row.decision_reason,
        "submitted_at": row.submitted_at,
        "approved_at": row.approved_at,
    }


def list_quality_approvals(
    db: Session,
    *,
    user: User,
    page: int = 0,
    size: int = 20,
    q: str | None = None,
):
    """Return approval history using bounded SQL search and pagination."""
    page = max(int(page), 0)
    size = max(int(size), 1)
    query = db.query(QualityPolicyApproval)
    keyword = str(q or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        query = query.filter(
            or_(
                cast(QualityPolicyApproval.id, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                QualityPolicyApproval.policy_name.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(QualityPolicyApproval.policy_version, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                QualityPolicyApproval.policy_signature.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                QualityPolicyApproval.scope_signature.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                QualityPolicyApproval.status.ilike(pattern, escape=LIKE_ESCAPE),
                QualityPolicyApproval.submission_reason.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                QualityPolicyApproval.decision_reason.ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(QualityPolicyApproval.submitted_by, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                cast(QualityPolicyApproval.approved_by, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
            )
        )
    total = int(query.count())
    rows = (
        query.order_by(QualityPolicyApproval.id.desc())
        .offset(page * size)
        .limit(size)
        .all()
    )
    return [quality_approval_payload(row) for row in rows], total


def list_quality_approval_workspace(
    db: Session,
    *,
    user: User,
    page: int = 0,
    size: int = 20,
    q: str | None = None,
):
    """Build the policy-centric approval workspace page.

    A policy can have no approval row yet, so paginating the approval history
    alone would hide policies that still need submission.  This view joins the
    bounded policy catalog with its approval history and applies the search and
    page at the policy level.  The default history endpoint remains available
    for selectors and audit consumers.
    """
    from labelu.internal.application.service import yolo_quality_policy

    page = max(int(page), 0)
    size = max(int(size), 1)
    bundle = yolo_quality_policy.list_quality_policies_bundle()
    policies = list(getattr(bundle, "policies", None) or [])
    keyword = str(q or "").strip()
    normalized_keyword = keyword.lower()

    # Search approval-only fields in SQL and keep only the matching policy
    # names.  We do not materialize the complete approval history just to
    # decide which policy cards belong on this page.
    approval_match_names: set[str] = set()
    if keyword:
        pattern = literal_like_pattern(keyword)
        approval_match_names = {
            str(name)
            for (name,) in db.query(QualityPolicyApproval.policy_name)
            .filter(
                or_(
                    cast(QualityPolicyApproval.id, String).ilike(
                        pattern, escape=LIKE_ESCAPE
                    ),
                    QualityPolicyApproval.policy_name.ilike(
                        pattern, escape=LIKE_ESCAPE
                    ),
                    cast(QualityPolicyApproval.policy_version, String).ilike(
                        pattern, escape=LIKE_ESCAPE
                    ),
                    QualityPolicyApproval.policy_signature.ilike(
                        pattern, escape=LIKE_ESCAPE
                    ),
                    QualityPolicyApproval.scope_signature.ilike(
                        pattern, escape=LIKE_ESCAPE
                    ),
                    QualityPolicyApproval.status.ilike(
                        pattern, escape=LIKE_ESCAPE
                    ),
                    QualityPolicyApproval.submission_reason.ilike(
                        pattern, escape=LIKE_ESCAPE
                    ),
                    QualityPolicyApproval.decision_reason.ilike(
                        pattern, escape=LIKE_ESCAPE
                    ),
                    cast(QualityPolicyApproval.submitted_by, String).ilike(
                        pattern, escape=LIKE_ESCAPE
                    ),
                    cast(QualityPolicyApproval.approved_by, String).ilike(
                        pattern, escape=LIKE_ESCAPE
                    ),
                )
            )
            .distinct()
            .all()
        }

    matching_policies: list[dict[str, Any]] = []
    for policy in policies:
        payload = policy.dict() if hasattr(policy, "dict") else dict(policy)
        policy_name = str(payload.get("policy_name") or "")
        policy_fields_match = any(
            normalized_keyword in str(payload.get(field) or "").lower()
            for field in (
                "policy_name",
                "scene_name",
                "model_family",
                "annotation_kind",
                "version",
            )
        )
        if normalized_keyword and not (
            policy_fields_match or policy_name in approval_match_names
        ):
            continue
        matching_policies.append(payload)

    total = len(matching_policies)
    start = page * size
    page_policies = matching_policies[start : start + size]
    page_names = [str(item.get("policy_name") or "") for item in page_policies]
    history_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if page_names:
        rows = (
            db.query(QualityPolicyApproval)
            .filter(QualityPolicyApproval.policy_name.in_(page_names))
            .order_by(QualityPolicyApproval.id.desc())
            .all()
        )
        for row in rows:
            history_by_name.setdefault(row.policy_name, []).append(
                quality_approval_payload(row)
            )

    for payload in page_policies:
        policy_name = str(payload.get("policy_name") or "")
        history = history_by_name.get(policy_name, [])
        current = history[0] if history else None
        current_version = payload.get("version")
        for item in history:
            if str(item.get("policy_version")) == str(current_version):
                current = item
                break
        payload["approval_history"] = history
        payload["current_approval"] = current
    return page_policies, total


def submit_current_quality_policy(
    db: Session, *, user: User, policy_name: str, reason: str
):
    ensure_auto_training_plan_writes_open()
    from labelu.internal.application.service import yolo_quality_policy

    bundle = yolo_quality_policy.list_quality_policies_bundle()
    policy = next(
        (item for item in bundle.policies if item.policy_name == policy_name),
        None,
    )
    if policy is None:
        _raise_not_found()
    snapshot = policy.model_dump()
    policy_signature = _sha(snapshot)
    scope_signature = _sha(
        {
            "model_family": policy.model_family,
            "annotation_kind": policy.annotation_kind,
            "label_set_signature": policy.label_set_signature,
            "rect_preset_slug": policy.rect_preset_slug,
        }
    )
    existing = (
        db.query(QualityPolicyApproval)
        .filter(
            QualityPolicyApproval.policy_signature == policy_signature,
            QualityPolicyApproval.scope_signature == scope_signature,
            QualityPolicyApproval.status.in_(["PENDING_APPROVAL", "APPROVED"]),
        )
        .order_by(QualityPolicyApproval.id.desc())
        .first()
    )
    if existing is not None:
        return quality_approval_payload(existing)
    return submit_quality_policy(
        db,
        user=user,
        policy_name=policy_name,
        data={
            "policy_version": int(policy.version),
            "policy_signature": policy_signature,
            "scope_signature": scope_signature,
            "policy_snapshot": snapshot,
            "reason": reason,
        },
    )


_RUNTIME_DEPENDENCIES = {
    "outbox_enabled": ("schema_ready",),
    "pool_enabled": ("schema_ready", "outbox_enabled"),
    "scheduler_enabled": ("schema_ready", "outbox_enabled", "pool_enabled"),
    "train_enabled": ("scheduler_enabled",),
    "worker_command_enabled": ("train_enabled",),
    "bm1688_enabled": ("train_enabled",),
    "edge_validation_enabled": ("bm1688_enabled",),
}

_RUNTIME_LABELS = {
    "schema_ready": "数据库结构",
    "observe_only": "只观察",
    "outbox_enabled": "事件队列",
    "pool_enabled": "训练池",
    "scheduler_enabled": "自动调度",
    "train_enabled": "自动训练",
    "worker_command_enabled": "标准 Worker 命令",
    "bm1688_enabled": "BM1688 编译",
    "edge_validation_enabled": "边端验证",
}


def runtime_settings_payload(*, user: User) -> dict[str, Any]:
    values = load_auto_training_switches()
    locked_keys = [
        key
        for key in AUTO_TRAINING_SWITCH_DEFAULTS
        if os.environ.get(f"LABELU_AUTO_TRAINING_{key.upper()}") is not None
    ]
    return {
        "values": values,
        "locked_keys": locked_keys,
        "can_edit": is_task_superuser(user),
        "dependencies": {
            key: list(required) for key, required in _RUNTIME_DEPENDENCIES.items()
        },
    }


def update_runtime_settings(
    *, user: User, values: dict[str, bool], reason: str
) -> dict[str, Any]:
    ensure_auto_training_plan_writes_open()
    if not is_task_superuser(user):
        raise LabelUException(ErrorCode.CODE_30001_NO_PERMISSION, status_code=403)
    current = load_auto_training_switches()
    locked = [
        key
        for key in AUTO_TRAINING_SWITCH_DEFAULTS
        if os.environ.get(f"LABELU_AUTO_TRAINING_{key.upper()}") is not None
    ]
    normalized = {
        key: bool(values.get(key, False)) for key in AUTO_TRAINING_SWITCH_DEFAULTS
    }
    for key in locked:
        normalized[key] = bool(current.get(key, False))
    errors: list[str] = []
    for key, required in _RUNTIME_DEPENDENCIES.items():
        if normalized[key]:
            missing = [item for item in required if not normalized[item]]
            if missing:
                errors.append(
                    f"{_RUNTIME_LABELS[key]}需要先开启："
                    + "、".join(_RUNTIME_LABELS[item] for item in missing)
                )
    if errors:
        raise LabelUException(
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=422,
            message="; ".join(errors),
        )
    write_group_values(
        "auto_training",
        normalized,
        updated_by=int(user.id),
        change_reason=reason,
    )
    return runtime_settings_payload(user=user)
