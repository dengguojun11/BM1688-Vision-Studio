from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text

from labelu.internal.common.db import Base


class TagtoolClassificationModel(Base):
    """Uploaded ResNet-style multi-head checkpoint for tagTool pre-annotation."""

    __tablename__ = "tagtool_classification_model"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    display_name = Column(String(128), nullable=True)
    category = Column(String(128), nullable=True, default="")
    original_filename = Column(String(256), nullable=True)
    weights_rel_path = Column(String(512), nullable=False)
    label_map_json = Column(Text, nullable=False)
    file_size = Column(BigInteger(), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
