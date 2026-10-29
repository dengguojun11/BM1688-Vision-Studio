"""add task training_ready and annotation_audit_json

Revision ID: o5p6q7r8s9t0
Revises: n4o5p6q7r8s9
Create Date: 2026-06-08 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "o5p6q7r8s9t0"
down_revision = "n4o5p6q7r8s9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("task")}
    if "training_ready" not in cols:
        op.add_column(
            "task",
            sa.Column(
                "training_ready",
                sa.Boolean(),
                nullable=True,
                comment="图像任务标注完整度：全局 tag + 有效拉框均满足",
            ),
        )
    if "annotation_audit_json" not in cols:
        op.add_column(
            "task",
            sa.Column(
                "annotation_audit_json",
                sa.Text(),
                nullable=True,
                comment="标注审计摘要 JSON",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("task")}
    if "annotation_audit_json" in cols:
        op.drop_column("task", "annotation_audit_json")
    if "training_ready" in cols:
        op.drop_column("task", "training_ready")
