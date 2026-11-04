"""Presence heartbeat and session helpers used by personnel statistics."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import or_

from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.user_activity_session import UserActivitySession

ONLINE_WINDOW_SECONDS = 120
HEARTBEAT_INTERVAL_SECONDS = 60


def normalize_session_id(value: str | None) -> str:
    text = str(value or "").strip()
    return text[:128] if text else uuid.uuid4().hex


def record_heartbeat(
    db,
    *,
    user: User,
    session_id: str | None,
    path: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    active_seconds: int = 0,
) -> UserActivitySession:
    now = datetime.now()
    today = now.date()
    normalized = normalize_session_id(session_id)
    try:
        delta = max(0, min(int(active_seconds or 0), 300))
    except (TypeError, ValueError):
        delta = 0
    row = (
        db.query(UserActivitySession)
        .filter(
            UserActivitySession.user_id == int(user.id),
            UserActivitySession.session_id == normalized,
        )
        .first()
    )
    with begin_transaction(db):
        if row is None:
            row = UserActivitySession(
                user_id=int(user.id),
                session_id=normalized,
                login_at=now,
                last_seen_at=now,
                last_path=(path or "")[:512] or None,
                ip=(ip or "")[:64] or None,
                user_agent=(user_agent or "")[:512] or None,
                active_seconds=delta,
                active_date=today,
                updated_at=now,
            )
            db.add(row)
        else:
            if row.active_date != today:
                row.active_seconds = 0
                row.active_date = today
            row.active_seconds = min(
                24 * 3600, int(row.active_seconds or 0) + delta
            )
            row.last_seen_at = now
            row.last_path = (path or "")[:512] or None
            row.ip = (ip or "")[:64] or None
            row.user_agent = (user_agent or "")[:512] or None
            row.logout_at = None
            row.updated_at = now
        db.flush()
    return row


def mark_logout(db, *, user: User, session_id: str | None) -> None:
    if not session_id:
        return
    row = (
        db.query(UserActivitySession)
        .filter(
            UserActivitySession.user_id == int(user.id),
            UserActivitySession.session_id == str(session_id)[:128],
        )
        .first()
    )
    if row is None:
        return
    now = datetime.now()
    with begin_transaction(db):
        row.logout_at = now
        row.last_seen_at = now
        row.updated_at = now


def online_cutoff(now: datetime | None = None) -> datetime:
    return (now or datetime.now()) - timedelta(seconds=ONLINE_WINDOW_SECONDS)


def online_users(db, *, now: datetime | None = None) -> list[dict]:
    cutoff = online_cutoff(now)
    rows = (
        db.query(UserActivitySession, User)
        .join(User, User.id == UserActivitySession.user_id)
        .filter(
            UserActivitySession.last_seen_at >= cutoff,
            UserActivitySession.logout_at.is_(None),
        )
        .order_by(UserActivitySession.last_seen_at.desc())
        .all()
    )
    seen: set[int] = set()
    result: list[dict] = []
    for session, user in rows:
        uid = int(user.id)
        if uid in seen:
            continue
        seen.add(uid)
        result.append(
            {
                "user_id": uid,
                "username": user.username,
                "last_seen_at": session.last_seen_at,
                "login_at": session.login_at,
                "last_path": session.last_path,
                "ip": session.ip,
                "session_id": session.session_id,
            }
        )
    return result


def user_latest_sessions(db, user_ids: Iterable[int]) -> dict[int, UserActivitySession]:
    ids = [int(value) for value in user_ids if value is not None]
    if not ids:
        return {}
    rows = (
        db.query(UserActivitySession)
        .filter(UserActivitySession.user_id.in_(ids))
        .order_by(
            UserActivitySession.user_id.asc(),
            UserActivitySession.last_seen_at.desc(),
        )
        .all()
    )
    result: dict[int, UserActivitySession] = {}
    for row in rows:
        result.setdefault(int(row.user_id), row)
    return result


def overlap_seconds(
    row: UserActivitySession,
    *,
    date_from: datetime,
    date_to: datetime,
) -> int:
    start = max(row.login_at or date_from, date_from)
    end = min(row.last_seen_at or start, date_to)
    return max(0, int((end - start).total_seconds()))


def active_session_user_ids(
    db,
    *,
    date_from: datetime,
    date_to: datetime,
    user_ids: Iterable[int] | None = None,
) -> set[int]:
    query = db.query(UserActivitySession.user_id).filter(
        UserActivitySession.login_at <= date_to,
        UserActivitySession.last_seen_at >= date_from,
        or_(
            UserActivitySession.logout_at.is_(None),
            UserActivitySession.logout_at >= date_from,
        ),
    )
    ids = [int(value) for value in (user_ids or []) if value is not None]
    if ids:
        query = query.filter(UserActivitySession.user_id.in_(ids))
    return {int(row[0]) for row in query.all()}
