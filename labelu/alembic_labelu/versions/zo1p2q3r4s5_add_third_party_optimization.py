"""Add third-party optimization request, cycle, event-image and publish decision tables.

Revision ID: zo1p2q3r4s5
Revises: zn0o1p2q3r4
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "zo1p2q3r4s5"
down_revision = "zn0o1p2q3r4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if "optimization_request" not in existing:
        op.create_table(
            "optimization_request",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("external_request_id", sa.String(length=160), nullable=False),
            sa.Column("source_system", sa.String(length=64), nullable=False, server_default="third_party"),
            sa.Column("client_id", sa.String(length=128), nullable=True),
            sa.Column("request_type", sa.String(length=48), nullable=False),
            sa.Column("external_model_id", sa.String(length=160), nullable=True),
            sa.Column("labelu_model_id", sa.Integer(), nullable=True),
            sa.Column("export_id", sa.Integer(), nullable=True),
            sa.Column("event_label", sa.String(length=160), nullable=True),
            sa.Column("scene_json", sa.Text(), nullable=True),
            sa.Column("package_ids_json", sa.Text(), nullable=True),
            sa.Column("event_time_from", sa.DateTime(timezone=True), nullable=True),
            sa.Column("event_time_to", sa.DateTime(timezone=True), nullable=True),
            sa.Column("require_complete", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("allow_partial", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("min_sample_count", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="PENDING"),
            sa.Column("stage", sa.String(length=48), nullable=False, server_default="RECEIVED"),
            sa.Column("progress_json", sa.Text(), nullable=True),
            sa.Column("coverage_json", sa.Text(), nullable=True),
            sa.Column("result_json", sa.Text(), nullable=True),
            sa.Column("error_json", sa.Text(), nullable=True),
            sa.Column("callback_url", sa.String(length=1024), nullable=True),
            sa.Column("callback_event_id", sa.String(length=160), nullable=True),
            sa.Column("strategy_external_task_id", sa.String(length=160), nullable=True),
            sa.Column("workflow_id", sa.Integer(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["workflow_id"], ["workflow_instance.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_system",
                "external_request_id",
                name="uq_optimization_request_external",
            ),
        )
        for name, columns in (
            ("ix_optimization_request_external_request_id", ["external_request_id"]),
            ("ix_optimization_request_source_system", ["source_system"]),
            ("ix_optimization_request_client_id", ["client_id"]),
            ("ix_optimization_request_request_type", ["request_type"]),
            ("ix_optimization_request_external_model_id", ["external_model_id"]),
            ("ix_optimization_request_labelu_model_id", ["labelu_model_id"]),
            ("ix_optimization_request_export_id", ["export_id"]),
            ("ix_optimization_request_event_label", ["event_label"]),
            ("ix_optimization_request_event_time_from", ["event_time_from"]),
            ("ix_optimization_request_event_time_to", ["event_time_to"]),
            ("ix_optimization_request_status", ["status"]),
            ("ix_optimization_request_stage", ["stage"]),
            ("ix_optimization_request_strategy_external_task_id", ["strategy_external_task_id"]),
            ("ix_optimization_request_workflow_id", ["workflow_id"]),
            ("ix_optimization_request_created_by", ["created_by"]),
            ("ix_optimization_request_deadline_at", ["deadline_at"]),
            ("ix_optimization_request_deleted_at", ["deleted_at"]),
            ("ix_optimization_request_status_updated", ["status", "updated_at"]),
        ):
            op.create_index(name, "optimization_request", columns, unique=False)

    if "optimization_cycle" not in existing:
        op.create_table(
            "optimization_cycle",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("request_id", sa.Integer(), nullable=False),
            sa.Column("aggregation_key", sa.String(length=512), nullable=False),
            sa.Column("model_id", sa.String(length=160), nullable=True),
            sa.Column("event_label", sa.String(length=160), nullable=True),
            sa.Column("scene_json", sa.Text(), nullable=True),
            sa.Column("aggregation_group_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="COLLECTING"),
            sa.Column("min_sample_count", sa.Integer(), nullable=False, server_default="50"),
            sa.Column("declared_sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("valid_sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("task_ids_json", sa.Text(), nullable=True),
            sa.Column("dataset_version_id", sa.Integer(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["request_id"], ["optimization_request.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("aggregation_key", name="uq_optimization_cycle_key"),
        )
        for name, columns in (
            ("ix_optimization_cycle_request_id", ["request_id"]),
            ("ix_optimization_cycle_group_id", ["aggregation_group_id"]),
            ("ix_optimization_cycle_status", ["status"]),
            ("ix_optimization_cycle_dataset_version_id", ["dataset_version_id"]),
        ):
            op.create_index(name, "optimization_cycle", columns, unique=False)

    if "third_party_event_image" not in existing:
        op.create_table(
            "third_party_event_image",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("request_id", sa.Integer(), nullable=False),
            sa.Column("cycle_id", sa.Integer(), nullable=True),
            sa.Column("source_system", sa.String(length=64), nullable=False, server_default="third_party"),
            sa.Column("package_id", sa.String(length=160), nullable=True),
            sa.Column("external_image_id", sa.String(length=160), nullable=False),
            sa.Column("external_event_id", sa.String(length=160), nullable=True),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("sample_id", sa.Integer(), nullable=True),
            sa.Column("event_label", sa.String(length=160), nullable=True),
            sa.Column("event_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column("scene_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["request_id"], ["optimization_request.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["cycle_id"], ["optimization_cycle.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "request_id",
                "external_image_id",
                name="uq_third_party_event_image_request_external",
            ),
        )
        for name, columns in (
            ("ix_third_party_event_image_request_id", ["request_id"]),
            ("ix_third_party_event_image_cycle_id", ["cycle_id"]),
            ("ix_third_party_event_image_source_system", ["source_system"]),
            ("ix_third_party_event_image_package_id", ["package_id"]),
            ("ix_third_party_event_image_external_image_id", ["external_image_id"]),
            ("ix_third_party_event_image_external_event_id", ["external_event_id"]),
            ("ix_third_party_event_image_task_id", ["task_id"]),
            ("ix_third_party_event_image_sample_id", ["sample_id"]),
            ("ix_third_party_event_image_event_label", ["event_label"]),
            ("ix_third_party_event_image_event_time", ["event_time"]),
        ):
            op.create_index(name, "third_party_event_image", columns, unique=False)

    if "integration_publish_decision" not in existing:
        op.create_table(
            "integration_publish_decision",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("request_id", sa.Integer(), nullable=False),
            sa.Column("candidate_model_id", sa.Integer(), nullable=True),
            sa.Column("baseline_model_id", sa.Integer(), nullable=True),
            sa.Column("candidate_export_id", sa.Integer(), nullable=True),
            sa.Column("metrics_json", sa.Text(), nullable=True),
            sa.Column("auto_publish_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("decision", sa.String(length=32), nullable=False, server_default="PENDING"),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("evidence_json", sa.Text(), nullable=True),
            sa.Column("decided_by", sa.Integer(), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("published_model_id", sa.Integer(), nullable=True),
            sa.Column("aicm_model_set_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["request_id"], ["optimization_request.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["decided_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("request_id", name="uq_publish_decision_request"),
        )
        for name, columns in (
            ("ix_integration_publish_decision_request_id", ["request_id"]),
            ("ix_integration_publish_decision_decision", ["decision"]),
            ("ix_integration_publish_decision_candidate_model_id", ["candidate_model_id"]),
            ("ix_integration_publish_decision_baseline_model_id", ["baseline_model_id"]),
        ):
            op.create_index(name, "integration_publish_decision", columns, unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table in (
        "integration_publish_decision",
        "third_party_event_image",
        "optimization_cycle",
        "optimization_request",
    ):
        if table in existing:
            op.drop_table(table)
