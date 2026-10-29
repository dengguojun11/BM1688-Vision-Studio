"""Make automatic sample preannotation jobs durable.

Revision ID: p4q5r6s7t8u9
Revises: o3p4q5r6s7t8
"""

from alembic import op
import sqlalchemy as sa

revision = "p4q5r6s7t8u9"
down_revision = "o3p4q5r6s7t8"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns("auto_label_job")
    }


def _indexes() -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(op.get_bind()).get_indexes("auto_label_job")
    }


def upgrade() -> None:
    columns = _columns()
    additions = [
        ("job_kind", sa.String(length=32), False, "MANUAL_BATCH"),
        ("source_hint", sa.String(length=128), True, None),
        ("source_key", sa.String(length=128), True, None),
        ("payload_json", sa.Text(), True, None),
        ("attempt_count", sa.Integer(), False, "0"),
        ("lease_owner", sa.String(length=128), True, None),
        ("lease_expires_at", sa.DateTime(timezone=True), True, None),
    ]
    for name, column_type, nullable, server_default in additions:
        if name in columns:
            continue
        op.add_column(
            "auto_label_job",
            sa.Column(
                name,
                column_type,
                nullable=nullable,
                server_default=server_default,
            ),
        )

    indexes = _indexes()
    if "ix_auto_label_job_job_kind" not in indexes:
        op.create_index(
            "ix_auto_label_job_job_kind",
            "auto_label_job",
            ["job_kind"],
            unique=False,
        )
    if "ix_auto_label_job_source_key" not in indexes:
        op.create_index(
            "ix_auto_label_job_source_key",
            "auto_label_job",
            ["source_key"],
            unique=True,
        )
    if "ix_auto_label_job_lease_expires_at" not in indexes:
        op.create_index(
            "ix_auto_label_job_lease_expires_at",
            "auto_label_job",
            ["lease_expires_at"],
            unique=False,
        )


def downgrade() -> None:
    indexes = _indexes()
    for name in (
        "ix_auto_label_job_lease_expires_at",
        "ix_auto_label_job_source_key",
        "ix_auto_label_job_job_kind",
    ):
        if name in indexes:
            op.drop_index(name, table_name="auto_label_job")

    columns = _columns()
    for name in (
        "lease_expires_at",
        "lease_owner",
        "attempt_count",
        "payload_json",
        "source_key",
        "source_hint",
        "job_kind",
    ):
        if name in columns:
            op.drop_column("auto_label_job", name)
