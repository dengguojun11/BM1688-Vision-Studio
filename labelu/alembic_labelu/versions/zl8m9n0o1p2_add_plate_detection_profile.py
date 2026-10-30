"""add detection-only electric plate training profile

Revision ID: zl8m9n0o1p2
Revises: zk7l8m9n0o1
Create Date: 2026-10-29 12:00:00
"""

from __future__ import annotations

import json
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = "zl8m9n0o1p2"
down_revision = "zk7l8m9n0o1"
branch_labels = None
depends_on = None


_PROFILE = "plate_detection_v1"
_SLUG = "plateDetection"
_CONFIG = {
    "profile": _PROFILE,
    "rect_label": "doubleEP",
    "polygon_label": "platePoint",
    "attributes": {},
}


def upgrade() -> None:
    conn = op.get_bind()
    metadata = sa.MetaData()
    preset = sa.Table("label_preset", metadata, autoload_with=conn)
    item = sa.Table("label_preset_item", metadata, autoload_with=conn)
    now = datetime.now()
    profile_json = json.dumps(_CONFIG, ensure_ascii=False, separators=(",", ":"))

    preset_id = conn.execute(
        sa.select(preset.c.id).where(sa.func.lower(preset.c.slug) == _SLUG.lower())
    ).scalar()
    if preset_id is None:
        result = conn.execute(
            preset.insert().values(
                slug=_SLUG,
                name="电动车牌检测与四角定位",
                description="车牌框 + 四角点，仅训练车牌定位，不识别号码、颜色或类型",
                training_profile=_PROFILE,
                training_profile_config=profile_json,
                sort_order=1,
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
                name="电动车牌检测与四角定位",
                description="车牌框 + 四角点，仅训练车牌定位，不识别号码、颜色或类型",
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
                label_key="车牌框",
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
            .values(label_key="车牌框", label_value="doubleEP", color="#ff6600")
        )

    recipe = sa.Table("training_recipe", metadata, autoload_with=conn)
    recipe_id = conn.execute(
        sa.select(recipe.c.id).where(
            recipe.c.recipe_key == _PROFILE,
            recipe.c.version == 1,
        )
    ).scalar()
    values = {
        "recipe_key": _PROFILE,
        "version": 1,
        "name": "车牌检测与四角定位",
        "job_type": "plate_detection",
        "model_family": "plate_detection",
        "annotation_kind": "bbox",
        "component_contract_json": json.dumps(
            {"geometry": ["rect", "four_corners"], "attributes": []},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "default_hyperparams_json": "{}",
        "status": "active",
        "updated_at": now,
    }
    if recipe_id is None:
        values["created_at"] = now
        conn.execute(recipe.insert().values(**values))
    else:
        conn.execute(recipe.update().where(recipe.c.id == recipe_id).values(**values))


def downgrade() -> None:
    conn = op.get_bind()
    metadata = sa.MetaData()
    preset = sa.Table("label_preset", metadata, autoload_with=conn)
    item = sa.Table("label_preset_item", metadata, autoload_with=conn)
    recipe = sa.Table("training_recipe", metadata, autoload_with=conn)
    preset_id = conn.execute(
        sa.select(preset.c.id).where(sa.func.lower(preset.c.slug) == _SLUG.lower())
    ).scalar()
    if preset_id is not None:
        conn.execute(item.delete().where(item.c.preset_id == preset_id))
        conn.execute(preset.delete().where(preset.c.id == preset_id))
    conn.execute(
        recipe.delete().where(recipe.c.recipe_key == _PROFILE, recipe.c.version == 1)
    )
