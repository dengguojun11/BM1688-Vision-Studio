"""add published model bmodel precision"""

from alembic import op
import sqlalchemy as sa


revision = "h7i8j9k0l1m2"
down_revision = "g6h7i8j9k0l1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "published_model_catalog",
        sa.Column("precision", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("published_model_catalog", "precision")
