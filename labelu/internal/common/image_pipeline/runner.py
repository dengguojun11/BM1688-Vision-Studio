from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from labelu.internal.application.command.sample import CreateSampleCommand
from labelu.internal.common.config import settings
from labelu.internal.common.image_pipeline.batch import BatchItem, run_import_batch
from labelu.internal.common.image_pipeline.config_loader import (
    is_image_upload,
    load_pipeline_config,
)
from labelu.internal.common.image_pipeline.deps import ensure_image_pipeline_deps
from labelu.internal.common.image_pipeline.features import (
    extract_features,
    validate_image_readable,
)
from labelu.internal.common.image_pipeline import staging
from labelu.internal.adapter.persistence import crud_attachment
from labelu.internal.common.storage import get_storage_backend
from labelu.internal.domain.models.attachment import TaskAttachment


def pipeline_enabled() -> bool:
    return bool(settings.IMAGE_PIPELINE_ENABLED)


def on_attachment_uploaded(
    task_id: int,
    attachment_id: int,
    local_path: Path,
    *,
    content_type: str | None,
    filename: str,
) -> None:
    """
    上传落盘后：校验可读性，特征写入磁盘暂存后立即释放内存（不写数据库）。
    """
    if not pipeline_enabled():
        return
    if not is_image_upload(content_type, filename):
        return
    if not ensure_image_pipeline_deps(reason="上传特征暂存"):
        return

    if not validate_image_readable(local_path):
        logger.warning("image_pipeline: skip unreadable image task={} file={}", task_id, filename)
        return

    feat = None
    try:
        feat = extract_features(local_path)
        if feat is not None:
            staging.save_feature(task_id, attachment_id, feat)
            logger.debug(
                "image_pipeline: 特征已暂存 task_id={} attachment_id={} file={}",
                task_id,
                attachment_id,
                filename,
            )
    except Exception as exc:
        logger.warning(
            "image_pipeline: 特征提取失败 task={} file={} err={}",
            task_id,
            filename,
            exc,
        )
    finally:
        del feat
        gc.collect()


async def on_samples_batch_create(
    db: Session,
    task_id: int,
    cmd: list[CreateSampleCommand],
) -> list[CreateSampleCommand]:
    """
    批量建样本前：对本批 file_id 跑聚类/离群/子场景，合并进 sample.data.image_pipeline；
    CPU 密集的特征提取和批处理在线程池执行，处理结束后删除暂存特征并 gc。
    """
    if not pipeline_enabled() or len(cmd) < 1:
        return cmd
    if not ensure_image_pipeline_deps(reason="批量建样本分析"):
        return cmd

    file_ids = [c.file_id for c in cmd]
    attachments = crud_attachment.get_by_ids(db=db, attachment_ids=file_ids)
    att_by_id = {a.id: a for a in attachments if a.task_id == task_id}
    active_attachment_ids = {
        row[0]
        for row in db.query(TaskAttachment.id)
        .filter(
            TaskAttachment.task_id == task_id,
            TaskAttachment.deleted_at.is_(None),
        )
        .all()
    }
    staging.clear_stale_task_staging(task_id, active_attachment_ids)

    items: list[BatchItem] = []
    attachment_ids_for_cleanup: list[int] = []

    storage = get_storage_backend()
    try:
        config = load_pipeline_config()
        for sample_cmd in cmd:
            att = att_by_id.get(sample_cmd.file_id)
            if not att:
                continue
            if not is_image_upload(None, att.filename or ""):
                continue
            local = storage.get_local_path(att.path)
            if not local:
                logger.debug(
                    "image_pipeline: skip non-local attachment id={}", att.id
                )
                continue
            path = Path(local)
            if not path.is_file():
                continue
            feat = staging.load_feature(task_id, att.id)
            items.append(
                BatchItem(
                    file_id=att.id,
                    path=path,
                    filename=att.filename or path.name,
                    feature=feat,
                )
            )
            attachment_ids_for_cleanup.append(att.id)
            if feat is not None:
                del feat

        if not items:
            logger.info(
                "image_pipeline: 批量建样本无可用图片项 task_id={} cmd_count={}",
                task_id,
                len(cmd),
            )
            return cmd

        meta_by_file = await run_in_threadpool(run_import_batch, items, config)
        item_count = len(items)
        del items

        outlier_action = (config.get("import_outlier_action") or "tag").lower()
        enriched: list[CreateSampleCommand] = []
        for sample_cmd in cmd:
            pipe_meta = meta_by_file.get(sample_cmd.file_id)
            if not pipe_meta:
                enriched.append(sample_cmd)
                continue
            if outlier_action == "reject" and pipe_meta.get("rejected"):
                logger.info(
                    "image_pipeline: skip sample for outlier file_id={}",
                    sample_cmd.file_id,
                )
                continue
            base_data = sample_cmd.data if isinstance(sample_cmd.data, dict) else {}
            merged = {**base_data, "image_pipeline": pipe_meta}
            enriched.append(
                CreateSampleCommand(file_id=sample_cmd.file_id, data=merged)
            )

        with_meta = sum(
            1 for fid in file_ids if meta_by_file.get(fid, {}).get("scene_id")
        )
        logger.info(
            "image_pipeline: 批量分析完成 task_id={} images={} with_scene_id={} "
            "samples_in={} samples_out={} staging_cleared={}",
            task_id,
            item_count,
            with_meta,
            len(cmd),
            len(enriched),
            len(attachment_ids_for_cleanup),
        )
        return enriched if enriched else cmd
    finally:
        if attachment_ids_for_cleanup:
            staging.clear_staging_for_attachments(task_id, attachment_ids_for_cleanup)
        gc.collect()
