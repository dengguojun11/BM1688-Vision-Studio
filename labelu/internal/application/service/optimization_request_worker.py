from __future__ import annotations

import asyncio
import os

from loguru import logger

from labelu.internal.application.service import integration_requests as service
from labelu.internal.application.service.system_operation_log import (
    record_system_operation,
)
from labelu.internal.common.db import session_scope
from labelu.internal.domain.models.third_party_optimization import OptimizationRequest

READY_STAGES = {"DATA_READY", "DATASET_CREATED", "WAITING_REVIEW"}


def run_optimization_request_worker_once(limit: int = 5) -> dict[str, int]:
    """Refresh request coverage and start the existing strategy flow when ready.

    This worker is intentionally thin: it only bridges request rows to the
    existing strategy_evolution pipeline and keeps their projection in sync.
    """
    started = 0
    synced = 0
    with session_scope() as db:
        try:
            service.sync_active_requests(db)
            service.sync_publish_jobs(db)
        except Exception:
            logger.exception("refresh optimization requests failed")
        try:
            service.dispatch_request_callbacks(db)
        except Exception:
            logger.exception("dispatch optimization request callbacks failed")
        rows = (
            db.query(OptimizationRequest)
            .filter(
                OptimizationRequest.deleted_at.is_(None),
                OptimizationRequest.status.notin_(
                    ["SUCCEEDED", "FAILED", "CANCELLED"]
                ),
                OptimizationRequest.strategy_external_task_id.is_(None),
                OptimizationRequest.stage.in_(list(READY_STAGES)),
            )
            .order_by(OptimizationRequest.updated_at.asc())
            .limit(max(1, min(int(limit), 50)))
            .all()
        )
        for row in rows:
            if not row.package_ids_json or row.package_ids_json.strip() in {"", "[]"}:
                continue
            try:
                result = asyncio.run(service.start_strategy_flow(db, int(row.id)))
                if result.get("started"):
                    started += 1
                    synced += 1
                elif result.get("reason") == "already_started":
                    synced += 1
            except Exception:
                logger.exception(
                    "start strategy flow for optimization request failed request_id={}",
                    row.id,
                )
    if started:
        record_system_operation(
            action="启动第三方优化编排",
            summary=f"自动启动 {started} 个优化请求的现有训练编排",
            detail={
                "started": started,
                "synced": synced,
                "scanned": len(rows) if "rows" in locals() else 0,
            },
            source="optimization_request_worker",
        )
    return {"started": started, "synced": synced, "scanned": len(rows) if "rows" in locals() else 0}


def worker_enabled() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return os.environ.get("OPTIMIZATION_REQUEST_WORKER_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


async def run_optimization_request_worker(interval_seconds: int = 15) -> None:
    logger.info(
        "third-party optimization request worker started (interval={}s)",
        interval_seconds,
    )
    while True:
        try:
            result = await asyncio.to_thread(run_optimization_request_worker_once)
            if result["started"] or result["synced"]:
                logger.info("optimization request worker: {}", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("optimization request worker iteration failed")
        await asyncio.sleep(max(5, int(interval_seconds)))
