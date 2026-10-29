"""训练完成且验证未达标时，按 AI 迭代建议在同一数据集上自动续训（有轮数上限）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy.orm import Session, object_session

from labelu.internal.adapter.persistence import (
    crud_task,
    crud_user,
    crud_yolo_eval_report,
    crud_yolo_train_job,
)
from labelu.internal.application.command.yolo_train import CreateYoloTrainJobCommand
from labelu.internal.application.service import task as task_service
from labelu.internal.application.service.yolo_train_merge import (
    parse_source_task_ids,
    resolve_source_task_ids_for_create,
    source_task_ids_json,
)
from labelu.internal.common.config import settings
from labelu.internal.common.ai_labeling_runtime import is_qwen_visual_provider
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.yolo_train_hyperparams import (
    ALLOWED_TRAIN_HYPER_KEYS,
    merge_ai_suggested_into_hyperparams,
    merge_ai_suggested_into_iteration_params,
    merge_iteration_param_dicts,
    normalize_hyperparams,
    protect_automatic_iteration_patience,
)
from labelu.internal.domain.models.yolo_eval_report import YoloEvalReport
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob, YoloTrainStatus

_KIND_NEXT = "next_iteration"
_EVAL_PROVIDER_ORDER = ("volcengine",)


def _disabled_eval_providers() -> set[str]:
    raw = (settings.YOLO_EVAL_DISABLED_PROVIDERS or "").strip()
    if not raw:
        return set()
    return {p.strip() for p in raw.split(",") if p.strip()}


def _parse_parent_trained_job_id(model_weights: Optional[str]) -> Optional[int]:
    token = (model_weights or "").strip()
    if not token.startswith("trained:"):
        return None
    try:
        return int(token.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def count_training_lineage_jobs(db: Session, job: YoloTrainJob) -> int:
    """从当前作业沿 trained: 父链回溯，计同一迭代链上的训练次数。"""
    seen: set[int] = set()
    current: Optional[YoloTrainJob] = job
    count = 0
    while current and current.id not in seen:
        seen.add(int(current.id))
        count += 1
        parent_id = _parse_parent_trained_job_id(current.model_weights)
        if not parent_id:
            break
        current = crud_yolo_train_job.get(db=db, job_id=parent_id)
    return count


def resolve_lineage_root_job(db: Session, job: YoloTrainJob) -> YoloTrainJob:
    """迭代链根作业（最初的数据集与任务配置）。"""
    current = job
    seen: set[int] = set()
    while True:
        if current.id in seen:
            break
        seen.add(int(current.id))
        parent_id = _parse_parent_trained_job_id(current.model_weights)
        if not parent_id:
            break
        parent = crud_yolo_train_job.get(db=db, job_id=parent_id)
        if not parent:
            break
        current = parent
    return current


def _inner_from_report_row(row: YoloEvalReport) -> Dict[str, Any]:
    if row.error_message:
        return {}
    inner = merge_ai_suggested_into_iteration_params(row.suggested_params)
    if inner:
        return inner
    body = (row.body or "").strip()
    if not body:
        return {}
    from labelu.internal.application.service.yolo_train_evaluation import (
        _extract_recommended_params_json,
    )

    block = _extract_recommended_params_json(body)
    if block:
        return merge_ai_suggested_into_iteration_params(block)
    return {}


def _merged_iteration_params_from_reports(
    *rows: Optional[YoloEvalReport],
) -> Dict[str, Any]:
    """合并全维度 + 下一次迭代建议（后者优先）。"""
    layers: list[Dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        inner = _inner_from_report_row(row)
        if inner:
            layers.append(inner)
    return merge_iteration_param_dicts(*layers)


def _count_actionable_suggestion_keys(inner: Dict[str, Any]) -> int:
    if not inner:
        return 0
    hyper_keys = set(ALLOWED_TRAIN_HYPER_KEYS)
    n = 0
    for key, val in inner.items():
        if key in ("notes", "model_weights", "device"):
            continue
        if val is None or val == "":
            continue
        if key in hyper_keys or key in ("epochs", "imgsz"):
            n += 1
    return n


def pick_best_next_iteration_report(
    rows: List[YoloEvalReport],
) -> Optional[YoloEvalReport]:
    disabled = _disabled_eval_providers()
    latest: Dict[str, YoloEvalReport] = {}
    for row in rows:
        pk = (row.provider_key or "").strip()
        if not pk or pk in disabled or is_qwen_visual_provider("", "", pk):
            continue
        if row.error_message:
            continue
        if not (row.suggested_params or (row.body or "").strip()):
            continue
        if pk not in latest or row.id > latest[pk].id:
            latest[pk] = row

    pick: Optional[YoloEvalReport] = None
    pick_score = 0
    for pk, row in latest.items():
        inner = _inner_from_report_row(row)
        score = _count_actionable_suggestion_keys(inner)
        if score <= 0:
            continue
        ia = _EVAL_PROVIDER_ORDER.index(pk) if pk in _EVAL_PROVIDER_ORDER else 999
        ib = (
            _EVAL_PROVIDER_ORDER.index(pick.provider_key)
            if pick and pick.provider_key in _EVAL_PROVIDER_ORDER
            else 999
        )
        if (
            not pick
            or score > pick_score
            or (score == pick_score and ia < ib)
            or (score == pick_score and ia == ib and row.id > pick.id)
        ):
            pick = row
            pick_score = score
    return pick


def _job_is_validation_passed(job: YoloTrainJob) -> bool:
    from labelu.internal.application.service import task as task_service
    from labelu.internal.application.service.yolo_quality_assessment import (
        build_training_quality_assessment,
    )
    from labelu.internal.application.service.yolo_train import (
        _sample_counts_for_job,
        _training_val_metrics_for_job,
    )

    db = object_session(job)
    task = crud_task.get(db=db, task_id=job.task_id) if db is not None else None
    rect_slug = task_service._task_rect_preset_slug(task) if task else None
    counts = _sample_counts_for_job(job)
    result = build_training_quality_assessment(
        training_val_metrics=_training_val_metrics_for_job(job),
        total_sample_count=counts.get("total"),
        rect_preset_slug=rect_slug,
    )
    return (result.status or "").strip() == "达标"


def _job_hyperparams_dict(job: YoloTrainJob) -> Dict[str, Any]:
    raw = (job.hyperparams_json or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _has_active_child_job(db: Session, parent_job_id: int) -> bool:
    token = f"trained:{int(parent_job_id)}"
    row = (
        db.query(YoloTrainJob)
        .filter(
            YoloTrainJob.model_weights == token,
            YoloTrainJob.status.in_(
                [
                    YoloTrainStatus.PENDING.value,
                    YoloTrainStatus.PROCESSING.value,
                ]
            ),
        )
        .first()
    )
    return row is not None


def _build_followup_command(
    db: Session,
    *,
    completed_job: YoloTrainJob,
    report: YoloEvalReport,
    full_report: Optional[YoloEvalReport] = None,
) -> Optional[CreateYoloTrainJobCommand]:
    root = resolve_lineage_root_job(db, completed_job)
    root_task = crud_task.get(db=db, task_id=root.task_id)
    if not root_task:
        return None

    inner = _merged_iteration_params_from_reports(full_report, report)
    if not inner:
        return None

    from labelu.internal.common.yolo_train_hyperparams import (
        get_iteration_baseline_hyperparams,
    )

    rect_slug = task_service._task_rect_preset_slug(root_task)
    sop_hp = get_iteration_baseline_hyperparams(rect_slug)
    base_hp = normalize_hyperparams(_job_hyperparams_dict(completed_job))
    suggested_hp = normalize_hyperparams(
        {k: v for k, v in inner.items() if k not in ("epochs", "imgsz")}
    )
    hyperparams = normalize_hyperparams({**base_hp, **sop_hp, **suggested_hp})

    epochs = int(completed_job.epochs or 50)
    imgsz = int(completed_job.imgsz or 640)
    if inner.get("epochs") is not None:
        try:
            epochs = max(1, min(3000, int(float(inner["epochs"]))))
        except (TypeError, ValueError):
            pass
    if inner.get("imgsz") is not None:
        try:
            imgsz = max(320, min(1280, int(float(inner["imgsz"]))))
        except (TypeError, ValueError):
            pass

    hyperparams = protect_automatic_iteration_patience(hyperparams, epochs=epochs)

    source_ids = parse_source_task_ids(root)
    merge_ids = [tid for tid in source_ids if tid != int(root.task_id)]
    return CreateYoloTrainJobCommand(
        model_weights="yolo11n.pt",
        weights_source="trained",
        weights_ref_id=int(completed_job.id),
        epochs=epochs,
        imgsz=imgsz,
        category=(completed_job.category or root.category or "").strip()[:128],
        hyperparams=hyperparams or None,
        merge_task_ids=merge_ids or None,
        rect_preset_slug=rect_slug or None,
    )


def maybe_start_auto_iteration_train(job_id: int) -> None:
    """在 next_iteration 评估完成后调用；未达标且未超轮数时自动创建续训作业。"""
    if not settings.YOLO_TRAIN_ENABLED:
        return
    if not settings.YOLO_AUTO_ITERATION_ENABLED:
        return

    from labelu.internal.common.db import session_scope
    from labelu.internal.application.service.yolo_train import (
        _model_weights_field_for_storage,
        _submit_yolo_train_job,
        _validate_weights_source_at_create,
    )

    with session_scope() as db:
        job = crud_yolo_train_job.get(db=db, job_id=job_id)
        if not job or job.status != YoloTrainStatus.COMPLETED.value:
            return
        if getattr(job, "auto_training_plan_id", None) is not None:
            logger.info(
                "skip legacy auto iteration train job {}: managed by auto training plan {}",
                job_id,
                job.auto_training_plan_id,
            )
            return
        if _job_is_validation_passed(job):
            logger.info("skip auto iteration train job {}: validation passed", job_id)
            return
        rounds = count_training_lineage_jobs(db, job)
        if rounds >= int(settings.YOLO_AUTO_ITERATION_MAX_ROUNDS):
            logger.info(
                "skip auto iteration train job {}: lineage rounds {} >= max {}",
                job_id,
                rounds,
                settings.YOLO_AUTO_ITERATION_MAX_ROUNDS,
            )
            return
        if _has_active_child_job(db, job_id):
            logger.info("skip auto iteration train job {}: child already active", job_id)
            return
        if (job.ai_eval_next_status or "").strip() != "COMPLETED":
            logger.info(
                "skip auto iteration train job {}: next_iteration eval not completed",
                job_id,
            )
            return

        reports_next = crud_yolo_eval_report.list_by_job_and_kind(
            db, job_id=job_id, report_kind=_KIND_NEXT
        )
        reports_full = crud_yolo_eval_report.list_by_job_and_kind(
            db, job_id=job_id, report_kind="full_analysis"
        )
        pick = pick_best_next_iteration_report(reports_next)
        pick_full = pick_best_next_iteration_report(reports_full)
        if not pick and not pick_full:
            logger.info(
                "skip auto iteration train job {}: no actionable eval reports",
                job_id,
            )
            return
        if not pick:
            pick = pick_full

        owner = crud_user.get(db, job.created_by) if job.created_by else None
        if not owner:
            logger.warning("skip auto iteration train job {}: missing owner", job_id)
            return

        cmd = _build_followup_command(
            db,
            completed_job=job,
            report=pick,
            full_report=pick_full if pick_full and pick_full.id != pick.id else None,
        )
        if not cmd:
            return

        root = resolve_lineage_root_job(db, job)
        task_id = int(root.task_id)

        try:
            _validate_weights_source_at_create(db, owner, cmd)
            resolve_source_task_ids_for_create(
                db, primary_task_id=task_id, cmd=cmd, current_user=owner
            )
        except Exception as exc:
            logger.warning(
                "skip auto iteration train job {}: preflight failed: {}",
                job_id,
                str(exc)[:300],
            )
            return

        stored_weights = _model_weights_field_for_storage(cmd)
        hp_json = None
        if cmd.hyperparams:
            hp_json = json.dumps(
                normalize_hyperparams(cmd.hyperparams), ensure_ascii=False
            )
        source_ids, _ = resolve_source_task_ids_for_create(
            db, primary_task_id=task_id, cmd=cmd, current_user=owner
        )

        with begin_transaction(db):
            new_job = crud_yolo_train_job.create(
                db=db,
                task_id=task_id,
                user_id=owner.id,
                model_weights=stored_weights,
                epochs=cmd.epochs,
                imgsz=cmd.imgsz,
                category=(cmd.category or "").strip()[:128],
                hyperparams_json=hp_json,
                source_task_ids_json=source_task_ids_json(source_ids),
            )
            new_job_id = int(new_job.id)
            submit_cmd = cmd.copy(update={"model_weights": stored_weights})

    logger.info(
        "auto iteration train: parent job {} -> new job {} (lineage round {}/{})",
        job_id,
        new_job_id,
        rounds + 1,
        settings.YOLO_AUTO_ITERATION_MAX_ROUNDS,
    )
    _submit_yolo_train_job(new_job_id, task_id, submit_cmd)
