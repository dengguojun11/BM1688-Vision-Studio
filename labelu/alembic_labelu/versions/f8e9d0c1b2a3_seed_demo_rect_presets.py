"""seed extra rect presets (riding no helmet, illegal parking)

Revision ID: f8e9d0c1b2a3
Revises: d4e5f6a7b8c0
Create Date: 2026-05-10

"""
from __future__ import annotations

from datetime import datetime

from alembic import op
from sqlalchemy.orm import sessionmaker

revision = "f8e9d0c1b2a3"
down_revision = "d4e5f6a7b8c0"
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

        def ensure_preset(
            slug: str,
            name: str,
            description: str,
            sort_order: int,
            items: list[tuple[str, str, str, int]],
        ) -> None:
            if session.query(LabelPreset).filter(LabelPreset.slug == slug).first():
                return
            p = LabelPreset(
                slug=slug,
                name=name,
                description=description,
                sort_order=sort_order,
                created_at=now,
                updated_at=now,
            )
            session.add(p)
            session.flush()
            for lk, lv, color, so in items:
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

        ensure_preset(
            "ridingNoHelmet",
            "骑行不带头盔",
            "示例预设：头盔检测",
            10,
            [
                ("未戴头盔", "no_helmet", "#E74C3C", 0),
                ("已戴头盔", "with_helmet", "#27AE60", 1),
                ("遮挡/不清", "unclear", "#95A5A6", 2),
            ],
        )
        ensure_preset(
            "illegalParking",
            "机动车违停",
            "示例预设：违停",
            20,
            [
                ("违停", "illegal_parking", "#E67E22", 0),
                ("规范停车", "legal_parking", "#3498DB", 1),
                ("非机动区域", "non_motor_zone", "#9B59B6", 2),
            ],
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

        for slug in ("ridingNoHelmet", "illegalParking"):
            p = session.query(LabelPreset).filter(LabelPreset.slug == slug).first()
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
