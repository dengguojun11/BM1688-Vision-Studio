"""标注统计：按人按日真实修改图片张数、单张图片真实修改记录估算用时、汇总大屏数据。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_task
from labelu.internal.application.response.annotation_stats import (
    AnnotationDailyUserRow,
    AnnotationSampleTimingRow,
    AnnotationStatsDashboardResponse,
    AnnotationStatsSummary,
)
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.sample_edit_log import SampleEditLog
from labelu.internal.domain.models.task import Task
from labelu.internal.domain.models.task_sample_updater import TaskSampleUpdater
from labelu.internal.domain.models.user import User

MAX_SAMPLE_DURATION_SECONDS = 4 * 3600
# 统计大屏不计入的管理/测试账号（username，与登录邮箱一致）
STATS_EXCLUDED_USERNAMES = frozenset({"admin@123.com"})


@dataclass
class _ResolvedSampleRow:
    sample_id: int
    task_id: int
    task_name: Optional[str]
    filename: Optional[str]
    annotator_id: int
    annotator_username: str
    created_at: datetime
    updated_at: datetime
    annotated_count: int


@dataclass
class _ResolvedEditRow:
    sample_id: int
    task_id: int
    task_name: Optional[str]
    filename: Optional[str]
    annotator_id: int
    annotator_username: str
    edited_at: datetime
    sample_created_at: Optional[datetime]
    annotated_count: int


def _excluded_user_ids(db: Session) -> List[int]:
    if not STATS_EXCLUDED_USERNAMES:
        return []
    rows = (
        db.query(User.id)
        .filter(User.username.in_(list(STATS_EXCLUDED_USERNAMES)))
        .all()
    )
    return [int(r[0]) for r in rows]


def _fallback_annotator_map(
    db: Session, sample_ids: List[int], excluded_ids: List[int]
) -> Dict[int, int]:
    """每个样本取 task_sample_updater 中最近一位非排除标注者。"""
    if not sample_ids:
        return {}
    q = (
        db.query(TaskSampleUpdater)
        .filter(TaskSampleUpdater.sample_id.in_(sample_ids))
        .order_by(
            TaskSampleUpdater.sample_id.asc(),
            TaskSampleUpdater.created_at.desc(),
        )
    )
    if excluded_ids:
        q = q.filter(~TaskSampleUpdater.user_id.in_(excluded_ids))
    out: Dict[int, int] = {}
    for row in q.all():
        sid = int(row.sample_id)
        if sid not in out:
            out[sid] = int(row.user_id)
    return out


def _resolve_annotator_id(
    updated_by: Optional[int],
    sample_id: int,
    fallback_map: Dict[int, int],
    excluded_ids: List[int],
) -> Optional[int]:
    if updated_by is not None and (not excluded_ids or updated_by not in excluded_ids):
        return int(updated_by)
    return fallback_map.get(sample_id)


def _default_date_range() -> Tuple[date, date]:
    today = date.today()
    return today - timedelta(days=29), today


def _visible_task_ids(
    db: Session,
    current_user: User,
    task_id: Optional[int] = None,
) -> List[int]:
    q = crud_task.visible_query(
        db,
        owner_id=current_user.id,
        list_all_tasks=is_task_superuser(current_user),
    )
    if task_id is not None:
        q = q.filter(Task.id == task_id)
    rows = q.with_entities(Task.id).all()
    return [int(r[0]) for r in rows]


def _sample_base_query(
    db: Session,
    task_ids: List[int],
    date_from: date,
    date_to: date,
):
    start_dt = datetime.combine(date_from, time.min)
    end_dt = datetime.combine(date_to + timedelta(days=1), time.min)
    return db.query(TaskSample).filter(
        TaskSample.deleted_at == None,
        TaskSample.task_id.in_(task_ids),
        TaskSample.state == SampleState.DONE.value,
        TaskSample.updated_at >= start_dt,
        TaskSample.updated_at < end_dt,
    )


def _edit_log_base_query(
    db: Session,
    task_ids: List[int],
    date_from: date,
    date_to: date,
):
    start_dt = datetime.combine(date_from, time.min)
    end_dt = datetime.combine(date_to + timedelta(days=1), time.min)
    return db.query(SampleEditLog).filter(
        SampleEditLog.task_id.in_(task_ids),
        SampleEditLog.created_at >= start_dt,
        SampleEditLog.created_at < end_dt,
        SampleEditLog.action == "annotation_edit",
    )


def _load_resolved_samples(
    db: Session,
    base,
    excluded_ids: List[int],
) -> List[_ResolvedSampleRow]:
    raw = (
        base.join(Task, Task.id == TaskSample.task_id)
        .outerjoin(TaskAttachment, TaskAttachment.id == TaskSample.file_id)
        .with_entities(
            TaskSample.id,
            TaskSample.task_id,
            TaskSample.updated_by,
            TaskSample.created_at,
            TaskSample.updated_at,
            TaskSample.annotated_count,
            Task.name,
            TaskAttachment.filename,
        )
        .all()
    )
    if not raw:
        return []

    need_fallback = [
        int(r[0])
        for r in raw
        if r[2] is None or (excluded_ids and int(r[2]) in excluded_ids)
    ]
    fallback_map = _fallback_annotator_map(db, need_fallback, excluded_ids)

    annotator_ids: set[int] = set()
    pending: List[tuple] = []
    for row in raw:
        sample_id = int(row[0])
        annotator_id = _resolve_annotator_id(
            int(row[2]) if row[2] is not None else None,
            sample_id,
            fallback_map,
            excluded_ids,
        )
        if annotator_id is None:
            continue
        annotator_ids.add(annotator_id)
        pending.append((row, annotator_id))

    if not pending:
        return []

    username_by_id = {
        int(uid): uname
        for uid, uname in db.query(User.id, User.username)
        .filter(User.id.in_(list(annotator_ids)))
        .all()
    }

    resolved: List[_ResolvedSampleRow] = []
    for row, annotator_id in pending:
        resolved.append(
            _ResolvedSampleRow(
                sample_id=int(row[0]),
                task_id=int(row[1]),
                task_name=row[6],
                filename=row[7],
                annotator_id=annotator_id,
                annotator_username=username_by_id.get(annotator_id) or f"user#{annotator_id}",
                created_at=row[3],
                updated_at=row[4],
                annotated_count=int(row[5] or 0),
            )
        )
    return resolved


def _load_resolved_edits(
    db: Session,
    base,
    excluded_ids: List[int],
) -> List[_ResolvedEditRow]:
    raw = (
        base.join(TaskSample, TaskSample.id == SampleEditLog.sample_id)
        .join(Task, Task.id == SampleEditLog.task_id)
        .outerjoin(TaskAttachment, TaskAttachment.id == TaskSample.file_id)
        .join(User, User.id == SampleEditLog.user_id)
        .with_entities(
            SampleEditLog.sample_id,
            SampleEditLog.task_id,
            SampleEditLog.user_id,
            SampleEditLog.created_at,
            TaskSample.created_at,
            SampleEditLog.new_annotated_count,
            Task.name,
            TaskAttachment.filename,
            User.username,
        )
        .all()
    )
    resolved: List[_ResolvedEditRow] = []
    for row in raw:
        annotator_id = int(row[2])
        if excluded_ids and annotator_id in excluded_ids:
            continue
        resolved.append(
            _ResolvedEditRow(
                sample_id=int(row[0]),
                task_id=int(row[1]),
                task_name=row[6],
                filename=row[7],
                annotator_id=annotator_id,
                annotator_username=row[8] or f"user#{annotator_id}",
                edited_at=row[3],
                sample_created_at=row[4],
                annotated_count=int(row[5] or 0),
            )
        )
    return resolved


def _dedupe_actual_edit_rows(
    edits: List[_ResolvedEditRow],
) -> List[_ResolvedEditRow]:
    """同一人同一天对同一张图反复保存，只算 1 张，保留当天最后一次真实修改。"""
    deduped: Dict[tuple[int, int, date], _ResolvedEditRow] = {}
    for row in edits:
        key = (row.annotator_id, row.sample_id, row.edited_at.date())
        existing = deduped.get(key)
        if existing is None or row.edited_at >= existing.edited_at:
            deduped[key] = row
    return list(deduped.values())


def get_annotation_stats_dashboard(
    db: Session,
    current_user: User,
    *,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    task_id: Optional[int] = None,
    sample_page: int = 0,
    sample_size: int = 50,
) -> AnnotationStatsDashboardResponse:
    if not is_task_superuser(current_user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=403,
        )

    d_from, d_to = date_from or _default_date_range()[0], date_to or _default_date_range()[1]
    if d_from > d_to:
        d_from, d_to = d_to, d_from

    task_ids = _visible_task_ids(db, current_user, task_id)
    empty_summary = AnnotationStatsSummary(
        total_done=0,
        total_annotators=0,
        avg_duration_seconds=None,
        today_done=0,
        date_from=d_from,
        date_to=d_to,
    )
    if not task_ids:
        return AnnotationStatsDashboardResponse(
            summary=empty_summary,
            daily_by_user=[],
            sample_timings=[],
            sample_timings_total=0,
        )

    excluded_ids = _excluded_user_ids(db)
    edits = _dedupe_actual_edit_rows(
        _load_resolved_edits(
        db,
        _edit_log_base_query(db, task_ids, d_from, d_to),
        excluded_ids,
        )
    )
    if not edits:
        return AnnotationStatsDashboardResponse(
            summary=empty_summary,
            daily_by_user=[],
            sample_timings=[],
            sample_timings_total=0,
        )

    total_done = len(edits)
    today_start = datetime.combine(date.today(), time.min)
    today_end = today_start + timedelta(days=1)
    today_done = sum(
        1 for s in edits if today_start <= s.edited_at < today_end
    )
    annotator_count = len({s.annotator_id for s in edits})

    daily_counter: Dict[tuple, int] = defaultdict(int)
    for s in edits:
        stat_date = s.edited_at.date() if isinstance(s.edited_at, datetime) else date.today()
        daily_counter[(s.annotator_id, s.annotator_username, stat_date)] += 1

    daily_by_user = [
        AnnotationDailyUserRow(
            user_id=uid,
            username=uname,
            stat_date=stat_date,
            done_count=cnt,
        )
        for (uid, uname, stat_date), cnt in daily_counter.items()
    ]
    daily_by_user.sort(key=lambda r: (r.stat_date, -r.done_count), reverse=True)

    sample_size = max(1, min(sample_size, 200))
    sorted_edits = sorted(edits, key=lambda s: s.edited_at, reverse=True)
    page_rows = sorted_edits[sample_page * sample_size : (sample_page + 1) * sample_size]

    sample_timings: List[AnnotationSampleTimingRow] = []
    for s in page_rows:
        duration_seconds = 0.0
        if s.sample_created_at and s.edited_at:
            sec = (s.edited_at - s.sample_created_at).total_seconds()
            if 0 <= sec <= MAX_SAMPLE_DURATION_SECONDS:
                duration_seconds = round(sec, 1)
        sample_timings.append(
            AnnotationSampleTimingRow(
                sample_id=s.sample_id,
                task_id=s.task_id,
                task_name=s.task_name,
                filename=s.filename,
                user_id=s.annotator_id,
                username=s.annotator_username,
                duration_seconds=duration_seconds,
                annotated_at=s.edited_at,
                annotated_count=s.annotated_count,
            )
        )

    samples = _load_resolved_samples(
        db,
        _sample_base_query(db, task_ids, d_from, d_to),
        excluded_ids,
    )

    durations: List[float] = []
    for s in samples:
        if not s.created_at or not s.updated_at:
            continue
        sec = (s.updated_at - s.created_at).total_seconds()
        if 0 <= sec <= MAX_SAMPLE_DURATION_SECONDS:
            durations.append(sec)
    avg_duration = sum(durations) / len(durations) if durations else None

    return AnnotationStatsDashboardResponse(
        summary=AnnotationStatsSummary(
            total_done=total_done,
            total_annotators=annotator_count,
            avg_duration_seconds=round(avg_duration, 1) if avg_duration is not None else None,
            today_done=today_done,
            date_from=d_from,
            date_to=d_to,
        ),
        daily_by_user=daily_by_user,
        sample_timings=sample_timings,
        sample_timings_total=total_done,
    )
