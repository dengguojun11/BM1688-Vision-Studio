"""一期八段迁移和租约候选的隔离预演契约。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect

from labelu.internal.common.phase_one_migration_rebuild_audit import audit_legacy_suffix


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "rehearse_phase_one_migration.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("phase_one_rehearsal", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_formal_graph_has_one_head_and_complete_legacy_audit():
    report = audit_legacy_suffix(ROOT / "alembic_labelu" / "alembic.ini")
    assert report["production_baseline"] == "h7i8j9k0l1m2"
    assert report["current_head"] == "zf2a3b4c5d6e"
    assert report["legacy_revision_count"] == 52


def test_isolated_rehearsal_verifies_dispatch_and_execution_lease_columns_and_indexes(tmp_path):
    module = _load_script()
    report = module.rehearse_lease_ddl(
        f"sqlite:///{(tmp_path / 'phase-one.sqlite').as_posix()}",
        allow_ddl=False,
    )
    assert report["status"] == "通过"
    assert report["租约字段"] == 15
    assert report["租约索引"] == 15
    assert report["每张表租约字段"] == 5
    assert report["每张表租约索引"] == 5


def test_rehearsal_mysql_guard_rejects_non_sandbox_database():
    module = _load_script()
    try:
        module._validate_mysql_url(
            "mysql+pymysql://user:pass@127.0.0.1/labelu",
            allow_ddl=True,
        )
    except RuntimeError as exc:
        assert "labelu_migration_sandbox" in str(exc)
    else:
        raise AssertionError("生产库名必须被MySQL隔离演练安全闸门拒绝")


def test_rehearsal_does_not_create_retired_tables(tmp_path):
    module = _load_script()
    url = f"sqlite:///{(tmp_path / 'retired.sqlite').as_posix()}"
    module.rehearse_lease_ddl(url, allow_ddl=False)
    engine = create_engine(url)
    try:
        assert not set(inspect(engine).get_table_names()) & module.RETIRED_TABLES
    finally:
        engine.dispose()
