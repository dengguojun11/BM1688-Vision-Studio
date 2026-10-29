"""add controlled auto training core tables

Revision ID: i7j8k9l0m1n2
Revises: o3p4q5r6s7t8
"""

from alembic import op
import sqlalchemy as sa


revision = "i7j8k9l0m1n2"
down_revision = "o3p4q5r6s7t8"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    tables = _tables()
    if "auto_training_plan" not in tables:
        op.create_table(
            "auto_training_plan",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("series_type", sa.String(32), nullable=False),
            sa.Column("dataset_category_id", sa.Integer(), nullable=False),
            sa.Column("label_set_signature", sa.String(64), nullable=False),
            sa.Column("class_order_signature", sa.String(64), nullable=False),
            sa.Column("status", sa.String(48), nullable=False),
            sa.Column("status_reason_code", sa.String(64)),
            sa.Column("status_reason_json", sa.Text()),
            sa.Column("current_version_id", sa.Integer()),
            sa.Column("active_round_id", sa.Integer()),
            sa.Column("champion_job_id", sa.Integer()),
            sa.Column("champion_export_id", sa.Integer()),
            sa.Column("last_qualified_change_at", sa.DateTime(timezone=True)),
            sa.Column("next_evaluation_at", sa.DateTime(timezone=True)),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("updated_by", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deleted_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("owner_id", "name", name="uq_auto_training_plan_owner_name"),
        )
        op.create_index("idx_auto_training_plan_status_next", "auto_training_plan", ["status", "next_evaluation_at"])
        op.create_index("idx_auto_training_plan_series_contract", "auto_training_plan", ["series_type", "dataset_category_id", "label_set_signature"])
    tables = _tables()
    if "auto_training_plan_version" not in tables:
        op.create_table(
            "auto_training_plan_version",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("config_json", sa.Text(), nullable=False),
            sa.Column("config_sha256", sa.String(64), nullable=False),
            sa.Column("quality_policy_snapshot_json", sa.Text()),
            sa.Column("quality_policy_signature", sa.String(64)),
            sa.Column("label_contract_snapshot_json", sa.Text()),
            sa.Column("training_recipe_snapshot_json", sa.Text()),
            sa.Column("runtime_profile_snapshot_json", sa.Text()),
            sa.Column("evaluation_binding_json", sa.Text()),
            sa.Column("missing_fields_json", sa.Text(), nullable=False),
            sa.Column("validation_errors_json", sa.Text(), nullable=False),
            sa.Column("change_reason", sa.Text()),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("approved_by", sa.Integer()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("plan_id", "version_no", name="uq_auto_training_plan_version"),
            sa.UniqueConstraint("plan_id", "config_sha256", name="uq_auto_training_plan_config"),
        )
    if "auto_training_task_scope" not in tables:
        op.create_table(
            "auto_training_task_scope",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("scope_status", sa.String(32), nullable=False),
            sa.Column("inclusion_source", sa.String(24), nullable=False),
            sa.Column("eligible_sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("pending_review_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("excluded_sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("contract_signature", sa.String(64)),
            sa.Column("reason_code", sa.String(64)),
            sa.Column("reason_json", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("plan_id", "task_id", name="uq_auto_training_task_scope"),
        )
        op.create_index("idx_auto_training_scope_plan_status", "auto_training_task_scope", ["plan_id", "scope_status"])
        op.create_index("idx_auto_training_scope_task", "auto_training_task_scope", ["task_id", "updated_at"])
    if "auto_training_sample_pool" not in tables:
        op.create_table(
            "auto_training_sample_pool",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("sample_id", sa.Integer(), nullable=False),
            sa.Column("pool_status", sa.String(32), nullable=False),
            sa.Column("reason_code", sa.String(64)),
            sa.Column("annotation_sha256", sa.String(64)),
            sa.Column("image_sha256", sa.String(64)),
            sa.Column("sample_signature", sa.String(64)),
            sa.Column("label_contract_signature", sa.String(64)),
            sa.Column("human_review_id", sa.Integer()),
            sa.Column("data_grant_id", sa.Integer()),
            sa.Column("duplicate_group_id", sa.Integer()),
            sa.Column("eligible_since", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("plan_id", "sample_id", name="uq_auto_training_pool_sample"),
        )
        op.create_index("idx_auto_training_pool_plan_status", "auto_training_sample_pool", ["plan_id", "pool_status", "updated_at"])
        op.create_index("idx_auto_training_pool_task_status", "auto_training_sample_pool", ["task_id", "pool_status"])
    if "auto_training_round" not in tables:
        op.create_table(
            "auto_training_round",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("plan_version_id", sa.Integer(), nullable=False),
            sa.Column("round_no", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(48), nullable=False),
            sa.Column("trigger_type", sa.String(32), nullable=False),
            sa.Column("trigger_snapshot_json", sa.Text(), nullable=False),
            sa.Column("dataset_signature", sa.String(64)),
            sa.Column("manifest_rel_path", sa.String(512)),
            sa.Column("manifest_sha256", sa.String(64)),
            sa.Column("execution_signature", sa.String(64), nullable=False),
            sa.Column("baseline_job_id", sa.Integer()),
            sa.Column("training_task_id", sa.Integer()),
            sa.Column("yolo_train_job_id", sa.Integer()),
            sa.Column("evaluation_run_id", sa.Integer()),
            sa.Column("bm1688_export_id", sa.Integer()),
            sa.Column("edge_validation_job_id", sa.Integer()),
            sa.Column("eligible_sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("train_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("val_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("test_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("decision_code", sa.String(64)),
            sa.Column("decision_json", sa.Text()),
            sa.Column("stage_status_json", sa.Text(), nullable=False),
            sa.Column("stage_errors_json", sa.Text(), nullable=False),
            sa.Column("retry_count_json", sa.Text(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("queued_at", sa.DateTime(timezone=True)),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("plan_id", "round_no", name="uq_auto_training_round_no"),
            sa.UniqueConstraint("execution_signature", name="uq_auto_training_round_execution"),
        )
    if "auto_training_round_sample" not in tables:
        op.create_table(
            "auto_training_round_sample",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("round_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("sample_id", sa.Integer(), nullable=False),
            sa.Column("split", sa.String(16), nullable=False),
            sa.Column("sample_signature", sa.String(64), nullable=False),
            sa.Column("annotation_sha256", sa.String(64), nullable=False),
            sa.Column("image_sha256", sa.String(64), nullable=False),
            sa.Column("source_sample_manifest_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("round_id", "sample_id", name="uq_auto_training_round_sample"),
        )
    if "auto_training_outbox" not in tables:
        op.create_table(
            "auto_training_outbox",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("event_name", sa.String(64), nullable=False),
            sa.Column("aggregate_type", sa.String(32), nullable=False),
            sa.Column("aggregate_id", sa.String(128), nullable=False),
            sa.Column("event_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("lease_owner", sa.String(128)),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
            sa.Column("last_error", sa.Text()),
            sa.Column("processed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("idx_auto_training_outbox_dispatch", "auto_training_outbox", ["status", "available_at", "id"])
        op.create_index("idx_auto_training_outbox_lease", "auto_training_outbox", ["status", "lease_expires_at"])
    if "auto_training_audit_event" not in tables:
        op.create_table(
            "auto_training_audit_event",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("round_id", sa.Integer()),
            sa.Column("actor_type", sa.String(24), nullable=False),
            sa.Column("actor_id", sa.Integer()),
            sa.Column("action", sa.String(64), nullable=False),
            sa.Column("before_json", sa.Text()),
            sa.Column("after_json", sa.Text()),
            sa.Column("reason", sa.Text()),
            sa.Column("request_id", sa.String(64)),
            sa.Column("event_sha256", sa.String(64), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade():
    for table in (
        "auto_training_audit_event",
        "auto_training_outbox",
        "auto_training_round_sample",
        "auto_training_round",
        "auto_training_sample_pool",
        "auto_training_task_scope",
        "auto_training_plan_version",
        "auto_training_plan",
    ):
        if table in _tables():
            op.drop_table(table)
