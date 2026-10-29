from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.schema import Index, UniqueConstraint

from labelu.internal.common.db import Base


class SystemDataDictionary(Base):
    __tablename__ = "system_data_dictionary"

    id = Column(Integer, primary_key=True, autoincrement=True, index=True, comment="主键ID")
    object_type = Column(String(32), nullable=False, comment="对象类型")
    schema_name = Column(String(64), nullable=False, default="", comment="数据库模式名")
    table_name = Column(String(128), nullable=False, default="", comment="表名")
    column_name = Column(String(128), nullable=False, default="", comment="字段名")
    object_key = Column(String(255), nullable=False, default="", comment="对象唯一键")
    name_zh = Column(String(128), nullable=False, comment="中文名称")
    comment_zh = Column(Text, nullable=False, comment="中文解释")
    business_definition = Column(Text, nullable=True, comment="业务定义")
    data_type = Column(String(128), nullable=True, comment="数据类型")
    enum_values_json = Column(Text, nullable=True, comment="枚举值JSON")
    validation_rule_json = Column(Text, nullable=True, comment="校验规则JSON")
    scope_desc = Column(String(255), nullable=True, comment="作用域说明")
    is_sensitive = Column(Boolean, nullable=False, default=False, comment="是否敏感")
    source_type = Column(String(32), nullable=True, comment="来源类型")
    status = Column(String(32), nullable=False, default="active", comment="状态")
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
            "object_type",
            "schema_name",
            "table_name",
            "column_name",
            "object_key",
            name="uq_system_data_dictionary_object",
        ),
        Index("idx_system_data_dictionary_table_column", "table_name", "column_name"),
        Index("idx_system_data_dictionary_object_type_status", "object_type", "status"),
        {"comment": "系统数据字典"},
    )
