"""一期空库建表范围与外键闭包专项测试。"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, create_engine, inspect

from labelu.internal.common.phase_one_schema import (
    PHASE_ONE_RETIRED_TABLES,
    PHASE_ONE_TABLE_ALLOWLIST,
    phase_one_foreign_key_violations,
    phase_one_table_names,
)


def test_phase_one_scope_excludes_retired_control_plane_tables() -> None:
    metadata = MetaData()
    Table("user", metadata, Column("id", Integer, primary_key=True))
    Table("task", metadata, Column("id", Integer, primary_key=True))
    Table("project", metadata, Column("id", Integer, primary_key=True))
    Table("future_unreviewed_table", metadata, Column("id", Integer, primary_key=True))

    assert phase_one_table_names(metadata) == frozenset({"task", "user"})
    assert PHASE_ONE_RETIRED_TABLES >= {"project", "organization", "edge_agent"}
    assert {"task", "dataset", "yolo_bm1688_export"} <= PHASE_ONE_TABLE_ALLOWLIST


def test_phase_one_foreign_key_closure_reports_legacy_reference() -> None:
    metadata = MetaData()
    Table("user", metadata, Column("id", Integer, primary_key=True))
    Table("project", metadata, Column("id", Integer, primary_key=True))
    Table(
        "task",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, ForeignKey("project.id")),
    )

    assert phase_one_foreign_key_violations(metadata) == {"task": ("project",)}


def test_current_phase_one_schema_has_auditable_project_dependencies() -> None:
    """锁住当前去项目化缺口，避免范围调整让残留依赖静默消失。

    该断言在八段迁移逐步落地时应随ORM改造逐项缩小，最终替换成空字典。
    它不是目标状态，而是迁移过程中的精确待办清单。
    """

    import labelu.internal.domain.models  # noqa: F401
    from labelu.internal.common.db import Base

    violations = phase_one_foreign_key_violations(Base.metadata)
    assert set(violations) == {
        "active_learning_batch",
        "auto_training_plan",
        "data_source",
        "edge_hard_example",
        "feedback_cluster",
        "inference_event",
        "ingestion_job",
        "media_asset",
        "prediction_set",
        "prediction_set_projection",
        "prediction_set_review_audit",
        "provider_job",
        "rule_binding",
        "rule_binding_audit",
        "rule_template",
        "rule_version_audit",
        "task",
        "third_party_call_audit",
        "workflow_instance",
        "yolo_train_job",
    }
    assert all(targets == ("project",) for targets in violations.values())


def test_global_dataset_models_keep_legacy_project_columns_without_new_fk() -> None:
    """数据集历史列可读，但空库不再被项目控制面拖入建表闭包。"""

    import labelu.internal.domain.models  # noqa: F401
    from labelu.internal.common.db import Base

    for table_name in ("dataset", "dataset_version"):
        table = Base.metadata.tables[table_name]
        column = table.c.project_id
        assert column.nullable is True
        assert list(column.foreign_keys) == []


def test_empty_sqlite_uses_phase_one_scope(tmp_path: Path) -> None:
    database_path = tmp_path / "phase-one-empty.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    config = Config(
        str(Path(__file__).resolve().parents[2] / "alembic_labelu" / "alembic.ini")
    )
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert "task" in tables
    assert "dataset" in tables
    assert not (tables & PHASE_ONE_RETIRED_TABLES)
