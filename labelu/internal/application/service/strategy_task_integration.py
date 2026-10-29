from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import status
from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_task
from labelu.internal.adapter.persistence import crud_strategy_task_integration
from labelu.internal.adapter.persistence import crud_experiment_dataset_import
from labelu.internal.application.command.datasource import CreateDataSourceCommand
from labelu.internal.application.command.experiment_dataset_import import (
    CreateExperimentDatasetImportCommand,
)
from labelu.internal.application.command.sample import CreateSampleCommand
from labelu.internal.application.command.strategy_task_integration import (
    CreateStrategyTaskIntegrationCommand,
)
from labelu.internal.application.response.strategy_task_integration import (
    StrategyTaskIntegrationResponse,
)
from labelu.internal.application.service import datasource as datasource_service
from labelu.internal.application.service import experiment_dataset_import as experiment_import_service
from labelu.internal.application.service import sample as sample_service
from labelu.internal.application.service import system_labels as system_label_service
from labelu.internal.application.service.annotation_taxonomy import _rect_tool_shell
from labelu.internal.application.service.pre_annotation_label_normalize import (
    normalize_pre_annotation_payload_labels,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.strategy_integration_s3_config import get_active_endpoint
from labelu.internal.common.bootstrap_admin import DEFAULT_ADMIN_USERNAME
from labelu.internal.common.service_account import ensure_open_system_user
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.data_source import DataSource
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.strategy_task_integration import (
    StrategyTaskIntegration,
)
from labelu.internal.domain.models.experiment_dataset_import import ExperimentDatasetImport
from labelu.internal.domain.models.task import MediaType, Task, TaskStatus
from labelu.internal.domain.models.user import User

STRATEGY_METADATA_SOURCE_TYPE = "strategy_metadata"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".gif"}
IMPORT_STATUS_PENDING = "PENDING"
IMPORT_STATUS_PROCESSING = "PROCESSING"
IMPORT_STATUS_COMPLETED = "COMPLETED"
IMPORT_STATUS_FAILED = "FAILED"


def _normalize_label(label: str) -> str:
    text = str(label or "").strip()
    if not text:
        return ""
    if text[0].isupper():
        return text[0].lower() + text[1:]
    return text


def _request_snapshot(cmd: CreateStrategyTaskIntegrationCommand) -> tuple[str, str]:
    payload = json.dumps(
        cmd.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _assert_idempotent_request(
    row: StrategyTaskIntegration,
    *,
    request_payload_json: str,
    request_sha256: str,
) -> None:
    stored_hash = str(row.request_sha256 or "").strip()
    stored_payload = str(row.request_payload_json or "").strip()
    if stored_hash and stored_hash != request_sha256:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="external_task_id 已存在，但本次请求内容与首次请求不一致",
        )
    if stored_payload and stored_payload != request_payload_json:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message="external_task_id 已存在，但本次请求内容与首次请求不一致",
        )
    if not stored_payload:
        stored_labels = json.loads(row.labeling_labels_json or "[]")
        requested_labels = (
            json.loads(request_payload_json).get("labeling", {}).get("labels", [])
        )
        if stored_labels != requested_labels:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="external_task_id 已存在，但标签契约与首次请求不一致",
            )


def _create_or_reload_integration(
    db: Session,
    *,
    row: StrategyTaskIntegration,
    external_task_id: str,
) -> tuple[StrategyTaskIntegration, bool]:
    """Create the integration row, or read the winner of a unique-key race.

    The preflight lookup in ``create`` is only an optimization.  The unique
    constraint remains the authority when two first requests arrive together.
    A losing transaction must roll back before re-reading the committed row;
    otherwise it would surface a transient 500 and the caller would retry the
    same external task indefinitely.
    """
    try:
        with begin_transaction(db):
            return crud_strategy_task_integration.create(db=db, row=row), False
    except IntegrityError:
        raced = crud_strategy_task_integration.get_by_external_task_id(
            db=db, external_task_id=external_task_id, lock=True
        )
        if raced is None:
            raise
        return raced, True


def _normalize_key(value: str, *, trailing_slash: bool = False) -> str:
    text = str(value or "").strip().replace("\\", "/")
    text = text.strip("/")
    if not text:
        return ""
    if trailing_slash:
        return f"{text}/"
    return text


def _join_key(base: str, child: str, *, directory: bool = False) -> str:
    base_norm = _normalize_key(base, trailing_slash=False)
    child_norm = _normalize_key(child, trailing_slash=False)
    if child_norm and base_norm and child_norm.startswith(base_norm):
        resolved = child_norm
    elif base_norm and child_norm:
        resolved = f"{base_norm}/{child_norm}"
    else:
        resolved = child_norm or base_norm
    if directory and resolved and not resolved.endswith("/"):
        resolved = f"{resolved}/"
    return resolved


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    parsed = urlparse(str(s3_uri or "").strip())
    if parsed.scheme.lower() != "s3" or not parsed.netloc:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="dataset.s3_uri must be a valid s3://bucket/prefix URI",
        )
    bucket = parsed.netloc.strip()
    prefix = _normalize_key(
        parsed.path.lstrip("/"), trailing_slash=str(s3_uri).endswith("/")
    )
    return bucket, prefix


def _build_task_config(labels: list[str], labeling_required: bool) -> str | None:
    if not labeling_required or not labels:
        return None
    rect_attrs = [{"key": label, "value": label} for label in labels]
    config = {
        "tools": [{"tool": "rectTool", "config": _rect_tool_shell(rect_attrs)}],
        "commonAttributeConfigurable": True,
    }
    return json.dumps(config, ensure_ascii=False)


def _build_s3_client(ds: DataSource):
    return datasource_service._build_s3_client(ds)


def _find_reusable_datasource(
    db: Session,
    *,
    bucket: str,
    current_user: User,
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


async def _get_or_create_datasource(
    db: Session,
    *,
    bucket: str,
    current_user: User,
) -> DataSource:
    reusable = _find_reusable_datasource(db, bucket=bucket, current_user=current_user)
    if reusable:
        return reusable

    endpoint = get_active_endpoint()
    cmd = CreateDataSourceCommand(
        name=f"strategy-minio:{bucket}",
        type="S3",
        endpoint=endpoint,
        region=None,
        bucket=bucket,
        prefix="",
        access_key_id=settings.STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID,
        secret_access_key=settings.STRATEGY_INTEGRATION_S3_SECRET_ACCESS_KEY,
        path_style=settings.STRATEGY_INTEGRATION_S3_PATH_STYLE,
        use_ssl=settings.STRATEGY_INTEGRATION_S3_USE_SSL,
        presign_expire_secs=settings.S3_PRESIGN_EXPIRE_SECONDS,
    )
    created = await datasource_service.create(db=db, cmd=cmd, current_user=current_user)
    ds = (
        db.query(DataSource)
        .filter(DataSource.id == created.id, DataSource.deleted_at.is_(None))
        .first()
    )
    if not ds:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to create datasource for strategy integration",
        )
    return ds


def _list_image_object_keys(ds: DataSource, prefix: str) -> list[str]:
    client = _build_s3_client(ds)
    normalized_prefix = _normalize_key(prefix, trailing_slash=True)
    keys: list[str] = []
    continuation_token: str | None = None

    while True:
        kwargs: dict[str, Any] = {
            "Bucket": ds.bucket,
            "Prefix": normalized_prefix,
            "MaxKeys": 1000,
        }
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token
        try:
            resp = client.list_objects_v2(**kwargs)
        except Exception as exc:
            logger.opt(exception=exc).error("strategy integration list objects failed")
            raise LabelUException(
                code=ErrorCode.CODE_62002_S3_REQUEST_FAILED,
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        for obj in resp.get("Contents", []):
            key = str(obj.get("Key") or "")
            if not key or key.endswith("/"):
                continue
            suffix = ""
            if "." in key:
                suffix = "." + key.rsplit(".", 1)[-1].lower()
            if suffix not in IMAGE_EXTENSIONS:
                continue
            keys.append(key)

        if not resp.get("IsTruncated"):
            break
        continuation_token = resp.get("NextContinuationToken")

    if not keys:
        raise LabelUException(
            code=ErrorCode.CODE_62001_S3_IMPORT_NO_MATCH,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="No image objects found under dataset.image_prefix",
        )
    return keys


def _read_metadata_json(ds: DataSource, metadata_key: str) -> Any:
    client = _build_s3_client(ds)
    try:
        resp = client.get_object(Bucket=ds.bucket, Key=metadata_key)
    except Exception as exc:
        logger.opt(exception=exc).error("strategy integration get metadata failed")
        raise LabelUException(
            code=ErrorCode.CODE_62002_S3_REQUEST_FAILED,
            status_code=status.HTTP_502_BAD_GATEWAY,
            message="Failed to read metadata.json from S3",
        )
    raw_bytes = resp["Body"].read()
    try:
        return json.loads(raw_bytes.decode("utf-8"))
    except Exception as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"metadata.json is not valid UTF-8 JSON: {exc}",
        )


def _metadata_samples(metadata: Any) -> list[dict[str, Any]]:
    if isinstance(metadata, dict):
        if isinstance(metadata.get("samples"), list):
            return [item for item in metadata.get("samples") if isinstance(item, dict)]
        if isinstance(metadata.get("images"), list):
            return [item for item in metadata.get("images") if isinstance(item, dict)]
        if any(
            key in metadata
            for key in ("image_name", "file_name", "filename", "target_boxes")
        ):
            return [metadata]
    if isinstance(metadata, list):
        return [item for item in metadata if isinstance(item, dict)]
    return []


def _metadata_sample_name(row: dict[str, Any]) -> str | None:
    for key in (
        "image_name",
        "file_name",
        "filename",
        "image",
        "image_path",
        "image_key",
    ):
        value = row.get(key)
        if not value:
            continue
        name = str(value).replace("\\", "/").split("/")[-1].strip()
        if name:
            return name
    return None


def _normalize_box_label(raw_label: Any, allowed_labels: list[str]) -> str | None:
    if raw_label is None or raw_label == "":
        return allowed_labels[0] if len(allowed_labels) == 1 else None
    label = _normalize_label(str(raw_label))
    if not label:
        return allowed_labels[0] if len(allowed_labels) == 1 else None
    if allowed_labels and label not in allowed_labels:
        return None
    return label


def _box_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rect_results_for_sample(
    row: dict[str, Any],
    *,
    allowed_labels: list[str],
    label_aliases: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    boxes = row.get("target_boxes")
    if not isinstance(boxes, list):
        return []
    results: list[dict[str, Any]] = []
    for index, item in enumerate(boxes, start=1):
        if not isinstance(item, dict):
            continue
        x = _box_number(item.get("x"))
        y = _box_number(item.get("y"))
        width = _box_number(item.get("width"))
        height = _box_number(item.get("height"))
        raw_label = _normalize_label(str(item.get("label") or ""))
        label = (label_aliases or {}).get(raw_label) or _normalize_box_label(
            item.get("label"), allowed_labels
        )
        if None in (x, y, width, height) or not label:
            continue
        results.append(
            {
                "id": str(uuid.uuid4()),
                "order": index,
                "label": label,
                "valid": True,
                "visible": True,
                "isVisible": True,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }
        )
    return results


def _preannotation_payload(
    *,
    external_task_id: str,
    sample_name: str,
    labels: list[str],
    rect_results: list[dict[str, Any]],
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
            "source_type": STRATEGY_METADATA_SOURCE_TYPE,
            "external_task_id": external_task_id,
        },
    }
    return normalize_pre_annotation_payload_labels(payload)


def _strategy_preannotation_rows(db: Session, task_id: int) -> list[TaskPreAnnotation]:
    rows = (
        db.query(TaskPreAnnotation)
        .filter(
            TaskPreAnnotation.task_id == task_id,
            TaskPreAnnotation.deleted_at.is_(None),
        )
        .all()
    )
    selected: list[TaskPreAnnotation] = []
    for row in rows:
        try:
            parsed = json.loads(row.data) if isinstance(row.data, str) else {}
        except json.JSONDecodeError:
            continue
        meta = parsed.get("meta") if isinstance(parsed, dict) else {}
        if (
            isinstance(meta, dict)
            and meta.get("source_type") == STRATEGY_METADATA_SOURCE_TYPE
        ):
            selected.append(row)
    return selected


def _count_task_samples(db: Session, task_id: int) -> int:
    return (
        db.query(TaskSample)
        .filter(TaskSample.task_id == task_id, TaskSample.deleted_at.is_(None))
        .count()
    )


def _count_task_pre_annotations(db: Session, task_id: int) -> int:
    return len(_strategy_preannotation_rows(db, task_id))


def _to_response(
    row: StrategyTaskIntegration,
    *,
    created: bool,
) -> StrategyTaskIntegrationResponse:
    return StrategyTaskIntegrationResponse(
        id=row.id,
        external_task_id=row.external_task_id,
        labelu_task_id=row.labelu_task_id,
        datasource_id=row.datasource_id,
        dataset_category_id=row.dataset_category_id,
        dataset_category_key=(
            row.dataset_category.category_key if row.dataset_category else None
        ),
        task_name=row.task_name,
        task_type=row.task_type,
        created=created,
        import_status=row.import_status,
        sample_count=row.imported_sample_count or 0,
        pre_annotation_count=row.imported_pre_annotation_count or 0,
        metadata_consumed=bool(row.metadata_json),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _create_task(
    db: Session,
    *,
    cmd: CreateStrategyTaskIntegrationCommand,
    current_user: User,
    integration_id: int,
    dataset_category_id: int | None,
) -> Task:
    config = _build_task_config(cmd.labeling.labels, cmd.labeling.required)
    task = Task(
        name=cmd.task_name,
        description=f"Strategy integration task: {cmd.external_task_id}",
        tips=None,
        config=config,
        media_type=MediaType.IMAGE.value,
        status=TaskStatus.CONFIGURED.value if config else TaskStatus.DRAFT.value,
        source_type="third_party",
        source_system=cmd.source_system,
        source_external_task_id=cmd.external_task_id,
        source_integration_id=integration_id,
        dataset_category_id=dataset_category_id,
        created_by=current_user.id,
        updated_by=current_user.id,
    )
    with begin_transaction(db):
        db.add(task)
        db.flush()
        db.refresh(task)
    return task


def _update_task_from_cmd(
    db: Session,
    *,
    task: Task,
    cmd: CreateStrategyTaskIntegrationCommand,
    current_user: User,
    integration_id: int,
    dataset_category_id: int | None,
) -> Task:
    config = _build_task_config(cmd.labeling.labels, cmd.labeling.required)
    obj_in = {
        Task.name.key: cmd.task_name,
        Task.description.key: f"Strategy integration task: {cmd.external_task_id}",
        Task.media_type.key: MediaType.IMAGE.value,
        Task.config.key: config,
        Task.source_type.key: "third_party",
        Task.source_system.key: cmd.source_system,
        Task.source_external_task_id.key: cmd.external_task_id,
        Task.source_integration_id.key: integration_id,
        Task.dataset_category_id.key: dataset_category_id,
        Task.updated_by.key: current_user.id,
    }
    if config:
        obj_in[Task.status.key] = TaskStatus.CONFIGURED.value
    with begin_transaction(db):
        task = crud_task.update(db=db, db_obj=task, obj_in=obj_in)
    return task


def _sync_task_attachments(
    db: Session,
    *,
    task_id: int,
    datasource_id: int,
    object_keys: list[str],
    current_user: User,
) -> list[TaskAttachment]:
    existing_rows = (
        db.query(TaskAttachment)
        .filter(
            TaskAttachment.task_id == task_id,
            TaskAttachment.data_source_id == datasource_id,
            TaskAttachment.deleted_at.is_(None),
            TaskAttachment.path.in_(object_keys),
        )
        .all()
    )
    existing_by_path = {row.path: row for row in existing_rows}
    missing = [key for key in object_keys if key not in existing_by_path]
    if missing:
        new_rows = [
            TaskAttachment(
                path=key,
                url="",
                filename=key.split("/")[-1],
                task_id=task_id,
                data_source_id=datasource_id,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            for key in missing
        ]
        with begin_transaction(db):
            for row in new_rows:
                db.add(row)
            db.flush()
            for row in new_rows:
                db.refresh(row)
        for row in new_rows:
            existing_by_path[row.path] = row
    return [existing_by_path[key] for key in object_keys]


async def _sync_task_samples(
    db: Session,
    *,
    task_id: int,
    attachments: list[TaskAttachment],
    current_user: User,
) -> None:
    await sample_service.create(
        db=db,
        task_id=task_id,
        cmd=[CreateSampleCommand(file_id=row.id, data={}) for row in attachments],
        current_user=current_user,
        auto_preannotate=False,
        auto_preannotate_source="strategy_task_integration",
    )


def _upsert_pre_annotations(
    db: Session,
    *,
    task_id: int,
    external_task_id: str,
    labels: list[str],
    metadata: Any,
    current_user: User,
    label_aliases: dict[str, str] | None = None,
) -> int:
    samples = (
        db.query(TaskSample)
        .filter(TaskSample.task_id == task_id, TaskSample.deleted_at.is_(None))
        .all()
    )
    filename_to_sample = {
        sample.file.filename: sample
        for sample in samples
        if getattr(sample, "file", None) and getattr(sample.file, "filename", None)
    }
    existing_by_sample_name = {
        row.sample_name: row for row in _strategy_preannotation_rows(db, task_id)
    }

    created_rows: list[TaskPreAnnotation] = []
    updated_rows: list[tuple[TaskPreAnnotation, str]] = []

    for item in _metadata_samples(metadata):
        sample_name = _metadata_sample_name(item)
        if not sample_name or sample_name not in filename_to_sample:
            continue
        rect_results = _rect_results_for_sample(
            item, allowed_labels=labels, label_aliases=label_aliases
        )
        if not rect_results:
            continue
        payload = _preannotation_payload(
            external_task_id=external_task_id,
            sample_name=sample_name,
            labels=labels,
            rect_results=rect_results,
        )
        payload_json = json.dumps(payload, ensure_ascii=False)
        existing = existing_by_sample_name.get(sample_name)
        if existing:
            updated_rows.append((existing, payload_json))
            continue
        created_rows.append(
            TaskPreAnnotation(
                task_id=task_id,
                file_id=None,
                sample_name=sample_name,
                data=payload_json,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
        )

    if created_rows or updated_rows:
        with begin_transaction(db):
            for row, payload_json in updated_rows:
                row.data = payload_json
                row.updated_by = current_user.id
            for row in created_rows:
                db.add(row)
            db.flush()

    return len(created_rows) + len(updated_rows)


async def _legacy_direct_create(
    db: Session,
    *,
    cmd: CreateStrategyTaskIntegrationCommand,
    current_user: User | None = None,
) -> StrategyTaskIntegrationResponse:
    """Retired synchronous implementation.

    The old implementation used to read S3 and create LabelU tasks in the
    request transaction.  Keeping that callable, even if the router no
    longer references it, leaves an internal bypass around the durable import
    queue, model-id gate, de-duplication and Worker fencing.  Fail closed so
    accidental callers cannot reintroduce the old side effects.
    """
    raise LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_410_GONE,
        message="旧版同步回流链路已停用，请使用统一第三方回流 Worker",
    )


# The legacy strategy endpoint is a compatibility adapter only.  It must not
# read S3 or create LabelU tasks in the request process; those side effects are
# owned by experiment_dataset_import_worker.py.
def _legacy_package_id(cmd: CreateStrategyTaskIntegrationCommand) -> str:
    seed = f"{cmd.source_system.strip()}:{cmd.external_task_id.strip()}"
    return f"legacy:{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:48]}"


def _legacy_window(cmd: CreateStrategyTaskIntegrationCommand) -> tuple[str, str, str]:
    experiment = cmd.experiment if isinstance(cmd.experiment, dict) else {}
    window = experiment.get("data_window")
    if not isinstance(window, dict):
        window = experiment.get("window")
    if not isinstance(window, dict):
        window = {}
    start = str(
        experiment.get("window_start")
        or experiment.get("start_time")
        or window.get("start_time")
        or window.get("start")
        or ""
    ).strip()
    end = str(
        experiment.get("window_end")
        or experiment.get("end_time")
        or window.get("end_time")
        or window.get("end")
        or ""
    ).strip()
    dataset_date = str(
        experiment.get("dataset_date")
        or experiment.get("date")
        or (start[:10] if start else "")
        or "unknown"
    ).strip()
    if not start:
        start = dataset_date
    if not end:
        end = dataset_date
    if dataset_date == "unknown":
        logger.warning(
            "legacy strategy request has no dataset date external_task_id={}",
            cmd.external_task_id,
        )
    return dataset_date, start, end


def _legacy_to_experiment_command(
    cmd: CreateStrategyTaskIntegrationCommand,
) -> CreateExperimentDatasetImportCommand:
    bucket, dataset_prefix = parse_s3_uri(cmd.dataset.s3_uri)
    image_prefix = _join_key(dataset_prefix, cmd.dataset.image_prefix, directory=True)
    metadata_key = _join_key(dataset_prefix, cmd.dataset.metadata_key, directory=False)
    dataset_date, window_start, window_end = _legacy_window(cmd)
    prefix = dataset_prefix or "."
    package_id = _legacy_package_id(cmd)
    target = cmd.target.model_dump(mode="json", by_alias=True)
    return CreateExperimentDatasetImportCommand.model_validate(
        {
            "schema_version": cmd.schema_version,
            "package_id": package_id,
            "source_type": "third_party",
            "source_system": cmd.source_system,
            "source_external_task_id": cmd.external_task_id,
            "dataset_type": cmd.task_type,
            "dataset_date": dataset_date,
            "window_start": window_start,
            "window_end": window_end,
            "dataset": {
                "s3_uri": cmd.dataset.s3_uri,
                "bucket": bucket,
                "prefix": prefix,
                "image_prefix": image_prefix,
                "image_s3_prefix": image_prefix,
                "metadata_key": metadata_key,
                "metadata_s3_uri": f"s3://{bucket}/{metadata_key}",
                "metadata_file": cmd.dataset.metadata_key,
                "image_directory": cmd.dataset.image_prefix,
                "sample_alignment": "legacy_strategy_task",
            },
            "labeling": cmd.labeling.model_dump(mode="json"),
            "target": target,
            "sample_count": 0,
            "target_boxes_required": bool(cmd.labeling.required),
            "dataset_category_key": cmd.dataset_category_key,
            # Let the unified import service derive the stable business key.
            # The external task id is trace metadata only; including it here
            # would split small packages from the same model/event/window when
            # the legacy adapter receives a different third-party task id.
            "aggregation_key": "",
        }
    )


def _legacy_status(status_value: str | None) -> str:
    return {
        "PENDING": IMPORT_STATUS_PENDING,
        "PROCESSING": IMPORT_STATUS_PROCESSING,
        "WAITING_FOR_SAMPLES": "WAITING_FOR_SAMPLES",
        "WAITING_FOR_MODEL": "WAITING_FOR_MODEL",
        "WAITING_FOR_CATEGORY": "WAITING_FOR_CATEGORY",
        "COMPLETED": IMPORT_STATUS_COMPLETED,
        "PARTIAL_COMPLETED": "PARTIAL_COMPLETED",
        "FAILED": IMPORT_STATUS_FAILED,
    }.get(str(status_value or "").upper(), IMPORT_STATUS_PENDING)


def _legacy_response_from_import(
    row: StrategyTaskIntegration,
    import_row: ExperimentDatasetImport | None,
    *,
    created: bool,
    db: Session | None = None,
) -> StrategyTaskIntegrationResponse:
    status_value = _legacy_status(import_row.status if import_row else row.import_status)
    formal_task_ids: list[int] = []
    if import_row:
        try:
            formal_task_ids = [
                int(value)
                for value in json.loads(import_row.formal_task_ids_json or "[]")
                if str(value).isdigit()
            ]
        except (TypeError, ValueError, json.JSONDecodeError):
            formal_task_ids = []
    task_id = row.labelu_task_id or (formal_task_ids[0] if formal_task_ids else None)
    sample_count = (
        int(import_row.formal_sample_count or 0)
        + int(import_row.draft_sample_count or 0)
        if import_row
        else int(row.imported_sample_count or 0)
    )
    preannotation_count = int(row.imported_pre_annotation_count or 0)
    if db is not None and formal_task_ids:
        preannotation_count = int(
            db.query(TaskPreAnnotation)
            .filter(
                TaskPreAnnotation.task_id.in_(formal_task_ids),
                TaskPreAnnotation.deleted_at.is_(None),
            )
            .count()
        )
    package_id = import_row.package_id if import_row else row.source_package_id
    return StrategyTaskIntegrationResponse(
        id=row.id,
        external_task_id=row.external_task_id,
        labelu_task_id=task_id,
        datasource_id=row.datasource_id,
        dataset_category_id=row.dataset_category_id,
        dataset_category_key=(row.dataset_category.category_key if row.dataset_category else None),
        task_name=row.task_name,
        task_type=row.task_type,
        created=created,
        import_status=status_value,
        sample_count=sample_count,
        pre_annotation_count=preannotation_count,
        metadata_consumed=status_value in {IMPORT_STATUS_COMPLETED, "PARTIAL_COMPLETED"},
        package_id=package_id,
        status_url=(f"/internal/v1/experiment-datasets/{package_id}" if package_id else None),
        created_at=row.created_at,
        updated_at=(import_row.updated_at if import_row else row.updated_at),
    )


async def create(
    db: Session,
    *,
    cmd: CreateStrategyTaskIntegrationCommand,
    current_user: User | None = None,
) -> StrategyTaskIntegrationResponse:
    """Accept the legacy request and enqueue the unified import workflow."""
    if current_user is None:
        current_user = ensure_open_system_user(db)
        if current_user is None:
            raise RuntimeError("open@system service account is unavailable")

    request_payload_json, request_sha256 = _request_snapshot(cmd)
    existing = crud_strategy_task_integration.get_by_external_task_id(
        db=db, external_task_id=cmd.external_task_id
    )
    was_existing = bool(existing)
    if existing:
        _assert_idempotent_request(
            existing,
            request_payload_json=request_payload_json,
            request_sha256=request_sha256,
        )
        # Preserve already-imported historical rows.  They predate the unified
        # package table and must remain read-only compatibility records.
        if existing.source_package_id is None and existing.labelu_task_id and existing.import_status == IMPORT_STATUS_COMPLETED:
            return _to_response(existing, created=False)

    translated = _legacy_to_experiment_command(cmd)
    bucket, dataset_prefix = parse_s3_uri(cmd.dataset.s3_uri)
    dataset_category = system_label_service.resolve_dataset_category_key(
        db, cmd.dataset_category_key
    )
    if not existing:
        existing, raced = _create_or_reload_integration(
            db,
            external_task_id=cmd.external_task_id,
            row=StrategyTaskIntegration(
                external_task_id=cmd.external_task_id,
                source_package_id=translated.package_id,
                source_external_package_id=translated.package_id,
                labelu_task_id=None,
                datasource_id=None,
                dataset_category_id=int(dataset_category.id) if dataset_category else None,
                schema_version=cmd.schema_version,
                task_name=cmd.task_name,
                task_type=cmd.task_type,
                source_type="third_party",
                source_system=cmd.source_system,
                dataset_s3_uri=cmd.dataset.s3_uri,
                dataset_image_prefix=_join_key(dataset_prefix, cmd.dataset.image_prefix, directory=True),
                metadata_key=_join_key(dataset_prefix, cmd.dataset.metadata_key),
                bucket=bucket,
                prefix=dataset_prefix,
                labeling_required=cmd.labeling.required,
                labeling_media_type=cmd.labeling.media_type,
                labeling_annotation_type=cmd.labeling.annotation_type,
                labeling_labels_json=json.dumps(cmd.labeling.labels, ensure_ascii=False),
                target_json=json.dumps(cmd.target.model_dump(mode="json"), ensure_ascii=False),
                experiment_json=json.dumps(cmd.experiment, ensure_ascii=False),
                request_payload_json=request_payload_json,
                request_sha256=request_sha256,
                import_status=IMPORT_STATUS_PENDING,
                created_by=current_user.id,
                updated_by=current_user.id,
            ),
        )
        if raced:
            was_existing = True
            _assert_idempotent_request(
                existing,
                request_payload_json=request_payload_json,
                request_sha256=request_sha256,
            )
    else:
        with begin_transaction(db):
            existing.source_package_id = translated.package_id
            existing.source_external_package_id = translated.package_id
            existing.dataset_s3_uri = cmd.dataset.s3_uri
            existing.dataset_image_prefix = _join_key(
                dataset_prefix, cmd.dataset.image_prefix, directory=True
            )
            existing.metadata_key = _join_key(dataset_prefix, cmd.dataset.metadata_key)
            existing.bucket = bucket
            existing.prefix = dataset_prefix
            existing.dataset_category_id = (
                int(dataset_category.id) if dataset_category else existing.dataset_category_id
            )
            existing.request_payload_json = request_payload_json
            existing.request_sha256 = request_sha256
            existing.updated_by = current_user.id
            existing.import_status = IMPORT_STATUS_PENDING
            db.flush()
            db.refresh(existing)
    translated.source_integration_id = int(existing.id)
    import_response = await experiment_import_service.accept(db=db, cmd=translated)
    import_row = crud_experiment_dataset_import.get_by_package_id(
        db=db, package_id=translated.package_id
    )
    if import_row and import_row.source_integration_id != existing.id:
        with begin_transaction(db):
            import_row.source_integration_id = int(existing.id)
            import_row.updated_by = current_user.id
            db.flush()
    with begin_transaction(db):
        existing.import_status = _legacy_status(import_response.status)
        existing.updated_by = current_user.id
        db.flush()
        db.refresh(existing)
    return _legacy_response_from_import(
        existing, import_row, created=not was_existing, db=db
    )
