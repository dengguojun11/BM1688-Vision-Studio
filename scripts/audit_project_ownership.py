"""Print a read-only project ownership review report.

Usage (point ``LABELU_ENV_FILE``/``DATABASE_URL`` at the intended database):
    python scripts/audit_project_ownership.py --include-ids

The command performs SELECTs only.  It never writes ownership values or
creates a project for an unscoped record.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from labelu.internal.application.service.project_ownership_audit import (
    audit_project_ownership,
    project_ownership_backfill_dry_run,
)
from labelu.internal.common.db import SessionLocal


def _safe_output_path(raw: str) -> Path:
    repository_root = Path(__file__).resolve().parents[1]
    temporary_root = (repository_root / ".tmp").resolve()
    output = Path(raw).expanduser()
    if not output.is_absolute():
        output = repository_root / output
    output = output.resolve()
    try:
        relative = output.relative_to(temporary_root)
    except ValueError as exc:
        raise ValueError("output 必须位于仓库 .tmp 子目录") from exc
    if len(relative.parts) < 2 or output.suffix.lower() != ".json":
        raise ValueError("output 必须是 .tmp/<子目录>/*.json")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit legacy NULL project ownership")
    parser.add_argument(
        "--include-ids", action="store_true", help="include bounded ID samples"
    )
    parser.add_argument("--sample-limit", type=int, default=100)
    parser.add_argument(
        "--dry-run-backfill",
        action="store_true",
        help="输出逐行确定性回填建议；只读，不更新数据库",
    )
    parser.add_argument("--row-limit", type=int, default=5000)
    parser.add_argument(
        "--output",
        help="可选 JSON 文件，仅允许写入仓库 .tmp/<子目录>/*.json",
    )
    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        if args.dry_run_backfill:
            report = project_ownership_backfill_dry_run(db, row_limit=args.row_limit)
        else:
            report = audit_project_ownership(
                db, include_ids=args.include_ids, sample_limit=args.sample_limit
            )
        payload = json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n"
        if args.output:
            try:
                output = _safe_output_path(args.output)
            except ValueError as exc:
                parser.error(str(exc))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload, encoding="utf-8")
            print(f"output={output}")
        else:
            print(payload, end="")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
