"""Add fencing generation to automatic preannotation jobs.

Revision ID: zc1d2e3f4a5b
Revises: zb1c2d3e4f5g
"""

from alembic import op
import sqlalchemy as sa


revision = "zc1d2e3f4a5b"
down_revision = "zb1c2d3e4f5g"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {str(item["name"]) for item in sa.inspect(op.get_bind()).get_columns("auto_label_job")}


def upgrade() -> None:
    if "lease_fencing_token" not in _columns():
        op.add_column(
            "auto_label_job",
            sa.Column(
                "lease_fencing_token",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    if "lease_fencing_token" in _columns():
        op.drop_column("auto_label_job", "lease_fencing_token")

