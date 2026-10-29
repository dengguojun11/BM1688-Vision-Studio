"""add canonical deployment releases, per-device desired/reported state and observations"""

from alembic import op
import sqlalchemy as sa


revision = "n3o4p5q6r7s8"
down_revision = "m2n3o4p5q6r7"
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index(name, table, columns):
    if name not in {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}:
        op.create_index(name, table, columns)


def upgrade() -> None:
    tables = _tables()
    if "deployment_release" not in tables:
        op.create_table(
            "deployment_release",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("project_id", sa.Integer(), nullable=False),
            sa.Column("model_version_id", sa.Integer(), nullable=False),
            sa.Column("release_key", sa.String(160), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("target_device_ids_json", sa.Text(), nullable=False),
            sa.Column("gray_ratio", sa.Float(), nullable=False),
            sa.Column("observation_seconds", sa.Integer(), nullable=False),
            sa.Column("rollback_model_version_id", sa.Integer(), nullable=True),
            sa.Column("metrics_json", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=False),
            sa.Column("approved_by", sa.Integer(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
            sa.ForeignKeyConstraint(["model_version_id"], ["model_version.id"]),
            sa.ForeignKeyConstraint(["rollback_model_version_id"], ["model_version.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["user.id"]),
            sa.ForeignKeyConstraint(["approved_by"], ["user.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("release_key", name="uq_deployment_release_key"),
        )
    _index("ix_deployment_release_project_id", "deployment_release", ["project_id"])
    _index("ix_deployment_release_model_version_id", "deployment_release", ["model_version_id"])
    _index("ix_deployment_release_status", "deployment_release", ["status"])
    _index("idx_deployment_release_project_status", "deployment_release", ["project_id", "status", "updated_at"])
    _index("idx_deployment_release_model", "deployment_release", ["model_version_id", "created_at"])

    tables = _tables()
    if "deployment_target" not in tables:
        op.create_table(
            "deployment_target",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("release_id", sa.Integer(), nullable=False),
            sa.Column("device_id", sa.String(128), nullable=False),
            sa.Column("desired_state", sa.String(32), nullable=False),
            sa.Column("reported_state", sa.String(32), nullable=False),
            sa.Column("target_bundle_sha256", sa.String(64), nullable=False),
            sa.Column("reported_bundle_sha256", sa.String(64), nullable=True),
            sa.Column("agent_version", sa.String(64), nullable=True),
            sa.Column("capabilities_json", sa.Text(), nullable=True),
            sa.Column("metrics_json", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["release_id"], ["deployment_release.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("release_id", "device_id", name="uq_deployment_target_device"),
        )
    _index("ix_deployment_target_release_id", "deployment_target", ["release_id"])
    _index("idx_deployment_target_release_state", "deployment_target", ["release_id", "desired_state", "reported_state"])
    _index("idx_deployment_target_device_state", "deployment_target", ["device_id", "reported_state", "updated_at"])

    tables = _tables()
    if "deployment_observation" not in tables:
        op.create_table(
            "deployment_observation",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=False),
            sa.Column("observation_key", sa.String(160), nullable=False),
            sa.Column("status", sa.String(24), nullable=False),
            sa.Column("fps", sa.Float(), nullable=True),
            sa.Column("latency_p95_ms", sa.Float(), nullable=True),
            sa.Column("latency_p99_ms", sa.Float(), nullable=True),
            sa.Column("cpu_percent", sa.Float(), nullable=True),
            sa.Column("npu_percent", sa.Float(), nullable=True),
            sa.Column("memory_percent", sa.Float(), nullable=True),
            sa.Column("temperature_c", sa.Float(), nullable=True),
            sa.Column("metrics_json", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["target_id"], ["deployment_target.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("target_id", "observation_key", name="uq_deployment_observation_key"),
        )
    _index("ix_deployment_observation_target_id", "deployment_observation", ["target_id"])
    _index("idx_deployment_observation_target_time", "deployment_observation", ["target_id", "sampled_at"])


def downgrade() -> None:
    tables = _tables()
    if "deployment_observation" in tables:
        op.drop_table("deployment_observation")
    if "deployment_target" in tables:
        op.drop_table("deployment_target")
    if "deployment_release" in tables:
        op.drop_table("deployment_release")
