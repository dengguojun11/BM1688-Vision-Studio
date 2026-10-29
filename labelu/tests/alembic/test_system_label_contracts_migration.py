from __future__ import annotations

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "w5x6y7z8a9b0_system_label_contracts.py"
    )
    spec = importlib.util.spec_from_file_location(
        "system_label_contracts_migration",
        migration_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mysql_text_columns_do_not_use_server_defaults():
    migration = _load_migration_module()
    source = Path(migration.__file__).read_text(encoding="utf-8")

    assert (
        "sa.Text(),\n                nullable=False,\n                server_default="
        not in source
    )

    table = sa.Table(
        "json_text_probe",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    ddl = str(CreateTable(table).compile(dialect=mysql.dialect()))
    assert "payload_json TEXT NOT NULL" in ddl
    assert "payload_json TEXT NOT NULL DEFAULT" not in ddl


def test_required_json_backfill_is_idempotent_on_sqlite(monkeypatch):
    migration = _load_migration_module()
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as bind:
        bind.execute(sa.text("CREATE TABLE training_task (id INTEGER PRIMARY KEY)"))
        bind.execute(sa.text("INSERT INTO training_task (id) VALUES (1)"))
        bind.execute(
            sa.text("ALTER TABLE training_task ADD COLUMN selected_label_ids_json TEXT")
        )
        bind.execute(
            sa.text(
                "ALTER TABLE training_task ADD COLUMN class_names_snapshot_json TEXT"
            )
        )

        class _Batch:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def alter_column(self, *_args, **_kwargs):
                return None

        monkeypatch.setattr(migration.op, "batch_alter_table", lambda _table: _Batch())

        migration._backfill_required_json_columns(
            bind,
            "training_task",
            {
                "selected_label_ids_json": "[]",
                "class_names_snapshot_json": "[]",
            },
        )
        migration._backfill_required_json_columns(
            bind,
            "training_task",
            {
                "selected_label_ids_json": "[]",
                "class_names_snapshot_json": "[]",
            },
        )

        row = bind.execute(
            sa.text(
                "SELECT selected_label_ids_json, class_names_snapshot_json "
                "FROM training_task WHERE id = 1"
            )
        ).one()

    assert tuple(row) == ("[]", "[]")
