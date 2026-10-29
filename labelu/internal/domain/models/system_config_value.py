from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.schema import Index, UniqueConstraint

from labelu.internal.common.db import Base


class SystemConfigValue(Base):
    __tablename__ = "system_config_value"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True, comment="主键ID")
    item_id = Column(
        Integer,
        ForeignKey("system_config_item.id"),
        nullable=False,
        index=True,
        comment="配置项ID",
    )
    scope_key = Column(
        String(128),
        nullable=False,
        default="__global__",
        comment="作用域键",
    )
    env_name = Column(
        String(64),
        nullable=False,
        default="default",
        comment="环境名称",
    )
    value_json = Column(Text, nullable=True, comment="当前值JSON")
    value_source = Column(
        String(32),
        nullable=False,
        default="db",
        comment="当前值来源",
    )
    is_active = Column(Boolean, nullable=False, default=True, comment="是否生效")
    version_no = Column(Integer, nullable=False, default=1, comment="版本号")
    updated_by = Column(
        Integer,
        ForeignKey("user.id"),
        nullable=True,
        index=True,
        comment="更新人ID",
    )
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
        UniqueConstraint(
            "item_id",
            "scope_key",
            "env_name",
            name="uq_system_config_value_item_scope_env",
        ),
        Index("idx_system_config_value_item_active", "item_id", "is_active"),
        {"comment": "系统配置当前值"},
    )
