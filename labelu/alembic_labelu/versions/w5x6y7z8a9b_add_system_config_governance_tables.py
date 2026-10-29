"""add system config governance tables

Revision ID: w5x6y7z8a9b
Revises: v3w4x5y6z7a8
Create Date: 2026-07-03 13:30:00
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "w5x6y7z8a9b"
down_revision = "v3w4x5y6z7a8"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "system_config_group",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="主键ID"),
        sa.Column("group_key", sa.String(length=64), nullable=False, comment="配置组唯一键"),
        sa.Column("group_name_zh", sa.String(length=128), nullable=False, comment="配置组中文名称"),
        sa.Column("group_comment", sa.Text(), nullable=True, comment="配置组说明"),
        sa.Column("scope_type", sa.String(length=32), nullable=False, server_default="global", comment="作用域类型"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否启用"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0", comment="排序值"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_key", name="uq_system_config_group_group_key"),
        comment="系统配置组",
    )
    op.create_index(op.f("ix_system_config_group_id"), "system_config_group", ["id"], unique=False)
    op.create_index(
        "idx_system_config_group_enabled_sort",
        "system_config_group",
        ["is_enabled", "sort_order"],
        unique=False,
    )

    op.create_table(
        "system_config_item",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="主键ID"),
        sa.Column("group_id", sa.Integer(), nullable=False, comment="所属配置组ID"),
        sa.Column("item_key", sa.String(length=128), nullable=False, comment="配置项唯一键"),
        sa.Column("item_name_zh", sa.String(length=128), nullable=False, comment="配置项中文名称"),
        sa.Column("item_comment", sa.Text(), nullable=True, comment="配置项说明"),
        sa.Column("value_type", sa.String(length=32), nullable=False, comment="配置值类型"),
        sa.Column("default_value_json", sa.Text(), nullable=True, comment="默认值JSON"),
        sa.Column(
            "source_mode",
            sa.String(length=32),
            nullable=False,
            server_default="db_primary",
            comment="来源模式：env_only / db_primary / db_overlay",
        ),
        sa.Column("env_var_name", sa.String(length=128), nullable=True, comment="环境变量名称"),
        sa.Column("is_secret", sa.Boolean(), nullable=False, server_default=sa.false(), comment="是否敏感"),
        sa.Column("validation_rule_json", sa.Text(), nullable=True, comment="校验规则JSON"),
        sa.Column("enum_options_json", sa.Text(), nullable=True, comment="枚举选项JSON"),
        sa.Column("scope_desc", sa.String(length=255), nullable=True, comment="作用域说明"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0", comment="排序值"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.CheckConstraint(
            "source_mode in ('env_only','db_primary','db_overlay')",
            name="ck_system_config_item_source_mode",
        ),
        sa.ForeignKeyConstraint(["group_id"], ["system_config_group.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("group_id", "item_key", name="uq_system_config_item_group_item_key"),
        comment="系统配置项",
    )
    op.create_index(op.f("ix_system_config_item_id"), "system_config_item", ["id"], unique=False)
    op.create_index(op.f("ix_system_config_item_group_id"), "system_config_item", ["group_id"], unique=False)
    op.create_index(
        "idx_system_config_item_group_sort",
        "system_config_item",
        ["group_id", "sort_order"],
        unique=False,
    )

    op.create_table(
        "system_config_value",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="主键ID"),
        sa.Column("item_id", sa.Integer(), nullable=False, comment="配置项ID"),
        sa.Column("scope_key", sa.String(length=128), nullable=False, server_default="__global__", comment="作用域键"),
        sa.Column("env_name", sa.String(length=64), nullable=False, server_default="default", comment="环境名称"),
        sa.Column("value_json", sa.Text(), nullable=True, comment="当前值JSON"),
        sa.Column("value_source", sa.String(length=32), nullable=False, server_default="db", comment="当前值来源"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否生效"),
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1", comment="版本号"),
        sa.Column("updated_by", sa.Integer(), nullable=True, comment="更新人ID"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.ForeignKeyConstraint(["item_id"], ["system_config_item.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "scope_key",
            "env_name",
            name="uq_system_config_value_item_scope_env",
        ),
        comment="系统配置当前值",
    )
    op.create_index(op.f("ix_system_config_value_id"), "system_config_value", ["id"], unique=False)
    op.create_index(op.f("ix_system_config_value_item_id"), "system_config_value", ["item_id"], unique=False)
    op.create_index(op.f("ix_system_config_value_updated_by"), "system_config_value", ["updated_by"], unique=False)
    op.create_index(
        "idx_system_config_value_item_active",
        "system_config_value",
        ["item_id", "is_active"],
        unique=False,
    )

    op.create_table(
        "system_config_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="主键ID"),
        sa.Column("item_id", sa.Integer(), nullable=False, comment="配置项ID"),
        sa.Column("scope_key", sa.String(length=128), nullable=False, server_default="__global__", comment="作用域键"),
        sa.Column("env_name", sa.String(length=64), nullable=False, server_default="default", comment="环境名称"),
        sa.Column("before_value_json", sa.Text(), nullable=True, comment="变更前值JSON"),
        sa.Column("after_value_json", sa.Text(), nullable=True, comment="变更后值JSON"),
        sa.Column("change_type", sa.String(length=32), nullable=False, server_default="update", comment="变更类型"),
        sa.Column("change_reason", sa.Text(), nullable=True, comment="变更原因"),
        sa.Column("changed_by", sa.Integer(), nullable=True, comment="变更人ID"),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=True, comment="变更时间"),
        sa.ForeignKeyConstraint(["changed_by"], ["user.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["system_config_item.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment="系统配置变更历史",
    )
    op.create_index(op.f("ix_system_config_history_id"), "system_config_history", ["id"], unique=False)
    op.create_index(op.f("ix_system_config_history_item_id"), "system_config_history", ["item_id"], unique=False)
    op.create_index(op.f("ix_system_config_history_changed_by"), "system_config_history", ["changed_by"], unique=False)
    op.create_index(op.f("ix_system_config_history_changed_at"), "system_config_history", ["changed_at"], unique=False)
    op.create_index(
        "idx_system_config_history_item_changed_at",
        "system_config_history",
        ["item_id", "changed_at"],
        unique=False,
    )
    op.create_index(
        "idx_system_config_history_scope_env",
        "system_config_history",
        ["scope_key", "env_name"],
        unique=False,
    )

    op.create_table(
        "system_data_dictionary",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False, comment="主键ID"),
        sa.Column("object_type", sa.String(length=32), nullable=False, comment="对象类型"),
        sa.Column("schema_name", sa.String(length=64), nullable=False, server_default="", comment="数据库模式名"),
        sa.Column("table_name", sa.String(length=128), nullable=False, server_default="", comment="表名"),
        sa.Column("column_name", sa.String(length=128), nullable=False, server_default="", comment="字段名"),
        sa.Column("object_key", sa.String(length=255), nullable=False, server_default="", comment="对象唯一键"),
        sa.Column("name_zh", sa.String(length=128), nullable=False, comment="中文名称"),
        sa.Column("comment_zh", sa.Text(), nullable=False, comment="中文解释"),
        sa.Column("business_definition", sa.Text(), nullable=True, comment="业务定义"),
        sa.Column("data_type", sa.String(length=128), nullable=True, comment="数据类型"),
        sa.Column("enum_values_json", sa.Text(), nullable=True, comment="枚举值JSON"),
        sa.Column("validation_rule_json", sa.Text(), nullable=True, comment="校验规则JSON"),
        sa.Column("scope_desc", sa.String(length=255), nullable=True, comment="作用域说明"),
        sa.Column("is_sensitive", sa.Boolean(), nullable=False, server_default=sa.false(), comment="是否敏感"),
        sa.Column("source_type", sa.String(length=32), nullable=True, comment="来源类型"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active", comment="状态"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "object_type",
            "schema_name",
            "table_name",
            "column_name",
            "object_key",
            name="uq_system_data_dictionary_object",
        ),
        comment="系统数据字典",
    )
    op.create_index(op.f("ix_system_data_dictionary_id"), "system_data_dictionary", ["id"], unique=False)
    op.create_index(
        "idx_system_data_dictionary_table_column",
        "system_data_dictionary",
        ["table_name", "column_name"],
        unique=False,
    )
    op.create_index(
        "idx_system_data_dictionary_object_type_status",
        "system_data_dictionary",
        ["object_type", "status"],
        unique=False,
    )


def downgrade():
    op.drop_index("idx_system_data_dictionary_object_type_status", table_name="system_data_dictionary")
    op.drop_index("idx_system_data_dictionary_table_column", table_name="system_data_dictionary")
    op.drop_index(op.f("ix_system_data_dictionary_id"), table_name="system_data_dictionary")
    op.drop_table("system_data_dictionary")

    op.drop_index("idx_system_config_history_scope_env", table_name="system_config_history")
    op.drop_index("idx_system_config_history_item_changed_at", table_name="system_config_history")
    op.drop_index(op.f("ix_system_config_history_changed_at"), table_name="system_config_history")
    op.drop_index(op.f("ix_system_config_history_changed_by"), table_name="system_config_history")
    op.drop_index(op.f("ix_system_config_history_item_id"), table_name="system_config_history")
    op.drop_index(op.f("ix_system_config_history_id"), table_name="system_config_history")
    op.drop_table("system_config_history")

    op.drop_index("idx_system_config_value_item_active", table_name="system_config_value")
    op.drop_index(op.f("ix_system_config_value_updated_by"), table_name="system_config_value")
    op.drop_index(op.f("ix_system_config_value_item_id"), table_name="system_config_value")
    op.drop_index(op.f("ix_system_config_value_id"), table_name="system_config_value")
    op.drop_table("system_config_value")

    op.drop_index("idx_system_config_item_group_sort", table_name="system_config_item")
    op.drop_index(op.f("ix_system_config_item_group_id"), table_name="system_config_item")
    op.drop_index(op.f("ix_system_config_item_id"), table_name="system_config_item")
    op.drop_table("system_config_item")

    op.drop_index("idx_system_config_group_enabled_sort", table_name="system_config_group")
    op.drop_index(op.f("ix_system_config_group_id"), table_name="system_config_group")
    op.drop_table("system_config_group")
