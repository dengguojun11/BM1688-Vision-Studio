"""只读对账执行租约候选与正式 Alembic 链。

此脚本只读取迁移源码和 Alembic 图，不导入数据库、不移动候选文件、不执行
upgrade/downgrade。它回答一个明确问题：``gai_p1_08`` 是否可以在不制造
第二个 head 的情况下直接成为正式迁移。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util import CommandError


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INI = ROOT / "alembic_labelu" / "alembic.ini"
DEFAULT_VERSIONS = ROOT / "alembic_labelu" / "versions"
DEFAULT_CANDIDATE = (
    ROOT / "docs" / "7gai_phase_one_migration_candidates" / "gai_p1_08_add_dispatch_leases.py"
)
PRODUCTION_BASELINE = "h7i8j9k0l1m2"
FORMAL_REVISION = "z4a5b6c7d8e9"

LEASE_COLUMNS = {
    "queue_lease_owner",
    "queue_lease_expires_at",
    "execution_lease_owner",
    "execution_lease_expires_at",
    "execution_fencing_token",
}
AUXILIARY_COLUMNS = {
    "yolo_train_job": {"assigned_gpu_json", "execution_runtime_pid"},
    "yolo_bm1688_export": {"worker_action"},
    "yolo_edge_validation_job": set(),
}
TABLES = tuple(AUXILIARY_COLUMNS)


def _load(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载迁移源码: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _formal_contract(module: Any) -> tuple[dict[str, set[str]], dict[str, dict[str, tuple[str, ...]]]]:
    columns: dict[str, set[str]] = {}
    indexes: dict[str, dict[str, tuple[str, ...]]] = {}
    for table_name, contract in module._TABLES.items():
        columns[table_name] = set(contract["columns"])
        indexes[table_name] = {
            name: tuple(values) for name, values in contract["indexes"].items()
        }
    return columns, indexes


def _candidate_contract(module: Any) -> tuple[dict[str, set[str]], dict[str, dict[str, tuple[str, ...]]]]:
    columns: dict[str, set[str]] = {}
    indexes: dict[str, dict[str, tuple[str, ...]]] = {}
    for table_name, names in module._TABLES.items():
        columns[table_name] = LEASE_COLUMNS | AUXILIARY_COLUMNS[table_name]
        index_names = list(names)
        if table_name == "yolo_bm1688_export":
            index_names.append("ix_yolo_bm1688_export_worker_action")
        scan_columns = (
            ("status", "worker_action", "queue_lease_expires_at", "id")
            if table_name == "yolo_bm1688_export"
            else ("status", "queued_at", "id")
            if table_name == "yolo_edge_validation_job"
            else ("status", "queue_lease_expires_at", "id")
        )
        indexes[table_name] = {}
        for name in index_names:
            if name.endswith("_worker_scan") or name.endswith("_dispatch_scan"):
                values = scan_columns
            elif name.endswith("_worker_action"):
                values = ("worker_action",)
            else:
                values = (name.rsplit("_", 1)[-1],)
                if name.endswith("_lease_owner"):
                    values = ("queue_lease_owner",) if "queue_lease" in name else ("execution_lease_owner",)
                elif name.endswith("_lease_expires_at"):
                    values = ("queue_lease_expires_at",) if "queue_lease" in name else ("execution_lease_expires_at",)
            indexes[table_name][name] = values
    return columns, indexes


def _reachable(scripts: ScriptDirectory, start: str, target: str) -> bool:
    """Follow all parent edges, including merge revisions, without a DB."""

    pending = [start]
    seen: set[str] = set()
    while pending:
        revision = pending.pop()
        if revision in seen:
            continue
        seen.add(revision)
        if revision == target:
            return True
        script = scripts.get_revision(revision)
        parents = script.down_revision
        if isinstance(parents, tuple):
            pending.extend(parents)
        elif parents:
            pending.append(parents)
    return False


def audit_alignment(
    *,
    alembic_ini: str | Path = DEFAULT_INI,
    versions_dir: str | Path = DEFAULT_VERSIONS,
    candidate_path: str | Path = DEFAULT_CANDIDATE,
) -> dict[str, Any]:
    """返回候选/正式字段、索引和父链的只读差异报告。"""

    ini = Path(alembic_ini)
    config = Config(str(ini))
    config.set_main_option("script_location", str(ini.parent))
    scripts = ScriptDirectory.from_config(config)
    versions = Path(versions_dir).resolve()
    candidate = Path(candidate_path).resolve()
    formal_path = next(
        versions.glob(f"{FORMAL_REVISION}_*.py"), None
    )
    if formal_path is None:
        raise FileNotFoundError(f"正式迁移不存在: {FORMAL_REVISION}")
    if not candidate.is_file():
        raise FileNotFoundError(f"候选迁移不存在: {candidate}")
    formal = _load(formal_path, "formal_execution_lease_migration")
    candidate_module = _load(candidate, "candidate_execution_lease_migration")
    formal_columns, formal_indexes = _formal_contract(formal)
    candidate_columns, candidate_indexes = _candidate_contract(candidate_module)

    column_diffs = {
        table: {
            "formal_only": sorted(formal_columns[table] - candidate_columns[table]),
            "candidate_only": sorted(candidate_columns[table] - formal_columns[table]),
        }
        for table in TABLES
    }
    index_diffs = {
        table: {
            "formal_only": sorted(set(formal_indexes[table]) - set(candidate_indexes[table])),
            "candidate_only": sorted(set(candidate_indexes[table]) - set(formal_indexes[table])),
            "column_order_mismatch": {
                name: {
                    "formal": list(formal_indexes[table][name]),
                    "candidate": list(candidate_indexes[table][name]),
                }
                for name in set(formal_indexes[table]) & set(candidate_indexes[table])
                if formal_indexes[table][name] != candidate_indexes[table][name]
            },
        }
        for table in TABLES
    }
    formal_revision = scripts.get_revision(FORMAL_REVISION)
    formal_heads = tuple(scripts.get_heads())
    reachable_from_baseline = (
        _reachable(scripts, formal_heads[0], PRODUCTION_BASELINE)
        if len(formal_heads) == 1
        else False
    )
    try:
        candidate_parent_exists = (
            scripts.get_revision(candidate_module.down_revision) is not None
        )
    except CommandError:
        # Alembic raises instead of returning None for an unknown parent. An
        # unknown candidate parent is the exact blocker this audit must report.
        candidate_parent_exists = False
    semantic_match = all(
        not diff["formal_only"] and not diff["candidate_only"]
        for diff in column_diffs.values()
    ) and all(
        not diff["formal_only"]
        and not diff["candidate_only"]
        and not diff["column_order_mismatch"]
        for diff in index_diffs.values()
    )
    blockers: list[str] = []
    if candidate_module.down_revision != formal_revision.down_revision:
        blockers.append(
            f"候选父版本 {candidate_module.down_revision} 与正式 {FORMAL_REVISION} 父版本 {formal_revision.down_revision} 不同"
        )
    if not candidate_parent_exists:
        blockers.append(f"候选父版本 {candidate_module.down_revision} 不存在于正式 Alembic 图")
    if candidate.parent == versions:
        blockers.append("候选已位于正式 versions，需先确认 revision 唯一性")
    if len(formal_heads) != 1:
        blockers.append(f"正式 Alembic head 非唯一: {list(formal_heads)}")
    if not reachable_from_baseline:
        blockers.append(f"当前 head 不可从生产基线 {PRODUCTION_BASELINE} 到达")
    if not semantic_match:
        blockers.append("候选与正式 z4 的字段或索引语义不完全一致")
    return {
        "production_baseline": PRODUCTION_BASELINE,
        "formal_revision": FORMAL_REVISION,
        "formal_parent": formal_revision.down_revision,
        "formal_heads": list(formal_heads),
        "current_head": formal_heads[0] if len(formal_heads) == 1 else None,
        "current_head_reachable_from_production_baseline": reachable_from_baseline,
        "candidate_revision": candidate_module.revision,
        "candidate_parent": candidate_module.down_revision,
        "candidate_parent_exists_in_formal_graph": candidate_parent_exists,
        "candidate_in_formal_versions": candidate.parent == versions,
        "semantic_contract_match": semantic_match,
        "column_diffs": column_diffs,
        "index_diffs": index_diffs,
        "direct_formal_merge_safe": not blockers,
        "minimum_blockers": blockers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计执行租约候选与正式 Alembic 链")
    parser.add_argument("--alembic-ini", default=str(DEFAULT_INI))
    parser.add_argument("--versions-dir", default=str(DEFAULT_VERSIONS))
    parser.add_argument("--candidate", default=str(DEFAULT_CANDIDATE))
    args = parser.parse_args(argv)
    report = audit_alignment(
        alembic_ini=args.alembic_ini,
        versions_dir=args.versions_dir,
        candidate_path=args.candidate,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["direct_formal_merge_safe"] else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
