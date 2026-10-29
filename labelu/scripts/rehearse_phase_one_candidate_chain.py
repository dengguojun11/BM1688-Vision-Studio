"""执行隔离 `.tmp/phase-one-rebuild/versions` 八段候选链。

此脚本只创建临时 SQLite 文件，依次执行候选 upgrade，再逆序 downgrade，
并检查一期空库不出现 project 表/外键、关键表存在以及 BM1688/边端租约
字段和索引闭合。它不扫描或修改正式 Alembic versions。
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, inspect

ROOT = Path(__file__).parents[1]
VERSIONS = ROOT.parent / ".tmp" / "phase-one-rebuild" / "versions"
REVISIONS = tuple(f"gai_p1_{number:02d}" for number in range(1, 9))
RETIRED = {
    "project",
    "project_member",
    "organization",
    "scene",
    "deployment_release",
    "deployment_target",
    "edge_agent",
    "edge_agent_release",
    "edge_agent_deployment",
    "edge_device_job",
    "model_optimization_job",
    "rule_device_signature_report",
}


def _load(revision: str):
    path = next(VERSIONS.glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"candidate_{revision}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载候选迁移：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(module, connection, operation) -> None:
    module.op = Operations(MigrationContext.configure(connection))
    operation()


_BASELINE_TABLES = {
    "user",
    "dataset_category",
    "task",
    "task_sample",
    "task_attachment",
    "annotation_label",
    "yolo_train_job",
    "published_model_catalog",
    "auto_training_plan",
    "auto_training_round",
    "yolo_bm1688_export",
    "yolo_edge_validation_job",
}


def _create_production_baseline(bind) -> None:
    """创建h7基线中仅供候选外键/第08段使用的最小前置表。"""

    metadata = MetaData()
    for name in (
        "user",
        "dataset_category",
        "task",
        "task_sample",
        "task_attachment",
        "annotation_label",
        "yolo_train_job",
        "published_model_catalog",
        "auto_training_plan",
        "auto_training_round",
    ):
        Table(name, metadata, Column("id", Integer, primary_key=True))
    Table(
        "yolo_bm1688_export",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String(32), nullable=False),
        Column("pipeline_origin", String(32)),
        Column("auto_training_round_id", Integer),
    )
    Table(
        "yolo_edge_validation_job",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String(32), nullable=False),
        Column("queued_at", DateTime(timezone=True)),
        Column("pipeline_origin", String(32)),
        Column("auto_training_round_id", Integer),
    )
    metadata.create_all(bind)


def _assert_upgraded(engine) -> dict[str, int]:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if tables & RETIRED:
        raise AssertionError(f"空库出现已退休表：{sorted(tables & RETIRED)}")
    required = {
        "user",
        "task",
        "dataset",
        "dataset_version",
        "model_version",
        "algorithm_group",
        "workflow_instance",
        "workflow_step",
        "workflow_event",
        "workflow_outbox",
        "scheduler_lease",
        "data_source",
        "ingestion_job",
        "media_asset",
        "media_upload_session",
        "sample_asset_fingerprint",
        "yolo_bm1688_export",
        "yolo_edge_validation_job",
    }
    missing = sorted(required - tables)
    if missing:
        raise AssertionError(f"一期候选缺少关键表：{missing}")
    project_fks: list[str] = []
    project_columns: list[str] = []
    for table_name in tables:
        if "project_id" in {item["name"] for item in inspector.get_columns(table_name)}:
            project_columns.append(table_name)
        for foreign_key in inspector.get_foreign_keys(table_name):
            target = str(foreign_key.get("referred_table") or "")
            if target == "project":
                project_fks.append(table_name)
    if project_fks:
        raise AssertionError(f"一期候选仍有project外键：{sorted(set(project_fks))}")
    if project_columns:
        raise AssertionError(f"一期候选仍有project_id列：{sorted(set(project_columns))}")
    for table_name in ("yolo_bm1688_export", "yolo_edge_validation_job"):
        columns = {item["name"] for item in inspector.get_columns(table_name)}
        expected_columns = {
            "queue_lease_owner",
            "queue_lease_expires_at",
            "execution_lease_owner",
            "execution_lease_expires_at",
            "execution_fencing_token",
        }
        if not expected_columns <= columns:
            raise AssertionError((table_name, sorted(expected_columns - columns)))
    return {
        "tables": len(tables),
        "project_columns": 0,
        "project_foreign_keys": 0,
    }


def rehearse() -> dict[str, object]:
    modules = [_load(revision) for revision in REVISIONS]
    with tempfile.TemporaryDirectory(prefix="labelu-phase-one-chain-") as directory:
        url = f"sqlite:///{Path(directory) / 'candidate-chain.sqlite'}"
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                _create_production_baseline(connection)
                for module in modules:
                    _run(module, connection, module.upgrade)
            schema = _assert_upgraded(engine)
            with engine.begin() as connection:
                for module in reversed(modules):
                    _run(module, connection, module.downgrade)
            remaining = (
                set(inspect(engine).get_table_names()) - _BASELINE_TABLES - {"alembic_version"}
            )
            if remaining:
                raise AssertionError(f"候选全链降级残留表：{sorted(remaining)}")
            return {
                "status": "通过",
                "数据库方言": engine.dialect.name,
                "候选段数": len(modules),
                **schema,
                "降级": "无候选业务表残留，保留h7基线前置表",
            }
        finally:
            engine.dispose()


if __name__ == "__main__":
    import json

    print(json.dumps(rehearse(), ensure_ascii=False, indent=2))
