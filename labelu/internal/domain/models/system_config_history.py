from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.schema import Index

from labelu.internal.common.db import Base


class SystemConfigHistory(Base):
    __tablename__ = "system_config_history"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True, comment="主键ID")
    item_id = Column(
        Integer,
        ForeignKey("system_config_item.id"),
        nullable=False,
        index=True,
        comment="配置项ID",
    )
    scope_key = Column(String(128), nullable=False, default="__global__", comment="作用域键")
    env_name = Column(String(64), nullable=False, default="default", comment="环境名称")
    before_value_json = Column(Text, nullable=True, comment="变更前值JSON")
    after_value_json = Column(Text, nullable=True, comment="变更后值JSON")
    change_type = Column(String(32), nullable=False, default="update", comment="变更类型")
    change_reason = Column(Text, nullable=True, comment="变更原因")
    changed_by = Column(
        Integer,
        ForeignKey("user.id"),
        nullable=True,
        index=True,
        comment="变更人ID",
    )
    changed_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        index=True,
        comment="变更时间",
    )

    __table_args__ = (
        Index("idx_system_config_history_item_changed_at", "item_id", "changed_at"),
        Index("idx_system_config_history_scope_env", "scope_key", "env_name"),
        {"comment": "系统配置变更历史"},
    )
