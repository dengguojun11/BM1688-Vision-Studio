from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String

from labelu.internal.common.db import Base


class YoloImportedModel(Base):
    """用户上传的外部 YOLO 权重（.pt），与本系统训练作业分离存储。"""

    __tablename__ = "yolo_imported_model"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    display_name = Column(String(128), nullable=True)
    category = Column(String(128), nullable=True, default="")
    original_filename = Column(String(256), nullable=True)
    weights_rel_path = Column(String(512), nullable=False)
    file_size = Column(BigInteger(), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.now)
    updated_at = Column(DateTime(timezone=True), default=datetime.now, onupdate=datetime.now)
