from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.schema import Index, UniqueConstraint

from labelu.internal.common.db import Base


class SystemConfigItem(Base):
    __tablename__ = "system_config_item"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True, comment="主键ID")
    group_id = Column(
        Integer,
        ForeignKey("system_config_group.id"),
        nullable=False,
        index=True,
        comment="所属配置组ID",
    )
    item_key = Column(String(128), nullable=False, comment="配置项唯一键")
    item_name_zh = Column(String(128), nullable=False, comment="配置项中文名称")
    item_comment = Column(Text, nullable=True, comment="配置项说明")
    value_type = Column(String(32), nullable=False, comment="配置值类型")
    default_value_json = Column(Text, nullable=True, comment="默认值JSON")
    source_mode = Column(
        String(32),
        nullable=False,
        default="db_primary",
        comment="来源模式：env_only / db_primary / db_overlay",
    )
    env_var_name = Column(String(128), nullable=True, comment="环境变量名称")
    is_secret = Column(Boolean, nullable=False, default=False, comment="是否敏感")
    validation_rule_json = Column(Text, nullable=True, comment="校验规则JSON")
    enum_options_json = Column(Text, nullable=True, comment="枚举选项JSON")
    scope_desc = Column(String(255), nullable=True, comment="作用域说明")
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
        UniqueConstraint("group_id", "item_key", name="uq_system_config_item_group_item_key"),
        CheckConstraint(
            "source_mode in ('env_only','db_primary','db_overlay')",
            name="ck_system_config_item_source_mode",
        ),
        Index("idx_system_config_item_group_sort", "group_id", "sort_order"),
        {"comment": "系统配置项"},
    )
