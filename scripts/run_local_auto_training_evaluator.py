#!/usr/bin/env python3
"""Run one explicitly local auto-training evaluation simulation.

This command is intentionally blocked for staging/production and for
non-SQLite databases.  It is a development harness for the trusted evaluator
contract, not a replacement for a real model evaluator.

Example::

    python scripts/run_local_auto_training_evaluator.py \
      --local-simulation --run-id 12 --metrics-file .tmp/metrics.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_metrics(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    target = Path(path).resolve()
    if not target.is_file():
        raise ValueError(f"metrics file does not exist: {target}")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("metrics file must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("metrics file must contain a JSON object")
    return payload


def _assert_local_only(*, local_simulation: bool) -> None:
    if not local_simulation:
        raise RuntimeError("refusing to run without --local-simulation")
    from labelu.internal.common.config import settings

    environment = str(settings.APP_ENV or "").strip().lower()
    if environment in {"staging", "production", "prod"}:
        raise RuntimeError(
            f"local simulation is disabled in APP_ENV={settings.APP_ENV!r}"
        )
    database_url = str(settings.DATABASE_URL or "").strip().lower()
    if not database_url.startswith("sqlite:"):
        raise RuntimeError("local simulation requires a local SQLite DATABASE_URL")


def run(args: argparse.Namespace) -> dict[str, Any]:
    _assert_local_only(local_simulation=bool(args.local_simulation))
    metrics = _load_metrics(args.metrics_file)
    from labelu.internal.application.service.auto_training_local_evaluator import (
        run_local_simulated_evaluation,
    )
    from labelu.internal.common.db import SessionLocal, begin_transaction

    db = SessionLocal()
    try:
        with begin_transaction(db):
            result = run_local_simulated_evaluation(
                db,
                evaluation_run_id=args.run_id,
                worker_id=args.worker_id,
                lease_seconds=args.lease_seconds,
                metrics=metrics,
            )
        return result
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-simulation", action="store_true")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--worker-id", default="local-simulation-evaluator")
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--metrics-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args), ensure_ascii=False, sort_keys=True))
    except Exception as exc:  # CLI boundary: keep failures structured and concise.
        print(
            json.dumps(
                {"error": type(exc).__name__, "message": str(exc)[:500]},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
