"""persist media malware scan and quarantine state"""

from alembic import op
import sqlalchemy as sa


revision = "a0b1c2d3e4f5"
down_revision = "z9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "media_upload_session" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("media_upload_session")}
    additions = (
        ("scan_status", sa.String(24), "PENDING"),
        ("scan_provider", sa.String(64), None),
        ("scan_signature", sa.String(256), None),
        ("scan_error", sa.Text(), None),
        ("scanned_at", sa.DateTime(timezone=True), None),
        ("quarantined_at", sa.DateTime(timezone=True), None),
        ("quarantine_path", sa.String(512), None),
        ("quarantine_until", sa.DateTime(timezone=True), None),
    )
    for name, column_type, default in additions:
        if name not in columns:
            column = sa.Column(name, column_type, nullable=False if name == "scan_status" else True, server_default=default)
            op.add_column("media_upload_session", column)
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("media_upload_session")}
    if "idx_media_upload_scan_status" not in indexes:
        op.create_index(
            "idx_media_upload_scan_status",
            "media_upload_session",
            ["status", "scan_status", "updated_at"],
            unique=False,
        )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("media_upload_session")}
    if "ix_media_upload_session_quarantine_until" not in indexes:
        op.create_index(
            "ix_media_upload_session_quarantine_until",
            "media_upload_session",
            ["quarantine_until"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "media_upload_session" not in inspector.get_table_names():
        return
    indexes = {index["name"] for index in inspector.get_indexes("media_upload_session")}
    if "ix_media_upload_session_quarantine_until" in indexes:
        op.drop_index("ix_media_upload_session_quarantine_until", table_name="media_upload_session")
    if "idx_media_upload_scan_status" in indexes:
        op.drop_index("idx_media_upload_scan_status", table_name="media_upload_session")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("media_upload_session")}
    for name in ("quarantine_until", "quarantine_path", "quarantined_at", "scanned_at", "scan_error", "scan_signature", "scan_provider", "scan_status"):
        if name in columns:
            op.drop_column("media_upload_session", name)
