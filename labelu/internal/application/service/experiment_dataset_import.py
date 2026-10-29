from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_annotation_taxonomy
from labelu.internal.adapter.persistence import crud_attachment
from labelu.internal.adapter.persistence import crud_auto_label_job
from labelu.internal.adapter.persistence import crud_datasource
from labelu.internal.adapter.persistence import crud_experiment_dataset_import
from labelu.internal.adapter.persistence import crud_experiment_dataset_group
from labelu.internal.adapter.persistence import crud_sample
from labelu.internal.adapter.persistence import crud_task
from labelu.internal.adapter.persistence import crud_user
from labelu.internal.adapter.persistence import crud_yolo_bm1688_export
from labelu.internal.adapter.persistence import crud_yolo_train_job
from labelu.internal.application.command.experiment_dataset_import import (
    CreateExperimentDatasetImportCommand,
)
from labelu.internal.application.response.experiment_dataset_import import (
    ExperimentDatasetImportAcceptedResponse,
    ExperimentDatasetCreatedTaskResponse,
    ExperimentDatasetImportDetailResponse,
    ExperimentDatasetImportListItemResponse,
)
from labelu.internal.application.service import (
    annotation_taxonomy as annotation_taxonomy_service,
)
from labelu.internal.application.service import datasource as datasource_service
from labelu.internal.application.service import system_labels as system_label_service
from labelu.internal.application.service import sample as sample_service
from labelu.internal.application.service import yolo_preannotate
from labelu.internal.application.service.auto_sample_preannotate import (
    schedule_auto_preannotate_for_new_samples,
)
from labelu.internal.application.service.pre_annotation_label_normalize import (
    normalize_pre_annotation_payload_labels,
)
from labelu.internal.common.bootstrap_admin import DEFAULT_ADMIN_USERNAME
from labelu.internal.common.service_account import ensure_open_system_user
from labelu.internal.common.config import settings
from labelu.internal.common.strategy_integration_s3_config import get_active_endpoint
from labelu.internal.common.crypto import encrypt_value
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.storage import build_attachment_api_path
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.data_source import DataSource
from labelu.internal.domain.models.annotation_taxonomy import DatasetCategory
from labelu.internal.domain.models.dataset import Dataset
from labelu.internal.domain.models.experiment_dataset_import import (
    ExperimentDatasetImport,
)
from labelu.internal.domain.models.experiment_dataset_group import (
    ExperimentDatasetGroup,
)
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import SampleState
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.user import User

IMPORT_STATUS_PENDING = "PENDING"
IMPORT_STATUS_PROCESSING = "PROCESSING"
IMPORT_STATUS_WAITING_FOR_SAMPLES = "WAITING_FOR_SAMPLES"
IMPORT_STATUS_WAITING_FOR_MODEL = "WAITING_FOR_MODEL"
IMPORT_STATUS_WAITING_FOR_CATEGORY = "WAITING_FOR_CATEGORY"
IMPORT_STATUS_COMPLETED = "COMPLETED"
IMPORT_STATUS_PARTIAL_COMPLETED = "PARTIAL_COMPLETED"
IMPORT_STATUS_FAILED = "FAILED"

# Retry metadata lives in the immutable source payload so a retry can be
# related to its failed predecessor without changing the historical row or
# requiring a destructive package-id reuse.  The command validator ignores
# this private server marker when the Worker reconstructs the request.
_RETRY_MARKER_KEY = "_labelu_retry"
_RETRY_ACTIVE_STATUSES = {
    IMPORT_STATUS_PENDING,
    IMPORT_STATUS_PROCESSING,
    IMPORT_STATUS_WAITING_FOR_SAMPLES,
    IMPORT_STATUS_WAITING_FOR_MODEL,
    IMPORT_STATUS_WAITING_FOR_CATEGORY,
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".gif"}
PREANNOTATION_SOURCE_TYPE = "experiment_dataset_metadata"
ANNOTATION_DISPLAY_MAX_SIDE = 1672
REVIEWED_FALSE_POSITIVE_DAILY_DATASET_TYPE = "reviewed_false_positive_daily"
TASK_NAME_MAX_LENGTH = 64
TASK_NAME_PACKAGE_SUFFIX_LENGTH = 8
S3_DOWNLOAD_CHUNK_SIZE = 8 * 1024 * 1024
S3_RANGE_REQUEST_CHUNK_SIZE = 64 * 1024


class ImportWorkerLeaseLost(RuntimeError):
    """Raised when an import Worker no longer owns its fencing generation."""


LeaseGuard = Callable[[], bool]


def _assert_import_worker_lease(lease_guard: LeaseGuard | None) -> None:
    if lease_guard is not None and not lease_guard():
        raise ImportWorkerLeaseLost("第三方 S3 回流 Worker 租约已失效")

# The external event name describes the business scenario, while the LabelU
# preset slug describes the annotation task taxonomy. Keep this mapping
# explicit when multiple presets share the same label values.
EVENT_TYPE_PRESET_SLUGS = {
    "electrombileWithoutHelmet": "ridingNoHelmet",
}

GROUP_STATUS_COLLECTING = "COLLECTING"
# The group uses the same persisted status values as an import row.  Keep the
# explicit alias because callers and UI tests describe the aggregate state as
# "waiting for samples" as well.
GROUP_STATUS_WAITING_FOR_SAMPLES = IMPORT_STATUS_WAITING_FOR_SAMPLES
GROUP_STATUS_WAITING_FOR_MODEL = "WAITING_FOR_MODEL"
GROUP_STATUS_WAITING_FOR_CATEGORY = "WAITING_FOR_CATEGORY"
GROUP_STATUS_PROCESSING = "PROCESSING"
GROUP_STATUS_CREATED = "CREATED"
GROUP_STATUS_FAILED = "FAILED"


class _ImportWorkerLeaseHeartbeat:
    """Renew a long S3 import lease from a short-lived independent Session."""

    def __init__(self, *, import_job_id: int, owner: str, fencing_token: int):
        self.import_job_id = int(import_job_id)
        self.owner = str(owner)
        self.fencing_token = int(fencing_token)
        self.lease_seconds = int(settings.EXPERIMENT_DATASET_WORKER_LEASE_SECONDS)
        self.interval_seconds = max(5.0, min(self.lease_seconds / 3.0, 60.0))
        self.stop_event = threading.Event()
        self.lost_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        self.thread = threading.Thread(
            target=self._run,
            name=f"experiment-import-lease-{self.import_job_id}",
            daemon=True,
        )
        self.thread.start()

    def _run(self) -> None:
        while not self.stop_event.wait(self.interval_seconds):
            try:
                with session_scope() as db:
                    with begin_transaction(db):
                        renewed = crud_experiment_dataset_import.renew_worker_lease(
                            db,
                            import_job_id=self.import_job_id,
                            owner=self.owner,
                            fencing_token=self.fencing_token,
                            lease_seconds=self.lease_seconds,
                        )
                if not renewed:
                    self.lost_event.set()
                    return
            except Exception as exc:  # pragma: no cover - depends on DB outage
                logger.warning(
                    "experiment dataset import lease renewal failed job_id={} error={}",
                    self.import_job_id,
                    exc,
                )
                self.lost_event.set()
                return

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=min(self.interval_seconds, 5.0))

    @property
    def lost(self) -> bool:
        return self.lost_event.is_set()


def _externalize_model_id_keys(value: Any) -> Any:
    """Expose camelCase model IDs without changing internal persistence data."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            output_key = "modelId" if key == "model_id" else key
            if output_key == "modelId" and output_key in result:
                continue
            result[output_key] = _externalize_model_id_keys(item)
        return result
    if isinstance(value, list):
        return [_externalize_model_id_keys(item) for item in value]
    return value


def _build_s3_client(ds: DataSource):
    return datasource_service._build_s3_client(ds)


def _target_signature_from_prefix(prefix: str) -> str:
    text = str(prefix or "").strip().rstrip("/")
    if not text:
        return ""
    return text.split("/")[-1]


def _aggregation_identity(cmd: CreateExperimentDatasetImportCommand) -> dict[str, Any]:
    """Build the stable business identity used to collect packages together."""
    identity = {
        "source_type": cmd.source_type,
        "source_system": cmd.source_system or "",
        "dataset_type": cmd.dataset_type,
        "dataset_date": cmd.dataset_date,
        "window_start": cmd.window_start,
        "window_end": cmd.window_end,
        "event_type": cmd.target.event_type,
        "model_id": cmd.target.model_id,
        "scene_type": cmd.target.scene_type,
        "device_type": cmd.target.device_type,
        "strategy_id": cmd.target.strategy_id,
        "labels": sorted(
            {str(label).strip() for label in cmd.labeling.labels if str(label).strip()}
        ),
        "media_type": cmd.labeling.media_type,
        "annotation_type": cmd.labeling.annotation_type,
    }
    category_key = str(cmd.dataset_category_key or "").strip().lower()
    if category_key:
        identity["dataset_category_key"] = category_key
    return identity


_INTERNAL_VALID_SOURCE_KEYS = "_labelu_valid_source_keys"
_INTERNAL_VALID_SOURCE_HASHES = "_labelu_valid_source_sha256"


def _sha256_s3_object(ds: DataSource, object_key: str) -> str:
    """Hash a source object without writing a second local copy.

    The aggregate threshold is evaluated before task creation, so the Worker
    must have a stable content identity at inspection time. Object keys alone
    are insufficient because the same image may be uploaded under different
    names by separate packages.
    """
    client = _build_s3_client(ds)
    response = client.get_object(Bucket=ds.bucket, Key=object_key)
    body = response.get("Body")
    if body is None:
        raise ValueError("S3 object response has no body")
    digest = hashlib.sha256()
    try:
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    finally:
        close = getattr(body, "close", None)
        if callable(close):
            close()
    return digest.hexdigest()


def _metadata_source_keys(
    cmd: CreateExperimentDatasetImportCommand, metadata: Any
) -> set[str]:
    """Return stable source identities for the samples accepted by inspection.

    The marker is stored inside the persisted metadata so a later worker run can
    recompute a group total without trusting the per-package count.  Older rows
    do not have the marker; for those rows we derive the object key from the
    immutable payload and metadata as a backwards-compatible fallback.
    """
    if not isinstance(metadata, dict):
        return set()
    marker = metadata.get(_INTERNAL_VALID_SOURCE_KEYS)
    if isinstance(marker, list):
        return {str(item).strip() for item in marker if str(item).strip()}
    keys: set[str] = set()
    image_prefix = str(cmd.dataset.image_prefix or "")
    for sample_row in _metadata_samples(metadata):
        sample_name = _sample_image_name(sample_row)
        if sample_name:
            keys.add(f"{cmd.dataset.bucket}|{image_prefix}{sample_name}")
    return keys


def _metadata_source_hashes(metadata: Any) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    values = metadata.get(_INTERNAL_VALID_SOURCE_HASHES)
    if not isinstance(values, list):
        return []
    return [str(item).strip().lower() for item in values if str(item).strip()]


def _aggregation_key(cmd: CreateExperimentDatasetImportCommand) -> tuple[str, dict[str, Any]]:
    supplied = str(cmd.aggregation_key or "").strip()
    identity = _aggregation_identity(cmd)
    if supplied:
        return supplied, identity
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"agg-v1:{hashlib.sha256(encoded).hexdigest()}", identity


def _parse_json_object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _model_resolution_from_group(group: ExperimentDatasetGroup | None) -> dict[str, Any]:
    if not group:
        return {}
    return _parse_json_object(group.model_resolution_json)


def _resolve_import_model(db: Session, model_id: str) -> dict[str, Any]:
    """Resolve ownership separately from the availability of local PT weights.

    A synchronized historical AICM ID is sufficient model identity for an
    annotation task.  It must never pretend to provide inference/training
    weights.  Preserve the existing local publication resolver as first choice.
    """
    inference = yolo_preannotate.resolve_published_model(db, model_id)
    if inference.get("ready"):
        return {**inference, "identity_ready": True, "preannotation_allowed": True}

    from labelu.internal.application.service import published_model_catalog

    try:
        catalog = published_model_catalog.resolve_model(db, model_id)
    except LabelUException as exc:
        if exc.status_code != 404:
            raise
        return {**inference, "identity_ready": False, "preannotation_allowed": False}
    try:
        labels = json.loads(catalog.labels_json or "[]")
    except (TypeError, ValueError):
        labels = []
    return {
        **inference,
        "identity_ready": True,
        "catalog_id": int(catalog.id),
        "published_model_id": catalog.aicm_model_set_id,
        "catalog_source_type": catalog.source_type,
        "model_name": catalog.name,
        "reason": None,
        "inference_reason": inference.get("reason"),
        "resolved_by": "published_model_catalog_identity",
        "labels": (
            [str(value).strip() for value in labels if str(value).strip()]
            if isinstance(labels, list)
            else []
        ),
        "preannotation_allowed": False,
        "preannotation_skip_reason": "published_model_local_weights_unavailable",
        "preannotation_skip_detail": "已匹配发布模型；本地没有可用训练权重，已跳过模型自动预标注，可人工标注。",
    }


def _resolve_import_model_labels(
    db: Session, cmd: CreateExperimentDatasetImportCommand, model: dict[str, Any]
) -> dict[str, Any]:
    """Reuse existing label-library records and an unambiguous dataset contract."""
    expected = {
        str(value).strip() for value in model.get("labels") or [] if str(value).strip()
    }
    requested = list(
        dict.fromkeys(
            str(value).strip() for value in cmd.labeling.labels if str(value).strip()
        )
    )
    if not expected or not requested or not set(requested).issubset(expected):
        return {
            "ready": False,
            "reason": "model_labels_do_not_match_requested_labels",
            "expected_labels": sorted(expected),
            "requested_labels": requested,
        }
    try:
        labels = system_label_service.resolve_external_labels(
            db, cmd.source_system, requested
        )
    except LabelUException as exc:
        if exc.status_code != 400:
            raise
        return {"ready": False, "reason": "requested_labels_not_in_label_library"}
    if any(str(label.annotation_kind or "") != "bbox" for label in labels):
        return {"ready": False, "reason": "requested_labels_do_not_support_rectangles"}
    label_ids = [int(label.id) for label in labels]
    requested_ids = set(label_ids)
    candidates = []
    for dataset in (
        db.query(Dataset)
        .filter(
            Dataset.deleted_at.is_(None),
            Dataset.status == "ACTIVE",
            Dataset.dataset_label_contract_id.isnot(None),
        )
        .all()
    ):
        if cmd.dataset_category_key and (
            dataset.dataset_category is None
            or str(dataset.dataset_category.category_key or "").lower()
            != cmd.dataset_category_key.lower()
        ):
            continue
        contract = dataset.label_contract
        if contract is None or contract.annotation_kind != "bbox":
            continue
        try:
            allowed_ids = {
                int(value) for value in json.loads(contract.label_ids_json or "[]")
            }
        except (TypeError, ValueError):
            continue
        if requested_ids.issubset(allowed_ids):
            candidates.append((dataset, allowed_ids))
    exact = [item for item in candidates if item[1] == requested_ids]
    matches = exact or candidates
    dataset = matches[0][0] if len(matches) == 1 else None
    return {
        "ready": True,
        "label_ids": label_ids,
        "labels": [str(label.label_value) for label in labels],
        "dataset_id": int(dataset.id) if dataset is not None else None,
        "dataset_label_contract_id": (
            int(dataset.dataset_label_contract_id) if dataset is not None else None
        ),
        "dataset_category_id": (
            int(dataset.dataset_category_id)
            if dataset is not None and dataset.dataset_category_id
            else None
        ),
    }


def _import_draft_contract_conflict(
    db: Session, task_id: int, label_contract: dict[str, Any]
) -> bool:
    """Never replace an operator's incompatible annotation configuration."""
    task = crud_task.get(db=db, task_id=task_id)
    if task is None:
        return False
    for field in ("dataset_id", "dataset_label_contract_id"):
        existing_id = getattr(task, field, None)
        requested_id = label_contract.get(field)
        if existing_id and requested_id and int(existing_id) != int(requested_id):
            return True
    contract = annotation_taxonomy_service.contract_service.current_contract(db, task_id)
    if contract is not None and contract.annotation_kind != "bbox":
        return True
    config = _parse_json_object(task.config)
    if any(
        item.get("tool") not in {"rectTool", "tagTool"}
        for item in config.get("tools", []) if isinstance(item, dict)
    ):
        return True
    existing = yolo_preannotate._task_contract_descriptor(db, task).get("ordered_labels") or []
    return bool(existing) and set(existing) != set(label_contract.get("labels") or [])


def _save_import_task_context(
    db: Session, task_id: int, row: ExperimentDatasetImport, model: dict[str, Any] | None
) -> None:
    task = crud_task.get(db=db, task_id=task_id)
    if task is None:
        return
    config = _parse_json_object(task.config)
    context = dict(config.get("third_party_context") or {})
    context.update(
        source_type=row.source_type,
        source_system=row.source_system,
        aggregation_group_id=row.aggregation_group_id,
        aggregation_key=row.aggregation_key,
        model_id=row.model_id,
        catalog_id=(model or {}).get("catalog_id"),
    )
    config["third_party_context"] = context
    with begin_transaction(db):
        crud_task.update(db=db, db_obj=task, obj_in={Task.config.key: json.dumps(config, ensure_ascii=False)})


def _dataset_category_by_id(
    db: Session,
    category_id: int | None,
) -> DatasetCategory | None:
    if not category_id:
        return None
    return (
        db.query(DatasetCategory)
        .filter(
            DatasetCategory.id == int(category_id),
            DatasetCategory.deleted_at.is_(None),
        )
        .first()
    )


def _published_model_dataset_category(
    db: Session,
    published_model_id: str,
) -> tuple[DatasetCategory | None, dict[str, Any]]:
    external_id = str(published_model_id or "").strip()
    if not external_id:
        return None, {"category_resolution": "model_id_missing"}

    from labelu.internal.application.service.strategy_evolution import (
        _resolve_export_id_from_model_id,
    )

    export_id, resolution = _resolve_export_id_from_model_id(db, external_id)
    details = dict(resolution) if isinstance(resolution, dict) else {}
    # Keep model readiness diagnostics authoritative at the top level.  The
    # lineage lookup may return a generic ``reason`` (for example, mapping
    # missing); preserve it under a category-specific key so it cannot mask
    # the resolver's published-model reason in the persisted group state.
    if "reason" in details:
        details["category_reason"] = details.pop("reason")
    details["published_model_id"] = external_id
    if details.get("resolved_by") == "direct_export_id" or export_id is None:
        details["category_resolution"] = "published_model_mapping_missing"
        return None, details

    export_row = crud_yolo_bm1688_export.get(db, int(export_id))
    if not export_row:
        details.update(
            {
                "export_id": int(export_id),
                "category_resolution": "export_not_found",
            }
        )
        return None, details

    source_train_job_id = int(export_row.yolo_train_job_id or 0)
    source_task_id = int(export_row.task_id or 0)
    job = (
        crud_yolo_train_job.get(db=db, job_id=source_train_job_id)
        if source_train_job_id
        else None
    )
    category_id = int(job.dataset_category_id or 0) if job else 0
    if not category_id and source_task_id:
        source_task = crud_task.get(db=db, task_id=source_task_id)
        category_id = int(source_task.dataset_category_id or 0) if source_task else 0
    category = _dataset_category_by_id(db, category_id)
    details.update(
        {
            "export_id": int(export_row.id),
            "source_train_job_id": source_train_job_id or None,
            "source_task_id": source_task_id or None,
            "dataset_category_id": int(category.id) if category else None,
            "dataset_category_key": category.category_key if category else None,
            "category_resolution": (
                "model_lineage" if category else "model_category_missing"
            ),
        }
    )
    return category, details


def _resolve_effective_dataset_category(
    db: Session,
    cmd: CreateExperimentDatasetImportCommand,
) -> tuple[DatasetCategory | None, str | None, dict[str, Any]]:
    explicit = system_label_service.resolve_dataset_category_key(
        db,
        cmd.dataset_category_key,
    )
    inherited, model_details = _published_model_dataset_category(
        db,
        cmd.target.model_id,
    )
    if explicit and inherited and int(explicit.id) != int(inherited.id):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=409,
            message=(
                "dataset_category_key 与发布模型源训练分类不一致: "
                f"explicit={explicit.category_key}, model={inherited.category_key}"
            ),
        )
    if explicit:
        return explicit, "explicit", model_details
    if inherited:
        return inherited, "model_lineage", model_details
    return None, None, model_details


def _category_response_fields(
    db: Session,
    row: ExperimentDatasetImport,
    group: ExperimentDatasetGroup | None = None,
    task: Task | None = None,
) -> dict[str, Any]:
    category_id = (
        int(task.dataset_category_id or 0)
        if task is not None
        else int((group.dataset_category_id if group is not None else 0) or 0)
    )
    if not category_id:
        category_id = int(row.dataset_category_id or 0)
    category = _dataset_category_by_id(db, category_id)
    return {
        "dataset_category_id": int(category.id) if category else None,
        "dataset_category_key": category.category_key if category else None,
        "dataset_category_name": category.name if category else None,
    }


def _group_for_row(
    db: Session, row: ExperimentDatasetImport, *, lock: bool = False
) -> ExperimentDatasetGroup | None:
    group_id = int(row.aggregation_group_id or 0)
    if group_id:
        group = crud_experiment_dataset_group.get(db, group_id=group_id, lock=lock)
        if group:
            return group
    aggregation_key = str(row.aggregation_key or "").strip()
    if not aggregation_key:
        return None
    return crud_experiment_dataset_group.get_by_key(
        db, aggregation_key=aggregation_key, lock=lock
    )


def _task_description(
    *, cmd: CreateExperimentDatasetImportCommand, source_kind: str
) -> str:
    return "\n".join(
        [
            f"source={source_kind}",
            f"package_id={cmd.package_id}",
            f"dataset_date={cmd.dataset_date}",
            f"event_type={cmd.target.event_type}",
            f"target_signature={_target_signature_from_prefix(cmd.dataset.prefix)}",
            f"s3_uri={cmd.dataset.s3_uri}",
            f"metadata_key={cmd.dataset.metadata_key}",
        ]
    )


def _admin_user(db: Session) -> User:
    admin = crud_user.get_user_by_username(db, username=DEFAULT_ADMIN_USERNAME)
    if not admin:
        raise RuntimeError(f"admin user not found: {DEFAULT_ADMIN_USERNAME}")
    return admin


def _import_owner(db: Session, cmd: CreateExperimentDatasetImportCommand) -> User:
    if cmd.source_type == "third_party":
        return ensure_open_system_user(db)
    return _admin_user(db)


def _status_message(status: str) -> str:
    return {
        IMPORT_STATUS_PENDING: "\u5df2\u53d7\u7406\uff0c\u7b49\u5f85\u540e\u53f0\u5bfc\u5165",
        IMPORT_STATUS_PROCESSING: "\u6b63\u5728\u540e\u53f0\u5bfc\u5165",
        IMPORT_STATUS_WAITING_FOR_SAMPLES: "\u5df2\u63a5\u6536\uff0c\u7b49\u5f85\u66f4\u591a\u56fe\u7247",
        IMPORT_STATUS_WAITING_FOR_MODEL: "\u5df2\u63a5\u6536\uff0c\u7b49\u5f85\u53d1\u5e03\u6a21\u578b\u5c31\u7eea",
        IMPORT_STATUS_WAITING_FOR_CATEGORY: "\u5df2\u63a5\u6536\uff0c\u7b49\u5f85\u786e\u5b9a\u6570\u636e\u96c6\u5206\u7c7b",
        IMPORT_STATUS_COMPLETED: "\u5bfc\u5165\u6210\u529f",
        IMPORT_STATUS_PARTIAL_COMPLETED: "\u5bfc\u5165\u90e8\u5206\u6210\u529f",
        IMPORT_STATUS_FAILED: "\u5bfc\u5165\u5931\u8d25",
    }.get(status, "\u5bfc\u5165\u72b6\u6001\u672a\u77e5")


def _status_flags(status: str) -> tuple[bool, bool | None]:
    completed = status in {
        IMPORT_STATUS_COMPLETED,
        IMPORT_STATUS_PARTIAL_COMPLETED,
        IMPORT_STATUS_FAILED,
    }
    success = True if status == IMPORT_STATUS_COMPLETED else False if completed else None
    return completed, success


def _status_snapshot(
    row: ExperimentDatasetImport,
    group: ExperimentDatasetGroup | None = None,
    db: Session | None = None,
) -> dict[str, Any]:
    status = str(row.status or "").strip()
    completed, success = _status_flags(status)
    formal_task_ids = _parse_task_ids(row.formal_task_ids_json)
    if not formal_task_ids and group is not None and group.formal_task_id:
        formal_task_ids = [int(group.formal_task_id)]
    declared = int(
        group.declared_sample_count
        if group is not None
        else row.declared_sample_count or 0
    )
    formal_samples = int(row.formal_sample_count or 0)
    draft_samples = int(row.draft_sample_count or 0)
    abnormal_samples = int(row.abnormal_sample_count or 0)
    valid_samples = int(
        group.valid_sample_count if group is not None else row.valid_sample_count or 0
    )
    required_samples = int(
        group.min_sample_count
        if group is not None
        else settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT
    )
    imported_samples = formal_samples + draft_samples
    stage_by_status = {
        IMPORT_STATUS_PENDING: "QUEUED",
        IMPORT_STATUS_PROCESSING: "IMPORTING",
        IMPORT_STATUS_WAITING_FOR_SAMPLES: "WAITING_FOR_SAMPLES",
        IMPORT_STATUS_WAITING_FOR_MODEL: "WAITING_FOR_MODEL",
        IMPORT_STATUS_WAITING_FOR_CATEGORY: "WAITING_FOR_CATEGORY",
        IMPORT_STATUS_COMPLETED: "TASKS_CREATED",
        IMPORT_STATUS_PARTIAL_COMPLETED: "PARTIAL_TASKS_CREATED",
        IMPORT_STATUS_FAILED: "IMPORT_FAILED",
    }
    label_by_status = {
        IMPORT_STATUS_WAITING_FOR_SAMPLES: "等待更多图片",
        IMPORT_STATUS_WAITING_FOR_MODEL: "等待模型就绪",
        IMPORT_STATUS_WAITING_FOR_CATEGORY: "等待数据集分类",
        IMPORT_STATUS_PENDING: "已受理",
        IMPORT_STATUS_PROCESSING: "处理中",
        IMPORT_STATUS_COMPLETED: "已完成",
        IMPORT_STATUS_PARTIAL_COMPLETED: "部分完成",
        IMPORT_STATUS_FAILED: "失败",
    }
    stage = stage_by_status.get(status, "UNKNOWN")
    status_label = label_by_status.get(status, "未知")
    model_resolution = _model_resolution_from_group(group)
    manual_model_confirmation = bool(
        model_resolution.get("manual_confirmation_required")
    )
    if status == IMPORT_STATUS_WAITING_FOR_MODEL and manual_model_confirmation:
        status_label = "待人工确认模型"
        stage = "WAITING_FOR_MODEL_CONFIRMATION"
    if status == IMPORT_STATUS_FAILED:
        detail = f"导入失败：{row.last_error or '未记录具体错误'}"
    elif status == IMPORT_STATUS_WAITING_FOR_SAMPLES:
        detail = f"已接收 {valid_samples} 张有效图片，达到 {required_samples} 张后创建任务"
    elif status == IMPORT_STATUS_WAITING_FOR_MODEL:
        if manual_model_confirmation:
            reason = str(model_resolution.get("reason") or "模型未匹配")
            detail = (
                f"已创建草稿任务，待人工确认模型（{reason}）；"
                "确认前不会自动预标注或进入正式训练"
            )
        else:
            detail = _status_message(status)
    elif status == IMPORT_STATUS_WAITING_FOR_CATEGORY:
        detail = row.last_error or _status_message(status)
    elif status == IMPORT_STATUS_PROCESSING:
        detail = f"正在导入，已登记 {imported_samples}/{declared} 个样本"
    elif status == IMPORT_STATUS_COMPLETED:
        detail = f"已创建 {len(formal_task_ids)} 个正式任务和 {1 if row.draft_task_id else 0} 个草稿任务"
    elif status == IMPORT_STATUS_PARTIAL_COMPLETED:
        detail = f"已导入 {imported_samples} 个样本，异常 {abnormal_samples} 个"
    else:
        detail = _status_message(status)
    auto_preannotation = _auto_preannotation_snapshot(db, row)
    if status == IMPORT_STATUS_COMPLETED and auto_preannotation.get("skipped_detail"):
        detail += "；" + str(auto_preannotation["skipped_detail"])
    return {
        "completed": completed,
        "success": success,
        "status_label": status_label,
        "stage": stage,
        "status_detail": detail,
        "progress": {
            "declared_samples": declared,
            "valid_samples": valid_samples,
            "required_samples": required_samples,
            "imported_samples": imported_samples,
            # 剩余数量表示距离“创建正式任务”的最低有效图片门槛还差多少。
            # 声明数量是所有第三方包的累计声明值，不能用它计算门槛剩余量；
            # 否则同一累计组多次请求后会出现“已达 50 张但还差 118 张”的错误。
            "remaining_samples": max(required_samples - valid_samples, 0),
            "formal_samples": formal_samples,
            "draft_samples": draft_samples,
            "abnormal_samples": abnormal_samples,
            "formal_task_count": len(formal_task_ids),
            "formal_task_ids": formal_task_ids,
            "draft_task_id": row.draft_task_id,
            "auto_preannotation": auto_preannotation,
        },
    }


def _auto_preannotation_snapshot(
    db: Session | None,
    row: ExperimentDatasetImport,
) -> dict[str, Any]:
    metadata = _parse_json_object(row.metadata_json)
    raw = metadata.get("_labelu_auto_preannotation")
    if not isinstance(raw, dict):
        raw = {}
    job_ids = [
        int(value)
        for value in raw.get("job_ids") or []
        if str(value).isdigit() and int(value) > 0
    ]
    snapshot = {
        "status": "NOT_QUEUED" if not job_ids else "PENDING",
        "job_ids": job_ids,
        "sample_count": int(raw.get("sample_count") or 0),
        "processed_count": 0,
        "success_count": 0,
        "failed_count": 0,
    }
    if raw.get("skipped_reason"):
        snapshot.update(
            status="SKIPPED" if not job_ids else snapshot["status"],
            skipped_reason=raw["skipped_reason"],
            skipped_detail=raw.get("skipped_detail"),
        )
    if db is None or not job_ids:
        return snapshot

    jobs = [crud_auto_label_job.get(db=db, job_id=job_id) for job_id in job_ids]
    jobs = [job for job in jobs if job is not None]
    if not jobs:
        snapshot["status"] = "MISSING"
        return snapshot
    statuses = {str(job.status or "") for job in jobs}
    if "PROCESSING" in statuses:
        aggregate_status = "PROCESSING"
    elif "PENDING" in statuses:
        aggregate_status = "PENDING"
    elif statuses == {"COMPLETED"}:
        aggregate_status = "COMPLETED"
    elif statuses == {"FAILED"}:
        aggregate_status = "FAILED"
    elif "FAILED" in statuses or "PARTIAL_COMPLETED" in statuses:
        aggregate_status = "PARTIAL_COMPLETED"
    elif statuses == {"CANCELLED"}:
        aggregate_status = "CANCELLED"
    else:
        aggregate_status = "PARTIAL_COMPLETED"
    snapshot.update(
        {
            "status": aggregate_status,
            "sample_count": sum(int(job.sample_count or 0) for job in jobs),
            "processed_count": sum(int(job.processed_count or 0) for job in jobs),
            "success_count": sum(int(job.success_count or 0) for job in jobs),
            "failed_count": sum(int(job.failed_count or 0) for job in jobs),
        }
    )
    return snapshot


def _accepted_response(
    db: Session,
    row: ExperimentDatasetImport,
    group: ExperimentDatasetGroup | None = None,
    *,
    message_override: str | None = None,
) -> ExperimentDatasetImportAcceptedResponse:
    source = _source_payload(row)
    status_message_map = {
        IMPORT_STATUS_WAITING_FOR_SAMPLES: "已接收，等待更多图片",
        IMPORT_STATUS_WAITING_FOR_MODEL: "已接收，等待发布模型就绪",
        IMPORT_STATUS_WAITING_FOR_CATEGORY: "已接收，等待确定数据集分类",
        IMPORT_STATUS_PENDING: "专家回流数据集导入任务已受理",
        IMPORT_STATUS_PROCESSING: "专家回流数据集正在导入",
        IMPORT_STATUS_COMPLETED: "专家回流数据集导入完成",
        IMPORT_STATUS_PARTIAL_COMPLETED: "专家回流数据集已部分导入完成",
        IMPORT_STATUS_FAILED: "专家回流数据集导入失败",
    }
    snapshot = _status_snapshot(row, group)
    message = status_message_map.get(
        str(row.status or "").strip(), "专家回流数据集导入任务已受理"
    )
    if message_override:
        message = str(message_override)
    if snapshot.get("status_label") == "待人工确认模型" and not message_override:
        message = "已创建草稿任务，待人工确认模型"
    return ExperimentDatasetImportAcceptedResponse(
        package_id=row.package_id,
        import_job_id=row.id,
        **snapshot,
        status=row.status,
        status_url=f"/internal/v1/experiment-datasets/{row.package_id}",
        last_error=row.last_error,
        **_retry_fields(row),
        message=message,
        dataset_type=row.dataset_type,
        dataset_date=row.dataset_date,
        window_start=str(source.get("window_start") or ""),
        window_end=str(source.get("window_end") or ""),
        target=_externalize_model_id_keys(
            source.get("target") if isinstance(source.get("target"), dict) else {}
        ),
        declared_sample_count=(
            group.declared_sample_count
            if group is not None
            else row.declared_sample_count or 0
        ),
        formal_task_ids=_status_snapshot(row, group)["progress"]["formal_task_ids"],
        draft_task_id=row.draft_task_id,
        formal_sample_count=row.formal_sample_count or 0,
        draft_sample_count=row.draft_sample_count or 0,
        abnormal_sample_count=row.abnormal_sample_count or 0,
        valid_sample_count=(
            group.valid_sample_count
            if group is not None
            else row.valid_sample_count or 0
        ),
        required_sample_count=(
            group.min_sample_count
            if group is not None
            else settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT
        ),
        aggregation_key=str(row.aggregation_key or ""),
        model_resolution=_model_resolution_from_group(group),
        **_category_response_fields(db, row, group),
    )


def schedule_import_job(import_job_id: int) -> None:
    # Production and compatibility ingress are fail-closed: the Web process
    # only enqueues.  A dedicated Worker must be enabled and consume the row.
    # Local tests and repair scripts call run_import_job_now explicitly.
    if not settings.EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED:
        raise RuntimeError("experiment dataset dedicated worker is disabled")


def wake_waiting_imports(
    db: Session,
    *,
    status: str | None = None,
    model_id: str | None = None,
    limit: int = 100,
    reason: str = "",
) -> int:
    """Immediately requeue imports whose external prerequisite changed.

    Model publication, category maintenance, and a repeated package callback
    use this hook.  It only changes waiting rows to ``PENDING``; the dedicated
    Worker still owns S3 downloads and task creation.  This is the immediate
    path for ``WAITING_FOR_MODEL`` and ``WAITING_FOR_CATEGORY``; the periodic
    Worker only retries ``WAITING_FOR_SAMPLES`` after its cooldown.
    """
    requeued = crud_experiment_dataset_import.requeue_waiting(
        db,
        limit=limit,
        status=status,
        model_id=model_id,
        min_age_seconds=0,
    )
    if requeued:
        logger.info(
            "experiment dataset imports woken count={} reason={} job_ids={}",
            len(requeued),
            reason or "external_prerequisite_changed",
            requeued[:20],
        )
    return len(requeued)


def _find_reusable_datasource(
    db: Session, *, bucket: str, current_user: User
) -> DataSource | None:
    endpoint = get_active_endpoint()
    return (
        db.query(DataSource)
        .filter(
            DataSource.deleted_at.is_(None),
            DataSource.created_by == current_user.id,
            DataSource.type == "S3",
            DataSource.endpoint == endpoint,
            DataSource.bucket == bucket,
            DataSource.prefix == "",
            DataSource.path_style == settings.STRATEGY_INTEGRATION_S3_PATH_STYLE,
            DataSource.use_ssl == settings.STRATEGY_INTEGRATION_S3_USE_SSL,
        )
        .order_by(DataSource.id.asc())
        .first()
    )


def _get_or_create_datasource(
    db: Session, *, bucket: str, current_user: User
) -> DataSource:
    reusable = _find_reusable_datasource(db, bucket=bucket, current_user=current_user)
    if reusable:
        return reusable

    endpoint = get_active_endpoint()
    row = DataSource(
        name=f"experiment-minio:{bucket}",
        type="S3",
        endpoint=endpoint,
        region=None,
        bucket=bucket,
        prefix="",
        access_key_id=encrypt_value(settings.STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID),
        secret_access_key=encrypt_value(
            settings.STRATEGY_INTEGRATION_S3_SECRET_ACCESS_KEY
        ),
        path_style=settings.STRATEGY_INTEGRATION_S3_PATH_STYLE,
        use_ssl=settings.STRATEGY_INTEGRATION_S3_USE_SSL,
        presign_expire_secs=settings.S3_PRESIGN_EXPIRE_SECONDS,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    with begin_transaction(db):
        row = crud_datasource.create(db=db, data_source=row)
    return row


def _read_metadata_json(ds: DataSource, metadata_key: str) -> Any:
    client = _build_s3_client(ds)
    response = client.get_object(Bucket=ds.bucket, Key=metadata_key)
    return json.loads(response["Body"].read().decode("utf-8"))


def _list_image_object_keys(ds: DataSource, prefix: str) -> set[str]:
    client = _build_s3_client(ds)
    continuation_token: str | None = None
    keys: set[str] = set()

    while True:
        kwargs: dict[str, Any] = {
            "Bucket": ds.bucket,
            "Prefix": prefix,
            "MaxKeys": 1000,
        }
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        response = client.list_objects_v2(**kwargs)
        for obj in response.get("Contents", []):
            key = str(obj.get("Key") or "")
            if not key or key.endswith("/"):
                continue
            suffix = "." + key.rsplit(".", 1)[-1].lower() if "." in key else ""
            if suffix not in IMAGE_EXTENSIONS:
                continue
            keys.add(key)
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")
    return keys


def _sanitize_attachment_filename(filename: str) -> str:
    sanitized = re.sub(r"%", "_pct_", str(filename or ""))
    sanitized = re.sub(r'[\\/*?:"<>|#]', "_", sanitized)
    return sanitized or f"image-{uuid.uuid4().hex[:8]}.jpg"


def _local_attachment_relative_path(*, task_id: int, filename: str) -> str:
    return _local_attachment_relative_path_with_suffix(
        task_id=task_id,
        filename=filename,
        source_suffix="",
    )


def _local_attachment_relative_path_with_suffix(
    *, task_id: int, filename: str, source_suffix: str = ""
) -> str:
    sanitized = _sanitize_attachment_filename(filename)
    suffix = str(source_suffix or "").strip()
    if suffix:
        stem, dot, extension = sanitized.rpartition(".")
        sanitized = (
            f"{stem}--{suffix}.{extension}"
            if dot
            else f"{sanitized}--{suffix}"
        )
    return str(Path(settings.UPLOAD_DIR).joinpath(str(task_id), sanitized)).replace(
        "\\", "/"
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _head_s3_image(*, ds: DataSource, object_key: str) -> dict[str, Any]:
    # Small unit-test doubles may only expose bucket; the real DataSource
    # always has credentials and therefore takes the strict HEAD path.
    if not hasattr(ds, "access_key_id"):
        return {
            "bucket": str(getattr(ds, "bucket", "") or ""),
            "object_key": str(object_key),
            "version_id": "",
            "etag": "",
            "size_bytes": 0,
            "content_type": None,
        }
    client = _build_s3_client(ds)
    head = client.head_object(Bucket=ds.bucket, Key=object_key)
    expected_size = int(head.get("ContentLength") or 0)
    if expected_size <= 0:
        raise RuntimeError(f"S3 object is empty: {object_key}")
    etag = str(head.get("ETag") or "").strip().strip('"')
    version_id = str(head.get("VersionId") or "").strip()
    return {
        "bucket": str(ds.bucket or ""),
        "object_key": str(object_key),
        "version_id": version_id,
        "etag": etag,
        "size_bytes": expected_size,
        "content_type": str(head.get("ContentType") or "").strip() or None,
    }


def _source_identity_suffix(identity: dict[str, Any]) -> str:
    encoded = json.dumps(
        {
            "bucket": identity.get("bucket"),
            "object_key": identity.get("object_key"),
            "version_id": identity.get("version_id"),
            "etag": identity.get("etag"),
            "size_bytes": identity.get("size_bytes"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _download_s3_image_with_resume(
    *,
    ds: DataSource,
    object_key: str,
    relative_path: str,
    head: dict[str, Any] | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    client = _build_s3_client(ds)
    source = head or _head_s3_image(ds=ds, object_key=object_key)
    expected_size = int(source.get("size_bytes") or 0)

    target_path = Path(settings.MEDIA_ROOT).joinpath(relative_path.lstrip("/\\"))
    part_path = target_path.with_name(f"{target_path.name}.part")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.is_file() and target_path.stat().st_size == expected_size:
        if expected_sha256 and _sha256_file(target_path) == str(expected_sha256):
            return {**source, "sha256": str(expected_sha256)}
        if expected_sha256:
            target_path.unlink()
        else:
            # Without a previously stored digest, same-size reuse is unsafe:
            # the S3 object may have changed in place.
            target_path.unlink()
    if target_path.exists():
        target_path.unlink()
    if part_path.exists() and part_path.stat().st_size > expected_size:
        part_path.unlink()

    downloaded = part_path.stat().st_size if part_path.exists() else 0
    while downloaded < expected_size:
        chunk_size = min(S3_RANGE_REQUEST_CHUNK_SIZE, expected_size - downloaded)
        chunk_end = downloaded + chunk_size - 1
        response = client.get_object(
            Bucket=ds.bucket,
            Key=object_key,
            Range=f"bytes={downloaded}-{chunk_end}",
        )
        body = response["Body"]
        chunk = body.read(chunk_size)
        if not chunk:
            break
        if len(chunk) > chunk_size:
            chunk = chunk[:chunk_size]
        mode = "ab" if downloaded > 0 else "wb"
        with part_path.open(mode) as out:
            out.write(chunk)
        downloaded += len(chunk)

        actual_size = part_path.stat().st_size if part_path.exists() else 0
        if actual_size != downloaded:
            downloaded = actual_size
        if actual_size >= expected_size:
            break

    if not part_path.is_file() or part_path.stat().st_size != expected_size:
        raise RuntimeError(
            f"S3 image download incomplete: key={object_key} "
            f"downloaded={part_path.stat().st_size if part_path.exists() else 0} "
            f"expected={expected_size}"
        )
    os.replace(part_path, target_path)
    return {**source, "sha256": _sha256_file(target_path)}


def _metadata_samples(metadata: Any) -> list[dict[str, Any]]:
    if isinstance(metadata, list):
        return [row for row in metadata if isinstance(row, dict)]
    if not isinstance(metadata, dict):
        return []
    samples = metadata.get("samples")
    if not isinstance(samples, list):
        samples = metadata.get("images")
    if isinstance(samples, list):
        return [row for row in samples if isinstance(row, dict)]
    if any(key in metadata for key in ("image", "image_name", "file_name", "filename", "target_boxes")):
        return [metadata]
    return []


def _sample_image_name(sample_row: dict[str, Any]) -> str:
    image = sample_row.get("image")
    if isinstance(image, dict):
        file_name = str(
            image.get("file_name") or image.get("filename") or image.get("path") or ""
        ).strip()
        if file_name:
            return file_name.replace("\\", "/").split("/")[-1]
    elif isinstance(image, str) and image.strip():
        return image.replace("\\", "/").split("/")[-1].strip()
    for key in ("image_name", "file_name", "filename", "image_path", "image_key"):
        value = str(sample_row.get(key) or "").strip()
        if value:
            return value.replace("\\", "/").split("/")[-1]
    return ""


def _sample_dimensions(sample_row: dict[str, Any]) -> tuple[float | None, float | None]:
    image = sample_row.get("image")
    if isinstance(image, dict):
        return _box_number(image.get("width")), _box_number(image.get("height"))
    return _box_number(sample_row.get("width")), _box_number(sample_row.get("height"))


def _sample_boxes(sample_row: dict[str, Any]) -> list[dict[str, Any]]:
    boxes = sample_row.get("target_boxes")
    if not isinstance(boxes, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in boxes:
        if not isinstance(item, dict):
            continue
        box = dict(item)
        raw_label = str(
            box.get("class_name") or box.get("label") or box.get("class") or ""
        ).strip()
        if raw_label and raw_label[0].isupper():
            raw_label = raw_label[0].lower() + raw_label[1:]
        box["class_name"] = raw_label
        if any(box.get(key) is None for key in ("x1", "y1", "x2", "y2")):
            x = _box_number(box.get("x"))
            y = _box_number(box.get("y"))
            width = _box_number(box.get("width"))
            height = _box_number(box.get("height"))
            if None not in (x, y, width, height):
                box["x1"] = x
                box["y1"] = y
                box["x2"] = x + width
                box["y2"] = y + height
        normalized.append(box)
    return normalized


def _sync_legacy_strategy_projection(
    db: Session,
    *,
    row: ExperimentDatasetImport,
    status: str,
    formal_task_ids: list[int],
    draft_task_id: int | None,
    formal_sample_count: int,
    draft_sample_count: int,
    metadata: Any | None,
) -> None:
    """Reflect Worker progress into the retired strategy table only.

    The compatibility row is never used to discover or create samples.  It is
    updated after task IDs exist so legacy strategy pages and callbacks can
    still resolve the first task while the new import row remains the source of
    truth for the complete task list.
    """
    if row.source_integration_id is None:
        return
    from labelu.internal.domain.models.strategy_task_integration import (
        StrategyTaskIntegration,
    )

    integration = (
        db.query(StrategyTaskIntegration)
        .filter(
            StrategyTaskIntegration.id == int(row.source_integration_id),
            StrategyTaskIntegration.deleted_at.is_(None),
        )
        .first()
    )
    if integration is None:
        return
    task_ids = [int(value) for value in formal_task_ids]
    if draft_task_id is not None:
        task_ids.append(int(draft_task_id))
    first_task_id = task_ids[0] if task_ids else None
    with begin_transaction(db):
        integration.labelu_task_id = first_task_id
        integration.import_status = str(status)
        integration.imported_sample_count = int(formal_sample_count or 0) + int(
            draft_sample_count or 0
        )
        integration.metadata_json = (
            json.dumps(metadata, ensure_ascii=False) if metadata is not None else None
        )
        integration.updated_by = row.updated_by
        for task_id in task_ids:
            task = crud_task.get(db=db, task_id=task_id)
            if task is not None and task.source_integration_id is None:
                task.source_integration_id = int(integration.id)
                task.updated_by = row.updated_by
        db.flush()


def _box_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_display_side(value: float, scale: float) -> int:
    return max(1, int(value * scale + 0.5))


def _annotation_display_dimensions(
    width: float | None,
    height: float | None,
) -> tuple[float | None, float | None]:
    if not width or not height:
        return width, height

    max_side = max(width, height)
    if max_side <= ANNOTATION_DISPLAY_MAX_SIDE:
        return width, height

    scale = ANNOTATION_DISPLAY_MAX_SIDE / max_side
    return (
        float(_round_display_side(width, scale)),
        float(_round_display_side(height, scale)),
    )


def _annotation_display_scale(
    width: float | None,
    height: float | None,
) -> tuple[float, float]:
    display_width, display_height = _annotation_display_dimensions(width, height)
    if not width or not height or not display_width or not display_height:
        return 1.0, 1.0
    return display_width / width, display_height / height


def _looks_like_percent_box(
    box: dict[str, Any],
    *,
    sample_width: float | None,
    sample_height: float | None,
    dataset_type: str,
) -> bool:
    if dataset_type != REVIEWED_FALSE_POSITIVE_DAILY_DATASET_TYPE:
        return False
    if not sample_width or not sample_height:
        return False

    values = tuple(_box_number(box.get(key)) for key in ("x1", "y1", "x2", "y2"))
    if any(value is None for value in values):
        return False

    x1, y1, x2, y2 = values
    return (
        x1 is not None
        and y1 is not None
        and x2 is not None
        and y2 is not None
        and min(values) >= 0
        and x2 <= 100
        and y2 <= 100
    )


def _parse_task_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out: list[int] = []
    for item in data:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _source_payload(row: ExperimentDatasetImport) -> dict[str, Any]:
    try:
        parsed = json.loads(row.source_payload_json or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _canonical_import_payload(value: Any) -> str:
    """Normalize a persisted/request payload for idempotency comparison.

    Retry metadata is server-owned bookkeeping and is intentionally excluded
    from the client payload identity.  This lets two concurrent retry calls
    converge on the same deterministic package id without treating their
    different operator reason strings as a payload conflict.
    """
    if isinstance(value, str):
        value = json.loads(value or "{}")
    if not isinstance(value, dict):
        raise ValueError("import payload must be an object")
    payload = dict(value)
    payload.pop(_RETRY_MARKER_KEY, None)
    return json.dumps(
        CreateExperimentDatasetImportCommand.model_validate(payload).model_dump(
            mode="json"
        ),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _retry_marker_from_payload(value: Any) -> dict[str, Any] | None:
    """Read a retry marker from a raw request/persistence payload."""
    try:
        payload = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    marker = payload.get(_RETRY_MARKER_KEY) if isinstance(payload, dict) else None
    return marker if isinstance(marker, dict) else None


def _retry_attempt_is_active(
    row: ExperimentDatasetImport, *, now: datetime | None = None
) -> bool:
    """Return whether a retry row still owns an in-flight execution slot.

    ``PROCESSING`` is only active while the dedicated Worker lease is valid.
    A crashed Worker can leave that status behind; treating it as permanently
    active would make the administrator retry action unusable forever.
    """
    status = str(row.status or "").strip()
    if status not in _RETRY_ACTIVE_STATUSES:
        return False
    if status != IMPORT_STATUS_PROCESSING:
        return True
    current = now or datetime.now()
    return bool(
        str(row.worker_lease_owner or "").strip()
        and row.worker_lease_expires_at is not None
        and row.worker_lease_expires_at > current
    )


def _retry_marker(row: ExperimentDatasetImport) -> dict[str, Any] | None:
    """Read the server-owned retry relation from a persisted source payload."""
    marker = _source_payload(row).get(_RETRY_MARKER_KEY)
    if not isinstance(marker, dict):
        return None
    try:
        source_id = int(marker.get("source_import_job_id"))
        attempt = int(marker.get("attempt") or 0)
    except (TypeError, ValueError):
        return None
    if source_id <= 0 or attempt <= 0:
        return None
    return {
        "source_import_job_id": source_id,
        "attempt": attempt,
        "reason": str(marker.get("reason") or "").strip()[:500] or None,
    }


def _retry_fields(row: ExperimentDatasetImport) -> dict[str, Any]:
    marker = _retry_marker(row)
    if not marker:
        return {
            "retry_of_import_job_id": None,
            "retry_attempt": 0,
            "retry_reason": None,
        }
    return {
        "retry_of_import_job_id": marker["source_import_job_id"],
        "retry_attempt": marker["attempt"],
        "retry_reason": marker.get("reason"),
    }


def _retry_candidates(
    db: Session, source_row: ExperimentDatasetImport, *, lock: bool = False
) -> list[ExperimentDatasetImport]:
    """Return non-archived attempts directly derived from ``source_row``.

    The table predates an explicit retry foreign key.  Filtering the small
    aggregate history in Python keeps this compatible with SQLite/MySQL and
    avoids dialect-specific JSON operators while the marker remains auditable
    in the original request snapshot.
    """
    group = _group_for_row(db, source_row, lock=lock)
    if group is not None:
        candidates = crud_experiment_dataset_import.list_by_aggregation_key(
            db, aggregation_key=str(group.aggregation_key or ""), lock=lock
        )
    else:
        # Legacy failed rows may predate aggregation groups.  Scan the bounded
        # historical table and use the explicit retry marker as the relation.
        # (The marker filter below prevents unrelated rows from matching.)
        candidates = crud_experiment_dataset_import.list_by(
            db, status=None, model_id=None, lock=lock
        )
    return sorted(
        [
            row
            for row in candidates
            if int(row.id) != int(source_row.id)
            and _retry_marker(row)
            and int(_retry_marker(row)["source_import_job_id"]) == int(source_row.id)
        ],
        key=lambda item: (int(_retry_marker(item)["attempt"]), int(item.id)),
    )


def _retry_package_id(source_package_id: str, source_id: int, attempt: int) -> str:
    """Build a bounded, deterministic package id for one retry generation."""
    digest = hashlib.sha256(
        f"{source_id}:{source_package_id}".encode("utf-8")
    ).hexdigest()[:12]
    suffix = f"-retry-{int(attempt)}-{digest}"
    # Keep the human-readable source prefix while always honoring the command's
    # 128-character package-id contract.
    return f"{str(source_package_id)[: max(1, 128 - len(suffix))]}{suffix}"


def _retry_source_payload(
    row: ExperimentDatasetImport,
    *,
    package_id: str,
    source_import_job_id: int,
    attempt: int,
    reason: str,
) -> tuple[CreateExperimentDatasetImportCommand, str]:
    """Validate and clone a failed request while adding server retry metadata."""
    try:
        raw = json.loads(row.source_payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=409,
            message="原失败任务的请求内容已损坏，无法重试，请重新提交",
        ) from exc
    if not isinstance(raw, dict):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=409,
            message="原失败任务的请求内容已损坏，无法重试，请重新提交",
        )
    payload = dict(raw)
    payload["package_id"] = package_id
    payload.pop(_RETRY_MARKER_KEY, None)
    try:
        cmd = CreateExperimentDatasetImportCommand.model_validate(payload)
    except (ValidationError, TypeError, ValueError) as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=409,
            message="原失败任务的请求内容已损坏，无法重试，请重新提交",
        ) from exc
    canonical = cmd.model_dump(mode="json")
    canonical[_RETRY_MARKER_KEY] = {
        "source_import_job_id": int(source_import_job_id),
        "attempt": int(attempt),
        "reason": str(reason or "").strip()[:500],
    }
    return cmd, json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _task_default_rect_preset_slug(task: Task | None) -> str:
    if not task or not task.config:
        return ""
    try:
        cfg = json.loads(task.config)
    except json.JSONDecodeError:
        return ""
    if not isinstance(cfg, dict):
        return ""
    return str(cfg.get("defaultRectPresetId") or "").strip()


def _load_preset_index(db: Session) -> list[tuple[Any, set[str]]]:
    presets, _ = crud_annotation_taxonomy.label_preset_list(db, page=0, size=500)
    index: list[tuple[Any, set[str]]] = []
    for preset in presets:
        values = {
            str(item.label_value or "").strip()
            for item in preset.items or []
            if str(item.label_value or "").strip()
        }
        index.append((preset, values))
    return index


def _resolve_formal_preset(
    preset_index: list[tuple[Any, set[str]]],
    class_names: list[str],
    event_type: str = "",
) -> Any | None:
    if not class_names:
        return None
    class_name_set = {name for name in class_names if name}
    if not class_name_set:
        return None

    mapped_slug = EVENT_TYPE_PRESET_SLUGS.get(str(event_type or "").strip())
    if mapped_slug:
        mapped_preset = next(
            (preset for preset, _ in preset_index if preset.slug == mapped_slug),
            None,
        )
        if mapped_preset is None:
            return None
        mapped_values = {
            str(item.label_value or "").strip()
            for item in mapped_preset.items or []
            if str(item.label_value or "").strip()
        }
        return mapped_preset if class_name_set.issubset(mapped_values) else None

    matches = [
        preset for preset, values in preset_index if class_name_set.issubset(values)
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _formal_task_name(event_type: str, preset_name: str, dataset_date: str) -> str:
    return f"专家回流-{event_type}-{preset_name}-dt={dataset_date}"


def _truncate_task_name_with_suffix(base: str, suffix: str) -> str:
    full_name = f"{base}-{suffix}" if suffix else base
    if len(full_name) <= TASK_NAME_MAX_LENGTH:
        return full_name

    normalized_suffix = str(suffix or "").strip("-")
    if not normalized_suffix:
        return full_name[:TASK_NAME_MAX_LENGTH]

    reserved = len(normalized_suffix) + 1
    if reserved >= TASK_NAME_MAX_LENGTH:
        return normalized_suffix[-TASK_NAME_MAX_LENGTH:]

    return f"{base[: TASK_NAME_MAX_LENGTH - reserved]}-{normalized_suffix}"


def _draft_task_name(event_type: str, dataset_date: str, package_id: str) -> str:
    full_name = f"专家回流草稿-{event_type}-dt={dataset_date}-{package_id}"
    if len(full_name) <= TASK_NAME_MAX_LENGTH:
        return full_name

    package_suffix = str(package_id or "").strip()[-TASK_NAME_PACKAGE_SUFFIX_LENGTH:]
    compact_base = f"专家回流草稿-{event_type}-{dataset_date}"
    return _truncate_task_name_with_suffix(compact_base, package_suffix)


def _existing_formal_tasks_by_slug(
    db: Session, row: ExperimentDatasetImport
) -> dict[str, int]:
    tasks_by_slug: dict[str, int] = {}
    for task_id in _parse_task_ids(row.formal_task_ids_json):
        task = crud_task.get(db=db, task_id=task_id)
        slug = _task_default_rect_preset_slug(task)
        if task and slug:
            tasks_by_slug[slug] = int(task.id)
    return tasks_by_slug


def _ensure_task_dataset_category(
    db: Session,
    task: Task,
    dataset_category_id: int | None,
) -> None:
    if dataset_category_id is None:
        return
    current_id = int(task.dataset_category_id or 0)
    if current_id and current_id != int(dataset_category_id):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=409,
            message=f"任务 {task.id} 已绑定到不同的数据集分类",
        )
    if not current_id:
        crud_task.update(
            db=db,
            db_obj=task,
            obj_in={Task.dataset_category_id.key: int(dataset_category_id)},
        )


def _get_or_create_formal_task(
    db: Session,
    *,
    existing_by_slug: dict[str, int],
    preset,
    cmd: CreateExperimentDatasetImportCommand,
    current_user: User,
    dataset_category_id: int,
) -> int:
    existing_task_id = existing_by_slug.get(preset.slug)
    if existing_task_id:
        existing = crud_task.get(db=db, task_id=existing_task_id)
        if existing:
            _ensure_task_dataset_category(db, existing, dataset_category_id)
        return int(existing_task_id)

    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name=_formal_task_name(
                    cmd.target.event_type, preset.name, cmd.dataset_date
                ),
                description=_task_description(
                    cmd=cmd, source_kind="experiment_dataset_formal"
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.DRAFT.value,
                source_type=cmd.source_type,
                source_system=cmd.source_system or None,
                source_external_task_id=cmd.source_external_task_id or None,
                source_external_package_id=cmd.package_id,
                dataset_category_id=dataset_category_id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
    asyncio.run(
        annotation_taxonomy_service.apply_label_preset_to_task(
            db=db,
            task_id=task.id,
            preset_id=preset.id,
            current_user=current_user,
        )
    )
    existing_by_slug[preset.slug] = int(task.id)
    return int(task.id)


def _get_or_create_draft_task(
    db: Session,
    *,
    existing_task_id: int | None,
    cmd: CreateExperimentDatasetImportCommand,
    current_user: User,
    dataset_category_id: int,
) -> int:
    if existing_task_id:
        existing = crud_task.get(db=db, task_id=existing_task_id)
        if existing:
            _ensure_task_dataset_category(db, existing, dataset_category_id)
            return int(existing.id)
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name=_draft_task_name(
                    cmd.target.event_type, cmd.dataset_date, cmd.package_id
                ),
                description=_task_description(
                    cmd=cmd, source_kind="experiment_dataset_draft"
                ),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.DRAFT.value,
                source_type=cmd.source_type,
                source_system=cmd.source_system or None,
                source_external_task_id=cmd.source_external_task_id or None,
                source_external_package_id=cmd.package_id,
                dataset_category_id=dataset_category_id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
    return int(task.id)


def _get_or_create_v2_contract_task(
    db: Session,
    *,
    existing_task_ids: list[int],
    cmd: CreateExperimentDatasetImportCommand,
    current_user: User,
    label_ids: list[int],
    dataset_category_id: int | None,
    dataset_id: int | None = None,
    dataset_label_contract_id: int | None = None,
    configure_existing: bool = False,
) -> int:
    for task_id in existing_task_ids:
        task = crud_task.get(db=db, task_id=task_id)
        if task:
            if dataset_category_id is not None:
                _ensure_task_dataset_category(db, task, dataset_category_id)
            if configure_existing and not yolo_preannotate._task_contract_descriptor(db, task).get("ordered_labels"):
                with begin_transaction(db):
                    if not task.dataset_id:
                        task.dataset_id = dataset_id
                    if not task.dataset_label_contract_id:
                        task.dataset_label_contract_id = dataset_label_contract_id
                    if task.name == _draft_task_name(cmd.target.event_type, cmd.dataset_date, cmd.package_id):
                        task.name = _truncate_task_name_with_suffix(
                            f"专家回流-{cmd.target.event_type}-{cmd.dataset_date}", cmd.package_id[-8:]
                        )
                    db.flush()
                asyncio.run(annotation_taxonomy_service.apply_labels_to_task(db, int(task.id), label_ids, current_user))
            return int(task.id)
    with begin_transaction(db):
        task = crud_task.create(
            db=db,
            task=Task(
                name=_truncate_task_name_with_suffix(
                    f"专家回流-{cmd.target.event_type}-{cmd.dataset_date}",
                    cmd.package_id[-8:],
                ),
                description=_task_description(cmd=cmd, source_kind="experiment_dataset_v2"),
                media_type=MediaType.IMAGE.value,
                status=TaskStatus.DRAFT.value,
                source_type=cmd.source_type,
                source_system=cmd.source_system or None,
                source_external_task_id=cmd.source_external_task_id or None,
                source_external_package_id=cmd.package_id,
                dataset_category_id=dataset_category_id,
                dataset_id=dataset_id,
                dataset_label_contract_id=dataset_label_contract_id,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
    asyncio.run(
        annotation_taxonomy_service.apply_labels_to_task(
            db, int(task.id), label_ids, current_user
        )
    )
    return int(task.id)


def _find_source_attachment(
    db: Session,
    *,
    task_id: int,
    source_bucket: str | None,
    source_object_key: str | None,
    source_version_id: str | None,
    source_etag: str | None,
) -> TaskAttachment | None:
    if not source_bucket or not source_object_key:
        return None
    return (
        db.query(TaskAttachment)
        .filter(
            TaskAttachment.task_id == int(task_id),
            TaskAttachment.source_bucket == source_bucket,
            TaskAttachment.source_object_key == source_object_key,
            TaskAttachment.source_version_id == source_version_id,
            TaskAttachment.source_etag == source_etag,
            TaskAttachment.deleted_at.is_(None),
        )
        .order_by(TaskAttachment.id.asc())
        .first()
    )


def _source_aware_attachment_path(
    db: Session,
    *,
    task_id: int,
    filename: str,
    identity: dict[str, Any],
) -> tuple[str, TaskAttachment | None]:
    existing_source = _find_source_attachment(
        db,
        task_id=task_id,
        source_bucket=str(identity.get("bucket") or "").strip() or None,
        source_object_key=str(identity.get("object_key") or "").strip() or None,
        source_version_id=str(identity.get("version_id") or "").strip() or None,
        source_etag=str(identity.get("etag") or "").strip() or None,
    )
    if existing_source:
        return str(existing_source.path), existing_source

    base_path = _local_attachment_relative_path(task_id=task_id, filename=filename)
    existing_path = (
        db.query(TaskAttachment)
        .filter(
            TaskAttachment.task_id == int(task_id),
            TaskAttachment.path == base_path,
            TaskAttachment.deleted_at.is_(None),
        )
        .order_by(TaskAttachment.id.asc())
        .first()
    )
    if existing_path is None:
        return base_path, None
    return (
        _local_attachment_relative_path_with_suffix(
            task_id=task_id,
            filename=filename,
            source_suffix=_source_identity_suffix(identity),
        ),
        None,
    )


def _get_or_create_attachment(
    db: Session,
    *,
    task_id: int,
    datasource_id: int,
    object_key: str,
    local_relative_path: str,
    filename: str,
    current_user: User,
    source_bucket: str | None = None,
    source_object_key: str | None = None,
    source_version_id: str | None = None,
    source_etag: str | None = None,
    source_sha256: str | None = None,
    source_size_bytes: int | None = None,
    source_content_type: str | None = None,
) -> TaskAttachment:
    source_bucket = str(source_bucket or "").strip() or None
    source_object_key = str(source_object_key or "").strip() or None
    source_version_id = str(source_version_id or "").strip() or None
    source_etag = str(source_etag or "").strip() or None
    source_sha256 = str(source_sha256 or "").strip() or None
    if source_bucket and source_object_key and (source_etag or source_version_id):
        source_query = (
            db.query(TaskAttachment)
            .filter(
                TaskAttachment.task_id == task_id,
                TaskAttachment.source_bucket == source_bucket,
                TaskAttachment.source_object_key == source_object_key,
                TaskAttachment.source_version_id == source_version_id,
                TaskAttachment.source_etag == source_etag,
                TaskAttachment.deleted_at.is_(None),
            )
            .order_by(TaskAttachment.id.asc())
            .first()
        )
        if source_query:
            if source_sha256 and source_query.source_sha256 != source_sha256:
                # The source identity (ETag/version) still matches.  A digest
                # mismatch therefore indicates a stale or corrupted local
                # cache; refresh the content evidence instead of creating a
                # second attachment for the same source object.
                source_query.source_sha256 = source_sha256
                source_query.sha256 = source_sha256
                source_query.size_bytes = source_size_bytes
                source_query.content_type = source_content_type
                db.flush()
            return source_query

    existing = (
        db.query(TaskAttachment)
        .filter(
            TaskAttachment.task_id == task_id,
            TaskAttachment.data_source_id.is_(None),
            TaskAttachment.path == local_relative_path,
            TaskAttachment.deleted_at.is_(None),
        )
        .first()
    )
    if existing and (
        not source_bucket
        or not source_object_key
        or (
            existing.source_bucket == source_bucket
            and existing.source_object_key == source_object_key
            and existing.source_version_id == source_version_id
            and existing.source_etag == source_etag
        )
    ):
        return existing

    legacy = (
        db.query(TaskAttachment)
        .filter(
            TaskAttachment.task_id == task_id,
            TaskAttachment.data_source_id == datasource_id,
            TaskAttachment.path == object_key,
            TaskAttachment.deleted_at.is_(None),
        )
        .first()
    )
    if legacy:
        with begin_transaction(db):
            legacy.path = local_relative_path
            legacy.url = build_attachment_api_path(local_relative_path)
            legacy.filename = filename
            legacy.data_source_id = None
            legacy.source_bucket = source_bucket
            legacy.source_object_key = source_object_key
            legacy.source_version_id = source_version_id
            legacy.source_etag = source_etag
            legacy.source_sha256 = source_sha256
            legacy.sha256 = source_sha256
            legacy.size_bytes = source_size_bytes
            legacy.content_type = source_content_type
            legacy.updated_by = current_user.id
            db.add(legacy)
            db.flush()
            db.refresh(legacy)
        return legacy

    row = TaskAttachment(
        path=local_relative_path,
        url=build_attachment_api_path(local_relative_path),
        filename=filename,
        task_id=task_id,
        data_source_id=None,
        source_bucket=source_bucket,
        source_object_key=source_object_key,
        source_version_id=source_version_id,
        source_etag=source_etag,
        source_sha256=source_sha256,
        sha256=source_sha256,
        size_bytes=source_size_bytes,
        content_type=source_content_type,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    with begin_transaction(db):
        db.add(row)
        db.flush()
        db.refresh(row)
    return row


def _sample_exists_for_attachment(
    db: Session, *, task_id: int, attachment_id: int
) -> bool:
    mapping = crud_sample.map_file_ids_to_sample_ids(
        db=db, task_id=task_id, file_ids=[attachment_id]
    )
    return attachment_id in mapping


def _attach_sample_if_missing(
    db: Session,
    *,
    task_id: int,
    attachment: TaskAttachment,
    current_user: User,
) -> tuple[bool, int | None]:
    existing_mapping = crud_sample.map_file_ids_to_sample_ids(
        db=db, task_id=task_id, file_ids=[attachment.id]
    )
    existing_sample_id = existing_mapping.get(attachment.id)
    if existing_sample_id is not None:
        return False, int(existing_sample_id)

    asyncio.run(
        sample_service.create(
            db=db,
            task_id=task_id,
            cmd=[sample_service.CreateSampleCommand(file_id=attachment.id, data={})],
            current_user=current_user,
            auto_preannotate=False,
            auto_preannotate_source="experiment_dataset_import",
            apply_image_pipeline=False,
        )
    )

    db.expire_all()
    created_mapping = crud_sample.map_file_ids_to_sample_ids(
        db=db, task_id=task_id, file_ids=[attachment.id]
    )
    created_sample_id = created_mapping.get(attachment.id)
    return True, int(created_sample_id) if created_sample_id is not None else None


def _rect_result_from_box(
    box: dict[str, Any],
    *,
    order: int,
    sample_width: float | None,
    sample_height: float | None,
    dataset_type: str,
) -> dict[str, Any] | None:
    x1 = _box_number(box.get("x1"))
    y1 = _box_number(box.get("y1"))
    x2 = _box_number(box.get("x2"))
    y2 = _box_number(box.get("y2"))
    label = str(box.get("class_name") or "").strip()
    if not label or None in (x1, y1, x2, y2) or x2 <= x1 or y2 <= y1:
        return None

    if _looks_like_percent_box(
        box,
        sample_width=sample_width,
        sample_height=sample_height,
        dataset_type=dataset_type,
    ):
        x1 = x1 / 100 * sample_width
        y1 = y1 / 100 * sample_height
        x2 = x2 / 100 * sample_width
        y2 = y2 / 100 * sample_height

    scale_x, scale_y = _annotation_display_scale(sample_width, sample_height)
    return {
        "id": str(uuid.uuid4()),
        "order": order,
        "label": label,
        "valid": True,
        "visible": True,
        "isVisible": True,
        "x": x1 * scale_x,
        "y": y1 * scale_y,
        "width": (x2 - x1) * scale_x,
        "height": (y2 - y1) * scale_y,
    }


def _build_preannotation_payload(
    *,
    package_id: str,
    sample_name: str,
    rect_results: list[dict[str, Any]],
    labels: list[str],
    width: float | None,
    height: float | None,
    raw_boxes: list[dict[str, Any]],
    source_external_task_id: str = "",
) -> dict[str, Any]:
    payload = {
        "sample_name": sample_name,
        "annotations": {
            "rectTool": {
                "toolName": "rectTool",
                "result": rect_results,
            }
        },
        "config": {"rectTool": [{"key": label, "value": label} for label in labels]},
        "meta": {
            "source_type": PREANNOTATION_SOURCE_TYPE,
            "package_id": package_id,
            "width": width,
            "height": height,
            "raw_target_boxes": raw_boxes,
            "external_task_id": source_external_task_id,
        },
    }
    return normalize_pre_annotation_payload_labels(payload)


def _create_preannotation_if_missing(
    db: Session,
    *,
    task_id: int,
    sample_name: str,
    rect_results: list[dict[str, Any]],
    labels: list[str],
    width: float | None,
    height: float | None,
    raw_boxes: list[dict[str, Any]],
    current_user: User,
    package_id: str,
    source_external_task_id: str = "",
) -> None:
    if not rect_results:
        return
    existing = (
        db.query(TaskPreAnnotation)
        .filter(
            TaskPreAnnotation.task_id == task_id,
            TaskPreAnnotation.sample_name == sample_name,
            TaskPreAnnotation.deleted_at.is_(None),
        )
        .first()
    )
    if existing:
        return

    payload = _build_preannotation_payload(
        package_id=package_id,
        sample_name=sample_name,
        rect_results=rect_results,
        labels=labels,
        width=width,
        height=height,
        raw_boxes=raw_boxes,
        source_external_task_id=source_external_task_id,
    )
    row = TaskPreAnnotation(
        task_id=task_id,
        file_id=None,
        sample_name=sample_name,
        data=json.dumps(payload, ensure_ascii=False),
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    with begin_transaction(db):
        db.add(row)
        db.flush()


def _count_task_samples(db: Session, task_id: int) -> int:
    return crud_sample.count(db=db, task_id=task_id)


def _inspect_import_row(
    db: Session,
    *,
    row: ExperimentDatasetImport,
) -> tuple[CreateExperimentDatasetImportCommand, Any, int, int]:
    """Validate S3 metadata and count usable samples without creating a task."""
    cmd = CreateExperimentDatasetImportCommand.model_validate(
        json.loads(row.source_payload_json)
    )
    admin = _import_owner(db, cmd)
    datasource = _get_or_create_datasource(
        db, bucket=cmd.dataset.bucket, current_user=admin
    )
    metadata = _read_metadata_json(datasource, cmd.dataset.metadata_key)
    metadata_samples = _metadata_samples(metadata)
    if not metadata_samples:
        return cmd, metadata, 0, max(int(cmd.sample_count or 0), 1)
    available_image_keys = _list_image_object_keys(datasource, cmd.dataset.image_prefix)
    valid = 0
    abnormal = 0
    seen_object_keys: set[str] = set()
    source_hashes: list[str] = []
    hash_failed = False
    for sample_row in metadata_samples:
        sample_name = _sample_image_name(sample_row)
        object_key = cmd.dataset.image_prefix + sample_name if sample_name else ""
        if not sample_name or object_key not in available_image_keys:
            abnormal += 1
            continue
        if object_key in seen_object_keys:
            continue
        seen_object_keys.add(object_key)
        valid += 1
        try:
            source_hashes.append(_sha256_s3_object(datasource, object_key))
        except Exception as exc:
            # Keep the existing object-key validation behavior when a source
            # store temporarily refuses a full read. The later download still
            # owns the definitive import failure; omitting the hash prevents
            # us from claiming content-level de-duplication we could not prove.
            hash_failed = True
            logger.warning(
                "experiment dataset source hash unavailable bucket={} key={} error={}",
                cmd.dataset.bucket,
                object_key,
                exc,
            )
    if isinstance(metadata, dict):
        # Keep the exact source identity used for the threshold calculation.
        # This is deliberately internal metadata; the original third-party
        # payload remains unchanged and is still the source of truth for S3.
        metadata = dict(metadata)
        metadata[_INTERNAL_VALID_SOURCE_KEYS] = sorted(
            f"{cmd.dataset.bucket}|{key}" for key in seen_object_keys
        )
        if not hash_failed and len(source_hashes) == valid:
            metadata[_INTERNAL_VALID_SOURCE_HASHES] = source_hashes
        else:
            metadata.pop(_INTERNAL_VALID_SOURCE_HASHES, None)
    return cmd, metadata, valid, abnormal


def _first_formal_preset_for_metadata(
    db: Session,
    *,
    cmd: CreateExperimentDatasetImportCommand,
    metadata: Any,
) -> Any | None:
    preset_index = _load_preset_index(db)
    for sample_row in _metadata_samples(metadata):
        class_names: list[str] = []
        for box in _sample_boxes(sample_row):
            label = str(box.get("class_name") or "").strip()
            if label and label not in class_names:
                class_names.append(label)
        preset = _resolve_formal_preset(
            preset_index,
            class_names,
            event_type=cmd.target.event_type,
        )
        if preset is not None:
            return preset
    return None


def _update_group_waiting(
    db: Session,
    *,
    group: ExperimentDatasetGroup,
    rows: list[ExperimentDatasetImport],
    status: str,
    valid_sample_count: int,
    declared_sample_count: int,
    model_resolution: dict[str, Any] | None = None,
    error: str | None = None,
    metadata_by_row_id: dict[int, Any] | None = None,
) -> None:
    # ``begin_transaction`` rolls back SQLAlchemy's implicit read transaction,
    # which expires ORM objects.  Capture inspection markers before entering it
    # or the waiting path would silently discard ``metadata_json`` (and the
    # source identities used for aggregate de-duplication).
    metadata_overrides = metadata_by_row_id or {}
    persisted_row_fields = {
        int(row.id): {
            "valid_sample_count": int(row.valid_sample_count or 0),
            "metadata_json": (
                json.dumps(metadata_overrides[int(row.id)], ensure_ascii=False)
                if int(row.id) in metadata_overrides
                else row.metadata_json
            ),
        }
        for row in rows
    }
    with begin_transaction(db):
        crud_experiment_dataset_group.update(
            db,
            row=group,
            obj_in={
                ExperimentDatasetGroup.status.key: status,
                ExperimentDatasetGroup.valid_sample_count.key: valid_sample_count,
                ExperimentDatasetGroup.declared_sample_count.key: declared_sample_count,
                ExperimentDatasetGroup.model_resolution_json.key: (
                    json.dumps(model_resolution, ensure_ascii=False)
                    if model_resolution is not None
                    else group.model_resolution_json
                ),
                ExperimentDatasetGroup.last_error.key: error,
            },
        )
        for row in rows:
            # Keep this as an ORM assignment instead of a bulk update.  The
            # inspection phase stores source keys/content hashes on the same
            # row, and ``update_by_id`` expires all instances after updating;
            # that used to discard the marker before the waiting transaction
            # was committed.
            row.status = status
            persisted = persisted_row_fields.get(int(row.id), {})
            row.valid_sample_count = int(
                persisted.get("valid_sample_count", 0) or 0
            )
            row.last_error = error
            row.finished_at = None
            row.metadata_json = persisted.get("metadata_json")
            db.add(row)
        db.flush()


def _persist_group_dataset_category(
    db: Session,
    *,
    group: ExperimentDatasetGroup,
    rows: list[ExperimentDatasetImport],
    dataset_category_id: int,
) -> None:
    category_id = int(dataset_category_id)
    existing_group_id = int(group.dataset_category_id or 0)
    if existing_group_id and existing_group_id != category_id:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=409,
            message="专家回流聚合组的数据集分类与当前请求不一致",
        )
    for row in rows:
        existing_row_id = int(row.dataset_category_id or 0)
        if existing_row_id and existing_row_id != category_id:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=409,
                message=f"专家回流包 {row.package_id} 已绑定到不同的数据集分类",
            )

    with begin_transaction(db):
        if not existing_group_id:
            crud_experiment_dataset_group.update(
                db,
                row=group,
                obj_in={ExperimentDatasetGroup.dataset_category_id.key: category_id},
            )
        for row in rows:
            if row.dataset_category_id is None:
                crud_experiment_dataset_import.update_by_id(
                    db,
                    import_job_id=int(row.id),
                    obj_in={
                        ExperimentDatasetImport.dataset_category_id.key: category_id
                    },
                )


def _group_model_id_conflict(
    inspections: list[
        tuple[
            ExperimentDatasetImport,
            CreateExperimentDatasetImportCommand,
            Any,
            int,
            int,
        ]
    ],
) -> dict[str, Any] | None:
    """Return a conflict payload when one aggregate mixes model identities.

    A caller-supplied aggregation key is allowed to group several packages,
    but it must never override the model lineage carried by each package.
    Empty and non-empty IDs are also a conflict: otherwise the first row would
    silently decide which published model owns the whole task.
    """
    model_ids = sorted(
        {
            str(cmd.target.model_id or "").strip()
            for _, cmd, _, _, _ in inspections
        }
    )
    non_empty = [item for item in model_ids if item]
    if len(model_ids) <= 1 or (not non_empty):
        return None
    return {
        "reason": "aggregation_model_id_conflict",
        "model_ids": [item or "<missing>" for item in model_ids],
    }


def _process_import_group(
    db: Session,
    *,
    trigger_row: ExperimentDatasetImport,
    lease_guard: LeaseGuard | None = None,
) -> None:
    _assert_import_worker_lease(lease_guard)
    aggregation_key = str(trigger_row.aggregation_key or "").strip()
    if not aggregation_key:
        _process_import_row(db, row=trigger_row, lease_guard=lease_guard)
        return

    group: ExperimentDatasetGroup | None = None
    rows: list[ExperimentDatasetImport] = []
    pending_rows: list[ExperimentDatasetImport] = []
    _assert_import_worker_lease(lease_guard)
    with begin_transaction(db):
        group = crud_experiment_dataset_group.get_by_key(
            db,
            aggregation_key=aggregation_key,
            lock=True,
        )
        if group is None:
            return
        if str(group.status or "").strip() == GROUP_STATUS_PROCESSING:
            return
        rows = crud_experiment_dataset_import.list_by_aggregation_key(
            db,
            aggregation_key=aggregation_key,
            lock=True,
        )
        pending_rows = [
            row
            for row in rows
            # A package_id is immutable after any terminal outcome.  FAILED
            # rows are retained as aggregate history and can only be retried
            # through a new package or an explicit operational retry path.
            if row.status
            not in {
                IMPORT_STATUS_COMPLETED,
                IMPORT_STATUS_PARTIAL_COMPLETED,
                IMPORT_STATUS_FAILED,
            }
        ]
        if not pending_rows:
            return
        _assert_import_worker_lease(lease_guard)
        crud_experiment_dataset_group.update(
            db,
            row=group,
            obj_in={ExperimentDatasetGroup.status.key: GROUP_STATUS_PROCESSING},
        )
        for row in pending_rows:
            _assert_import_worker_lease(lease_guard)
            crud_experiment_dataset_import.update_by_id(
                db,
                import_job_id=int(row.id),
                obj_in={ExperimentDatasetImport.status.key: IMPORT_STATUS_PROCESSING},
            )

    if group is None:
        _process_import_row(db, row=trigger_row, lease_guard=lease_guard)
        return

    inspections: list[tuple[ExperimentDatasetImport, CreateExperimentDatasetImportCommand, Any, int, int]] = []
    pending_ids = {int(row.id) for row in pending_rows}
    completed_rows = [row for row in rows if int(row.id) not in pending_ids]
    source_keys: set[str] = set()
    source_hashes: set[str] = set()
    hashed_valid = 0
    legacy_valid = 0
    inspection_metadata_by_row_id: dict[int, Any] = {}
    for completed_row in completed_rows:
        try:
            completed_cmd = CreateExperimentDatasetImportCommand.model_validate(
                json.loads(completed_row.source_payload_json)
            )
        except Exception:
            completed_cmd = None
        completed_metadata = _parse_json_object(completed_row.metadata_json)
        completed_hashes = _metadata_source_hashes(completed_metadata)
        if completed_hashes:
            # Content identity, rather than the source path, is the unit of
            # aggregation.  De-duplicate both within this package and against
            # earlier packages in the same aggregate.
            unique_hashes = list(dict.fromkeys(completed_hashes))
            new_hashes = [item for item in unique_hashes if item not in source_hashes]
            hashed_valid += len(new_hashes)
            source_hashes.update(unique_hashes)
            continue
        marker = completed_metadata.get(_INTERNAL_VALID_SOURCE_KEYS)
        if isinstance(marker, list):
            source_keys.update(
                str(item).strip() for item in marker if str(item).strip()
            )
        else:
            # Rows written before the marker was introduced remain counted by
            # their persisted valid count. New rows always use source identity
            # de-duplication and cannot inflate the total.
            legacy_valid += int(completed_row.valid_sample_count or 0)
    # A retry is an immutable re-attempt of an already declared package.  Its
    # sample count must not inflate the aggregate declaration a second time;
    # valid samples are de-duplicated separately by source identity/hash.
    previous_declared = sum(
        int(row.declared_sample_count or 0)
        for row in completed_rows
        if _retry_marker(row) is None
    )
    total_valid = legacy_valid
    total_declared = previous_declared
    for row in pending_rows:
        _assert_import_worker_lease(lease_guard)
        cmd, metadata, valid, abnormal = _inspect_import_row(db, row=row)
        inspections.append((row, cmd, metadata, valid, abnormal))
        inspection_metadata_by_row_id[int(row.id)] = metadata
        row_hashes = _metadata_source_hashes(metadata)
        has_source_hashes = bool(row_hashes)
        if row_hashes:
            unique_hashes = list(dict.fromkeys(row_hashes))
            new_hashes = [item for item in unique_hashes if item not in source_hashes]
            hashed_valid += len(new_hashes)
            source_hashes.update(unique_hashes)
            row.metadata_json = json.dumps(metadata, ensure_ascii=False)
            # A content marker is authoritative for this row; do not count
            # the object-key marker a second time below.
        marker = metadata.get(_INTERNAL_VALID_SOURCE_KEYS) if isinstance(metadata, dict) else None
        if isinstance(marker, list) and not has_source_hashes:
            source_keys.update(str(item).strip() for item in marker if str(item).strip())
        else:
            if not has_source_hashes:
                total_valid += int(valid)
        if _retry_marker(row) is None:
            total_declared += int(cmd.sample_count or 0)
        row.valid_sample_count = int(valid)
        row.metadata_json = json.dumps(metadata, ensure_ascii=False)

    # Make the inspection markers visible to the waiting-state update below.
    # The claim/processing transition uses bulk updates with session expiry;
    # an explicit flush prevents that expiry from discarding the in-memory
    # marker before it is persisted.
    db.flush()

    if source_keys:
        total_valid += len(source_keys)
    total_valid += hashed_valid

    model_conflict = _group_model_id_conflict(inspections)

    if group.formal_task_id is None and total_valid < int(
        group.min_sample_count or settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT
    ):
        _assert_import_worker_lease(lease_guard)
        _update_group_waiting(
            db,
            group=group,
            rows=pending_rows,
            status=IMPORT_STATUS_WAITING_FOR_SAMPLES,
            valid_sample_count=total_valid,
            declared_sample_count=total_declared,
            metadata_by_row_id=inspection_metadata_by_row_id,
        )
        return

    _, first_cmd, first_metadata, _, _ = inspections[0]
    dataset_category, _, category_resolution = _resolve_effective_dataset_category(
        db, first_cmd
    )
    if dataset_category is None and group.dataset_category_id:
        dataset_category = _dataset_category_by_id(db, group.dataset_category_id)
    if dataset_category is None and group.formal_task_id:
        existing_task = crud_task.get(db=db, task_id=int(group.formal_task_id))
        if existing_task and existing_task.dataset_category_id:
            dataset_category = _dataset_category_by_id(
                db, existing_task.dataset_category_id
            )
    if dataset_category is not None:
        _assert_import_worker_lease(lease_guard)
        _persist_group_dataset_category(
            db,
            group=group,
            rows=rows,
            dataset_category_id=int(dataset_category.id),
        )

    requested_model_id = ""
    manual_model_confirmation_required = model_conflict is not None
    model_resolution: dict[str, Any] = {
        **(_model_resolution_from_group(group) if model_conflict is None else model_conflict),
        **category_resolution,
    }
    if model_conflict is not None:
        model_resolution.update(
            {
                "manual_confirmation_required": True,
                "preannotation_allowed": False,
            }
        )
    for _, cmd, _, _, _ in inspections:
        requested_model_id = str(cmd.target.model_id or "").strip()
        if requested_model_id:
            break
    # The explicit source fields identify the current third-party contract.
    # Older internal callers omitted both fields and relied on the legacy
    # label-preset import path; keep that compatibility path while applying
    # the stricter missing-model rule to real integration payloads.
    explicit_external_contract = any(
        isinstance(_parse_json_object(row.source_payload_json), dict)
        and (
            "source_type" in _parse_json_object(row.source_payload_json)
            or "source_system" in _parse_json_object(row.source_payload_json)
        )
        for row, _, _, _, _ in inspections
    )
    if (
        group.formal_task_id is None
        and not requested_model_id
        and model_conflict is None
        and explicit_external_contract
    ):
        # A package without an external model id cannot be safely assigned to
        # any local model.  The integration contract requires an AICM model
        # match (name, labels, event and scene) before automatic preannotation;
        # this payload carries no model identity to perform that match.  Keep
        # the imported samples reviewable as a draft and wait for an explicit
        # model confirmation instead of sending them to a generic AI provider.
        manual_model_confirmation_required = True
        model_resolution = {
            **model_resolution,
            "ready": False,
            "reason": "model_id_missing",
            "manual_confirmation_required": True,
            "preannotation_allowed": False,
        }
    # Model identity allows task creation; local weights independently govern
    # automatic inference. Historical AICM models do not acquire fake lineage.
    model_label_contract: dict[str, Any] | None = None
    if requested_model_id and model_conflict is None:
        _assert_import_worker_lease(lease_guard)
        model_resolution = _resolve_import_model(
            db,
            requested_model_id,
        )
        if category_resolution:
            model_resolution = {**model_resolution, **category_resolution}
        if not model_resolution.get("identity_ready"):
            manual_model_confirmation_required = True
            model_resolution = {
                **model_resolution,
                "manual_confirmation_required": True,
                "preannotation_allowed": False,
            }

        if model_resolution.get("identity_ready") and (
            model_resolution.get("catalog_source_type") or model_resolution.get("labels")
        ):
            model_label_contract = _resolve_import_model_labels(
                db, first_cmd, model_resolution
            )
            if not model_label_contract.get("ready"):
                manual_model_confirmation_required = True
                model_resolution = {
                    **model_resolution,
                    **model_label_contract,
                    "manual_confirmation_required": True,
                    "preannotation_allowed": False,
                }
            elif dataset_category is None and model_label_contract.get(
                "dataset_category_id"
            ):
                dataset_category = _dataset_category_by_id(
                    db, model_label_contract["dataset_category_id"]
                )
                if dataset_category is not None:
                    _persist_group_dataset_category(
                        db,
                        group=group,
                        rows=rows,
                        dataset_category_id=int(dataset_category.id),
                    )

    reusable_model_labels = bool(
        model_label_contract and model_label_contract.get("ready")
    )
    if reusable_model_labels:
        draft_ids = {int(item.draft_task_id) for item in rows if item.draft_task_id}
        if len(draft_ids) > 1 or any(
            _import_draft_contract_conflict(db, task_id, model_label_contract)
            for task_id in draft_ids
        ):
            manual_model_confirmation_required = True
            model_resolution.update(
                reason="existing_draft_label_contract_conflict",
                manual_confirmation_required=True,
                preannotation_allowed=False,
            )
    if dataset_category is None and not reusable_model_labels:
        _assert_import_worker_lease(lease_guard)
        _update_group_waiting(
            db,
            group=group,
            rows=pending_rows,
            status=IMPORT_STATUS_WAITING_FOR_CATEGORY,
            valid_sample_count=total_valid,
            declared_sample_count=total_declared,
            model_resolution=model_resolution,
            error=(
                "dataset category is missing; provide dataset_category_key or "
                "configure the published model source category"
            ),
            metadata_by_row_id=inspection_metadata_by_row_id,
        )
        return

    if group.formal_task_id is None and requested_model_id and not reusable_model_labels:
        _assert_import_worker_lease(lease_guard)
        preset = _first_formal_preset_for_metadata(
            db,
            cmd=first_cmd,
            metadata=first_metadata,
        )
        if preset is not None:
            preset_labels = {
                str(item.label_value or "").strip()
                for item in preset.items or []
                if str(item.label_value or "").strip()
            }
            model_labels = {
                str(item).strip()
                for item in model_resolution.get("labels") or []
                if str(item).strip()
            }
            if model_labels and model_labels != preset_labels:
                model_resolution = {
                    **model_resolution,
                    "ready": False,
                    "reason": "model_labels_do_not_match_task_preset",
                    "expected_labels": sorted(preset_labels),
                    "manual_confirmation_required": True,
                    "preannotation_allowed": False,
                }
                manual_model_confirmation_required = True

    shared_formal_by_slug: dict[str, int] = {}
    # Reuse an existing draft created by an earlier package in this aggregate.
    # Pending rows can be claimed in a different order after a retry; deriving
    # the shared id from all rows prevents a second draft task in that case.
    shared_draft_task_id: int | None = next(
        (
            int(item.draft_task_id)
            for item in rows
            if item.draft_task_id is not None
        ),
        None,
    )
    group_task_id = int(group.formal_task_id) if group.formal_task_id else None
    final_statuses: list[str] = []
    for row, _, _, _, _ in inspections:
        _assert_import_worker_lease(lease_guard)
        (
            final_status,
            row_formal_total,
            row_draft_total,
            abnormal,
            formal_ids,
            draft_id,
            error,
            metadata,
        ) = _process_import_row(
            db,
            row=row,
            shared_formal_by_slug=shared_formal_by_slug,
            shared_draft_task_id=shared_draft_task_id,
            forced_formal_task_id=group_task_id,
            requested_model_id=requested_model_id or None,
            effective_dataset_category_id=(
                int(dataset_category.id) if dataset_category is not None else None
            ),
            force_draft=manual_model_confirmation_required,
            model_label_contract=(
                model_label_contract if reusable_model_labels else None
            ),
            import_model_resolution=model_resolution,
            auto_preannotation_skip_reason=(
                str(model_resolution.get("preannotation_skip_reason") or "") or None
                if not model_resolution.get("preannotation_allowed", True)
                else None
            ),
            lease_guard=lease_guard,
        )
        final_statuses.append(str(final_status))
        if formal_ids and group_task_id is None:
            group_task_id = int(formal_ids[0])
        if draft_id:
            shared_draft_task_id = int(draft_id)
        # Persist the total currently attached to the formal task(s). This is
        # important for an idempotent notification that adds one new image:
        # the row should report 2 imported samples, not only the delta 1.
        row_formal_count = int(row_formal_total or 0)
        row_status = final_status
        row_error = error
        if manual_model_confirmation_required:
            # Keep the completed import visible as a draft awaiting model
            # confirmation.  It remains retryable after the model is resolved,
            # but never looks like a formal, ready-to-train task in the meantime.
            row_status = IMPORT_STATUS_WAITING_FOR_MODEL
            row_error = str(
                model_resolution.get("reason")
                or "published model is not ready; manual confirmation required"
            )
        # Preserve the immutable source markers computed during inspection.
        # The row processor rereads the original metadata and adds import
        # evidence, which otherwise used to overwrite these markers and make
        # a later aggregate count legacy rows again.
        inspected_metadata = next(
            item[2] for item in inspections if int(item[0].id) == int(row.id)
        )
        if isinstance(inspected_metadata, dict):
            if not isinstance(metadata, dict):
                metadata = {}
            for marker_key in (
                _INTERNAL_VALID_SOURCE_KEYS,
                _INTERNAL_VALID_SOURCE_HASHES,
            ):
                if marker_key in inspected_metadata:
                    metadata[marker_key] = inspected_metadata[marker_key]
        _assert_import_worker_lease(lease_guard)
        with begin_transaction(db):
            crud_experiment_dataset_import.update_by_id(
                db,
                import_job_id=int(row.id),
                obj_in={
                    ExperimentDatasetImport.status.key: row_status,
                    ExperimentDatasetImport.formal_sample_count.key: row_formal_count,
                    ExperimentDatasetImport.draft_sample_count.key: (
                        int(row_draft_total or 0) if draft_id else 0
                    ),
                    ExperimentDatasetImport.abnormal_sample_count.key: int(abnormal),
                    ExperimentDatasetImport.valid_sample_count.key: int(
                        next(item[3] for item in inspections if item[0].id == row.id)
                    ),
                    ExperimentDatasetImport.formal_task_ids_json.key: json.dumps(
                        formal_ids, ensure_ascii=False
                    ),
                    ExperimentDatasetImport.draft_task_id.key: draft_id,
                    ExperimentDatasetImport.metadata_json.key: (
                        json.dumps(metadata, ensure_ascii=False)
                        if metadata is not None
                        else None
                    ),
                    ExperimentDatasetImport.last_error.key: row_error,
                    ExperimentDatasetImport.finished_at.key: datetime.now(),
                },
            )

    _assert_import_worker_lease(lease_guard)
    with begin_transaction(db):
        crud_experiment_dataset_group.update(
            db,
            row=group,
            obj_in={
                ExperimentDatasetGroup.status.key: (
                    GROUP_STATUS_WAITING_FOR_MODEL
                    if manual_model_confirmation_required
                    else (
                        GROUP_STATUS_FAILED
                        if not (group_task_id or shared_draft_task_id)
                        and any(
                            status == IMPORT_STATUS_FAILED for status in final_statuses
                        )
                        else GROUP_STATUS_CREATED
                    )
                ),
                ExperimentDatasetGroup.formal_task_id.key: group_task_id,
                ExperimentDatasetGroup.valid_sample_count.key: total_valid,
                ExperimentDatasetGroup.declared_sample_count.key: total_declared,
                ExperimentDatasetGroup.model_resolution_json.key: (
                    json.dumps(model_resolution, ensure_ascii=False)
                    if model_resolution
                    else group.model_resolution_json
                ),
                ExperimentDatasetGroup.created_task_at.key: datetime.now()
                if group_task_id
                else group.created_task_at,
                ExperimentDatasetGroup.last_error.key: (
                    str(
                        model_resolution.get("reason")
                        or "published model is not ready; manual confirmation required"
                    )
                    if manual_model_confirmation_required
                    else None
                ),
            },
        )


def _mark_group_failed(
    db: Session, *, trigger_row: ExperimentDatasetImport, error: str
) -> None:
    group = _group_for_row(db, trigger_row, lock=True)
    if group is None:
        with begin_transaction(db):
            crud_experiment_dataset_import.update_by_id(
                db,
                import_job_id=int(trigger_row.id),
                obj_in={
                    ExperimentDatasetImport.status.key: IMPORT_STATUS_FAILED,
                    ExperimentDatasetImport.last_error.key: error,
                    ExperimentDatasetImport.finished_at.key: datetime.now(),
                },
            )
        return
    rows = crud_experiment_dataset_import.list_by_aggregation_key(
        db, aggregation_key=str(group.aggregation_key), lock=True
    )
    with begin_transaction(db):
        crud_experiment_dataset_group.update(
            db,
            row=group,
            obj_in={
                ExperimentDatasetGroup.status.key: GROUP_STATUS_FAILED,
                ExperimentDatasetGroup.last_error.key: error,
            },
        )
        for row in rows:
            if row.status in {
                IMPORT_STATUS_COMPLETED,
                IMPORT_STATUS_PARTIAL_COMPLETED,
            }:
                continue
            crud_experiment_dataset_import.update_by_id(
                db,
                import_job_id=int(row.id),
                obj_in={
                    ExperimentDatasetImport.status.key: IMPORT_STATUS_FAILED,
                    ExperimentDatasetImport.last_error.key: error,
                    ExperimentDatasetImport.finished_at.key: datetime.now(),
                },
            )


def _process_import_row(
    db: Session,
    *,
    row: ExperimentDatasetImport,
    shared_formal_by_slug: dict[str, int] | None = None,
    shared_draft_task_id: int | None = None,
    forced_formal_task_id: int | None = None,
    requested_model_id: str | None = None,
    effective_dataset_category_id: int | None = None,
    force_draft: bool = False,
    model_label_contract: dict[str, Any] | None = None,
    import_model_resolution: dict[str, Any] | None = None,
    auto_preannotation_skip_reason: str | None = None,
    lease_guard: LeaseGuard | None = None,
) -> tuple[str, int, int, int, list[int], int | None, str | None, Any | None]:
    _assert_import_worker_lease(lease_guard)
    cmd = CreateExperimentDatasetImportCommand.model_validate(
        json.loads(row.source_payload_json)
    )
    admin = _import_owner(db, cmd)
    if effective_dataset_category_id is None and model_label_contract is None:
        _assert_import_worker_lease(lease_guard)
        dataset_category, _, _ = _resolve_effective_dataset_category(db, cmd)
        effective_dataset_category_id = (
            int(dataset_category.id) if dataset_category else None
        )
    if effective_dataset_category_id is None and model_label_contract is None:
        return (
            IMPORT_STATUS_WAITING_FOR_CATEGORY,
            0,
            0,
            0,
            [],
            None,
            "dataset category is missing and cannot be inherited from the published model",
            None,
        )
    dataset_category_id = int(effective_dataset_category_id) if effective_dataset_category_id is not None else None
    _assert_import_worker_lease(lease_guard)
    datasource = _get_or_create_datasource(
        db, bucket=cmd.dataset.bucket, current_user=admin
    )
    _assert_import_worker_lease(lease_guard)
    metadata = _read_metadata_json(datasource, cmd.dataset.metadata_key)
    metadata_samples = _metadata_samples(metadata)
    if not metadata_samples:
        return (
            IMPORT_STATUS_FAILED,
            0,
            0,
            max(int(cmd.sample_count or 0), 1),
            [],
            None,
            "metadata.json has no valid samples",
            metadata,
        )

    _assert_import_worker_lease(lease_guard)
    available_image_keys = _list_image_object_keys(datasource, cmd.dataset.image_prefix)
    preset_index = _load_preset_index(db)
    existing_formal_by_slug = (
        shared_formal_by_slug
        if shared_formal_by_slug is not None
        else _existing_formal_tasks_by_slug(db, row)
    )
    existing_draft_task_id = row.draft_task_id or shared_draft_task_id
    draft_task_id = None
    if existing_draft_task_id:
        _assert_import_worker_lease(lease_guard)
        draft_task_id = _get_or_create_draft_task(
            db,
            existing_task_id=existing_draft_task_id,
            cmd=cmd,
            current_user=admin,
            dataset_category_id=dataset_category_id,
        )

    v2_mode = (
        str(cmd.schema_version).lower().startswith("v2")
        or model_label_contract is not None
    )
    v2_task_id = None
    v2_label_aliases: dict[str, str] = {}
    v2_allowed_labels: set[str] = set()
    if v2_mode and not force_draft:
        _assert_import_worker_lease(lease_guard)
        resolved = system_label_service.resolve_external_labels(
            db, cmd.source_system, cmd.labeling.labels
        )
        _assert_import_worker_lease(lease_guard)
        v2_task_id = _get_or_create_v2_contract_task(
            db,
            existing_task_ids=(
                [int(forced_formal_task_id)]
                if forced_formal_task_id
                else list(
                    dict.fromkeys(
                        _parse_task_ids(row.formal_task_ids_json)
                        + list(existing_formal_by_slug.values())
                        + ([int(existing_draft_task_id)] if model_label_contract and existing_draft_task_id else [])
                    )
                )
            ),
            cmd=cmd,
            current_user=admin,
            label_ids=[int(label.id) for label in resolved],
            dataset_category_id=dataset_category_id,
            dataset_id=(model_label_contract or {}).get("dataset_id"),
            dataset_label_contract_id=(model_label_contract or {}).get(
                "dataset_label_contract_id"
            ),
            configure_existing=bool(model_label_contract and existing_draft_task_id),
        )
        _save_import_task_context(db, v2_task_id, row, import_model_resolution)
        if existing_draft_task_id and int(existing_draft_task_id) == v2_task_id:
            draft_task_id = None
        existing_formal_by_slug["__label_contract__"] = v2_task_id
        v2_allowed_labels = {label.label_value for label in resolved}
        v2_label_aliases = {
            str(external).strip().lower(): label.label_value
            for external, label in zip(cmd.labeling.labels, resolved)
        }

    formal_sample_count = 0
    draft_sample_count = 0
    abnormal_sample_count = 0
    prepared_samples: list[dict[str, Any]] = []
    auto_preannotate_sample_ids_by_task: dict[int, list[int]] = {}

    for sample_row in metadata_samples:
        _assert_import_worker_lease(lease_guard)
        sample_name = _sample_image_name(sample_row)
        if not sample_name:
            abnormal_sample_count += 1
            continue

        object_key = cmd.dataset.image_prefix + sample_name
        if object_key not in available_image_keys:
            abnormal_sample_count += 1
            continue

        boxes = _sample_boxes(sample_row)
        raw_class_names = []
        for box in boxes:
            label = str(box.get("class_name") or "").strip()
            if label and label not in raw_class_names:
                raw_class_names.append(label)
        preset = None if (v2_mode or force_draft) else _resolve_formal_preset(
            preset_index, raw_class_names, event_type=cmd.target.event_type
        )

        if v2_mode and v2_task_id is not None:
            task_id = v2_task_id
            sample_kind = "formal"
            allowed_labels = v2_allowed_labels
            normalized_boxes = []
            for box in boxes:
                normalized_box = dict(box)
                raw_value = str(box.get("class_name") or "").strip()
                normalized_box["class_name"] = v2_label_aliases.get(raw_value.lower(), raw_value)
                normalized_boxes.append(normalized_box)
            boxes = normalized_boxes
        elif preset is not None:
            if forced_formal_task_id:
                task_id = int(forced_formal_task_id)
                existing_formal_by_slug.setdefault(preset.slug, task_id)
                forced_task = crud_task.get(db=db, task_id=task_id)
                if forced_task:
                    _assert_import_worker_lease(lease_guard)
                    _ensure_task_dataset_category(db, forced_task, dataset_category_id)
            else:
                _assert_import_worker_lease(lease_guard)
                task_id = _get_or_create_formal_task(
                    db=db,
                    existing_by_slug=existing_formal_by_slug,
                    preset=preset,
                    cmd=cmd,
                    current_user=admin,
                    dataset_category_id=dataset_category_id,
                )
            sample_kind = "formal"
            allowed_labels = {
                str(item.label_value or "").strip()
                for item in preset.items or []
                if str(item.label_value or "").strip()
            }
        else:
            if draft_task_id is None:
                _assert_import_worker_lease(lease_guard)
                draft_task_id = _get_or_create_draft_task(
                    db=db,
                    existing_task_id=draft_task_id or existing_draft_task_id,
                    cmd=cmd,
                    current_user=admin,
                    dataset_category_id=dataset_category_id,
                )
            task_id = int(draft_task_id)
            sample_kind = "draft"
            allowed_labels = set()

        current_task = crud_task.get(db=db, task_id=task_id)
        if current_task:
            _assert_import_worker_lease(lease_guard)
            _ensure_task_dataset_category(db, current_task, dataset_category_id)

        source_head = _head_s3_image(ds=datasource, object_key=object_key)
        local_relative_path, source_attachment = _source_aware_attachment_path(
            db,
            task_id=task_id,
            filename=sample_name,
            identity=source_head,
        )
        _assert_import_worker_lease(lease_guard)
        download_result = _download_s3_image_with_resume(
            ds=datasource,
            object_key=object_key,
            relative_path=local_relative_path,
            head=source_head,
            expected_sha256=(
                str(source_attachment.source_sha256 or "").strip() or None
                if source_attachment is not None
                else None
            ),
        ) or {}
        prepared_samples.append(
            {
                "kind": sample_kind,
                "sample_row": sample_row,
                "sample_name": sample_name,
                "object_key": object_key,
                "local_relative_path": local_relative_path,
                "source_bucket": source_head.get("bucket"),
                "source_object_key": source_head.get("object_key"),
                "source_version_id": source_head.get("version_id"),
                "source_etag": source_head.get("etag"),
                "source_sha256": download_result.get("sha256"),
                "source_size_bytes": source_head.get("size_bytes"),
                "source_content_type": source_head.get("content_type"),
                "task_id": int(task_id),
                "allowed_labels": sorted(allowed_labels),
                "boxes": boxes,
            }
        )

    for prepared in prepared_samples:
        _assert_import_worker_lease(lease_guard)
        task_id = int(prepared["task_id"])
        sample_name = prepared["sample_name"]
        object_key = prepared["object_key"]
        source_sha256 = str(prepared.get("source_sha256") or "").strip().lower()
        # A different S3 object key can still contain the same image.  The
        # aggregate threshold is calculated by content hash, so the import
        # side effect must use the same identity.  Reuse the existing sample
        # and skip the duplicate attachment/preannotation for this task.
        # Source-object identity remains the fallback for legacy rows whose
        # content hash was not available.
        if source_sha256:
            duplicate_attachment = crud_attachment.get_by_task_sha256(
                db, task_id=task_id, sha256=source_sha256
            )
            if duplicate_attachment is not None:
                continue
        attachment = _get_or_create_attachment(
            db=db,
            task_id=task_id,
            datasource_id=datasource.id,
            object_key=object_key,
            local_relative_path=prepared["local_relative_path"],
            filename=_sanitize_attachment_filename(sample_name),
            current_user=admin,
            source_bucket=prepared.get("source_bucket"),
            source_object_key=prepared.get("source_object_key"),
            source_version_id=prepared.get("source_version_id"),
            source_etag=prepared.get("source_etag"),
            source_sha256=source_sha256 or prepared.get("source_sha256"),
            source_size_bytes=prepared.get("source_size_bytes"),
            source_content_type=prepared.get("source_content_type"),
        )

        if prepared["kind"] == "formal":
            _assert_import_worker_lease(lease_guard)
            created_new_sample, sample_id = _attach_sample_if_missing(
                db=db,
                task_id=task_id,
                attachment=attachment,
                current_user=admin,
            )
            if not created_new_sample or sample_id is None:
                continue

            formal_sample_count += 1
            sample_row = prepared["sample_row"]
            boxes = prepared["boxes"]
            width, height = _sample_dimensions(sample_row)
            display_width, display_height = _annotation_display_dimensions(
                width, height
            )
            allowed_labels = set(prepared["allowed_labels"])
            rect_results = []
            for order, box in enumerate(boxes, start=1):
                if str(box.get("class_name") or "").strip() not in allowed_labels:
                    continue
                rect_result = _rect_result_from_box(
                    box,
                    order=order,
                    sample_width=width,
                    sample_height=height,
                    dataset_type=cmd.dataset_type,
                )
                if rect_result:
                    rect_results.append(rect_result)
            if cmd.target_boxes_required and boxes and not rect_results:
                # Keep the sample for manual review, but never report a clean
                # import when metadata contained boxes that could not be
                # normalized or matched to the task label contract.
                abnormal_sample_count += 1
            _assert_import_worker_lease(lease_guard)
            _create_preannotation_if_missing(
                db=db,
                task_id=task_id,
                sample_name=sample_name,
                rect_results=rect_results,
                labels=sorted(allowed_labels),
                width=display_width,
                height=display_height,
                raw_boxes=boxes,
                current_user=admin,
                package_id=cmd.package_id,
                source_external_task_id=cmd.source_external_task_id,
            )
            auto_preannotate_sample_ids_by_task.setdefault(task_id, []).append(sample_id)
            continue

        _assert_import_worker_lease(lease_guard)
        created_new_sample, sample_id = _attach_sample_if_missing(
            db=db,
            task_id=task_id,
            attachment=attachment,
            current_user=admin,
        )
        if created_new_sample:
            draft_sample_count += 1
            if sample_id is not None:
                auto_preannotate_sample_ids_by_task.setdefault(task_id, []).append(sample_id)

    auto_preannotation_job_ids: list[int] = []
    for task_id, sample_ids in auto_preannotate_sample_ids_by_task.items():
        if sample_ids and not force_draft and not auto_preannotation_skip_reason:
            _assert_import_worker_lease(lease_guard)
            auto_job_id = schedule_auto_preannotate_for_new_samples(
                task_id=task_id,
                sample_ids=sample_ids,
                user_id=admin.id,
                source_hint="experiment_dataset_import_post_preannotation",
                requested_model_id=requested_model_id,
                idempotency_key=(
                    f"experiment_dataset_import:{int(row.id)}:task:{int(task_id)}"
                ),
            )
            if auto_job_id is not None:
                auto_preannotation_job_ids.append(int(auto_job_id))

    if isinstance(metadata, dict):
        metadata = dict(metadata)
        metadata["_labelu_auto_preannotation"] = {
            "job_ids": auto_preannotation_job_ids,
            "sample_count": (
                sum(
                    len(sample_ids)
                    for sample_ids in auto_preannotate_sample_ids_by_task.values()
                )
                if not force_draft and not auto_preannotation_skip_reason
                else 0
            ),
            "skipped_reason": (
                "manual_model_confirmation_required"
                if force_draft
                else auto_preannotation_skip_reason
            ),
            "skipped_detail": (
                "已匹配发布模型；本地没有可用训练权重，已跳过模型自动预标注，可人工标注。"
                if auto_preannotation_skip_reason
                == "published_model_local_weights_unavailable"
                else None
            ),
        }

    formal_task_ids = sorted(
        {int(task_id) for task_id in existing_formal_by_slug.values()}
    )
    formal_sample_count = sum(
        _count_task_samples(db, task_id) for task_id in formal_task_ids
    )
    draft_sample_count = _count_task_samples(db, draft_task_id) if draft_task_id else 0
    imported_total = formal_sample_count + draft_sample_count

    if imported_total == 0 and abnormal_sample_count > 0:
        _assert_import_worker_lease(lease_guard)
        _sync_legacy_strategy_projection(
            db,
            row=row,
            status=IMPORT_STATUS_FAILED,
            formal_task_ids=formal_task_ids,
            draft_task_id=draft_task_id,
            formal_sample_count=formal_sample_count,
            draft_sample_count=draft_sample_count,
            metadata=metadata,
        )
        return (
            IMPORT_STATUS_FAILED,
            0,
            0,
            abnormal_sample_count,
            formal_task_ids,
            draft_task_id,
            "no valid samples imported",
            metadata,
        )
    if abnormal_sample_count > 0:
        _assert_import_worker_lease(lease_guard)
        _sync_legacy_strategy_projection(
            db,
            row=row,
            status=IMPORT_STATUS_PARTIAL_COMPLETED,
            formal_task_ids=formal_task_ids,
            draft_task_id=draft_task_id,
            formal_sample_count=formal_sample_count,
            draft_sample_count=draft_sample_count,
            metadata=metadata,
        )
        return (
            IMPORT_STATUS_PARTIAL_COMPLETED,
            formal_sample_count,
            draft_sample_count,
            abnormal_sample_count,
            formal_task_ids,
            draft_task_id,
            None,
            metadata,
        )
    _assert_import_worker_lease(lease_guard)
    _sync_legacy_strategy_projection(
        db,
        row=row,
        status=IMPORT_STATUS_COMPLETED,
        formal_task_ids=formal_task_ids,
        draft_task_id=draft_task_id,
        formal_sample_count=formal_sample_count,
        draft_sample_count=draft_sample_count,
        metadata=metadata,
    )
    return (
        IMPORT_STATUS_COMPLETED,
        formal_sample_count,
        draft_sample_count,
        0,
        formal_task_ids,
        draft_task_id,
        None,
        metadata,
    )


def run_import_job_now(
    import_job_id: int,
    *,
    lease_owner: str | None = None,
    fencing_token: int | None = None,
) -> None:
    with session_scope() as db:
        row = crud_experiment_dataset_import.get(db, import_job_id=import_job_id)
        if not row:
            return
        if lease_owner is not None and (
            str(row.worker_lease_owner or "") != str(lease_owner)
            or int(row.worker_fencing_token or 0) != int(fencing_token or 0)
        ):
            logger.warning("experiment import lease lost before start job_id={}", import_job_id)
            return
        row_id = int(row.id)

        def lease_guard() -> bool:
            if lease_owner is None:
                return True
            try:
                return crud_experiment_dataset_import.worker_lease_is_valid(
                    db,
                    import_job_id=row_id,
                    owner=lease_owner,
                    fencing_token=int(fencing_token or 0),
                )
            except Exception as exc:
                logger.warning(
                    "experiment dataset import lease check failed job_id={} error={}",
                    row_id,
                    exc,
                )
                return False

        def fenced_update(obj_in: dict[str, Any]) -> bool:
            if lease_owner is None:
                return bool(
                    crud_experiment_dataset_import.update_by_id(
                        db, import_job_id=row_id, obj_in=obj_in
                    )
                )
            return crud_experiment_dataset_import.update_by_worker_lease(
                db,
                import_job_id=row_id,
                owner=lease_owner,
                fencing_token=int(fencing_token or 0),
                obj_in=obj_in,
            )

        if str(row.aggregation_key or "").strip():
            _assert_import_worker_lease(lease_guard if lease_owner is not None else None)
            if lease_owner is not None:
                # A crashed generation can leave the aggregate in PROCESSING
                # even though all of its row leases have expired.  Recover it
                # only when no other generation still owns a live row; this
                # prevents two Workers from processing the same aggregate.
                with begin_transaction(db):
                    group = _group_for_row(db, row, lock=True)
                    if group is not None and str(group.status or "").strip() == GROUP_STATUS_PROCESSING:
                        now = datetime.now()
                        active_other = any(
                            item.id != row.id
                            and str(item.worker_lease_owner or "").strip()
                            and item.worker_lease_expires_at is not None
                            and item.worker_lease_expires_at > now
                            for item in crud_experiment_dataset_import.list_by_aggregation_key(
                                db,
                                aggregation_key=str(row.aggregation_key),
                                lock=True,
                            )
                        )
                        if active_other:
                            logger.info(
                                "skip aggregate import while another Worker owns a live row job_id={}",
                                row_id,
                            )
                            return
                        crud_experiment_dataset_group.update(
                            db,
                            row=group,
                            obj_in={
                                ExperimentDatasetGroup.status.key: GROUP_STATUS_COLLECTING,
                                ExperimentDatasetGroup.last_error.key: None,
                            },
                        )
            heartbeat = None
            if lease_owner is not None:
                heartbeat = _ImportWorkerLeaseHeartbeat(
                    import_job_id=row_id,
                    owner=lease_owner,
                    fencing_token=int(fencing_token or 0),
                )
                heartbeat.start()
            try:
                _process_import_group(
                    db,
                    trigger_row=row,
                    lease_guard=lease_guard if lease_owner is not None else None,
                )
                if heartbeat is not None and heartbeat.lost:
                    logger.warning(
                        "experiment dataset group import lease lost during processing job_id={}",
                        row_id,
                    )
            except ImportWorkerLeaseLost:
                logger.warning(
                    "experiment dataset group import lease lost before side effect completion job_id={}",
                    row_id,
                )
                return
            except Exception as exc:
                logger.opt(exception=exc).error(
                    "experiment dataset group import failed package_id={} import_job_id={} aggregation_key={}",
                    row.package_id,
                    import_job_id,
                    row.aggregation_key,
                )
                # A replacement Worker may have taken the lease while the
                # aggregate was reading/downloading S3 objects.  Do not let
                # the stale generation mark the group failed after takeover;
                # the new generation will resume the still-pending members.
                if heartbeat is not None and heartbeat.lost:
                    logger.warning(
                        "experiment dataset group import lease lost before failure write job_id={}",
                        row_id,
                    )
                    return
                _mark_group_failed(
                    db,
                    trigger_row=row,
                    error=str(exc),
                )
            finally:
                if heartbeat is not None:
                    heartbeat.stop()
                if lease_owner is not None:
                    with begin_transaction(db):
                        crud_experiment_dataset_import.release_worker_lease(
                            db,
                            import_job_id=row_id,
                            owner=lease_owner,
                            fencing_token=int(fencing_token or 0),
                        )
            return
        package_id = str(row.package_id or "")
        started_at = row.started_at or datetime.now()

        with begin_transaction(db):
            if not fenced_update(
                {
                    ExperimentDatasetImport.status.key: IMPORT_STATUS_PROCESSING,
                    ExperimentDatasetImport.started_at.key: started_at,
                    ExperimentDatasetImport.last_error.key: None,
                }
            ):
                return

        heartbeat = None
        if lease_owner is not None:
            heartbeat = _ImportWorkerLeaseHeartbeat(
                import_job_id=row_id,
                owner=lease_owner,
                fencing_token=int(fencing_token or 0),
            )
            heartbeat.start()
        try:
            (
                final_status,
                formal_sample_count,
                draft_sample_count,
                abnormal_sample_count,
                formal_task_ids,
                draft_task_id,
                last_error,
                metadata,
            ) = _process_import_row(
                db,
                row=row,
                lease_guard=lease_guard if lease_owner is not None else None,
            )
            if heartbeat is not None and heartbeat.lost:
                logger.warning(
                    "experiment dataset import lease lost during processing job_id={}",
                    row_id,
                )
                return
            with begin_transaction(db):
                fenced_update(
                    {
                        ExperimentDatasetImport.status.key: final_status,
                        ExperimentDatasetImport.formal_sample_count.key: formal_sample_count,
                        ExperimentDatasetImport.draft_sample_count.key: draft_sample_count,
                        ExperimentDatasetImport.abnormal_sample_count.key: abnormal_sample_count,
                        ExperimentDatasetImport.formal_task_ids_json.key: json.dumps(
                            formal_task_ids, ensure_ascii=False
                        ),
                        ExperimentDatasetImport.draft_task_id.key: draft_task_id,
                        ExperimentDatasetImport.metadata_json.key: (
                            json.dumps(metadata, ensure_ascii=False)
                            if metadata is not None
                            else None
                        ),
                        ExperimentDatasetImport.last_error.key: last_error,
                        ExperimentDatasetImport.finished_at.key: datetime.now(),
                    }
                )
        except ImportWorkerLeaseLost:
            logger.warning(
                "experiment dataset import lease lost before side effect completion job_id={}",
                row_id,
            )
            return
        except Exception as exc:
            logger.opt(exception=exc).error(
                "experiment dataset import failed package_id={} import_job_id={}",
                package_id,
                import_job_id,
            )
            with begin_transaction(db):
                fenced_update(
                    {
                        ExperimentDatasetImport.status.key: IMPORT_STATUS_FAILED,
                        ExperimentDatasetImport.last_error.key: str(exc),
                        ExperimentDatasetImport.finished_at.key: datetime.now(),
                    }
                )
        finally:
            if heartbeat is not None:
                heartbeat.stop()
            if lease_owner is not None:
                with begin_transaction(db):
                    crud_experiment_dataset_import.release_worker_lease(
                        db,
                        import_job_id=row_id,
                        owner=lease_owner,
                        fencing_token=int(fencing_token or 0),
                    )


async def accept(
    db: Session,
    *,
    cmd: CreateExperimentDatasetImportCommand,
    source_payload_json_override: str | None = None,
    _manage_transaction: bool = True,
    _schedule_state: list[bool] | None = None,
    _owner_override: User | None = None,
) -> ExperimentDatasetImportAcceptedResponse:
    owner = _owner_override or _import_owner(db, cmd)
    dataset_category, _, _ = _resolve_effective_dataset_category(db, cmd)
    dataset_category_id = int(dataset_category.id) if dataset_category else None
    aggregation_key, identity = _aggregation_key(cmd)
    identity_json = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    payload_json = source_payload_json_override or _canonical_import_payload(
        cmd.model_dump(mode="json")
    )
    try:
        payload_identity_json = _canonical_import_payload(payload_json)
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=409,
            message="导入请求内容无效，无法创建重试任务，请重新提交",
        ) from exc
    retry_marker = _retry_marker_from_payload(payload_json)
    retry_source_row: ExperimentDatasetImport | None = None
    if retry_marker is not None:
        try:
            retry_source_id = int(retry_marker.get("source_import_job_id") or 0)
        except (TypeError, ValueError):
            retry_source_id = 0
        if retry_source_id > 0:
            retry_source_row = crud_experiment_dataset_import.get(
                db, import_job_id=retry_source_id, lock=_manage_transaction is False
            )
    should_schedule = False
    transaction = begin_transaction(db) if _manage_transaction else nullcontext()
    with transaction:
        row = crud_experiment_dataset_import.get_by_package_id(
            db=db, package_id=cmd.package_id, lock=True
        )
        if row:
            try:
                stored_payload = _canonical_import_payload(row.source_payload_json)
            except (TypeError, ValueError, json.JSONDecodeError, ValidationError):
                stored_payload = str(row.source_payload_json or "")
            if stored_payload != payload_identity_json:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=409,
                    message="package_id 已存在，但本次请求内容与首次请求不一致",
                )
        group = crud_experiment_dataset_group.get_by_key(
            db, aggregation_key=aggregation_key, lock=True
        )
        if group is None:
            try:
                with db.begin_nested():
                    group = crud_experiment_dataset_group.create(
                        db,
                        ExperimentDatasetGroup(
                            aggregation_key=aggregation_key,
                            source_type=cmd.source_type,
                            source_system=cmd.source_system or None,
                            source_external_task_id=cmd.source_external_task_id or None,
                            dataset_type=cmd.dataset_type,
                            dataset_date=cmd.dataset_date,
                            event_type=cmd.target.event_type,
                            model_id=cmd.target.model_id,
                            dataset_category_id=dataset_category_id,
                            identity_json=identity_json,
                            min_sample_count=settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT,
                            declared_sample_count=0,
                            status=GROUP_STATUS_COLLECTING,
                        ),
                    )
            except IntegrityError:
                group = crud_experiment_dataset_group.get_by_key(
                    db, aggregation_key=aggregation_key, lock=True
                )
            if group is None:
                raise RuntimeError("aggregation group creation raced and could not be reloaded")
        elif _parse_json_object(group.identity_json) != identity:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=409,
                message="aggregation_key 已存在，但本次请求的任务身份不一致",
            )

        if (
            group.dataset_category_id is not None
            and dataset_category_id is not None
            and int(group.dataset_category_id) != int(dataset_category_id)
        ):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=409,
                message="aggregation_key 已绑定到不同的数据集分类",
            )


        if group.dataset_category_id is None and dataset_category_id is not None:
            group.dataset_category_id = dataset_category_id

        # Legacy failed rows may predate aggregation groups.  When their first
        # retry creates/joins a group, seed the aggregate declaration once from
        # that historical row; subsequent retry attempts see the source or an
        # existing retry marker and do not add it again.
        if retry_marker is not None and retry_source_row is not None:
            group_rows = crud_experiment_dataset_import.list_by_aggregation_key(
                db,
                aggregation_key=aggregation_key,
                lock=not _manage_transaction,
            )
            source_id = int(retry_source_row.id)
            source_present = any(int(item.id) == source_id for item in group_rows)
            if not source_present:
                # Rows written before aggregate groups existed are not returned
                # by ``list_by_aggregation_key``. Attach that immutable source
                # row to the newly created/current group so the Worker includes
                # its declared count and valid-source markers on every retry.
                retry_source_row = crud_experiment_dataset_import.get(
                    db, import_job_id=source_id, lock=not _manage_transaction
                )
                if retry_source_row is not None:
                    retry_source_row.aggregation_key = aggregation_key
                    retry_source_row.aggregation_group_id = group.id
                    if (
                        retry_source_row.dataset_category_id is None
                        and dataset_category_id is not None
                    ):
                        retry_source_row.dataset_category_id = dataset_category_id
                    db.add(retry_source_row)
                    db.flush()
                    group_rows.append(retry_source_row)

            # Seed/repair the aggregate declaration from non-retry rows.  The
            # Worker recomputes this value from group rows, so keeping the
            # baseline at least this high prevents a legacy source that was
            # previously outside the group from being overwritten to zero.
            non_retry_declared = sum(
                int(item.declared_sample_count or 0)
                for item in group_rows
                if _retry_marker(item) is None
            )
            if non_retry_declared > int(group.declared_sample_count or 0):
                group.declared_sample_count = non_retry_declared

        active_statuses = {
            IMPORT_STATUS_PENDING,
            IMPORT_STATUS_PROCESSING,
            IMPORT_STATUS_WAITING_FOR_SAMPLES,
            IMPORT_STATUS_WAITING_FOR_MODEL,
            IMPORT_STATUS_WAITING_FOR_CATEGORY,
        }
        if row and row.status in active_statuses:
            waiting_retry = row.status in {
                IMPORT_STATUS_WAITING_FOR_SAMPLES,
                IMPORT_STATUS_WAITING_FOR_MODEL,
                IMPORT_STATUS_WAITING_FOR_CATEGORY,
            } and (
                row.worker_lease_expires_at is None
                or row.worker_lease_expires_at <= datetime.now()
            )
            row = crud_experiment_dataset_import.update(
                db=db,
                row=row,
                obj_in={
                    ExperimentDatasetImport.aggregation_key.key: aggregation_key,
                    ExperimentDatasetImport.aggregation_group_id.key: group.id,
                    ExperimentDatasetImport.dataset_category_id.key: (
                        row.dataset_category_id or dataset_category_id
                    ),
                    **(
                        {
                            ExperimentDatasetImport.status.key: IMPORT_STATUS_PENDING,
                            ExperimentDatasetImport.last_error.key: None,
                            ExperimentDatasetImport.started_at.key: None,
                            ExperimentDatasetImport.finished_at.key: None,
                        }
                        if waiting_retry
                        else {}
                    ),
                },
            )
            accepted = _accepted_response(db, row, group)
            should_schedule = waiting_retry
        elif row:
            # ``package_id`` is the idempotency key for one immutable data
            # package.  Once that package reached a terminal state, a
            # repeated notification is only a status read: it must not reset
            # the row to PENDING and re-read a changed metadata.json, because
            # that could append new attachments/samples to an already created
            # task.  A retry must use a new package_id (or the dedicated
            # operational retry path), keeping package contents auditable.
            if row.status in {
                IMPORT_STATUS_COMPLETED,
                IMPORT_STATUS_PARTIAL_COMPLETED,
                IMPORT_STATUS_FAILED,
            }:
                accepted = _accepted_response(
                    db,
                    row,
                    group,
                    message_override=(
                        "已有失败记录，请重新执行/换 package_id"
                        if row.status == IMPORT_STATUS_FAILED
                        else None
                    ),
                )
                should_schedule = False
            else:
                row = crud_experiment_dataset_import.update(
                    db=db,
                    row=row,
                    obj_in={
                        ExperimentDatasetImport.aggregation_key.key: aggregation_key,
                        ExperimentDatasetImport.aggregation_group_id.key: group.id,
                        ExperimentDatasetImport.dataset_category_id.key: (
                            row.dataset_category_id or dataset_category_id
                        ),
                        ExperimentDatasetImport.status.key: IMPORT_STATUS_PENDING,
                        ExperimentDatasetImport.last_error.key: None,
                        ExperimentDatasetImport.started_at.key: None,
                        ExperimentDatasetImport.finished_at.key: None,
                        ExperimentDatasetImport.updated_by.key: owner.id,
                    },
                )
                accepted = _accepted_response(db, row, group)
                should_schedule = True
        else:
            # The original failed attempt already declared these samples. A
            # retry shares its aggregation group but must not declare them a
            # second time.
            if retry_marker is None:
                group.declared_sample_count = int(
                    group.declared_sample_count or 0
                ) + int(cmd.sample_count or 0)
            row = crud_experiment_dataset_import.create(
                db=db,
                row=ExperimentDatasetImport(
                    package_id=cmd.package_id,
                    source_type=cmd.source_type,
                    source_system=cmd.source_system or None,
                    source_external_task_id=cmd.source_external_task_id or None,
                    source_external_package_id=cmd.package_id,
                    source_integration_id=cmd.source_integration_id,
                    dataset_type=cmd.dataset_type,
                    dataset_date=cmd.dataset_date,
                    event_type=cmd.target.event_type,
                    model_id=cmd.target.model_id,
                    dataset_category_id=dataset_category_id,
                    target_signature=_target_signature_from_prefix(cmd.dataset.prefix),
                    source_payload_json=payload_json,
                    status=IMPORT_STATUS_PENDING,
                    formal_task_ids_json="[]",
                    declared_sample_count=cmd.sample_count,
                    aggregation_key=aggregation_key,
                    aggregation_group_id=group.id,
                    created_by=owner.id,
                    updated_by=owner.id,
                ),
            )
            accepted = _accepted_response(db, row, group)
            should_schedule = True
    if _schedule_state is not None:
        _schedule_state[:] = [should_schedule]
    if should_schedule and _manage_transaction:
        try:
            schedule_import_job(accepted.import_job_id)
        except RuntimeError:
            logger.warning(
                "experiment dataset import schedule skipped package_id={} because event loop is unavailable",
                cmd.package_id,
            )
    return accepted


async def retry_import(
    db: Session,
    *,
    import_job_id: int,
    reason: str = "",
) -> ExperimentDatasetImportAcceptedResponse:
    """Create a new immutable attempt for a failed import.

    The historical ``FAILED`` row is never reset or overwritten.  The cloned
    request is accepted under a unique package id and carries a server-owned
    marker linking it back to the original row.  Worker execution resolves the
    current S3 datasource at run time, so endpoint rotations apply to retries.
    """
    schedule_state: list[bool] = []
    accepted: ExperimentDatasetImportAcceptedResponse | None = None
    # Resolve the datasource owner before opening the retry lock transaction.
    # ``ensure_open_system_user`` may backfill legacy provenance in its own
    # transaction; calling it after the lock is acquired would roll back that
    # lock in the project's legacy transaction helper.
    owner_override: User | None = None
    preflight_source = crud_experiment_dataset_import.get(
        db, import_job_id=int(import_job_id)
    )
    if preflight_source is not None:
        if preflight_source.created_by:
            owner_override = crud_user.get(db, int(preflight_source.created_by))
        if owner_override is None:
            owner_override = (
                ensure_open_system_user(db)
                if str(preflight_source.source_type or "").strip() == "third_party"
                else _admin_user(db)
            )
    try:
        # Lock the failed source and all of its retry rows together.  This
        # makes duplicate administrator clicks converge before generating the
        # next deterministic package id on databases that honor row locks.
        with begin_transaction(db):
            source_row = crud_experiment_dataset_import.get(
                db, import_job_id=int(import_job_id), lock=True
            )
            if source_row is None:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=404,
                    message="失败任务不存在或已归档",
                )
            if str(source_row.status or "").strip() != IMPORT_STATUS_FAILED:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=409,
                    message="仅失败状态的专家回流任务可以重新执行",
                )

            candidates = _retry_candidates(db, source_row, lock=True)
            active = next(
                (item for item in candidates if _retry_attempt_is_active(item)),
                None,
            )
            if active is not None:
                accepted = _accepted_response(
                    db,
                    active,
                    _group_for_row(db, active),
                    message_override="已有重试任务正在执行，请勿重复操作",
                )
            else:
                completed_retry = next(
                    (
                        item
                        for item in reversed(candidates)
                        if str(item.status or "").strip()
                        in {
                            IMPORT_STATUS_COMPLETED,
                            IMPORT_STATUS_PARTIAL_COMPLETED,
                        }
                    ),
                    None,
                )
                if completed_retry is not None:
                    accepted = _accepted_response(
                        db,
                        completed_retry,
                        _group_for_row(db, completed_retry),
                        message_override=(
                            "已有成功的重试结果，无需重复执行"
                            if completed_retry.status == IMPORT_STATUS_COMPLETED
                            else "已有部分完成的重试结果，如需补充请使用新的数据包编号"
                        ),
                    )
                else:
                    max_attempt = max(
                        [
                            int((_retry_marker(item) or {}).get("attempt") or 0)
                            for item in candidates
                        ]
                        + [0]
                    )
                    attempt = max_attempt + 1
                    package_id = _retry_package_id(
                        source_row.package_id, int(source_row.id), attempt
                    )
                    # A manually supplied package id can theoretically collide
                    # with the deterministic suffix. Advance the generation
                    # rather than mutating or reusing an unrelated row.
                    while crud_experiment_dataset_import.get_by_package_id(
                        db,
                        package_id=package_id,
                        lock=True,
                        include_deleted=True,
                    ):
                        attempt += 1
                        package_id = _retry_package_id(
                            source_row.package_id, int(source_row.id), attempt
                        )
                    cmd, payload_json = _retry_source_payload(
                        source_row,
                        package_id=package_id,
                        source_import_job_id=int(source_row.id),
                        attempt=attempt,
                        reason=reason,
                    )
                    accepted = await accept(
                        db,
                        cmd=cmd,
                        source_payload_json_override=payload_json,
                        _manage_transaction=False,
                        _schedule_state=schedule_state,
                        _owner_override=owner_override,
                    )
    except IntegrityError:
        # Two administrator clicks can compute the same deterministic package
        # id concurrently.  The unique package constraint is the arbiter; once
        # the losing transaction rolls back, return the winner as an idempotent
        # response instead of surfacing a database error.
        db.rollback()
        source_row = crud_experiment_dataset_import.get(
            db, import_job_id=int(import_job_id)
        )
        if source_row is None:
            raise
        winner = next(
            (
                item
                for item in _retry_candidates(db, source_row)
                if _retry_attempt_is_active(item)
            ),
            None,
        )
        if winner is None:
            raise
        return _accepted_response(
            db,
            winner,
            _group_for_row(db, winner),
            message_override="已有重试任务正在执行，请勿重复操作",
        )
    if accepted is None:
        raise RuntimeError("重试任务未生成响应")
    if schedule_state and schedule_state[0]:
        try:
            schedule_import_job(accepted.import_job_id)
        except RuntimeError:
            logger.warning(
                "experiment dataset retry schedule skipped package_id={} because event loop is unavailable",
                accepted.package_id,
            )
    if schedule_state and schedule_state[0]:
        accepted.message = "失败任务已重新提交，正在使用当前启用的 S3 配置重试"
    return accepted


def archive_import(
    db: Session, *, import_job_id: int, actor_id: int | None = None
) -> dict[str, Any]:
    """Soft-delete an unproductive failed row while retaining DB history.

    ``updated_by`` records the administrator who performed the archive when
    the request came through the authenticated evolution endpoint.  The
    optional argument keeps internal maintenance callers backwards compatible.
    """
    with begin_transaction(db):
        # Lock the source before checking its state and retry descendants. This
        # makes archive and retry serialize on databases that honor row locks;
        # the checks are deliberately repeated inside the same transaction.
        row = crud_experiment_dataset_import.get(
            db, import_job_id=int(import_job_id), lock=True
        )
        if row is None:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=404,
                message="专家回流任务不存在或已归档",
            )
        if str(row.status or "").strip() != IMPORT_STATUS_FAILED:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=409,
                message="仅失败状态的专家回流任务可以归档",
            )
        if _parse_task_ids(row.formal_task_ids_json) or row.draft_task_id:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=409,
                message="该失败任务已创建任务，不能归档",
            )
        group = _group_for_row(db, row, lock=True)
        if group is not None and group.formal_task_id:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=409,
                message="该失败任务所属聚合组已创建正式任务，不能归档",
            )
        active_retry = next(
            (
                item
                for item in _retry_candidates(db, row, lock=True)
                if _retry_attempt_is_active(item)
            ),
            None,
        )
        if active_retry is not None:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=409,
                message="该失败任务已有重试任务，完成后再归档",
            )
        if actor_id is not None:
            row.updated_by = int(actor_id)
        crud_experiment_dataset_import.soft_delete(db, row)
    return {
        "archived": True,
        "import_job_id": int(row.id),
        "package_id": row.package_id,
        "message": "失败任务已归档，原始失败原因仍保留在历史记录中",
    }


def _import_list_item(
    db: Session, row: ExperimentDatasetImport
) -> ExperimentDatasetImportListItemResponse:
    """Build the public list representation for one persisted import row."""
    group = _group_for_row(db, row)
    source = _source_payload(row)
    dataset = source.get("dataset") if isinstance(source.get("dataset"), dict) else {}
    source_target = source.get("target") if isinstance(source.get("target"), dict) else {}
    # 第三方请求同时存在 model_id 和 modelId 两种历史写法。列表页
    # 必须使用实际请求中的模型编号，不能因为数据库旧字段为空而显示“未提供”。
    display_model_id = row.model_id or source_target.get("model_id") or source_target.get("modelId")
    snapshot = _status_snapshot(row, group, db)
    return ExperimentDatasetImportListItemResponse(
        package_id=row.package_id,
        source_type=str(row.source_type or "internal"),
        source_system=row.source_system,
        source_external_task_id=row.source_external_task_id,
        source_external_package_id=row.source_external_package_id,
        source_integration_id=row.source_integration_id,
        import_job_id=row.id,
        status=row.status,
        **snapshot,
        message=_status_message(str(row.status or "").strip()),
        status_url=f"/internal/v1/experiment-datasets/{row.package_id}",
        last_error=row.last_error,
        **_retry_fields(row),
        dataset_type=row.dataset_type,
        dataset_date=row.dataset_date,
        window_start=str(source.get("window_start") or ""),
        window_end=str(source.get("window_end") or ""),
        event_type=row.event_type,
        # 响应契约要求字符串；历史回流包可能没有模型编号，列表
        # 仍必须正常返回并在前端显示“未提供”，不能让一条旧数据
        # 使整个专家回流列表返回 500。
        model_id=str(display_model_id) if display_model_id not in (None, "") else "",
        target_signature=row.target_signature,
        s3_uri=str(dataset.get("s3_uri") or ""),
        metadata_key=str(dataset.get("metadata_key") or ""),
        dataset=dataset,
        labeling=(
            source.get("labeling") if isinstance(source.get("labeling"), dict) else {}
        ),
        target=_externalize_model_id_keys(
            source.get("target") if isinstance(source.get("target"), dict) else {}
        ),
        declared_sample_count=(
            group.declared_sample_count if group is not None else row.declared_sample_count
        ),
        formal_task_ids=_parse_task_ids(row.formal_task_ids_json),
        draft_task_id=row.draft_task_id,
        formal_sample_count=row.formal_sample_count,
        draft_sample_count=row.draft_sample_count,
        abnormal_sample_count=row.abnormal_sample_count,
        valid_sample_count=(
            group.valid_sample_count if group is not None else row.valid_sample_count
        ),
        required_sample_count=(
            group.min_sample_count
            if group is not None
            else settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT
        ),
        aggregation_key=str(row.aggregation_key or ""),
        model_resolution=_model_resolution_from_group(group),
        **_category_response_fields(db, row, group),
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def _serialize_import_rows(
    db: Session, rows: list[ExperimentDatasetImport]
) -> list[ExperimentDatasetImportListItemResponse]:
    return [_import_list_item(db, row) for row in rows]


async def list_imports(
    db: Session,
    *,
    package_id: str | None = None,
    status: str | None = None,
    model_id: str | None = None,
) -> list[ExperimentDatasetImportListItemResponse]:
    """Legacy unbounded list used by the internal strategy callback."""
    rows = crud_experiment_dataset_import.list_by(
        db,
        package_id=package_id,
        status=status,
        model_id=model_id,
    )
    return _serialize_import_rows(db, rows)


async def list_imports_page(
    db: Session,
    *,
    package_id: str | None = None,
    status: str | None = None,
    model_id: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 20,
) -> tuple[list[ExperimentDatasetImportListItemResponse], int]:
    """List a bounded, searchable page for the evolution center."""
    rows, total = crud_experiment_dataset_import.list_by_page(
        db,
        package_id=package_id,
        status=status,
        model_id=model_id,
        search=search,
        page=page,
        size=size,
    )
    return _serialize_import_rows(db, rows), total


async def list_created_tasks(
    db: Session,
    *,
    page: int = 0,
    size: int = 100,
    status: str | None = None,
) -> tuple[list[ExperimentDatasetCreatedTaskResponse], int]:
    """List active formal tasks created by the expert-feedback import flow."""
    rows = crud_experiment_dataset_import.list_by(db)
    task_rows: list[ExperimentDatasetCreatedTaskResponse] = []
    task_ids: list[int] = []
    pending: list[
        tuple[ExperimentDatasetImport, Task, ExperimentDatasetGroup | None]
    ] = []
    seen_task_ids: set[int] = set()

    for row in rows:
        group = _group_for_row(db, row)
        for task_id in _parse_task_ids(row.formal_task_ids_json):
            task = crud_task.get(db=db, task_id=task_id)
            if not task or task.deleted_at is not None:
                continue
            if status and str(task.status or "").strip() != str(status).strip():
                continue
            if int(task.id) in seen_task_ids:
                continue
            seen_task_ids.add(int(task.id))
            pending.append((row, task, group))
            task_ids.append(int(task.id))

    statics = crud_sample.statics(db=db, task_ids=task_ids)
    state_names = {
        "new": SampleState.NEW.value,
        "done": SampleState.DONE.value,
        "skipped": SampleState.SKIPPED.value,
        "abnormal": SampleState.ABNORMAL.value,
        "incomplete": SampleState.INCOMPLETE.value,
    }

    for row, task, group in pending:
        source = _source_payload(row)
        target = source.get("target") if isinstance(source.get("target"), dict) else {}
        task_stats = {
            key: int(statics.get(f"{task.id}_{state}", 0) or 0)
            for key, state in state_names.items()
        }
        task_rows.append(
            ExperimentDatasetCreatedTaskResponse(
                task_id=int(task.id),
                source_type=str(task.source_type or row.source_type or "internal"),
                source_system=task.source_system or row.source_system,
                source_external_task_id=(
                    task.source_external_task_id or row.source_external_task_id
                ),
                source_external_package_id=(
                    task.source_external_package_id
                    or row.source_external_package_id
                    or row.package_id
                ),
                source_integration_id=(
                    task.source_integration_id or row.source_integration_id
                ),
                task_url=f"/tasks/{task.id}",
                name=str(task.name or ""),
                description=task.description,
                media_type=str(task.media_type or ""),
                status=str(task.status or ""),
                created_at=task.created_at,
                package_id=row.package_id,
                import_job_id=int(row.id),
                import_status=str(row.status or ""),
                dataset_date=row.dataset_date,
                event_type=row.event_type,
                model_id=row.model_id,
                target=_externalize_model_id_keys(target),
                formal_sample_count=int(
                    group.valid_sample_count
                    if group is not None
                    else row.formal_sample_count or 0
                ),
                draft_sample_count=int(row.draft_sample_count or 0),
                abnormal_sample_count=int(row.abnormal_sample_count or 0),
                valid_sample_count=int(
                    group.valid_sample_count
                    if group is not None
                    else row.valid_sample_count or 0
                ),
                required_sample_count=int(
                    group.min_sample_count
                    if group is not None
                    else settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT
                ),
                aggregation_key=str(row.aggregation_key or ""),
                model_resolution=_model_resolution_from_group(group),
                **_category_response_fields(db, row, group, task),
                stats=task_stats,
            )
        )

    task_rows.sort(key=lambda item: item.task_id, reverse=True)
    total = len(task_rows)
    start = page * size
    return task_rows[start : start + size], total


async def get_import_detail(
    db: Session, *, package_id: str
) -> ExperimentDatasetImportDetailResponse | None:
    row = crud_experiment_dataset_import.get_by_package_id(db, package_id=package_id)
    if not row:
        return None
    group = _group_for_row(db, row)
    source = _source_payload(row)
    dataset = source.get("dataset") if isinstance(source.get("dataset"), dict) else {}
    snapshot = _status_snapshot(row, group, db)
    return ExperimentDatasetImportDetailResponse(
        package_id=row.package_id,
        source_type=str(row.source_type or "internal"),
        source_system=row.source_system,
        source_external_task_id=row.source_external_task_id,
        source_external_package_id=row.source_external_package_id,
        source_integration_id=row.source_integration_id,
        import_job_id=row.id,
        status=row.status,
        **snapshot,
        message=_status_message(str(row.status or "").strip()),
        status_url=f"/internal/v1/experiment-datasets/{row.package_id}",
        dataset_type=row.dataset_type,
        dataset_date=row.dataset_date,
        window_start=str(source.get("window_start") or ""),
        window_end=str(source.get("window_end") or ""),
        event_type=row.event_type,
        model_id=row.model_id,
        target_signature=row.target_signature,
        s3_uri=str(dataset.get("s3_uri") or ""),
        metadata_key=str(dataset.get("metadata_key") or ""),
        dataset=dataset,
        labeling=(
            source.get("labeling") if isinstance(source.get("labeling"), dict) else {}
        ),
        target=_externalize_model_id_keys(
            source.get("target") if isinstance(source.get("target"), dict) else {}
        ),
        declared_sample_count=(
            group.declared_sample_count
            if group is not None
            else row.declared_sample_count
        ),
        formal_task_ids=snapshot["progress"]["formal_task_ids"],
        draft_task_id=row.draft_task_id,
        formal_sample_count=row.formal_sample_count,
        draft_sample_count=row.draft_sample_count,
        abnormal_sample_count=row.abnormal_sample_count,
        valid_sample_count=(
            group.valid_sample_count if group is not None else row.valid_sample_count
        ),
        required_sample_count=(
            group.min_sample_count
            if group is not None
            else settings.EXPERIMENT_DATASET_MIN_SAMPLE_COUNT
        ),
        aggregation_key=str(row.aggregation_key or ""),
        model_resolution=_model_resolution_from_group(group),
        **_category_response_fields(db, row, group),
        last_error=row.last_error,
        **_retry_fields(row),
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
