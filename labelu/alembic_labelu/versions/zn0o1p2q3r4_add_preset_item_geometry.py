"""Store the drawing geometry on legacy label-preset items.

Revision ID: zn0o1p2q3r4
Revises: zm9n0o1p2q3
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "zn0o1p2q3r4"
down_revision = "zm9n0o1p2q3"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    if table not in _tables(bind):
        return set()
    return {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "label_preset_item" not in _tables(bind):
        return

    if "annotation_kind" not in _columns(bind, "label_preset_item"):
        op.add_column(
            "label_preset_item",
            sa.Column(
                "annotation_kind",
                sa.String(length=32),
                nullable=False,
                server_default="bbox",
            ),
        )

    metadata = sa.MetaData()
    items = sa.Table("label_preset_item", metadata, autoload_with=bind)
    # Legacy rows are rectangle labels unless their canonical value is the
    # four-corner polygon label.  The update is deliberately value-based so
    # partially migrated databases are repaired without duplicate rows.
    bind.execute(
        items.update()
        .where(sa.func.lower(sa.func.trim(items.c.label_value)) == "platepoint")
        .values(annotation_kind="polygon")
    )
    bind.execute(
        items.update()
        .where(
            sa.or_(
                items.c.annotation_kind.is_(None),
                ~sa.func.lower(sa.func.trim(items.c.annotation_kind)).in_(
                    ["bbox", "polygon"]
                ),
            )
        )
        .values(annotation_kind="bbox")
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "label_preset_item" in _tables(bind) and "annotation_kind" in _columns(
        bind, "label_preset_item"
    ):
        op.drop_column("label_preset_item", "annotation_kind")
