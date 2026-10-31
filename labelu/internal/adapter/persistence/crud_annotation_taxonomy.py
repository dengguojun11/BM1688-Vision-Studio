from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session, joinedload

from labelu.internal.common.sql_like import LIKE_ESCAPE, literal_like_pattern
from labelu.internal.domain.models.annotation_taxonomy import (
    GlobalTagDimension,
    GlobalTagOption,
    LabelPreset,
    LabelPresetItem,
)


# --- Label presets ---


def label_preset_list(
    db: Session,
    page: int = 0,
    size: int = 100,
    include_deleted: bool = False,
    search: str | None = None,
) -> Tuple[List[LabelPreset], int]:
    q = db.query(LabelPreset).options(joinedload(LabelPreset.items))
    if not include_deleted:
        q = q.filter(LabelPreset.deleted_at.is_(None))
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        # Include the preset's own metadata and its legacy item vocabulary.
        # DISTINCT is required because one preset can match several items.
        q = q.outerjoin(
            LabelPresetItem,
            LabelPresetItem.preset_id == LabelPreset.id,
        ).filter(
            or_(
                cast(LabelPreset.id, String).ilike(pattern, escape=LIKE_ESCAPE),
                cast(LabelPreset.sort_order, String).ilike(
                    pattern, escape=LIKE_ESCAPE
                ),
                LabelPreset.slug.ilike(pattern, escape=LIKE_ESCAPE),
                LabelPreset.name.ilike(pattern, escape=LIKE_ESCAPE),
                LabelPreset.description.ilike(pattern, escape=LIKE_ESCAPE),
                LabelPreset.training_profile.ilike(pattern, escape=LIKE_ESCAPE),
                LabelPresetItem.label_key.ilike(pattern, escape=LIKE_ESCAPE),
                LabelPresetItem.label_value.ilike(pattern, escape=LIKE_ESCAPE),
            )
        ).distinct()
    total = (
        q.with_entities(func.count(func.distinct(LabelPreset.id))).scalar() or 0
    )
    rows = (
        q.order_by(LabelPreset.sort_order, LabelPreset.id)
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total


def label_preset_get(db: Session, preset_id: int) -> Optional[LabelPreset]:
    return (
        db.query(LabelPreset)
        .options(joinedload(LabelPreset.items))
        .filter(LabelPreset.id == preset_id, LabelPreset.deleted_at.is_(None))
        .first()
    )


def label_preset_get_by_slug(db: Session, slug: str) -> Optional[LabelPreset]:
    return (
        db.query(LabelPreset)
        .options(joinedload(LabelPreset.items))
        .filter(LabelPreset.slug == slug, LabelPreset.deleted_at.is_(None))
        .first()
    )


def label_preset_get_by_slug_including_deleted(
    db: Session, slug: str
) -> Optional[LabelPreset]:
    return label_preset_find_by_slug_ci(db, slug, active_only=False)


def label_preset_find_by_slug_ci(
    db: Session, slug: str, *, active_only: bool = False
) -> Optional[LabelPreset]:
    """按 slug 不区分大小写查找（含软删除，除非 active_only）。"""
    key = (slug or "").strip().lower()
    if not key:
        return None
    q = (
        db.query(LabelPreset)
        .options(joinedload(LabelPreset.items))
        .filter(func.lower(LabelPreset.slug) == key)
    )
    if active_only:
        q = q.filter(LabelPreset.deleted_at.is_(None))
    return q.order_by(LabelPreset.id.asc()).first()


def label_preset_create(
    db: Session,
    slug: str,
    name: str,
    description: Optional[str],
    sort_order: int,
    items: list[dict],
    training_profile: Optional[str] = None,
    training_profile_config: Optional[str] = None,
) -> LabelPreset:
    now = datetime.now()
    p = LabelPreset(
        slug=slug,
        name=name,
        description=description,
        training_profile=training_profile,
        training_profile_config=training_profile_config,
        sort_order=sort_order,
        created_at=now,
        updated_at=now,
    )
    db.add(p)
    db.flush()
    for it in items:
        db.add(
            LabelPresetItem(
                preset_id=p.id,
                label_key=it["label_key"],
                label_value=it["label_value"],
                color=it["color"],
                annotation_kind=it.get("annotation_kind", "bbox"),
                sort_order=it.get("sort_order", 0),
                created_at=now,
            )
        )
    db.flush()
    db.refresh(p)
    return p


def label_preset_update(
    db: Session,
    db_obj: LabelPreset,
    name: Optional[str] = None,
    description: Optional[str] = None,
    sort_order: Optional[int] = None,
    replace_items: Optional[list[dict]] = None,
    training_profile: Optional[str] = None,
    training_profile_config: Optional[str] = None,
    update_training_profile: bool = False,
) -> LabelPreset:
    if name is not None:
        db_obj.name = name.strip() if isinstance(name, str) else name
    if description is not None:
        db_obj.description = description
    if sort_order is not None:
        db_obj.sort_order = sort_order
    if update_training_profile:
        db_obj.training_profile = training_profile
        db_obj.training_profile_config = training_profile_config
    db_obj.updated_at = datetime.now()

    if replace_items is not None:
        db.query(LabelPresetItem).filter(LabelPresetItem.preset_id == db_obj.id).delete(
            synchronize_session=False
        )
        now = datetime.now()
        for it in replace_items:
            db.add(
                LabelPresetItem(
                    preset_id=db_obj.id,
                    label_key=it["label_key"],
                    label_value=it["label_value"],
                    color=it["color"],
                    annotation_kind=it.get("annotation_kind", "bbox"),
                    sort_order=it.get("sort_order", 0),
                    created_at=now,
                )
            )
    db.add(db_obj)
    db.flush()
    db.refresh(db_obj)
    return db_obj


def label_preset_hard_delete(db: Session, db_obj: LabelPreset) -> None:
    """物理删除预设及其子项（ORM cascade，非软删除）。"""
    db.delete(db_obj)
    db.flush()


def purge_soft_deleted_label_presets(db: Session) -> int:
    """清理历史软删除行，返回删除的预设条数。"""
    tombstones = (
        db.query(LabelPreset.id).filter(LabelPreset.deleted_at.isnot(None)).all()
    )
    ids = [row[0] for row in tombstones]
    if not ids:
        return 0
    db.query(LabelPresetItem).filter(LabelPresetItem.preset_id.in_(ids)).delete(
        synchronize_session=False
    )
    return db.query(LabelPreset).filter(LabelPreset.id.in_(ids)).delete(
        synchronize_session=False
    )


# --- Global tag dimensions ---


def global_dimension_list(
    db: Session,
    page: int = 0,
    size: int = 100,
    include_deleted: bool = False,
    search: str | None = None,
) -> Tuple[List[GlobalTagDimension], int]:
    q = db.query(GlobalTagDimension).options(joinedload(GlobalTagDimension.options))
    if not include_deleted:
        q = q.filter(GlobalTagDimension.deleted_at.is_(None))
    keyword = str(search or "").strip()
    if keyword:
        pattern = literal_like_pattern(keyword)
        # Search both the dimension and its option vocabulary.  The explicit
        # outer join keeps dimensions with no options searchable by their own
        # Chinese/English fields, while DISTINCT prevents an option match from
        # inflating the total or producing duplicate dimensions.
        q = q.outerjoin(
            GlobalTagOption,
            GlobalTagOption.dimension_id == GlobalTagDimension.id,
        ).filter(
            or_(
                GlobalTagDimension.field_key.ilike(pattern, escape=LIKE_ESCAPE),
                GlobalTagDimension.name_cn.ilike(pattern, escape=LIKE_ESCAPE),
                GlobalTagOption.name_cn.ilike(pattern, escape=LIKE_ESCAPE),
                GlobalTagOption.value_en.ilike(pattern, escape=LIKE_ESCAPE),
            )
        ).distinct()
    total = q.with_entities(func.count(func.distinct(GlobalTagDimension.id))).scalar() or 0
    rows = (
        q.order_by(GlobalTagDimension.sort_order, GlobalTagDimension.id)
        .offset(page * size)
        .limit(size)
        .all()
    )
    return rows, total


def global_dimension_get(db: Session, dimension_id: int) -> Optional[GlobalTagDimension]:
    return (
        db.query(GlobalTagDimension)
        .options(joinedload(GlobalTagDimension.options))
        .filter(
            GlobalTagDimension.id == dimension_id,
            GlobalTagDimension.deleted_at.is_(None),
        )
        .first()
    )


def global_dimension_get_by_field_key(
    db: Session, field_key: str
) -> Optional[GlobalTagDimension]:
    return (
        db.query(GlobalTagDimension)
        .options(joinedload(GlobalTagDimension.options))
        .filter(
            GlobalTagDimension.field_key == field_key,
            GlobalTagDimension.deleted_at.is_(None),
        )
        .first()
    )


def global_dimension_create(
    db: Session,
    field_key: str,
    name_cn: str,
    sort_order: int,
    options: list[dict],
) -> GlobalTagDimension:
    now = datetime.now()
    d = GlobalTagDimension(
        field_key=field_key,
        name_cn=name_cn,
        sort_order=sort_order,
        created_at=now,
        updated_at=now,
    )
    db.add(d)
    db.flush()
    for opt in options:
        db.add(
            GlobalTagOption(
                dimension_id=d.id,
                name_cn=opt["name_cn"],
                value_en=opt["value_en"],
                sort_order=opt.get("sort_order", 0),
                created_at=now,
            )
        )
    db.flush()
    db.refresh(d)
    return d


def global_dimension_update(
    db: Session,
    db_obj: GlobalTagDimension,
    name_cn: Optional[str] = None,
    sort_order: Optional[int] = None,
    replace_options: Optional[list[dict]] = None,
) -> GlobalTagDimension:
    if name_cn is not None:
        db_obj.name_cn = name_cn
    if sort_order is not None:
        db_obj.sort_order = sort_order
    db_obj.updated_at = datetime.now()

    if replace_options is not None:
        db.query(GlobalTagOption).filter(
            GlobalTagOption.dimension_id == db_obj.id
        ).delete(synchronize_session=False)
        now = datetime.now()
        for opt in replace_options:
            db.add(
                GlobalTagOption(
                    dimension_id=db_obj.id,
                    name_cn=opt["name_cn"],
                    value_en=opt["value_en"],
                    sort_order=opt.get("sort_order", 0),
                    created_at=now,
                )
            )
    db.add(db_obj)
    db.flush()
    db.refresh(db_obj)
    return db_obj


def global_dimension_disable(db: Session, db_obj: GlobalTagDimension) -> None:
    """停用维度并保留历史配置引用。"""
    db_obj.deleted_at = datetime.now()
    db_obj.updated_at = datetime.now()
    db.add(db_obj)
    db.flush()


def purge_soft_deleted_global_dimensions(db: Session) -> int:
    tombstones = (
        db.query(GlobalTagDimension.id)
        .filter(GlobalTagDimension.deleted_at.isnot(None))
        .all()
    )
    ids = [row[0] for row in tombstones]
    if not ids:
        return 0
    db.query(GlobalTagOption).filter(GlobalTagOption.dimension_id.in_(ids)).delete(
        synchronize_session=False
    )
    return db.query(GlobalTagDimension).filter(GlobalTagDimension.id.in_(ids)).delete(
        synchronize_session=False
    )


def global_dimensions_all_active(db: Session) -> List[GlobalTagDimension]:
    return (
        db.query(GlobalTagDimension)
        .options(joinedload(GlobalTagDimension.options))
        .filter(GlobalTagDimension.deleted_at.is_(None))
        .order_by(GlobalTagDimension.sort_order, GlobalTagDimension.id)
        .all()
    )
