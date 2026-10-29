from typing import Optional

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.tagtool_classification_model import (
    TagtoolClassificationModel,
)


def create(
    db: Session,
    *,
    user_id: int,
    display_name: str,
    category: str,
    original_filename: str,
    weights_rel_path: str,
    label_map_json: str,
    file_size: int,
) -> TagtoolClassificationModel:
    row = TagtoolClassificationModel(
        user_id=user_id,
        display_name=display_name,
        category=category or "",
        original_filename=original_filename,
        weights_rel_path=weights_rel_path,
        label_map_json=label_map_json,
        file_size=file_size,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def get_by_id(db: Session, model_id: int) -> Optional[TagtoolClassificationModel]:
    return (
        db.query(TagtoolClassificationModel)
        .filter(TagtoolClassificationModel.id == model_id)
        .first()
    )


def get_for_user(
    db: Session, *, model_id: int, user_id: int
) -> Optional[TagtoolClassificationModel]:
    return (
        db.query(TagtoolClassificationModel)
        .filter(
            TagtoolClassificationModel.id == model_id,
            TagtoolClassificationModel.user_id == user_id,
        )
        .first()
    )


def list_for_user(
    db: Session,
    user_id: int,
    *,
    name: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 0,
    size: int = 20,
    list_all_users_models: bool = False,
) -> tuple[list[TagtoolClassificationModel], int]:
    q = db.query(TagtoolClassificationModel)
    if not list_all_users_models:
        q = q.filter(TagtoolClassificationModel.user_id == user_id)
    if name is not None and str(name).strip():
        term = literal_like_pattern(str(name).strip())
        q = q.filter(
            or_(
                TagtoolClassificationModel.display_name.ilike(
                    term, escape=LIKE_ESCAPE
                ),
                TagtoolClassificationModel.original_filename.ilike(
                    term, escape=LIKE_ESCAPE
                ),
            )
        )
    if category is not None and str(category).strip():
        q = q.filter(
            TagtoolClassificationModel.category.ilike(
                literal_like_pattern(str(category).strip()), escape=LIKE_ESCAPE
            )
        )
    normalized_search = str(search or "").strip()
    if normalized_search:
        term = literal_like_pattern(normalized_search)
        q = q.filter(
            or_(
                cast(TagtoolClassificationModel.id, String).ilike(
                    term, escape=LIKE_ESCAPE
                ),
                TagtoolClassificationModel.display_name.ilike(
                    term, escape=LIKE_ESCAPE
                ),
                TagtoolClassificationModel.category.ilike(
                    term, escape=LIKE_ESCAPE
                ),
                TagtoolClassificationModel.original_filename.ilike(
                    term, escape=LIKE_ESCAPE
                ),
                TagtoolClassificationModel.label_map_json.ilike(
                    term, escape=LIKE_ESCAPE
                ),
            )
        )
    total = q.count()
    rows = (
        q.order_by(TagtoolClassificationModel.id.desc())
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total


def delete_row(db: Session, row: TagtoolClassificationModel) -> None:
    db.delete(row)
    db.flush()
