"""一期八段迁移的只读/隔离预演。

这个脚本不改正式 ``alembic_labelu/versions``，也不会连接生产数据库。
它负责三件事：

* 审计当前正式迁移图仍只有一个 head，并确认生产基线后的旧后缀都有处置记录；
* 在调用方提供的隔离候选目录上验证八段单父链；
* 在临时 SQLite（或显式允许的本机 MySQL 沙箱）中执行租约候选 DDL，
  验证训练、编译、边端三张表各五个租约字段（交接2个、执行期3个）和各五个索引可重复升级并可降级。

完整八段迁移文件尚未进入正式目录，因此脚本不会伪造“八段已执行”的结果。
``--replacement-versions`` 缺失时只做正式图审计和租约候选演练。
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

from labelu.internal.common.phase_one_migration_rebuild_audit import (
    audit_legacy_suffix,
    audit_replacement_workspace,
)


ROOT = Path(__file__).parents[1]
CANDIDATE = (
    ROOT / "docs" / "7gai_phase_one_migration_candidates" / "gai_p1_08_add_dispatch_leases.py"
)
TARGET_TABLES = ("yolo_bm1688_export", "yolo_edge_validation_job", "yolo_train_job")
RETIRED_TABLES = {
    "project",
    "project_member",
    "organization",
    "scene",
    "deployment_release",
    "edge_agent",
    "edge_agent_release",
    "edge_agent_deployment",
    "edge_device_job",
    "model_optimization_job",
    "rule_device_signature_report",
}


def _load_candidate():
    spec = importlib.util.spec_from_file_location("gai_p1_08_rehearsal_candidate", CANDIDATE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载候选迁移：{CANDIDATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(module, connection, operation) -> None:
    module.op = Operations(MigrationContext.configure(connection))
    operation()


def _create_target_tables(engine) -> None:
    metadata = MetaData()
    for table_name in TARGET_TABLES:
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


def _assert_lease_schema(engine, *, expected: bool) -> None:
    inspector = inspect(engine)
    for table_name in TARGET_TABLES:
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
        if expected:
            if not expected_columns <= columns or not expected_indexes <= indexes:
                raise AssertionError((table_name, sorted(columns), sorted(indexes)))
        elif expected_columns & columns or expected_indexes & indexes:
            raise AssertionError((table_name, sorted(columns), sorted(indexes)))


def _validate_mysql_url(url: str, *, allow_ddl: bool) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "mysql":
        return
    host = str(parsed.host or "").lower()
    database = str(parsed.database or "")
    if not allow_ddl:
        raise RuntimeError("MySQL演练必须显式传入 --允许修改")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("MySQL演练仅允许本机主机")
    if not database.startswith("labelu_migration_sandbox"):
        raise RuntimeError("MySQL演练库名必须以 labelu_migration_sandbox 开头，拒绝生产库")


def rehearse_lease_ddl(url: str, *, allow_ddl: bool) -> dict[str, object]:
    """在隔离数据库验证候选第八段的结构闭包。"""

    _validate_mysql_url(url, allow_ddl=allow_ddl)
    engine = create_engine(url, pool_pre_ping=True)
    module = _load_candidate()
    try:
        _create_target_tables(engine)
        _assert_lease_schema(engine, expected=False)
        with engine.begin() as connection:
            _run_migration(module, connection, module.upgrade)
            _run_migration(module, connection, module.upgrade)
        _assert_lease_schema(engine, expected=True)
        with engine.begin() as connection:
            _run_migration(module, connection, module.downgrade)
        _assert_lease_schema(engine, expected=False)
        tables = set(inspect(engine).get_table_names())
        if tables & RETIRED_TABLES:
            raise AssertionError(f"演练意外创建已退休表：{sorted(tables & RETIRED_TABLES)}")
        return {
            "status": "通过",
            "方言": engine.dialect.name,
            "候选修订": module.revision,
            "重复升级": "幂等",
            "租约字段": 15,
            "租约索引": 15,
            "每张表租约字段": 5,
            "每张表租约索引": 5,
            "降级": "仅移除租约字段和索引",
        }
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="一期八段迁移隔离预演")
    parser.add_argument("--alembic-ini", default="labelu/alembic_labelu/alembic.ini")
    parser.add_argument("--replacement-versions", help="隔离的八段候选versions目录")
    parser.add_argument("--url", help="隔离数据库URL；不传则使用临时SQLite")
    parser.add_argument("--允许修改", dest="allow_ddl", action="store_true")
    args = parser.parse_args()

    report: dict[str, object] = {"正式迁移图": audit_legacy_suffix(args.alembic_ini)}
    if args.replacement_versions:
        report["八段候选链"] = audit_replacement_workspace(args.replacement_versions)

    temporary_directory = None
    if args.url:
        url = args.url
    else:
        temporary_directory = tempfile.TemporaryDirectory(prefix="labelu-phase-one-")
        url = f"sqlite:///{Path(temporary_directory.name) / 'lease-rehearsal.sqlite'}"
    try:
        report["租约候选结构"] = rehearse_lease_ddl(url, allow_ddl=args.allow_ddl)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        if temporary_directory is not None:
            temporary_directory.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
