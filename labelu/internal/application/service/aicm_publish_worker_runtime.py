"""独立 AICM 发布 Worker。

Web 进程只负责校验并持久化发布请求；开启本 Worker 后，AICM 上传、平台
模型目录登记和结果回写全部在独立进程中完成。默认关闭，数据库字段不完整
时失败关闭。发布到外部 AICM 后不提供回滚，问题模型由管理人员关闭本平台
后续下载并发布替代版本。
"""

from __future__ import annotations

import argparse
import os
import socket
import time
import uuid
from datetime import datetime
from typing import Callable

from sqlalchemy import inspect as sqlalchemy_inspect, or_

from labelu.internal.adapter.persistence import crud_yolo_bm1688_export
from labelu.internal.application.service import yolo_aicm_publish
from labelu.internal.common.aicm_publish_job import load_job
from labelu.internal.common.config import settings
from labelu.internal.common.db import SessionLocal, begin_transaction
from labelu.internal.domain.models.yolo_bm1688_export import YoloBm1688Export


def dedicated_worker_enabled() -> bool:
    return bool(getattr(settings, "AICM_PUBLISH_DEDICATED_WORKER_ENABLED", False))


def default_owner() -> str:
    host = socket.gethostname() or "unknown-host"
    return f"aicm-publish-worker:{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def dedicated_worker_ready(db_factory: Callable[[], object] = SessionLocal) -> tuple[bool, str]:
    required = (
        "publish_status",
        "publish_job_id",
        "publish_lease_owner",
        "publish_lease_expires_at",
        "publish_fencing_token",
    )
    missing_orm = [name for name in required if not hasattr(YoloBm1688Export, name)]
    if missing_orm:
        return False, "AICM 发布表缺少租约字段：" + ",".join(missing_orm)
    db = db_factory()
    try:
        columns = {
            str(item["name"])
            for item in sqlalchemy_inspect(db.get_bind()).get_columns(
                YoloBm1688Export.__tablename__
            )
        }
    except Exception as exc:
        return False, f"无法确认 AICM 发布租约字段：{exc}"
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()
    missing_columns = sorted(set(required) - columns)
    if missing_columns:
        return False, "数据库缺少 AICM 发布租约字段：" + ",".join(missing_columns)
    return True, "ok"


def _candidate_ids(db, *, now: datetime, limit: int) -> list[tuple[int, str]]:
    rows = (
        db.query(YoloBm1688Export.id, YoloBm1688Export.publish_job_id)
        .filter(
            YoloBm1688Export.published_model_id.is_(None),
            or_(
                YoloBm1688Export.publish_status == "PENDING",
                YoloBm1688Export.publish_status.is_(None),
                YoloBm1688Export.publish_status == "PROCESSING",
            ),
            or_(
                YoloBm1688Export.publish_lease_owner.is_(None),
                YoloBm1688Export.publish_lease_expires_at.is_(None),
                YoloBm1688Export.publish_lease_expires_at <= now,
            ),
            YoloBm1688Export.publish_job_id.isnot(None),
        )
        .order_by(YoloBm1688Export.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    return [
        (int(row[0]), str(row[1]))
        for row in rows
        if str(row[1] or "").strip()
    ]


def _payload(job_id: str) -> dict:
    raw = load_job(job_id) or {}
    return raw if isinstance(raw, dict) else {}


def run_worker_once(
    db_factory: Callable[[], object] = SessionLocal,
    *,
    owner: str | None = None,
    limit: int | None = None,
    enabled: bool | None = None,
    run_job: Callable[..., None] | None = None,
) -> dict[str, object]:
    active = dedicated_worker_enabled() if enabled is None else bool(enabled)
    if not active:
        return {"enabled": False, "ready": False, "claimed": 0, "failed": 0}
    ready, reason = dedicated_worker_ready(db_factory)
    if not ready:
        return {"enabled": True, "ready": False, "claimed": 0, "failed": 0, "reason": reason}
    batch_limit = int(
        limit
        if limit is not None
        else getattr(settings, "AICM_PUBLISH_WORKER_BATCH_LIMIT", 1)
    )
    if batch_limit < 1:
        raise ValueError("limit must be positive")
    lease_seconds = max(
        60,
        int(getattr(settings, "AICM_PUBLISH_WORKER_LEASE_SECONDS", 21600)),
    )
    worker_owner = owner or default_owner()
    scan_db = db_factory()
    try:
        candidates = _candidate_ids(scan_db, now=datetime.now(), limit=batch_limit)
    finally:
        close = getattr(scan_db, "close", None)
        if callable(close):
            close()
    claimed = failed = 0
    for export_id, job_id in candidates:
        claim_db = db_factory()
        try:
            with begin_transaction(claim_db):
                row, claim_reason = crud_yolo_bm1688_export.claim_publish(
                    claim_db,
                    export_id=export_id,
                    job_id=job_id,
                    lease_owner=worker_owner,
                    lease_seconds=lease_seconds,
                )
                token = int(row.publish_fencing_token) if row is not None else None
        finally:
            close = getattr(claim_db, "close", None)
            if callable(close):
                close()
        if token is None:
            continue
        claimed += 1
        payload = _payload(job_id)
        if not payload:
            fail_db = db_factory()
            try:
                with begin_transaction(fail_db):
                    crud_yolo_bm1688_export.finish_publish_claim(
                        fail_db,
                        export_id=export_id,
                        job_id=job_id,
                        lease_owner=worker_owner,
                        fencing_token=token,
                        status="FAILED",
                    )
            finally:
                close = getattr(fail_db, "close", None)
                if callable(close):
                    close()
            failed += 1
            continue
        try:
            runner = run_job or yolo_aicm_publish._run_publish_job_sync
            runner(
                job_id,
                export_id,
                int(payload.get("user_id") or 0),
                str(payload.get("environment_id") or ""),
                bool(payload.get("keep_template_cache", False)),
                payload.get("bmodel_precision"),
                payload.get("bmodel_rel_path"),
                str(payload.get("publish_mode") or "automatic"),
                worker_owner,
                token,
            )
        except Exception:
            failed += 1
    return {
        "enabled": True,
        "ready": True,
        "candidates": len(candidates),
        "claimed": claimed,
        "failed": failed,
        "reason": reason,
    }


def run_worker_loop(*, once: bool = False) -> None:
    if not dedicated_worker_enabled():
        raise RuntimeError("AICM_PUBLISH_DEDICATED_WORKER_ENABLED=false，拒绝启动发布 Worker")
    interval = max(
        1.0,
        float(getattr(settings, "AICM_PUBLISH_WORKER_INTERVAL_SECONDS", 5.0)),
    )
    while True:
        run_worker_once()
        if once:
            return
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AICM 模型发布独立 Worker")
    parser.add_argument("--once", action="store_true", help="只执行一次扫描")
    args = parser.parse_args(argv)
    run_worker_loop(once=bool(args.once))
    return 0


__all__ = [
    "dedicated_worker_enabled",
    "dedicated_worker_ready",
    "default_owner",
    "run_worker_once",
    "run_worker_loop",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
