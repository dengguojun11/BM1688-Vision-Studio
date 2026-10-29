from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects import mysql

from labelu.internal.common.db import Base


LONGTEXT = Text().with_variant(mysql.LONGTEXT(), "mysql")


class AutoTrainingPlan(Base):
    __tablename__ = "auto_training_plan"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_auto_training_plan_owner_name"),
        Index("idx_auto_training_plan_status_next", "status", "next_evaluation_at"),
        Index(
            "idx_auto_training_plan_series_contract",
            "series_type",
            "dataset_category_id",
            "label_set_signature",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Tenant and immutable source lineage.  Nullable keeps historical plans
    # readable while new dataset-driven plans always populate both fields.
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True, index=True)
    source_dataset_version_id = Column(
        Integer,
        ForeignKey("dataset_version.id"),
        nullable=True,
        unique=True,
        index=True,
    )
    owner_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    series_type = Column(String(32), nullable=False, index=True)
    dataset_category_id = Column(
        Integer, ForeignKey("dataset_category.id"), nullable=False, index=True
    )
    label_set_signature = Column(String(64), nullable=False, index=True)
    class_order_signature = Column(String(64), nullable=False, index=True)
    status = Column(String(48), nullable=False, index=True)
    status_reason_code = Column(String(64), nullable=True)
    status_reason_json = Column(LONGTEXT, nullable=True)
    current_version_id = Column(Integer, nullable=True)
    active_round_id = Column(Integer, nullable=True)
    # These are application-validated lineage pointers. Keeping the reverse
    # foreign keys here would create cycles with the pipeline tables, which in
    # turn makes create/drop ordering and partial migrations unreliable.
    champion_job_id = Column(Integer, nullable=True)
    champion_export_id = Column(Integer, nullable=True)
    last_qualified_change_at = Column(DateTime(timezone=True), nullable=True)
    next_evaluation_at = Column(DateTime(timezone=True), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    updated_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)


class AutoTrainingPlanVersion(Base):
    __tablename__ = "auto_training_plan_version"
    __table_args__ = (
        UniqueConstraint("plan_id", "version_no", name="uq_auto_training_plan_version"),
        UniqueConstraint("plan_id", "config_sha256", name="uq_auto_training_plan_config"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("auto_training_plan.id"), nullable=False, index=True)
    version_no = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="DRAFT")
    config_json = Column(LONGTEXT, nullable=False)
    config_sha256 = Column(String(64), nullable=False)
    quality_policy_snapshot_json = Column(LONGTEXT, nullable=True)
    quality_policy_signature = Column(String(64), nullable=True)
    label_contract_snapshot_json = Column(LONGTEXT, nullable=True)
    training_recipe_snapshot_json = Column(LONGTEXT, nullable=True)
    runtime_profile_snapshot_json = Column(LONGTEXT, nullable=True)
    evaluation_binding_json = Column(LONGTEXT, nullable=True)
    missing_fields_json = Column(LONGTEXT, nullable=False, default="[]")
    validation_errors_json = Column(LONGTEXT, nullable=False, default="[]")
    change_reason = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    approved_at = Column(DateTime(timezone=True), nullable=True)


class AutoTrainingTaskScope(Base):
    __tablename__ = "auto_training_task_scope"
    __table_args__ = (
        UniqueConstraint("plan_id", "task_id", name="uq_auto_training_task_scope"),
        Index("idx_auto_training_scope_plan_status", "plan_id", "scope_status"),
        Index("idx_auto_training_scope_task", "task_id", "updated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("auto_training_plan.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    scope_status = Column(String(32), nullable=False, default="WATCHING")
    inclusion_source = Column(String(24), nullable=False, default="FUTURE_MATCH")
    eligible_sample_count = Column(Integer, nullable=False, default=0)
    pending_review_count = Column(Integer, nullable=False, default=0)
    excluded_sample_count = Column(Integer, nullable=False, default=0)
    contract_signature = Column(String(64), nullable=True)
    reason_code = Column(String(64), nullable=True)
    reason_json = Column(LONGTEXT, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )


class AutoTrainingSamplePool(Base):
    __tablename__ = "auto_training_sample_pool"
    __table_args__ = (
        UniqueConstraint("plan_id", "sample_id", name="uq_auto_training_pool_sample"),
        Index("idx_auto_training_pool_plan_status", "plan_id", "pool_status", "updated_at"),
        Index("idx_auto_training_pool_task_status", "task_id", "pool_status"),
        Index("idx_auto_training_pool_signature", "plan_id", "sample_signature"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("auto_training_plan.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    sample_id = Column(Integer, ForeignKey("task_sample.id"), nullable=False)
    pool_status = Column(String(32), nullable=False, default="PENDING_HASH")
    reason_code = Column(String(64), nullable=True)
    annotation_sha256 = Column(String(64), nullable=True)
    image_sha256 = Column(String(64), nullable=True)
    sample_signature = Column(String(64), nullable=True)
    label_contract_signature = Column(String(64), nullable=True)
    human_review_id = Column(Integer, nullable=True)
    data_grant_id = Column(Integer, nullable=True)
    duplicate_group_id = Column(Integer, nullable=True)
    eligible_since = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )


class SampleHumanReview(Base):
    __tablename__ = "sample_human_review"
    __table_args__ = (
        UniqueConstraint(
            "sample_id", "annotation_sha256", "review_status", name="uq_sample_human_review"
        ),
        Index("idx_sample_human_review_current", "sample_id", "review_status", "invalidated_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False, index=True)
    sample_id = Column(Integer, ForeignKey("task_sample.id"), nullable=False, index=True)
    annotation_sha256 = Column(String(64), nullable=False)
    review_status = Column(String(24), nullable=False)
    comment = Column(Text, nullable=True)
    reviewed_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    reviewed_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    invalidated_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    invalidated_at = Column(DateTime(timezone=True), nullable=True)
    invalidation_reason = Column(Text, nullable=True)


class AutoTrainingRound(Base):
    __tablename__ = "auto_training_round"
    __table_args__ = (
        UniqueConstraint("plan_id", "round_no", name="uq_auto_training_round_no"),
        UniqueConstraint("execution_signature", name="uq_auto_training_round_execution"),
        Index("idx_auto_training_round_plan_state", "plan_id", "state"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("auto_training_plan.id"), nullable=False, index=True)
    plan_version_id = Column(
        Integer, ForeignKey("auto_training_plan_version.id"), nullable=False
    )
    round_no = Column(Integer, nullable=False)
    state = Column(String(48), nullable=False, default="PREPARING", index=True)
    trigger_type = Column(String(32), nullable=False, default="MANUAL")
    trigger_snapshot_json = Column(LONGTEXT, nullable=False, default="{}")
    dataset_signature = Column(String(64), nullable=True, index=True)
    manifest_rel_path = Column(String(512), nullable=True)
    manifest_sha256 = Column(String(64), nullable=True)
    execution_signature = Column(String(64), nullable=False)
    baseline_job_id = Column(Integer, nullable=True)
    training_task_id = Column(Integer, nullable=True)
    yolo_train_job_id = Column(Integer, nullable=True)
    evaluation_run_id = Column(Integer, nullable=True)
    bm1688_export_id = Column(Integer, nullable=True)
    edge_validation_job_id = Column(Integer, nullable=True)
    publish_job_id = Column(String(64), nullable=True, index=True)
    publish_status = Column(String(32), nullable=True, index=True)
    published_model_id = Column(Integer, nullable=True, index=True)
    publish_requested_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    publish_reason = Column(Text, nullable=True)
    publish_error = Column(Text, nullable=True)
    publish_requested_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    eligible_sample_count = Column(Integer, nullable=False, default=0)
    train_count = Column(Integer, nullable=False, default=0)
    val_count = Column(Integer, nullable=False, default=0)
    test_count = Column(Integer, nullable=False, default=0)
    decision_code = Column(String(64), nullable=True, index=True)
    decision_json = Column(LONGTEXT, nullable=True)
    stage_status_json = Column(LONGTEXT, nullable=False, default="{}")
    stage_errors_json = Column(LONGTEXT, nullable=False, default="{}")
    retry_count_json = Column(LONGTEXT, nullable=False, default="{}")
    version = Column(Integer, nullable=False, default=1)
    queued_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )


class AutoTrainingRoundSample(Base):
    __tablename__ = "auto_training_round_sample"
    __table_args__ = (
        UniqueConstraint("round_id", "sample_id", name="uq_auto_training_round_sample"),
        Index("idx_auto_training_round_split", "round_id", "split"),
        Index("idx_auto_training_round_task", "round_id", "task_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    round_id = Column(Integer, ForeignKey("auto_training_round.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    sample_id = Column(Integer, ForeignKey("task_sample.id"), nullable=False)
    split = Column(String(16), nullable=False)
    sample_signature = Column(String(64), nullable=False)
    annotation_sha256 = Column(String(64), nullable=False)
    image_sha256 = Column(String(64), nullable=False)
    source_sample_manifest_json = Column(LONGTEXT, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class AutoTrainingOutbox(Base):
    __tablename__ = "auto_training_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_auto_training_outbox_idempotency"),
        Index("idx_auto_training_outbox_dispatch", "status", "available_at", "id"),
        Index("idx_auto_training_outbox_lease", "status", "lease_expires_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_name = Column(String(64), nullable=False)
    aggregate_type = Column(String(32), nullable=False)
    aggregate_id = Column(String(128), nullable=False)
    event_version = Column(Integer, nullable=False, default=1)
    payload_json = Column(LONGTEXT, nullable=False)
    idempotency_key = Column(String(160), nullable=False)
    status = Column(String(24), nullable=False, default="PENDING")
    available_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )


class AutoTrainingEvaluationRun(Base):
    __tablename__ = "auto_training_evaluation_run"
    __table_args__ = (
        UniqueConstraint("round_id", name="uq_auto_training_evaluation_round"),
        Index("idx_auto_training_evaluation_policy", "policy_signature", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    round_id = Column(Integer, ForeignKey("auto_training_round.id"), nullable=False)
    policy_signature = Column(String(64), nullable=False)
    evaluation_binding_signature = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, index=True)
    request_manifest_rel_path = Column(String(512), nullable=True)
    request_manifest_sha256 = Column(String(64), nullable=True, index=True)
    evaluator_worker_id = Column(String(128), nullable=True)
    evaluator_claimed_at = Column(DateTime(timezone=True), nullable=True)
    evaluator_lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    overall_metrics_json = Column(LONGTEXT, nullable=True)
    per_label_metrics_json = Column(LONGTEXT, nullable=True)
    plate_component_metrics_json = Column(LONGTEXT, nullable=True)
    champion_comparison_json = Column(LONGTEXT, nullable=True)
    evidence_manifest_rel_path = Column(String(512), nullable=True)
    evidence_manifest_sha256 = Column(String(64), nullable=True)
    decision_code = Column(String(64), nullable=True, index=True)
    decision_json = Column(LONGTEXT, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now
    )


class AutoTrainingAuditEvent(Base):
    __tablename__ = "auto_training_audit_event"
    __table_args__ = (
        UniqueConstraint("event_sha256", name="uq_auto_training_audit_event"),
        Index("idx_auto_training_audit_plan", "plan_id", "created_at"),
        Index("idx_auto_training_audit_round", "round_id", "created_at"),
        Index("idx_auto_training_audit_action", "action", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("auto_training_plan.id"), nullable=False)
    round_id = Column(Integer, ForeignKey("auto_training_round.id"), nullable=True)
    actor_type = Column(String(24), nullable=False)
    actor_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    action = Column(String(64), nullable=False)
    before_json = Column(LONGTEXT, nullable=True)
    after_json = Column(LONGTEXT, nullable=True)
    reason = Column(Text, nullable=True)
    request_id = Column(String(64), nullable=True)
    event_sha256 = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


@event.listens_for(AutoTrainingRoundSample, "before_update")
@event.listens_for(AutoTrainingRoundSample, "before_delete")
def _prevent_frozen_round_sample_mutation(_mapper, _connection, _target) -> None:
    raise ValueError("auto training round samples are immutable")
