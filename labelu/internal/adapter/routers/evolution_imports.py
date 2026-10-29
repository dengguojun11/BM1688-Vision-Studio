from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from labelu.internal.application.command.experiment_dataset_import import (
    RetryExperimentDatasetImportCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.experiment_dataset_import import (
    ExperimentDatasetImportAcceptedResponse,
    ExperimentDatasetImportListItemResponse,
)
from labelu.internal.application.service import experiment_dataset_import as service
from labelu.internal.common import db as db_module
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/api/v1/evolution/imports", tags=["evolution"])


@router.get(
    "",
    response_model=OkRespWithMeta[list[ExperimentDatasetImportListItemResponse]],
)
async def list_evolution_imports(
    status: str | None = Query(default=None),
    model_id: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=128),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=500),
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_current_user),
):
    """进化中心只读查看第三方回流数据包及累计状态。"""
    rows, total = await service.list_imports_page(
        db=db,
        package_id=None,
        status=status,
        model_id=model_id,
        search=q,
        page=page,
        size=size,
    )
    return OkRespWithMeta[list[ExperimentDatasetImportListItemResponse]](
        data=rows,
        meta_data=MetaData(total=total, page=page, size=len(rows)),
    )


@router.post(
    "/{import_job_id}/retry",
    response_model=OkResp[ExperimentDatasetImportAcceptedResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_evolution_import(
    import_job_id: int,
    cmd: RetryExperimentDatasetImportCommand | None = None,
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(require_system_admin),
):
    """管理员以新 package_id 重新执行失败的专家回流任务。"""
    data = await service.retry_import(
        db=db,
        import_job_id=import_job_id,
        reason=cmd.reason if cmd else "",
    )
    return OkResp[ExperimentDatasetImportAcceptedResponse](data=data)


@router.delete(
    "/{import_job_id}",
    response_model=OkResp[dict],
)
async def archive_evolution_import(
    import_job_id: int,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    """管理员归档未创建任务的失败记录（软删除，不复用 package_id）。"""
    data = service.archive_import(
        db=db,
        import_job_id=import_job_id,
        actor_id=int(current_user.id),
    )
    return OkResp[dict](data=data)
