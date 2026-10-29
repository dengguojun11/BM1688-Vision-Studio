from enum import Enum
from datetime import datetime

from sqlalchemy.schema import Index, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from labelu.internal.common.db import Base


class TaskStatus(str, Enum):
    """
    task status
    """

    DRAFT = "DRAFT"
    IMPORTED = "IMPORTED"
    CONFIGURED = "CONFIGURED"
    INPROGRESS = "INPROGRESS"
    FINISHED = "FINISHED"


class MediaType(str, Enum):
    """
    task meida type
    """

    IMAGE = "IMAGE"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"


class Task(Base):
    __tablename__ = "task"
    __table_args__ = (
        UniqueConstraint(
            "created_by",
            "idempotency_key",
            name="uq_task_creator_idempotency",
        ),
        Index("idx_task_id_deleted_at_index", "id", "deleted_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    name = Column(String(64), index=True)
    description = Column(String(1024), comment="task description")
    tips = Column(String(1024), comment="task tips")
    last_sample_inner_id = Column(
        Integer, default=0, comment="The last inner id of sample in a task"
    )
    config = Column(Text, comment="task config yaml")
    media_type = Column(
        String(32),
        comment="task media type: image, video, audio",
    )
    status = Column(String(32), default=TaskStatus.DRAFT.value, comment="task status")
    training_ready = Column(
        Boolean,
        nullable=True,
        comment="image task: global tags + valid rects on every sample",
    )
    annotation_audit_json = Column(
        Text,
        nullable=True,
        comment="annotation audit summary JSON",
    )
    created_by = Column(Integer, ForeignKey(column="user.id"), index=True)
    idempotency_key = Column(
        String(256),
        nullable=True,
        comment="Optional client retry key scoped to the task creator",
    )
    idempotency_request_hash = Column(
        String(64),
        nullable=True,
        comment="Canonical request digest for idempotency-key reuse checks",
    )
    updated_by = Column(
        Integer, ForeignKey(column="user.id"), comment="Last time a task was updated by"
    )
    current_annotation_contract_id = Column(
        Integer,
        nullable=True,
        index=True,
        comment="Current versioned annotation label contract",
    )
    dataset_category_id = Column(
        Integer,
        ForeignKey("dataset_category.id"),
        nullable=True,
        index=True,
        comment="Independent dataset management category",
    )
    project_id = Column(
        Integer,
        ForeignKey("project.id"),
        nullable=True,
        index=True,
        comment="7GAI project boundary; nullable for legacy tasks during migration",
    )
    dataset_id = Column(
        Integer,
        ForeignKey("dataset.id"),
        nullable=True,
        index=True,
        comment="Logical 7GAI dataset; nullable for legacy tasks",
    )
    dataset_label_contract_id = Column(
        Integer,
        ForeignKey("dataset_label_contract.id"),
        nullable=True,
        index=True,
        comment="Dataset label contract referenced by this task",
    )
    task_label_scope_mode = Column(
        String(16),
        nullable=False,
        default="FULL",
        comment="FULL uses all dataset labels; SUBSET uses a declared subset",
    )
    source_type = Column(String(32), nullable=False, default="internal", index=True)
    source_system = Column(String(64), nullable=True, index=True)
    source_external_task_id = Column(String(128), nullable=True, index=True)
    source_external_package_id = Column(String(128), nullable=True, index=True)
    source_integration_id = Column(Integer, nullable=True, index=True)
    created_at = Column(
        DateTime(timezone=True), default=datetime.now, comment="Time a task was created"
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        onupdate=datetime.now,
        comment="Last time a task was updated",
    )
    deleted_at = Column(DateTime(timezone=True), index=True, comment="Task delete time")

    collaborators = relationship(
        "User", secondary="task_collaborator", back_populates="tasks"
    )
    owner = relationship("User", foreign_keys=[created_by])
    updater = relationship("User", foreign_keys=[updated_by])
    dataset_category = relationship(
        "DatasetCategory", foreign_keys=[dataset_category_id]
    )
    project = relationship("Project", foreign_keys=[project_id])
    dataset = relationship("Dataset", foreign_keys=[dataset_id])
    dataset_label_contract = relationship(
        "DatasetLabelContract",
        foreign_keys=[dataset_label_contract_id],
    )
