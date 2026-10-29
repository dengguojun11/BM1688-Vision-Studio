"""运行只读迁移发布闸门。

该命令不会连接数据库。它只读取 Alembic/ORM 源码和显式提供的 JSON 证据；
缺少正式合入、备份恢复、MySQL 锁等待或 2/10/20/50 并发矩阵证据时返回非零退出码。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from labelu.internal.common.migration_release_gate import (
    DEFAULT_ALEMBIC_INI,
    DEFAULT_CANDIDATE_DIR,
    DEFAULT_VERSIONS,
    run_migration_release_gate,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="7GAI 只读迁移发布闸门")
    parser.add_argument("--alembic-config", default=str(DEFAULT_ALEMBIC_INI))
    parser.add_argument("--versions-dir", default=str(DEFAULT_VERSIONS))
    parser.add_argument("--candidate-dir", default=str(DEFAULT_CANDIDATE_DIR))
    parser.add_argument(
        "--sandbox-url",
        default=None,
        help="仅用于 URL 安全解析；不会建立数据库连接",
    )
    parser.add_argument("--backup-evidence", type=Path, default=None)
    parser.add_argument("--lock-wait-evidence", type=Path, default=None)
    parser.add_argument("--concurrency-evidence", type=Path, default=None)
    parser.add_argument("--evidence-output", type=Path, default=None)
    args = parser.parse_args(argv)
    report = run_migration_release_gate(
        config_path=args.alembic_config,
        versions_dir=args.versions_dir,
        candidate_dir=args.candidate_dir,
        database_url=args.sandbox_url,
        backup_evidence=args.backup_evidence,
        lock_wait_evidence=args.lock_wait_evidence,
        concurrency_evidence=args.concurrency_evidence,
    )
    payload = report.as_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.evidence_output:
        args.evidence_output.parent.mkdir(parents=True, exist_ok=True)
        args.evidence_output.write_text(text + "\n", encoding="utf-8")
    return 0 if report.valid else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
