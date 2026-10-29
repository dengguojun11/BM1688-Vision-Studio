from pathlib import Path
import hashlib
import os
import re
import tempfile
import aiofiles
from PIL import Image, ImageOps
from sqlalchemy.orm import Session
from fastapi import APIRouter, status, Depends, Query, Security
from fastapi import File, Form, Header, UploadFile
from fastapi.responses import FileResponse, StreamingResponse, Response, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool
import mimetypes

from labelu.internal.common import db as db_module
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.security import security
from labelu.internal.domain.models.user import User
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.application.service import attachment as service
from labelu.internal.application.command.attachment import AttachmentCommand
from labelu.internal.application.command.attachment import AttachmentDeleteCommand
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.base import CommonDataResp
from labelu.internal.application.response.attachment import AttachmentResponse


router = APIRouter(prefix="/tasks", tags=["attachments"])

ATTACHMENT_CACHE_CONTROL = "public, max-age=31536000"
ANNOTATION_DISPLAY_MAX_SIDE = 1672
ANNOTATION_DISPLAY_CACHE_CONTROL = "public, max-age=31536000"
ANNOTATION_DISPLAY_CACHE_DIR = ".annotation_display_cache"


def _is_image_path(file_path: str) -> bool:
    mime_type = mimetypes.guess_type(file_path)[0] or ""
    return mime_type.startswith("image/")


def _annotation_display_cache_path(local_path: Path, max_side: int, suffix: str) -> Path:
    stat = local_path.stat()
    cache_key = hashlib.sha256(
        f"{local_path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{max_side}".encode("utf-8")
    ).hexdigest()
    return Path(settings.MEDIA_ROOT) / ANNOTATION_DISPLAY_CACHE_DIR / f"{cache_key}{suffix}"


def _is_fresh_cache(cache_path: Path, local_path: Path) -> bool:
    return cache_path.is_file() and cache_path.stat().st_mtime_ns >= local_path.stat().st_mtime_ns


def _prepare_annotation_display_image(
    local_path: Path,
    max_side: int,
    fallback_media_type: str,
) -> tuple[Path, str, bool]:
    """Decode/resize/cache an image outside the async request event loop."""
    with Image.open(local_path) as image:
        image = ImageOps.exif_transpose(image)
        width, height = image.size
        image_max_side = max(width, height)

        if image_max_side <= max_side:
            return local_path, fallback_media_type, False

        suffix = local_path.suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
            suffix = ".jpg"
        cache_path = _annotation_display_cache_path(local_path, max_side, suffix)
        cache_media_type = mimetypes.guess_type(str(cache_path))[0] or "image/jpeg"

        if _is_fresh_cache(cache_path, local_path):
            return cache_path, cache_media_type, True

        scale = max_side / image_max_side
        target_size = (
            max(1, int(width * scale + 0.5)),
            max(1, int(height * scale + 0.5)),
        )
        image = image.resize(target_size, Image.Resampling.LANCZOS)

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, dir=str(cache_path.parent)
        )
        tmp_path = Path(tmp.name)
        tmp.close()

        format_name = {
            ".jpg": "JPEG",
            ".jpeg": "JPEG",
            ".png": "PNG",
            ".webp": "WEBP",
        }.get(suffix, "JPEG")
        if format_name == "JPEG" and image.mode != "RGB":
            image = image.convert("RGB")
        save_kwargs = {"quality": 85, "optimize": True} if format_name == "JPEG" else {}
        try:
            image.save(tmp_path, format=format_name, **save_kwargs)
            os.replace(tmp_path, cache_path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        return cache_path, cache_media_type, True


@router.post(
    "/{task_id}/attachments",
    response_model=OkResp[AttachmentResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create(
    task_id: int,
    file: UploadFile = File(...),
    purpose: str | None = Form(default=None),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create attechment as annnotation sample.
    """
    # business logic
    cmd = AttachmentCommand(file=file, purpose=purpose)
    data = await service.create(
        db=db, task_id=task_id, cmd=cmd, current_user=current_user
    )

    # response
    return OkResp[AttachmentResponse](data=data)

@router.get(
    "/attachment/data-source/{attachment_id}",
    response_class=FileResponse,
    status_code=status.HTTP_200_OK,
)
async def download_datasource_attachment(
    attachment_id: int,
    db: Session = Depends(db_module.get_db),
):
    data = await service.download_datasource_attachment(db=db, attachment_id=attachment_id)
    return FileResponse(
        path=data["local_path"],
        media_type=data.get("content_type"),
        headers={"Cache-Control": ATTACHMENT_CACHE_CONTROL},
    )


@router.get(
    "/attachment/{file_path:path}",
    response_class=FileResponse,
    status_code=status.HTTP_200_OK,
)
async def download_attachment(file_path: str):
    """
    download attachment.
    """

    # business logic
    data = await service.download_attachment(file_path=file_path)
    if data.get("redirect_url"):
        return RedirectResponse(url=data["redirect_url"], status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    return FileResponse(
        path=data["local_path"],
        headers={"Cache-Control": ATTACHMENT_CACHE_CONTROL},
    )

@router.get(
    "/annotation-display/data-source/{attachment_id}",
    response_class=FileResponse,
    status_code=status.HTTP_200_OK,
)
async def get_datasource_annotation_display_image(
    attachment_id: int,
    max_side: int = Query(default=ANNOTATION_DISPLAY_MAX_SIDE, ge=256, le=4096),
    db: Session = Depends(db_module.get_db),
):
    try:
        data = await service.download_datasource_attachment(db=db, attachment_id=attachment_id)
        local_path = Path(data["local_path"])
    except (FileNotFoundError, OSError, LabelUException):
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not _is_image_path(str(local_path)):
        return FileResponse(
            path=str(local_path),
            media_type=data.get("content_type") or "application/octet-stream",
            headers={"Cache-Control": ATTACHMENT_CACHE_CONTROL},
        )

    if not local_path.exists():
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    try:
        display_path, display_media_type, is_cached = await run_in_threadpool(
            _prepare_annotation_display_image,
            local_path,
            max_side,
            data.get("content_type")
            or mimetypes.guess_type(str(local_path))[0]
            or "application/octet-stream",
        )
    except LabelUException:
        raise
    except Exception:
        return FileResponse(
            path=str(local_path),
            media_type=data.get("content_type") or mimetypes.guess_type(str(local_path))[0] or "application/octet-stream",
            headers={"Cache-Control": ATTACHMENT_CACHE_CONTROL},
        )

    return FileResponse(
        path=str(display_path),
        media_type=display_media_type,
        headers={
            "Cache-Control": (
                ANNOTATION_DISPLAY_CACHE_CONTROL
                if is_cached
                else ATTACHMENT_CACHE_CONTROL
            )
        },
    )


@router.get(
    "/annotation-display/{file_path:path}",
    response_class=FileResponse,
    status_code=status.HTTP_200_OK,
)
async def get_annotation_display_image(
    file_path: str,
    max_side: int = Query(default=ANNOTATION_DISPLAY_MAX_SIDE, ge=256, le=4096),
):
    """
    Serve a temporary image with bounded max side for the annotation page only.

    默认最大边 1672；标注结果仍按原图绝对坐标入库，前端会按原图/展示图比例换算。
    不写 task_attachment，不替换原文件，不影响导出与 YOLO 训练读取原图。
    """
    if not _is_image_path(file_path):
        return await download_attachment(file_path)

    try:
        data = await service.download_attachment(file_path=file_path)
        if data.get("redirect_url"):
            return RedirectResponse(url=data["redirect_url"], status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        local_path = Path(data["local_path"])
    except (FileNotFoundError, OSError, LabelUException):
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not local_path.exists():
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    try:
        display_path, display_media_type, is_cached = await run_in_threadpool(
            _prepare_annotation_display_image,
            local_path,
            max_side,
            mimetypes.guess_type(str(local_path))[0] or "application/octet-stream",
        )
    except LabelUException:
        raise
    except Exception:
        return FileResponse(
            path=str(local_path),
            media_type=mimetypes.guess_type(str(local_path))[0] or "application/octet-stream",
            headers={"Cache-Control": ATTACHMENT_CACHE_CONTROL},
        )

    return FileResponse(
        path=str(display_path),
        media_type=display_media_type,
        headers={
            "Cache-Control": (
                ANNOTATION_DISPLAY_CACHE_CONTROL
                if is_cached
                else ATTACHMENT_CACHE_CONTROL
            )
        },
    )


@router.get(
    "/partial/data-source/{attachment_id}",
    response_class=FileResponse,
    status_code=status.HTTP_200_OK,
)
async def get_datasource_content(
    attachment_id: int,
    range: str = Header(None),
    db: Session = Depends(db_module.get_db),
):
    try:
        full_path = await service.download_datasource_attachment(db=db, attachment_id=attachment_id)
        full_path = Path(full_path["local_path"])
    except (FileNotFoundError, OSError, LabelUException):
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if not full_path.exists():
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    file_size = full_path.stat().st_size
    media_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"

    if not range:
        return FileResponse(
            path=str(full_path),
            media_type=media_type,
            headers={"Accept-Ranges": "bytes"}
        )

    try:
        range_match = re.match(r'bytes=(\d+)-(\d*)', range)
        if not range_match:
            raise ValueError("Invalid range format")

        start = int(range_match.group(1))
        end_str = range_match.group(2)
        end = int(end_str) if end_str else min(start + 1024 * 1024, file_size - 1)

        if start < 0 or start >= file_size:
            raise ValueError(f"Start position {start} out of bounds")

        if end >= file_size:
            end = file_size - 1

        content_length = end - start + 1
    except ValueError:
        return Response(
            content=full_path.read_bytes(),
            media_type=media_type,
            headers={"Accept-Ranges": "bytes"}
        )

    async def file_stream():
        async with aiofiles.open(str(full_path), 'rb') as f:
            await f.seek(start)
            remaining = content_length
            chunk_size = min(64 * 1024, remaining)

            while remaining > 0:
                chunk = await f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(content_length),
    }

    return StreamingResponse(
        file_stream(),
        status_code=206,
        media_type=media_type,
        headers=headers
    )


@router.get(
    "/partial/{file_path:path}",
    response_class=FileResponse,
    status_code=status.HTTP_200_OK,
)
async def get_content(file_path: str, range: str = Header(None)):
    """
    partial content
    """
    
    try:
        full_path = await service.download_attachment(file_path=file_path)
        if full_path.get("redirect_url"):
            return RedirectResponse(url=full_path["redirect_url"], status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        full_path = Path(full_path["local_path"])
    except (FileNotFoundError, OSError, LabelUException):
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    
    if not full_path.exists():
        raise LabelUException(
            code=ErrorCode.CODE_51001_TASK_ATTACHMENT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Business logic
    file_size = full_path.stat().st_size
    media_type = mimetypes.guess_type(str(full_path))[0] or "application/octet-stream"
    
    if not range:
        return FileResponse(
            path=str(full_path),
            media_type=media_type,
            headers={"Accept-Ranges": "bytes"}
        )
        
    try:
        range_match = re.match(r'bytes=(\d+)-(\d*)', range)
        if not range_match:
            raise ValueError("Invalid range format")
        
        start = int(range_match.group(1))
        end_str = range_match.group(2)
        end = int(end_str) if end_str else min(start + 1024 * 1024, file_size - 1)
        
        # 验证范围
        if start < 0 or start >= file_size:
            raise ValueError(f"Start position {start} out of bounds")
        
        if end >= file_size:
            end = file_size - 1
        
        content_length = end - start + 1
        
    except ValueError:
        return Response(
            content=full_path.read_bytes(),
            media_type=media_type,
            headers={"Accept-Ranges": "bytes"}
        )
        
        
    async def file_stream():
        async with aiofiles.open(str(full_path), 'rb') as f:
            await f.seek(start)
            remaining = content_length
            chunk_size = min(64 * 1024, remaining)
            
            while remaining > 0:
                chunk = await f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
    
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(content_length),
    }
    
    return StreamingResponse(
        file_stream(),
        status_code=206,
        media_type=media_type,
        headers=headers
    )


@router.delete(
    "/{task_id}/attachments",
    response_model=OkResp[CommonDataResp],
    status_code=status.HTTP_200_OK,
)
async def delete(
    task_id: int,
    cmd: AttachmentDeleteCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    delete task.
    """

    # business logic
    data = await service.delete(
        db=db, task_id=task_id, cmd=cmd, current_user=current_user
    )

    # response
    return OkResp[CommonDataResp](data=data)
