"""add training task for label dimension

Revision ID: b1c2d3e4f5a
Revises: a0b1c2d3e4f
Create Date: 2026-07-07 20:05:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "b1c2d3e4f5a"
down_revision = "a0b1c2d3e4f"
branch_labels = None
depends_on = None


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(idx.get("name") == index_name for idx in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = inspector.get_table_names()

    if "training_task" not in table_names:
        op.create_table(
            "training_task",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=128), nullable=False),
            sa.Column("mode", sa.String(length=32), nullable=False, server_default="label_dimension"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
            sa.Column("primary_task_id", sa.Integer(), nullable=True),
            sa.Column("selected_presets_snapshot_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("selected_label_items_snapshot_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("merged_class_names_snapshot_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("source_tasks_snapshot_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("ignored_tasks_snapshot_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("preview_summary_json", sa.Text(), nullable=True),
            sa.Column("latest_job_id", sa.Integer(), nullable=True),
            sa.Column("latest_job_status", sa.String(length=32), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["owner_id"], ["user.id"]),
            sa.ForeignKeyConstraint(["primary_task_id"], ["task.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_id", "name", name="uq_training_task_owner_name"),
        )
        op.create_index("ix_training_task_id", "training_task", ["id"])
        op.create_index("ix_training_task_owner_id", "training_task", ["owner_id"])
        op.create_index("ix_training_task_primary_task_id", "training_task", ["primary_task_id"])
        op.create_index("ix_training_task_latest_job_id", "training_task", ["latest_job_id"])

    inspector = inspect(bind)
    yolo_columns = {col["name"] for col in inspector.get_columns("yolo_train_job")}
    if "training_task_id" not in yolo_columns:
        op.add_column("yolo_train_job", sa.Column("training_task_id", sa.Integer(), nullable=True))
        if bind.dialect.name != "sqlite":
            op.create_foreign_key(
                "fk_yolo_train_job_training_task_id",
                "yolo_train_job",
                "training_task",
                ["training_task_id"],
                ["id"],
            )

    inspector = inspect(bind)
    if not _has_index(inspector, "yolo_train_job", "ix_yolo_train_job_training_task_id"):
        op.create_index(
            "ix_yolo_train_job_training_task_id",
            "yolo_train_job",
            ["training_task_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = inspector.get_table_names()

    if "yolo_train_job" in table_names:
        yolo_columns = {col["name"] for col in inspector.get_columns("yolo_train_job")}
        if "training_task_id" in yolo_columns:
            if _has_index(inspector, "yolo_train_job", "ix_yolo_train_job_training_task_id"):
                op.drop_index("ix_yolo_train_job_training_task_id", table_name="yolo_train_job")
            if bind.dialect.name != "sqlite":
                fk_names = {
                    fk.get("name")
                    for fk in inspector.get_foreign_keys("yolo_train_job")
                    if "training_task_id" in (fk.get("constrained_columns") or [])
                }
                if "fk_yolo_train_job_training_task_id" in fk_names:
                    op.drop_constraint(
                        "fk_yolo_train_job_training_task_id",
                        "yolo_train_job",
                        type_="foreignkey",
                    )
            op.drop_column("yolo_train_job", "training_task_id")

    inspector = inspect(bind)
    if "training_task" in inspector.get_table_names():
        if _has_index(inspector, "training_task", "ix_training_task_latest_job_id"):
            op.drop_index("ix_training_task_latest_job_id", table_name="training_task")
        if _has_index(inspector, "training_task", "ix_training_task_primary_task_id"):
            op.drop_index("ix_training_task_primary_task_id", table_name="training_task")
        if _has_index(inspector, "training_task", "ix_training_task_owner_id"):
            op.drop_index("ix_training_task_owner_id", table_name="training_task")
        if _has_index(inspector, "training_task", "ix_training_task_id"):
            op.drop_index("ix_training_task_id", table_name="training_task")
        op.drop_table("training_task")
