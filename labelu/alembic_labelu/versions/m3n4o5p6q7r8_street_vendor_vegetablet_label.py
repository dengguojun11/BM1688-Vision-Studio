"""streetVendor_vegetable -> streetVendor_vegetablet（与 YOLO/历史预标注一致）

Revision ID: m3n4o5p6q7r8
Revises: l1m2n3o4p5q6
Create Date: 2026-05-27

将占道经营预设及任务 config / 样本 / 预标注 JSON 中的蔬菜摊类名
streetVendor_vegetable 更正为 streetVendor_vegetablet（已是 vegetablet 的不重复追加 t）。
"""
from __future__ import annotations

import re
from datetime import datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

revision = "m3n4o5p6q7r8"
down_revision = "l1m2n3o4p5q6"
branch_labels = None
depends_on = None

OLD_TOKEN = "streetVendor_vegetable"
NEW_TOKEN = "streetVendor_vegetablet"
PRESET_SLUG = "streetVendorOccupation"

# 仅匹配 vegetable，不匹配已是 vegetablet 的 token
_TOKEN_RE = re.compile(r"streetVendor_vegetable(?!t)")


def _replace_token(text: str | None) -> tuple[str | None, bool]:
    if not text or OLD_TOKEN not in text:
        return text, False
    replaced, n = _TOKEN_RE.subn(NEW_TOKEN, text)
    return replaced, n > 0


def upgrade() -> None:
    conn = op.get_bind()
    Session = sessionmaker(bind=conn)
    session = Session()
    try:
        meta = sa.MetaData()
        label_preset = sa.Table("label_preset", meta, autoload_with=conn)
        label_preset_item = sa.Table("label_preset_item", meta, autoload_with=conn)
        task = sa.Table("task", meta, autoload_with=conn)
        task_sample = sa.Table("task_sample", meta, autoload_with=conn)
        task_pre_annotation = sa.Table("task_pre_annotation", meta, autoload_with=conn)

        now = datetime.now()
        preset = (
            session.execute(
                sa.select(label_preset.c.id).where(label_preset.c.slug == PRESET_SLUG)
            ).first()
        )
        if preset:
            preset_id = preset.id
            items = session.execute(
                sa.select(label_preset_item.c.id).where(
                    label_preset_item.c.preset_id == preset_id,
                    label_preset_item.c.label_value == OLD_TOKEN,
                )
            )
            item_ids = [row.id for row in items]
            if item_ids:
                session.execute(
                    label_preset_item.update()
                    .where(label_preset_item.c.id.in_(item_ids))
                    .values(label_value=NEW_TOKEN)
                )
                session.execute(
                    label_preset.update()
                    .where(label_preset.c.id == preset_id)
                    .values(updated_at=now)
                )

        task_rows = session.execute(
            sa.select(task.c.id, task.c.config).where(task.c.config.is_not(None))
        )
        for row in task_rows:
            new_cfg, changed = _replace_token(row.config)
            if changed:
                session.execute(
                    task.update()
                    .where(task.c.id == row.id)
                    .values(config=new_cfg, updated_at=now)
                )

        sample_rows = session.execute(
            sa.select(task_sample.c.id, task_sample.c.data).where(
                task_sample.c.data.is_not(None)
            )
        )
        for row in sample_rows:
            new_data, changed = _replace_token(row.data)
            if changed:
                session.execute(
                    task_sample.update()
                    .where(task_sample.c.id == row.id)
                    .values(data=new_data, updated_at=now)
                )

        pre_annotation_rows = session.execute(
            sa.select(task_pre_annotation.c.id, task_pre_annotation.c.data).where(
                task_pre_annotation.c.data.is_not(None)
            )
        )
        for row in pre_annotation_rows:
            new_data, changed = _replace_token(row.data)
            if changed:
                session.execute(
                    task_pre_annotation.update()
                    .where(task_pre_annotation.c.id == row.id)
                    .values(data=new_data, updated_at=now)
                )

        session.commit()
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
        meta = sa.MetaData()
        label_preset = sa.Table("label_preset", meta, autoload_with=conn)
        label_preset_item = sa.Table("label_preset_item", meta, autoload_with=conn)
        task = sa.Table("task", meta, autoload_with=conn)
        task_sample = sa.Table("task_sample", meta, autoload_with=conn)
        task_pre_annotation = sa.Table("task_pre_annotation", meta, autoload_with=conn)

        now = datetime.now()
        preset = (
            session.execute(
                sa.select(label_preset.c.id).where(label_preset.c.slug == PRESET_SLUG)
            ).first()
        )
        if preset:
            preset_id = preset.id
            items = session.execute(
                sa.select(label_preset_item.c.id).where(
                    label_preset_item.c.preset_id == preset_id,
                    label_preset_item.c.label_value == NEW_TOKEN,
                )
            )
            item_ids = [row.id for row in items]
            if item_ids:
                session.execute(
                    label_preset_item.update()
                    .where(label_preset_item.c.id.in_(item_ids))
                    .values(label_value=OLD_TOKEN)
                )
                session.execute(
                    label_preset.update()
                    .where(label_preset.c.id == preset_id)
                    .values(updated_at=now)
                )

        revert_re = re.compile(r"streetVendor_vegetablet")

        def _revert(text: str | None) -> tuple[str | None, bool]:
            if not text or NEW_TOKEN not in text:
                return text, False
            replaced, n = revert_re.subn(OLD_TOKEN, text)
            return replaced, n > 0

        task_rows = session.execute(
            sa.select(task.c.id, task.c.config).where(task.c.config.is_not(None))
        )
        for row in task_rows:
            new_cfg, changed = _revert(row.config)
            if changed:
                session.execute(
                    task.update()
                    .where(task.c.id == row.id)
                    .values(config=new_cfg, updated_at=now)
                )

        sample_rows = session.execute(
            sa.select(task_sample.c.id, task_sample.c.data).where(
                task_sample.c.data.is_not(None)
            )
        )
        for row in sample_rows:
            new_data, changed = _revert(row.data)
            if changed:
                session.execute(
                    task_sample.update()
                    .where(task_sample.c.id == row.id)
                    .values(data=new_data, updated_at=now)
                )

        pre_annotation_rows = session.execute(
            sa.select(task_pre_annotation.c.id, task_pre_annotation.c.data).where(
                task_pre_annotation.c.data.is_not(None)
            )
        )
        for row in pre_annotation_rows:
            new_data, changed = _revert(row.data)
            if changed:
                session.execute(
                    task_pre_annotation.update()
                    .where(task_pre_annotation.c.id == row.id)
                    .values(data=new_data, updated_at=now)
                )

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
