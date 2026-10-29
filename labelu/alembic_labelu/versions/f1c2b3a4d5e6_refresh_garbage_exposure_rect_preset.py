"""sync garbageExposure rect preset to canonical garbage_street_* / object_box / non_garbage / garbage_bulky

Revision ID: f1c2b3a4d5e6
Revises: f0e1d2c3b4a5
Create Date: 2026-05-14

与 d4e5f6a7b8c0 中 _GARBAGE_ITEMS 一致；已存在库会清空该预设下旧条目后重写。
若预设被误删则重新创建。
"""
from __future__ import annotations

from datetime import datetime

from alembic import op
from sqlalchemy.orm import sessionmaker

revision = "f1c2b3a4d5e6"
down_revision = "f0e1d2c3b4a5"
branch_labels = None
depends_on = None

_SLUG = "garbageExposure"

_ROWS: list[tuple[str, str, str, int]] = [
    ("垃圾堆", "garbage_street_dump", "#E74C3C", 0),
    ("打包垃圾", "garbage_street_pack", "#3498DB", 1),
    ("零散垃圾", "garbage_street_scattered", "#2ECC71", 2),
    ("纸皮箱子", "object_box", "#F39C12", 3),
    ("瓶罐", "garbage_street_can", "#9B59B6", 4),
    ("非垃圾类", "non_garbage", "#1ABC9C", 5),
    ("大件垃圾", "garbage_bulky", "#E67E22", 6),
]


def upgrade() -> None:
    conn = op.get_bind()
    Session = sessionmaker(bind=conn)
    session = Session()
    try:
        from labelu.internal.domain.models.annotation_taxonomy import (
            LabelPreset,
            LabelPresetItem,
        )

        now = datetime.now()
        p = session.query(LabelPreset).filter(LabelPreset.slug == _SLUG).first()
        if not p:
            p = LabelPreset(
                slug=_SLUG,
                name="垃圾暴露",
                description="垃圾暴露拉框：与 YOLO/导出类名 garbage_street_*、object_box、non_garbage、garbage_bulky 对齐",
                sort_order=0,
                created_at=now,
                updated_at=now,
            )
            session.add(p)
            session.flush()
        else:
            p.description = (
                "垃圾暴露拉框：与 YOLO/导出类名 garbage_street_*、object_box、non_garbage、garbage_bulky 对齐"
            )
            p.updated_at = now

        session.query(LabelPresetItem).filter(LabelPresetItem.preset_id == p.id).delete(
            synchronize_session=False
        )
        for lk, lv, color, so in _ROWS:
            session.add(
                LabelPresetItem(
                    preset_id=p.id,
                    label_key=lk,
                    label_value=lv,
                    color=color,
                    sort_order=so,
                    created_at=now,
                )
            )
        session.flush()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def downgrade() -> None:
    """无可逆快照；若需旧数据请从备份库恢复。"""
    pass
