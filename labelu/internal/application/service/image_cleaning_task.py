"""Task-scoped image cleaning before newly uploaded samples are created."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterable

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_attachment, crud_task
from labelu.internal.application.command.image_cleaning_task import (
    ImageCleaningCommitCommand,
    ImageCleaningPreviewCommand,
)
from labelu.internal.application.response.image_cleaning_task import (
    ImageCleaningPairResponse,
    ImageCleaningPreviewResponse,
)
from labelu.internal.application.service import image_cleaning as policy_service
from labelu.internal.application.service.attachment import resolve_attachment_local_path
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.image_pipeline.config_loader import is_image_upload
from labelu.internal.common.storage import build_thumbnail_key, get_storage_backend
from labelu.internal.common.task_access import assert_user_has_task_access
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.ingestion import MediaAsset
from labelu.internal.domain.models.media_upload import MediaUploadSession
from labelu.internal.domain.models.user import User


def _client_error(message: str, *, conflict: bool = False) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=(
            status.HTTP_409_CONFLICT if conflict else status.HTTP_400_BAD_REQUEST
        ),
        message=message,
    )


def _normalized_ids(ids: Iterable[int], *, allow_empty: bool = False) -> list[int]:
    values: list[int] = []
    seen: set[int] = set()
    for raw in ids:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise _client_error("图片附件 ID 无效") from None
        if value <= 0:
            raise _client_error("图片附件 ID 无效")
        if value not in seen:
            values.append(value)
            seen.add(value)
    if not values and not allow_empty:
        raise _client_error("至少需要一张图片")
    return values


def _task_or_error(db: Session, task_id: int):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return task


def _active_reference_ids(
    db: Session, task_id: int, attachment_ids: Iterable[int] | None = None
) -> set[int]:
    """Return attachment ids still referenced by live task records."""

    normalized = tuple(int(value) for value in attachment_ids or ())
    referenced: set[int] = set()
    for model in (TaskSample, TaskPreAnnotation):
        query = db.query(model.file_id).filter(
            model.task_id == int(task_id),
            model.deleted_at.is_(None),
            model.file_id.isnot(None),
        )
        if normalized:
            query = query.filter(model.file_id.in_(normalized))
        referenced.update(int(row[0]) for row in query.all() if row[0] is not None)
    return referenced


def _resolve_cleaning_path(attachment, cleanup_paths: list[Path]) -> Path | None:
    """Resolve a local upload or materialize a remote object for this scan."""

    if getattr(attachment, "data_source_id", None):
        return None

    storage = get_storage_backend()
    if not storage.is_remote:
        path = resolve_attachment_local_path(attachment)
        return Path(path) if path and path.is_file() else None

    filename = getattr(attachment, "filename", None) or "image"
    temporary = tempfile.NamedTemporaryFile(
        delete=False,
        suffix=Path(filename).suffix,
        prefix="labelu-image-cleaning-",
    )
    temporary_path = Path(temporary.name)
    temporary.close()
    try:
        storage.download_file(str(attachment.path), temporary_path)
        if not temporary_path.is_file() or temporary_path.stat().st_size < 1:
            raise OSError("remote image is empty")
    except Exception:
        temporary_path.unlink(missing_ok=True)
        return None
    cleanup_paths.append(temporary_path)
    return temporary_path


def _candidate_rows(
    db: Session, task_id: int, attachment_ids: list[int], cleanup_paths: list[Path]
):
    rows = crud_attachment.get_by_ids(db=db, attachment_ids=attachment_ids)
    by_id = {int(row.id): row for row in rows if int(row.task_id or 0) == int(task_id)}
    missing = [str(value) for value in attachment_ids if value not in by_id]
    if missing:
        raise _client_error(f"图片附件不存在或不属于当前任务：{', '.join(missing)}")

    active_reference_ids = _active_reference_ids(db, task_id, attachment_ids)
    items: list[dict] = []
    skipped_ids: list[int] = []
    for attachment_id in sorted(attachment_ids):
        attachment = by_id[attachment_id]
        if attachment_id in active_reference_ids:
            # A retry can submit an attachment that already became a sample.
            # It is protected from cleaning and must not be physically deleted.
            skipped_ids.append(attachment_id)
            continue
        if getattr(attachment, "data_source_id", None):
            raise _client_error(
                "图片清洗仅支持任务编辑页直接上传的图片，外部数据源图片不参与清洗"
            )
        if not is_image_upload(None, attachment.filename or ""):
            raise _client_error(
                f"附件不是可清洗的图片：{attachment.filename or attachment.id}"
            )
        path = _resolve_cleaning_path(attachment, cleanup_paths)
        if not path or not path.is_file():
            # Legacy sample deletion may leave an active attachment row after
            # its object has gone.  Ignore that stale queue item and let the
            # client remove it after the preview is confirmed.
            skipped_ids.append(attachment_id)
            continue
        items.append(
            {
                "id": int(attachment.id),
                "path": Path(path),
                "filename": attachment.filename or Path(path).name,
                "sha256": attachment.sha256 or "",
            }
        )
    skipped_set = set(skipped_ids)
    return items, by_id, [value for value in attachment_ids if value in skipped_set]


def _protected_rows(
    db: Session, task_id: int, candidate_ids: set[int], cleanup_paths: list[Path]
) -> list[dict]:
    """Return readable existing sample/pre-annotation images before candidates."""

    # Only active references can make a new upload unsafe to remove.  Scanning
    # every historical attachment made a small upload batch needlessly slow.
    protected_ids = _active_reference_ids(db, task_id) - set(candidate_ids)
    if not protected_ids:
        return []
    attachments = (
        db.query(TaskAttachment)
        .filter(
            TaskAttachment.id.in_(tuple(protected_ids)),
            TaskAttachment.task_id == int(task_id),
            TaskAttachment.deleted_at.is_(None),
        )
        .order_by(TaskAttachment.id.asc())
        .all()
    )
    protected: list[dict] = []
    for attachment in attachments:
        if getattr(attachment, "data_source_id", None):
            continue
        if not is_image_upload(None, attachment.filename or ""):
            continue
        path = _resolve_cleaning_path(attachment, cleanup_paths)
        if not path or not path.is_file():
            continue
        protected.append(
            {
                "id": int(attachment.id),
                "path": Path(path),
                "filename": attachment.filename or Path(path).name,
                "sha256": attachment.sha256 or "",
            }
        )
    return protected


def _preview(
    db: Session,
    task_id: int,
    attachment_ids: Iterable[int],
    *,
    policy_revision: int | None = None,
    current_user: User | None = None,
) -> tuple[ImageCleaningPreviewResponse, list[int]]:
    task = _task_or_error(db, task_id)
    if current_user is not None:
        assert_user_has_task_access(task, current_user)

    candidate_ids = _normalized_ids(attachment_ids)
    policy = policy_service.load_policy()
    if policy_revision is not None and int(policy_revision) != int(policy["revision"]):
        raise _client_error("图片清洗规则已更新，请重新预览", conflict=True)

    cleanup_paths: list[Path] = []
    try:
        candidates, _by_id, skipped_ids = _candidate_rows(
            db, task_id, candidate_ids, cleanup_paths
        )
        valid_candidate_ids = [int(item["id"]) for item in candidates]
        protected = _protected_rows(
            db, task_id, set(valid_candidate_ids), cleanup_paths
        )
        all_items = protected + candidates
        threshold = float(policy["presets"][policy["active_preset"]]["threshold"])
        raw = policy_service.preview_paths(
            all_items,
            threshold,
            revision=int(policy["revision"]),
        )
        candidate_set = set(valid_candidate_ids)
        duplicate_ids = [
            int(value) for value in raw["duplicate_ids"] if int(value) in candidate_set
        ]
        duplicate_set = set(duplicate_ids)
        keep_ids = [
            int(value)
            for value in valid_candidate_ids
            if int(value) not in duplicate_set
        ]
        pairs = [
            ImageCleaningPairResponse(**pair)
            for pair in raw["pairs"]
            if int(pair["duplicate_id"]) in candidate_set
        ]
        response = ImageCleaningPreviewResponse(
            active_preset=str(policy["active_preset"]),
            threshold=threshold,
            policy_revision=int(policy["revision"]),
            algorithm_version=str(policy["algorithm_version"]),
            total_count=len(candidates),
            duplicate_count=len(duplicate_ids),
            keep_count=len(keep_ids),
            duplicate_attachment_ids=duplicate_ids,
            keep_attachment_ids=keep_ids,
            pairs=pairs,
            fingerprint=str(raw["fingerprint"]),
            candidate_attachment_ids=valid_candidate_ids,
            skipped_attachment_ids=skipped_ids,
            skipped_count=len(skipped_ids),
        )
        return response, duplicate_ids
    finally:
        for path in cleanup_paths:
            path.unlink(missing_ok=True)


def preview_task_images(
    db: Session,
    task_id: int,
    cmd: ImageCleaningPreviewCommand,
    current_user: User,
) -> ImageCleaningPreviewResponse:
    response, _ = _preview(
        db,
        task_id,
        cmd.attachment_ids,
        policy_revision=cmd.policy_revision,
        current_user=current_user,
    )
    return response


def _assert_unreferenced(db: Session, _task_id: int, attachment_ids: list[int]) -> None:
    """Reject every database reference before hard-deleting an attachment."""
    if not attachment_ids:
        return
    sample_refs = (
        db.query(TaskSample.id)
        .filter(
            TaskSample.file_id.in_(tuple(attachment_ids)),
        )
        .first()
    )
    if sample_refs:
        raise _client_error("相似图片已经创建样本，不能物理删除")
    pre_refs = (
        db.query(TaskPreAnnotation.id)
        .filter(TaskPreAnnotation.file_id.in_(tuple(attachment_ids)))
        .first()
    )
    if pre_refs:
        raise _client_error("相似图片已有预标注引用，不能物理删除")


def _physically_delete(db: Session, task_id: int, attachment_ids: list[int]) -> None:
    if not attachment_ids:
        return
    storage = get_storage_backend()
    storage_keys: list[tuple[str, str | None]] = []
    try:
        # Keep the row lock while removing objects. If storage deletion fails,
        # the transaction rolls back and the attachment row remains retryable.
        with begin_transaction(db):
            attachments = (
                db.query(TaskAttachment)
                .filter(
                    TaskAttachment.id.in_(tuple(attachment_ids)),
                    TaskAttachment.task_id == int(task_id),
                    TaskAttachment.deleted_at.is_(None),
                )
                .with_for_update()
                .all()
            )
            if len(attachments) != len(attachment_ids):
                raise _client_error("待清洗图片状态已变化，请重新预览", conflict=True)
            for attachment in attachments:
                if getattr(attachment, "data_source_id", None):
                    raise _client_error("外部数据源图片不允许物理删除")
                thumbnail_key = build_thumbnail_key(attachment.path)
                storage_keys.append((attachment.path, thumbnail_key))
            _assert_unreferenced(db, task_id, attachment_ids)
            storage_errors: list[Exception] = []
            for object_key, thumbnail_key in storage_keys:
                for storage_key in (object_key, thumbnail_key):
                    if not storage_key:
                        continue
                    try:
                        # Local and S3 deletion are idempotent; deleting the
                        # thumbnail without a preceding HEAD also avoids
                        # retaining it when a transient HEAD request fails.
                        storage.delete(storage_key)
                    except Exception as exc:
                        # Continue through the batch so a retry only needs to
                        # repeat failed keys.
                        storage_errors.append(exc)
            if storage_errors:
                exc = storage_errors[0]
                raise LabelUException(
                    code=ErrorCode.CODE_50000_TASK_ERROR,
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    message=f"相似图片物理文件删除失败，请重试清理：{exc}",
                ) from exc

            # Completed resumable uploads retain an attachment_id foreign key;
            # clear it before the attachment rows are physically removed.
            db.query(MediaUploadSession).filter(
                MediaUploadSession.attachment_id.in_(tuple(attachment_ids)),
            ).update(
                {MediaUploadSession.attachment_id: None}, synchronize_session=False
            )
            # MediaAsset.attachment_id is a nullable compatibility projection
            # without a database FK; clear it before removing the attachment.
            db.query(MediaAsset).filter(
                MediaAsset.attachment_id.in_(tuple(attachment_ids)),
            ).update({MediaAsset.attachment_id: None}, synchronize_session=False)
            crud_attachment.hard_delete_by_ids(
                db=db,
                attachment_ids=attachment_ids,
                task_id=task_id,
            )
    except LabelUException:
        raise
    except Exception as exc:
        raise LabelUException(
            code=ErrorCode.CODE_50000_TASK_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="相似图片数据库记录删除失败，请停止创建样本并联系管理员",
        ) from exc

    # Feature staging files are independent of the attachment row.  They can
    # outlive a runtime toggle, so clean them whenever the helper is available.
    # Keep the import optional because image-pipeline dependencies are optional
    # in installations that do not enable the pipeline.
    try:
        from labelu.internal.common.image_pipeline import staging
    except ImportError:
        staging = None
    if staging is not None:
        staging.clear_staging_for_attachments(task_id, attachment_ids)


def validate_task_cleaning(
    db: Session,
    task_id: int,
    cmd: ImageCleaningCommitCommand,
    current_user: User,
    submitted_keep_ids: Iterable[int],
) -> tuple[list[int], ImageCleaningPreviewResponse]:
    preview, _ = _preview(
        db,
        task_id,
        cmd.attachment_ids,
        policy_revision=cmd.policy_revision,
        current_user=current_user,
    )
    if preview.fingerprint != cmd.fingerprint:
        raise _client_error("图片列表或规则已变化，请重新预览", conflict=True)

    expected_keep = set(preview.keep_attachment_ids)
    raw_submitted = list(submitted_keep_ids)
    submitted = _normalized_ids(raw_submitted, allow_empty=True)
    if len(submitted) != len(raw_submitted):
        raise _client_error("保留图片列表包含重复附件，请重新预览", conflict=True)
    if set(submitted) != expected_keep:
        raise _client_error("保留图片列表与预览结果不一致，请重新预览", conflict=True)

    return submitted, preview


def delete_task_cleaning_duplicates(
    db: Session, task_id: int, duplicate_attachment_ids: Iterable[int]
) -> None:
    _physically_delete(
        db,
        task_id,
        _normalized_ids(duplicate_attachment_ids, allow_empty=True),
    )


def apply_task_cleaning(
    db: Session,
    task_id: int,
    cmd: ImageCleaningCommitCommand,
    current_user: User,
    submitted_keep_ids: Iterable[int],
) -> tuple[list[int], ImageCleaningPreviewResponse]:
    """Validate and delete duplicates for callers that do not create samples."""

    submitted, preview = validate_task_cleaning(
        db=db,
        task_id=task_id,
        cmd=cmd,
        current_user=current_user,
        submitted_keep_ids=submitted_keep_ids,
    )
    delete_task_cleaning_duplicates(db, task_id, preview.duplicate_attachment_ids)
    return submitted, preview


__all__ = [
    "apply_task_cleaning",
    "delete_task_cleaning_duplicates",
    "preview_task_images",
    "validate_task_cleaning",
]
