from fastapi import APIRouter, Depends, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from labelu.internal.application.response.base import OkResp
from labelu.internal.application.service import edge_validation_settings as service
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.security import security
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(tags=["边端设备管理"])


class EdgeValidationSettingsResponse(BaseModel):
    version: int = Field(default=1, description="配置版本")
    min_images: int = Field(default=200, description="边端验证最低图片数")
    max_images: int = Field(default=500, description="边端验证最多图片数")


class UpdateEdgeValidationSettingsCommand(BaseModel):
    min_images: int = Field(ge=1, le=500, description="边端验证最低图片数")
    max_images: int = Field(ge=1, le=500, description="边端验证最多图片数")


def _manager_only(user: User) -> None:
    if not is_task_superuser(user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="仅管理人员可维护边端验证配置",
        )


@router.get(
    "/edge-validation-settings",
    response_model=OkResp[EdgeValidationSettingsResponse],
)
def get_edge_validation_settings(
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    _manager_only(current_user)
    return OkResp[EdgeValidationSettingsResponse](
        data=EdgeValidationSettingsResponse(**service.get_config())
    )


@router.put(
    "/edge-validation-settings",
    response_model=OkResp[EdgeValidationSettingsResponse],
)
def update_edge_validation_settings(
    cmd: UpdateEdgeValidationSettingsCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    _manager_only(current_user)
    try:
        config = service.update_config(
            min_images=cmd.min_images,
            max_images=cmd.max_images,
            updated_by=int(current_user.id),
        )
    except ValueError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise LabelUException(
            code=ErrorCode.CODE_50000_TASK_ERROR,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message=str(exc),
        ) from exc
    return OkResp[EdgeValidationSettingsResponse](
        data=EdgeValidationSettingsResponse(**config)
    )
