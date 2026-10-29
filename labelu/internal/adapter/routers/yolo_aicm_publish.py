from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.yolo_aicm_publish import (
    AicmPublishRequestCommand,
    CreateAicmPublishEnvironmentCommand,
    PatchAicmPublishConfigCommand,
    SetActiveAicmPublishEnvironmentCommand,
    UpdateAicmPublishEnvironmentCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.yolo_aicm_publish import (
    AicmPublishConfigResponse,
    AicmPublishEnvironmentResponse,
    AicmPublishEnvironmentsBundleResponse,
    AicmPublishHistoryRowResponse,
    AicmPublishJobResponse,
    AicmPublishResultResponse,
    AicmPublishableRowResponse,
    AicmRuntimeTemplateCandidateResponse,
)
from labelu.internal.application.service import yolo_aicm_publish as aicm_publish_service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/yolo-aicm-publish", tags=["yolo_train"])


@router.get(
    "/config",
    response_model=OkResp[AicmPublishConfigResponse],
    status_code=status.HTTP_200_OK,
)
async def get_aicm_publish_config(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = db, current_user, authorization
    data = aicm_publish_service.get_config_view()
    return OkResp[AicmPublishConfigResponse](data=data)


@router.get(
    "/environments",
    response_model=OkResp[AicmPublishEnvironmentsBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def list_aicm_publish_environments(
    q: str | None = Query(
        default=None,
        max_length=128,
        description="按环境名称、地址、账号、芯片或就绪状态搜索",
    ),
    page: int | None = Query(default=None, ge=0),
    size: int | None = Query(default=None, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = db, current_user, authorization
    data = aicm_publish_service.list_environments_view(
        search=q,
        page=page,
        size=size,
    )
    return OkResp[AicmPublishEnvironmentsBundleResponse](data=data)


@router.get(
    "/runtime-template-candidates",
    response_model=OkRespWithMeta[List[AicmRuntimeTemplateCandidateResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_runtime_template_candidates(
    environment_id: str = Query(..., min_length=1),
    q: str = Query("", max_length=128),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = aicm_publish_service.list_runtime_template_candidates(
        environment_id=environment_id,
        q=q,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[AicmRuntimeTemplateCandidateResponse]](
        meta_data=MetaData(
            total=int(getattr(data, "total", len(data))),
            page=page,
            size=len(data),
        ),
        data=data,
    )


@router.post(
    "/environments",
    response_model=OkResp[AicmPublishEnvironmentResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_aicm_publish_environment(
    cmd: CreateAicmPublishEnvironmentCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = aicm_publish_service.create_environment(cmd)
    return OkResp[AicmPublishEnvironmentResponse](data=data)


@router.patch(
    "/environments/{environment_id}",
    response_model=OkResp[AicmPublishEnvironmentResponse],
    status_code=status.HTTP_200_OK,
)
async def update_aicm_publish_environment(
    environment_id: str,
    cmd: UpdateAicmPublishEnvironmentCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = aicm_publish_service.update_environment(environment_id, cmd)
    return OkResp[AicmPublishEnvironmentResponse](data=data)


@router.delete(
    "/environments/{environment_id}",
    response_model=OkResp[AicmPublishEnvironmentsBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def delete_aicm_publish_environment(
    environment_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = aicm_publish_service.delete_environment(environment_id)
    return OkResp[AicmPublishEnvironmentsBundleResponse](data=data)


@router.put(
    "/environments/active",
    response_model=OkResp[AicmPublishEnvironmentsBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def set_active_aicm_publish_environment(
    cmd: SetActiveAicmPublishEnvironmentCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = aicm_publish_service.set_active_environment(cmd)
    return OkResp[AicmPublishEnvironmentsBundleResponse](data=data)


@router.patch(
    "/config",
    response_model=OkResp[AicmPublishConfigResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_aicm_publish_config(
    cmd: PatchAicmPublishConfigCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = aicm_publish_service.patch_config(cmd)
    return OkResp[AicmPublishConfigResponse](data=data)


@router.get(
    "/publishable",
    response_model=OkRespWithMeta[List[AicmPublishableRowResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_aicm_publishable_models(
    dataset_category_id: Optional[int] = Query(
        None, gt=0, description="独立数据集分类 ID"
    ),
    rect_preset_slug: Optional[str] = Query(None, description="拉框预设 slug"),
    q: Optional[str] = Query(None, max_length=128, description="任务、标签或发布记录关键词"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data, total = aicm_publish_service.list_publishable(
        db,
        current_user,
        dataset_category_id=dataset_category_id,
        rect_preset_slug=rect_preset_slug,
        search=q,
        page=page,
        size=size,
    )
    meta_data = MetaData(total=total, page=page, size=len(data))
    return OkRespWithMeta[List[AicmPublishableRowResponse]](
        meta_data=meta_data, data=data
    )


@router.get(
    "/publish-jobs",
    response_model=OkRespWithMeta[List[AicmPublishHistoryRowResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_aicm_publish_jobs(
    dataset_category_id: Optional[int] = Query(
        None, gt=0, description="独立数据集分类 ID"
    ),
    rect_preset_slug: Optional[str] = Query(None, description="拉框预设 slug"),
    q: Optional[str] = Query(None, max_length=128, description="任务、标签或发布记录关键词"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data, total = aicm_publish_service.list_publish_history(
        db,
        current_user,
        dataset_category_id=dataset_category_id,
        rect_preset_slug=rect_preset_slug,
        search=q,
        page=page,
        size=size,
    )
    meta_data = MetaData(total=total, page=page, size=len(data))
    return OkRespWithMeta[List[AicmPublishHistoryRowResponse]](
        meta_data=meta_data, data=data
    )


@router.post(
    "/publish",
    response_model=OkResp[AicmPublishJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def publish_model_to_aicm(
    cmd: AicmPublishRequestCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await aicm_publish_service.start_publish_job(
        db,
        current_user,
        export_id=cmd.export_id,
        environment_id=cmd.environment_id,
        keep_template_cache=cmd.keep_template_cache,
        bmodel_precision=cmd.bmodel_precision,
        bmodel_rel_path=cmd.bmodel_rel_path,
        publish_mode="manual",
    )
    return OkResp[AicmPublishJobResponse](data=data)


@router.get(
    "/publish-jobs/{job_id}",
    response_model=OkResp[AicmPublishJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_aicm_publish_job(
    job_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = aicm_publish_service.get_publish_job(
        db, current_user, job_id=job_id
    )
    return OkResp[AicmPublishJobResponse](data=data)
