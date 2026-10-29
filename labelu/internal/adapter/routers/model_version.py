from typing import List

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from labelu.internal.application.command.model_version import (
    CreateModelArtifactCommand,
    CreateModelQualityEvidenceCommand,
    CreateModelVersionCommand,
    ImportExternalModelCommand,
    PublishModelVersionCommand,
    RegisterTrainingJobModelCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.model_version import (
    ModelFamilyResponse,
    ModelVersionResponse,
)
from labelu.internal.application.service import model_version as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/model-versions", tags=["model_versions"])
compat_router = APIRouter(
    prefix="/models",
    tags=["model_versions"],
    deprecated=False,
)


@router.post(
    "", response_model=OkResp[ModelVersionResponse], status_code=status.HTTP_201_CREATED
)
async def create(
    cmd: CreateModelVersionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ModelVersionResponse](
        data=await service.create(db, cmd, current_user)
    )


@router.get("", response_model=OkRespWithMeta[List[ModelVersionResponse]])
async def list_all(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=50, ge=1, le=500),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按任务名、模型显示名或模型键搜索",
    ),
    status_filter: str | None = Query(default=None, alias="status"),
    source: str | None = Query(default=None, pattern="^(training|external)$"),
    training_job_id: int | None = Query(default=None, ge=1),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = await service.list_by(
        db,
        page,
        size,
        status_filter,
        current_user,
        source,
        training_job_id,
        search,
    )
    return OkRespWithMeta[List[ModelVersionResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@router.post(
    "/import-external",
    response_model=OkResp[ModelVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def import_external(
    cmd: ImportExternalModelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Register an uploaded external weight as a governed model version."""
    return OkResp[ModelVersionResponse](
        data=await service.import_external(db, cmd, current_user)
    )


@router.post(
    "/from-training-job",
    response_model=OkResp[ModelVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def from_training_job(
    cmd: RegisterTrainingJobModelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Register a completed platform training run in one governed operation."""
    return OkResp[ModelVersionResponse](
        data=await service.register_from_training_job(db, cmd, current_user)
    )


@router.post(
    "/import",
    response_model=OkResp[ModelVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def import_model(
    cmd: ImportExternalModelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Canonical short alias for external model registration."""
    return OkResp[ModelVersionResponse](
        data=await service.import_external(db, cmd, current_user)
    )


@router.get("/{model_version_id}", response_model=OkResp[ModelVersionResponse])
async def get(
    model_version_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ModelVersionResponse](
        data=await service.get(db, model_version_id, current_user)
    )


@router.get(
    "/{model_version_id}/artifacts/{artifact_type}/download",
    response_class=FileResponse,
)
async def download_artifact(
    model_version_id: int,
    artifact_type: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    path, filename = service.resolve_artifact_download(
        db, model_version_id, artifact_type, current_user
    )
    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/octet-stream",
    )


@router.post(
    "/{model_version_id}/artifacts",
    response_model=OkResp[ModelVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_artifact(
    model_version_id: int,
    cmd: CreateModelArtifactCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ModelVersionResponse](
        data=await service.add_artifact(db, model_version_id, cmd, current_user)
    )


@router.post(
    "/{model_version_id}/quality-evidence",
    response_model=OkResp[ModelVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_evidence(
    model_version_id: int,
    cmd: CreateModelQualityEvidenceCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ModelVersionResponse](
        data=await service.add_evidence(db, model_version_id, cmd, current_user)
    )


@router.post(
    "/{model_version_id}/publish",
    response_model=OkResp[ModelVersionResponse],
    include_in_schema=False,
)
async def publish(
    model_version_id: int,
    cmd: PublishModelVersionCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ModelVersionResponse](
        data=await service.publish(
            db, model_version_id, cmd or PublishModelVersionCommand(), current_user
        )
    )


# `/models` is the 7GAI model-repository resource name used by the new UI.
# Keep `/model-versions` above as an explicit API name for integrations.
@compat_router.get("", response_model=OkRespWithMeta[List[ModelVersionResponse]])
async def list_models(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=50, ge=1, le=500),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description="按任务名、模型显示名或模型键搜索",
    ),
    status_filter: str | None = Query(default=None, alias="status"),
    source: str | None = Query(default=None, pattern="^(training|external)$"),
    training_job_id: int | None = Query(default=None, ge=1),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = await service.list_by(
        db, page, size, status_filter, current_user, source, training_job_id, search
    )
    return OkRespWithMeta[List[ModelVersionResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@compat_router.get("/families", response_model=OkRespWithMeta[List[ModelFamilyResponse]])
async def list_model_families(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(
        default=None,
        alias="q",
        max_length=128,
        description=(
            "按模型系列键、任务/算法、模型版本、训练作业或权重信息搜索"
        ),
    ),
    artifact_type: str | None = Query(default=None, pattern="^(pt|bmodel)$"),
    model_id: int | None = Query(default=None, ge=1),
    training_job_id: int | None = Query(default=None, ge=1),
    export_id: int | None = Query(default=None, ge=1),
    task_id: int | None = Query(
        default=None,
        ge=1,
        description="按训练任务限制模型系列，供新建迭代训练选择兼容来源",
    ),
    training_recipe_key: str | None = Query(default=None, max_length=128),
    training_recipe_version: int | None = Query(default=None, ge=1),
    label_ids: list[int] | None = Query(
        default=None,
        min_length=1,
        description="按标签子集计算训练契约并筛选可迭代模型",
    ),
    label_set_signature: str | None = Query(default=None, max_length=128),
    class_order_signature: str | None = Query(default=None, max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = await service.list_families(
        db,
        page,
        size,
        status_filter,
        current_user,
        search,
        artifact_type,
        model_id,
        training_job_id,
        export_id,
        task_id,
        training_recipe_key,
        training_recipe_version,
        label_ids,
        label_set_signature,
        class_order_signature,
    )
    return OkRespWithMeta[List[ModelFamilyResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)), data=data
    )


@compat_router.post(
    "/import-external",
    response_model=OkResp[ModelVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def import_external_model(
    cmd: ImportExternalModelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ModelVersionResponse](
        data=await service.import_external(db, cmd, current_user)
    )


@compat_router.post(
    "/import",
    response_model=OkResp[ModelVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def import_model_compat(
    cmd: ImportExternalModelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ModelVersionResponse](
        data=await service.import_external(db, cmd, current_user)
    )


@compat_router.post(
    "/from-training-job",
    response_model=OkResp[ModelVersionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def from_training_job_compat(
    cmd: RegisterTrainingJobModelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ModelVersionResponse](
        data=await service.register_from_training_job(db, cmd, current_user)
    )


@compat_router.get("/{model_version_id}", response_model=OkResp[ModelVersionResponse])
async def get_model(
    model_version_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[ModelVersionResponse](
        data=await service.get(db, model_version_id, current_user)
    )
