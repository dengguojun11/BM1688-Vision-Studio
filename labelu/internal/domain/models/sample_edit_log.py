from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from labelu.internal.common.db import Base


class SampleEditLog(Base):
    __tablename__ = "sample_edit_log"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), index=True, nullable=False)
    sample_id = Column(Integer, ForeignKey("task_sample.id"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), index=True, nullable=False)
    action = Column(String(32), nullable=False, default="annotation_edit")
    previous_state = Column(String(32), nullable=True)
    new_state = Column(String(32), nullable=True)
    previous_annotated_count = Column(Integer, nullable=True)
    new_annotated_count = Column(Integer, nullable=True)
    previous_data = Column(Text, nullable=True)
    new_data = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now, nullable=False, index=True)
