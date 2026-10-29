"""在严格本机 MySQL 沙箱中演练第01段历史数据外键移除。

脚本只接受本机 ``labelu_migration_sandbox*`` 数据库，必须显式传入
``--允许修改``。它创建并最终删除自己的测试表，不连接生产，也不执行正式
Alembic versions。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine, inspect, select, text
from sqlalchemy.engine import make_url


ROOT = Path(__file__).parents[1]
CANDIDATE = (
    ROOT.parent / ".tmp" / "phase-one-rebuild" / "supplemental"
    / "gai_p1_01_project_fk_rebuild.py"
)
TABLES = ("dataset_version", "dataset", "project", "labelu_dataset_fk_user", "dataset_category")


def _validate_url(url: str, *, allow_write: bool) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "mysql":
        raise ValueError("只接受 MySQL URL")
    if str(parsed.host or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("只允许本机 MySQL")
    if not str(parsed.database or "").startswith("labelu_migration_sandbox"):
        raise ValueError("只允许 labelu_migration_sandbox* 数据库")
    if allow_write and __import__("os").environ.get("LABELU_MYSQL_REHEARSAL_CONFIRM") != "YES":
        raise ValueError("写入演练需要 LABELU_MYSQL_REHEARSAL_CONFIRM=YES")


def _load_candidate():
    spec = importlib.util.spec_from_file_location("gai_p1_01_project_fk_rebuild", CANDIDATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载隔离候选：{CANDIDATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _drop_tables(connection) -> None:
    for table_name in TABLES:
        connection.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))


def _create_seed(engine) -> None:
    metadata = MetaData()
    project = Table("project", metadata, Column("id", Integer, primary_key=True))
    owner = Table("labelu_dataset_fk_user", metadata, Column("id", Integer, primary_key=True))
    Table("dataset_category", metadata, Column("id", Integer, primary_key=True))
    dataset = Table(
        "dataset", metadata,
        Column("id", Integer, primary_key=True),
        Column("project_id", Integer, ForeignKey("project.id"), nullable=False),
        Column("owner_id", Integer, ForeignKey("labelu_dataset_fk_user.id"), nullable=False),
        Column("name", String(128), nullable=False),
        Column("status", String(24), nullable=False),
    )
    dataset_version = Table(
        "dataset_version", metadata,
        Column("id", Integer, primary_key=True),
        Column("dataset_id", Integer, ForeignKey("dataset.id"), nullable=False),
        Column("project_id", Integer, ForeignKey("project.id"), nullable=False),
        Column("version_no", Integer, nullable=False),
        Column("manifest_sha256", String(64), nullable=False),
    )
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(project.insert(), [{"id": 7}])
        connection.execute(owner.insert(), [{"id": 11}])
        connection.execute(dataset.insert(), [
            {"id": 101, "project_id": 7, "owner_id": 11, "name": "历史数据集一", "status": "ACTIVE"},
            {"id": 102, "project_id": 7, "owner_id": 11, "name": "历史数据集二", "status": "ARCHIVED"},
        ])
        connection.execute(dataset_version.insert(), [
            {"id": 201, "dataset_id": 101, "project_id": 7, "version_no": 1, "manifest_sha256": "a" * 64},
            {"id": 202, "dataset_id": 102, "project_id": 7, "version_no": 2, "manifest_sha256": "b" * 64},
        ])


def _run_candidate(module, connection, operation) -> None:
    module.op = Operations(MigrationContext.configure(connection))
    operation()


def run_rehearsal(url: str) -> dict[str, object]:
    _validate_url(url, allow_write=True)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            _drop_tables(connection)
        _create_seed(engine)
        module = _load_candidate()
        with engine.begin() as connection:
            _run_candidate(module, connection, module.upgrade)
            _run_candidate(module, connection, module.upgrade)
        inspector = inspect(engine)
        dataset_columns = {item["name"] for item in inspector.get_columns("dataset")}
        version_columns = {item["name"] for item in inspector.get_columns("dataset_version")}
        if "project_id" in dataset_columns or "project_id" in version_columns:
            raise AssertionError("project_id 未移除")
        with engine.connect() as connection:
            dataset_rows = connection.execute(
                text("SELECT id, owner_id, name, status FROM dataset ORDER BY id")
            ).all()
            version_rows = connection.execute(
                text("SELECT id, dataset_id, version_no, manifest_sha256 FROM dataset_version ORDER BY id")
            ).all()
        if len(dataset_rows) != 2 or len(version_rows) != 2:
            raise AssertionError("历史行数不一致")
        dataset_version_fks = inspector.get_foreign_keys("dataset_version")
        if not any("dataset_id" in (item.get("constrained_columns") or []) for item in dataset_version_fks):
            raise AssertionError("dataset_version.dataset_id 外键丢失")
        return {
            "状态": "通过",
            "数据库": make_url(url).database,
            "候选修订": module.revision,
            "重复升级": "幂等",
            "dataset历史行": len(dataset_rows),
            "dataset_version历史行": len(version_rows),
            "project_id": "已移除",
            "dataset_version.dataset_id外键": "保留",
            "生产写入": "未执行",
        }
    finally:
        try:
            with engine.begin() as connection:
                _drop_tables(connection)
        finally:
            engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="第01段 MySQL 历史数据外键移除沙箱演练")
    parser.add_argument("--url", required=True)
    parser.add_argument("--允许修改", dest="allow_write", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.allow_write:
            raise ValueError("必须显式传入 --允许修改")
        print(json.dumps(run_rehearsal(args.url), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"状态": "阻断", "原因": str(exc), "MySQL通过": False}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

