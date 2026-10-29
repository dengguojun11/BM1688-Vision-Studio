"""Move third-party ingestion deduplication from projects to creators.

The project columns are retained as nullable historical compatibility fields.
New一期 writes and queries use ``created_by`` and task ownership instead.  The
migration refuses to continue when existing rows would collide with the new
creator-scoped unique keys; production must resolve those rows before retrying.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "z5a6b7c8d9e0"
down_revision = "z4a5b6c7d8e9"
branch_labels = None
depends_on = None


def _inspector():
    return inspect(op.get_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _columns(table: str) -> set[str]:
    return {item["name"] for item in _inspector().get_columns(table)}


def _indexes(table: str) -> dict[str, dict]:
    return {item.get("name") or "": item for item in _inspector().get_indexes(table)}


def _unique_constraints(table: str) -> dict[str, dict]:
    return {
        item.get("name") or "": item
        for item in _inspector().get_unique_constraints(table)
    }


def _assert_no_collisions(table: str, columns: tuple[str, ...], label: str) -> None:
    bind = op.get_bind()
    if not set(columns) <= _columns(table):
        return
    cols = ", ".join(columns)
    rows = bind.execute(
        text(
            f"SELECT {cols}, COUNT(*) AS duplicate_count "
            f"FROM {table} GROUP BY {cols} HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).fetchall()
    if rows:
        raise RuntimeError(
            f"{label} 存在重复数据，不能自动迁移：{rows[0]}；"
            "请先按生产备份核对并合并重复记录后重试"
        )


def _drop_index_if_present(table: str, name: str) -> None:
    if name in _indexes(table):
        op.drop_index(name, table_name=table)


def _make_nullable_and_replace_unique(
    table: str,
    old_name: str,
    new_name: str,
    columns: tuple[str, ...],
) -> None:
    bind = op.get_bind()
    uniques = _unique_constraints(table)
    if new_name in uniques:
        # The formal migration may be re-run after an interrupted deployment.
        if bind.dialect.name != "sqlite" and "project_id" in _columns(table):
            op.alter_column(
                table,
                "project_id",
                existing_type=sa.Integer(),
                nullable=True,
            )
        return

    old_exists = old_name in uniques
    if bind.dialect.name == "sqlite":
        # SQLite cannot alter nullability or constraints in-place.  Alembic's
        # batch rebuild preserves rows and foreign keys while applying both.
        with op.batch_alter_table(table, recreate="always") as batch:
            if old_exists:
                batch.drop_constraint(old_name, type_="unique")
            batch.alter_column(
                "project_id",
                existing_type=sa.Integer(),
                nullable=True,
            )
            batch.create_unique_constraint(new_name, list(columns))
        return

    if old_exists:
        op.drop_constraint(old_name, table_name=table, type_="unique")
    op.alter_column(
        table,
        "project_id",
        existing_type=sa.Integer(),
        nullable=True,
    )
    op.create_unique_constraint(new_name, table, list(columns))


def upgrade() -> None:
    tables = _tables()
    required = {"ingestion_job", "media_asset"}
    if not required <= tables:
        # Empty databases are created by the complete Alembic chain; this guard
        # keeps direct migration rehearsals descriptive instead of cryptic.
        return

    for table, columns, label in (
        (
            "ingestion_job",
            ("created_by", "idempotency_key"),
            "ingestion_job creator/idempotency",
        ),
        (
            "media_asset",
            ("created_by", "sha256", "media_type"),
            "media_asset creator/sha256/media_type",
        ),
    ):
        _assert_no_collisions(table, columns, label)

    _make_nullable_and_replace_unique(
        "ingestion_job",
        "uq_ingestion_job_project_idempotency",
        "uq_ingestion_job_creator_idempotency",
        ("created_by", "idempotency_key"),
    )
    _make_nullable_and_replace_unique(
        "media_asset",
        "uq_media_asset_project_sha_media",
        "uq_media_asset_creator_sha_media",
        ("created_by", "sha256", "media_type"),
    )

    # Project-scoped indexes are historical dead paths for一期 ingestion.  The
    # columns stay for compatibility, but hot queries use creator ownership.
    for table, index_name in (
        ("ingestion_job", "idx_ingestion_job_project_status"),
        ("media_asset", "idx_media_asset_project_scan"),
        ("media_asset", "idx_media_asset_scene"),
    ):
        _drop_index_if_present(table, index_name)

    for table, name, columns in (
        (
            "ingestion_job",
            "idx_ingestion_job_creator_status",
            ["created_by", "status", "updated_at"],
        ),
        (
            "media_asset",
            "idx_media_asset_creator_scan",
            ["created_by", "scan_status", "lifecycle_status"],
        ),
    ):
        if name not in _indexes(table):
            op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    raise RuntimeError(
        "接入去项目化迁移只做前向兼容；生产恢复必须使用迁移前备份，"
        "禁止自动恢复 project_id 非空或旧项目唯一约束"
    )
