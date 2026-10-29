"""Add explicit dataset-version review decision fields."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "z2a3b4c5d6e7"
down_revision = "z1a2b3c4d5e6"
branch_labels = None
depends_on = None


def _add_column_if_missing(bind, name: str, column: sa.Column) -> None:
    columns = {item["name"] for item in inspect(bind).get_columns("dataset_version")}
    if name not in columns:
        with op.batch_alter_table("dataset_version") as batch:
            batch.add_column(column)


def upgrade() -> None:
    bind = op.get_bind()
    if "dataset_version" not in inspect(bind).get_table_names():
        return

    _add_column_if_missing(
        bind, "review_status", sa.Column("review_status", sa.String(24), nullable=True)
    )
    _add_column_if_missing(
        bind, "review_comment", sa.Column("review_comment", sa.Text(), nullable=True)
    )
    _add_column_if_missing(
        bind,
        "reviewed_by",
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        bind,
        "reviewed_at",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )

    indexes = {item["name"] for item in inspect(bind).get_indexes("dataset_version")}
    if "ix_dataset_version_review_status" not in indexes:
        op.create_index(
            "ix_dataset_version_review_status",
            "dataset_version",
            ["review_status"],
            unique=False,
        )

    # Existing frozen versions already passed the old freeze gate. Preserve
    # that fact; all other existing versions enter the new pending-review
    # state and must be explicitly reviewed before freezing.
    bind.execute(
        text(
            "UPDATE dataset_version "
            "SET review_status = CASE "
            "WHEN status = 'FROZEN' THEN 'APPROVED' "
            "WHEN review_status IS NULL THEN 'PENDING' "
            "ELSE review_status END"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "dataset_version" not in inspect(bind).get_table_names():
        return
    indexes = {item["name"] for item in inspect(bind).get_indexes("dataset_version")}
    if "ix_dataset_version_review_status" in indexes:
        op.drop_index("ix_dataset_version_review_status", table_name="dataset_version")
    existing = {item["name"] for item in inspect(bind).get_columns("dataset_version")}
    with op.batch_alter_table("dataset_version") as batch:
        for name in ("reviewed_at", "reviewed_by", "review_comment", "review_status"):
            if name in existing:
                batch.drop_column(name)
