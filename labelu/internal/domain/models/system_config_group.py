from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.schema import Index, UniqueConstraint

from labelu.internal.common.db import Base


class SystemConfigGroup(Base):
    __tablename__ = "system_config_group"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True, comment="主键ID")
    group_key = Column(String(64), nullable=False, comment="配置组唯一键")
    group_name_zh = Column(String(128), nullable=False, comment="配置组中文名称")
    group_comment = Column(Text, nullable=True, comment="配置组说明")
    scope_type = Column(
        String(32),
        nullable=False,
        default="global",
        comment="作用域类型",
    )
    is_enabled = Column(Boolean, nullable=False, default=True, comment="是否启用")
    sort_order = Column(Integer, nullable=False, default=0, comment="排序值")
    created_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        comment="创建时间",
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.now,
        onupdate=datetime.now,
        comment="更新时间",
    )

    __table_args__ = (
        UniqueConstraint("group_key", name="uq_system_config_group_group_key"),
        Index("idx_system_config_group_enabled_sort", "is_enabled", "sort_order"),
        {"comment": "系统配置组"},
    )
