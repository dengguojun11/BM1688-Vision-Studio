"""add published model download switch"""

from alembic import op
import sqlalchemy as sa

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "published_model_catalog",
        sa.Column(
            "download_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_index(
        "ix_published_model_catalog_download_enabled",
        "published_model_catalog",
        ["download_enabled"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_published_model_catalog_download_enabled",
        table_name="published_model_catalog",
    )
    op.drop_column("published_model_catalog", "download_enabled")
