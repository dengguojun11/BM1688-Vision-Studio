"""refresh streetVendorOccupation rect preset items (YOLO-aligned keys)

Revision ID: f0e1d2c3b4a5
Revises: e1f2a3b4c5d6
Create Date: 2026-05-14

将「占道经营」拉框预设的 label_value 统一为 streetVendor_* / object_*，
与 YOLO 权重类名一致；已存在库中该预设的旧条目会被替换。
"""
from __future__ import annotations

from datetime import datetime

from alembic import op
from sqlalchemy.orm import sessionmaker

revision = "f0e1d2c3b4a5"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

# 与 a1c2e3f4b5d6_seed_street_vendor_rect_preset 升级后保持一致
_NEW_ROWS: list[tuple[str, str, str, int]] = [
    ("蔬菜摊", "streetVendor_vegetablet", "#E67E22", 0),
    ("餐饮摊贩", "streetVendor_catering", "#3498DB", 1),
    ("衣服摊", "streetVendor_clothing", "#9B59B6", 2),
    ("水果摊", "streetVendor_fruit", "#27AE60", 3),
    ("其他摊贩", "streetVendor_other", "#95A5A6", 4),
    ("箱子（水果摊/蔬菜摊装蔬菜的箱子）", "object_plasticbasket", "#1ABC9C", 5),
    ("篮子", "object_basket", "#F39C12", 6),
    ("水桶", "object_bucket", "#E74C3C", 7),
    ("冰柜", "object_cabinet", "#34495E", 8),
    ("桌子", "object_table", "#16A085", 9),
    ("椅子", "object_chair", "#2980B9", 10),
]

# downgrade：恢复 a1c2e3f4b5d6 初版种子中的英文键（迁移前状态）
_OLD_ROWS: list[tuple[str, str, str, int]] = [
    ("蔬菜摊", "vegetable_stall", "#E67E22", 0),
    ("餐饮摊贩", "catering_vendor", "#3498DB", 1),
    ("衣服摊", "clothing_vendor", "#9B59B6", 2),
    ("水果摊", "fruit_vendor", "#27AE60", 3),
    ("其他摊贩", "other_vendor", "#95A5A6", 4),
    ("箱子（水果摊/蔬菜摊装蔬菜的筐）", "box", "#1ABC9C", 5),
    ("筐子", "basket", "#F39C12", 6),
    ("水桶", "bucket", "#E74C3C", 7),
    ("冰柜", "cabinet", "#34495E", 8),
    ("桌子", "table", "#16A085", 9),
    ("椅子", "chair", "#2980B9", 10),
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
        slug = "streetVendorOccupation"
        p = session.query(LabelPreset).filter(LabelPreset.slug == slug).first()
        if not p:
            return

        session.query(LabelPresetItem).filter(LabelPresetItem.preset_id == p.id).delete(
            synchronize_session=False
        )
        p.description = (
            "占道经营拉框：摊贩与物体（label_value 与 YOLO 导出类名 streetVendor_* / object_* 对齐，可在管理页改）"
        )
        p.updated_at = now

        for lk, lv, color, so in _NEW_ROWS:
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
    conn = op.get_bind()
    Session = sessionmaker(bind=conn)
    session = Session()
    try:
        from labelu.internal.domain.models.annotation_taxonomy import (
            LabelPreset,
            LabelPresetItem,
        )

        now = datetime.now()
        p = session.query(LabelPreset).filter(
            LabelPreset.slug == "streetVendorOccupation"
        ).first()
        if not p:
            return

        session.query(LabelPresetItem).filter(LabelPresetItem.preset_id == p.id).delete(
            synchronize_session=False
        )
        p.description = "占道经营拉框：摊贩类型与相关物体（可由业务在管理页继续改）"
        p.updated_at = now

        for lk, lv, color, so in _OLD_ROWS:
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
