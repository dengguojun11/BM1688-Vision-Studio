"""自动迭代训练的统一质量判定谓词。

迭代是否继续、是否进入编译、页面标签都走这里，保证「一个谓词定义达标」，
避免出现页面显示达标、系统却继续迭代的情况。

判定口径来自页面可视化的自动迭代策略（``auto_iteration`` 配置组），默认值
保持与历史行为一致：不开启自动迭代时该谓词不参与任何流程。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy.orm import Session

from labelu.internal.common.auto_iteration_runtime import load_auto_iteration_policy
from labelu.internal.common.yolo_training_metrics import (
    read_per_class_metrics_from_summary,
)

PASS_STATUSES = frozenset({"达标", "条件达标"})
FAIL_STATUSES = frozenset({"不达标", "待验证"})


@dataclass(frozen=True)
class IterationQualityDecision:
    passed: bool
    status: str
    metric_basis: str
    score: Optional[float]
    threshold: float
    summary: str
    reasons: tuple[str, ...] = ()

    def as_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "status": self.status,
            "metric_basis": self.metric_basis,
            "score": self.score,
            "threshold": self.threshold,
            "summary": self.summary,
            "reasons": list(self.reasons),
        }


def _job_val_metrics(job: Any):
    from labelu.internal.application.service.yolo_train import (
        _training_val_metrics_for_job,
    )

    return _training_val_metrics_for_job(job)


def _job_sample_total(job: Any) -> Optional[int]:
    from labelu.internal.application.service.yolo_train import _sample_counts_for_job

    counts = _sample_counts_for_job(job)
    return counts.get("total")


def _task_rect_slug(db: Optional[Session], job: Any) -> Optional[str]:
    if db is None:
        return None
    from labelu.internal.adapter.persistence import crud_task
    from labelu.internal.application.service import task as task_service

    task = crud_task.get(db=db, task_id=int(job.task_id))
    return task_service._task_rect_preset_slug(task) if task else None


def _per_class_recall(job: Any) -> dict[str, float]:
    rows = read_per_class_metrics_from_summary(
        getattr(job, "metrics_summary", None), split="val"
    )
    out: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "").strip()
        try:
            value = float(row.get("recall"))
        except (TypeError, ValueError):
            continue
        if label and value == value:
            out[label] = value
    return out


def _parse_parent_job_id(model_weights: Optional[str]) -> Optional[int]:
    token = str(model_weights or "").strip()
    if not token.startswith("trained:"):
        return None
    try:
        return int(token.split(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _score_for_job(job: Any, metric_basis: str) -> Optional[float]:
    metrics = _job_val_metrics(job)
    if metrics is None:
        return None
    if metric_basis == "val_recall":
        return metrics.recall
    if metric_basis == "overall_recall":
        return metrics.overall_recall
    return metrics.map50_95 if metrics.map50_95 is not None else metrics.map50


def _previous_round_score(
    db: Optional[Session], job: Any, metric_basis: str
) -> Optional[float]:
    if db is None:
        return None
    parent_id = _parse_parent_job_id(getattr(job, "model_weights", None))
    if not parent_id:
        return None
    from labelu.internal.adapter.persistence import crud_yolo_train_job

    parent = crud_yolo_train_job.get(db=db, job_id=int(parent_id))
    if parent is None:
        return None
    return _score_for_job(parent, metric_basis)


def _decision_passed(status: str) -> bool:
    return str(status or "").strip() in PASS_STATUSES


def evaluate_iteration_quality(
    db: Optional[Session],
    job: Any,
    *,
    policy: Optional[dict[str, Any]] = None,
) -> IterationQualityDecision:
    """对单个训练作业做统一质量判定（迭代/编译门禁共用）。"""
    from labelu.internal.application.service.yolo_quality_assessment import (
        build_training_quality_assessment,
    )

    effective = policy or load_auto_iteration_policy()
    metric_basis = str(effective.get("metric_basis") or "val_recall")
    overrides = {
        "metric_basis": metric_basis,
        "pass_threshold": effective.get("pass_threshold"),
        "warn_threshold": effective.get("warn_threshold"),
        "allow_conditional_pass": effective.get("allow_conditional_pass"),
        "precision_floor_enabled": effective.get("precision_floor_enabled"),
        "precision_floor": effective.get("precision_floor"),
    }
    item = build_training_quality_assessment(
        training_val_metrics=_job_val_metrics(job),
        total_sample_count=_job_sample_total(job),
        rect_preset_slug=_task_rect_slug(db, job),
        policy_overrides=overrides,
    )
    status = str(item.status or "").strip()
    reasons: list[str] = list(item.reasons or [])

    if _decision_passed(status) and bool(effective.get("min_class_recall_enabled")):
        floor = float(effective.get("min_class_recall") or 0.0)
        per_class = _per_class_recall(job)
        if per_class:
            label, worst = min(per_class.items(), key=lambda pair: pair[1])
            if float(worst) < floor:
                status = "不达标"
                reasons.append(
                    f"类别「{label}」召回 {float(worst):.4f} 低于单类下限 {floor:.2f}"
                )
        else:
            reasons.append("缺少各类别召回指标，跳过单类最低召回守护")

    if _decision_passed(status):
        previous = _previous_round_score(db, job, metric_basis)
        if previous is not None and item.score is not None:
            delta = float(item.score) - float(previous)
            max_regression = float(effective.get("max_regression") or 0.0)
            if delta < -abs(max_regression):
                status = "不达标"
                reasons.append(
                    f"相对上一轮下降 {-delta:.4f}，超过回退上限 {max_regression:.2f}"
                )

    passed = _decision_passed(status)
    return IterationQualityDecision(
        passed=passed,
        status=status,
        metric_basis=metric_basis,
        score=item.score,
        threshold=float(effective.get("pass_threshold") or 0.6),
        summary=str(item.summary or ""),
        reasons=tuple(reasons),
    )
