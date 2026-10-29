"""Dedicated worker for model-provider preannotation jobs.

TagTool, LocateAnything, VLX-Seek and Rex-Omni have different model request
contracts, so they keep their historical result tables.  This runtime only
owns queue consumption: each row persists the exact input payload, is claimed
with a lease/fencing token, and is executed by the existing provider service.
The API-thread path remains available while the feature switch is disabled.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from typing import Any

from loguru import logger

from labelu.internal.adapter.persistence import (
    crud_locateanything_preannotate_job,
    crud_tagtool_preannotate_job,
)
from labelu.internal.application.service import (
    locateanything_preannotate,
    tagtool_preannotate,
    vlx_seek_preannotate,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction, session_scope
from labelu.internal.domain.models.auto_label_job import AutoLabelStatus

_LOG = "[provider-preannotate-worker]"
_PROVIDERS = ("tagtool", "locateanything", "vlx", "rex")


def dedicated_worker_enabled() -> bool:
    return bool(getattr(settings, "PROVIDER_PREANNOTATE_DEDICATED_WORKER_ENABLED", False))


def worker_owner() -> str:
    return f"provider-preannotate:{os.getpid()}:{uuid.uuid4().hex[:12]}"


def _decode_payload(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"预标注 Worker 输入不是有效 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("预标注 Worker 输入必须是对象")
    return value


def _fail_tagtool(job_id: int, owner: str, token: int, message: str) -> None:
    with session_scope() as db:
        with begin_transaction(db):
            crud_tagtool_preannotate_job.finish_fenced(
                db, job_id=job_id, lease_owner=owner,
                lease_fencing_token=token, status=AutoLabelStatus.FAILED.value,
                error_message=message[:4000],
            )


def _fail_provider(job_id: int, owner: str, token: int, message: str) -> None:
    with session_scope() as db:
        with begin_transaction(db):
            crud_locateanything_preannotate_job.finish_fenced(
                db, job_id=job_id, lease_owner=owner,
                lease_fencing_token=token, status=AutoLabelStatus.FAILED.value,
                error_message=message[:4000],
            )


def _execute_tagtool(job, owner: str, token: int) -> None:
    try:
        payload = _decode_payload(job.payload_json)
        sample_ids = [int(value) for value in payload["sample_ids"]]
        tagtool_preannotate._run_tagtool_preannotate_sync(
            int(job.id), int(job.task_id), sample_ids,
            str(payload["weights_rel_path"]), str(payload["label_map_json"]),
            bool(payload.get("overwrite", False)), str(payload["model_kind"]),
            int(payload["model_id"]), int(payload["user_id"]),
            str(payload.get("device") or ""),
            str(payload.get("provider_key") or ""),
            lease_owner=owner, lease_fencing_token=token,
            lease_seconds=int(getattr(settings, "PROVIDER_PREANNOTATE_WORKER_LEASE_SECONDS", 900)),
        )
    except Exception as exc:
        logger.exception("{} TagTool job failed id={}", _LOG, job.id)
        _fail_tagtool(int(job.id), owner, token, str(exc))


def _execute_locate(job, owner: str, token: int) -> None:
    try:
        payload = _decode_payload(job.payload_json)
        locateanything_preannotate._run_batch_sync(
            int(job.id), int(job.task_id), [int(value) for value in payload["sample_ids"]],
            dict(payload["task_config"]), bool(payload.get("filter_by_labels", True)),
            bool(payload.get("overwrite", False)), int(payload["created_by"]),
            lease_owner=owner, lease_fencing_token=token,
            lease_seconds=int(getattr(settings, "PROVIDER_PREANNOTATE_WORKER_LEASE_SECONDS", 900)),
        )
    except Exception as exc:
        logger.exception("{} LocateAnything job failed id={}", _LOG, job.id)
        _fail_provider(int(job.id), owner, token, str(exc))


def _execute_remote_provider(job, owner: str, token: int, provider: str) -> None:
    try:
        payload = _decode_payload(job.payload_json)
        vlx_seek_preannotate._run_batch_sync(
            int(job.id), int(job.task_id), [int(value) for value in payload["sample_ids"]],
            dict(payload["task_config"]), [str(value) for value in payload.get("target_labels", [])],
            bool(payload.get("overwrite", False)), int(payload["created_by"]), provider,
            lease_owner=owner, lease_fencing_token=token,
            lease_seconds=int(getattr(settings, "PROVIDER_PREANNOTATE_WORKER_LEASE_SECONDS", 900)),
        )
    except Exception as exc:
        logger.exception("{} {} job failed id={}", _LOG, provider, job.id)
        _fail_provider(int(job.id), owner, token, str(exc))


def _claim_one(kind: str, owner: str, lease_seconds: int):
    with session_scope() as db:
        if kind == "tagtool":
            rows = crud_tagtool_preannotate_job.list_runnable(db, limit=1)
            if not rows:
                return None
            row = rows[0]
            with begin_transaction(db):
                token = crud_tagtool_preannotate_job.claim_with_token(
                    db, job_id=int(row.id), lease_owner=owner, lease_seconds=lease_seconds,
                )
            return ("tagtool", int(row.id), int(token or 0)) if token else None
        rows = crud_locateanything_preannotate_job.list_runnable(db, provider=kind, limit=1)
        if not rows:
            return None
        row = rows[0]
        with begin_transaction(db):
            token = crud_locateanything_preannotate_job.claim_with_token(
                db, job_id=int(row.id), lease_owner=owner, lease_seconds=lease_seconds,
                provider=kind,
            )
        return (kind, int(row.id), int(token or 0)) if token else None


def _run_claim(claim: tuple[str, int, int], owner: str) -> None:
    kind, job_id, token = claim
    with session_scope() as db:
        job = (
            crud_tagtool_preannotate_job.get(db, job_id)
            if kind == "tagtool"
            else crud_locateanything_preannotate_job.get(db, job_id)
        )
    if job is None:
        return
    if kind == "tagtool":
        _execute_tagtool(job, owner, token)
    elif kind == "locateanything":
        _execute_locate(job, owner, token)
    else:
        _execute_remote_provider(job, owner, token, kind)


def run_worker_once(*, limit: int | None = None) -> int:
    if not dedicated_worker_enabled():
        logger.warning("{} disabled; refusing to execute jobs", _LOG)
        return 0
    configured = max(1, int(limit if limit is not None else getattr(settings, "PROVIDER_PREANNOTATE_WORKER_BATCH_LIMIT", 1)))
    configured = min(configured, max(1, int(getattr(settings, "PROVIDER_PREANNOTATE_MAX_CONCURRENT_JOBS", 1))))
    owner = worker_owner()
    count = 0
    while count < configured:
        claim = None
        for kind in _PROVIDERS:
            claim = _claim_one(kind, owner, int(getattr(settings, "PROVIDER_PREANNOTATE_WORKER_LEASE_SECONDS", 900)))
            if claim:
                break
        if claim is None:
            break
        count += 1
        _run_claim(claim, owner)
    return count


def run_worker_loop(*, once: bool = False) -> None:
    if not dedicated_worker_enabled():
        raise RuntimeError("PROVIDER_PREANNOTATE_DEDICATED_WORKER_ENABLED=false，拒绝启动预标注 Worker")
    interval = max(1.0, float(getattr(settings, "PROVIDER_PREANNOTATE_WORKER_INTERVAL_SECONDS", 5.0)))
    while True:
        run_worker_once()
        if once:
            return
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="模型 Provider 预标注独立 Worker")
    parser.add_argument("--once", action="store_true", help="只执行一次扫描")
    args = parser.parse_args()
    run_worker_loop(once=bool(args.once))


__all__ = ["dedicated_worker_enabled", "run_worker_once", "run_worker_loop"]
