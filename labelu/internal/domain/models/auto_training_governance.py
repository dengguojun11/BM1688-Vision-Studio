from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects import mysql

from labelu.internal.common.db import Base


LONGTEXT = Text().with_variant(mysql.LONGTEXT(), "mysql")


class TrainingEvaluationDataset(Base):
    __tablename__ = "training_evaluation_dataset"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", "version_no", name="uq_training_eval_dataset_version"),
        UniqueConstraint("manifest_sha256", name="uq_training_eval_dataset_manifest"),
        Index("idx_training_eval_dataset_scope", "series_type", "dataset_category_id", "label_set_signature", "status"),
        Index("idx_training_eval_dataset_plan", "plan_id", "dataset_kind", "status"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("auto_training_plan.id"), nullable=True, index=True)
    owner_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    name = Column(String(128), nullable=False)
    dataset_kind = Column(String(32), nullable=False)
    series_type = Column(String(32), nullable=False)
    dataset_category_id = Column(Integer, ForeignKey("dataset_category.id"), nullable=False)
    label_set_signature = Column(String(64), nullable=False)
    version_no = Column(Integer, nullable=False, default=1)
    status = Column(String(32), nullable=False, default="DRAFT")
    manifest_rel_path = Column(String(512), nullable=False)
    manifest_sha256 = Column(String(64), nullable=False)
    sample_count = Column(Integer, nullable=False, default=0)
    grouping_policy_json = Column(LONGTEXT, nullable=False, default="{}")
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)


class TrainingEvaluationSample(Base):
    __tablename__ = "training_evaluation_sample"
    __table_args__ = (
        UniqueConstraint("evaluation_dataset_id", "sample_id", name="uq_training_eval_sample"),
        Index("idx_training_eval_identity", "evaluation_dataset_id", "identity_group_key"),
        Index("idx_training_eval_capture", "evaluation_dataset_id", "capture_group_key"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    evaluation_dataset_id = Column(Integer, ForeignKey("training_evaluation_dataset.id"), nullable=False)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    sample_id = Column(Integer, ForeignKey("task_sample.id"), nullable=False)
    sample_signature = Column(String(64), nullable=False)
    annotation_sha256 = Column(String(64), nullable=False)
    image_sha256 = Column(String(64), nullable=False)
    split_role = Column(String(24), nullable=False)
    identity_group_key = Column(String(256), nullable=True)
    capture_group_key = Column(String(256), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class QualityPolicyApproval(Base):
    __tablename__ = "quality_policy_approval"
    __table_args__ = (
        UniqueConstraint("policy_signature", "scope_signature", name="uq_quality_policy_approval"),
        Index("idx_quality_policy_approval_name", "policy_name", "policy_version", "status"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    policy_name = Column(String(128), nullable=False)
    policy_version = Column(Integer, nullable=False)
    policy_signature = Column(String(64), nullable=False)
    scope_signature = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default="PENDING_APPROVAL")
    policy_snapshot_json = Column(LONGTEXT, nullable=False)
    submitted_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    submission_reason = Column(Text, nullable=True)
    decision_reason = Column(Text, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    retired_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)


class SampleDuplicateGroup(Base):
    __tablename__ = "sample_duplicate_group"
    __table_args__ = (
        UniqueConstraint("group_signature", name="uq_sample_duplicate_group_signature"),
        Index("idx_sample_duplicate_canonical", "canonical_sample_id"),
        Index("idx_sample_duplicate_status", "status", "updated_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    group_signature = Column(String(64), nullable=False)
    canonical_sample_id = Column(Integer, ForeignKey("task_sample.id"), nullable=False)
    match_kind = Column(String(32), nullable=False)
    threshold_snapshot_json = Column(LONGTEXT, nullable=False)
    status = Column(String(24), nullable=False, default="ACTIVE")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)


class SampleAssetFingerprint(Base):
    __tablename__ = "sample_asset_fingerprint"
    __table_args__ = (
        UniqueConstraint("sample_id", "image_sha256", name="uq_sample_asset_fingerprint"),
        Index("idx_sample_asset_sha", "image_sha256"),
        Index("idx_sample_asset_phash", "perceptual_hash"),
        Index("idx_sample_asset_capture", "capture_group_key"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    sample_id = Column(Integer, ForeignKey("task_sample.id"), nullable=False)
    image_sha256 = Column(String(64), nullable=False)
    perceptual_hash = Column(String(128), nullable=True)
    source_identity = Column(String(512), nullable=True)
    capture_group_key = Column(String(256), nullable=True)
    duplicate_group_id = Column(Integer, ForeignKey("sample_duplicate_group.id"), nullable=True)
    fingerprint_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)


class TrainingDataGrant(Base):
    __tablename__ = "training_data_grant"
    __table_args__ = (
        UniqueConstraint("grant_scope_type", "grant_scope_id", "grantee_plan_id", "terms_sha256", name="uq_training_data_grant"),
        Index("idx_training_data_grant_active", "grantee_plan_id", "status", "valid_to"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    grant_scope_type = Column(String(24), nullable=False)
    grant_scope_id = Column(String(128), nullable=False)
    owner_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    grantee_plan_id = Column(Integer, ForeignKey("auto_training_plan.id"), nullable=False)
    status = Column(String(24), nullable=False, default="DRAFT")
    valid_from = Column(DateTime(timezone=True), nullable=True)
    valid_to = Column(DateTime(timezone=True), nullable=True)
    terms_snapshot_json = Column(LONGTEXT, nullable=False)
    terms_sha256 = Column(String(64), nullable=False)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    approved_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)


class TrainingDataWithdrawal(Base):
    __tablename__ = "training_data_withdrawal"
    __table_args__ = (Index("idx_training_withdrawal_target", "task_id", "sample_id", "status"),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=False)
    sample_id = Column(Integer, ForeignKey("task_sample.id"), nullable=True)
    request_reason = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="REQUESTED")
    impact_json = Column(LONGTEXT, nullable=True)
    requested_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    closed_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    requested_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    analyzed_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)


class Bm1688CalibrationManifest(Base):
    __tablename__ = "bm1688_calibration_manifest"
    __table_args__ = (
        UniqueConstraint("round_id", name="uq_bm1688_calibration_round"),
        UniqueConstraint("manifest_sha256", "compiler_fingerprint", "quantization_signature", name="uq_bm1688_calibration_execution"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    round_id = Column(Integer, ForeignKey("auto_training_round.id"), nullable=False)
    export_id = Column(Integer, ForeignKey("yolo_bm1688_export.id"), nullable=True, unique=True)
    evaluation_dataset_id = Column(Integer, ForeignKey("training_evaluation_dataset.id"), nullable=False)
    manifest_rel_path = Column(String(512), nullable=False)
    manifest_sha256 = Column(String(64), nullable=False)
    sample_count = Column(Integer, nullable=False)
    selection_policy_json = Column(LONGTEXT, nullable=False)
    compiler_fingerprint = Column(String(128), nullable=False)
    quantization_signature = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)


class EdgeModelDeployment(Base):
    __tablename__ = "edge_model_deployment"
    __table_args__ = (
        UniqueConstraint("round_id", "edge_device_id", "deployment_kind", "target_bundle_sha256", name="uq_edge_model_deployment"),
        Index("idx_edge_model_deployment_device", "edge_device_id", "status", "updated_at"),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    plan_id = Column(Integer, ForeignKey("auto_training_plan.id"), nullable=False)
    round_id = Column(Integer, ForeignKey("auto_training_round.id"), nullable=False)
    export_id = Column(Integer, ForeignKey("yolo_bm1688_export.id"), nullable=False)
    edge_validation_job_id = Column(Integer, ForeignKey("yolo_edge_validation_job.id"), nullable=True)
    edge_device_id = Column(String(64), nullable=False)
    deployment_kind = Column(String(24), nullable=False)
    target_bundle_sha256 = Column(String(64), nullable=False)
    reported_bundle_sha256 = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False)
    health_json = Column(LONGTEXT, nullable=True)
    requested_by = Column(Integer, ForeignKey("user.id"), nullable=False)
    confirmed_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    requested_at = Column(DateTime(timezone=True), nullable=True)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now, onupdate=datetime.now)
