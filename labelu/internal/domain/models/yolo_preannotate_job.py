from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from labelu.internal.common.db import Base


class YoloPreannotateJob(Base):
    """异步批量：对任务样本用本地 YOLO 权重推理并写入预标注。"""

    __tablename__ = "yolo_preannotate_job"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), index=True)
    created_by = Column(Integer, ForeignKey("user.id"))
    status = Column(String(32), default="PENDING")
    sample_count = Column(Integer, default=0)
    processed_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    model_kind = Column(String(32), nullable=False)
    model_id = Column(Integer, nullable=False)
    task_contract_id = Column(Integer, ForeignKey("task_annotation_contract.id"), nullable=True, index=True)
    task_contract_revision = Column(Integer, nullable=True)
    labels_snapshot_json = Column(Text, nullable=True)
    label_set_signature = Column(String(64), nullable=True, index=True)
    # Durable execution input.  The API stores the resolved model path,
    # sample ids and inference options before enqueueing so a separate worker
    # can resume after an API restart without reconstructing request state.
    payload_json = Column(Text, nullable=True)
    lease_owner = Column(String(128), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    # Every takeover increments this generation; stale workers cannot mutate
    # progress or terminal state with an old generation.
    lease_fencing_token = Column(Integer, nullable=False, default=0, server_default="0")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        onupdate=datetime.now,
    )
