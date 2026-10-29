"""add resumable media upload sessions and attachment content identity"""

from alembic import op
import sqlalchemy as sa

revision = "t3u4v5w6x7y8"
down_revision = "s2t3u4v5w6x7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    attachment_columns = {column["name"] for column in inspector.get_columns("task_attachment")}
    if "sha256" not in attachment_columns:
        op.add_column("task_attachment", sa.Column("sha256", sa.String(64), nullable=True))
    if "size_bytes" not in attachment_columns:
        op.add_column("task_attachment", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    if "content_type" not in attachment_columns:
        op.add_column("task_attachment", sa.Column("content_type", sa.String(128), nullable=True))
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("task_attachment")}
    if "ix_task_attachment_sha256" not in existing_indexes:
        op.create_index("ix_task_attachment_sha256", "task_attachment", ["sha256"], unique=False)
    if "idx_attachment_task_sha256" not in existing_indexes:
        op.create_index("idx_attachment_task_sha256", "task_attachment", ["task_id", "sha256", "deleted_at"], unique=False)

    if "media_upload_session" not in inspector.get_table_names():
        op.create_table(
            "media_upload_session",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("session_id", sa.String(64), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("filename", sa.String(256), nullable=False),
            sa.Column("total_size", sa.Integer(), nullable=False),
            sa.Column("chunk_size", sa.Integer(), nullable=False),
            sa.Column("total_chunks", sa.Integer(), nullable=False),
            sa.Column("received_chunks_json", sa.Text(), nullable=False),
            sa.Column("sha256", sa.String(64), nullable=True),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("attachment_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["task_id"], ["task.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["attachment_id"], ["task_attachment.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("session_id", name="uq_media_upload_session_id"),
        )
        op.create_index("idx_media_upload_task_status", "media_upload_session", ["task_id", "status", "updated_at"], unique=False)
        op.create_index("idx_media_upload_sha256", "media_upload_session", ["task_id", "sha256"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if "media_upload_session" in sa.inspect(bind).get_table_names():
        op.drop_index("idx_media_upload_sha256", table_name="media_upload_session")
        op.drop_index("idx_media_upload_task_status", table_name="media_upload_session")
        op.drop_table("media_upload_session")
    indexes = {idx["name"] for idx in sa.inspect(bind).get_indexes("task_attachment")}
    if "idx_attachment_task_sha256" in indexes:
        op.drop_index("idx_attachment_task_sha256", table_name="task_attachment")
    if "ix_task_attachment_sha256" in indexes:
        op.drop_index("ix_task_attachment_sha256", table_name="task_attachment")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("task_attachment")}
    for name in ("content_type", "size_bytes", "sha256"):
        if name in columns:
            op.drop_column("task_attachment", name)
