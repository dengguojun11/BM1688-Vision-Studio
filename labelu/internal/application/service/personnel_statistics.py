"""Per-person annotation/review workload and presence statistics."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func

from labelu.internal.application.service import user_activity
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.annotation_review_event import AnnotationReviewEvent
from labelu.internal.domain.models.sample_edit_log import SampleEditLog
from labelu.internal.domain.models.system_operation_log import SystemOperationLog
from labelu.internal.domain.models.user import User

WORK_CLUSTER_GAP_SECONDS = 30 * 60
REVIEW_DECISION_STATUSES = {"APPROVED", "REJECTED", "ARBITRATED", "INVALIDATED"}
EXCLUDED_USERNAMES = {"open@system"}


def _date_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return datetime.combine(date_from, time.min), datetime.combine(
        date_to, time.max
    )


def _is_effective_annotation(log: SampleEditLog) -> bool:
    # A pure state/updated_at save is not annotation work.  Negative samples and
    # global-tag-only edits change the data payload and therefore remain valid.
    if str(log.previous_data or "") != str(log.new_data or ""):
        return True
    return int(log.previous_annotated_count or 0) != int(
        log.new_annotated_count or 0
    )


def _cluster_seconds(timestamps: list[datetime]) -> int:
    if len(timestamps) < 2:
        return 0
    ordered = sorted(timestamps)
    total = 0
    start = ordered[0]
    previous = ordered[0]
    for current in ordered[1:]:
        if (current - previous).total_seconds() <= WORK_CLUSTER_GAP_SECONDS:
            previous = current
            continue
        total += int((previous - start).total_seconds())
        start = current
        previous = current
    total += int((previous - start).total_seconds())
    return max(0, total)


def _empty_user_row(user: User, *, online: bool, latest_seen: datetime | None) -> dict[str, Any]:
    return {
        "user_id": int(user.id),
        "username": user.username,
        "online": online,
        "last_seen_at": latest_seen,
        "last_business_at": None,
        "online_seconds": 0,
        "effective_work_seconds": 0,
        "annotation_images": 0,
        "annotation_boxes": 0,
        "annotation_edits": 0,
        "review_images": 0,
        "review_approved": 0,
        "review_rejected": 0,
        "review_arbitrated": 0,
        "review_invalidated": 0,
        "task_count": 0,
    }


def get_personnel_dashboard(
    db,
    current_user: User,
    *,
    date_from: date,
    date_to: date,
    user_id: int | None = None,
) -> dict[str, Any]:
    if not is_task_superuser(current_user):
        from labelu.internal.common.error_code import ErrorCode, LabelUException
        from fastapi import status

        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="仅管理人员可查看人员统计",
        )

    start, end = _date_bounds(date_from, date_to)
    excluded = {
        int(row[0])
        for row in db.query(User.id)
        .filter(User.username.in_(list(EXCLUDED_USERNAMES)))
        .all()
    }
    query = db.query(User)
    if user_id is not None:
        query = query.filter(User.id == int(user_id))
    users = [
        user
        for user in query.order_by(User.username.asc()).all()
        if int(user.id) not in excluded
    ]
    user_ids = [int(user.id) for user in users]
    online_map = {
        int(item["user_id"]): item
        for item in user_activity.online_users(db)
        if int(item["user_id"]) in user_ids
    }
    latest_sessions = user_activity.user_latest_sessions(db, user_ids)
    rows = {
        int(user.id): _empty_user_row(
            user,
            online=int(user.id) in online_map,
            latest_seen=(
                latest_sessions[int(user.id)].last_seen_at
                if int(user.id) in latest_sessions
                else None
            ),
        )
        for user in users
    }

    # Annotation: dedupe by user/sample/day; only effective content changes.
    annotation_events: dict[int, list[datetime]] = defaultdict(list)
    if user_ids:
        logs = (
            db.query(SampleEditLog)
            .filter(
                SampleEditLog.created_at >= start,
                SampleEditLog.created_at <= end,
                SampleEditLog.user_id.in_(user_ids),
            )
            .order_by(SampleEditLog.created_at.asc())
            .all()
        )
        last_by_day: dict[tuple[int, int, date], SampleEditLog] = {}
        task_sets: dict[int, set[int]] = defaultdict(set)
        for log in logs:
            if not _is_effective_annotation(log):
                continue
            uid = int(log.user_id)
            day = log.created_at.date()
            key = (uid, int(log.sample_id), day)
            existing = last_by_day.get(key)
            if existing is None or log.created_at >= existing.created_at:
                last_by_day[key] = log
            annotation_events[uid].append(log.created_at)
            task_sets[uid].add(int(log.task_id))
        for (uid, _sample_id, _day), log in last_by_day.items():
            rows[uid]["annotation_images"] += 1
            rows[uid]["annotation_boxes"] += int(log.new_annotated_count or 0)
        for uid, task_ids in task_sets.items():
            rows[uid]["task_count"] = len(task_ids)
        for uid, events in annotation_events.items():
            rows[uid]["annotation_edits"] = len(events)
            rows[uid]["effective_work_seconds"] += _cluster_seconds(events)
            rows[uid]["last_business_at"] = max(
                filter(None, [rows[uid]["last_business_at"], *events])
            )

    # Review events: append-only decisions, one count per review/user/status.
    review_events: dict[int, list[AnnotationReviewEvent]] = defaultdict(list)
    if user_ids:
        for event in (
            db.query(AnnotationReviewEvent)
            .filter(
                AnnotationReviewEvent.created_at >= start,
                AnnotationReviewEvent.created_at <= end,
                AnnotationReviewEvent.user_id.in_(user_ids),
                AnnotationReviewEvent.to_status.in_(LIST_REVIEW_STATUSES),
            )
            .all()
        ):
            review_events[int(event.user_id)].append(event)
    for uid, events in review_events.items():
        row = rows[uid]
        seen_images: set[int] = set()
        status_sets: dict[str, set[int]] = defaultdict(set)
        for event in events:
            seen_images.add(int(event.sample_id))
            status_sets[event.to_status].add(int(event.review_id))
            if event.created_at and (
                row["last_business_at"] is None
                or event.created_at > row["last_business_at"]
            ):
                row["last_business_at"] = event.created_at
        row["review_images"] = len(seen_images)
        row["review_approved"] = len(status_sets.get("APPROVED", set()))
        row["review_rejected"] = len(status_sets.get("REJECTED", set()))
        row["review_arbitrated"] = len(status_sets.get("ARBITRATED", set()))
        row["review_invalidated"] = len(status_sets.get("INVALIDATED", set()))

    # Online duration, active-session marker, and last business activity.
    if user_ids:
        for session in (
            db.query(user_activity.UserActivitySession)
            .filter(
                user_activity.UserActivitySession.user_id.in_(user_ids),
                user_activity.UserActivitySession.login_at <= end,
                user_activity.UserActivitySession.last_seen_at >= start,
            )
            .all()
        ):
            uid = int(session.user_id)
            if uid not in rows:
                continue
            rows[uid]["online_seconds"] += user_activity.overlap_seconds(
                session, date_from=start, date_to=end
            )

    result_rows = list(rows.values())
    result_rows.sort(
        key=lambda item: (
            bool(item["online"]),
            item["annotation_images"] + item["review_images"],
            item["online_seconds"],
        ),
        reverse=True,
    )
    session_active_ids = {
        int(row[0])
        for row in db.query(user_activity.UserActivitySession.user_id)
        .filter(
            user_activity.UserActivitySession.login_at <= end,
            user_activity.UserActivitySession.last_seen_at >= start,
        )
        .all()
        if int(row[0]) in user_ids
    }
    operation_active_ids = {
        int(row[0])
        for row in db.query(SystemOperationLog.user_id)
        .filter(
            SystemOperationLog.created_at >= start,
            SystemOperationLog.created_at <= end,
            SystemOperationLog.user_id.in_(user_ids) if user_ids else False,
        )
        .all()
        if row[0] is not None and int(row[0]) in user_ids
    }
    active_user_ids = session_active_ids | operation_active_ids
    summary = {
        "online_now": len(online_map),
        "active_users": len(active_user_ids),
        "annotation_images": sum(item["annotation_images"] for item in result_rows),
        "annotation_boxes": sum(item["annotation_boxes"] for item in result_rows),
        "annotation_edits": sum(item["annotation_edits"] for item in result_rows),
        "review_images": sum(item["review_images"] for item in result_rows),
        "review_approved": sum(item["review_approved"] for item in result_rows),
        "review_rejected": sum(item["review_rejected"] for item in result_rows),
        "review_arbitrated": sum(item["review_arbitrated"] for item in result_rows),
        "review_invalidated": sum(item["review_invalidated"] for item in result_rows),
        "effective_work_seconds": sum(
            item["effective_work_seconds"] for item in result_rows
        ),
    }
    return {
        "summary": {
            **summary,
            "date_from": date_from,
            "date_to": date_to,
        },
        "users": result_rows,
    }


LIST_REVIEW_STATUSES = list(REVIEW_DECISION_STATUSES)


def get_personnel_detail(
    db,
    current_user: User,
    *,
    user_id: int,
    date_from: date,
    date_to: date,
) -> dict[str, Any]:
    dashboard = get_personnel_dashboard(
        db,
        current_user,
        date_from=date_from,
        date_to=date_to,
        user_id=user_id,
    )
    user_row = next(
        (item for item in dashboard["users"] if item["user_id"] == int(user_id)),
        None,
    )
    if user_row is None:
        return {"user": None, "daily": []}

    start, end = _date_bounds(date_from, date_to)
    daily: dict[date, dict[str, Any]] = {}
    for log in (
        db.query(SampleEditLog)
        .filter(
            SampleEditLog.user_id == int(user_id),
            SampleEditLog.created_at >= start,
            SampleEditLog.created_at <= end,
        )
        .all()
    ):
        if not _is_effective_annotation(log):
            continue
        day = log.created_at.date()
        bucket = daily.setdefault(
            day,
            {
                "date": day,
                "annotation_images_set": set(),
                "annotation_boxes": 0,
                "annotation_edits": 0,
                "review_images_set": set(),
                "review_approved": 0,
                "review_rejected": 0,
                "review_arbitrated": 0,
                "review_invalidated": 0,
            },
        )
        bucket["annotation_images_set"].add(int(log.sample_id))
        bucket["annotation_boxes"] += int(log.new_annotated_count or 0)
        bucket["annotation_edits"] += 1

    for event in (
        db.query(AnnotationReviewEvent)
        .filter(
            AnnotationReviewEvent.user_id == int(user_id),
            AnnotationReviewEvent.created_at >= start,
            AnnotationReviewEvent.created_at <= end,
            AnnotationReviewEvent.to_status.in_(LIST_REVIEW_STATUSES),
        )
        .all()
    ):
        day = event.created_at.date()
        bucket = daily.setdefault(
            day,
            {
                "date": day,
                "annotation_images_set": set(),
                "annotation_boxes": 0,
                "annotation_edits": 0,
                "review_images_set": set(),
                "review_approved": 0,
                "review_rejected": 0,
                "review_arbitrated": 0,
                "review_invalidated": 0,
            },
        )
        bucket["review_images_set"].add(int(event.sample_id))
        key = f"review_{event.to_status.lower()}"
        bucket[key] = int(bucket.get(key, 0)) + 1

    daily_rows = []
    for day in sorted(daily):
        value = daily[day]
        daily_rows.append(
            {
                "date": day,
                "annotation_images": len(value["annotation_images_set"]),
                "annotation_boxes": value["annotation_boxes"],
                "annotation_edits": value["annotation_edits"],
                "review_images": len(value["review_images_set"]),
                "review_approved": value["review_approved"],
                "review_rejected": value["review_rejected"],
                "review_arbitrated": value["review_arbitrated"],
                "review_invalidated": value["review_invalidated"],
            }
        )
    return {"user": user_row, "daily": daily_rows}
