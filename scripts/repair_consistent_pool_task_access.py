"""补全「训练筛选一致」任务的协同成员（修复 task 在列表不可见 / 打开 404）。

用法（生产服务器）：
  cd /data/labeIU/labeIuproject/labeIU
  python scripts/repair_consistent_pool_task_access.py 59
  python scripts/repair_consistent_pool_task_access.py --all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from labelu.internal.application.service.yolo_train_consistent_pool import (  # noqa: E402
    _read_registry,
    repair_pool_task_access,
)
from labelu.internal.common.db import session_scope  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task_ids", nargs="*", type=int, help="筛选池 task_id")
    parser.add_argument("--all", action="store_true", help="registry 中全部筛选池任务")
    args = parser.parse_args()

    ids = list(args.task_ids or [])
    if args.all:
        reg = _read_registry()
        cats = reg.get("categories") if isinstance(reg.get("categories"), dict) else {}
        for entry in cats.values():
            if isinstance(entry, dict) and entry.get("task_id"):
                ids.append(int(entry["task_id"]))
    ids = sorted(set(ids))
    if not ids:
        parser.error("请指定 task_id 或 --all")

    with session_scope() as db:
        for tid in ids:
            result = repair_pool_task_access(db, tid)
            print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
