from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_dataset, crud_yolo_train_job
from labelu.internal.application.service.auto_iteration_quality import (
    evaluate_iteration_quality,
)
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.dataset import DatasetVersion
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob


def _json_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        import json

        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _metric(job: YoloTrainJob) -> dict[str, float | None]:
    summary = _json_dict(getattr(job, "metrics_summary", None))
    candidates = []
    for key in ("best_pt_val_metrics", "final_metrics", "last_epoch_metrics"):
        value = summary.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    metrics = candidates[0] if candidates else {}
    return {
        "map50_95": _as_float(metrics.get("map50_95")),
        "map50": _as_float(metrics.get("map50")),
        "precision": _as_float(metrics.get("precision")),
        "recall": _as_float(metrics.get("recall")),
    }


def _as_float(value: Any) -> float | None:
    try:
        return round(float(value), 6) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parent_job_id(job: YoloTrainJob) -> int | None:
    value = str(getattr(job, "model_weights", "") or "").strip()
    if value.startswith("trained:"):
        try:
            return int(value.split(":", 1)[1])
        except (IndexError, ValueError):
            return None
    return getattr(job, "parent_training_job_id", None)


def _job_payload(db: Session, job: YoloTrainJob, task_name: str | None) -> dict[str, Any]:
    quality = evaluate_iteration_quality(db, job)
    metrics = _metric(job)
    return {
        "job_id": int(job.id),
        "name": getattr(job, "name", None) or f"训练作业 #{job.id}",
        "task_id": int(job.task_id),
        "task_name": task_name,
        "status": getattr(job, "status", None),
        "created_at": (
            job.created_at.isoformat()
            if isinstance(getattr(job, "created_at", None), datetime)
            else getattr(job, "created_at", None)
        ),
        "auto_iteration_root_id": getattr(job, "auto_iteration_root_id", None),
        "auto_iteration_round_no": getattr(job, "auto_iteration_round_no", None),
        "parent_job_id": _parent_job_id(job),
        "lineage_kind": (
            "AUTO"
            if getattr(job, "auto_iteration_root_id", None) is not None
            and int(getattr(job, "auto_iteration_round_no", None) or 1) > 1
            else "MANUAL"
        ),
        "quality_status": quality.status,
        "quality_passed": bool(quality.passed),
        "metrics": metrics,
    }


def _dataset_version_count(db: Session, dataset_id: int) -> int:
    return int(
        db.query(func.count(DatasetVersion.id))
        .filter(DatasetVersion.dataset_id == int(dataset_id))
        .scalar()
        or 0
    )


def list_training_dataset_summaries(
    db: Session,
    current_user: User,
    *,
    page: int,
    size: int,
    search: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    datasets, total = crud_dataset.list_datasets(
        db,
        0,
        500,
        user_id=None if is_task_superuser(current_user) else int(current_user.id),
        search=search,
    )
    if not datasets:
        return [], total

    rows, _ = crud_yolo_train_job.list_jobs_for_user(
        db,
        int(current_user.id),
        page=0,
        size=1000,
        search=search,
        task_access_unrestricted=is_task_superuser(current_user),
        task_access_builtin_admin=False,
    )
    grouped: dict[int, list[tuple[YoloTrainJob, Any]]] = {}
    for job, task in rows:
        dataset_id = getattr(task, "dataset_id", None)
        if not dataset_id:
            manifest = _json_dict(getattr(job, "dataset_manifest_json", None))
            dataset_id = manifest.get("dataset_id")
        try:
            dataset_id = int(dataset_id) if dataset_id else None
        except (TypeError, ValueError):
            dataset_id = None
        if dataset_id:
            grouped.setdefault(dataset_id, []).append((job, task))

    output: list[dict[str, Any]] = []
    for dataset in datasets:
        jobs = grouped.get(int(dataset.id), [])
        payloads = [
            _job_payload(db, job, getattr(task, "name", None))
            for job, task in jobs
        ]
        payloads.sort(
            key=lambda item: (
                int(item.get("auto_iteration_round_no") or 1),
                int(item.get("job_id") or 0),
            )
        )
        latest = max(
            payloads,
            key=lambda item: (item.get("created_at") or "", item.get("job_id") or 0),
            default=None,
        )
        completed = [
            item
            for item in payloads
            if item.get("metrics", {}).get("map50_95") is not None
        ]
        best = max(
            completed,
            key=lambda item: (
                1 if item.get("quality_passed") else 0,
                item.get("metrics", {}).get("map50_95") or 0,
            ),
            default=None,
        )
        output.append(
            {
                "dataset_id": int(dataset.id),
                "dataset_name": dataset.name,
                "dataset_label_contract_id": getattr(
                    dataset, "dataset_label_contract_id", None
                ),
                "dataset_label_contract_revision": getattr(
                    dataset, "dataset_label_contract_revision", None
                ),
                "dataset_label_contract_status": getattr(
                    dataset, "dataset_label_contract_status", None
                ),
                "version_count": _dataset_version_count(db, int(dataset.id)),
                "job_count": len(payloads),
                "last_trained_at": latest.get("created_at") if latest else None,
                "latest_job": latest,
                "best_job": best,
                "lineage": payloads,
            }
        )
    output.sort(
        key=lambda item: item.get("last_trained_at") or "",
        reverse=True,
    )
    start = max(0, int(page)) * max(1, int(size))
    return output[start : start + max(1, int(size))], total
