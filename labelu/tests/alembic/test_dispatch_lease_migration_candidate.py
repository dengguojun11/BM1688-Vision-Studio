"""隔离验证 BM1688/边端调度租约候选迁移。

候选文件位于 docs 目录，测试不会导入正式 Alembic versions，也不会修改正式
迁移 head。MySQL 环境由显式外部演练提供，本地默认使用 SQLite 结构演练。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, inspect


_CANDIDATE = (
    Path(__file__).parents[2]
    / "docs"
    / "7gai_phase_one_migration_candidates"
    / "gai_p1_08_add_dispatch_leases.py"
)


def _load_candidate():
    spec = importlib.util.spec_from_file_location("gai_p1_08_candidate", _CANDIDATE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _target_tables(engine):
    metadata = MetaData()
    Table(
        "yolo_bm1688_export",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String(32), nullable=False),
        Column("pipeline_origin", String(32), nullable=True),
        Column("auto_training_round_id", Integer, nullable=True),
        Column("worker_action", String(32), nullable=True),
        Column("queued_at", DateTime(timezone=True), nullable=True),
    )
    Table(
        "yolo_edge_validation_job",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String(32), nullable=False),
        Column("pipeline_origin", String(32), nullable=True),
        Column("auto_training_round_id", Integer, nullable=True),
        Column("queued_at", DateTime(timezone=True), nullable=True),
    )
    Table(
        "yolo_train_job",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("status", String(32), nullable=False),
        Column("queue_lease_owner", String(128), nullable=True),
        Column("queue_lease_expires_at", DateTime(timezone=True), nullable=True),
    )
    metadata.create_all(engine)


def _run_candidate(module, connection, operation):
    module.op = Operations(MigrationContext.configure(connection))
    operation()


def test_candidate_adds_nullable_columns_and_indexes_idempotently(tmp_path):
    database = tmp_path / "dispatch-lease-candidate.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    try:
        _target_tables(engine)
        module = _load_candidate()
        with engine.begin() as connection:
            _run_candidate(module, connection, module.upgrade)
            # 重复执行必须保持幂等，不能重复建列或索引。
            _run_candidate(module, connection, module.upgrade)

        inspector = inspect(engine)
        for table_name in ("yolo_bm1688_export", "yolo_edge_validation_job", "yolo_train_job"):
            columns = {item["name"]: item for item in inspector.get_columns(table_name)}
            assert columns["queue_lease_owner"]["nullable"] is True
            assert columns["queue_lease_expires_at"]["nullable"] is True
            assert columns["execution_lease_owner"]["nullable"] is True
            assert columns["execution_lease_expires_at"]["nullable"] is True
            assert columns["execution_fencing_token"]["nullable"] is False
            if table_name == "yolo_bm1688_export":
                assert columns["worker_action"]["nullable"] is True
            indexes = {item["name"] for item in inspector.get_indexes(table_name)}
            if table_name == "yolo_bm1688_export":
                assert "ix_yolo_bm1688_export_worker_action" in indexes
            assert {
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
            } <= indexes
            scan_index_name = (
                "ix_yolo_bm1688_export_worker_scan"
                if table_name == "yolo_bm1688_export"
                else "ix_yolo_edge_validation_job_dispatch_scan"
                if table_name == "yolo_edge_validation_job"
                else "ix_yolo_train_job_training_worker_scan"
            )
            worker_scan = next(
                item
                for item in inspector.get_indexes(table_name)
                if item["name"] == scan_index_name
            )
            expected_scan_columns = (
                ["status", "worker_action", "queue_lease_expires_at", "id"]
                if table_name == "yolo_bm1688_export"
                else ["status", "queued_at", "id"]
                if table_name == "yolo_edge_validation_job"
                else ["status", "queue_lease_expires_at", "id"]
            )
            assert worker_scan["column_names"] == expected_scan_columns
    finally:
        engine.dispose()


def test_candidate_downgrade_removes_only_lease_objects(tmp_path):
    database = tmp_path / "dispatch-lease-downgrade.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    try:
        _target_tables(engine)
        module = _load_candidate()
        with engine.begin() as connection:
            _run_candidate(module, connection, module.upgrade)
            _run_candidate(module, connection, module.downgrade)

        inspector = inspect(engine)
        for table_name in ("yolo_bm1688_export", "yolo_edge_validation_job", "yolo_train_job"):
            columns = {item["name"] for item in inspector.get_columns(table_name)}
            assert "status" in columns
            assert "queue_lease_owner" not in columns
            assert "queue_lease_expires_at" not in columns
            assert "execution_lease_owner" not in columns
            assert "execution_lease_expires_at" not in columns
            assert "execution_fencing_token" not in columns
            if table_name == "yolo_bm1688_export":
                assert "worker_action" not in columns
            assert not {
                f"ix_{table_name}_queue_lease_owner",
                f"ix_{table_name}_queue_lease_expires_at",
                f"ix_{table_name}_execution_lease_owner",
                f"ix_{table_name}_execution_lease_expires_at",
                (
                    "ix_yolo_bm1688_export_worker_scan"
                    if table_name == "yolo_bm1688_export"
                    else "ix_yolo_edge_validation_job_dispatch_scan"
                ),
            } & {item["name"] for item in inspector.get_indexes(table_name)}
    finally:
        engine.dispose()


def test_candidate_fails_closed_when_preceding_segment_did_not_create_tables(tmp_path):
    database = tmp_path / "dispatch-lease-missing-table.sqlite"
    engine = create_engine(f"sqlite:///{database}")
    try:
        module = _load_candidate()
        with engine.begin() as connection:
            module.op = Operations(MigrationContext.configure(connection))
            with pytest.raises(RuntimeError, match="要求前置段创建目标表"):
                module.upgrade()
    finally:
        engine.dispose()


def test_candidate_is_outside_formal_alembic_versions():
    assert "alembic_labelu" not in _CANDIDATE.parts
    assert _CANDIDATE.exists()
    module = _load_candidate()
    assert module.revision == "gai_p1_08"
    assert module.down_revision == "gai_p1_07"


def test_mysql_rehearsal_guard_rejects_production_host_and_database():
    script_path = _CANDIDATE.parents[2] / "scripts" / "rehearse_dispatch_lease_migration.py"
    spec = importlib.util.spec_from_file_location("lease_rehearsal", script_path)
    assert spec is not None and spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    with pytest.raises(RuntimeError, match="仅允许本机主机"):
        script._validate_url(
            "mysql+pymysql://user:pass@production.example/labelu_migration_sandbox",
            allow_ddl=True,
        )
    with pytest.raises(RuntimeError, match="必须以 labelu_migration_sandbox"):
        script._validate_url(
            "mysql+pymysql://user:pass@127.0.0.1/labelu",
            allow_ddl=True,
        )
