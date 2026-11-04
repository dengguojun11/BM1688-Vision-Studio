"""add public model integration metadata

Revision ID: zs5t6u7v8w9
Revises: zr4s5t6u7v8
Create Date: 2026-11-04 15:40:00
"""
from alembic import op
import sqlalchemy as sa


revision = "zs5t6u7v8w9"
down_revision = "zr4s5t6u7v8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "published_model_catalog",
        sa.Column("f1", sa.Float(), nullable=True),
    )
    op.add_column(
        "published_model_catalog",
        sa.Column("integration_meta_json", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_published_model_catalog_f1",
        "published_model_catalog",
        ["f1"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_published_model_catalog_f1",
        table_name="published_model_catalog",
    )
    op.drop_column("published_model_catalog", "integration_meta_json")
    op.drop_column("published_model_catalog", "f1")
