from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects import mysql

from labelu.internal.common.db import Base


class TrainingTask(Base):
    __tablename__ = "training_task"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_training_task_owner_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    owner_id = Column(Integer, ForeignKey("user.id"), index=True, nullable=False)
    name = Column(String(128), nullable=False)
    mode = Column(String(32), nullable=False, default="label_dimension")
    status = Column(String(32), nullable=False, default="PENDING")
    primary_task_id = Column(Integer, ForeignKey("task.id"), nullable=True, index=True)
    selected_presets_snapshot_json = Column(Text, nullable=False, default="[]")
    selected_label_items_snapshot_json = Column(Text, nullable=False, default="[]")
    merged_class_names_snapshot_json = Column(Text, nullable=False, default="[]")
    source_tasks_snapshot_json = Column(Text, nullable=False, default="[]")
    ignored_tasks_snapshot_json = Column(Text, nullable=False, default="[]")
    preview_summary_json = Column(Text, nullable=True)
    latest_job_id = Column(Integer, nullable=True, index=True)
    latest_job_status = Column(String(32), nullable=True)
    series_id = Column(Integer, ForeignKey("model_series.id", ondelete="SET NULL"), nullable=True, index=True)
    variant_id = Column(Integer, ForeignKey("model_variant.id", ondelete="SET NULL"), nullable=True, index=True)
    branch_key = Column(String(64), nullable=True, index=True)
    selection_contract_sha256 = Column(String(64), nullable=True, index=True)
    selection_contract_json = Column(Text, nullable=True)
    sample_conditions_json = Column(Text, nullable=True)
    dataset_category_id = Column(Integer, ForeignKey("dataset_category.id"), nullable=True, index=True)
    dataset_category_snapshot_json = Column(Text, nullable=True)
    selected_label_ids_json = Column(Text, nullable=False, default="[]")
    class_names_snapshot_json = Column(Text, nullable=False, default="[]")
    label_set_signature = Column(String(64), nullable=True, index=True)
    class_order_signature = Column(String(64), nullable=True, index=True)
    source_sample_snapshot_json = Column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True
    )
    training_recipe_key = Column(String(128), nullable=True, index=True)
    training_recipe_version = Column(Integer, nullable=True)
    runtime_profile = Column(String(128), nullable=True, index=True)
    runtime_interface_version = Column(Integer, nullable=True)
    quality_policy_snapshot_json = Column(Text, nullable=True)
    auto_training_plan_id = Column(Integer, ForeignKey("auto_training_plan.id"), nullable=True, index=True)
    auto_training_round_id = Column(Integer, ForeignKey("auto_training_round.id"), nullable=True, unique=True)
    source_mode = Column(String(16), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
