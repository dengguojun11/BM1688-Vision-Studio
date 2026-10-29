"""Versioned prompts compiled from annotation contracts.

The binding is deliberately separate from the label contract.  A contract
defines the legal annotation vocabulary; this table records the model-facing
rendering of one immutable contract revision and its publication state.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from labelu.internal.common.db import Base


class AnnotationPromptBinding(Base):
    __tablename__ = "annotation_prompt_binding"
    __table_args__ = (
        Index(
            "ix_annotation_prompt_binding_dataset_contract",
            "dataset_label_contract_id",
            "contract_revision",
            "operation",
            "status",
        ),
        Index(
            "ix_annotation_prompt_binding_task_contract",
            "task_annotation_contract_id",
            "contract_revision",
            "operation",
            "status",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    dataset_id = Column(Integer, ForeignKey("dataset.id"), nullable=True, index=True)
    dataset_label_contract_id = Column(
        Integer,
        ForeignKey("dataset_label_contract.id"),
        nullable=True,
        index=True,
    )
    task_annotation_contract_id = Column(
        Integer,
        ForeignKey("task_annotation_contract.id"),
        nullable=True,
        index=True,
    )
    contract_revision = Column(Integer, nullable=False)
    label_set_signature = Column(String(64), nullable=False, index=True)
    class_order_signature = Column(String(64), nullable=False, index=True)
    operation = Column(String(64), nullable=False, default="bbox_detection", index=True)
    provider_key = Column(String(64), nullable=False, default="volcengine", index=True)
    model_name = Column(String(256), nullable=True)
    profile_key = Column(String(128), nullable=False, default="annotation_bbox_v1")
    profile_version = Column(String(32), nullable=False, default="1")
    compiler_version = Column(String(32), nullable=False, default="1")
    prompt_spec_json = Column(Text, nullable=False, default="{}")
    generated_prompt = Column(Text, nullable=False, default="")
    edited_prompt = Column(Text, nullable=True)
    effective_prompt = Column(Text, nullable=False, default="")
    prompt_sha256 = Column(String(64), nullable=False, index=True)
    warnings_json = Column(Text, nullable=False, default="[]")
    status = Column(String(24), nullable=False, default="DRAFT", index=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    published_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.now)
    published_at = Column(DateTime(timezone=True), nullable=True)
