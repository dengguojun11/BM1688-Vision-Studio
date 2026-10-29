"""检查一期候选迁移是否具备真实生产基线证据。

默认没有快照输入时故意返回阻塞。该命令不会连接数据库，也不会执行候选
``upgrade``；它只是防止将空库 bootstrap 结果误写成生产结构结论。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from labelu.internal.common.phase_one_candidate_baseline_guard import (
    guard_candidate_baseline,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="一期候选迁移生产基线闭锁检查")
    parser.add_argument("--candidate-table", action="append", default=[])
    parser.add_argument("--snapshot-tables", help="脱敏生产快照的JSON数组文件")
    parser.add_argument("--snapshot-revision")
    args = parser.parse_args()
    snapshot = None
    if args.snapshot_tables:
        snapshot = json.loads(Path(args.snapshot_tables).read_text(encoding="utf-8"))
        if not isinstance(snapshot, list) or not all(isinstance(item, str) for item in snapshot):
            raise SystemExit("快照文件必须是字符串表名JSON数组")
    report = guard_candidate_baseline(
        args.candidate_table,
        snapshot_tables=snapshot,
        snapshot_revision=args.snapshot_revision,
    )
    print(json.dumps(report.__dict__, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.status == "通过" else 2


if __name__ == "__main__":
    raise SystemExit(main())
