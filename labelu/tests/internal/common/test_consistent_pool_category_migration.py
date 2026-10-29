from __future__ import annotations

import importlib

import sqlalchemy as sa


def _create_legacy_schema(engine) -> None:
    metadata = sa.MetaData()
    sa.Table(
        "dataset_category",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("category_key", sa.String(128), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.String(512)),
        sa.Column("sort_order", sa.Integer(), nullable=False, default=0),
        sa.Column("status", sa.String(32), nullable=False, default="active"),
        sa.Column("version", sa.Integer(), nullable=False, default=1),
        sa.Column("created_by", sa.Integer()),
        sa.Column("updated_by", sa.Integer()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.Column("deleted_at", sa.DateTime()),
    )
    sa.Table(
        "task",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(32)),
        sa.Column("config", sa.Text()),
        sa.Column("dataset_category_id", sa.Integer()),
        sa.Column("deleted_at", sa.DateTime()),
    )
    metadata.create_all(engine)


def test_upgrade_creates_one_system_category_and_backfills_pool_tasks(monkeypatch):
    migration = importlib.import_module(
        "labelu.alembic_labelu.versions.f4g5h6i7j8k9_add_consistent_pool_dataset_category"
    )
    engine = sa.create_engine("sqlite:///:memory:")
    _create_legacy_schema(engine)

    with engine.begin() as connection:
        metadata = sa.MetaData()
        task = sa.Table("task", metadata, autoload_with=connection)
        connection.execute(
            task.insert(),
            [
                {
                    "id": 1,
                    "source_type": "consistent_pool",
                    "config": "{}",
                },
                {
                    "id": 2,
                    "source_type": "internal",
                    "config": '{"consistent_pool":{"kind":"train_filtered_consistent"}}',
                },
                {
                    "id": 3,
                    "source_type": "internal",
                    "config": "{}",
                },
            ],
        )
        monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

        migration.upgrade()
        migration.upgrade()

        category = sa.Table("dataset_category", metadata, autoload_with=connection)
        categories = connection.execute(sa.select(category)).mappings().all()
        tasks = {
            int(row["id"]): row["dataset_category_id"]
            for row in connection.execute(sa.select(task)).mappings()
        }

    assert len(categories) == 1
    assert categories[0]["category_key"] == migration.CATEGORY_KEY
    assert categories[0]["name"] == migration.CATEGORY_NAME
    assert categories[0]["status"] == "active"
    assert tasks[1] == categories[0]["id"]
    assert tasks[2] == categories[0]["id"]
    assert tasks[3] is None
