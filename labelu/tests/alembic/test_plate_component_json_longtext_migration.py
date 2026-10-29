from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.dialects import mysql


def _load_migration_module():
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic_labelu"
        / "versions"
        / "u3v4w5x6y7z8_expand_plate_component_json_longtext.py"
    )
    spec = importlib.util.spec_from_file_location(
        "plate_component_json_longtext_migration",
        migration_path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plate_component_json_migration_uses_mysql_longtext(monkeypatch):
    migration = _load_migration_module()
    calls = []
    connection = SimpleNamespace(dialect=SimpleNamespace(name="mysql"))

    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(
        migration,
        "_existing_component_json_columns",
        lambda: list(migration._COMPONENT_JSON_COLUMNS),
    )
    monkeypatch.setattr(
        migration.op,
        "alter_column",
        lambda table, column, **kwargs: calls.append((table, column, kwargs)),
    )

    migration.upgrade()

    assert [(table, column) for table, column, _kwargs in calls] == [
        ("yolo_train_job", "component_status_json"),
        ("yolo_train_job", "component_artifacts_json"),
    ]
    assert all(isinstance(kwargs["type_"], mysql.LONGTEXT) for _, _, kwargs in calls)
