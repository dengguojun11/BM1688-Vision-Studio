"""add personnel activity and review event tables

Revision ID: zr4s5t6u7v8
Revises: zq3r4s5t6u7
Create Date: 2026-11-02 16:10:00
"""
from alembic import op
import sqlalchemy as sa


revision = "zr4s5t6u7v8"
down_revision = "zq3r4s5t6u7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_activity_session",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("login_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_path", sa.String(length=512), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("logout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "session_id", name="uq_user_activity_session"
        ),
    )
    op.create_index(
        "ix_user_activity_session_user_id", "user_activity_session", ["user_id"]
    )
    op.create_index(
        "ix_user_activity_session_session_id", "user_activity_session", ["session_id"]
    )
    op.create_index(
        "ix_user_activity_session_last_seen", "user_activity_session", ["last_seen_at"]
    )
    op.create_index(
        "ix_user_activity_session_user_last_seen",
        "user_activity_session",
        ["user_id", "last_seen_at"],
    )

    op.create_table(
        "annotation_review_event",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("review_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("sample_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["review_id"], ["annotation_review.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["task.id"]),
        sa.ForeignKeyConstraint(["sample_id"], ["task_sample.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_annotation_review_event_review_id", "annotation_review_event", ["review_id"]
    )
    op.create_index(
        "ix_annotation_review_event_task_id", "annotation_review_event", ["task_id"]
    )
    op.create_index(
        "ix_annotation_review_event_sample_id", "annotation_review_event", ["sample_id"]
    )
    op.create_index(
        "ix_annotation_review_event_user_id", "annotation_review_event", ["user_id"]
    )
    op.create_index(
        "ix_annotation_review_event_to_status",
        "annotation_review_event",
        ["to_status"],
    )
    op.create_index(
        "ix_annotation_review_event_created_at",
        "annotation_review_event",
        ["created_at"],
    )
    op.create_index(
        "ix_annotation_review_event_user_created",
        "annotation_review_event",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_annotation_review_event_task_created",
        "annotation_review_event",
        ["task_id", "created_at"],
    )

    # Preserve the latest known review/arbitration/invalidation actions for
    # historical rows. Future transitions append precise events from the service.
    op.execute(
        """
        INSERT INTO annotation_review_event
            (review_id, task_id, sample_id, user_id, from_status, to_status, action, reason, created_at)
        SELECT id, task_id, sample_id, reviewed_by, NULL, review_status, 'BACKFILL_REVIEW',
               review_reason, reviewed_at
        FROM annotation_review
        WHERE reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO annotation_review_event
            (review_id, task_id, sample_id, user_id, from_status, to_status, action, reason, created_at)
        SELECT id, task_id, sample_id, arbitrator_id, NULL, 'ARBITRATED', 'BACKFILL_ARBITRATE',
               arbitration_reason, arbitrated_at
        FROM annotation_review
        WHERE arbitrated_at IS NOT NULL AND arbitrator_id IS NOT NULL
        """
    )
    op.execute(
        """
        INSERT INTO annotation_review_event
            (review_id, task_id, sample_id, user_id, from_status, to_status, action, reason, created_at)
        SELECT id, task_id, sample_id, invalidated_by, NULL, 'INVALIDATED', 'BACKFILL_INVALIDATE',
               invalidation_reason, invalidated_at
        FROM annotation_review
        WHERE invalidated_at IS NOT NULL AND invalidated_by IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_table("annotation_review_event")
    op.drop_table("user_activity_session")
