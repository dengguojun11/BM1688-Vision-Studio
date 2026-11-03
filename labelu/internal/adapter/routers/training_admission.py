from fastapi import APIRouter, Depends, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field

from labelu.internal.application.response.base import OkResp
from labelu.internal.application.service import training_admission as service
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.security import security
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter()


class TrainingAdmissionConfigResponse(BaseModel):
    version: int = 1
    min_sample_count: int = 50


class UpdateTrainingAdmissionConfigCommand(BaseModel):
    min_sample_count: int = Field(ge=1, le=100000)


def _manager_only(user: User) -> None:
    if not is_task_superuser(user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="仅管理人员可维护训练准入配置",
        )


@router.get(
    "/training-admission-config",
    response_model=OkResp[TrainingAdmissionConfigResponse],
    status_code=status.HTTP_200_OK,
)
def get_training_admission_config(
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    _manager_only(current_user)
    return OkResp[TrainingAdmissionConfigResponse](
        data=TrainingAdmissionConfigResponse(**service.get_config())
    )


@router.put(
    "/training-admission-config",
    response_model=OkResp[TrainingAdmissionConfigResponse],
    status_code=status.HTTP_200_OK,
)
def update_training_admission_config(
    cmd: UpdateTrainingAdmissionConfigCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    _manager_only(current_user)
    try:
        config = service.update_config(
            min_sample_count=cmd.min_sample_count,
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
    return OkResp[TrainingAdmissionConfigResponse](
        data=TrainingAdmissionConfigResponse(**config)
    )
