"""审计三个执行租约 ORM 字段与正式 Alembic 链的漂移。

这是只读审计，不连接数据库、不执行迁移、不修改正式 ``versions``。空库
bootstrap 会直接按 ORM 建表，因此不能用空库结构反推正式迁移已经覆盖；本
脚本同时读取正式迁移源码、当前 ORM 元数据和一期候选迁移，明确输出缺口。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).parents[1]
DEFAULT_INI = ROOT / "alembic_labelu" / "alembic.ini"
FORMAL_VERSIONS = ROOT / "alembic_labelu" / "versions"
CANDIDATE = (
    ROOT
    / "docs"
    / "7gai_phase_one_migration_candidates"
    / "gai_p1_08_add_dispatch_leases.py"
)
TARGET_TABLES = ("yolo_train_job", "yolo_bm1688_export", "yolo_edge_validation_job")
LEASE_COLUMNS = (
    "queue_lease_owner",
    "queue_lease_expires_at",
    "execution_lease_owner",
    "execution_lease_expires_at",
    "execution_fencing_token",
)
LEASE_INDEXES = {
    table: (
        f"ix_{table}_queue_lease_owner",
        f"ix_{table}_queue_lease_expires_at",
        f"ix_{table}_execution_lease_owner",
        f"ix_{table}_execution_lease_expires_at",
        scan_index,
    )
    for table, scan_index in (
        ("yolo_train_job", "ix_yolo_train_job_training_worker_scan"),
        ("yolo_bm1688_export", "ix_yolo_bm1688_export_worker_scan"),
        ("yolo_edge_validation_job", "ix_yolo_edge_validation_job_dispatch_scan"),
    )
}
AUXILIARY_COLUMNS = {
    "yolo_train_job": ("assigned_gpu_json", "execution_runtime_pid"),
    "yolo_bm1688_export": ("worker_action",),
}
AUXILIARY_INDEXES = {"yolo_bm1688_export": ("ix_yolo_bm1688_export_worker_action",)}


def _formal_sources(versions_dir: Path) -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(versions_dir.glob("*.py"))
        if path.name != "__init__.py"
    }


def _orm_contract() -> dict[str, dict[str, list[str]]]:
    # Importing the model package registers all declarative tables on Base.metadata.
    from labelu.internal.common.db import Base
    import labelu.internal.domain.models  # noqa: F401

    result: dict[str, dict[str, list[str]]] = {}
    for table_name in TARGET_TABLES:
        table = Base.metadata.tables.get(table_name)
        if table is None:
            result[table_name] = {
                "columns": [],
                "indexes": [],
                "辅助字段": [],
                "辅助索引": [],
            }
            continue
        result[table_name] = {
            "columns": sorted(
                column.name for column in table.columns if column.name in LEASE_COLUMNS
            ),
            "indexes": sorted(
                index.name
                for index in table.indexes
                if index.name
                and (
                    index.name in LEASE_INDEXES[table_name]
                    or any(column.name in LEASE_COLUMNS for column in index.columns)
                )
            ),
            "辅助字段": sorted(
                column.name
                for column in table.columns
                if column.name in AUXILIARY_COLUMNS.get(table_name, ())
            ),
            "辅助索引": sorted(
                index.name
                for index in table.indexes
                if index.name in AUXILIARY_INDEXES.get(table_name, ())
            ),
        }
    return result


def audit_migration_drift(
    alembic_ini: str | Path = DEFAULT_INI,
    *,
    versions_dir: str | Path = FORMAL_VERSIONS,
    candidate_path: str | Path = CANDIDATE,
) -> dict[str, Any]:
    """返回正式迁移、ORM 和候选租约对象的差异矩阵。"""

    ini_path = Path(alembic_ini)
    config = Config(str(ini_path))
    config.set_main_option("script_location", str(ini_path.parent))
    scripts = ScriptDirectory.from_config(config)
    sources = _formal_sources(Path(versions_dir))
    formal_columns = {
        column: sorted(name for name, text in sources.items() if column in text)
        for column in LEASE_COLUMNS
    }
    formal_indexes = {
        index: sorted(name for name, text in sources.items() if index in text)
        for indexes in LEASE_INDEXES.values()
        for index in indexes
    }
    auxiliary_columns = {
        column: sorted(name for name, text in sources.items() if column in text)
        for columns in AUXILIARY_COLUMNS.values()
        for column in columns
    }
    auxiliary_indexes = {
        index: sorted(name for name, text in sources.items() if index in text)
        for indexes in AUXILIARY_INDEXES.values()
        for index in indexes
    }
    orm = _orm_contract()
    missing_formal_columns = {
        table: [column for column in LEASE_COLUMNS if not formal_columns[column]]
        for table in TARGET_TABLES
    }
    missing_formal_indexes = {
        table: [index for index in indexes if not formal_indexes[index]]
        for table, indexes in LEASE_INDEXES.items()
    }
    missing_auxiliary_columns = {
        table: [column for column in columns if not auxiliary_columns[column]]
        for table, columns in AUXILIARY_COLUMNS.items()
    }
    missing_auxiliary_indexes = {
        table: [index for index in indexes if not auxiliary_indexes[index]]
        for table, indexes in AUXILIARY_INDEXES.items()
    }
    return {
        "正式迁移 head": scripts.get_heads(),
        "正式迁移是否唯一 head": len(scripts.get_heads()) == 1,
        "正式迁移文件数": len(sources),
        "ORM租约契约": orm,
        "正式迁移字段来源": formal_columns,
        "正式迁移索引来源": formal_indexes,
        "正式迁移辅助字段来源": auxiliary_columns,
        "正式迁移辅助索引来源": auxiliary_indexes,
        "正式迁移缺失字段": missing_formal_columns,
        "正式迁移缺失索引": missing_formal_indexes,
        "正式迁移缺失辅助字段": missing_auxiliary_columns,
        "正式迁移缺失辅助索引": missing_auxiliary_indexes,
        "候选迁移路径": str(Path(candidate_path)),
        "候选迁移存在": Path(candidate_path).exists(),
        "候选是否属于正式versions": Path(candidate_path).parent.resolve()
        == Path(versions_dir).resolve(),
        "状态": (
            "存在迁移漂移"
            if any(missing_formal_columns.values())
            or any(missing_formal_indexes.values())
            or any(missing_auxiliary_columns.values())
            or any(missing_auxiliary_indexes.values())
            or not Path(candidate_path).exists()
            or Path(candidate_path).parent.resolve() == Path(versions_dir).resolve()
            else "无迁移漂移"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计执行租约 ORM 与正式 Alembic 链")
    parser.add_argument("--alembic-ini", default=str(DEFAULT_INI))
    parser.add_argument("--versions-dir", default=str(FORMAL_VERSIONS))
    parser.add_argument("--candidate", default=str(CANDIDATE))
    args = parser.parse_args(argv)
    report = audit_migration_drift(
        args.alembic_ini,
        versions_dir=args.versions_dir,
        candidate_path=args.candidate,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["状态"] == "存在迁移漂移" else 0


if __name__ == "__main__":
    raise SystemExit(main())
