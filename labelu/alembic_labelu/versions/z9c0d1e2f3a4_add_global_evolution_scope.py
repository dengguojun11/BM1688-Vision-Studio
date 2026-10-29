"""Add project-free evolution scope for edge feedback.

Project ownership remains readable on historical rows. New edge reports use
the durable ``scope_key`` (currently ``global``) and creator identity for
authorization; this avoids relying on NULL semantics in old unique indexes.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import case, cast, func, inspect, Integer, String, update


revision = "z9c0d1e2f3a4"
down_revision = "z9b0c1d2e3f4"
branch_labels = None
depends_on = None


def _add_columns(table: str) -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = {item["name"] for item in inspector.get_columns(table)}
    if "scope_key" not in columns:
        op.add_column(table, sa.Column("scope_key", sa.String(160), nullable=True))
    if "created_by" not in columns:
        op.add_column(table, sa.Column("created_by", sa.Integer(), nullable=True))


def _backfill(table: str) -> None:
    bind = op.get_bind()
    # Existing project rows retain an explicit historical scope. Rows that
    # were already unscoped are kept in the global scope.
    table_obj = sa.table(
        table,
        sa.column("project_id", Integer),
        sa.column("scope_key", String(160)),
    )
    project_scope = (
        func.concat("project:", cast(table_obj.c.project_id, String(32)))
        if bind.dialect.name == "mysql"
        else "project:" + cast(table_obj.c.project_id, String(32))
    )
    bind.execute(
        update(table_obj)
        .where(table_obj.c.scope_key.is_(None))
        .values(scope_key=case((table_obj.c.project_id.is_(None), "global"), else_=project_scope))
    )


def _unique_names(table: str) -> set[str]:
    return {
        item.get("name") or ""
        for item in inspect(op.get_bind()).get_unique_constraints(table)
    }


def _create_unique(table: str, name: str, columns: list[str]) -> None:
    """Create a unique constraint on both MySQL and SQLite.

    SQLite cannot add a table constraint with ``ALTER TABLE``.  Alembic's
    batch rebuild is required there; using the same operation on a second
    table keeps the migration deterministic for the local test database.
    """
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.create_unique_constraint(name, columns)
    else:
        op.create_unique_constraint(name, table, columns)


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    for table in ("inference_event", "edge_hard_example"):
        if table not in tables:
            raise RuntimeError(f"去项目化迁移要求 {table} 已存在")
        _add_columns(table)
        _backfill(table)

    # MySQL supports direct nullability changes; SQLite needs a table rebuild.
    if bind.dialect.name == "sqlite":
        for table in ("inference_event", "edge_hard_example"):
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.alter_column("scope_key", existing_type=sa.String(160), nullable=False)
                batch.alter_column("project_id", existing_type=sa.Integer(), nullable=True)
    else:
        for table in ("inference_event", "edge_hard_example"):
            op.alter_column(table, "scope_key", existing_type=sa.String(160), nullable=False)
            op.alter_column(table, "project_id", existing_type=sa.Integer(), nullable=True)

    existing = _unique_names("inference_event")
    if "uq_inference_event_scope_key" not in existing:
        _create_unique(
            "inference_event", "uq_inference_event_scope_key", ["scope_key", "event_key"]
        )
    existing = _unique_names("edge_hard_example")
    if "uq_edge_hard_example_scope_fingerprint" not in existing:
        _create_unique(
            "edge_hard_example",
            "uq_edge_hard_example_scope_fingerprint",
            ["scope_key", "fingerprint"],
        )
    indexes = {
        item.get("name") or ""
        for item in inspect(op.get_bind()).get_indexes("inference_event")
    }
    if "idx_inference_event_scope_created" not in indexes:
        op.create_index(
            "idx_inference_event_scope_created",
            "inference_event",
            ["scope_key", "created_at"],
        )
    indexes = {
        item.get("name") or ""
        for item in inspect(op.get_bind()).get_indexes("edge_hard_example")
    }
    if "idx_edge_hard_example_scope_queue" not in indexes:
        op.create_index(
            "idx_edge_hard_example_scope_queue",
            "edge_hard_example",
            ["scope_key", "review_status", "training_status"],
        )


def downgrade() -> None:
    raise RuntimeError("进化中心范围迁移涉及历史幂等数据，生产降级必须恢复备份")
