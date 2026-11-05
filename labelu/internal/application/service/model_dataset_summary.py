from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_model_version
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.model_version import ModelVersion
from labelu.internal.domain.models.user import User


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


def _as_float(value: Any) -> float | None:
    try:
        return round(float(value), 6) if value is not None else None
    except (TypeError, ValueError):
        return None


def _model_metrics(model: ModelVersion) -> dict[str, float | None]:
    raw = _json_dict(getattr(model, "metrics_json", None))
    merged: dict[str, Any] = {}
    for value in raw.values():
        if isinstance(value, dict):
            merged.update(value)
    return {
        "map50_95": _as_float(raw.get("map50_95") or merged.get("map50_95")),
        "map50": _as_float(raw.get("map50") or merged.get("map50")),
        "precision": _as_float(raw.get("precision") or merged.get("precision")),
        "recall": _as_float(raw.get("recall") or merged.get("recall")),
    }


def _artifact_status(model: ModelVersion) -> dict[str, str | None]:
    statuses: dict[str, str | None] = {}
    for artifact in getattr(model, "artifacts", []) or []:
        artifact_type = str(getattr(artifact, "artifact_type", "") or "").lower()
        if not artifact_type:
            continue
        statuses[artifact_type] = str(getattr(artifact, "status", "") or "")
    return {
        "pt": statuses.get("pt"),
        "onnx": statuses.get("onnx"),
        "bmodel": statuses.get("bmodel"),
    }


def _model_payload(model: ModelVersion) -> dict[str, Any]:
    artifacts = _artifact_status(model)
    return {
        "model_version_id": int(model.id),
        "display_name": model.display_name,
        "model_key": model.model_key,
        "status": model.status,
        "created_at": (
            model.created_at.isoformat()
            if isinstance(getattr(model, "created_at", None), datetime)
            else getattr(model, "created_at", None)
        ),
        "version_no": getattr(model, "version_no", None),
        "branch_version_no": getattr(model, "branch_version_no", None),
        "metrics": _model_metrics(model),
        "artifacts": artifacts,
        "published": bool(
            getattr(model, "published_catalog_id", None)
            or str(getattr(model, "status", "") or "").upper() == "PUBLISHED"
        ),
    }


def list_model_dataset_summaries(
    db: Session,
    current_user: User,
    *,
    page: int,
    size: int,
    search: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    unrestricted = is_task_superuser(current_user)
    all_models: list[ModelVersion] = []
    page_no = 0
    while True:
        rows, total = crud_model_version.list_accessible(
            db,
            user_id=None if unrestricted else int(current_user.id),
            unrestricted=unrestricted,
            page=page_no,
            size=500,
            search=search,
        )
        all_models.extend(rows)
        if not rows or len(all_models) >= int(total):
            break
        page_no += 1

    grouped: dict[int, list[ModelVersion]] = {}
    for model in all_models:
        dataset_id = getattr(model, "dataset_id", None)
        if dataset_id:
            grouped.setdefault(int(dataset_id), []).append(model)

    dataset_ids = sorted(grouped)
    dataset_rows = (
        db.query(Dataset).filter(Dataset.id.in_(dataset_ids)).all()
        if dataset_ids
        else []
    )
    datasets = {int(row.id): row for row in dataset_rows}
    ordered_ids = [
        dataset_id
        for dataset_id in dataset_ids
        if dataset_id in datasets
    ]
    ordered_ids.sort(
        key=lambda dataset_id: max(
            (
                getattr(model, "created_at", None)
                for model in grouped[dataset_id]
            ),
            default=None,
        )
        or datetime.min,
        reverse=True,
    )
    start = max(0, int(page)) * max(1, int(size))
    selected = ordered_ids[start : start + max(1, int(size))]
    output: list[dict[str, Any]] = []
    for dataset_id in selected:
        models = sorted(
            grouped[dataset_id],
            key=lambda item: (
                getattr(item, "created_at", None) or datetime.min,
                int(item.id),
            ),
            reverse=True,
        )
        payloads = [_model_payload(model) for model in models]
        latest = payloads[0] if payloads else None
        best = max(
            payloads,
            key=lambda item: item.get("metrics", {}).get("map50_95") or 0,
            default=None,
        )
        dataset = datasets[dataset_id]
        output.append(
            {
                "dataset_id": dataset_id,
                "dataset_name": dataset.name,
                "dataset_label_contract_id": getattr(
                    dataset, "dataset_label_contract_id", None
                ),
                "dataset_label_contract_revision": getattr(
                    dataset, "dataset_label_contract_revision", None
                ),
                "model_count": len(payloads),
                "latest_model": latest,
                "best_model": best,
                "last_updated_at": latest.get("created_at") if latest else None,
                "models": payloads,
            }
        )
    return output, len(ordered_ids)
