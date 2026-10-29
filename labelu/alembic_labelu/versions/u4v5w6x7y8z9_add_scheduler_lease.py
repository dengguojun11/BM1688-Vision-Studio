"""Add database-backed singleton scheduler leases and fencing tokens."""

from alembic import op
import sqlalchemy as sa


revision = "u4v5w6x7y8z9"
down_revision = "t3u4v5w6x7y8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "scheduler_lease" in inspector.get_table_names():
        return
    op.create_table(
        "scheduler_lease",
        sa.Column("lease_name", sa.String(length=96), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("lease_name"),
    )
    op.create_index(
        "ix_scheduler_lease_lease_owner", "scheduler_lease", ["lease_owner"], unique=False
    )
    op.create_index(
        "ix_scheduler_lease_lease_expires_at",
        "scheduler_lease",
        ["lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "scheduler_lease" not in sa.inspect(bind).get_table_names():
        return
    op.drop_index("ix_scheduler_lease_lease_expires_at", table_name="scheduler_lease")
    op.drop_index("ix_scheduler_lease_lease_owner", table_name="scheduler_lease")
    op.drop_table("scheduler_lease")
