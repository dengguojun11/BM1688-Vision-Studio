"""restore mysql business id auto increment after comment sync

Revision ID: y7z8a9b0c1d
Revises: x6y7z8a9b0c
Create Date: 2026-07-03 20:10:00
"""

from alembic import op
import sqlalchemy as sa

import labelu.internal.domain.models  # noqa: F401
from labelu.internal.common.db import Base
from labelu.internal.common.schema_governance import (
    apply_schema_comment_defaults,
    get_column_comment,
    iter_governed_tables,
)


# revision identifiers, used by Alembic.
revision = "y7z8a9b0c1d"
down_revision = "x6y7z8a9b0c"
branch_labels = None
depends_on = None


def _normalize_existing_server_default(value):
    if value in (None, False):
        return None
    text = str(value).strip()
    if not text or text.upper() == "NULL":
        return None
    return sa.text(text)


def _iter_business_id_columns():
    apply_schema_comment_defaults(Base.metadata, include_governance=False)
    for table in iter_governed_tables(Base.metadata, include_governance=False):
        column = table.columns.get("id")
        if column is None:
            continue
        if not column.primary_key:
            continue
        if not isinstance(column.type, sa.Integer):
            continue
        yield table, column


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != "mysql":
        return

    inspector = sa.inspect(bind)
    op.execute(sa.text("SET FOREIGN_KEY_CHECKS=0"))
    try:
        for table, column in _iter_business_id_columns():
            reflected_columns = {item["name"]: item for item in inspector.get_columns(table.name)}
            reflected = reflected_columns.get(column.name)
            if reflected is None:
                continue
            extra = str(reflected.get("extra") or reflected.get("autoincrement") or "").strip().lower()
            if extra == "auto_increment":
                continue
            op.alter_column(
                table.name,
                column.name,
                existing_type=reflected["type"],
                existing_nullable=reflected["nullable"],
                existing_server_default=_normalize_existing_server_default(reflected.get("default")),
                existing_comment=reflected.get("comment"),
                autoincrement=True,
                comment=get_column_comment(table, column),
            )
    finally:
        op.execute(sa.text("SET FOREIGN_KEY_CHECKS=1"))


def downgrade():
    # Avoid reverting back to a broken non-auto-increment primary key state.
    pass
