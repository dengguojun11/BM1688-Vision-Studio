from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine

from labelu.internal.application.service import experiment_dataset_import
from labelu.internal.application.service import strategy_evolution
from labelu.internal.common.config import settings
from labelu.internal.common.db import SessionLocal, engine
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export
from labelu.internal.domain.models.yolo_train_job import YoloTrainJob

BUSINESS_TABLES = (
    "user",
    "task",
    "task_sample",
    "task_attachment",
    "strategy_task_integration",
    "experiment_dataset_import",
    "yolo_train_job",
    "yolo_bm1688_export",
    "yolo_edge_validation_job",
)

REQUIRED_COLUMNS = {
    "experiment_dataset_import": {
        "package_id",
        "status",
        "source_payload_json",
        "formal_task_ids_json",
        "formal_sample_count",
        "abnormal_sample_count",
    },
    "strategy_task_integration": {
        "external_task_id",
        "task_type",
        "platform_status",
        "automation_stage",
        "automation_status",
        "outbound_callback_status",
        "platform_result_json",
    },
    "yolo_bm1688_export": {
        "published_model_id",
        "business_thresholds_json",
        "validation_thresholds_json",
        "threshold_binding_json",
    },
}

_WRITE_SQL = re.compile(
    r"^\s*(?:/\*.*?\*/\s*)*"
    r"(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|TRUNCATE|RENAME|CALL|LOAD)\b",
    re.IGNORECASE | re.DOTALL,
)


def _require_existing_mysql(target: Engine) -> None:
    if target.dialect.name != "mysql":
        raise RuntimeError(
            "真实业务验证只允许连接现有 MySQL；" f"当前 dialect={target.dialect.name!r}"
        )
    database_name = str(target.url.database or "").strip()
    if not database_name:
        raise RuntimeError("MySQL DATABASE_URL 未指定数据库名")


def _table_counts(target: Engine) -> dict[str, int]:
    with target.connect() as connection:
        return {
            table_name: int(
                connection.execute(
                    text(f"SELECT COUNT(*) FROM `{table_name}`")
                ).scalar_one()
            )
            for table_name in BUSINESS_TABLES
        }


@pytest.fixture(scope="session", autouse=True)
def existing_mysql_read_only_guard() -> Iterator[None]:
    _require_existing_mysql(engine)
    before_counts = _table_counts(engine)

    def reject_write_sql(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if _WRITE_SQL.match(str(statement or "")):
            raise AssertionError(
                "真实 MySQL 验证禁止写入或修改结构：" f"{str(statement).strip()[:160]}"
            )

    event.listen(engine, "before_cursor_execute", reject_write_sql)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", reject_write_sql)
        after_counts = _table_counts(engine)
        assert after_counts == before_counts, (
            "真实 MySQL 验证前后关键表行数发生变化，已停止验收："
            f"before={before_counts}, after={after_counts}"
        )


def test_runtime_connects_to_existing_mysql() -> None:
    _require_existing_mysql(engine)
    assert settings.DATABASE_URL.lower().startswith("mysql")
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT DATABASE()")).scalar_one()
            == engine.url.database
        )
        assert str(connection.execute(text("SELECT VERSION()")).scalar_one()).strip()


def test_required_business_schema_exists() -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    assert set(BUSINESS_TABLES).issubset(table_names)

    for table_name, required in REQUIRED_COLUMNS.items():
        actual = {item["name"] for item in inspector.get_columns(table_name)}
        assert required.issubset(
            actual
        ), f"{table_name} 缺少字段：{sorted(required - actual)}"


def test_model_asset_manifest_columns_use_mysql_longtext() -> None:
    inspector = inspect(engine)
    manifest_columns = {
        ("yolo_train_job", "dataset_manifest_json"): (
            YoloTrainJob.__table__.c.dataset_manifest_json
        ),
        ("yolo_bm1688_export", "artifact_manifest_json"): (
            YoloBm1688Export.__table__.c.artifact_manifest_json
        ),
    }

    for (table_name, column_name), model_column in manifest_columns.items():
        columns = {item["name"]: item for item in inspector.get_columns(table_name)}
        assert type(columns[column_name]["type"]).__name__.upper() == "LONGTEXT"
        assert model_column.type.compile(dialect=engine.dialect).upper() == "LONGTEXT"


def test_existing_business_relations_have_no_active_orphans() -> None:
    statements = {
        "task_sample.task_id": """
            SELECT COUNT(*)
            FROM task_sample AS sample
            LEFT JOIN task ON task.id = sample.task_id
            WHERE sample.deleted_at IS NULL AND task.id IS NULL
        """,
        "task_attachment.task_id": """
            SELECT COUNT(*)
            FROM task_attachment AS attachment
            LEFT JOIN task ON task.id = attachment.task_id
            WHERE attachment.deleted_at IS NULL AND task.id IS NULL
        """,
        "strategy_task_integration.labelu_task_id": """
            SELECT COUNT(*)
            FROM strategy_task_integration AS integration
            LEFT JOIN task ON task.id = integration.labelu_task_id
            WHERE integration.deleted_at IS NULL
              AND integration.labelu_task_id IS NOT NULL
              AND task.id IS NULL
        """,
    }
    with engine.connect() as connection:
        orphan_counts = {
            name: int(connection.execute(text(statement)).scalar_one())
            for name, statement in statements.items()
        }
    assert orphan_counts == {name: 0 for name in statements}, orphan_counts


def test_expert_feedback_query_contract_with_existing_data() -> None:
    with SessionLocal() as db:
        rows = asyncio.run(
            experiment_dataset_import.list_imports(
                db,
                package_id=None,
                status=None,
                model_id=None,
            )
        )
        for row in rows:
            payload = row.model_dump(mode="json")
            assert payload["package_id"]
            assert payload["status"]
            assert isinstance(payload["formal_task_ids"], list)
            detail = asyncio.run(
                experiment_dataset_import.get_import_detail(
                    db,
                    package_id=row.package_id,
                )
            )
            assert detail is not None
            assert detail.package_id == row.package_id
            assert detail.status == row.status


def test_strategy_evolution_query_contract_with_existing_data() -> None:
    with SessionLocal() as db:
        rows, meta = asyncio.run(
            strategy_evolution.list_tasks(
                db,
                task_type=None,
                platform_status=None,
                keyword=None,
                page=0,
                size=100,
            )
        )
        assert meta.total >= len(rows)
        for row in rows:
            payload = row.model_dump(mode="json")
            assert payload["external_task_id"]
            assert payload["task_type"] in {
                "threshold_experiment",
                "model_capability",
            }
            assert payload["platform_status"]
            detail = asyncio.run(
                strategy_evolution.get_task_detail(
                    db,
                    external_task_id=row.external_task_id,
                )
            )
            assert detail is not None
            assert detail.external_task_id == row.external_task_id
            assert detail.task_type == row.task_type
