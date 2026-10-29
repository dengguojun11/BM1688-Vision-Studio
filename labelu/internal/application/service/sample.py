import json
import os
import uuid
import asyncio
from datetime import datetime
from typing import Any, List, Tuple, Union

from pathlib import Path
from loguru import logger
from fastapi import status
from sqlalchemy.orm import Session, joinedload

from labelu.internal.common.business_log import biz_log, log_business_operation, set_request_context
from labelu.internal.common.db import begin_transaction
from labelu.internal.application.service.auto_training_events import (
    enqueue_auto_training_event,
)
from labelu.internal.common.config import settings
from labelu.internal.common.converter import converter
from labelu.internal.common.error_code import ErrorCode
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.storage import (
    build_thumbnail_key,
    get_storage_backend,
)
from labelu.internal.common.task_annotation_completeness import (
    audit_one_sample,
    sample_geometry_ok,
)
from labelu.internal.common.label_image import sample_data_with_only_label
from labelu.internal.common.training_sample import (
    is_explicit_negative_sample,
    rect_annotation_items,
)
from labelu.internal.common.plate_recognition_profile import (
    normalize_plate_recognition_sample_data,
    task_config_matches_profile,
)
from labelu.internal.adapter.persistence import crud_attachment, crud_pre_annotation, crud_task, crud_user
from labelu.internal.adapter.persistence import crud_sample
from labelu.internal.adapter.persistence import crud_export_job
from labelu.internal.adapter.persistence import crud_datasource
from labelu.internal.adapter.persistence import crud_sample_bounds_check_job
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task import TaskStatus
from labelu.internal.domain.models.task import MediaType
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.sample import SampleState
from labelu.internal.application.service import annotation_contract as annotation_contract_service
from labelu.internal.domain.models.sample_edit_log import SampleEditLog
from labelu.internal.domain.models.export_job import ExportStatus
from labelu.internal.domain.models.sample_bounds_check_job import SampleBoundsCheckStatus
from labelu.internal.application.command.sample import ExportType
from labelu.internal.application.command.sample import PatchSampleCommand
from labelu.internal.application.command.sample import CreateSampleCommand
from labelu.internal.application.command.sample import CopySampleToTaskCommand
from labelu.internal.application.command.datasource import ImportS3SamplesCommand
from labelu.internal.application.response.base import UserResp
from labelu.internal.application.response.base import CommonDataResp
from labelu.internal.application.response.sample import CreateSampleResponse
from labelu.internal.application.response.sample import SampleBoundsCheckJobResponse
from labelu.internal.application.response.sample import SampleResponse
from labelu.internal.application.service.attachment import build_attachment_response
from labelu.internal.application.service.attachment import copy_attachment_to_task
from labelu.internal.application.service.task_training_ready import (
    refresh_task_training_ready,
    refresh_task_training_ready_after_sample_change,
)
from labelu.internal.application.service.task_background_jobs import assert_no_active_task_background_job
from labelu.internal.application.service.auto_sample_preannotate import (
    schedule_auto_preannotate_for_new_samples,
)
from labelu.internal.application.service.rect_label_normalization import (
    normalize_sample_api_data_rect_labels,
)
from labelu.internal.application.service.yolo_preannotate import (
    is_yolo_preannotation_data,
    sample_has_yolo_preannotation,
)
from labelu.internal.clients.ws import sampleConnectionManager
from labelu.internal.common.websocket import Message, MessageType
from labelu.internal.common.task_access import assert_user_has_task_access, user_has_task_access, is_task_superuser
from labelu.internal.common.compare_sample_access_token import (
    verify_compare_sample_access_token,
)
from labelu.internal.adapter.ws.sample import TaskSampleWsPayload


EXPERIMENT_DATASET_PREANNOTATION_SOURCE = "experiment_dataset_metadata"
VLX_PREANNOTATION_SOURCE = "vlx_seek_generated"
REX_PREANNOTATION_SOURCE = "rex_omni_generated"
VLM_PREANNOTATION_SOURCES = {VLX_PREANNOTATION_SOURCE, REX_PREANNOTATION_SOURCE}


def _parse_task_config(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _validate_explicit_negative_sample_data(data: Any, state: Any) -> None:
    if not is_explicit_negative_sample(data):
        return
    state_value = getattr(state, "value", state)
    if state_value in {SampleState.NEW.value, SampleState.SKIPPED.value}:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="负样本必须提交为 DONE，不能同时标记为未开始或跳过。",
        )
    result_raw = data.get("result") if isinstance(data, dict) else None
    try:
        result = json.loads(result_raw) if isinstance(result_raw, str) else result_raw
    except json.JSONDecodeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="负样本标注结果不是有效 JSON。",
        ) from exc
    if not isinstance(result, dict):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="负样本必须包含可解析的空框标注结果。",
        )
    if rect_annotation_items(result):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="负样本不能包含矩形框；请先取消负样本标记或删除全部检测框。",
        )


def _sample_api_data_with_file_metadata(data: Any, file, *, for_annotation: bool = False) -> Any:
    """标注页拉取预标注时用 ``sample.data.file.filename`` 与 ``task_pre_annotation.sample_name`` 对齐。

    仅含 ``file_id``、``data`` 为空或不带 ``file`` 的样本在库中很常见；YOLO 等写入的 ``sample_name`` 则来自附件
    ``filename``。在接口返回中补全 ``data.file``，避免预标注已写入但画布不显示。
    """
    if file is None:
        return data
    att = build_attachment_response(file, for_annotation=for_annotation)
    if att is None:
        return data
    file_meta = att.model_dump() if hasattr(att, "model_dump") else att.dict()
    if not isinstance(data, dict):
        return {"file": file_meta}
    existing_file = data.get("file")
    base_file = existing_file if isinstance(existing_file, dict) else {}
    return {**data, "file": {**base_file, **file_meta}}


def is_sample_pre_annotated(
    db: Session, task_id: Union[int, None], sample_name: str | None = None
) -> bool:
    """样本是否有关联「上传的预标注文件」记录（``file_id`` 非空；不含仅 AI/YOLO 写入的行）。"""
    if sample_name is None or task_id is None:
        return False

    q = (
        db.query(TaskPreAnnotation.id)
        .filter(
            TaskPreAnnotation.task_id == task_id,
            TaskPreAnnotation.sample_name == sample_name,
            TaskPreAnnotation.deleted_at == None,
            TaskPreAnnotation.file_id.isnot(None),
        )
        .limit(1)
    )
    return q.first() is not None


def _is_experiment_dataset_preannotation_data(raw: Any) -> bool:
    try:
        if not raw:
            return False
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(parsed, dict):
            return False
        return (parsed.get("meta") or {}).get("source_type") == EXPERIMENT_DATASET_PREANNOTATION_SOURCE
    except Exception:
        return False


def sample_has_experiment_dataset_preannotation(
    db: Session, task_id: Union[int, None], sample_name: str | None
) -> bool:
    if not sample_name or task_id is None:
        return False
    rows, _ = crud_pre_annotation.list_by(
        db=db, task_id=task_id, sample_name=sample_name, page=0, size=100
    )
    return any(_is_experiment_dataset_preannotation_data(r.data) for r in rows)


def _is_vlx_preannotation_data(raw: Any) -> bool:
    try:
        if not raw:
            return False
        parsed = json.loads(raw) if isinstance(raw, str) else raw
        return (parsed.get("meta") or {}).get("source_type") in VLM_PREANNOTATION_SOURCES
    except Exception:
        return False


def sample_has_vlx_preannotation(
    db: Session, task_id: Union[int, None], sample_name: str | None
) -> bool:
    if not sample_name or task_id is None:
        return False
    rows, _ = crud_pre_annotation.list_by(
        db=db, task_id=task_id, sample_name=sample_name, page=0, size=100
    )
    return any(_is_vlx_preannotation_data(r.data) for r in rows)


def _sample_preannotation_flags(
    db: Session, task_id: int, sample_name: str | None
) -> tuple[bool, bool, bool, bool]:
    """Load all annotation flags with one indexed query for the annotation page."""
    if not sample_name:
        return False, False, False, False

    names = [sample_name]
    if len(sample_name) > 9:
        names.append(sample_name[9:])
    rows = (
        db.query(TaskPreAnnotation.sample_name, TaskPreAnnotation.data, TaskPreAnnotation.file_id)
        .filter(
            TaskPreAnnotation.task_id == task_id,
            TaskPreAnnotation.deleted_at == None,
            TaskPreAnnotation.sample_name.in_(tuple(set(names))),
        )
        .all()
    )
    has_uploaded = any(row.file_id is not None for row in rows)
    has_yolo = any(is_yolo_preannotation_data(row.data) for row in rows)
    has_experiment = any(_is_experiment_dataset_preannotation_data(row.data) for row in rows)
    has_vlx = any(_is_vlx_preannotation_data(row.data) for row in rows)
    return has_uploaded, has_yolo, has_experiment, has_vlx


def _batch_pre_annotation_and_yolo_flags(
    db: Session,
    task_id: Union[int, None],
    filenames: List[str | None],
) -> List[tuple[bool, bool, bool, bool]]:
    """与逐条 ``is_sample_pre_annotated`` + ``sample_has_yolo_preannotation`` 同语义，列表页一次查库。"""
    n = len(filenames)
    if n == 0:
        return []

    if task_id is None:
        return [
            (
                is_sample_pre_annotated(db, task_id, fn),
                sample_has_yolo_preannotation(db, task_id, fn),
                sample_has_experiment_dataset_preannotation(db, task_id, fn),
                sample_has_vlx_preannotation(db, task_id, fn),
            )
            for fn in filenames
        ]

    expanded: set[str] = set()
    for fn in filenames:
        if not fn:
            continue
        expanded.add(fn)
        if len(fn) > 9:
            expanded.add(fn[9:])

    if not expanded:
        return [(False, False, False, False)] * n

    rows = (
        db.query(
            TaskPreAnnotation.sample_name,
            TaskPreAnnotation.data,
            TaskPreAnnotation.file_id,
        )
        .filter(
            TaskPreAnnotation.task_id == task_id,
            TaskPreAnnotation.deleted_at == None,
            TaskPreAnnotation.sample_name.in_(tuple(expanded)),
        )
        .all()
    )

    names_with_any: set[str] = {r[0] for r in rows if r[0] and r[2] is not None}
    names_with_yolo: set[str] = {
        r[0] for r in rows if r[0] and is_yolo_preannotation_data(r[1])
    }
    names_with_experiment_dataset: set[str] = {
        r[0] for r in rows if r[0] and _is_experiment_dataset_preannotation_data(r[1])
    }
    names_with_vlx: set[str] = {
        r[0] for r in rows if r[0] and _is_vlx_preannotation_data(r[1])
    }

    def pair_for(fn: str | None) -> tuple[bool, bool, bool, bool]:
        if not fn:
            return False, False, False, False
        any_pre = fn in names_with_any or (len(fn) > 9 and fn[9:] in names_with_any)
        yolo_pre = fn in names_with_yolo or (len(fn) > 9 and fn[9:] in names_with_yolo)
        experiment_pre = fn in names_with_experiment_dataset or (
            len(fn) > 9 and fn[9:] in names_with_experiment_dataset
        )
        vlx_pre = fn in names_with_vlx or (len(fn) > 9 and fn[9:] in names_with_vlx)
        return any_pre, yolo_pre, experiment_pre, vlx_pre

    return [pair_for(fn) for fn in filenames]


async def create(
    db: Session,
    task_id: int,
    cmd: List[CreateSampleCommand],
    current_user: User,
    *,
    auto_preannotate: bool = True,
    auto_preannotate_source: str = "sample_create",
    apply_image_pipeline: bool = True,
) -> CreateSampleResponse:
    if not cmd:
        return CreateSampleResponse(ids=[])

    file_ids = [sample.file_id for sample in cmd]
    existing_by_file_id = crud_sample.map_file_ids_to_sample_ids(
        db=db, task_id=task_id, file_ids=file_ids
    )
    new_cmd = [sample for sample in cmd if sample.file_id not in existing_by_file_id]

    if new_cmd and apply_image_pipeline and settings.IMAGE_PIPELINE_ENABLED:
        pipeline_limit = max(int(settings.IMAGE_PIPELINE_MAX_IMPORT_BATCH or 0), 0)
        if pipeline_limit and len(new_cmd) > pipeline_limit:
            logger.info(
                "image_pipeline: skip large import batch task_id={} new_count={} limit={}",
                task_id,
                len(new_cmd),
                pipeline_limit,
            )
        else:
            from labelu.internal.common.image_pipeline.runner import on_samples_batch_create

            new_cmd = await on_samples_batch_create(db, task_id, list(new_cmd))

    if new_cmd:
        obj_in = {}
        with begin_transaction(db):
            task = crud_task.get(db=db, task_id=task_id, lock=True)
            if not task:
                task = crud_task.get(db=db, task_id=task_id, lock=False)
            if not task:
                logger.error("cannot find task:{}", task_id)
                raise LabelUException(
                    code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                    status_code=status.HTTP_404_NOT_FOUND,
                )

            samples = [
                TaskSample(
                    inner_id=task.last_sample_inner_id + i + 1,
                    task_id=task_id,
                    file_id=sample.file_id,
                    created_by=current_user.id,
                    updated_by=current_user.id,
                    data=json.dumps(sample.data, ensure_ascii=False),
                )
                for i, sample in enumerate(new_cmd)
            ]
            obj_in[Task.last_sample_inner_id.key] = task.last_sample_inner_id + len(new_cmd)
            if task.status == TaskStatus.DRAFT.value:
                obj_in[Task.status.key] = TaskStatus.IMPORTED
            crud_task.update(db=db, db_obj=task, obj_in=obj_in)
            new_samples = crud_sample.batch(db=db, samples=samples)
            for row in new_samples:
                if row.file_id is not None:
                    existing_by_file_id[int(row.file_id)] = int(row.id)
    else:
        task = crud_task.get(db=db, task_id=task_id)
        if not task:
            logger.error("cannot find task:{}", task_id)
            raise LabelUException(
                code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )

    ids = []
    for sample in cmd:
        sample_id = existing_by_file_id.get(sample.file_id)
        if sample_id is None:
            logger.error(
                "create samples missing id for file_id task_id={} file_id={}",
                task_id,
                sample.file_id,
            )
            raise LabelUException(
                code=ErrorCode.CODE_50000_TASK_ERROR,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        ids.append(sample_id)
    if auto_preannotate and new_cmd:
        auto_preannotate_sample_ids = [
            existing_by_file_id[sample.file_id]
            for sample in new_cmd
            if sample.file_id in existing_by_file_id
        ]
        if auto_preannotate_sample_ids:
            schedule_auto_preannotate_for_new_samples(
                task_id=task_id,
                sample_ids=auto_preannotate_sample_ids,
                user_id=current_user.id,
                source_hint=auto_preannotate_source,
            )
    return CreateSampleResponse(ids=ids)


async def copy_from_sample(
    db: Session,
    task_id: int,
    cmd: CopySampleToTaskCommand,
    current_user: User,
) -> CreateSampleResponse:
    source_task = crud_task.get(db=db, task_id=cmd.source_task_id)
    if not source_task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    target_task = crud_task.get(db=db, task_id=task_id)
    if not target_task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    assert_user_has_task_access(source_task, current_user)
    assert_user_has_task_access(target_task, current_user)

    source_sample = crud_sample.get(db=db, sample_id=cmd.source_sample_id)
    if not source_sample or source_sample.task_id != cmd.source_task_id:
        raise LabelUException(
            code=ErrorCode.CODE_55001_SAMPLE_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    source_attachment = source_sample.file
    if not source_attachment and source_sample.file_id:
        source_attachment = crud_attachment.get(db=db, attachment_id=source_sample.file_id)
    if not source_attachment:
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    copied_attachment = await copy_attachment_to_task(
        db=db,
        source_attachment=source_attachment,
        target_task_id=task_id,
        current_user=current_user,
    )
    return await create(
        db=db,
        task_id=task_id,
        cmd=[CreateSampleCommand(file_id=copied_attachment.id, data={})],
        current_user=current_user,
        auto_preannotate=False,
        auto_preannotate_source="copy_from_sample",
    )


async def copy_by_labels(
    db: Session,
    task_id: int,
    label_ids: list[int],
    current_user: User,
) -> CreateSampleResponse:
    target_task = crud_task.get(db=db, task_id=task_id)
    if not target_task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(target_task, current_user)

    normalized_label_ids = list(dict.fromkeys(int(value) for value in label_ids if int(value) > 0))
    labels = (
        db.query(AnnotationLabel)
        .filter(
            AnnotationLabel.id.in_(normalized_label_ids),
            AnnotationLabel.deleted_at.is_(None),
        )
        .all()
    )
    if len({int(row.id) for row in labels}) != len(normalized_label_ids):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="标签不存在",
        )

    source_task_ids = [
        int(row.id)
        for row in crud_task.visible_query(
            db=db,
            owner_id=current_user.id,
            list_all_tasks=is_task_superuser(current_user),
        )
        .filter(Task.media_type == MediaType.IMAGE.value, Task.id != int(task_id))
        .all()
    ]
    if not source_task_ids:
        return CreateSampleResponse(ids=[])

    label_values = {
        str(value).strip()
        for label in labels
        for value in (label.label_value, label.label_key)
        if value
    }
    source_samples = (
        db.query(TaskSample)
        .options(joinedload(TaskSample.file))
        .filter(TaskSample.task_id.in_(source_task_ids), TaskSample.deleted_at.is_(None))
        .order_by(TaskSample.id.asc())
        .all()
    )
    copied_ids: list[int] = []
    for source_sample in source_samples:
        filtered_data = sample_data_with_only_label(source_sample.data, label_values)
        if not filtered_data or not source_sample.file:
            continue
        copied_attachment = await copy_attachment_to_task(
            db=db,
            source_attachment=source_sample.file,
            target_task_id=task_id,
            current_user=current_user,
        )
        created = await create(
            db=db,
            task_id=task_id,
            cmd=[CreateSampleCommand(file_id=copied_attachment.id, data=filtered_data)],
            current_user=current_user,
            auto_preannotate=False,
            auto_preannotate_source="copy_by_label",
            apply_image_pipeline=False,
        )
        copied_ids.extend(created.ids or [])
    return CreateSampleResponse(ids=copied_ids)


LABEL_DATASET_IMPORT_TYPE = "LABEL_DATASET_IMPORT"


def _label_copy_candidates(
    db: Session,
    *,
    task_id: int,
    label_ids: list[int],
    current_user: User,
) -> tuple[list[int], set[str]]:
    """Return source sample ids and normalized label values for an import job."""
    target_task = crud_task.get(db=db, task_id=task_id)
    if not target_task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(target_task, current_user)
    normalized_ids = list(dict.fromkeys(int(value) for value in label_ids if int(value) > 0))
    labels = (
        db.query(AnnotationLabel)
        .filter(AnnotationLabel.id.in_(normalized_ids), AnnotationLabel.deleted_at.is_(None))
        .all()
    )
    if len({int(row.id) for row in labels}) != len(normalized_ids):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="label does not exist",
        )
    source_tasks = (
        crud_task.visible_query(
            db=db,
            owner_id=current_user.id,
            list_all_tasks=is_task_superuser(current_user),
        )
        .filter(Task.media_type == MediaType.IMAGE.value, Task.id != int(task_id))
        .all()
    )
    source_task_ids = [int(row.id) for row in source_tasks]
    if not source_task_ids:
        return [], set()
    label_values = {
        str(value).strip()
        for label in labels
        for value in (label.label_value, label.label_key)
        if value
    }
    source_samples = (
        db.query(TaskSample)
        .filter(TaskSample.task_id.in_(source_task_ids), TaskSample.deleted_at.is_(None))
        .order_by(TaskSample.id.asc())
        .all()
    )
    candidate_ids = [
        int(sample.id)
        for sample in source_samples
        if sample_data_with_only_label(sample.data, label_values)
    ]
    return candidate_ids, label_values


async def create_label_dataset_import_job(
    db: Session,
    *,
    task_id: int,
    label_ids: list[int],
    current_user: User,
) -> int:
    """Create a persisted label import job and return immediately."""
    # Validate access and label ids only. The potentially large source-sample scan
    # is deliberately deferred to the worker so the save request returns quickly.
    target_task = crud_task.get(db=db, task_id=task_id)
    if not target_task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(target_task, current_user)
    normalized_ids = list(dict.fromkeys(int(value) for value in label_ids if int(value) > 0))
    labels = (
        db.query(AnnotationLabel)
        .filter(AnnotationLabel.id.in_(normalized_ids), AnnotationLabel.deleted_at.is_(None))
        .all()
    )
    if len({int(row.id) for row in labels}) != len(normalized_ids):
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="label does not exist",
        )
    with begin_transaction(db):
        job = crud_export_job.create(
            db=db,
            task_id=task_id,
            user_id=current_user.id,
            export_type=LABEL_DATASET_IMPORT_TYPE,
            sample_ids=[],
        )
        job.sample_ids = json.dumps(
            {"source_sample_ids": [], "label_ids": normalized_ids},
            ensure_ascii=False,
        )
        db.flush()
        job_id = int(job.id)
    asyncio.get_event_loop().run_in_executor(
        None, _run_label_dataset_import_sync, job_id, int(task_id), int(current_user.id)
    )
    return job_id


def _run_label_dataset_import_sync(job_id: int, task_id: int, user_id: int) -> None:
    """Copy selected source samples in a worker thread and persist progress."""
    from labelu.internal.common.db import session_scope

    async def _run() -> None:
        try:
            with session_scope() as db:
                job = crud_export_job.get(db=db, job_id=job_id)
                user = crud_user.get(db=db, id=user_id)
                if not job or not user:
                    return
                payload = json.loads(job.sample_ids or "{}")
                label_ids = [int(value) for value in payload.get("label_ids", [])]
                source_ids, label_values = _label_copy_candidates(
                    db, task_id=task_id, label_ids=label_ids, current_user=user
                )
                with begin_transaction(db):
                    job.sample_count = len(source_ids)
                    job.sample_ids = json.dumps(
                        {"source_sample_ids": source_ids, "label_ids": label_ids},
                        ensure_ascii=False,
                    )
                    crud_export_job.update_status(db, job, ExportStatus.PROCESSING.value)
            for source_id in source_ids:
                success = False
                try:
                    with session_scope() as db:
                        job = crud_export_job.get(db=db, job_id=job_id)
                        if not job or job.status in {ExportStatus.COMPLETED.value, ExportStatus.FAILED.value}:
                            return
                        source_sample = (
                            db.query(TaskSample)
                            .options(joinedload(TaskSample.file))
                            .filter(TaskSample.id == source_id, TaskSample.deleted_at.is_(None))
                            .first()
                        )
                        current_user = crud_user.get(db=db, id=user_id)
                        if not current_user:
                            raise LabelUException(
                                code=ErrorCode.CODE_40002_USER_NOT_FOUND,
                                status_code=status.HTTP_404_NOT_FOUND,
                            )
                        filtered_data = (
                            sample_data_with_only_label(source_sample.data, label_values)
                            if source_sample
                            else None
                        )
                        if source_sample and source_sample.file and filtered_data:
                            copied_attachment = await copy_attachment_to_task(
                                db=db,
                                source_attachment=source_sample.file,
                                target_task_id=task_id,
                                current_user=current_user,
                            )
                            await create(
                                db=db,
                                task_id=task_id,
                                cmd=[CreateSampleCommand(file_id=copied_attachment.id, data=filtered_data)],
                                current_user=current_user,
                                auto_preannotate=False,
                                auto_preannotate_source="copy_by_label_job",
                                apply_image_pipeline=False,
                            )
                            success = True
                        with begin_transaction(db):
                            job = crud_export_job.get(db=db, job_id=job_id)
                            if job:
                                crud_export_job.increment_progress(db, job, success=success)
                except Exception as exc:
                    logger.exception("label dataset import item failed job_id={} source_id={}", job_id, source_id)
                    with session_scope() as db:
                        job = crud_export_job.get(db=db, job_id=job_id)
                        if job:
                            with begin_transaction(db):
                                crud_export_job.increment_progress(db, job, success=False)
            with session_scope() as db:
                job = crud_export_job.get(db=db, job_id=job_id)
                if job:
                    with begin_transaction(db):
                        crud_export_job.update_status(db, job, ExportStatus.COMPLETED.value)
        except Exception as exc:
            logger.exception("label dataset import failed job_id={}", job_id)
            with session_scope() as db:
                job = crud_export_job.get(db=db, job_id=job_id)
                if job:
                    with begin_transaction(db):
                        crud_export_job.update_status(
                            db, job, ExportStatus.FAILED.value, error_message=str(exc)
                        )

    asyncio.run(_run())


def get_label_dataset_import_job(db: Session, *, task_id: int, job_id: int, current_user: User):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
    assert_user_has_task_access(task, current_user)
    job = crud_export_job.get(db=db, job_id=job_id)
    if not job or int(job.task_id) != int(task_id) or job.export_type != LABEL_DATASET_IMPORT_TYPE:
        raise LabelUException(code=ErrorCode.CODE_61000_NO_DATA, status_code=status.HTTP_404_NOT_FOUND)
    return job


def get_active_label_dataset_import_job(db: Session, *, task_id: int, current_user: User):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
    assert_user_has_task_access(task, current_user)
    return crud_export_job.get_active_label_dataset_import(db=db, task_id=task_id)


async def copy_by_label(
    db: Session,
    task_id: int,
    label_id: int,
    current_user: User,
) -> CreateSampleResponse:
    return await copy_by_labels(
        db=db,
        task_id=task_id,
        label_ids=[label_id],
        current_user=current_user,
    )


MAX_IMPORT_KEYS = 10000


def _collect_s3_keys(ds, prefix: str, extension: str | None) -> list[str]:
    """List all matching S3 object keys under *prefix*, paginating internally."""
    from labelu.internal.application.service.datasource import _build_s3_client

    client = _build_s3_client(ds)
    full_prefix = prefix if prefix else (ds.prefix or "")

    allowed_exts = None
    if extension:
        allowed_exts = {
            ("." + e.strip().lower().lstrip("."))
            for e in extension.split(",")
            if e.strip()
        }

    keys: list[str] = []
    continuation_token = None

    while True:
        kwargs = {"Bucket": ds.bucket, "Prefix": full_prefix, "MaxKeys": 1000}
        if continuation_token:
            kwargs["ContinuationToken"] = continuation_token

        try:
            resp = client.list_objects_v2(**kwargs)
        except Exception as exc:
            logger.opt(exception=exc).error(
                "S3 list_objects_v2 failed for datasource {}", ds.id
            )
            raise LabelUException(
                code=ErrorCode.CODE_62002_S3_REQUEST_FAILED,
                status_code=status.HTTP_502_BAD_GATEWAY,
            )

        for obj in resp.get("Contents", []):
            key: str = obj["Key"]
            if key.endswith("/"):
                continue
            if allowed_exts:
                ext = ("." + key.rsplit(".", 1)[-1].lower()) if "." in key else ""
                if ext not in allowed_exts:
                    continue
            keys.append(key)
            if len(keys) > MAX_IMPORT_KEYS:
                raise LabelUException(
                    code=ErrorCode.CODE_62000_S3_IMPORT_TOO_MANY,
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        if not resp.get("IsTruncated"):
            break
        continuation_token = resp.get("NextContinuationToken")

    return keys


async def import_from_s3(
    db: Session, task_id: int, cmd: ImportS3SamplesCommand, current_user: User,
) -> CreateSampleResponse:
    """Import S3 objects as task samples (no file copy — stores reference only)."""
    from labelu.internal.domain.models.attachment import TaskAttachment
    from labelu.internal.application.service.datasource import _build_s3_client

    with begin_transaction(db):
        task = crud_task.get(db=db, task_id=task_id, lock=True)
        if not task:
            raise LabelUException(
                code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )

        ds = crud_datasource.get(db=db, ds_id=cmd.data_source_id)
        if not ds:
            raise LabelUException(
                code=ErrorCode.CODE_61000_NO_DATA,
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Resolve object keys: either from explicit list or by listing S3 prefix
        object_keys = cmd.object_keys
        if not object_keys and cmd.prefix is not None:
            object_keys = _collect_s3_keys(ds, cmd.prefix, cmd.extension)

        if not object_keys:
            raise LabelUException(
                code=ErrorCode.CODE_62001_S3_IMPORT_NO_MATCH,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        attachments = []
        for key in object_keys:
            filename = key.rsplit("/", 1)[-1] if "/" in key else key
            att = TaskAttachment(
                path=key,
                url="",
                filename=filename,
                task_id=task_id,
                data_source_id=ds.id,
                created_by=current_user.id,
                updated_by=current_user.id,
            )
            db.add(att)
            attachments.append(att)
        db.flush()

        samples = [
            TaskSample(
                inner_id=task.last_sample_inner_id + i + 1,
                task_id=task_id,
                file_id=att.id,
                created_by=current_user.id,
                updated_by=current_user.id,
                data=json.dumps({}),
            )
            for i, att in enumerate(attachments)
        ]
        obj_in = {Task.last_sample_inner_id.key: task.last_sample_inner_id + len(samples)}
        if task.status == TaskStatus.DRAFT.value:
            obj_in[Task.status.key] = TaskStatus.IMPORTED
        crud_task.update(db=db, db_obj=task, obj_in=obj_in)
        new_samples = crud_sample.batch(db=db, samples=samples)

    sample_ids = [s.id for s in new_samples]
    if sample_ids:
        schedule_auto_preannotate_for_new_samples(
            task_id=task_id,
            sample_ids=sample_ids,
            user_id=current_user.id,
            source_hint="import_from_s3",
        )
    return CreateSampleResponse(ids=sample_ids)


def list_by(
    db: Session,
    task_id: Union[int, None],
    after: Union[int, None],
    before: Union[int, None],
    page: Union[int, None],
    size: int,
    sorting: Union[str, None],
    state: Union[str, None] = None,
    search: Union[str, None] = None,
) -> Tuple[List[SampleResponse], int]:
    samples = crud_sample.list_by(
        db=db,
        task_id=task_id,
        after=after,
        before=before,
        page=page,
        size=size,
        sorting=sorting,
        state=state,
        search=search,
    )

    total = crud_sample.count(db=db, task_id=task_id, state=state, search=search)

    filenames = [s.file.filename if s.file else None for s in samples]
    pre_yolo_flags = _batch_pre_annotation_and_yolo_flags(db, task_id, filenames)

    # response
    return [
        SampleResponse(
            id=sample.id,
            inner_id=sample.inner_id,
            state=sample.state,
            data=_sample_api_data_with_file_metadata(
                json.loads(sample.data), sample.file
            ),
            annotated_count=sample.annotated_count,
            is_pre_annotated=pre_yolo_flags[i][0],
            is_yolo_pre_annotated=pre_yolo_flags[i][1],
            is_experiment_dataset_pre_annotated=pre_yolo_flags[i][2],
            is_vlx_pre_annotated=pre_yolo_flags[i][3],
            file=build_attachment_response(sample.file),
            created_at=sample.created_at,
            created_by=UserResp(
                id=sample.owner.id,
                username=sample.owner.username,
            ),
            updated_at=sample.updated_at,
            updaters=[UserResp(
                id=updater.id,
                username=updater.username,
            ) for updater in sample.updaters],
        )
        for i, sample in enumerate(samples)
    ], total


def get(
    db: Session,
    task_id: int,
    sample_id: int,
    current_user: User,
    compare_access_token: str | None = None,
) -> SampleResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    sample = crud_sample.get(
        db=db,
        sample_id=sample_id,
    )

    if not sample:
        logger.error("cannot find sample:{}", sample_id)
        raise LabelUException(
            code=ErrorCode.CODE_55001_SAMPLE_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if int(sample.task_id) != int(task_id):
        logger.error("sample task mismatch sample_id={} task_id={}", sample_id, task_id)
        raise LabelUException(
            code=ErrorCode.CODE_55001_SAMPLE_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    has_normal_access = (not task.created_by) or user_has_task_access(current_user, task)
    has_compare_access = bool(compare_access_token) and verify_compare_sample_access_token(
        compare_access_token or "",
        task_id=task_id,
        sample_id=sample_id,
    )
    if not has_normal_access and not has_compare_access:
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    preannotation_flags = _sample_preannotation_flags(
        db, task_id, sample.file.filename if sample.file else None
    )

    # response
    return SampleResponse(
        id=sample.id,
        inner_id=sample.inner_id,
        state=sample.state,
        data=_sample_api_data_with_file_metadata(json.loads(sample.data), sample.file, for_annotation=True),
        is_pre_annotated=preannotation_flags[0],
        is_yolo_pre_annotated=preannotation_flags[1],
        is_vlx_pre_annotated=preannotation_flags[3],
        is_experiment_dataset_pre_annotated=preannotation_flags[2],
        file=build_attachment_response(sample.file, for_annotation=True),
        annotated_count=sample.annotated_count,
        created_at=sample.created_at,
        created_by=UserResp(
            id=sample.owner.id,
            username=sample.owner.username,
        ),
        updated_at=sample.updated_at,
        updaters=[UserResp(
            id=updater.id,
            username=updater.username,
        ) for updater in sample.updaters],
    )


@log_business_operation("sample.patch")
async def patch(
    db: Session,
    task_id: int,
    sample_id: int,
    cmd: PatchSampleCommand,
    current_user: User,
    compare_access_token: str | None = None,
) -> SampleResponse:

    # check task exist
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # get sample
    sample = crud_sample.get(db=db, sample_id=sample_id)
    if not sample:
        logger.error("cannot find sample:{}", sample_id)
        raise LabelUException(
            code=ErrorCode.CODE_55001_SAMPLE_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if int(sample.task_id) != int(task_id):
        logger.error("sample task mismatch sample_id={} task_id={}", sample_id, task_id)
        raise LabelUException(
            code=ErrorCode.CODE_55001_SAMPLE_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    has_normal_access = (not task.created_by) or user_has_task_access(current_user, task)
    has_compare_access = bool(compare_access_token) and verify_compare_sample_access_token(
        compare_access_token or "",
        task_id=task_id,
        sample_id=sample_id,
    )
    if not has_normal_access and not has_compare_access:
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    biz_log(
        "sample.patch.apply",
        task_id=task_id,
        sample_id=sample_id,
        state=cmd.state,
        annotated_count=cmd.annotated_count,
        previous_state=sample.state,
    )

    normalized_data = cmd.data
    task_contract = annotation_contract_service.current_contract(db, task_id)
    if task_contract is not None:
        if cmd.expected_task_contract_revision is None:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="标注请求缺少任务标签契约版本，请刷新标注页面后再保存",
            )
        if int(task_contract.revision) != int(cmd.expected_task_contract_revision):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="任务标签契约已变化，请刷新标注页面后再保存",
            )
    task_config = _parse_task_config(task.config)
    if isinstance(cmd.data, dict):
        if task_config_matches_profile(task_config):
            normalized_data, _plate_stats = normalize_plate_recognition_sample_data(
                normalized_data
            )
        normalized_data, _replaced, unknown, allowed_labels = (
            normalize_sample_api_data_rect_labels(
                normalized_data,
                task_config=task_config,
            )
        )
        if unknown:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=(
                    f"样本标注含任务未配置的拉框类别 {unknown}；"
                    f"任务类别为 {allowed_labels}"
                ),
            )
    _validate_explicit_negative_sample_data(normalized_data, cmd.state)
    is_negative_sample = is_explicit_negative_sample(normalized_data)
    sample_name = sample.file.filename if sample.file is not None else None

    # update
    sample_obj_in = {}
    if cmd.state == SampleState.SKIPPED.value:
        sample_obj_in[TaskSample.state.key] = SampleState.SKIPPED.value
    elif cmd.state == SampleState.NEW.value:
        sample_obj_in[TaskSample.data.key] = json.dumps(normalized_data, ensure_ascii=False)
        sample_obj_in[TaskSample.annotated_count.key] = cmd.annotated_count
        sample_obj_in[TaskSample.state.key] = SampleState.NEW.value
    else:  # can be None, or DONE
        sample_obj_in[TaskSample.data.key] = json.dumps(normalized_data, ensure_ascii=False)
        sample_obj_in[TaskSample.annotated_count.key] = cmd.annotated_count
        sample_obj_in[TaskSample.state.key] = SampleState.DONE.value
    has_actual_changes = _sample_patch_has_actual_changes(sample, sample_obj_in)
    previous_sample_audit = (
        audit_one_sample(sample, task_config) if has_actual_changes else None
    )
    sample_obj_in[TaskSample.updated_by.key] = current_user.id

    previous_state = str(sample.state or "")
    previous_annotated_count = int(sample.annotated_count or 0)
    previous_data = _normalize_annotation_payload(sample.data)
    next_state = str(sample_obj_in.get(TaskSample.state.key) or previous_state)
    next_annotated_count = int(
        sample_obj_in.get(TaskSample.annotated_count.key, previous_annotated_count)
        or 0
    )
    next_data = _normalize_annotation_payload(
        sample_obj_in.get(TaskSample.data.key, sample.data)
    )

    with begin_transaction(db):
        if cmd.expected_updated_at is not None:
            locked_sample = crud_sample.get_for_update(db=db, sample_id=sample_id)
            if locked_sample is None or locked_sample.updated_at != cmd.expected_updated_at:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=status.HTTP_409_CONFLICT,
                    message="样本已被其他用户更新，请刷新后再保存，避免覆盖最新标注",
                )
            sample = locked_sample

        # update task status
        if task.status != TaskStatus.FINISHED.value:
            statics = crud_sample.statics(
                db=db, task_ids=[task_id]
            )
            task_obj_in = {Task.status.key: TaskStatus.INPROGRESS.value}
            new_sample_cnt = statics.get(f"{task.id}_{SampleState.NEW.value}", 0)
            if new_sample_cnt == 0 or (
                new_sample_cnt == 1 and sample.state == SampleState.NEW.value
            ):
                task_obj_in[Task.status.key] = TaskStatus.FINISHED.value
            if task.status != task_obj_in[Task.status.key]:
                crud_task.update(db=db, db_obj=task, obj_in=task_obj_in)
        if has_actual_changes:
            # updaters
            if current_user not in sample.updaters:
                sample.updaters.append(current_user)
            # update task sample result
            updated_sample = crud_sample.update(
                db=db, db_obj=sample, obj_in=sample_obj_in
            )
            db.add(
                SampleEditLog(
                    task_id=task_id,
                    sample_id=sample_id,
                    user_id=current_user.id,
                    action="annotation_edit",
                    previous_state=previous_state,
                    new_state=next_state,
                    previous_annotated_count=previous_annotated_count,
                    new_annotated_count=next_annotated_count,
                    previous_data=previous_data,
                    new_data=next_data,
                )
            )
            db.flush()
        else:
            updated_sample = sample
        if is_negative_sample and sample_name:
            stale_pre_annotations = crud_pre_annotation.list_by_task_id_and_sample_name(
                db=db,
                task_id=task_id,
                sample_name=sample_name,
            )
            stale_pre_annotation_ids = [row.id for row in stale_pre_annotations]
            if stale_pre_annotation_ids:
                crud_pre_annotation.delete(
                    db=db,
                    pre_annotation_ids=stale_pre_annotation_ids,
                )
        if task_contract is not None:
            sample_scope = annotation_contract_service.upsert_sample_scope(
                db,
                sample=updated_sample,
                contract=task_contract,
                annotation_data=normalized_data,
                state=str(sample_obj_in.get(TaskSample.state.key) or sample.state or SampleState.NEW.value),
                reviewed_by=current_user.id,
                explicit_negative=is_explicit_negative_sample(normalized_data),
            )
            if has_actual_changes and settings.AUTO_TRAINING_PLAN_WRITES_ENABLED:
                enqueue_auto_training_event(
                    db,
                    event_name="sample.annotation.changed.v1",
                    aggregate_type="task_sample",
                    aggregate_id=str(updated_sample.id),
                    payload={
                        "task_id": int(task_id),
                        "sample_id": int(updated_sample.id),
                        "state": str(updated_sample.state or ""),
                        "annotation_sha256": sample_scope.annotation_sha256,
                    },
                    idempotency_key=(
                        f"sample:{updated_sample.id}:annotation:"
                        f"{sample_scope.annotation_sha256}:{updated_sample.state}"
                    ),
                )

    refreshed_task = crud_task.get(db=db, task_id=task_id)
    if has_actual_changes:
        refresh_task_training_ready_after_sample_change(
            db,
            refreshed_task,
            previous_audit=previous_sample_audit or {},
            current_audit=audit_one_sample(updated_sample, task_config),
            previous_state=previous_state,
            current_state=str(updated_sample.state or ""),
        )
    elif refreshed_task.training_ready is None or not refreshed_task.annotation_audit_json:
        refresh_task_training_ready(db, refreshed_task)
    
    # tell other clients in the same sample page to refresh data
    await sampleConnectionManager.send_message(
        client_id=f"task_{task_id}",
        message=Message(
            type=MessageType.UPDATE,
            data=TaskSampleWsPayload(
                task_id=task_id,
                user_id=current_user.id,
                username=current_user.username,
                sample_id=sample_id,
            )
        )
    )

    # response
    return SampleResponse(
        id=updated_sample.id,
        inner_id=updated_sample.inner_id,
        state=updated_sample.state,
        data=_sample_api_data_with_file_metadata(
            json.loads(updated_sample.data), updated_sample.file, for_annotation=True
        ),
        is_pre_annotated=is_sample_pre_annotated(
            db=db, task_id=task_id, sample_name=updated_sample.file.filename if updated_sample.file else None
        ),
        is_yolo_pre_annotated=sample_has_yolo_preannotation(
            db=db, task_id=task_id, sample_name=updated_sample.file.filename if updated_sample.file else None
        ),
        is_vlx_pre_annotated=sample_has_vlx_preannotation(
            db=db, task_id=task_id, sample_name=updated_sample.file.filename if updated_sample.file else None
        ),
        is_experiment_dataset_pre_annotated=sample_has_experiment_dataset_preannotation(
            db=db, task_id=task_id, sample_name=updated_sample.file.filename if updated_sample.file else None
        ),
        file=build_attachment_response(updated_sample.file, for_annotation=True),
        annotated_count=updated_sample.annotated_count,
        created_at=updated_sample.created_at,
        created_by=UserResp(
            id=updated_sample.owner.id,
            username=updated_sample.owner.username,
        ),
        updated_at=updated_sample.updated_at,
        updaters=[UserResp(
            id=updater.id,
            username=updater.username,
        ) for updater in updated_sample.updaters],
    )


async def delete(
    db: Session, task_id: int, sample_ids: List[int], current_user: User
) -> CommonDataResp:
    storage = get_storage_backend()

    with begin_transaction(db):
        task = crud_task.get(db=db, task_id=task_id)
        if not task:
            logger.error("cannot find task:{}", task_id)
            raise LabelUException(
                code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if not user_has_task_access(current_user, task):
            raise LabelUException(
                code=ErrorCode.CODE_30001_NO_PERMISSION,
                status_code=status.HTTP_403_FORBIDDEN,
            )

        # delete media
        samples = crud_sample.get_by_ids(db=db, sample_ids=sample_ids, task_id=task_id)
        attachment_ids = [sample.file_id for sample in samples if sample.file_id]
        attachments = crud_attachment.get_by_ids(db=db, attachment_ids=attachment_ids)

        for attachment in attachments:
            storage.delete(attachment.path)
            thumbnail_key = build_thumbnail_key(attachment.path)
            if storage.exists(thumbnail_key):
                storage.delete(thumbnail_key)

        crud_sample.delete(db=db, sample_ids=[sample.id for sample in samples])
    # response
    return CommonDataResp(ok=True)


BOUNDS_EPSILON = 1e-6


def _json_loads_maybe(raw: Any) -> Any:
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        return json.loads(raw)
    return raw


def _normalize_annotation_payload(raw: Any) -> str:
    parsed = _json_loads_maybe(raw)
    if parsed is None:
        return ""
    if isinstance(parsed, str):
        return parsed
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sample_patch_has_actual_changes(sample: TaskSample, sample_obj_in: dict[str, Any]) -> bool:
    current_state = str(sample.state or "")
    next_state = str(sample_obj_in.get(TaskSample.state.key) or current_state)
    if current_state != next_state:
        return True

    current_count = int(sample.annotated_count or 0)
    next_count = int(sample_obj_in.get(TaskSample.annotated_count.key, current_count) or 0)
    if current_count != next_count:
        return True

    current_data = _normalize_annotation_payload(sample.data)
    next_data = _normalize_annotation_payload(sample_obj_in.get(TaskSample.data.key, sample.data))
    return current_data != next_data


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _tool_items(result_data: dict[str, Any], *tool_names: str) -> list[Any]:
    items: list[Any] = []
    for tool_name in tool_names:
        block = result_data.get(tool_name)
        if isinstance(block, dict):
            block_items = block.get("result")
        else:
            block_items = block
        if isinstance(block_items, list):
            items.extend(block_items)
    return items


def _point_xy(point: Any) -> tuple[float, float] | None:
    if isinstance(point, dict):
        x = _number(point.get("x"))
        y = _number(point.get("y"))
    elif isinstance(point, (list, tuple)) and len(point) >= 2:
        x = _number(point[0])
        y = _number(point[1])
    else:
        return None
    if x is None or y is None:
        return None
    return x, y


def _point_out_of_bounds(x: float, y: float, width: float, height: float) -> bool:
    return (
        x < -BOUNDS_EPSILON
        or y < -BOUNDS_EPSILON
        or x > width + BOUNDS_EPSILON
        or y > height + BOUNDS_EPSILON
    )


def _extract_result_dimensions(
    sample: TaskSample,
    sample_data: dict[str, Any],
    result_data: dict[str, Any],
) -> tuple[float | None, float | None]:
    width = _number(result_data.get("width"))
    height = _number(result_data.get("height"))
    if width and height:
        return width, height

    file_data = sample_data.get("file") if isinstance(sample_data.get("file"), dict) else {}
    width = _number(file_data.get("original_width") or file_data.get("width"))
    height = _number(file_data.get("original_height") or file_data.get("height"))
    if width and height:
        return width, height

    attachment = sample.file
    if not attachment or not getattr(attachment, "path", None):
        return None, None

    storage = get_storage_backend()
    if storage.is_remote:
        return None, None

    try:
        from PIL import Image, ImageOps

        local_path = storage.get_local_path(attachment.path)
        if not local_path or not local_path.is_file():
            return None, None
        with Image.open(local_path) as image:
            image = ImageOps.exif_transpose(image)
            image_width, image_height = image.size
            return float(image_width), float(image_height)
    except Exception:
        logger.warning("failed to read sample image size, sample_id={}", sample.id)
        return None, None


def check_sample_annotations_out_of_bounds(sample: TaskSample) -> tuple[bool, str | None]:
    """Return (is_abnormal, error_message). Kept for old callers/tests."""
    try:
        sample_data = _json_loads_maybe(sample.data) or {}
        if not isinstance(sample_data, dict):
            return False, "sample data is not an object"
        result_data = _json_loads_maybe(sample_data.get("result")) or {}
        if not result_data:
            return False, None
        if not isinstance(result_data, dict):
            return False, "annotation result is not an object"
    except Exception as exc:
        return False, f"invalid annotation JSON: {exc}"
    ok, reason = sample_geometry_ok(sample, result_data, sample_data)
    if ok:
        return False, None
    # Missing dimensions and malformed annotations cannot be classified as an
    # out-of-bounds defect; keep the diagnostic so callers can retry/repair it.
    if reason and reason.endswith("out of bounds"):
        return True, None
    return False, reason


def _bounds_check_job_response(job) -> SampleBoundsCheckJobResponse:
    return SampleBoundsCheckJobResponse(
        id=job.id,
        task_id=job.task_id,
        status=job.status,
        sample_count=job.sample_count or 0,
        processed_count=job.processed_count or 0,
        normal_count=job.normal_count or 0,
        incomplete_count=job.incomplete_count or 0,
        abnormal_count=job.abnormal_count or 0,
        failed_count=job.failed_count or 0,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@log_business_operation("sample.bounds_check_job")
async def create_bounds_check_job(
    db: Session, task_id: int, current_user: User
) -> SampleBoundsCheckJobResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)
    if task.media_type != MediaType.IMAGE.value:
        raise LabelUException(
            code=ErrorCode.CODE_56008_SAMPLE_BOUNDS_CHECK_UNSUPPORTED_MEDIA,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    sample_count = sum(
        1
        for sample in crud_sample.list_by_task(db=db, task_id=task_id)
        if sample.state != SampleState.NEW.value
    )
    with begin_transaction(db):
        assert_no_active_task_background_job(db=db, task_id=task_id)
        job = crud_sample_bounds_check_job.create(
            db=db,
            task_id=task_id,
            user_id=current_user.id,
            sample_count=sample_count,
        )
        response = _bounds_check_job_response(job)

    biz_log(
        "sample.bounds_check_job.enqueue",
        job_id=response.id,
        task_id=task_id,
        sample_count=sample_count,
    )
    asyncio.get_event_loop().run_in_executor(
        None, _run_bounds_check_sync, response.id, task_id, current_user.id
    )
    return response


def get_bounds_check_job(
    db: Session, task_id: int, job_id: int, current_user: User
) -> SampleBoundsCheckJobResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)

    job = crud_sample_bounds_check_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_56009_SAMPLE_BOUNDS_CHECK_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _bounds_check_job_response(job)


def get_active_bounds_check_job(
    db: Session, task_id: int, current_user: User
) -> SampleBoundsCheckJobResponse | None:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)

    job = crud_sample_bounds_check_job.get_active_by_task_id(db=db, task_id=task_id)
    if not job:
        return None
    return _bounds_check_job_response(job)


def _run_bounds_check_sync(job_id: int, task_id: int, user_id: int) -> None:
    from labelu.internal.common.db import session_scope

    set_request_context(request_id=f"bounds-check-{job_id}", user_id=user_id)
    biz_log("sample.bounds_check_job.run", job_id=job_id, task_id=task_id)
    try:
        with session_scope() as db:
            job = crud_sample_bounds_check_job.get(db=db, job_id=job_id)
            if not job:
                return
            with begin_transaction(db):
                crud_sample_bounds_check_job.update_status(
                    db, job, SampleBoundsCheckStatus.PROCESSING.value
                )

            samples = crud_sample.list_by_task(db=db, task_id=task_id)
            task = crud_task.get(db=db, task_id=task_id)
            task_config = {}
            if task and task.config:
                try:
                    parsed_config = json.loads(task.config)
                    task_config = parsed_config if isinstance(parsed_config, dict) else {}
                except json.JSONDecodeError:
                    task_config = {}
            for sample in samples:
                if sample.state == SampleState.NEW.value:
                    continue
                err = None
                try:
                    audit = audit_one_sample(sample, task_config)
                except Exception as exc:
                    audit = {}
                    err = str(exc)
                if err:
                    biz_log(
                        "sample.bounds_check_job.sample_failed",
                        job_id=job_id,
                        task_id=task_id,
                        sample_id=sample.id,
                        error=err,
                    )
                with begin_transaction(db):
                    job = crud_sample_bounds_check_job.get(db=db, job_id=job_id)
                    if err:
                        crud_sample_bounds_check_job.update_progress(db, job, failed_delta=1)
                        continue
                    quality_state = audit.get("sample_quality_state")
                    fresh_sample = crud_sample.get(db=db, sample_id=sample.id)
                    if quality_state == SampleState.ABNORMAL.value:
                        if fresh_sample and fresh_sample.state != SampleState.SKIPPED.value:
                            crud_sample.update(
                                db=db,
                                db_obj=fresh_sample,
                                obj_in={
                                    TaskSample.state.key: SampleState.ABNORMAL.value,
                                    TaskSample.updated_by.key: user_id,
                                },
                            )
                        crud_sample_bounds_check_job.update_progress(db, job, abnormal_delta=1)
                    elif quality_state == SampleState.INCOMPLETE.value:
                        if fresh_sample and fresh_sample.state != SampleState.SKIPPED.value:
                            crud_sample.update(
                                db=db,
                                db_obj=fresh_sample,
                                obj_in={
                                    TaskSample.state.key: SampleState.INCOMPLETE.value,
                                    TaskSample.updated_by.key: user_id,
                                },
                            )
                        crud_sample_bounds_check_job.update_progress(db, job, incomplete_delta=1)
                    else:
                        if fresh_sample and fresh_sample.state in (
                            SampleState.ABNORMAL.value,
                            SampleState.INCOMPLETE.value,
                        ):
                            crud_sample.update(
                                db=db,
                                db_obj=fresh_sample,
                                obj_in={
                                    TaskSample.state.key: SampleState.DONE.value,
                                    TaskSample.updated_by.key: user_id,
                                },
                            )
                        crud_sample_bounds_check_job.update_progress(db, job, normal_delta=1)

            with begin_transaction(db):
                job = crud_sample_bounds_check_job.get(db=db, job_id=job_id)
                crud_sample_bounds_check_job.update_status(db, job, SampleBoundsCheckStatus.COMPLETED.value)
                task = crud_task.get(db=db, task_id=task_id)
            if task:
                refresh_task_training_ready(db, task)
            biz_log("sample.bounds_check_job.done", job_id=job_id, task_id=task_id)
    except Exception as exc:
        biz_log("sample.bounds_check_job.failed", job_id=job_id, task_id=task_id, error=str(exc))
        logger.error("Sample bounds check job {} failed: {}", job_id, str(exc))
        try:
            with session_scope() as db:
                job = crud_sample_bounds_check_job.get(db=db, job_id=job_id)
                if job:
                    with begin_transaction(db):
                        crud_sample_bounds_check_job.update_status(
                            db,
                            job,
                            SampleBoundsCheckStatus.FAILED.value,
                            error_message=str(exc),
                        )
        except Exception:
            logger.error("Failed to update sample bounds check job status for job {}", job_id)


@log_business_operation("sample.export_job")
async def create_export_job(
    db: Session,
    task_id: int,
    export_type: ExportType,
    sample_ids: List[int],
    current_user: User,
) -> int:
    """Create an export job and start background processing. Returns job_id immediately."""

    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    with begin_transaction(db):
        job = crud_export_job.create(
            db=db,
            task_id=task_id,
            user_id=current_user.id,
            export_type=export_type.value,
            sample_ids=sample_ids,
        )
        job_id = job.id

    biz_log(
        "sample.export_job.enqueue",
        job_id=job_id,
        task_id=task_id,
        export_type=export_type.value,
        sample_count=len(sample_ids),
    )

    # Run blocking export work in a thread pool to avoid blocking the event loop
    asyncio.get_event_loop().run_in_executor(
        None, _run_export_sync, job_id, task_id, export_type, sample_ids
    )
    return job_id


def _run_export_sync(job_id: int, task_id: int, export_type: ExportType, sample_ids: List[int]):
    """Run export in a thread. All operations here are synchronous and blocking."""
    from labelu.internal.common.db import session_scope

    set_request_context(request_id=f"export-{job_id}", user_id=None)
    biz_log(
        "sample.export_job.run",
        job_id=job_id,
        task_id=task_id,
        export_type=export_type.value,
        sample_count=len(sample_ids),
    )
    try:
        with session_scope() as db:
            job = crud_export_job.get(db=db, job_id=job_id)
            with begin_transaction(db):
                crud_export_job.update_status(db, job, ExportStatus.PROCESSING.value)

            task = crud_task.get(db=db, task_id=task_id)
            samples = crud_sample.get_by_ids(db=db, sample_ids=sample_ids)

            data = []
            for sample in samples:
                file_dict = {}
                if sample.file:
                    file_dict = {
                        "id": sample.file.id,
                        "filename": sample.file.filename,
                        "url": sample.file.url,
                        "path": sample.file.path if hasattr(sample.file, "path") else "",
                    }
                data.append({
                    "id": sample.id,
                    "inner_id": sample.inner_id,
                    "state": sample.state,
                    "data": sample.data,
                    "annotated_count": sample.annotated_count,
                    "file": file_dict,
                })

            task_config = json.loads(task.config)
            out_data_dir = Path(settings.MEDIA_ROOT).joinpath(
                settings.EXPORT_DIR,
                f"task-{task_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[0:8]}",
            )

        file_full_path = converter.convert(
            config=task_config,
            input_data=data,
            out_data_dir=out_data_dir,
            out_data_file_name_prefix=task_id,
            format=export_type.value,
        )

        storage = get_storage_backend()
        if storage.is_remote:
            local_export_path = Path(file_full_path)
            export_key = f"{settings.EXPORT_DIR}/{local_export_path.name}"
            storage.save_file(local_export_path, export_key)
            local_export_path.unlink(missing_ok=True)
            stored_path = export_key
        else:
            stored_path = str(file_full_path)

        with session_scope() as db:
            job = crud_export_job.get(db=db, job_id=job_id)
            if job:
                with begin_transaction(db):
                    crud_export_job.update_status(
                        db,
                        job,
                        ExportStatus.COMPLETED.value,
                        file_path=stored_path,
                        processed_count=len(data),
                    )
                biz_log(
                    "sample.export_job.done",
                    job_id=job_id,
                    task_id=task_id,
                    file_path=stored_path,
                    sample_count=len(data),
                )
    except Exception as e:
        biz_log("sample.export_job.failed", job_id=job_id, task_id=task_id, error=str(e))
        logger.error("Export job {} failed: {}", job_id, str(e))
        try:
            with session_scope() as db:
                job = crud_export_job.get(db=db, job_id=job_id)
                if job:
                    with begin_transaction(db):
                        crud_export_job.update_status(
                            db,
                            job,
                            ExportStatus.FAILED.value,
                            error_message=str(e),
                        )
        except Exception:
            logger.error("Failed to update export job status for job {}", job_id)


async def export(
    db: Session,
    task_id: int,
    export_type: ExportType,
    sample_ids: List[int],
    current_user: User,
) -> str:
    """Legacy synchronous export. Kept for backward compatibility."""

    task = crud_task.get(db=db, task_id=task_id)
    samples = crud_sample.get_by_ids(db=db, sample_ids=sample_ids)
    data = []
    for sample in samples:
        file_dict = {}
        if sample.file:
            file_dict = {
                "id": sample.file.id,
                "filename": sample.file.filename,
                "url": sample.file.url,
                "path": sample.file.path if hasattr(sample.file, "path") else "",
            }
        data.append({
            "id": sample.id,
            "inner_id": sample.inner_id,
            "state": sample.state,
            "data": sample.data,
            "annotated_count": sample.annotated_count,
            "file": file_dict,
        })

    # output data path
    out_data_dir = Path(settings.MEDIA_ROOT).joinpath(
        settings.EXPORT_DIR,
        f"task-{task_id}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[0:8]}",
    )

    # converter to export_type
    file_full_path = converter.convert(
        config=json.loads(task.config),
        input_data=data,
        out_data_dir=out_data_dir,
        out_data_file_name_prefix=task_id,
        format=export_type.value,
    )

    # response
    return file_full_path
