from typing import List, Optional, Tuple

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.domain.models.yolo_imported_model import YoloImportedModel
from labelu.internal.domain.models.user import User


def create(
    db: Session,
    *,
    user_id: int,
    display_name: str,
    category: str,
    original_filename: str,
    weights_rel_path: str,
    file_size: int,
) -> YoloImportedModel:
    row = YoloImportedModel(
        user_id=user_id,
        display_name=display_name,
        category=category or "",
        original_filename=original_filename,
        weights_rel_path=weights_rel_path,
        file_size=file_size,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get_by_id(db: Session, model_id: int) -> Optional[YoloImportedModel]:
    return db.query(YoloImportedModel).filter(YoloImportedModel.id == model_id).first()


def get_for_user(db: Session, model_id: int, user_id: int) -> Optional[YoloImportedModel]:
    return (
        db.query(YoloImportedModel)
        .filter(YoloImportedModel.id == model_id, YoloImportedModel.user_id == user_id)
        .first()
    )


def public_owner_ids(db: Session) -> list[int]:
    """Return user ids whose imported models are shared platform-wide."""
    return [
        int(user.id)
        for user in db.query(User).all()
        if user.id is not None and is_task_superuser(user)
    ]


def get_available_for_user(
    db: Session,
    model_id: int,
    user_id: int,
    *,
    public_owner_ids: Optional[list[int]] = None,
) -> Optional[YoloImportedModel]:
    allowed_owner_ids = [int(item) for item in (public_owner_ids or []) if item is not None]
    owner_filter = [YoloImportedModel.user_id == user_id]
    if allowed_owner_ids:
        owner_filter.append(YoloImportedModel.user_id.in_(allowed_owner_ids))
    return (
        db.query(YoloImportedModel)
        .filter(YoloImportedModel.id == model_id, or_(*owner_filter))
        .first()
    )


def delete_row(db: Session, row: YoloImportedModel) -> None:
    db.delete(row)
    db.flush()


def list_for_user(
    db: Session,
    user_id: int,
    *,
    search: Optional[str] = None,
    name: Optional[str] = None,
    category: Optional[str] = None,
    page: int = 0,
    size: int = 20,
    list_all_users_models: bool = False,
    public_owner_ids: Optional[list[int]] = None,
) -> Tuple[List[YoloImportedModel], int]:
    q = db.query(YoloImportedModel)
    if not list_all_users_models:
        owner_filter = [YoloImportedModel.user_id == user_id]
        allowed_owner_ids = [
            int(item) for item in (public_owner_ids or []) if item is not None
        ]
        if allowed_owner_ids:
            owner_filter.append(YoloImportedModel.user_id.in_(allowed_owner_ids))
        q = q.filter(or_(*owner_filter))
    normalized_name = str(search or name or "").strip()
    if normalized_name:
        term = literal_like_pattern(normalized_name)
        q = q.filter(
            or_(
                cast(YoloImportedModel.id, String).ilike(
                    term, escape=LIKE_ESCAPE
                ),
                YoloImportedModel.display_name.ilike(term, escape=LIKE_ESCAPE),
                YoloImportedModel.original_filename.ilike(term, escape=LIKE_ESCAPE),
                YoloImportedModel.category.ilike(term, escape=LIKE_ESCAPE),
            )
        )
    if category is not None and str(category).strip():
        q = q.filter(
            YoloImportedModel.category.ilike(
                literal_like_pattern(str(category).strip()), escape=LIKE_ESCAPE
            )
        )
    total = q.count()
    rows = (
        q.order_by(YoloImportedModel.id.desc())
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total
