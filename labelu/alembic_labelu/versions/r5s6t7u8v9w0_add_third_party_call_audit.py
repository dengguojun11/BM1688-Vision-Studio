"""Add sanitized third-party HTTP call audit records.

Revision ID: r5s6t7u8v9w0
Revises: p4q5r6s7t8u9
"""

from alembic import op
import sqlalchemy as sa

revision = "r5s6t7u8v9w0"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "third_party_call_audit",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("request_method", sa.String(length=16), nullable=False),
        sa.Column("request_path", sa.String(length=512), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=True),
        sa.Column("external_task_id", sa.String(length=128), nullable=True),
        sa.Column("external_package_id", sa.String(length=128), nullable=True),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("response_status", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("client_ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id"),
    )
    for column in (
        "id",
        "request_id",
        "source_system",
        "operation",
        "request_path",
        "external_task_id",
        "external_package_id",
        "response_status",
        "outcome",
        "received_at",
    ):
        op.create_index(
            op.f(f"ix_third_party_call_audit_{column}"),
            "third_party_call_audit",
            [column],
            unique=column == "request_id",
        )
    op.create_index(
        "ix_third_party_call_source_received",
        "third_party_call_audit",
        ["source_system", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_third_party_call_operation_received",
        "third_party_call_audit",
        ["operation", "received_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("third_party_call_audit")
