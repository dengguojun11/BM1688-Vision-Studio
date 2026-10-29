import json
from datetime import datetime
from typing import Any, Dict, List, Union, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import or_
from fastapi.encoders import jsonable_encoder

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation

GENERATED_PRE_ANNOTATION_SOURCE_TYPES = (
    "ai_generated",
    "yolo_generated",
    "tagtool_cls_generated",
    "locateanything_generated",
    "vlx_seek_generated",
    "rex_omni_generated",
)


def _parse_pre_annotation_data(data: str | None) -> dict:
    if not data:
        return {}
    try:
        parsed = json.loads(data)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pre_annotation_has_geometry(data: str | None) -> bool:
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


def _is_canvas_overwrite_candidate(data: str | None) -> bool:
    meta = _parse_pre_annotation_data(data).get("meta") or {}
    return (
        meta.get("source_type") in GENERATED_PRE_ANNOTATION_SOURCE_TYPES
        and meta.get("apply_to_canvas_overwrite") is True
    )


def batch(db: Session, pre_annotations: List[TaskPreAnnotation]) -> List[TaskPreAnnotation]:
    db.bulk_save_objects(pre_annotations, return_defaults=True)
    return pre_annotations


def _sample_name_filter(sample_name: str):
    return or_(
        TaskPreAnnotation.sample_name == sample_name,
        TaskPreAnnotation.sample_name == sample_name[9:],
    )


def list_by(
    db: Session,
    task_id: int | None = None,
    sample_name: str | None = None,
    after: int | None = None,
    before: int | None = None,
    page: int | None = None,
    sorting: str | None = None,
    size: int | None = 10,
    search: str | None = None,
) -> Tuple[List[TaskPreAnnotation], int]:

    # query filter
    query_filter = [TaskPreAnnotation.deleted_at == None]
    if before:
        query_filter.append(TaskPreAnnotation.id < before)
    if after:
        query_filter.append(TaskPreAnnotation.id > after)
    if task_id:
        query_filter.append(TaskPreAnnotation.task_id == task_id)
        
    if sample_name:
        query_filter.append(_sample_name_filter(sample_name))

    keyword = str(search or "").strip()
    if keyword:
        # Search imported sample names, source filenames and identifiers while
        # retaining the task/deleted-row filters above.
        pattern = literal_like_pattern(keyword)
        search_filter = [
            TaskPreAnnotation.sample_name.ilike(pattern, escape=LIKE_ESCAPE),
            TaskAttachment.filename.ilike(pattern, escape=LIKE_ESCAPE),
        ]
        if keyword.isdigit():
            numeric_id = int(keyword)
            search_filter.extend(
                (
                    TaskPreAnnotation.id == numeric_id,
                    TaskPreAnnotation.file_id == numeric_id,
                )
            )
        query_filter.append(or_(*search_filter))
        
    query = (
        db.query(TaskPreAnnotation)
        .outerjoin(TaskAttachment, TaskAttachment.id == TaskPreAnnotation.file_id)
        .filter(*query_filter)
    )

    # default order by id, before need select last items
    if before:
        query = query.order_by(TaskPreAnnotation.id.desc())
    else:
        query = query.order_by(TaskPreAnnotation.id.asc())
    
    count = query.count()
    
    results = (
        query.offset(offset=page * size if page else 0)
        .limit(limit=size)
        .all()
    )
    
    if sorting:
        field, order = sorting.split(":")
        if order == "desc":
            results = sorted(results, key=lambda x: getattr(x, field), reverse=True)
        else:
            results = sorted(results, key=lambda x: getattr(x, field))
    
    if before:
        results.reverse()
        
    return results, count


def get_canvas_candidate_by_task_id_and_sample_name(
    db: Session, task_id: int, sample_name: str
) -> TaskPreAnnotation | None:
    rows, _ = list_by(db=db, task_id=task_id, sample_name=sample_name, page=0, size=200)
    if not rows:
        return None

    canvas_overrides = [row for row in rows if _is_canvas_overwrite_candidate(row.data)]
    if canvas_overrides:
        return canvas_overrides[-1]

    uploaded = next((row for row in rows if row.file_id is not None), None)
    if uploaded:
        return uploaded

    generated = [
        row
        for row in rows
        if _parse_pre_annotation_data(row.data).get("meta", {}).get("source_type")
        in GENERATED_PRE_ANNOTATION_SOURCE_TYPES
    ]
    generated_with_geometry = [row for row in generated if _pre_annotation_has_geometry(row.data)]
    if generated_with_geometry:
        return generated_with_geometry[-1]

    any_with_geometry = [row for row in rows if _pre_annotation_has_geometry(row.data)]
    if any_with_geometry:
        return any_with_geometry[-1]

    if generated:
        return generated[-1]

    return rows[-1]

def list_by_task_id_and_owner_id_lightweight(db: Session, task_id: int, owner_id: int) -> List[Dict[str, Any]]:
    pre_annotations = db.query(
        TaskPreAnnotation.file_id,
        TaskPreAnnotation.sample_name
    ).filter(
        TaskPreAnnotation.task_id == task_id,
        TaskPreAnnotation.deleted_at == None,
        TaskPreAnnotation.created_by == owner_id
    ).all()

    return [
        {
            'file_id': pre_annotation.file_id,
            'sample_name': pre_annotation.sample_name
        }
        for pre_annotation in pre_annotations
    ]


def list_by_task_id(db: Session, task_id: int) -> List[Dict[str, Any]]:
    pre_annotations = db.query(
        TaskPreAnnotation.file_id,
        TaskPreAnnotation.sample_name,
    ).filter(
        TaskPreAnnotation.task_id == task_id,
        TaskPreAnnotation.deleted_at == None,
    ).all()

    return [
        {
            "file_id": pre_annotation.file_id,
            "sample_name": pre_annotation.sample_name,
        }
        for pre_annotation in pre_annotations
    ]


def list_sample_names_by_source_type(
    db: Session, task_id: int, source_type: str
) -> set[str]:
    """Return sample names that already have a generated result from a provider."""
    rows = (
        db.query(TaskPreAnnotation.sample_name, TaskPreAnnotation.data)
        .filter(
            TaskPreAnnotation.task_id == task_id,
            TaskPreAnnotation.deleted_at == None,
            TaskPreAnnotation.data.like(f"%{source_type}%"),
        )
        .all()
    )
    names: set[str] = set()
    for sample_name, data in rows:
        parsed = _parse_pre_annotation_data(data)
        if (parsed.get("meta") or {}).get("source_type") == source_type and sample_name:
            names.add(str(sample_name))
    return names

def list_by_task_id_and_owner_id(db: Session, task_id: int, owner_id: int) -> List[TaskPreAnnotation]:
    pre_annotations = db.query(TaskPreAnnotation).filter(
        TaskPreAnnotation.task_id == task_id,
        TaskPreAnnotation.deleted_at == None,
        TaskPreAnnotation.created_by == owner_id
    ).all()
    
    return pre_annotations

def list_by_task_id_and_file_id(db: Session, task_id: int, file_id: int) -> List[TaskPreAnnotation]:
    return db.query(TaskPreAnnotation).filter(
        TaskPreAnnotation.task_id == task_id,
        TaskPreAnnotation.deleted_at == None,
        TaskPreAnnotation.file_id == file_id
    ).all()

def list_by_task_id_and_owner_id_and_sample_name(db: Session, task_id: int, sample_name: str) -> List[TaskPreAnnotation]:
    """list pre annotations by task_id, owner_id and sample_name without pagination

    Args:
        db (Session): _description_
        task_id (int): _description_
        owner_id (int): _description_
        sample_name (str): _description_

    Returns:
        List[TaskPreAnnotation]: _description_
    """
    return db.query(TaskPreAnnotation).filter(
        TaskPreAnnotation.task_id == task_id,
        TaskPreAnnotation.deleted_at == None,
        TaskPreAnnotation.sample_name == sample_name
    ).all()


def list_by_task_id_and_sample_name(
    db: Session, task_id: int, sample_name: str
) -> List[TaskPreAnnotation]:
    """Return all active pre-annotations for one task sample."""
    return db.query(TaskPreAnnotation).filter(
        TaskPreAnnotation.task_id == task_id,
        TaskPreAnnotation.deleted_at == None,
        TaskPreAnnotation.sample_name == sample_name,
    ).all()

def get(db: Session, pre_annotation_id: int) -> TaskPreAnnotation:
    return (
        db.query(TaskPreAnnotation)
        .filter(TaskPreAnnotation.id == pre_annotation_id, TaskPreAnnotation.deleted_at == None)
        .first()
    )


def get_by_ids(db: Session, pre_annotation_ids: List[int]) -> List[TaskPreAnnotation]:
    return (
        db.query(TaskPreAnnotation)
        .filter(TaskPreAnnotation.id.in_(pre_annotation_ids), TaskPreAnnotation.deleted_at == None)
        .all()
    )


def update(db: Session, db_obj: TaskPreAnnotation, obj_in: Dict[str, Any]) -> TaskPreAnnotation:
    obj_data = jsonable_encoder(obj_in)
    for field in obj_data:
        if field in obj_in:
            setattr(db_obj, field, obj_in[field])
    db.add(db_obj)
    db.flush()
    db.refresh(db_obj)
    return db_obj


def delete(db: Session, pre_annotation_ids: List[int]) -> None:
    db.query(TaskPreAnnotation).filter(TaskPreAnnotation.id.in_(pre_annotation_ids)).update(
        {TaskPreAnnotation.deleted_at: datetime.now()}
    )


def count(db: Session, task_id: int, sample_name: str | None) -> int:
    query_filter = [TaskPreAnnotation.deleted_at == None]
    if task_id:
        query_filter.append(TaskPreAnnotation.task_id == task_id)
        
    if sample_name:
        query_filter.append(TaskPreAnnotation.sample_name == sample_name)
        
    return db.query(TaskPreAnnotation).filter(*query_filter).count()
