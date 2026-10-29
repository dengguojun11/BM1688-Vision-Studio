"""add auto training governance tables

Revision ID: j8k9l0m1n2o3
Revises: i7j8k9l0m1n2
"""

from alembic import op
import sqlalchemy as sa


revision = "j8k9l0m1n2o3"
down_revision = "i7j8k9l0m1n2"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    definitions = {
        "sample_human_review": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("sample_id", sa.Integer(), nullable=False), sa.Column("annotation_sha256", sa.String(64), nullable=False),
            sa.Column("review_status", sa.String(24), nullable=False), sa.Column("comment", sa.Text()),
            sa.Column("reviewed_by", sa.Integer(), nullable=False), sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("invalidated_by", sa.Integer()), sa.Column("invalidated_at", sa.DateTime(timezone=True)),
            sa.Column("invalidation_reason", sa.Text()),
            sa.UniqueConstraint("sample_id", "annotation_sha256", "review_status", name="uq_sample_human_review"),
        ],
        "training_evaluation_dataset": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(128), nullable=False), sa.Column("dataset_kind", sa.String(32), nullable=False),
            sa.Column("series_type", sa.String(32), nullable=False), sa.Column("dataset_category_id", sa.Integer(), nullable=False),
            sa.Column("label_set_signature", sa.String(64), nullable=False), sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False), sa.Column("manifest_rel_path", sa.String(512), nullable=False),
            sa.Column("manifest_sha256", sa.String(64), nullable=False, unique=True), sa.Column("sample_count", sa.Integer(), nullable=False),
            sa.Column("grouping_policy_json", sa.Text(), nullable=False), sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("approved_by", sa.Integer()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("approved_at", sa.DateTime(timezone=True)), sa.Column("retired_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("owner_id", "name", "version_no", name="uq_training_eval_dataset_version"),
        ],
        "training_evaluation_sample": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("evaluation_dataset_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=False), sa.Column("sample_id", sa.Integer(), nullable=False),
            sa.Column("sample_signature", sa.String(64), nullable=False), sa.Column("annotation_sha256", sa.String(64), nullable=False),
            sa.Column("image_sha256", sa.String(64), nullable=False), sa.Column("split_role", sa.String(24), nullable=False),
            sa.Column("identity_group_key", sa.String(256)), sa.Column("capture_group_key", sa.String(256)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("evaluation_dataset_id", "sample_id", name="uq_training_eval_sample"),
        ],
        "auto_training_evaluation_run": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("round_id", sa.Integer(), nullable=False, unique=True),
            sa.Column("policy_signature", sa.String(64), nullable=False), sa.Column("evaluation_binding_signature", sa.String(64), nullable=False),
            sa.Column("status", sa.String(32), nullable=False), sa.Column("overall_metrics_json", sa.Text()),
            sa.Column("per_label_metrics_json", sa.Text()), sa.Column("plate_component_metrics_json", sa.Text()),
            sa.Column("champion_comparison_json", sa.Text()), sa.Column("evidence_manifest_rel_path", sa.String(512)),
            sa.Column("evidence_manifest_sha256", sa.String(64)), sa.Column("decision_code", sa.String(64)),
            sa.Column("decision_json", sa.Text()), sa.Column("error_message", sa.Text()),
            sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        ],
        "quality_policy_approval": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("policy_name", sa.String(128), nullable=False),
            sa.Column("policy_version", sa.Integer(), nullable=False), sa.Column("policy_signature", sa.String(64), nullable=False),
            sa.Column("scope_signature", sa.String(64), nullable=False), sa.Column("status", sa.String(32), nullable=False),
            sa.Column("policy_snapshot_json", sa.Text(), nullable=False), sa.Column("submitted_by", sa.Integer(), nullable=False),
            sa.Column("approved_by", sa.Integer()), sa.Column("submission_reason", sa.Text()), sa.Column("decision_reason", sa.Text()),
            sa.Column("submitted_at", sa.DateTime(timezone=True)), sa.Column("approved_at", sa.DateTime(timezone=True)),
            sa.Column("retired_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("policy_signature", "scope_signature", name="uq_quality_policy_approval"),
        ],
        "sample_duplicate_group": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("group_signature", sa.String(64), nullable=False, unique=True),
            sa.Column("canonical_sample_id", sa.Integer(), nullable=False), sa.Column("match_kind", sa.String(32), nullable=False),
            sa.Column("threshold_snapshot_json", sa.Text(), nullable=False), sa.Column("status", sa.String(24), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        ],
        "sample_asset_fingerprint": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("sample_id", sa.Integer(), nullable=False), sa.Column("image_sha256", sa.String(64), nullable=False),
            sa.Column("perceptual_hash", sa.String(128)), sa.Column("source_identity", sa.String(512)),
            sa.Column("capture_group_key", sa.String(256)), sa.Column("duplicate_group_id", sa.Integer()),
            sa.Column("fingerprint_version", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("sample_id", "image_sha256", name="uq_sample_asset_fingerprint"),
        ],
        "training_data_grant": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("grant_scope_type", sa.String(24), nullable=False),
            sa.Column("grant_scope_id", sa.String(128), nullable=False), sa.Column("owner_id", sa.Integer(), nullable=False),
            sa.Column("grantee_plan_id", sa.Integer(), nullable=False), sa.Column("status", sa.String(24), nullable=False),
            sa.Column("valid_from", sa.DateTime(timezone=True)), sa.Column("valid_to", sa.DateTime(timezone=True)),
            sa.Column("terms_snapshot_json", sa.Text(), nullable=False), sa.Column("terms_sha256", sa.String(64), nullable=False),
            sa.Column("created_by", sa.Integer(), nullable=False), sa.Column("approved_by", sa.Integer()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("approved_at", sa.DateTime(timezone=True)),
            sa.Column("revoked_at", sa.DateTime(timezone=True)), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("grant_scope_type", "grant_scope_id", "grantee_plan_id", "terms_sha256", name="uq_training_data_grant"),
        ],
        "training_data_withdrawal": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("task_id", sa.Integer(), nullable=False),
            sa.Column("sample_id", sa.Integer()), sa.Column("request_reason", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False), sa.Column("impact_json", sa.Text()),
            sa.Column("requested_by", sa.Integer(), nullable=False), sa.Column("closed_by", sa.Integer()),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False), sa.Column("analyzed_at", sa.DateTime(timezone=True)),
            sa.Column("closed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        ],
        "bm1688_calibration_manifest": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("round_id", sa.Integer(), nullable=False, unique=True),
            sa.Column("export_id", sa.Integer(), unique=True), sa.Column("evaluation_dataset_id", sa.Integer(), nullable=False),
            sa.Column("manifest_rel_path", sa.String(512), nullable=False), sa.Column("manifest_sha256", sa.String(64), nullable=False),
            sa.Column("sample_count", sa.Integer(), nullable=False), sa.Column("selection_policy_json", sa.Text(), nullable=False),
            sa.Column("compiler_fingerprint", sa.String(128), nullable=False), sa.Column("quantization_signature", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("manifest_sha256", "compiler_fingerprint", "quantization_signature", name="uq_bm1688_calibration_execution"),
        ],
        "edge_model_deployment": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("plan_id", sa.Integer(), nullable=False),
            sa.Column("round_id", sa.Integer(), nullable=False), sa.Column("export_id", sa.Integer(), nullable=False),
            sa.Column("edge_validation_job_id", sa.Integer()), sa.Column("edge_device_id", sa.String(64), nullable=False),
            sa.Column("deployment_kind", sa.String(24), nullable=False), sa.Column("target_bundle_sha256", sa.String(64), nullable=False),
            sa.Column("reported_bundle_sha256", sa.String(64)), sa.Column("status", sa.String(32), nullable=False),
            sa.Column("health_json", sa.Text()), sa.Column("requested_by", sa.Integer(), nullable=False),
            sa.Column("confirmed_by", sa.Integer()), sa.Column("requested_at", sa.DateTime(timezone=True)),
            sa.Column("confirmed_at", sa.DateTime(timezone=True)), sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("round_id", "edge_device_id", "deployment_kind", "target_bundle_sha256", name="uq_edge_model_deployment"),
        ],
    }
    tables = _tables()
    for name, columns in definitions.items():
        if name not in tables:
            op.create_table(name, *columns)


def downgrade():
    for table in (
        "edge_model_deployment", "bm1688_calibration_manifest", "training_data_withdrawal",
        "training_data_grant", "sample_asset_fingerprint", "sample_duplicate_group",
        "quality_policy_approval", "auto_training_evaluation_run", "training_evaluation_sample",
        "training_evaluation_dataset", "sample_human_review",
    ):
        if table in _tables():
            op.drop_table(table)
