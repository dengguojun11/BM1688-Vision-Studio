"""seed rect preset: 占道经营（摊贩与物体）

Revision ID: a1c2e3f4b5d6
Revises: f8e9d0c1b2a3
Create Date: 2026-05-10

"""
from __future__ import annotations

from datetime import datetime

from alembic import op
from sqlalchemy.orm import sessionmaker

revision = "a1c2e3f4b5d6"
down_revision = "f8e9d0c1b2a3"
branch_labels = None
depends_on = None


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

        if session.query(LabelPreset).filter(LabelPreset.slug == slug).first():
            return

        p = LabelPreset(
            slug=slug,
            name="占道经营",
            description="占道经营拉框：摊贩与物体（label_value 与 YOLO 导出类名 streetVendor_* / object_* 对齐，可在管理页改）",
            sort_order=30,
            created_at=now,
            updated_at=now,
        )
        session.add(p)
        session.flush()

        # label_key = 前端中文展示；label_value = 保存用英文键（与批量预标注模型类名一致）
        rows: list[tuple[str, str, str, int]] = [
            ("蔬菜摊", "streetVendor_vegetablet", "#E67E22", 0),
            ("餐饮摊贩", "streetVendor_catering", "#3498DB", 1),
            ("衣服摊", "streetVendor_clothing", "#9B59B6", 2),
            ("水果摊", "streetVendor_fruit", "#27AE60", 3),
            ("其他摊贩", "streetVendor_other", "#95A5A6", 4),
            (
                "箱子（水果摊/蔬菜摊装蔬菜的箱子）",
                "object_plasticbasket",
                "#1ABC9C",
                5,
            ),
            ("篮子", "object_basket", "#F39C12", 6),
            ("水桶", "object_bucket", "#E74C3C", 7),
            ("冰柜", "object_cabinet", "#34495E", 8),
            ("桌子", "object_table", "#16A085", 9),
            ("椅子", "object_chair", "#2980B9", 10),
        ]

        for lk, lv, color, so in rows:
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

        p = session.query(LabelPreset).filter(
            LabelPreset.slug == "streetVendorOccupation"
        ).first()
        if p:
            session.query(LabelPresetItem).filter(
                LabelPresetItem.preset_id == p.id
            ).delete(synchronize_session=False)
            session.delete(p)
        session.flush()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
