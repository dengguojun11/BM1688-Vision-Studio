from __future__ import annotations

from fastapi import APIRouter, Depends, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.image_cleaning_task import (
    CreateSamplesWithImageCleaningCommand,
    ImageCleaningPreviewCommand,
)
from labelu.internal.application.command.sample import CreateSampleCommand
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.image_cleaning_task import (
    ImageCleaningCreateSamplesResponse,
    ImageCleaningPreviewResponse,
)
from labelu.internal.application.service import image_cleaning_task as cleaning_service
from labelu.internal.application.service import sample as sample_service
from labelu.internal.common import db as db_module
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/tasks", tags=["任务图片清洗"])


def _partial_cleaning_error(
    exc: Exception,
    *,
    duplicate_attachment_ids: list[int],
    duplicate_count: int,
    kept_count: int,
) -> LabelUException:
    """Expose the irreversible part of a failed cleaning submission.

    Physical deletion intentionally happens before sample creation so a failed
    storage deletion cannot leave samples pointing at an undeleted duplicate.
    If sample creation then fails, the client must remove the already-deleted
    attachments from its upload queue before allowing a retry.
    """

    details = {
        "cleaning_partial": True,
        "deleted_attachment_ids": [int(value) for value in duplicate_attachment_ids],
        "deleted_count": int(duplicate_count),
        "kept_count": int(kept_count),
        "samples_created": False,
    }
    if isinstance(exc, LabelUException):
        exc.msg = f"{exc.msg}；重复图片已物理删除，请刷新任务后重试"
        exc.detail = exc.msg
        exc.extra = {**getattr(exc, "extra", {}), "data": details}
        return exc
    return LabelUException(
        code=ErrorCode.CODE_50000_TASK_ERROR,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message="保留图片样本创建失败；重复图片已物理删除，请刷新任务后重试",
        extra={"data": details},
    )


@router.post(
    "/{task_id}/image-cleaning/preview",
    response_model=OkResp[ImageCleaningPreviewResponse],
    status_code=status.HTTP_200_OK,
)
async def preview_image_cleaning(
    task_id: int,
    cmd: ImageCleaningPreviewCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = cleaning_service.preview_task_images(
        db=db,
        task_id=task_id,
        cmd=cmd,
        current_user=current_user,
    )
    return OkResp[ImageCleaningPreviewResponse](data=data)


@router.post(
    "/{task_id}/samples/with-image-cleaning",
    response_model=OkResp[ImageCleaningCreateSamplesResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_samples_with_image_cleaning(
    task_id: int,
    cmd: CreateSamplesWithImageCleaningCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    submitted_ids, preview = cleaning_service.validate_task_cleaning(
        db=db,
        task_id=task_id,
        cmd=cmd.cleaning,
        current_user=current_user,
        submitted_keep_ids=[sample.file_id for sample in cmd.samples],
    )
    submitted_set = set(submitted_ids)
    sample_commands = [
        sample for sample in cmd.samples if int(sample.file_id) in submitted_set
    ]
    # Delete duplicates before creating any samples.  A failed physical
    # deletion must not leave a partially-created batch behind.
    cleaning_service.delete_task_cleaning_duplicates(
        db=db,
        task_id=task_id,
        duplicate_attachment_ids=preview.duplicate_attachment_ids,
    )
    result = None
    if sample_commands:
        try:
            result = await sample_service.create(
                db=db,
                task_id=task_id,
                cmd=[
                    CreateSampleCommand(file_id=item.file_id, data=item.data)
                    for item in sample_commands
                ],
                current_user=current_user,
            )
        except Exception as exc:
            raise _partial_cleaning_error(
                exc,
                duplicate_attachment_ids=preview.duplicate_attachment_ids,
                duplicate_count=preview.duplicate_count,
                kept_count=preview.keep_count,
            ) from exc
    return OkResp[ImageCleaningCreateSamplesResponse](
        data=ImageCleaningCreateSamplesResponse(
            ids=list(result.ids or []) if result is not None else [],
            deleted_attachment_ids=list(preview.duplicate_attachment_ids),
            deleted_count=preview.duplicate_count,
            kept_count=preview.keep_count,
        )
    )
