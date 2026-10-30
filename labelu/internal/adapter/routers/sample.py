from typing import Any, List, Optional, Union
from pathlib import Path

from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, Query, status, Security
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool

from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.common.error_code import ErrorCode
from labelu.internal.common.error_code import LabelUException
from labelu.internal.domain.models.user import User
from labelu.internal.domain.models.sample import SampleState
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.application.service import sample as service
from labelu.internal.application.service import auto_label as auto_label_service
from labelu.internal.application.service import locateanything_preannotate as locateanything_preannotate_service
from labelu.internal.application.service import vlx_seek_preannotate as vlx_seek_preannotate_service
from labelu.internal.application.service import yolo_preannotate as yolo_preannotate_service
from labelu.internal.application.command.auto_label import AutoLabelCommand, BatchAutoLabelCommand
from labelu.internal.application.command.locateanything_preannotate import BatchLocateAnythingPreannotateCommand
from labelu.internal.application.command.yolo_preannotate import BatchYoloPreannotateCommand
from labelu.internal.application.command.sample import ExportType
from labelu.internal.application.command.sample import PatchSampleCommand
from labelu.internal.application.command.sample import CreateSampleCommand
from labelu.internal.application.command.sample import CopySampleToTaskCommand
from labelu.internal.application.command.sample import CopySamplesByLabelCommand
from labelu.internal.application.command.sample import DeleteSampleCommand
from labelu.internal.application.command.sample import ExportSampleCommand
from labelu.internal.application.command.datasource import ImportS3SamplesCommand
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.base import MetaData
from labelu.internal.application.response.base import CommonDataResp
from labelu.internal.application.response.base import OkRespWithMeta
from labelu.internal.application.response.sample import SampleResponse
from labelu.internal.application.response.sample import CreateSampleResponse
from labelu.internal.application.response.sample import SampleBoundsCheckJobResponse
from labelu.internal.application.response.auto_label import AutoLabelResponse, AutoLabelJobResponse
from labelu.internal.application.response.locateanything_preannotate import LocateAnythingPreannotateJobResponse
from labelu.internal.application.response.yolo_preannotate import (
    YoloPreannotateJobResponse,
    YoloPreannotateModelsResponse,
)
from labelu.internal.application.response.export import ExportJobResponse
from labelu.internal.adapter.persistence import crud_export_job
from labelu.internal.common.storage import get_storage_backend


router = APIRouter(prefix="/tasks", tags=["samples"])


def _export_progress(sample_count: int, processed_count: int, status: str) -> tuple[int | None, str | None]:
    if status not in {"COMPLETED", "FAILED"}:
        return None, None
    skipped_count = max(sample_count - processed_count, 0)
    if skipped_count == 0:
        return 0, None
    return skipped_count, (
        f"Requested {sample_count} samples, exported {processed_count}, skipped {skipped_count}."
    )


@router.post(
    "/{task_id}/samples",
    response_model=OkResp[CreateSampleResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create(
    task_id: int,
    cmd: List[CreateSampleCommand],
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a sample.
    """

    # business logic
    data = await service.create(
        db=db, task_id=task_id, cmd=cmd, current_user=current_user
    )

    # response
    return OkResp[CreateSampleResponse](data=data)


@router.post(
    "/{task_id}/samples/copy-from-sample",
    response_model=OkResp[CreateSampleResponse],
    status_code=status.HTTP_201_CREATED,
)
async def copy_from_sample(
    task_id: int,
    cmd: CopySampleToTaskCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await service.copy_from_sample(
        db=db,
        task_id=task_id,
        cmd=cmd,
        current_user=current_user,
    )
    return OkResp[CreateSampleResponse](data=data)


@router.post(
    "/{task_id}/samples/copy-by-label",
    response_model=OkResp[ExportJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def copy_by_label(
    task_id: int,
    cmd: CopySamplesByLabelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    label_ids = cmd.normalized_label_ids
    if not label_ids:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message="至少选择一个标签",
        )
    job_id = await service.create_label_dataset_import_job(
        db=db,
        task_id=task_id,
        label_ids=label_ids,
        current_user=current_user,
    )
    job = crud_export_job.get(db=db, job_id=job_id)
    return OkResp[ExportJobResponse](data=ExportJobResponse(
        id=job_id,
        task_id=task_id,
        export_type=service.LABEL_DATASET_IMPORT_TYPE,
        status="PENDING",
        sample_count=int(job.sample_count or 0),
        processed_count=0,
    ))


@router.get(
    "/{task_id}/samples/copy-by-label/active",
    response_model=OkResp[Optional[ExportJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_active_copy_by_label_status(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    job = service.get_active_label_dataset_import_job(
        db=db, task_id=task_id, current_user=current_user
    )
    if not job:
        return OkResp[Optional[ExportJobResponse]](data=None)
    return OkResp[Optional[ExportJobResponse]](data=ExportJobResponse(
        id=job.id,
        task_id=job.task_id,
        export_type=job.export_type,
        status=job.status,
        sample_count=job.sample_count,
        processed_count=job.processed_count,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    ))


@router.get(
    "/{task_id}/samples/copy-by-label/{job_id}",
    response_model=OkResp[ExportJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_copy_by_label_status(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    job = service.get_label_dataset_import_job(
        db=db, task_id=task_id, job_id=job_id, current_user=current_user
    )
    skipped_count, warning_message = _export_progress(
        sample_count=job.sample_count,
        processed_count=job.processed_count,
        status=job.status,
    )
    return OkResp[ExportJobResponse](data=ExportJobResponse(
        id=job.id,
        task_id=job.task_id,
        export_type=job.export_type,
        status=job.status,
        sample_count=job.sample_count,
        processed_count=job.processed_count,
        skipped_count=skipped_count,
        warning_message=warning_message,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    ))


@router.get(
    "/{task_id}/samples",
    response_model=OkRespWithMeta[List[SampleResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_by(
    task_id: int,
    after: Union[int, None] = Query(default=None, gt=0),
    before: Union[int, None] = Query(default=None, gt=0),
    page: Union[int, None] = Query(default=None, ge=0),
    size: Union[int, None] = 100,
    state: Union[SampleState, None] = Query(default=None),
    q: Union[str, None] = Query(default=None, max_length=128),
    sort: Union[str, None] = Query(
        default=None, pattern="(annotated_count|state|inner_id|updated_at):(desc|asc)"
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a annotation result.
    """

    # 任务编辑页 /tasks/:id/edit?isNew=true 等场景下，前端 loader 可能只带 size、未传 page/after/before，
    # 原先会 422；React Router 的 errorElement 统一为「404」页，易被误判为真 404。
    effective_after = after
    effective_before = before
    effective_page = page
    if effective_page is None and effective_after is None and effective_before is None:
        effective_page = 0

    if len([i for i in (effective_after, effective_before, effective_page) if i is not None]) != 1:
        raise LabelUException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCode.CODE_55000_SAMPLE_LIST_PARAMETERS_ERROR,
        )

    # business logic
    data, total = await run_in_threadpool(
        service.list_by,
        db=db,
        task_id=task_id,
        after=effective_after,
        before=effective_before,
        page=effective_page,
        size=size,
        sorting=sort,
        state=state.value if state else None,
        search=q,
    )

    # response
    meta_data = MetaData(total=total, page=effective_page, size=len(data))
    return OkRespWithMeta[List[SampleResponse]](meta_data=meta_data, data=data)


@router.post(
    "/{task_id}/samples/bounds_check",
    response_model=OkResp[SampleBoundsCheckJobResponse],
    status_code=status.HTTP_200_OK,
)
async def create_bounds_check_job(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await service.create_bounds_check_job(
        db=db,
        task_id=task_id,
        current_user=current_user,
    )
    return OkResp[SampleBoundsCheckJobResponse](data=data)


@router.get(
    "/{task_id}/samples/bounds_check/active",
    response_model=OkResp[Optional[SampleBoundsCheckJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_active_bounds_check_job(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = service.get_active_bounds_check_job(
        db=db,
        task_id=task_id,
        current_user=current_user,
    )
    return OkResp[Optional[SampleBoundsCheckJobResponse]](data=data)


@router.get(
    "/{task_id}/samples/bounds_check/{job_id}",
    response_model=OkResp[SampleBoundsCheckJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_bounds_check_job_status(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = service.get_bounds_check_job(
        db=db,
        task_id=task_id,
        job_id=job_id,
        current_user=current_user,
    )
    return OkResp[SampleBoundsCheckJobResponse](data=data)


@router.get(
    "/{task_id}/samples/{sample_id}",
    response_model=OkResp[SampleResponse],
    status_code=status.HTTP_200_OK,
)
async def get(
    task_id: int,
    sample_id: int,
    compare_access_token: Optional[str] = Query(default=None),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get a annotation result.
    """

    # business logic
    data = await run_in_threadpool(
        service.get,
        db=db,
        task_id=task_id,
        sample_id=sample_id,
        current_user=current_user,
        compare_access_token=compare_access_token,
    )

    # response
    return OkResp[SampleResponse](data=data)


@router.patch(
    "/{task_id}/samples/{sample_id}",
    response_model=OkResp[SampleResponse],
    status_code=status.HTTP_200_OK,
)
async def update(
    task_id: int,
    sample_id: int,
    cmd: PatchSampleCommand,
    compare_access_token: Optional[str] = Query(default=None),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    update a annotation.
    """

    # business logic
    data = await service.patch(
        db=db,
        task_id=task_id,
        sample_id=sample_id,
        cmd=cmd,
        current_user=current_user,
        compare_access_token=compare_access_token,
    )

    # response
    return OkResp[SampleResponse](data=data)


@router.post(
    "/{task_id}/samples/{sample_id}/auto_label",
    response_model=OkResp[AutoLabelResponse],
    status_code=status.HTTP_200_OK,
)
async def auto_label(
    task_id: int,
    sample_id: int,
    cmd: AutoLabelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await auto_label_service.create(
        db=db,
        task_id=task_id,
        sample_id=sample_id,
        cmd=cmd,
        current_user=current_user,
    )
    return OkResp[AutoLabelResponse](data=data)


@router.get(
    "/{task_id}/samples/{sample_id}/ai_process",
    response_model=OkResp[dict[str, Any]],
    status_code=status.HTTP_200_OK,
)
def get_ai_process(
    task_id: int,
    sample_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the latest AI detection prompt/response trace for one sample."""

    data = auto_label_service.get_latest_ai_process(
        db=db,
        task_id=task_id,
        sample_id=sample_id,
        current_user=current_user,
    )
    return OkResp[dict[str, Any]](data=data)


@router.post(
    "/{task_id}/auto_label_job",
    response_model=OkResp[AutoLabelJobResponse],
    status_code=status.HTTP_200_OK,
)
async def create_auto_label_job(
    task_id: int,
    cmd: BatchAutoLabelCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await auto_label_service.create_batch_job(
        db=db,
        task_id=task_id,
        cmd=cmd,
        current_user=current_user,
    )
    return OkResp[AutoLabelJobResponse](data=data)


@router.get(
    "/{task_id}/auto_label_jobs/active",
    response_model=OkResp[Optional[AutoLabelJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_active_auto_label_job(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = auto_label_service.get_active_batch_job(
        db=db, task_id=task_id, current_user=current_user
    )
    return OkResp[Optional[AutoLabelJobResponse]](data=data)


@router.get(
    "/{task_id}/auto_label_job/{job_id}",
    response_model=OkResp[AutoLabelJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_auto_label_job_status(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = auto_label_service.get_batch_job(
        db=db,
        task_id=task_id,
        job_id=job_id,
        current_user=current_user,
    )
    return OkResp[AutoLabelJobResponse](data=data)


@router.post(
    "/{task_id}/locateanything_preannotate_job",
    response_model=OkResp[LocateAnythingPreannotateJobResponse],
    status_code=status.HTTP_200_OK,
)
async def create_locateanything_preannotate_job(
    task_id: int,
    cmd: BatchLocateAnythingPreannotateCommand,
    sample_id: Optional[int] = Query(
        default=None,
        ge=1,
        description="仅处理该样本时传入查询参数，与请求体中的 sample_ids / sample_id 合并（单图入口推荐）",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    if sample_id is not None:
        merged = list(cmd.sample_ids) if cmd.sample_ids else []
        if sample_id not in merged:
            merged.append(sample_id)
        cmd = cmd.model_copy(update={"sample_ids": merged})
    data = await locateanything_preannotate_service.create_batch_job(
        db=db,
        task_id=task_id,
        cmd=cmd,
        current_user=current_user,
    )
    return OkResp[LocateAnythingPreannotateJobResponse](data=data)


@router.get(
    "/{task_id}/locateanything_preannotate_job/active",
    response_model=OkResp[Optional[LocateAnythingPreannotateJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_active_locateanything_preannotate_job(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = locateanything_preannotate_service.get_active_job(
        db=db,
        task_id=task_id,
        current_user=current_user,
    )
    return OkResp[Optional[LocateAnythingPreannotateJobResponse]](data=data)


@router.get(
    "/{task_id}/locateanything_preannotate_job/{job_id}",
    response_model=OkResp[LocateAnythingPreannotateJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_locateanything_preannotate_job_status(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = locateanything_preannotate_service.get_job(
        db=db,
        task_id=task_id,
        job_id=job_id,
        current_user=current_user,
    )
    return OkResp[LocateAnythingPreannotateJobResponse](data=data)


@router.post(
    "/{task_id}/vlx_seek_preannotate_job",
    response_model=OkResp[LocateAnythingPreannotateJobResponse],
    status_code=status.HTTP_200_OK,
)
async def create_vlx_seek_preannotate_job(
    task_id: int,
    cmd: BatchLocateAnythingPreannotateCommand,
    sample_id: Optional[int] = Query(default=None, ge=1),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    if sample_id is not None:
        cmd = cmd.model_copy(update={"sample_ids": [sample_id], "sample_id": sample_id})
    data = await vlx_seek_preannotate_service.create_batch_job(db=db, task_id=task_id, cmd=cmd, current_user=current_user)
    return OkResp[LocateAnythingPreannotateJobResponse](data=data)


@router.get(
    "/{task_id}/vlx_seek_preannotate_job/active",
    response_model=OkResp[Optional[LocateAnythingPreannotateJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_active_vlx_seek_preannotate_job(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = vlx_seek_preannotate_service.get_active_job(db=db, task_id=task_id, current_user=current_user)
    return OkResp[Optional[LocateAnythingPreannotateJobResponse]](data=data)


@router.get(
    "/{task_id}/vlx_seek_preannotate_job/{job_id}",
    response_model=OkResp[LocateAnythingPreannotateJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_vlx_seek_preannotate_job_status(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = vlx_seek_preannotate_service.get_job(db=db, task_id=task_id, job_id=job_id, current_user=current_user)
    return OkResp[LocateAnythingPreannotateJobResponse](data=data)


@router.post(
    "/{task_id}/rex_omni_preannotate_job",
    response_model=OkResp[LocateAnythingPreannotateJobResponse],
    status_code=status.HTTP_200_OK,
)
async def create_rex_omni_preannotate_job(
    task_id: int,
    cmd: BatchLocateAnythingPreannotateCommand,
    sample_id: Optional[int] = Query(default=None, ge=1),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    if sample_id is not None:
        cmd = cmd.model_copy(update={"sample_ids": [sample_id], "sample_id": sample_id})
    data = await vlx_seek_preannotate_service.create_batch_job(
        db=db, task_id=task_id, cmd=cmd, current_user=current_user, provider="rex"
    )
    return OkResp[LocateAnythingPreannotateJobResponse](data=data)


@router.get(
    "/{task_id}/rex_omni_preannotate_job/active",
    response_model=OkResp[Optional[LocateAnythingPreannotateJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_active_rex_omni_preannotate_job(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = vlx_seek_preannotate_service.get_active_job(
        db=db, task_id=task_id, current_user=current_user, provider="rex"
    )
    return OkResp[Optional[LocateAnythingPreannotateJobResponse]](data=data)


@router.get(
    "/{task_id}/rex_omni_preannotate_job/{job_id}",
    response_model=OkResp[LocateAnythingPreannotateJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_rex_omni_preannotate_job_status(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = vlx_seek_preannotate_service.get_job(
        db=db, task_id=task_id, job_id=job_id, current_user=current_user, provider="rex"
    )
    return OkResp[LocateAnythingPreannotateJobResponse](data=data)


@router.post(
    "/{task_id}/auto_label_job/{job_id}/cancel",
    response_model=OkResp[AutoLabelJobResponse],
    status_code=status.HTTP_200_OK,
)
async def cancel_auto_label_job(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = auto_label_service.cancel_batch_job(
        db=db,
        task_id=task_id,
        job_id=job_id,
        current_user=current_user,
    )
    return OkResp[AutoLabelJobResponse](data=data)


@router.get(
    "/{task_id}/yolo_preannotate/models",
    response_model=OkResp[YoloPreannotateModelsResponse],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_preannotate_models(
    task_id: int,
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按模型名称、分类、作业、任务或权重搜索",
    ),
    legacy_search: Optional[str] = Query(
        None, alias="search", max_length=128, include_in_schema=False
    ),
    page: int = Query(0, ge=0),
    size: int = Query(100, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = yolo_preannotate_service.list_models_for_task(
        db=db,
        task_id=task_id,
        current_user=current_user,
        search=(search if search is not None else legacy_search),
        page=page,
        size=size,
    )
    return OkResp[YoloPreannotateModelsResponse](data=data)


@router.get(
    "/{task_id}/yolo-preannotate-jobs",
    response_model=OkRespWithMeta[List[YoloPreannotateJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_preannotate_jobs(
    task_id: int,
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按作业编号、模型、状态或错误信息搜索",
    ),
    legacy_search: Optional[str] = Query(
        None, alias="search", max_length=128, include_in_schema=False
    ),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = yolo_preannotate_service.list_jobs_for_task(
        db=db,
        task_id=task_id,
        current_user=current_user,
        search=(search if search is not None else legacy_search),
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[YoloPreannotateJobResponse]](
        data=data,
        meta_data=MetaData(total=total, page=page, size=len(data)),
    )


@router.post(
    "/{task_id}/yolo_preannotate_job",
    response_model=OkResp[YoloPreannotateJobResponse],
    status_code=status.HTTP_200_OK,
)
async def create_yolo_preannotate_job(
    task_id: int,
    cmd: BatchYoloPreannotateCommand,
    sample_id: Optional[int] = Query(
        default=None,
        ge=1,
        description="仅处理该样本时传入查询参数，与请求体中的 sample_ids / sample_id 合并（单图入口推荐）",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    if sample_id is not None:
        # 单图入口会同时把 sample_id 放在 query 和 body 中。SPA 页面快速切换时，
        # 旧弹窗/旧 body 可能滞后；以当前请求 URL 的 query sample_id 为准。
        cmd = cmd.model_copy(update={"sample_ids": [sample_id], "sample_id": sample_id})
    data = await yolo_preannotate_service.create_batch_job(
        db=db, task_id=task_id, cmd=cmd, current_user=current_user
    )
    return OkResp[YoloPreannotateJobResponse](data=data)


@router.get(
    "/{task_id}/yolo_preannotate_job/active",
    response_model=OkResp[Optional[YoloPreannotateJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_active_yolo_preannotate_job(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = yolo_preannotate_service.get_active_job(
        db=db, task_id=task_id, current_user=current_user
    )
    return OkResp[Optional[YoloPreannotateJobResponse]](data=data)


@router.get(
    "/{task_id}/yolo_preannotate_job/{job_id}",
    response_model=OkResp[YoloPreannotateJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_preannotate_job_status(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = yolo_preannotate_service.get_job(
        db=db, task_id=task_id, job_id=job_id, current_user=current_user
    )
    return OkResp[YoloPreannotateJobResponse](data=data)


@router.post(
    "/{task_id}/samples/import_s3",
    response_model=OkResp[CreateSampleResponse],
    status_code=status.HTTP_201_CREATED,
)
async def import_s3(
    task_id: int,
    cmd: ImportS3SamplesCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await service.import_from_s3(
        db=db, task_id=task_id, cmd=cmd, current_user=current_user,
    )
    return OkResp[CreateSampleResponse](data=data)


@router.delete(
    "/{task_id}/samples",
    response_model=OkResp[CommonDataResp],
    status_code=status.HTTP_200_OK,
)
async def delete(
    task_id: int,
    cmd: DeleteSampleCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    delete a annotation.
    """

    # business logic
    data = await service.delete(
        db=db, task_id=task_id, sample_ids=cmd.sample_ids, current_user=current_user
    )

    # response
    return OkResp[CommonDataResp](data=data)


@router.post(
    "/{task_id}/samples/export",
    response_model=OkResp[ExportJobResponse],
    status_code=status.HTTP_200_OK,
)
async def export(
    task_id: int,
    export_type: ExportType,
    cmd: ExportSampleCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create an async export job.
    """

    job_id = await service.create_export_job(
        db=db,
        task_id=task_id,
        export_type=export_type,
        sample_ids=cmd.sample_ids,
        current_user=current_user,
    )

    return OkResp[ExportJobResponse](data=ExportJobResponse(
        id=job_id,
        task_id=task_id,
        export_type=export_type.value,
        status="PENDING",
        sample_count=len(cmd.sample_ids),
        processed_count=0,
        skipped_count=None,
        warning_message=None,
    ))


@router.get(
    "/{task_id}/samples/export/{job_id}",
    response_model=OkResp[ExportJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_export_status(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get export job status.
    """

    job = crud_export_job.get(db=db, job_id=job_id)
    if not job:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    skipped_count, warning_message = _export_progress(
        sample_count=job.sample_count,
        processed_count=job.processed_count,
        status=job.status,
    )

    return OkResp[ExportJobResponse](data=ExportJobResponse(
        id=job.id,
        task_id=job.task_id,
        export_type=job.export_type,
        status=job.status,
        sample_count=job.sample_count,
        processed_count=job.processed_count,
        skipped_count=skipped_count,
        warning_message=warning_message,
        file_path=job.file_path,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    ))


@router.get(
    "/{task_id}/samples/export/{job_id}/download",
    status_code=status.HTTP_200_OK,
)
async def download_export(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Download completed export file.
    """

    job = crud_export_job.get(db=db, job_id=job_id)
    if not job or job.status != "COMPLETED":
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    storage = get_storage_backend()
    if storage.is_remote:
        download_url = storage.get_read_url(job.file_path)
        return RedirectResponse(url=download_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    file_path = Path(job.file_path)
    media_type = ".json" if file_path.suffix == ".json" else file_path.suffix.strip(".")
    return FileResponse(
        path=file_path, filename=file_path.name, media_type=f"application/{media_type}"
    )
