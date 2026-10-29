"""add rule publication signing contract

Revision ID: o1p2q3r4s5t6
Revises: n0p1q2r3s4t5
"""

from alembic import op
import sqlalchemy as sa


revision = "o1p2q3r4s5t6"
down_revision = "n0p1q2r3s4t5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rule_version", sa.Column("signing_payload_sha256", sa.String(64), nullable=True)
    )
    op.add_column(
        "rule_version", sa.Column("signature_algorithm", sa.String(32), nullable=True)
    )
    op.add_column(
        "rule_version", sa.Column("signature_key_id", sa.String(128), nullable=True)
    )
    op.add_column("rule_version", sa.Column("signature", sa.Text(), nullable=True))
    op.add_column(
        "rule_version",
        sa.Column(
            "server_signature_status",
            sa.String(32),
            nullable=False,
            server_default="NOT_PROVIDED",
        ),
    )
    op.add_column(
        "rule_version",
        sa.Column(
            "device_verification_status",
            sa.String(32),
            nullable=False,
            server_default="UNVERIFIED",
        ),
    )
    op.add_column(
        "rule_version", sa.Column("signature_verified_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("rule_version", "signature_verified_at")
    op.drop_column("rule_version", "device_verification_status")
    op.drop_column("rule_version", "server_signature_status")
    op.drop_column("rule_version", "signature")
    op.drop_column("rule_version", "signature_key_id")
    op.drop_column("rule_version", "signature_algorithm")
    op.drop_column("rule_version", "signing_payload_sha256")
