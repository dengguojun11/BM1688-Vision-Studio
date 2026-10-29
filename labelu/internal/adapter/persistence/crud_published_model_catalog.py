from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.published_model_catalog import (
    PublishedModelCatalog,
    PublishedModelIdentifierAlias,
)


def get(db: Session, catalog_id: int) -> Optional[PublishedModelCatalog]:
    return (
        db.query(PublishedModelCatalog)
        .options(joinedload(PublishedModelCatalog.aliases))
        .filter(
            PublishedModelCatalog.id == int(catalog_id),
            PublishedModelCatalog.deleted_at.is_(None),
        )
        .first()
    )


def get_by_public_model_id(
    db: Session, public_model_id: str
) -> Optional[PublishedModelCatalog]:
    return (
        db.query(PublishedModelCatalog)
        .options(joinedload(PublishedModelCatalog.aliases))
        .filter(
            PublishedModelCatalog.public_model_id == str(public_model_id).strip(),
            PublishedModelCatalog.deleted_at.is_(None),
        )
        .first()
    )


def get_by_aicm_model_set_id(
    db: Session,
    model_set_id: int,
    *,
    environment_id: Optional[str] = None,
) -> Optional[PublishedModelCatalog]:
    query = (
        db.query(PublishedModelCatalog)
        .options(joinedload(PublishedModelCatalog.aliases))
        .filter(
            PublishedModelCatalog.aicm_model_set_id == int(model_set_id),
            PublishedModelCatalog.deleted_at.is_(None),
        )
    )
    if environment_id:
        query = query.filter(
            PublishedModelCatalog.aicm_environment_id == str(environment_id).strip()
        )
    return query.order_by(PublishedModelCatalog.id.desc()).first()


def get_by_alias(
    db: Session,
    external_model_id: str,
    *,
    source_system: Optional[str] = None,
) -> Optional[PublishedModelCatalog]:
    query = (
        db.query(PublishedModelCatalog)
        .join(
            PublishedModelIdentifierAlias,
            PublishedModelIdentifierAlias.catalog_id == PublishedModelCatalog.id,
        )
        .options(joinedload(PublishedModelCatalog.aliases))
        .filter(
            PublishedModelIdentifierAlias.external_model_id
            == str(external_model_id).strip(),
            PublishedModelCatalog.deleted_at.is_(None),
        )
    )
    if source_system:
        query = query.filter(
            PublishedModelIdentifierAlias.source_system
            == str(source_system).strip().lower()
        )
    return query.order_by(PublishedModelCatalog.id.desc()).first()


def list_by_alias(
    db: Session,
    external_model_id: str,
    *,
    source_system: Optional[str] = None,
) -> list[PublishedModelCatalog]:
    query = (
        db.query(PublishedModelCatalog)
        .join(
            PublishedModelIdentifierAlias,
            PublishedModelIdentifierAlias.catalog_id == PublishedModelCatalog.id,
        )
        .options(joinedload(PublishedModelCatalog.aliases))
        .filter(
            PublishedModelIdentifierAlias.external_model_id
            == str(external_model_id).strip(),
            PublishedModelCatalog.deleted_at.is_(None),
        )
    )
    if source_system:
        query = query.filter(
            PublishedModelIdentifierAlias.source_system
            == str(source_system).strip().lower()
        )
    return query.order_by(PublishedModelCatalog.id.desc()).all()


def resolve_identifier(
    db: Session,
    identifier: str,
    *,
    source_system: Optional[str] = None,
) -> Optional[PublishedModelCatalog]:
    text = str(identifier or "").strip()
    if not text:
        return None
    row = get_by_alias(db, text, source_system=source_system)
    if row:
        return row
    row = get_by_public_model_id(db, text)
    if row:
        return row
    if text.isdigit():
        return get_by_aicm_model_set_id(db, int(text))
    return None


def list_rows(
    db: Session,
    *,
    page: int,
    size: int,
    query_text: str = "",
    export_id: Optional[int] = None,
    yolo_train_job_id: Optional[int] = None,
    status: str = "",
    availability_status: str = "",
    source_type: str = "",
    is_public: Optional[bool] = None,
    download_enabled: Optional[bool] = None,
    downloadable_only: bool = False,
) -> tuple[list[PublishedModelCatalog], int]:
    query = (
        db.query(PublishedModelCatalog)
        .options(joinedload(PublishedModelCatalog.aliases))
        .filter(PublishedModelCatalog.deleted_at.is_(None))
    )
    text = str(query_text or "").strip()
    if text:
        like = literal_like_pattern(text)
        alias_exists = (
            db.query(PublishedModelIdentifierAlias.id)
            .filter(
                PublishedModelIdentifierAlias.catalog_id == PublishedModelCatalog.id,
                PublishedModelIdentifierAlias.external_model_id.ilike(
                    like, escape=LIKE_ESCAPE
                ),
            )
            .exists()
        )
    if export_id is not None:
        query = query.filter(PublishedModelCatalog.export_id == int(export_id))
    if yolo_train_job_id is not None:
        query = query.filter(
            PublishedModelCatalog.yolo_train_job_id == int(yolo_train_job_id)
        )
    if text:
        query = query.filter(
            or_(
                PublishedModelCatalog.public_model_id.ilike(
                    like, escape=LIKE_ESCAPE
                ),
                PublishedModelCatalog.name.ilike(like, escape=LIKE_ESCAPE),
                PublishedModelCatalog.display_name.ilike(
                    like, escape=LIKE_ESCAPE
                ),
                alias_exists,
            )
        )
    if status:
        query = query.filter(PublishedModelCatalog.status == status)
    if availability_status:
        query = query.filter(
            PublishedModelCatalog.availability_status == availability_status
        )
    if source_type:
        query = query.filter(PublishedModelCatalog.source_type == source_type)
    if is_public is not None:
        query = query.filter(PublishedModelCatalog.is_public.is_(bool(is_public)))
    if download_enabled is not None:
        query = query.filter(
            PublishedModelCatalog.download_enabled.is_(bool(download_enabled))
        )
    if downloadable_only:
        query = query.filter(
            or_(
                PublishedModelCatalog.package_rel_path.isnot(None),
                PublishedModelCatalog.availability_status == "available",
            )
        )
    total = query.count()
    rows = (
        query.order_by(
            PublishedModelCatalog.published_at.desc(),
            PublishedModelCatalog.id.desc(),
        )
        .offset(max(0, int(page)) * max(1, int(size)))
        .limit(max(1, int(size)))
        .all()
    )
    return rows, total


def create(db: Session, **values) -> PublishedModelCatalog:
    row = PublishedModelCatalog(**values)
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def update(db: Session, row: PublishedModelCatalog, **values) -> PublishedModelCatalog:
    for key, value in values.items():
        setattr(row, key, value)
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def compare_and_set_download_enabled(
    db: Session,
    *,
    catalog_id: int,
    expected: bool,
    value: bool,
) -> Optional[PublishedModelCatalog]:
    """Atomically update the download switch when the caller's view is current."""

    changed = (
        db.query(PublishedModelCatalog)
        .filter(
            PublishedModelCatalog.id == int(catalog_id),
            PublishedModelCatalog.deleted_at.is_(None),
            PublishedModelCatalog.download_enabled.is_(bool(expected)),
        )
        .update(
            {PublishedModelCatalog.download_enabled: bool(value)},
            synchronize_session="fetch",
        )
    )
    if int(changed) != 1:
        return None
    db.flush()
    return get(db, catalog_id)


def get_alias(
    db: Session, source_system: str, external_model_id: str
) -> Optional[PublishedModelIdentifierAlias]:
    return (
        db.query(PublishedModelIdentifierAlias)
        .filter(
            PublishedModelIdentifierAlias.source_system
            == str(source_system).strip().lower(),
            PublishedModelIdentifierAlias.external_model_id
            == str(external_model_id).strip(),
        )
        .first()
    )


def create_alias(
    db: Session,
    *,
    catalog_id: int,
    source_system: str,
    external_model_id: str,
    alias_type: str,
    created_by: Optional[int],
) -> PublishedModelIdentifierAlias:
    row = PublishedModelIdentifierAlias(
        catalog_id=int(catalog_id),
        source_system=str(source_system).strip().lower(),
        external_model_id=str(external_model_id).strip(),
        alias_type=str(alias_type).strip().lower(),
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def delete_alias(db: Session, catalog_id: int, alias_id: int) -> bool:
    count = (
        db.query(PublishedModelIdentifierAlias)
        .filter(
            PublishedModelIdentifierAlias.id == int(alias_id),
            PublishedModelIdentifierAlias.catalog_id == int(catalog_id),
        )
        .delete(synchronize_session=False)
    )
    db.flush()
    return int(count) > 0
