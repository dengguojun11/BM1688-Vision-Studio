import json
import os
from copy import copy
from typing import List, Tuple, Optional

from loguru import logger
from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.common.db import begin_transaction
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.storage import (
    build_attachment_api_path,
    build_partial_api_path,
    build_thumbnail_key,
    get_storage_backend,
)
from labelu.internal.adapter.persistence import crud_task
from labelu.internal.adapter.persistence import crud_pre_annotation
from labelu.internal.adapter.persistence import crud_attachment
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.application.command.pre_annotation import CreatePreAnnotationCommand
from labelu.internal.application.response.base import UserResp
from labelu.internal.application.response.base import CommonDataResp
from labelu.internal.application.response.pre_annotation import CreatePreAnnotationResponse, PreAnnotationFileResponse
from labelu.internal.application.response.pre_annotation import PreAnnotationResponse
from labelu.internal.application.service.attachment import build_attachment_response
from labelu.internal.common.tag_tool_frontend_payload import expand_pre_annotation_api_data
from labelu.internal.common.task_access import (
    assert_user_has_task_access,
    assert_user_is_task_owner_or_superuser,
    is_task_superuser,
)
from labelu.internal.application.service.pre_annotation_label_normalize import (
    normalize_pre_annotation_payload_labels,
)
from labelu.internal.application.service.rect_label_normalization import (
    normalize_uploaded_pre_annotation_rect_labels,
)

GENERATED_PRE_ANNOTATION_SOURCE_TYPES = {
    "ai_generated",
    "yolo_generated",
    "tagtool_cls_generated",
    "locateanything_generated",
    "vlx_seek_generated",
    "rex_omni_generated",
}


def _parse_pre_annotation_data(data: Optional[str]) -> dict:
    if not data:
        return {}

    try:
        parsed = json.loads(data)
    except (TypeError, json.JSONDecodeError):
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _pre_annotation_has_geometry(data: Optional[str]) -> bool:
    parsed = _parse_pre_annotation_data(data)
    annotations = parsed.get("annotations")
    if not isinstance(annotations, dict):
        return False

    for tool_name, tool_payload in annotations.items():
        if tool_name in {"tagTool", "textTool"}:
            continue
        if isinstance(tool_payload, dict):
            result = tool_payload.get("result")
            if isinstance(result, list) and len(result) > 0:
                return True
        elif isinstance(tool_payload, list) and len(tool_payload) > 0:
            return True
    return False


def _is_canvas_overwrite_candidate(data: Optional[str]) -> bool:
    meta = _parse_pre_annotation_data(data).get("meta") or {}
    return (
        meta.get("source_type") in GENERATED_PRE_ANNOTATION_SOURCE_TYPES
        and meta.get("apply_to_canvas_overwrite") is True
    )


def _tool_result_items(payload: object) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("result"), list):
        return payload["result"]
    return []


def _merge_canvas_global_annotations(
    selected: TaskPreAnnotation,
    pre_annotations: List[TaskPreAnnotation],
) -> TaskPreAnnotation:
    parsed = _parse_pre_annotation_data(selected.data)
    meta = parsed.get("meta") or {}
    if meta.get("preserve_global_annotations") is not True:
        return selected

    annotations = parsed.get("annotations")
    if not isinstance(annotations, dict):
        annotations = {}
    merged_annotations = dict(annotations)
    merged_config = dict(parsed.get("config") or {})
    changed = False

    for tool_name in ("tagTool", "textTool"):
        base_payload = merged_annotations.get(tool_name)
        merged_items = list(_tool_result_items(base_payload))
        seen = {
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
            for item in merged_items
        }
        payload_template = base_payload if isinstance(base_payload, dict) else None

        for row in pre_annotations:
            if row is selected:
                continue
            source = _parse_pre_annotation_data(row.data)
            source_annotations = source.get("annotations") or {}
            source_payload = (
                source_annotations.get(tool_name)
                if isinstance(source_annotations, dict)
                else None
            )
            source_items = _tool_result_items(source_payload)
            if not source_items:
                continue
            if payload_template is None and isinstance(source_payload, dict):
                payload_template = source_payload
            for item in source_items:
                signature = json.dumps(
                    item, ensure_ascii=False, sort_keys=True, default=str
                )
                if signature not in seen:
                    seen.add(signature)
                    merged_items.append(item)
                    changed = True
            source_config = source.get("config") or {}
            if tool_name not in merged_config and isinstance(source_config, dict):
                if tool_name in source_config:
                    merged_config[tool_name] = source_config[tool_name]

        if merged_items:
            merged_annotations[tool_name] = {
                **(payload_template or {}),
                "toolName": tool_name,
                "result": merged_items,
            }

    if not changed:
        return selected

    merged = copy(selected)
    parsed["annotations"] = merged_annotations
    if merged_config:
        parsed["config"] = merged_config
    merged.data = json.dumps(parsed, ensure_ascii=False)
    return merged


def _parse_task_config(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def select_canvas_pre_annotation(
    pre_annotations: List[TaskPreAnnotation],
) -> List[TaskPreAnnotation]:
    if not pre_annotations:
        return []

    canvas_overrides = [
        item for item in pre_annotations if _is_canvas_overwrite_candidate(item.data)
    ]
    if canvas_overrides:
        return [
            _merge_canvas_global_annotations(canvas_overrides[-1], pre_annotations)
        ]

    uploaded = [item for item in pre_annotations if item.file_id is not None]
    if uploaded:
        return [uploaded[0]]

    generated = [
        item
        for item in pre_annotations
        if _parse_pre_annotation_data(item.data).get("meta", {}).get("source_type")
        in GENERATED_PRE_ANNOTATION_SOURCE_TYPES
    ]
    generated_with_geometry = [item for item in generated if _pre_annotation_has_geometry(item.data)]
    if generated_with_geometry:
        return [generated_with_geometry[-1]]

    any_with_geometry = [item for item in pre_annotations if _pre_annotation_has_geometry(item.data)]
    if any_with_geometry:
        return [any_with_geometry[-1]]

    if generated:
        return [generated[-1]]

    return [pre_annotations[-1]]


def read_pre_annotation_file(attachment: TaskAttachment) -> List[dict]:
    if attachment is None:
        return []

    storage = get_storage_backend()
    
    # check if the file exists
    if not storage.exists(attachment.path) or (not attachment.filename.endswith('.jsonl') and not attachment.filename.endswith('.json')):
        return []

    try:
        if attachment.filename.endswith('.jsonl'):
            data = storage.read_text(attachment.path, encoding="utf-8-sig").splitlines()
            return [json.loads(line) for line in data if line.strip()]
        else:
            parsed_data = json.loads(
                storage.read_text(attachment.path, encoding="utf-8-sig")
            )
            return [{**item, "result": json.loads(item["result"])} for item in parsed_data]
    
    except FileNotFoundError:
        raise LabelUException(status_code=404, code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND)
    except LabelUException:
        raise
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError, ValueError) as exc:
        # A malformed upload is a client error, never a 500.
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=(
                "预标注文件格式不正确："
                "JSON/JSONL 需为 LabelU 导出格式（JSON 数组项包含 fileName 与 result 字符串）"
                f"（{str(exc)[:160]}）"
            ),
        ) from exc

async def create(
    db: Session, task_id: int, cmd: List[CreatePreAnnotationCommand], current_user: User
) -> CreatePreAnnotationResponse:
    with begin_transaction(db):
        # check task exist
        task = crud_task.get(db=db, task_id=task_id, lock=True)
        if not task:
            logger.error("cannot find task:{}", task_id)
            raise LabelUException(
                code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        # Pre-annotations are task data.  Apply the same access policy as the
        # sample/attachment APIs before reading or writing any rows.
        assert_user_has_task_access(task, current_user)
        task_config = _parse_task_config(task.config)
        
        pre_annotations = []
        for pre_annotation in cmd:
            pre_annotation_file = crud_attachment.get(db, pre_annotation.file_id)
            if (
                not pre_annotation_file
                or pre_annotation_file.task_id != task_id
            ):
                raise LabelUException(
                    code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            pre_annotation_contents = read_pre_annotation_file(pre_annotation_file)
            
            for _item in pre_annotation_contents:
                sample_name = _item.get("sample_name") if pre_annotation_file.filename.endswith(".jsonl") else _item.get("fileName")
                exist_pre_annotations = crud_pre_annotation.list_by_task_id_and_owner_id_and_sample_name(
                    db=db, task_id=task_id, sample_name=sample_name
                )
                # 重新上传时覆盖同样本名的「文件上传」预标注；AI/YOLO 行（无 file_id）保留
                upload_row_ids = [
                    p.id for p in exist_pre_annotations if p.file_id is not None
                ]
                if upload_row_ids:
                    crud_pre_annotation.delete(db, upload_row_ids)

                payload = dict(_item) if isinstance(_item, dict) else _item
                if isinstance(payload, dict):
                    payload = normalize_pre_annotation_payload_labels(payload)
                    payload, _replaced, unknown, allowed_labels = (
                        normalize_uploaded_pre_annotation_rect_labels(
                            payload,
                            task_config=task_config,
                        )
                    )
                    if unknown:
                        raise LabelUException(
                            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                            status_code=status.HTTP_400_BAD_REQUEST,
                            message=(
                                f"预标注文件样本 {sample_name} 含任务未配置的拉框类别 {unknown}；"
                                f"任务类别为 {allowed_labels}"
                            ),
                        )
                    meta = dict(payload.get("meta") or {})
                    meta.setdefault("source_type", "file_upload")
                    payload["meta"] = meta
                pre_annotations.append(
                    TaskPreAnnotation(
                        task_id=task_id,
                        file_id=pre_annotation.file_id,
                        sample_name=sample_name,
                        data=json.dumps(payload, ensure_ascii=False),
                        created_by=current_user.id,
                        updated_by=current_user.id,
                    )
                )
        
        new_pre_annotations = crud_pre_annotation.batch(db=db, pre_annotations=pre_annotations)

    # response
    ids = [s.id for s in new_pre_annotations]
    return CreatePreAnnotationResponse(ids=ids)


async def list_by(
    db: Session,
    task_id: Optional[int],
    sample_name: Optional[str],
    after: Optional[int],
    before: Optional[int],
    page: Optional[int],
    size: int,
    sorting: Optional[str],
    canvas_only: bool,
    current_user: User,
    search: Optional[str] = None,
) -> Tuple[List[PreAnnotationResponse], int]:
    if task_id is not None:
        task = crud_task.get(db=db, task_id=task_id)
        if not task:
            raise LabelUException(
                code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        assert_user_has_task_access(task, current_user)

    if canvas_only and task_id is not None and sample_name:
        # A sample can have independent generated rows: the vision model owns
        # global tagTool values while the detection model owns geometry.  Use
        # the same selector as the paged path so the chosen geometry row is
        # enriched with global values before it reaches the canvas.
        candidates, _ = crud_pre_annotation.list_by(
            db=db,
            task_id=task_id,
            sample_name=sample_name,
            search=search,
            page=0,
            size=200,
        )
        pre_annotations = select_canvas_pre_annotation(candidates)
        total = len(pre_annotations)
    else:
        pre_annotations, total = crud_pre_annotation.list_by(
            db=db,
            task_id=task_id,
            after=after,
            before=before,
            page=page,
            sample_name=sample_name,
            search=search,
            size=size,
            sorting=sorting,
        )

        if canvas_only:
            pre_annotations = select_canvas_pre_annotation(pre_annotations)
            total = len(pre_annotations)
    
    return [
        PreAnnotationResponse(
            id=pre_annotation.id,
            data=expand_pre_annotation_api_data(pre_annotation.data),
            file=build_attachment_response(pre_annotation.file),
            created_at=pre_annotation.created_at,
            created_by=UserResp(
                id=pre_annotation.owner.id,
                username=pre_annotation.owner.username,
            ),
            updated_at=pre_annotation.updated_at,
            updated_by=UserResp(
                id=pre_annotation.updater.id,
                username=pre_annotation.updater.username,
            ),
        ) for pre_annotation in pre_annotations
    ], total

async def list_pre_annotation_files(
    db: Session,
    task_id: Optional[int],
    after: Optional[int],
    before: Optional[int],
    page: Optional[int],
    size: int,
    sorting: Optional[str],
    current_user: User,
    search: Optional[str] = None,
) -> Tuple[List[TaskAttachment], int]:
    try:
        if task_id is not None:
            task = crud_task.get(db=db, task_id=task_id)
            if task:
                assert_user_has_task_access(task, current_user)
        # Superusers manage the whole task, including files registered by another
        # uploader. Keep the owner filter for ordinary users so their uploads
        # remain isolated as before.
        if is_task_superuser(current_user):
            pre_annotations = crud_pre_annotation.list_by_task_id(db=db, task_id=task_id)
        else:
            pre_annotations = crud_pre_annotation.list_by_task_id_and_owner_id_lightweight(
                db=db, task_id=task_id, owner_id=current_user.id
            )
        file_ids = [pre_annotation['file_id'] for pre_annotation in pre_annotations]
        
        attachments, total = crud_attachment.list_by(
            db=db,
            ids=file_ids,
            after=after,
            before=before,
            page=page,
            size=size,
            sorting=sorting,
            search=search,
        )
        _attachment_ids = [attachment.id for attachment in attachments]
        sample_names_those_has_pre_annotations = []
        
        for pre_annotation in pre_annotations:
            if pre_annotation['file_id'] in _attachment_ids and pre_annotation['sample_name'] is not None:
                sample_names_those_has_pre_annotations.append(pre_annotation['sample_name'])

        storage = get_storage_backend()
        return [
            PreAnnotationFileResponse(
                id=attachment.id,
                url=(storage.get_read_url(attachment.path) if storage.is_remote else attachment.url),
                thumbnail_url=(
                    storage.get_read_url(build_thumbnail_key(attachment.path))
                    if storage.is_remote
                    else build_attachment_api_path(build_thumbnail_key(attachment.path))
                ),
                stream_url=(
                    storage.get_read_url(attachment.path)
                    if storage.is_remote
                    else build_partial_api_path(attachment.path)
                ),
                storage_backend=storage.backend_name,
                filename=attachment.filename,
                sample_names=sample_names_those_has_pre_annotations,
            )
            for attachment in attachments
        ], total
        
    except LabelUException:
        raise
    except Exception as e:
        logger.error("list pre annotation files error: {}", e)
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )


async def get(
    db: Session, task_id: int, pre_annotation_id: int, current_user: User
) -> PreAnnotationResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)

    pre_annotation = crud_pre_annotation.get(
        db=db,
        pre_annotation_id=pre_annotation_id,
    )

    if not pre_annotation or pre_annotation.task_id != task_id:
        logger.error("cannot find pre_annotation: {}", pre_annotation_id)
        raise LabelUException(
            code=ErrorCode.CODE_55001_SAMPLE_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
        
    # response
    return PreAnnotationResponse(
        id=pre_annotation.id,
        data=expand_pre_annotation_api_data(pre_annotation.data),
        file=build_attachment_response(pre_annotation.file),
        created_at=pre_annotation.created_at,
        created_by=UserResp(
            id=pre_annotation.owner.id,
            username=pre_annotation.owner.username,
        ),
        updated_at=pre_annotation.updated_at,
        updated_by=UserResp(
            id=pre_annotation.updater.id,
            username=pre_annotation.updater.username,
        ),
    )
    
async def delete_pre_annotation_file(
    db: Session, task_id: int, file_id: int, current_user: User
) -> CommonDataResp:
    storage = get_storage_backend()
    with begin_transaction(db):
        task = crud_task.get(db=db, task_id=task_id, lock=True)
        if not task:
            logger.error("cannot find task:{}", task_id)
            raise LabelUException(
                code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        assert_user_is_task_owner_or_superuser(task, current_user)
        
        attachments = crud_attachment.get_by_ids(
            db=db, attachment_ids=[file_id]
        )

        if len(attachments) == 0 or attachments[0].task_id != task_id:
            raise LabelUException(
                code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        
        for attachment in attachments:
            storage.delete(attachment.path)
            thumbnail_key = build_thumbnail_key(attachment.path)
            if storage.exists(thumbnail_key):
                storage.delete(thumbnail_key)
            
        pre_annotations = crud_pre_annotation.list_by_task_id_and_file_id(db=db, task_id=task_id, file_id=file_id)
        pre_annotation_ids = [pre_annotation.id for pre_annotation in pre_annotations]
        
        crud_pre_annotation.delete(db=db, pre_annotation_ids=pre_annotation_ids)
        crud_attachment.delete(db=db, attachment_ids=[file_id])
            
    # response
    return CommonDataResp(ok=True)

async def delete(
    db: Session,
    pre_annotation_ids: List[int],
    current_user: User,
    task_id: Optional[int] = None,
) -> CommonDataResp:
    # The route carries a task id.  Keep task_id optional for internal legacy
    # callers, but still verify access for every row when it is omitted.
    with begin_transaction(db):
        rows = crud_pre_annotation.get_by_ids(db=db, pre_annotation_ids=pre_annotation_ids)
        if task_id is not None:
            task = crud_task.get(db=db, task_id=task_id, lock=True)
            if not task:
                raise LabelUException(
                    code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            assert_user_has_task_access(task, current_user)
            if any(row.task_id != task_id for row in rows):
                raise LabelUException(
                    code=ErrorCode.CODE_55001_SAMPLE_NOT_FOUND,
                    status_code=status.HTTP_404_NOT_FOUND,
                )
        else:
            checked_task_ids = set()
            for row in rows:
                if row.task_id in checked_task_ids:
                    continue
                task = crud_task.get(db=db, task_id=row.task_id)
                if not task:
                    raise LabelUException(
                        code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
                        status_code=status.HTTP_404_NOT_FOUND,
                    )
                assert_user_has_task_access(task, current_user)
                checked_task_ids.add(row.task_id)
        crud_pre_annotation.delete(db=db, pre_annotation_ids=[row.id for row in rows])
    # response
    return CommonDataResp(ok=True)
