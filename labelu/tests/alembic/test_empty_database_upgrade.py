"""Regression coverage for bootstrapping a brand-new database via Alembic."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text


def test_upgrade_head_bootstraps_empty_database(tmp_path: Path) -> None:
    database_path = tmp_path / "empty-labelu.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    config = Config(
        str(Path(__file__).resolve().parents[2] / "alembic_labelu" / "alembic.ini")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    expected_head = ScriptDirectory.from_config(config).get_heads()
    assert len(expected_head) == 1

    command.upgrade(config, "head")
    first_engine = create_engine(database_url)
    try:
        first_tables = set(inspect(first_engine).get_table_names())
        with first_engine.connect() as connection:
            first_version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        first_engine.dispose()

    # A deploy/restart can safely invoke the migration runner more than once.
    # The second upgrade must be a no-op and preserve the same schema/version.
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        with engine.connect() as connection:
            version = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
    finally:
        engine.dispose()

    assert {"user", "task", "task_sample", "task_attachment"} <= tables
    # 一期空库不创建已取消的项目/组织/场景和设备控制面；生产遗留表
    # 仍由迁移保留，不能据此要求空库复制历史控制面。
    assert not tables & {
        "organization",
        "scene",
        "project",
        "project_member",
        "deployment_release",
        "deployment_target",
        "edge_agent",
        "edge_agent_release",
        "edge_agent_deployment",
        "edge_model_deployment",
        "edge_device_job",
        "model_optimization_job",
        "rule_device_signature_report",
    }
    # 空库引导也必须闭合 BM1688/边端调度租约结构；正式八段迁移合入
    # 前，这里是现有 ORM 白名单的最低结构契约。
    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        for table_name in ("yolo_bm1688_export", "yolo_edge_validation_job"):
            columns = {item["name"] for item in inspector.get_columns(table_name)}
            indexes = {item.get("name") for item in inspector.get_indexes(table_name)}
            assert {"queue_lease_owner", "queue_lease_expires_at"} <= columns
            assert {
                f"ix_{table_name}_queue_lease_owner",
                f"ix_{table_name}_queue_lease_expires_at",
            } <= indexes
    finally:
        engine.dispose()
    assert version == expected_head[0]
    assert version
    assert tables == first_tables
    assert version == first_version
