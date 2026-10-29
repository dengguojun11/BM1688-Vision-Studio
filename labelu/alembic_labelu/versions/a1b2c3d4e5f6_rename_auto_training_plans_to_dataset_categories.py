"""rename legacy automatic training plans to dataset category names"""

from alembic import op
import sqlalchemy as sa
import re


revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    plans = sa.Table("auto_training_plan", metadata, autoload_with=bind)
    categories = sa.Table("dataset_category", metadata, autoload_with=bind)
    rows = bind.execute(
        sa.select(
            plans.c.id,
            plans.c.owner_id,
            plans.c.dataset_category_id,
            plans.c.name,
        )
        .select_from(
            plans.join(
                categories,
                categories.c.id == plans.c.dataset_category_id,
            )
        )
        .where(
            plans.c.deleted_at.is_(None),
            sa.or_(
                plans.c.name.like("auto-training-%"),
                plans.c.name.like("auto_training-%"),
            ),
            categories.c.deleted_at.is_(None),
        )
        .order_by(plans.c.owner_id, plans.c.dataset_category_id, plans.c.id)
    ).mappings().all()
    used = set(
        bind.execute(
            sa.select(plans.c.owner_id, plans.c.name).where(plans.c.deleted_at.is_(None))
        ).all()
    )
    for row in rows:
        if not re.fullmatch(
            r"auto-training-\d+-[A-Za-z0-9_]+-[0-9a-f]{8}-[0-9a-f]{8}",
            str(row["name"] or ""),
        ):
            continue
        category_name = str(
            bind.execute(
                sa.select(categories.c.name).where(categories.c.id == row["dataset_category_id"])
            ).scalar_one()
            or ""
        ).strip()
        if not category_name:
            continue
        candidate = category_name
        suffix = 2
        while (row["owner_id"], candidate) in used:
            candidate = f"{category_name}（{suffix}）"
            suffix += 1
        if candidate == row["name"]:
            continue
        bind.execute(
            plans.update().where(plans.c.id == row["id"]).values(name=candidate)
        )
        used.add((row["owner_id"], candidate))


def downgrade() -> None:
    # Legacy generated names contained contract hashes and cannot be restored
    # without an audit snapshot. Keep the Chinese names on downgrade.
    pass
