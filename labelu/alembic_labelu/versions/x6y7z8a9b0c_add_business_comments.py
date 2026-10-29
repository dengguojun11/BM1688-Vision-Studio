"""add business table comments and column comments

Revision ID: x6y7z8a9b0c
Revises: w5x6y7z8a9b
Create Date: 2026-07-03 18:30:00
"""

from alembic import op
import sqlalchemy as sa

import labelu.internal.domain.models  # noqa: F401
from labelu.internal.common.db import Base
from labelu.internal.common.schema_governance import (
    apply_schema_comment_defaults,
    get_column_comment,
    get_table_comment,
    iter_governed_tables,
)


# revision identifiers, used by Alembic.
revision = "x6y7z8a9b0c"
down_revision = "w5x6y7z8a9b"
branch_labels = None
depends_on = None


def _normalize_existing_server_default(value):
    if value in (None, False):
        return None
    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return None
    return sa.text(text)


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return

    apply_schema_comment_defaults(Base.metadata, include_governance=False)
    inspector = sa.inspect(bind)
    op.execute(sa.text("SET FOREIGN_KEY_CHECKS=0"))
    try:
        for table in iter_governed_tables(Base.metadata, include_governance=False):
            op.execute(
                sa.text(f"ALTER TABLE `{table.name}` COMMENT = :comment").bindparams(
                    comment=get_table_comment(table)
                )
            )
            reflected_columns = {item["name"]: item for item in inspector.get_columns(table.name)}
            for column in table.columns:
                reflected = reflected_columns.get(column.name)
                if reflected is None:
                    continue
                op.alter_column(
                    table.name,
                    column.name,
                    existing_type=reflected["type"],
                    existing_nullable=reflected["nullable"],
                    existing_server_default=_normalize_existing_server_default(
                        reflected.get("default")
                    ),
                    comment=get_column_comment(table, column),
                )
    finally:
        op.execute(sa.text("SET FOREIGN_KEY_CHECKS=1"))


def downgrade():
    # 本次迁移的目标是把现有业务表注释补齐到 MySQL；
    # downgrade 不再尝试恢复历史空注释状态，避免误删旧列原有注释。
    pass
