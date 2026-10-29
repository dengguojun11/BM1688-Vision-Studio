"""在隔离数据库演练 BM1688/边端调度租约候选迁移。

默认创建临时 SQLite 文件并在结束时删除目标表。MySQL 只能连接显式命名为
``labelu_migration_sandbox*`` 的本地库，并且必须传入 ``--允许修改``；脚本
拒绝生产主机、生产库名和未显式授权的 DDL，避免把演练误指向线上数据库。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, inspect
from sqlalchemy.engine import make_url


_CANDIDATE = (
    Path(__file__).parents[1]
    / "docs"
    / "7gai_phase_one_migration_candidates"
    / "gai_p1_08_add_dispatch_leases.py"
)
_TABLES = ("yolo_bm1688_export", "yolo_edge_validation_job", "yolo_train_job")


def _load_candidate():
    spec = importlib.util.spec_from_file_location("gai_p1_08_candidate", _CANDIDATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载候选迁移：{_CANDIDATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_target_tables(engine) -> None:
    metadata = MetaData()
    for table_name in _TABLES:
        Table(
            table_name,
            metadata,
            Column("id", Integer, primary_key=True),
            Column("status", String(32), nullable=False),
            Column("pipeline_origin", String(32), nullable=True),
            Column("auto_training_round_id", Integer, nullable=True),
            Column("queued_at", DateTime(timezone=True), nullable=True),
        )
    metadata.create_all(engine)


def _run(module, connection, operation) -> None:
    module.op = Operations(MigrationContext.configure(connection))
    operation()


def _assert_schema(engine, *, has_lease: bool) -> None:
    inspector = inspect(engine)
    for table_name in _TABLES:
        columns = {item["name"] for item in inspector.get_columns(table_name)}
        indexes = {item.get("name") for item in inspector.get_indexes(table_name)}
        expected_columns = {
            "queue_lease_owner",
            "queue_lease_expires_at",
            "execution_lease_owner",
            "execution_lease_expires_at",
            "execution_fencing_token",
        }
        expected_indexes = {
            f"ix_{table_name}_queue_lease_owner",
            f"ix_{table_name}_queue_lease_expires_at",
            f"ix_{table_name}_execution_lease_owner",
            f"ix_{table_name}_execution_lease_expires_at",
            (
                "ix_yolo_bm1688_export_worker_scan"
                if table_name == "yolo_bm1688_export"
                else "ix_yolo_edge_validation_job_dispatch_scan"
                if table_name == "yolo_edge_validation_job"
                else "ix_yolo_train_job_training_worker_scan"
            ),
        }
        if has_lease:
            assert expected_columns <= columns, (table_name, columns)
            assert expected_indexes <= indexes, (table_name, indexes)
        else:
            assert not expected_columns & columns, (table_name, columns)
            assert not expected_indexes & indexes, (table_name, indexes)


def _validate_url(url: str, *, allow_ddl: bool) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "mysql":
        return
    database = str(parsed.database or "")
    host = str(parsed.host or "").lower()
    if not allow_ddl:
        raise RuntimeError("MySQL演练必须显式传入 --允许修改")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("MySQL演练仅允许本机主机")
    if not database.startswith("labelu_migration_sandbox"):
        raise RuntimeError(
            "MySQL演练库名必须以 labelu_migration_sandbox 开头，拒绝生产库"
        )


def rehearse(url: str, *, allow_ddl: bool) -> dict[str, object]:
    _validate_url(url, allow_ddl=allow_ddl)
    engine = create_engine(url, pool_pre_ping=True)
    module = _load_candidate()
    try:
        _create_target_tables(engine)
        _assert_schema(engine, has_lease=False)
        with engine.begin() as connection:
            _run(module, connection, module.upgrade)
            _run(module, connection, module.upgrade)
        _assert_schema(engine, has_lease=True)
        with engine.begin() as connection:
            _run(module, connection, module.downgrade)
        _assert_schema(engine, has_lease=False)
        return {
            "status": "通过",
            "数据库方言": engine.dialect.name,
            "目标表": list(_TABLES),
            "候选修订": module.revision,
            "重复升级": "幂等",
            "降级": "仅移除租约字段和索引",
        }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="一期调度租约迁移隔离演练")
    parser.add_argument(
        "--url",
        help="隔离数据库URL；不传则使用临时SQLite",
    )
    parser.add_argument(
        "--允许修改",
        dest="allow_ddl",
        action="store_true",
        help="仅对本机 labelu_migration_sandbox* MySQL库允许DDL",
    )
    args = parser.parse_args()

    temporary_directory = None
    if args.url:
        url = args.url
    else:
        temporary_directory = tempfile.TemporaryDirectory(prefix="labelu-lease-")
        url = f"sqlite:///{Path(temporary_directory.name) / 'rehearsal.sqlite'}"
    try:
        report = rehearse(url, allow_ddl=args.allow_ddl)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        if temporary_directory is not None:
            temporary_directory.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
