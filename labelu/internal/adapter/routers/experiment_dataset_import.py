from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.orm import Session

from labelu.internal.application.command.experiment_dataset_import import (
    CreateExperimentDatasetImportCommand,
)
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.base import OkRespWithMeta, MetaData
from labelu.internal.application.response.experiment_dataset_import import (
    ExperimentDatasetImportAcceptedResponse,
    ExperimentDatasetCreatedTaskResponse,
    ExperimentDatasetImportDetailResponse,
    ExperimentDatasetImportListItemResponse,
)
from labelu.internal.application.service import experiment_dataset_import as service
from labelu.internal.common import db as db_module
from labelu.internal.common.internal_service_auth import (
    verify_strategy_evolution_internal_request,
)
from labelu.internal.common.third_party_auth import verify_third_party_request


router = APIRouter(
    prefix="/internal/v1/experiment-datasets",
    tags=["experiment_dataset_import"],
)


@router.post(
    "",
    response_model=OkResp[ExperimentDatasetImportAcceptedResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_experiment_dataset_import(
    cmd: CreateExperimentDatasetImportCommand,
    request: Request,
    db: Session = Depends(db_module.get_db),
):
    await verify_third_party_request(
        request,
        required=str(cmd.schema_version).lower().startswith("v2"),
        db=db,
        source_system=cmd.source_system,
    )
    data = await service.accept(db=db, cmd=cmd)
    return OkResp[ExperimentDatasetImportAcceptedResponse](data=data)


@router.get("", response_model=OkResp[list[ExperimentDatasetImportListItemResponse]])
async def list_experiment_dataset_imports(
    package_id: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    model_id: str | None = Query(default=None, alias="modelId"),
    legacy_model_id: str | None = Query(default=None, alias="model_id"),
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    data = await service.list_imports(
        db=db,
        package_id=package_id,
        status=status_value,
        model_id=model_id or legacy_model_id,
    )
    return OkResp[list[ExperimentDatasetImportListItemResponse]](data=data)


@router.get(
    "/created-tasks",
    response_model=OkRespWithMeta[list[ExperimentDatasetCreatedTaskResponse]],
)
async def list_created_experiment_tasks(
    page: int = Query(default=0, ge=0),
    size: int = Query(default=100, ge=1, le=500),
    status_value: str | None = Query(default=None, alias="status"),
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    data, total = await service.list_created_tasks(
        db=db,
        page=page,
        size=size,
        status=status_value,
    )
    return OkRespWithMeta[list[ExperimentDatasetCreatedTaskResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)),
        data=data,
    )


@router.get("/{package_id}", response_model=OkResp[ExperimentDatasetImportDetailResponse | None])
async def get_experiment_dataset_import(
    package_id: str,
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    data = await service.get_import_detail(db=db, package_id=package_id)
    return OkResp[ExperimentDatasetImportDetailResponse | None](data=data)
