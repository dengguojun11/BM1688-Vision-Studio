from __future__ import annotations

from fastapi import APIRouter, Depends, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.image_cleaning import (
    PatchImageCleaningPolicyCommand,
)
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.image_cleaning import (
    ImageCleaningPolicyResponse,
)
from labelu.internal.application.service import image_cleaning as service
from labelu.internal.common import db as db_module
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/image-cleaning-policy", tags=["图片清洗配置"])


@router.get("", response_model=OkResp[ImageCleaningPolicyResponse])
async def get_policy(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    try:
        return OkResp[ImageCleaningPolicyResponse](data=service.policy_response())
    except (ValueError, RuntimeError) as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=f"图片清洗配置不可用：{exc}",
        ) from exc


@router.patch("", response_model=OkResp[ImageCleaningPolicyResponse])
async def patch_policy(
    cmd: PatchImageCleaningPolicyCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    try:
        saved = service.save_policy(cmd)
        return OkResp[ImageCleaningPolicyResponse](data=service.policy_response(saved))
    except ValueError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message=f"图片清洗配置保存失败：{exc}",
        ) from exc
