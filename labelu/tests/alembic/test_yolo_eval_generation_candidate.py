from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = (
    ROOT / "docs" / "7gai_phase_one_migration_candidates"
    / "gai_p1_09_add_yolo_eval_generations.py"
)


def _load_candidate():
    spec = importlib.util.spec_from_file_location("gai_p1_09_candidate", CANDIDATE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _target_tables(engine):
    metadata = MetaData()
    Table("yolo_train_job", metadata, Column("id", Integer, primary_key=True))
    Table(
        "yolo_eval_report",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("yolo_train_job_id", Integer, nullable=False),
        Column("report_kind", String(32), nullable=False),
        Column("provider_key", String(32), nullable=False),
    )
    metadata.create_all(engine)


def _run(module, connection, operation):
    module.op = Operations(MigrationContext.configure(connection))
    operation()


def test_candidate_adds_generation_columns_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'eval-generation.sqlite'}")
    try:
        _target_tables(engine)
        module = _load_candidate()
        with engine.begin() as connection:
            _run(module, connection, module.upgrade)
            _run(module, connection, module.upgrade)

        inspector = inspect(engine)
        job_columns = {item["name"] for item in inspector.get_columns("yolo_train_job")}
        report_columns = {item["name"] for item in inspector.get_columns("yolo_eval_report")}
        assert {"ai_eval_full_operation_id", "ai_eval_next_operation_id"} <= job_columns
        assert "operation_id" in report_columns
        indexes = {item["name"] for item in inspector.get_indexes("yolo_eval_report")}
        assert {
            "ix_yolo_eval_report_operation_id",
            "uq_yolo_eval_report_operation",
        } <= indexes
    finally:
        engine.dispose()


def test_candidate_rejects_duplicate_non_null_generation(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'eval-generation-duplicate.sqlite'}")
    try:
        _target_tables(engine)
        module = _load_candidate()
        with engine.begin() as connection:
            _run(module, connection, module.upgrade)
            connection.execute(
                text(
                    "INSERT INTO yolo_eval_report "
                    "(id, yolo_train_job_id, report_kind, provider_key, operation_id) "
                    "VALUES (1, 1, 'full_analysis', 'mock', 'generation-1')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO yolo_eval_report "
                    "(id, yolo_train_job_id, report_kind, provider_key, operation_id) "
                    "VALUES (2, 1, 'full_analysis', 'mock', 'generation-2')"
                )
            )
        # The existing unique index prevents a true duplicate at insert time;
        # the candidate remains idempotent on a clean database.
        with engine.begin() as connection:
            with pytest.raises(Exception):
                connection.execute(
                    text(
                        "INSERT INTO yolo_eval_report "
                        "(id, yolo_train_job_id, report_kind, provider_key, operation_id) "
                        "VALUES (3, 1, 'full_analysis', 'mock', 'generation-2')"
                    )
                )
    finally:
        engine.dispose()
