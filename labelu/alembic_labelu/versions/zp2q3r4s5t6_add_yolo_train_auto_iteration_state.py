"""Add auto iteration lineage and terminal state to yolo training jobs.

Revision ID: zp2q3r4s5t6
Revises: zo1p2q3r4s5

The single-job auto-iteration chain needs three nullable columns so a stopped
chain is visible after the fact:

* ``auto_iteration_root_id``  - first job of the chain
* ``auto_iteration_round_no`` - 1-based round number inside the chain
* ``auto_iteration_stop_reason`` - ROUND_LIMIT / NO_IMPROVEMENT / ...
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "zp2q3r4s5t6"
down_revision = "zo1p2q3r4s5"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "yolo_train_job" not in _tables(bind):
        return
    columns = _columns(bind, "yolo_train_job")
    if "auto_iteration_root_id" not in columns:
        op.add_column(
            "yolo_train_job", sa.Column("auto_iteration_root_id", sa.Integer())
        )
        op.create_index(
            "ix_yolo_train_job_auto_iteration_root_id",
            "yolo_train_job",
            ["auto_iteration_root_id"],
        )
    if "auto_iteration_round_no" not in columns:
        op.add_column(
            "yolo_train_job", sa.Column("auto_iteration_round_no", sa.Integer())
        )
    if "auto_iteration_stop_reason" not in columns:
        op.add_column(
            "yolo_train_job",
            sa.Column("auto_iteration_stop_reason", sa.String(length=32)),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "yolo_train_job" not in _tables(bind):
        return
    columns = _columns(bind, "yolo_train_job")
    if "auto_iteration_stop_reason" in columns:
        op.drop_column("yolo_train_job", "auto_iteration_stop_reason")
    if "auto_iteration_round_no" in columns:
        op.drop_column("yolo_train_job", "auto_iteration_round_no")
    if "auto_iteration_root_id" in columns:
        try:
            op.drop_index(
                "ix_yolo_train_job_auto_iteration_root_id",
                table_name="yolo_train_job",
            )
        except Exception:
            pass
        op.drop_column("yolo_train_job", "auto_iteration_root_id")
