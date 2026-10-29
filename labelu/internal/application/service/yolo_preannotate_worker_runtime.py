"""Dedicated worker for manual YOLO batch preannotation.

The web process persists the complete inference request and returns.  This
runtime claims rows with a lease fencing generation, then reuses the existing
inference implementation.  It is deliberately synchronous and bounded so a
single managed process cannot fan out unbounded model memory.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from labelu.internal.adapter.persistence import crud_yolo_preannotate_job
from labelu.internal.application.service import yolo_preannotate
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.domain.models.auto_label_job import AutoLabelStatus


_LOG = "[yolo-preannotate-worker]"


def dedicated_worker_enabled() -> bool:
    return bool(getattr(settings, "YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED", False))


def worker_owner() -> str:
    return f"yolo-preannotate:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def _payload(job) -> dict[str, Any]:
    try:
        value = json.loads(job.payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _finish_fatal(job_id: int, owner: str, token: int, message: str) -> None:
    with session_scope() as db:
        with begin_transaction(db):
            crud_yolo_preannotate_job.finish_fenced(
                db,
                job_id=job_id,
                lease_owner=owner,
                fencing_token=token,
                status=AutoLabelStatus.FAILED.value,
                error_message=str(message)[:4000],
            )


def _run_claimed(job_id: int, owner: str, token: int, lease_seconds: int) -> None:
    with session_scope() as db:
        job = crud_yolo_preannotate_job.get(db=db, job_id=job_id)
        payload = _payload(job) if job else {}
    try:
        sample_ids = [
            int(value)
            for value in payload.get("sample_ids") or []
            if str(value).isdigit() and int(value) > 0
        ]
        if not sample_ids:
            raise RuntimeError("手工 YOLO 预标注作业缺少 sample_ids")
        plate_artifacts = {
            str(key): Path(str(value))
            for key, value in (payload.get("plate_artifacts") or {}).items()
            if str(value).strip()
        }
        yolo_preannotate._run_yolo_preannotate_sync(
            int(job_id),
            int(payload["task_id"]),
            sample_ids,
            str(payload["weights_path"]),
            int(payload["imgsz"]),
            float(payload["conf"]),
            bool(payload.get("overwrite", True)),
            str(payload["model_kind"]),
            int(payload["model_id"]),
            int(payload["user_id"]),
            str(payload.get("job_type") or "yolo_detection"),
            plate_artifacts,
            payload.get("plate_metadata") or {},
            lease_owner=owner,
            lease_fencing_token=token,
            lease_seconds=lease_seconds,
            apply_to_canvas_overwrite=payload.get("apply_to_canvas_overwrite"),
            agnostic_nms=bool(payload.get("agnostic_nms", False)),
        )
    except Exception as exc:
        logger.opt(exception=exc).error("{} job_fatal job_id={}", _LOG, job_id)
        _finish_fatal(job_id, owner, token, str(exc))


def run_worker_once(*, limit: int | None = None) -> int:
    if not dedicated_worker_enabled():
        logger.warning("{} disabled; refusing to execute jobs", _LOG)
        return 0
    configured_limit = max(
        1,
        int(
            limit
            if limit is not None
            else getattr(settings, "YOLO_PREANNOTATE_WORKER_BATCH_LIMIT", 1)
        ),
    )
    configured_limit = min(
        configured_limit,
        max(1, int(getattr(settings, "YOLO_PREANNOTATE_MAX_CONCURRENT_JOBS", 1))),
    )
    lease_seconds = int(
        getattr(settings, "YOLO_PREANNOTATE_WORKER_LEASE_SECONDS", 900)
    )
    owner = worker_owner()
    claimed = 0
    for _ in range(configured_limit):
        with session_scope() as db:
            rows = crud_yolo_preannotate_job.list_runnable_jobs(db, limit=1)
            job_id = int(rows[0].id) if rows else None
            token = (
                crud_yolo_preannotate_job.claim_with_token(
                    db,
                    job_id=job_id,
                    lease_owner=owner,
                    lease_seconds=lease_seconds,
                )
                if job_id is not None
                else None
            )
        if job_id is None or token is None:
            break
        claimed += 1
        _run_claimed(job_id, owner, int(token), lease_seconds)
    return claimed


def run_worker_loop(*, once: bool = False) -> None:
    if not dedicated_worker_enabled():
        raise RuntimeError(
            "YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED=false，拒绝启动预标注 Worker"
        )
    interval = max(
        1.0,
        float(getattr(settings, "YOLO_PREANNOTATE_WORKER_INTERVAL_SECONDS", 5.0)),
    )
    while True:
        run_worker_once()
        if once:
            return
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="手工 YOLO 批量预标注独立 Worker")
    parser.add_argument("--once", action="store_true", help="只执行一次扫描")
    args = parser.parse_args()
    run_worker_loop(once=bool(args.once))


__all__ = ["dedicated_worker_enabled", "run_worker_once", "run_worker_loop"]
