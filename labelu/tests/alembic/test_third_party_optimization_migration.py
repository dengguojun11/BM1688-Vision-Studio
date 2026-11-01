from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_fresh_schema_contains_third_party_optimization_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "third-party-optimization.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(
        str(Path(__file__).resolve().parents[2] / "alembic_labelu" / "alembic.ini")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "alembic_labelu"),
    )
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        request_columns = {
            column["name"]
            for column in inspect(engine).get_columns("optimization_request")
        }
    finally:
        engine.dispose()

    assert {
        "optimization_request",
        "optimization_cycle",
        "third_party_event_image",
        "integration_publish_decision",
    } <= tables
    assert "external_request_id" in request_columns
    assert "package_ids_json" in request_columns
    assert "strategy_external_task_id" in request_columns
