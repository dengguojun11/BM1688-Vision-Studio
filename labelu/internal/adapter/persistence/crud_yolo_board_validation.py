import os
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

from sqlalchemy import desc, func, or_
from sqlalchemy.orm import Session

from labelu.internal.domain.models.yolo_board_validation import YoloBoardValidation


def create_for_export(
    db: Session,
    *,
    export_id: int,
    task_id: int,
    yolo_train_job_id: int,
) -> YoloBoardValidation:
    row = YoloBoardValidation(
        export_id=export_id,
        task_id=task_id,
        yolo_train_job_id=yolo_train_job_id,
        status="PENDING",
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get(db: Session, validation_id: int) -> Optional[YoloBoardValidation]:
    return (
        db.query(YoloBoardValidation)
        .filter(YoloBoardValidation.id == validation_id)
        .first()
    )


def get_latest_for_export(
    db: Session, *, export_id: int
) -> Optional[YoloBoardValidation]:
    return (
        db.query(YoloBoardValidation)
        .filter(YoloBoardValidation.export_id == export_id)
        .order_by(desc(YoloBoardValidation.id))
        .limit(1)
        .first()
    )


def latest_by_export_ids(
    db: Session, *, export_ids: Iterable[int]
) -> Dict[int, YoloBoardValidation]:
    normalized_ids = sorted({int(export_id) for export_id in export_ids if export_id})
    if not normalized_ids:
        return {}
    latest_ids = (
        db.query(func.max(YoloBoardValidation.id).label("id"))
        .filter(YoloBoardValidation.export_id.in_(normalized_ids))
        .group_by(YoloBoardValidation.export_id)
        .subquery()
    )
    rows = (
        db.query(YoloBoardValidation)
        .join(latest_ids, YoloBoardValidation.id == latest_ids.c.id)
        .all()
    )
    return {int(row.export_id): row for row in rows}


def delete_all_for_export(db: Session, *, export_id: int) -> int:
    n = (
        db.query(YoloBoardValidation)
        .filter(YoloBoardValidation.export_id == export_id)
        .delete(synchronize_session=False)
    )
    db.flush()
    return int(n)


def delete_all_for_job(db: Session, *, yolo_train_job_id: int) -> int:
    n = (
        db.query(YoloBoardValidation)
        .filter(YoloBoardValidation.yolo_train_job_id == yolo_train_job_id)
        .delete(synchronize_session=False)
    )
    db.flush()
    return int(n)


def has_active_for_export(db: Session, *, export_id: int) -> bool:
    return (
        db.query(YoloBoardValidation)
        .filter(
            YoloBoardValidation.export_id == export_id,
            YoloBoardValidation.status.in_(("PENDING", "PROCESSING")),
        )
        .first()
        is not None
    )


def _stale_processing_cutoff() -> datetime:
    """PROCESSING 超过该时长视为板端掉线，允许重新领取。"""
    minutes = int(os.environ.get("BOARD_AGENT_STALE_MINUTES", "15"))
    return datetime.now() - timedelta(minutes=max(5, minutes))


def claim_next_pending(db: Session) -> Optional[YoloBoardValidation]:
    """Atomically claim one pending or stale job across concurrent agents."""
    for status_value, stale_cutoff in (
        ("PENDING", None),
        ("PROCESSING", _stale_processing_cutoff()),
    ):
        while True:
            query = db.query(YoloBoardValidation.id).filter(
                YoloBoardValidation.status == status_value
            )
            if stale_cutoff is not None:
                query = query.filter(
                    or_(
                        YoloBoardValidation.claimed_at.is_(None),
                        YoloBoardValidation.claimed_at < stale_cutoff,
                    )
                )
            candidate = query.order_by(YoloBoardValidation.id).first()
            if candidate is None:
                break
            candidate_id = int(candidate[0])

            claimed_at = datetime.now()
            update_query = db.query(YoloBoardValidation).filter(
                YoloBoardValidation.id == candidate_id,
                YoloBoardValidation.status == status_value,
            )
            if stale_cutoff is not None:
                update_query = update_query.filter(
                    or_(
                        YoloBoardValidation.claimed_at.is_(None),
                        YoloBoardValidation.claimed_at < stale_cutoff,
                    )
                )
            updated = update_query.update(
                {
                    YoloBoardValidation.status: "PROCESSING",
                    YoloBoardValidation.claimed_at: claimed_at,
                    YoloBoardValidation.updated_at: claimed_at,
                },
                synchronize_session=False,
            )
            db.flush()
            if updated == 1:
                return get(db, candidate_id)
            db.expire_all()
    return None


def update_row(db: Session, row: YoloBoardValidation, **kwargs) -> YoloBoardValidation:
    for k, v in kwargs.items():
        setattr(row, k, v)
    row.updated_at = datetime.now()
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def list_recent(
    db: Session, *, limit: int = 20
) -> List[YoloBoardValidation]:
    return (
        db.query(YoloBoardValidation)
        .order_by(desc(YoloBoardValidation.id))
        .limit(max(1, int(limit)))
        .all()
    )
