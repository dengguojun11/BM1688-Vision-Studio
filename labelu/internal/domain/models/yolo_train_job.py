from enum import Enum
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
)
from sqlalchemy.dialects import mysql

from labelu.internal.common.db import Base


class YoloTrainStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class YoloTrainJob(Base):
    __tablename__ = "yolo_train_job"
    __table_args__ = (
        UniqueConstraint(
            "created_by",
            "idempotency_key",
            name="uq_yolo_train_job_creator_idempotency",
        ),
        Index(
            "ix_yolo_train_job_training_worker_scan",
            "status",
            "queue_lease_expires_at",
            "id",
        ),
        Index(
            "ix_yolo_train_job_eval_full_operation",
            "ai_eval_full_status",
            "ai_eval_full_operation_id",
        ),
        Index(
            "ix_yolo_train_job_eval_next_operation",
            "ai_eval_next_status",
            "ai_eval_next_operation_id",
        ),
        Index(
            "ix_yolo_train_job_series_variant",
            "series_id",
            "variant_id",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    training_task_id = Column(
        Integer, ForeignKey("training_task.id"), index=True, nullable=True
    )
    series_id = Column(
        Integer,
        ForeignKey("model_series.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    variant_id = Column(
        Integer,
        ForeignKey("model_variant.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    parent_model_version_id = Column(Integer, nullable=True, index=True)
    parent_training_job_id = Column(Integer, nullable=True, index=True)
    branch_key = Column(String(64), nullable=True, index=True)
    selection_contract_sha256 = Column(String(64), nullable=True, index=True)
    selection_contract_json = Column(Text, nullable=True)
    sample_conditions_json = Column(Text, nullable=True)
    inheritance_mode = Column(String(32), nullable=True)
    task_id = Column(Integer, ForeignKey("task.id"), index=True)
    project_id = Column(
        Integer,
        ForeignKey("project.id"),
        nullable=True,
        index=True,
        comment="Denormalized project boundary copied from source task",
    )
    created_by = Column(Integer, ForeignKey("user.id"))
    idempotency_key = Column(String(256), nullable=True)
    idempotency_request_hash = Column(String(64), nullable=True)
    source_type = Column(String(32), nullable=False, default="internal", index=True)
    source_system = Column(String(64), nullable=True, index=True)
    source_external_task_id = Column(String(128), nullable=True, index=True)
    source_external_package_id = Column(String(128), nullable=True, index=True)
    source_integration_id = Column(Integer, nullable=True, index=True)
    status = Column(String(32), default=YoloTrainStatus.PENDING.value)
    job_type = Column(
        String(32),
        default="yolo_detection",
        nullable=False,
        index=True,
        comment="yolo_detection、plate_detection 或 plate_recognition",
    )
    model_weights = Column(String(256), nullable=True)
    epochs = Column(Integer, default=50)
    imgsz = Column(Integer, default=640)
    train_sample_count = Column(Integer, default=0)
    val_sample_count = Column(Integer, default=0)
    weights_path = Column(Text, nullable=True)
    metrics_summary = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    ai_eval_full_status = Column(String(16), nullable=True)
    ai_eval_next_status = Column(String(16), nullable=True)
    # 每个评估阶段的当前执行代次。历史作业允许为空；新领取必须生成随机值。
    ai_eval_full_operation_id = Column(String(64), nullable=True, index=True)
    ai_eval_next_operation_id = Column(String(64), nullable=True, index=True)
    category = Column(String(128), nullable=True, default="")
    hyperparams_json = Column(Text, nullable=True)
    source_task_ids_json = Column(
        Text,
        nullable=True,
        comment="合并训练时的来源任务 id 列表 JSON，如 [1,2,3]",
    )
    dataset_manifest_json = Column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"),
        nullable=True,
        comment="训练数据不可变清单、样本统计与内容指纹 JSON",
    )
    dataset_category_id = Column(
        Integer, ForeignKey("dataset_category.id"), nullable=True, index=True
    )
    class_names_json = Column(Text, nullable=True)
    label_set_signature = Column(String(64), nullable=True, index=True)
    class_order_signature = Column(String(64), nullable=True, index=True)
    data_yaml_sha256 = Column(String(64), nullable=True)
    dataset_content_signature = Column(String(64), nullable=True, index=True)
    source_sample_manifest_json = Column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True
    )
    training_recipe_key = Column(String(128), nullable=True, index=True)
    training_recipe_version = Column(Integer, nullable=True)
    quality_policy_snapshot_json = Column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=True
    )
    runtime_profile = Column(String(128), nullable=True, index=True)
    runtime_interface_version = Column(Integer, nullable=True)
    auto_training_plan_id = Column(
        Integer, ForeignKey("auto_training_plan.id"), nullable=True, index=True
    )
    auto_training_round_id = Column(
        Integer, ForeignKey("auto_training_round.id"), nullable=True, unique=True
    )
    queue_priority = Column(Integer, nullable=True, index=True)
    queue_source = Column(String(16), nullable=True, index=True)
    queue_lease_owner = Column(String(128), nullable=True)
    queue_lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    execution_lease_owner = Column(String(128), nullable=True, index=True)
    execution_lease_expires_at = Column(
        DateTime(timezone=True), nullable=True, index=True
    )
    execution_fencing_token = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    assigned_gpu_json = Column(
        Text,
        nullable=True,
        comment="独立训练 Worker 已领取的 GPU 槽位 JSON",
    )
    execution_runtime_pid = Column(Integer, nullable=True)
    execution_signature = Column(String(64), nullable=True, unique=True)
    component_status_json = Column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"),
        nullable=True,
        comment="复合训练各组件状态JSON",
    )
    component_artifacts_json = Column(
        Text().with_variant(mysql.LONGTEXT(), "mysql"),
        nullable=True,
        comment="复合训练各组件PT/ONNX/BModel产物JSON",
    )
    execution_mode = Column(
        String(16),
        default="local",
        nullable=False,
        comment="local=本机训练；remote=远程 GPU 节点",
    )
    train_node_id = Column(
        String(64),
        nullable=True,
        comment="remote 时对应 remote_train_nodes.json 中的节点 id",
    )
    remote_status_json = Column(
        Text,
        nullable=True,
        comment="远程训练状态 JSON：上传/启动/同步阶段等",
    )
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.now, onupdate=datetime.now
    )
