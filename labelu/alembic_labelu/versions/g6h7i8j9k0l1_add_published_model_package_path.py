"""add local package path to published model catalog"""

from alembic import op
import sqlalchemy as sa

revision = "g6h7i8j9k0l1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "published_model_catalog",
        sa.Column("package_rel_path", sa.String(length=512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("published_model_catalog", "package_rel_path")
