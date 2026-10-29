from datetime import datetime
from typing import Any, Dict, List, Union


from sqlalchemy import and_, case, func, or_, text
from sqlalchemy.orm import Session, joinedload, selectinload
from fastapi.encoders import jsonable_encoder

from labelu.internal.domain.models.sample import SampleState
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.annotation_contract import SampleAnnotationLabelIndex
from labelu.internal.common.label_image import rect_label_values

_INDEX_SYNC_CHUNK_SIZE = 500


def _chunks(values: List[int], size: int = _INDEX_SYNC_CHUNK_SIZE):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def sync_annotation_label_index(db: Session, samples: List[TaskSample]) -> None:
    """Replace the rectangle-label index for the supplied persisted samples."""
    persisted = [sample for sample in samples if sample.id is not None]
    sample_ids = [int(sample.id) for sample in persisted]
    for chunk in _chunks(sample_ids):
        db.query(SampleAnnotationLabelIndex).filter(
            SampleAnnotationLabelIndex.sample_id.in_(chunk)
        ).delete(synchronize_session="fetch")

    rows = []
    for sample in persisted:
        if sample.deleted_at is not None or sample.task_id is None:
            continue
        for label_value in rect_label_values(sample.data):
            if len(label_value) > 128:
                continue
            rows.append(
                SampleAnnotationLabelIndex(
                    sample_id=int(sample.id),
                    task_id=int(sample.task_id),
                    label_value=label_value,
                )
            )
    if rows:
        db.bulk_save_objects(rows)


def batch(db: Session, samples: List[TaskSample]) -> List[TaskSample]:
    db.bulk_save_objects(samples, return_defaults=True)
    sync_annotation_label_index(db, samples)
    return samples


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
) -> List[TaskSample]:

    # query filter
    query_filter = [TaskSample.deleted_at == None]
    if before:
        query_filter.append(TaskSample.id < before)
    if after:
        query_filter.append(TaskSample.id > after)
    if task_id:
        query_filter.append(TaskSample.task_id == task_id)
    if state:
        query_filter.append(TaskSample.state == state)
    keyword = (search or "").strip()
    if keyword:
        from labelu.internal.domain.models.attachment import TaskAttachment

        escaped = (
            keyword.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        search_filter = [
            and_(
                TaskAttachment.deleted_at == None,
                TaskAttachment.filename.ilike(f"%{escaped}%", escape="\\"),
            )
        ]
        if keyword.isdigit():
            numeric_value = int(keyword)
            search_filter.extend(
                [TaskSample.id == numeric_value, TaskSample.inner_id == numeric_value]
            )
        query_filter.extend(
            [or_(*search_filter)]
        )
    query = db.query(TaskSample).options(
        joinedload(TaskSample.file),
        joinedload(TaskSample.owner),
        selectinload(TaskSample.updaters),
    )
    if keyword:
        from labelu.internal.domain.models.attachment import TaskAttachment

        query = query.outerjoin(TaskAttachment, TaskSample.file_id == TaskAttachment.id)
    query = query.filter(*query_filter)

    # case when for state enum
    whens = {state.value: index for index, state in enumerate(SampleState)}
    sort_logic = case(whens, value=TaskSample.state).label(TaskSample.state.key)

    if sorting:
        sort_strings = sorting.split(",")
        for item in sort_strings:
            sort_key = item.split(":")
            if sort_key[0] == TaskSample.state.key:
                if sort_key[1] == "asc":
                    query = query.order_by(sort_logic.asc())
                else:
                    query = query.order_by(sort_logic.desc())
            else:
                query = query.order_by(text(f"{sort_key[0]} {sort_key[1]}"))

    # default order by id, before need select last items
    if before:
        query = query.order_by(TaskSample.id.desc())
    else:
        query = query.order_by(TaskSample.id.asc())
    results = (
        query.offset(offset=page * size if page else 0)
        .limit(limit=size)
        .all()
    )
    if before:
        results.reverse()
    return results


def get(db: Session, sample_id: int) -> TaskSample:
    return (
        db.query(TaskSample)
        .options(
            joinedload(TaskSample.file),
            joinedload(TaskSample.owner),
            selectinload(TaskSample.updaters),
        )
        .filter(TaskSample.id == sample_id, TaskSample.deleted_at == None)
        .first()
    )


def get_for_update(db: Session, sample_id: int) -> TaskSample:
    """Load the sample row with a database write lock for version checks."""
    return (
        db.query(TaskSample)
        .populate_existing()
        .filter(TaskSample.id == sample_id, TaskSample.deleted_at == None)
        .with_for_update()
        .first()
    )


def get_by_ids(db: Session, sample_ids: List[int], task_id: Union[int, None] = None) -> List[TaskSample]:
    query_filter = [TaskSample.id.in_(sample_ids), TaskSample.deleted_at == None]
    if task_id is not None:
        query_filter.append(TaskSample.task_id == task_id)
    return db.query(TaskSample).filter(*query_filter).all()


def map_file_ids_to_sample_ids(
    db: Session, task_id: int, file_ids: List[int]
) -> dict[int, int]:
    """Return existing sample id for each attachment file_id in this task."""
    if not file_ids:
        return {}
    rows = (
        db.query(TaskSample.file_id, TaskSample.id)
        .filter(
            TaskSample.task_id == task_id,
            TaskSample.file_id.in_(file_ids),
            TaskSample.deleted_at == None,
        )
        .all()
    )
    return {int(file_id): int(sample_id) for file_id, sample_id in rows if file_id is not None}


def list_new_samples(db: Session, task_id: int) -> List[TaskSample]:
    return (
        db.query(TaskSample)
        .filter(
            TaskSample.task_id == task_id,
            TaskSample.state == SampleState.NEW.value,
            TaskSample.deleted_at == None,
        )
        .all()
    )


def list_by_task(db: Session, task_id: int) -> List[TaskSample]:
    return (
        db.query(TaskSample)
        .options(joinedload(TaskSample.file))
        .filter(TaskSample.task_id == task_id, TaskSample.deleted_at == None)
        .order_by(TaskSample.id.asc())
        .all()
    )


def list_batch_auto_label_targets(
    db: Session,
    task_id: int,
    include_done_samples: bool,
    sample_states: List[str] | None = None,
) -> List[TaskSample]:
    """Samples eligible for batch AI pre-label."""
    query_filter = [
        TaskSample.task_id == task_id,
        TaskSample.deleted_at == None,
    ]
    if sample_states:
        query_filter.append(TaskSample.state.in_(sample_states))
    elif include_done_samples:
        query_filter.append(TaskSample.state != SampleState.SKIPPED.value)
    else:
        query_filter.append(TaskSample.state == SampleState.NEW.value)
    return (
        db.query(TaskSample)
        .options(joinedload(TaskSample.file))
        .filter(*query_filter)
        .order_by(TaskSample.id.asc())
        .all()
    )


def update(db: Session, db_obj: TaskSample, obj_in: Dict[str, Any]) -> TaskSample:
    should_sync_label_index = any(
        field in obj_in for field in (TaskSample.data.key, TaskSample.task_id.key)
    )
    obj_data = jsonable_encoder(obj_in)
    for field in obj_data:
        if field in obj_in:
            setattr(db_obj, field, obj_in[field])
    db.add(db_obj)
    db.flush()
    db.refresh(db_obj)
    if should_sync_label_index:
        sync_annotation_label_index(db, [db_obj])
    return db_obj


def delete(db: Session, sample_ids: List[int]) -> None:
    if not sample_ids:
        return
    for chunk in _chunks([int(sample_id) for sample_id in sample_ids]):
        db.query(SampleAnnotationLabelIndex).filter(
            SampleAnnotationLabelIndex.sample_id.in_(chunk)
        ).delete(synchronize_session="fetch")
    db.query(TaskSample).filter(TaskSample.id.in_(sample_ids)).update(
        {TaskSample.deleted_at: datetime.now()}
    )


def count(
    db: Session,
    task_id: int,
    state: Union[str, None] = None,
    search: Union[str, None] = None,
) -> int:
    query_filter = [TaskSample.deleted_at == None]
    if task_id:
        query_filter.append(TaskSample.task_id == task_id)
    if state:
        query_filter.append(TaskSample.state == state)
    query = db.query(TaskSample)
    keyword = (search or "").strip()
    if keyword:
        from labelu.internal.domain.models.attachment import TaskAttachment

        escaped = (
            keyword.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        search_filter = [
            and_(
                TaskAttachment.deleted_at == None,
                TaskAttachment.filename.ilike(f"%{escaped}%", escape="\\"),
            )
        ]
        if keyword.isdigit():
            numeric_value = int(keyword)
            search_filter.extend(
                [TaskSample.id == numeric_value, TaskSample.inner_id == numeric_value]
            )
        query = query.outerjoin(TaskAttachment, TaskSample.file_id == TaskAttachment.id)
        query_filter.extend(
            [or_(*search_filter)]
        )
    return query.filter(*query_filter).count()


def task_list_sample_total(db: Session, task_id: int) -> int:
    """与任务列表卡片 total 一致：new + done + skipped + abnormal + incomplete。"""
    if not task_id:
        return 0
    statics_map = statics(db=db, task_ids=[int(task_id)])
    tid = int(task_id)
    return sum(
        int(statics_map.get(f"{tid}_{state.value}", 0) or 0)
        for state in SampleState
    )


def statics(
    db: Session,
    task_ids: List[int],
) -> dict:
    query_filter = [TaskSample.deleted_at == None]
    if task_ids:
        query_filter.append(TaskSample.task_id.in_(task_ids))

    statics = (
        db.query(
            TaskSample.task_id,
            TaskSample.state,
            func.count(TaskSample.state).label("count"),
        )
        .filter(*query_filter)
        .group_by(TaskSample.task_id, TaskSample.state)
        .all()
    )
    d = {f"{s[0]}_{s[1]}": s[2] for s in statics}
    return d


def _filename_like_pattern(keyword: str) -> str:
    escaped = (
        keyword.strip()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def search_by_filename(
    db: Session,
    task_ids: List[int],
    keyword: str,
    page: int,
    size: int,
) -> tuple[List[TaskSample], int]:
    """按附件文件名模糊搜索样本（限定 task_ids）。"""
    from labelu.internal.domain.models.attachment import TaskAttachment
    from labelu.internal.domain.models.task import Task

    q = (keyword or "").strip()
    if not q or not task_ids:
        return [], 0
    pattern = _filename_like_pattern(q)
    base = (
        db.query(TaskSample)
        .join(TaskAttachment, TaskSample.file_id == TaskAttachment.id)
        .join(Task, TaskSample.task_id == Task.id)
        .filter(
            TaskSample.deleted_at == None,
            TaskAttachment.deleted_at == None,
            Task.deleted_at == None,
            TaskSample.task_id.in_(task_ids),
            TaskAttachment.filename.ilike(pattern, escape="\\"),
        )
    )
    total = base.count()
    rows = (
        base.options(
            joinedload(TaskSample.file),
            joinedload(TaskSample.task),
        )
        .order_by(TaskSample.updated_at.desc())
        .offset(max(0, page) * size)
        .limit(size)
        .all()
    )
    return rows, total
