"""Per-person annotation/review workload and presence statistics.

Definitions used by the statistics screen:

* annotation image: a sample whose rectangle annotation actually changed
  (and the user clicked next/save); tag-only saves, uploads and bare "next"
  clicks do not count.  A correction made while reviewing an image counts as
  an annotated image as well as a review approval;
* annotation box: one added, removed or modified rectangle in such a save;
* review image: a successful sample save that changed nothing, i.e. the user
  compared the image and clicked next without editing;
* review approved: a save on a ``DONE`` sample that changed the annotation and
  was submitted with next;
* deleted image: one sample deleted from a task through the task sample list.

Online duration is the union of the user's sessions for *today*, clipped to
today's bounds, so it can never exceed 24 hours even if several tabs or devices
were open at the same time.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, time
from typing import Any

from labelu.internal.application.service import user_activity
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.sample_edit_log import SampleEditLog
from labelu.internal.domain.models.system_operation_log import SystemOperationLog
from labelu.internal.domain.models.user import User

WORK_CLUSTER_GAP_SECONDS = 30 * 60
NOOP_MATCH_WINDOW_SECONDS = 10
MAX_ONLINE_SECONDS = 24 * 3600
EXCLUDED_USERNAMES = {"open@system"}

_SAMPLE_SAVE_PATH = re.compile(r"/tasks/(?P<task_id>\d+)/samples/(?P<sample_id>\d+)$")
_SAMPLE_DELETE_PATH = re.compile(r"/tasks/(?P<task_id>\d+)/samples$")


def _date_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return datetime.combine(date_from, time.min), datetime.combine(date_to, time.max)


def _json_loads(raw: Any) -> Any:
    if raw in (None, ""):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _rect_boxes(raw: Any) -> dict[str, tuple]:
    """Return {rect id: (label, x, y, width, height, attributes)}."""
    value = _json_loads(raw)
    if not isinstance(value, dict):
        return {}
    result = _json_loads(value.get("result"))
    if not isinstance(result, dict):
        return {}
    rect_tool = result.get("rectTool")
    if not isinstance(rect_tool, dict):
        return {}
    items = rect_tool.get("result")
    if not isinstance(items, list):
        return {}
    boxes: dict[str, tuple] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        key = str(item.get("id") or f"index-{index}")
        try:
            geometry = (
                round(float(item.get("x", 0)), 3),
                round(float(item.get("y", 0)), 3),
                round(float(item.get("width", 0)), 3),
                round(float(item.get("height", 0)), 3),
            )
        except (TypeError, ValueError):
            geometry = (0.0, 0.0, 0.0, 0.0)
        attributes = item.get("attributes")
        boxes[key] = (
            item.get("label"),
            *geometry,
            (
                json.dumps(attributes, ensure_ascii=False, sort_keys=True, default=str)
                if attributes
                else ""
            ),
        )
    return boxes


def _rect_change_count(previous_data: Any, new_data: Any) -> int:
    previous = _rect_boxes(previous_data)
    current = _rect_boxes(new_data)
    added = set(current) - set(previous)
    removed = set(previous) - set(current)
    modified = {
        key for key in set(previous) & set(current) if previous[key] != current[key]
    }
    return len(added) + len(removed) + len(modified)


def _is_effective_annotation(log: SampleEditLog) -> bool:
    # A pure state/updated_at save is not annotation work.  Negative samples and
    # global-tag-only edits change the data payload and therefore remain valid.
    if str(log.previous_data or "") != str(log.new_data or ""):
        return True
    return int(log.previous_annotated_count or 0) != int(log.new_annotated_count or 0)


def _is_done_state(value: Any) -> bool:
    return str(value or "").upper() == "DONE"


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


def _new_bucket() -> dict[str, Any]:
    return {
        "annotation_images": set(),
        "annotation_boxes": 0,
        "review_images": set(),
        "review_approved": set(),
        "deleted_images": set(),
        "work_events": [],
        "task_ids": set(),
    }


def _merge_intervals(intervals: list[tuple[datetime, datetime]]) -> int:
    if not intervals:
        return 0
    ordered = sorted(intervals)
    total = 0
    start, end = ordered[0]
    for current_start, current_end in ordered[1:]:
        if current_start <= end:
            if current_end > end:
                end = current_end
            continue
        total += int((end - start).total_seconds())
        start, end = current_start, current_end
    total += int((end - start).total_seconds())
    return max(0, total)


def _today_online_seconds(db, user_ids: list[int]) -> dict[int, int]:
    """Actual browser-interaction seconds accumulated today.

    The session span is intentionally not used here: a tab left open overnight
    would otherwise report many hours of "online" time that the user never
    actually spent interacting with the platform.
    """
    if not user_ids:
        return {}
    today = datetime.now().date()
    totals: dict[int, int] = defaultdict(int)
    rows = (
        db.query(user_activity.UserActivitySession)
        .filter(
            user_activity.UserActivitySession.user_id.in_(user_ids),
            user_activity.UserActivitySession.active_date == today,
        )
        .all()
    )
    for row in rows:
        totals[int(row.user_id)] += int(row.active_seconds or 0)
    return {uid: min(MAX_ONLINE_SECONDS, seconds) for uid, seconds in totals.items()}


def _deleted_sample_ids(detail_raw: Any) -> list[int]:
    detail = _json_loads(detail_raw)
    if not isinstance(detail, dict):
        return []
    body = detail.get("body")
    if isinstance(body, str):
        body = _json_loads(body)
    if not isinstance(body, dict):
        return []
    values = body.get("sample_ids")
    if not isinstance(values, list):
        return []
    result: list[int] = []
    for value in values:
        try:
            result.append(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _collect_daily(
    db,
    user_ids: list[int],
    start: datetime,
    end: datetime,
) -> dict[int, dict[date, dict[str, Any]]]:
    """Collect per-user, per-day metrics for the requested range."""
    daily: dict[int, dict[date, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(_new_bucket)
    )
    edit_times: dict[tuple[int, int], list[datetime]] = defaultdict(list)

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
    for log in logs:
        uid = int(log.user_id)
        sample_id = int(log.sample_id)
        edit_times[(uid, sample_id)].append(log.created_at)
        if not _is_effective_annotation(log):
            continue
        bucket = daily[uid][log.created_at.date()]
        bucket["work_events"].append(log.created_at)
        bucket["task_ids"].add(int(log.task_id))
        if _is_done_state(log.previous_state):
            bucket["review_approved"].add(sample_id)
        changed = _rect_change_count(log.previous_data, log.new_data)
        if changed:
            bucket["annotation_images"].add(sample_id)
            bucket["annotation_boxes"] += changed

    operations = (
        db.query(SystemOperationLog)
        .filter(
            SystemOperationLog.created_at >= start,
            SystemOperationLog.created_at <= end,
            SystemOperationLog.user_id.in_(user_ids),
            SystemOperationLog.method == "PATCH",
            SystemOperationLog.status_code == 200,
            SystemOperationLog.path.like("%/samples/%"),
        )
        .order_by(SystemOperationLog.created_at.asc())
        .all()
    )
    for operation in operations:
        match = _SAMPLE_SAVE_PATH.search(operation.path or "")
        if match is None:
            continue
        uid = int(operation.user_id)
        sample_id = int(match.group("sample_id"))
        related = edit_times.get((uid, sample_id), [])
        if any(
            abs((timestamp - operation.created_at).total_seconds())
            <= NOOP_MATCH_WINDOW_SECONDS
            for timestamp in related
        ):
            continue
        bucket = daily[uid][operation.created_at.date()]
        if (
            sample_id in bucket["annotation_images"]
            or sample_id in bucket["review_approved"]
        ):
            continue
        bucket["review_images"].add(sample_id)
        bucket["work_events"].append(operation.created_at)
        bucket["task_ids"].add(int(match.group("task_id")))

    deletions = (
        db.query(SystemOperationLog)
        .filter(
            SystemOperationLog.created_at >= start,
            SystemOperationLog.created_at <= end,
            SystemOperationLog.user_id.in_(user_ids),
            SystemOperationLog.method == "DELETE",
            SystemOperationLog.status_code == 200,
            SystemOperationLog.path.like("%/samples"),
        )
        .order_by(SystemOperationLog.created_at.asc())
        .all()
    )
    for operation in deletions:
        match = _SAMPLE_DELETE_PATH.search(operation.path or "")
        if match is None:
            continue
        uid = int(operation.user_id)
        bucket = daily[uid][operation.created_at.date()]
        sample_ids = _deleted_sample_ids(operation.detail)
        if not sample_ids:
            # The operation body is truncated for very large batches; count the
            # operation itself rather than silently dropping the deletion.
            sample_ids = [-(int(operation.id))]
        bucket["deleted_images"].update(sample_ids)
        bucket["task_ids"].add(int(match.group("task_id")))
        bucket["work_events"].append(operation.created_at)

    return daily


def _empty_user_row(
    user: User, *, online: bool, latest_seen: datetime | None
) -> dict[str, Any]:
    return {
        "user_id": int(user.id),
        "username": user.username,
        "online": online,
        "last_seen_at": latest_seen,
        "last_active_at": latest_seen,
        "last_business_at": None,
        "online_seconds": 0,
        "effective_work_seconds": 0,
        "annotation_images": 0,
        "annotation_boxes": 0,
        "review_images": 0,
        "review_approved": 0,
        "deleted_images": 0,
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

    daily = _collect_daily(db, user_ids, start, end)
    latest_work: dict[int, datetime] = {}
    for uid, buckets in daily.items():
        row = rows[uid]
        task_ids: set[int] = set()
        work_events: list[datetime] = []
        for day in sorted(buckets):
            bucket = buckets[day]
            row["annotation_images"] += len(bucket["annotation_images"])
            row["annotation_boxes"] += int(bucket["annotation_boxes"])
            row["review_images"] += len(bucket["review_images"])
            row["review_approved"] += len(bucket["review_approved"])
            row["deleted_images"] += len(bucket["deleted_images"])
            row["effective_work_seconds"] += _cluster_seconds(bucket["work_events"])
            task_ids.update(bucket["task_ids"])
            work_events.extend(bucket["work_events"])
        row["task_count"] = len(task_ids)
        if work_events:
            latest_work[uid] = max(work_events)
            row["last_business_at"] = latest_work[uid]
            if (
                row["last_active_at"] is None
                or row["last_business_at"] > row["last_active_at"]
            ):
                row["last_active_at"] = row["last_business_at"]

    online_today = _today_online_seconds(db, user_ids)
    for uid, seconds in online_today.items():
        if uid in rows:
            rows[uid]["online_seconds"] = seconds

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
        "review_images": sum(item["review_images"] for item in result_rows),
        "review_approved": sum(item["review_approved"] for item in result_rows),
        "deleted_images": sum(item["deleted_images"] for item in result_rows),
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
    daily = _collect_daily(db, [int(user_id)], start, end).get(int(user_id), {})
    daily_rows = []
    for day in sorted(daily):
        bucket = daily[day]
        daily_rows.append(
            {
                "date": day,
                "annotation_images": len(bucket["annotation_images"]),
                "annotation_boxes": int(bucket["annotation_boxes"]),
                "review_images": len(bucket["review_images"]),
                "review_approved": len(bucket["review_approved"]),
                "deleted_images": len(bucket["deleted_images"]),
            }
        )
    return {"user": user_row, "daily": daily_rows}
