"""Read-only preflight for the phase-one Alembic suffix replacement."""

from __future__ import annotations

import argparse
import json

from labelu.internal.common.phase_one_migration_rebuild_audit import (
    audit_legacy_suffix,
    audit_replacement_workspace,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="一期迁移后缀离线审计")
    parser.add_argument(
        "--alembic-ini",
        default="labelu/alembic_labelu/alembic.ini",
        help="现有Alembic配置路径",
    )
    parser.add_argument(
        "--replacement-versions",
        help="包含八个候选迁移文件的隔离versions目录",
    )
    args = parser.parse_args()
    report = {"legacy_suffix": audit_legacy_suffix(args.alembic_ini)}
    if args.replacement_versions:
        report["replacement_workspace"] = audit_replacement_workspace(
            args.replacement_versions
        )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
