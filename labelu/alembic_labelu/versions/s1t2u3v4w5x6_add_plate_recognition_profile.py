"""add plate recognition composite training profile

Revision ID: s1t2u3v4w5x6
Revises: r9s0t1u2v3w4
Create Date: 2026-07-28 18:20:00
"""

from __future__ import annotations

import json
from datetime import datetime

from alembic import op
import sqlalchemy as sa

revision = "s1t2u3v4w5x6"
down_revision = "r9s0t1u2v3w4"
branch_labels = None
depends_on = None


_PROFILE = "plate_recognition_v1"
_SLUG = "plateRecognition"
_CONFIG = {
    "rect_label": "doubleEP",
    "polygon_label": "platePoint",
    "attributes": {
        "plateNumber": {"type": "ocr"},
        "color": ["white", "blue", "yellow", "black", "green"],
        "plateType": [
            "whitePlate",
            "bluePlate",
            "greenPlate",
            "yellowPlate",
            "blackPlate",
        ],
    },
}


def upgrade() -> None:
    with op.batch_alter_table("label_preset") as batch_op:
        batch_op.add_column(
            sa.Column("training_profile", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("training_profile_config", sa.Text(), nullable=True)
        )
        batch_op.create_index(
            "ix_label_preset_training_profile",
            ["training_profile"],
            unique=False,
        )
    with op.batch_alter_table("yolo_train_job") as batch_op:
        batch_op.add_column(
            sa.Column(
                "job_type",
                sa.String(length=32),
                nullable=False,
                server_default="yolo_detection",
            )
        )
        batch_op.add_column(
            sa.Column("component_status_json", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("component_artifacts_json", sa.Text(), nullable=True)
        )
        batch_op.create_index("ix_yolo_train_job_job_type", ["job_type"], unique=False)

    conn = op.get_bind()
    meta = sa.MetaData()
    preset = sa.Table("label_preset", meta, autoload_with=conn)
    item = sa.Table("label_preset_item", meta, autoload_with=conn)
    now = datetime.now()
    profile_json = json.dumps(_CONFIG, ensure_ascii=False, separators=(",", ":"))
    preset_id = conn.execute(
        sa.select(preset.c.id).where(sa.func.lower(preset.c.slug) == _SLUG.lower())
    ).scalar()
    if preset_id is None:
        result = conn.execute(
            preset.insert().values(
                slug=_SLUG,
                name="双层电动车牌识别",
                description="双层电动车牌复合训练：定位四角、号码识别、颜色与车牌类型分类",
                training_profile=_PROFILE,
                training_profile_config=profile_json,
                sort_order=0,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )
        preset_id = result.inserted_primary_key[0]
    else:
        conn.execute(
            preset.update()
            .where(preset.c.id == preset_id)
            .values(
                name="双层电动车牌识别",
                description="双层电动车牌复合训练：定位四角、号码识别、颜色与车牌类型分类",
                training_profile=_PROFILE,
                training_profile_config=profile_json,
                updated_at=now,
                deleted_at=None,
            )
        )

    item_id = conn.execute(
        sa.select(item.c.id).where(
            item.c.preset_id == preset_id,
            sa.func.trim(item.c.label_value) == "doubleEP",
        )
    ).scalar()
    if item_id is None:
        conn.execute(
            item.insert().values(
                preset_id=preset_id,
                label_key="双层车牌",
                label_value="doubleEP",
                color="#ff6600",
                sort_order=0,
                created_at=now,
            )
        )
    else:
        conn.execute(
            item.update()
            .where(item.c.id == item_id)
            .values(
                label_key="双层车牌",
                label_value="doubleEP",
                color="#ff6600",
                sort_order=0,
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    meta = sa.MetaData()
    preset = sa.Table("label_preset", meta, autoload_with=conn)
    conn.execute(
        preset.update()
        .where(preset.c.slug == _SLUG)
        .values(training_profile=None, training_profile_config=None)
    )
    with op.batch_alter_table("yolo_train_job") as batch_op:
        batch_op.drop_index("ix_yolo_train_job_job_type")
        batch_op.drop_column("component_artifacts_json")
        batch_op.drop_column("component_status_json")
        batch_op.drop_column("job_type")
    with op.batch_alter_table("label_preset") as batch_op:
        batch_op.drop_index("ix_label_preset_training_profile")
        batch_op.drop_column("training_profile_config")
        batch_op.drop_column("training_profile")
