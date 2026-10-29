"""Add a searchable index for rectangle labels present in task samples.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

from labelu.internal.common.label_image import rect_label_values

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


TABLE_NAME = "sample_annotation_label_index"
BACKFILL_BATCH_SIZE = 500


def _backfill(bind) -> None:
    tables = set(inspect(bind).get_table_names())
    if not {TABLE_NAME, "task_sample"}.issubset(tables):
        return

    metadata = sa.MetaData()
    sample = sa.Table("task_sample", metadata, autoload_with=bind)
    label_index = sa.Table(TABLE_NAME, metadata, autoload_with=bind)
    last_sample_id = 0
    while True:
        rows = bind.execute(
            sa.select(sample.c.id, sample.c.task_id, sample.c.data)
            .where(
                sample.c.id > last_sample_id,
                sample.c.deleted_at.is_(None),
                sample.c.data.isnot(None),
            )
            .order_by(sample.c.id.asc())
            .limit(BACKFILL_BATCH_SIZE)
        ).fetchall()
        if not rows:
            break
        sample_ids = [int(row[0]) for row in rows]
        existing = {
            (int(sample_id), str(label_value))
            for sample_id, label_value in bind.execute(
                sa.select(label_index.c.sample_id, label_index.c.label_value).where(
                    label_index.c.sample_id.in_(sample_ids)
                )
            ).fetchall()
        }
        inserts = []
        for sample_id, task_id, sample_data in rows:
            if task_id is None:
                continue
            for label_value in rect_label_values(sample_data):
                if (
                    len(label_value) <= 128
                    and (int(sample_id), label_value) not in existing
                ):
                    inserts.append(
                        {
                            "sample_id": int(sample_id),
                            "task_id": int(task_id),
                            "label_value": label_value,
                        }
                    )
                    existing.add((int(sample_id), label_value))
        if inserts:
            bind.execute(label_index.insert(), inserts)
        last_sample_id = int(rows[-1][0])


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if TABLE_NAME not in tables:
        op.create_table(
            TABLE_NAME,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("sample_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("label_value", sa.String(length=128), nullable=False),
            sa.ForeignKeyConstraint(["sample_id"], ["task_sample.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
            sa.UniqueConstraint(
                "sample_id",
                "label_value",
                name="uq_sample_annotation_label_index_sample_value",
            ),
        )
        op.create_index(
            "ix_sample_annotation_label_index_value_task_sample",
            TABLE_NAME,
            ["label_value", "task_id", "sample_id"],
            unique=False,
        )
    _backfill(bind)


def downgrade() -> None:
    if TABLE_NAME in set(inspect(op.get_bind()).get_table_names()):
        op.drop_table(TABLE_NAME)
