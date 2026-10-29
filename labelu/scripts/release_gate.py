"""Run the local M6-04 release gate.

Usage:
    python -m labelu.scripts.release_gate
"""

from __future__ import annotations

import argparse
from pathlib import Path

from labelu.internal.common.release_gate import run_release_gate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="7GAI local release gate")
    parser.add_argument(
        "--alembic-config",
        default=str(Path(__file__).resolve().parents[1] / "alembic_labelu" / "alembic.ini"),
    )
    parser.add_argument(
        "--jwt-from-env",
        action="store_true",
        help="require PASSWORD_SECRET_KEY to be treated as externally supplied",
    )
    args = parser.parse_args(argv)
    report = run_release_gate(
        config_path=args.alembic_config,
        jwt_from_env=True if args.jwt_from_env else None,
    )
    print(f"migration_heads={','.join(report.migration_heads)}")
    print(f"security_valid={report.security.valid}")
    if report.security.errors:
        print("security_errors=" + " | ".join(report.security.errors))
    print(f"backup_restore_ok={report.backup_restore_ok}")
    print(f"release_gate_valid={report.valid}")
    return 0 if report.valid else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
