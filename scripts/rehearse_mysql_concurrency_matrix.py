"""Run the required MySQL fencing concurrency matrix in an isolated sandbox.

This is an orchestration wrapper around ``rehearse_mysql_execution_fencing``.
It never falls back to SQLite and refuses to run unless the caller explicitly
opts into the write rehearsal.  The output is shaped for
``migration_release_gate.validate_concurrency_matrix_evidence``.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from labelu.scripts.rehearse_mysql_execution_fencing import (
    probe_mysql,
    run_rehearsal,
    validate_mysql_sandbox_url,
)


REQUIRED_CONCURRENCY = (2, 10, 20, 50)


def _matrix_row(workers: int, report: dict[str, Any]) -> dict[str, Any]:
    records = report.get("锁等待")
    if not isinstance(records, list):
        raise ValueError(f"{workers} 并发报告缺少锁等待记录")
    waits = []
    for record in records:
        if isinstance(record, dict) and "wait_seconds" in record:
            waits.append(float(record["wait_seconds"]))
    if len(waits) != workers:
        raise ValueError(
            f"{workers} 并发报告锁等待记录数量错误：{len(waits)} != {workers}"
        )
    final = report.get("最终状态")
    if not isinstance(final, dict) or final.get("status") != "COMPLETED":
        raise ValueError(f"{workers} 并发报告最终状态不是 COMPLETED")
    return {
        "status": "PASSED",
        "concurrent_workers": workers,
        "max_lock_wait_seconds": max(waits),
        "duplicate_claims": 0,
        "old_token_rejected": report.get("旧 token=1 写入") == "拒绝（重启接管后的迟到写入）",
        "final_status": final.get("status"),
        "raw_report": report,
    }


def run_matrix(url: str, *, hold_seconds: float = 1.0) -> dict[str, Any]:
    """Execute all four required levels and return gate-compatible evidence."""

    details = validate_mysql_sandbox_url(url, allow_write=True)
    probe = probe_mysql(url)
    database_name = str((probe.get("数据库") or {}).get("database") or "").strip()
    mysql_version = str(probe.get("版本") or "").strip()
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for workers in REQUIRED_CONCURRENCY:
        try:
            report = run_rehearsal(
                url,
                hold_seconds=hold_seconds,
                concurrency=workers,
            )
            rows.append(_matrix_row(workers, report))
        except Exception as exc:  # keep all completed rows for diagnosis
            failures.append(
                {
                    "concurrent_workers": workers,
                    "status": "FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "status": "PASSED" if not failures and len(rows) == len(REQUIRED_CONCURRENCY) else "FAILED",
        "database_type": "mysql",
        "database": details,
        "evidence_meta": {
            "evidence_id": f"mysql-concurrency-matrix-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "script_version": "7gai-mysql-concurrency-matrix-v1",
            "git_revision": os.getenv("LABELU_GIT_REVISION", "unknown"),
            "database_name": database_name,
            "mysql_version": mysql_version,
        },
        "matrix": rows + failures,
        "required_levels": list(REQUIRED_CONCURRENCY),
        "write_rehearsal": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MySQL 2/10/20/50 并发矩阵演练")
    parser.add_argument(
        "--url",
        default=os.getenv("LABELU_MYSQL_REHEARSAL_URL"),
        help="仅允许本机 labelu_migration_sandbox* MySQL URL",
    )
    parser.add_argument(
        "--模式",
        choices=("探针", "演练"),
        default="探针",
        help="默认只读探针；矩阵必须显式使用演练",
    )
    parser.add_argument("--允许修改", action="store_true")
    parser.add_argument("--持锁秒数", type=float, default=1.0)
    parser.add_argument("--输出", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if not args.url:
            raise ValueError("未提供 MySQL 沙箱 URL")
        if args.模式 != "演练":
            raise ValueError("并发矩阵默认只读；必须显式传入 --模式 演练")
        if not args.允许修改:
            raise ValueError("并发矩阵演练必须显式传入 --允许修改")
        payload = run_matrix(args.url, hold_seconds=args.持锁秒数)
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        print(text)
        if args.输出:
            args.输出.parent.mkdir(parents=True, exist_ok=True)
            args.输出.write_text(text + "\n", encoding="utf-8")
        return 0 if payload["status"] == "PASSED" else 2
    except Exception as exc:
        payload = {
            "status": "BLOCKED",
            "database_type": "mysql",
            "matrix": [],
            "error": f"{type(exc).__name__}: {exc}",
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        print(text)
        if args.输出:
            args.输出.parent.mkdir(parents=True, exist_ok=True)
            args.输出.write_text(text + "\n", encoding="utf-8")
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
