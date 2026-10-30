import re
import aiofiles
import os
import tempfile
import mimetypes
import shutil
import hashlib
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image, ImageOps
from loguru import logger
from fastapi import status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from labelu.internal.common.db import begin_transaction
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode
from labelu.internal.common.error_code import LabelUException
from labelu.internal.common.task_access import (
    assert_user_has_task_access,
    assert_user_has_task_member_access,
)
from labelu.internal.common.storage import (
    build_absolute_media_url,
    build_annotation_display_api_path,
    build_attachment_api_path,
    build_datasource_annotation_display_api_path,
    build_datasource_attachment_api_path,
    build_datasource_partial_api_path,
    build_partial_api_path,
    build_thumbnail_key,
    create_thumbnail_bytes,
    get_storage_backend,
)
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.attachment import TaskAttachment
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.adapter.persistence import crud_task
from labelu.internal.adapter.persistence import crud_attachment
from labelu.internal.application.command.attachment import AttachmentCommand
from labelu.internal.application.command.attachment import AttachmentDeleteCommand
from labelu.internal.application.response.base import CommonDataResp
from labelu.internal.application.response.attachment import AttachmentResponse


EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE = "edge_validation_external_image"


def _is_image_filename(filename: str | None) -> bool:
    if not filename:
        return False
    return Path(filename).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


def _get_local_image_size(path: Path | None, filename: str | None = None) -> tuple[int | None, int | None]:
    if not _is_image_filename(filename or (path.name if path else None)):
        return None, None
    if not path or not path.is_file():
        return None, None
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            return image.size
    except Exception:
        return None, None


def _sanitize_attachment_filename(filename: str) -> str:
    sanitized = re.sub(r'%', '_pct_', filename)
    sanitized = re.sub(r'[\\/*?:"<>|#]', '_', sanitized)
    return sanitized


def normalize_attachment_purpose(purpose: str | None) -> str | None:
    raw = str(purpose or "").strip().lower()
    if raw == EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE:
        return EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE
    return None


def is_edge_validation_external_attachment_path(path: str | None) -> bool:
    normalized = "/" + str(path or "").replace("\\", "/").strip("/") + "/"
    return f"/{EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE}/" in normalized or "/edge_validation_external/" in normalized


def _retire_missing_duplicate(db: Session, attachment: TaskAttachment) -> None:
    """Hide an orphaned local attachment before creating its replacement.

    Sample deletion historically removed the object but left the attachment row
    active.  A content-hash lookup must not keep returning that unusable row.
    Do not retire a row that is still referenced by an active sample; that case
    needs an explicit data repair rather than silently changing a live sample.
    """

    if getattr(attachment, "data_source_id", None):
        return
    active_sample = (
        db.query(TaskSample.id)
        .filter(
            TaskSample.file_id == int(attachment.id),
            TaskSample.deleted_at.is_(None),
        )
        .first()
    )
    if active_sample:
        return
    with begin_transaction(db):
        attachment.deleted_at = datetime.now()
        db.flush()


def _build_unique_attachment_relative_path(
    storage,
    task_id: int,
    filename: str,
    *,
    purpose: str | None = None,
) -> tuple[str, str]:
    sanitized = _sanitize_attachment_filename(filename or "copied-file")
    base_dir = Path(settings.UPLOAD_DIR).joinpath(str(task_id))
    if purpose == EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE:
        base_dir = base_dir.joinpath("edge_validation_external")
    stem = Path(sanitized).stem or "copied-file"
    suffix = Path(sanitized).suffix

    candidate = sanitized
    index = 1
    while storage.exists(str(base_dir.joinpath(candidate)).replace("\\", "/")):
        suffix_part = suffix or ""
        candidate = f"{stem}-copy{'' if index == 1 else f'-{index}'}{suffix_part}"
        index += 1

    relative_path = str(base_dir.joinpath(candidate)).replace("\\", "/")
    return candidate, relative_path


def _staging_dir() -> Path:
    path = Path(settings.MEDIA_ROOT) / ".labelu_upload_staging"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _datasource_cache_dir() -> Path:
    path = Path(settings.MEDIA_ROOT) / ".labelu_datasource_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _guess_content_type(filename: str | None, fallback: str | None = None) -> str:
    guessed = mimetypes.guess_type(filename or "")[0]
    return guessed or fallback or "application/octet-stream"


def _build_datasource_cache_path(
    attachment,
    *,
    filename: str,
    suffix: str,
    fingerprint: str,
) -> Path:
    safe_stem = Path(_sanitize_attachment_filename(filename or "copied-file")).stem or "copied-file"
    digest = hashlib.sha1(
        f"{attachment.id}|{attachment.path}|{fingerprint}".encode("utf-8")
    ).hexdigest()[:16]
    return _datasource_cache_dir() / f"{attachment.id}-{safe_stem}-{digest}{suffix}"


def _materialize_datasource_attachment_to_cache(attachment) -> tuple[Path, str]:
    if not getattr(attachment, "data_source_id", None) or not getattr(attachment, "data_source", None):
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    filename = getattr(attachment, "filename", None) or Path(getattr(attachment, "path", "")).name or "copied-file"
    suffix = Path(filename).suffix

    from botocore.exceptions import ClientError
    from labelu.internal.application.service.datasource import _build_s3_client

    client = _build_s3_client(attachment.data_source)
    try:
        head = client.head_object(Bucket=attachment.data_source.bucket, Key=attachment.path)
    except ClientError as exc:
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            raise LabelUException(
                code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        logger.opt(exception=exc).error(
            "datasource attachment head failed attachment_id={} key={}",
            getattr(attachment, "id", None),
            attachment.path,
        )
        raise LabelUException(
            code=ErrorCode.CODE_62002_S3_REQUEST_FAILED,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    except Exception as exc:
        logger.opt(exception=exc).error(
            "datasource attachment head failed attachment_id={} key={}",
            getattr(attachment, "id", None),
            attachment.path,
        )
        raise LabelUException(
            code=ErrorCode.CODE_62002_S3_REQUEST_FAILED,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    content_type = _guess_content_type(filename, head.get("ContentType"))
    fingerprint = "|".join(
        [
            str(head.get("ETag", "")),
            str(head.get("LastModified", "")),
            str(head.get("ContentLength", "")),
        ]
    )
    cache_path = _build_datasource_cache_path(
        attachment,
        filename=filename,
        suffix=suffix,
        fingerprint=fingerprint,
    )
    if cache_path.is_file():
        return cache_path, content_type

    tmp = tempfile.NamedTemporaryFile(delete=False, dir=str(_datasource_cache_dir()), suffix=suffix)
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        response = client.get_object(Bucket=attachment.data_source.bucket, Key=attachment.path)
        body = response["Body"]
        try:
            with tmp_path.open("wb") as out_file:
                shutil.copyfileobj(body, out_file)
        finally:
            body.close()

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not cache_path.exists():
            os.replace(tmp_path, cache_path)
        else:
            tmp_path.unlink(missing_ok=True)

        for stale_path in _datasource_cache_dir().glob(f"{attachment.id}-*"):
            if stale_path != cache_path:
                stale_path.unlink(missing_ok=True)
    except ClientError as exc:
        tmp_path.unlink(missing_ok=True)
        error_code = str(exc.response.get("Error", {}).get("Code", ""))
        if error_code in {"404", "NoSuchKey", "NotFound"}:
            raise LabelUException(
                code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        logger.opt(exception=exc).error(
            "datasource attachment get failed attachment_id={} key={}",
            getattr(attachment, "id", None),
            attachment.path,
        )
        raise LabelUException(
            code=ErrorCode.CODE_62002_S3_REQUEST_FAILED,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        logger.opt(exception=exc).error(
            "datasource attachment get failed attachment_id={} key={}",
            getattr(attachment, "id", None),
            attachment.path,
        )
        raise LabelUException(
            code=ErrorCode.CODE_62002_S3_REQUEST_FAILED,
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    return cache_path, content_type


def resolve_attachment_local_path(attachment) -> Path | None:
    """Resolve local uploads and external S3 attachments through one path."""
    if not attachment:
        return None
    if getattr(attachment, "data_source_id", None):
        try:
            path, _content_type = _materialize_datasource_attachment_to_cache(attachment)
            return path if path.is_file() else None
        except Exception:
            return None
    relative = str(getattr(attachment, "path", "") or "").lstrip("/\\")
    if not relative:
        return None
    path = (Path(settings.MEDIA_ROOT) / relative).resolve()
    return path if path.is_file() else None


async def _materialize_source_attachment_to_temp(attachment) -> tuple[Path, str]:
    filename = getattr(attachment, "filename", None) or Path(getattr(attachment, "path", "")).name or "copied-file"
    storage = get_storage_backend()
    local_path = None
    if getattr(attachment, "path", None) and not getattr(attachment, "data_source_id", None):
        local_path = storage.get_local_path(attachment.path)

    suffix = Path(filename).suffix
    if local_path and local_path.is_file():
        tmp = tempfile.NamedTemporaryFile(delete=False, dir=str(_staging_dir()), suffix=suffix)
        tmp_path = Path(tmp.name)
        tmp.close()
        shutil.copyfile(local_path, tmp_path)
        return tmp_path, _guess_content_type(filename)

    if getattr(attachment, "data_source_id", None):
        datasource_path, content_type = _materialize_datasource_attachment_to_cache(attachment)
        tmp = tempfile.NamedTemporaryFile(delete=False, dir=str(_staging_dir()), suffix=suffix)
        tmp_path = Path(tmp.name)
        tmp.close()
        shutil.copyfile(datasource_path, tmp_path)
        return tmp_path, content_type

    source = build_attachment_response(attachment)
    source_url = source.url if source else None
    if not source_url:
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    tmp = tempfile.NamedTemporaryFile(delete=False, dir=str(_staging_dir()), suffix=suffix)
    tmp_path = Path(tmp.name)
    tmp.close()

    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            async with client.stream("GET", source_url) as response:
                response.raise_for_status()
                content_type = _guess_content_type(filename, response.headers.get("content-type"))
                async with aiofiles.open(tmp_path, "wb") as out_file:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            await out_file.write(chunk)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise LabelUException(
            code=ErrorCode.CODE_51000_CREATE_ATTACHMENT_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return tmp_path, _guess_content_type(filename)


def build_attachment_response(attachment, *, for_annotation: bool = False) -> AttachmentResponse | None:
    if not attachment:
        return None

    # External data source attachment — use the data source's own credentials
    if getattr(attachment, "data_source_id", None) and getattr(attachment, "data_source", None):
        purpose = (
            EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE
            if is_edge_validation_external_attachment_path(getattr(attachment, "path", None))
            else None
        )
        return AttachmentResponse(
            id=attachment.id,
            filename=attachment.filename,
            url=build_absolute_media_url(build_datasource_attachment_api_path(attachment.id)),
            thumbnail_url=None,
            stream_url=build_absolute_media_url(build_datasource_partial_api_path(attachment.id)),
            annotation_display_url=(
                build_absolute_media_url(
                    build_datasource_annotation_display_api_path(attachment.id)
                )
                if for_annotation
                else None
            ),
            storage_backend="s3",
            purpose=purpose,
            sha256=getattr(attachment, "sha256", None),
            size_bytes=getattr(attachment, "size_bytes", None),
        )

    # Local or global S3 storage
    storage = get_storage_backend()
    annotation_display_url = None
    original_width = None
    original_height = None
    if storage.is_remote:
        url = storage.get_read_url(attachment.path)
        thumbnail_key = build_thumbnail_key(attachment.path)
        thumbnail_url = storage.get_read_url(thumbnail_key) if storage.exists(thumbnail_key) else None
        stream_url = url
    else:
        local_path = storage.get_local_path(attachment.path)
        if for_annotation:
            original_width, original_height = _get_local_image_size(local_path, attachment.filename)
        thumb_key = build_thumbnail_key(attachment.path)
        url = build_absolute_media_url(build_attachment_api_path(attachment.path))
        thumbnail_url = (
            build_absolute_media_url(build_attachment_api_path(thumb_key))
            if storage.exists(thumb_key)
            else None
        )
        stream_url = build_absolute_media_url(build_partial_api_path(attachment.path))
        if for_annotation:
            annotation_display_url = build_absolute_media_url(
                build_annotation_display_api_path(attachment.path)
            )
    return AttachmentResponse(
        id=attachment.id,
        filename=attachment.filename,
        url=url,
        thumbnail_url=thumbnail_url,
        stream_url=stream_url,
        annotation_display_url=annotation_display_url,
        original_width=original_width,
        original_height=original_height,
        storage_backend=storage.backend_name,
        purpose=(
            EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE
            if is_edge_validation_external_attachment_path(getattr(attachment, "path", None))
            else None
        ),
        sha256=getattr(attachment, "sha256", None),
        size_bytes=getattr(attachment, "size_bytes", None),
    )


async def copy_attachment_to_task(
    db: Session,
    *,
    source_attachment,
    target_task_id: int,
    current_user: User,
):
    storage = get_storage_backend()
    filename = getattr(source_attachment, "filename", None) or "copied-file"
    saved_filename, attachment_relative_path = _build_unique_attachment_relative_path(
        storage,
        target_task_id,
        filename,
    )
    thumbnail_key = build_thumbnail_key(attachment_relative_path)
    thumbnail_bytes = None
    temp_file_path = None

    from labelu.internal.common.image_pipeline.config_loader import is_image_upload
    from labelu.internal.common.image_pipeline.features import validate_image_readable

    async def _reject_invalid_image(
        temp_path: Path, total_size: int, content_type: str
    ) -> None:
        if not is_image_upload(content_type, saved_filename):
            return
        readable = (
            await run_in_threadpool(validate_image_readable, temp_path)
            if total_size >= 1
            else False
        )
        if not readable:
            raise LabelUException(
                code=ErrorCode.CODE_51003_TASK_ATTACHMENT_INVALID_IMAGE,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    try:
        temp_file_path, content_type = await _materialize_source_attachment_to_temp(source_attachment)
        total_size = temp_file_path.stat().st_size if temp_file_path.exists() else 0
        is_image = is_image_upload(content_type, saved_filename)

        if is_image and settings.IMAGE_PIPELINE_REJECT_INVALID_IMAGE:
            await _reject_invalid_image(temp_file_path, total_size, content_type)

        if is_image:
            thumbnail_bytes = await run_in_threadpool(
                create_thumbnail_bytes, temp_file_path
            )
            if thumbnail_bytes is None:
                await _reject_invalid_image(temp_file_path, total_size, content_type)

        await run_in_threadpool(
            storage.save_file,
            local_path=temp_file_path,
            key=attachment_relative_path,
            content_type=content_type,
        )

        if thumbnail_bytes:
            await run_in_threadpool(
                storage.save_bytes,
                content=thumbnail_bytes,
                key=thumbnail_key,
                content_type=content_type,
            )

        with begin_transaction(db):
            attachment = crud_attachment.create(
                db=db,
                attachment=TaskAttachment(
                    path=attachment_relative_path,
                    url=build_attachment_api_path(attachment_relative_path),
                    filename=saved_filename,
                    created_by=current_user.id,
                    updated_by=current_user.id,
                    task_id=target_task_id,
                ),
            )
    except LabelUException:
        if temp_file_path and temp_file_path.exists():
            temp_file_path.unlink(missing_ok=True)
        if storage.exists(attachment_relative_path):
            storage.delete(attachment_relative_path)
        if thumbnail_bytes and storage.exists(thumbnail_key):
            storage.delete(thumbnail_key)
        raise
    except Exception as exc:
        if temp_file_path and temp_file_path.exists():
            temp_file_path.unlink(missing_ok=True)
        if storage.exists(attachment_relative_path):
            storage.delete(attachment_relative_path)
        if thumbnail_bytes and storage.exists(thumbnail_key):
            storage.delete(thumbnail_key)
        logger.error("copy attachment failed: {}", exc)
        raise LabelUException(
            code=ErrorCode.CODE_51000_CREATE_ATTACHMENT_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if settings.IMAGE_PIPELINE_ENABLED:
        from labelu.internal.common.image_pipeline.runner import on_attachment_uploaded

        local_saved = storage.get_local_path(attachment_relative_path)
        if local_saved:
            try:
                await run_in_threadpool(
                    on_attachment_uploaded,
                    target_task_id,
                    attachment.id,
                    Path(local_saved),
                    content_type=content_type,
                    filename=saved_filename,
                )
            except Exception as exc:
                logger.warning(
                    "image_pipeline: copied attachment staging failed task={} file={} err={}",
                    target_task_id,
                    saved_filename,
                    exc,
                )

    return attachment


async def create(
    db: Session, task_id: int, cmd: AttachmentCommand, current_user: User
) -> AttachmentResponse:
    storage = get_storage_backend()
    purpose = normalize_attachment_purpose(getattr(cmd, "purpose", None))

    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task: {}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    assert_user_has_task_access(task, current_user)

    # file relative path
    path_filename = cmd.file.filename.split("/")
    #  filename = str(uuid.uuid4())[0:8] + "-" + path_filename[-1] NOTE: If you want keep filename safe, you can use uuid as filename 
    filename = path_filename[-1]
    sanitized = _sanitize_attachment_filename(filename)
    path = "/".join(path_filename[:-1])
    attachment_relative_base_dir = Path(settings.UPLOAD_DIR).joinpath(str(task_id))
    if purpose == EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE:
        attachment_relative_base_dir = attachment_relative_base_dir.joinpath("edge_validation_external")
    if path:
        attachment_relative_base_dir = attachment_relative_base_dir.joinpath(path)
    attachment_relative_path = str(attachment_relative_base_dir.joinpath(sanitized)).replace("\\", "/")

    # check file exist
    path_exists_conflict = False
    if purpose == EDGE_VALIDATION_EXTERNAL_IMAGE_PURPOSE and storage.exists(attachment_relative_path):
        stem = Path(sanitized).stem or "upload"
        suffix = Path(sanitized).suffix
        index = 1
        while storage.exists(attachment_relative_path):
            candidate = f"{stem}-copy{'' if index == 1 else f'-{index}'}{suffix}"
            attachment_relative_path = str(attachment_relative_base_dir.joinpath(candidate)).replace("\\", "/")
            sanitized = candidate
            index += 1
    else:
        # Defer the filename collision decision until content hashing. A
        # repeated upload with the same filename and bytes should be deduped.
        path_exists_conflict = storage.exists(attachment_relative_path)

    CHUNK_SIZE = 8 * 1024 * 1024  # 8MB
    thumbnail_key = build_thumbnail_key(attachment_relative_path)
    thumbnail_bytes = None
    temp_file_path = None
    from labelu.internal.common.image_pipeline.config_loader import is_image_upload
    from labelu.internal.common.image_pipeline.features import validate_image_readable

    is_image = is_image_upload(cmd.file.content_type, sanitized)

    async def _reject_invalid_image(temp_path: Path, total_size: int) -> None:
        if not is_image:
            return
        readable = (
            await run_in_threadpool(validate_image_readable, temp_path)
            if total_size >= 1
            else False
        )
        if not readable:
            raise LabelUException(
                code=ErrorCode.CODE_51003_TASK_ATTACHMENT_INVALID_IMAGE,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    try:
        staging_dir = Path(settings.MEDIA_ROOT) / ".labelu_upload_staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        temp_file = tempfile.NamedTemporaryFile(delete=False, dir=str(staging_dir))
        temp_file_path = Path(temp_file.name)
        temp_file.close()

        async with aiofiles.open(temp_file_path, "wb") as out_file:
            total_size = 0
            content_hasher = hashlib.sha256()
            while True:
                chunk = await cmd.file.read(CHUNK_SIZE)
                if not chunk:
                    break
                await out_file.write(chunk)
                content_hasher.update(chunk)
                total_size += len(chunk)
                logger.debug(f"{total_size} bytes written")

        content_sha256 = content_hasher.hexdigest()
        duplicate = crud_attachment.get_by_task_sha256(db, task_id, content_sha256)
        if duplicate is not None:
            # A hash match is reusable only while its physical object still
            # exists.  Older sample deletion removed the object but left the
            # attachment row active, so blindly returning this row creates a
            # queue item that the image-cleaning preview cannot read.
            duplicate_available = False
            try:
                duplicate_available = bool(storage.exists(duplicate.path))
            except Exception as exc:
                logger.warning(
                    "attachment deduplication availability check failed path={} err={}",
                    duplicate.path,
                    exc,
                )
            if duplicate_available:
                # Preserve the legacy contract for an exact filename
                # collision; content-addressed deduplication applies when the
                # caller chose a different name (for example a retry/import).
                if path_exists_conflict:
                    temp_file_path.unlink(missing_ok=True)
                    logger.error("file already exists:{}", attachment_relative_path)
                    raise LabelUException(
                        code=ErrorCode.CODE_51002_TASK_ATTACHMENT_ALREADY_EXISTS,
                        status_code=status.HTTP_400_BAD_REQUEST,
                    )
                # The temporary upload is deliberately discarded. Reusing the
                # existing object avoids duplicate media and preserves
                # annotations.
                temp_file_path.unlink(missing_ok=True)
                return build_attachment_response(duplicate)

            logger.warning(
                "ignoring stale attachment deduplication row id={} path={}",
                duplicate.id,
                duplicate.path,
            )
            _retire_missing_duplicate(db, duplicate)
            duplicate = None
        if path_exists_conflict:
            temp_file_path.unlink(missing_ok=True)
            logger.error("file already exists:{}", attachment_relative_path)
            raise LabelUException(
                code=ErrorCode.CODE_51002_TASK_ATTACHMENT_ALREADY_EXISTS,
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if is_image and settings.IMAGE_PIPELINE_REJECT_INVALID_IMAGE:
            await _reject_invalid_image(temp_file_path, total_size)

        if is_image:
            thumbnail_bytes = await run_in_threadpool(
                create_thumbnail_bytes, temp_file_path
            )
            if thumbnail_bytes is None:
                await _reject_invalid_image(temp_file_path, total_size)

        await run_in_threadpool(
            storage.save_file,
            local_path=temp_file_path,
            key=attachment_relative_path,
            content_type=cmd.file.content_type,
        )

        if thumbnail_bytes:
            await run_in_threadpool(
                storage.save_bytes,
                content=thumbnail_bytes,
                key=thumbnail_key,
                content_type=cmd.file.content_type,
            )

        if temp_file_path and temp_file_path.exists():
            os.remove(temp_file_path)

        logger.info(f"File saved: {attachment_relative_path}, size: {total_size} bytes")
    except LabelUException:
        if temp_file_path and temp_file_path.exists():
            os.remove(temp_file_path)
        if storage.exists(attachment_relative_path):
            storage.delete(attachment_relative_path)
        if thumbnail_bytes and storage.exists(thumbnail_key):
            storage.delete(thumbnail_key)
        raise
    except Exception as e:
        if temp_file_path and temp_file_path.exists():
            os.remove(temp_file_path)
        if storage.exists(attachment_relative_path):
            storage.delete(attachment_relative_path)
        if thumbnail_bytes and storage.exists(thumbnail_key):
            storage.delete(thumbnail_key)
        logger.error(f"Upload failed: {str(e)}")
        raise LabelUException(
            code=ErrorCode.CODE_51000_CREATE_ATTACHMENT_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    # check file already saved
    attachment_saved = await run_in_threadpool(
        storage.exists, attachment_relative_path
    )
    thumbnail_saved = (
        await run_in_threadpool(storage.exists, thumbnail_key) if is_image else True
    )
    if not attachment_saved or not thumbnail_saved:
        logger.error(
            "cannot find saved images, path is:{}, image content-type is:{}, thumbnail path is:{}",
            attachment_relative_path,
            cmd.file.content_type,
            thumbnail_key,
        )
        raise LabelUException(
            code=ErrorCode.CODE_51000_CREATE_ATTACHMENT_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    attachment_url_path = attachment_relative_path.replace("\\", "/")
    attachment_api_url = build_attachment_api_path(attachment_url_path)
    # add a task file record
    with begin_transaction(db):
        attachment = crud_attachment.create(
            db=db,
            attachment=TaskAttachment(
                path=attachment_url_path,
                url=attachment_api_url,
                filename=sanitized,
                created_by=current_user.id,
                updated_by=current_user.id,
                task_id=task_id,
                sha256=content_sha256,
                size_bytes=total_size,
                content_type=cmd.file.content_type,
            ),
        )

    if is_image and settings.IMAGE_PIPELINE_ENABLED:
        from labelu.internal.common.image_pipeline.runner import on_attachment_uploaded

        local_saved = storage.get_local_path(attachment_url_path)
        if local_saved:
            try:
                await run_in_threadpool(
                    on_attachment_uploaded,
                    task_id,
                    attachment.id,
                    Path(local_saved),
                    content_type=cmd.file.content_type,
                    filename=sanitized,
                )
            except Exception as exc:
                logger.warning(
                    "image_pipeline: 特征暂存失败 task={} file={} err={}",
                    task_id,
                    sanitized,
                    exc,
                )

    # response
    if storage.is_remote:
        url = storage.get_read_url(attachment_url_path)
        thumb_url = storage.get_read_url(thumbnail_key) if thumbnail_bytes else None
        stream = url
    else:
        url = build_absolute_media_url(attachment_api_url)
        thumb_url = (
            build_absolute_media_url(build_attachment_api_path(thumbnail_key))
            if thumbnail_bytes
            else None
        )
        stream = build_absolute_media_url(build_partial_api_path(attachment_url_path))
    annotation_display_url = None
    original_width = None
    original_height = None
    if is_image:
        original_width, original_height = await run_in_threadpool(
            _get_local_image_size,
            storage.get_local_path(attachment_url_path),
            sanitized,
        )
    if not storage.is_remote:
        annotation_display_url = build_absolute_media_url(
            build_annotation_display_api_path(attachment_url_path)
        )
    return AttachmentResponse(
        id=attachment.id,
        url=url,
        thumbnail_url=thumb_url,
        stream_url=stream,
        annotation_display_url=annotation_display_url,
        original_width=original_width,
        original_height=original_height,
        storage_backend=storage.backend_name,
        filename=sanitized,
        purpose=purpose,
        sha256=content_sha256,
        size_bytes=total_size,
    )


async def download_attachment(file_path: str) -> dict:
    storage = get_storage_backend()

    # check file exist
    if not storage.exists(file_path):
        logger.error("attachment not found:{}", file_path)
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    local_path = storage.get_local_path(file_path)
    if local_path:
        return {"local_path": str(local_path)}
    return {"redirect_url": storage.get_read_url(file_path)}


async def download_datasource_attachment(db: Session, attachment_id: int) -> dict:
    attachment = crud_attachment.get(db=db, attachment_id=attachment_id)
    if not attachment or not getattr(attachment, "data_source_id", None):
        logger.error("datasource attachment not found:{}", attachment_id)
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    local_path, content_type = _materialize_datasource_attachment_to_cache(attachment)
    return {"local_path": str(local_path), "content_type": content_type}


async def delete(
    db: Session, task_id: int, cmd: AttachmentDeleteCommand, current_user: User
) -> CommonDataResp:
    storage = get_storage_backend()

    # get task
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        logger.error("cannot find task:{}", task_id)
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    assert_user_has_task_member_access(task, current_user)

    # Only remove an attachment object when no active sample or pre-annotation
    # still references it.  The task editor calls this endpoint before its
    # sample-delete request, and deleting the object first would break the
    # sample that is about to be retired (or any other sample sharing it).
    deletable_ids: list[int] = []
    try:
        attachments = crud_attachment.get_by_ids(
            db=db, attachment_ids=cmd.attachment_ids
        )
        for attachment in attachments:
            active_sample_ref = (
                db.query(TaskSample.id)
                .filter(
                    TaskSample.file_id == int(attachment.id),
                    TaskSample.deleted_at.is_(None),
                )
                .first()
            )
            active_pre_annotation_ref = (
                db.query(TaskPreAnnotation.id)
                .filter(
                    TaskPreAnnotation.file_id == int(attachment.id),
                    TaskPreAnnotation.deleted_at.is_(None),
                )
                .first()
            )
            if active_sample_ref or active_pre_annotation_ref:
                logger.info(
                    "keep referenced attachment id={} sample_ref={} pre_annotation_ref={}",
                    attachment.id,
                    bool(active_sample_ref),
                    bool(active_pre_annotation_ref),
                )
                continue

            deletable_ids.append(int(attachment.id))
            if is_edge_validation_external_attachment_path(getattr(attachment, "path", None)):
                continue
            storage.delete(attachment.path)
            thumbnail_key = build_thumbnail_key(attachment.path)
            if storage.exists(thumbnail_key):
                storage.delete(thumbnail_key)
    except Exception as e:
        logger.error(e)

    if settings.IMAGE_PIPELINE_ENABLED:
        from labelu.internal.common.image_pipeline import staging as pipeline_staging

        pipeline_staging.clear_staging_for_attachments(task_id, deletable_ids)

    # delete
    with begin_transaction(db):
        crud_attachment.delete(db=db, attachment_ids=deletable_ids)

    # response
    return CommonDataResp(ok=True)
