from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Security, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_tagtool_classification_model
from labelu.internal.application.command.tagtool_preannotate import (
    BatchTagtoolPreannotateCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.tagtool_preannotate import (
    TagtoolClassificationModelResponse,
    TagtoolPreannotateJobResponse,
    TagtoolPreannotateModelsResponse,
)
from labelu.internal.application.service import tagtool_preannotate as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User


models_router = APIRouter(prefix="/tagtool-classification-models", tags=["tagtool_preannotate"])
tasks_router = APIRouter(prefix="/tasks", tags=["tagtool_preannotate"])


@models_router.get(
    "",
    response_model=OkRespWithMeta[List[TagtoolClassificationModelResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_tagtool_classification_models(
    name: Optional[str] = Query(None, description="显示名称或原始文件名，模糊匹配"),
    category: Optional[str] = Query(None, description="分类，模糊匹配"),
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按模型编号、名称、文件名、分类或标签头搜索",
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
    data, total = service.list_imported_models(
        db,
        current_user,
        name=name,
        category=category,
        search=(search if search is not None else legacy_search),
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[TagtoolClassificationModelResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)),
        data=data,
    )


@models_router.post(
    "",
    response_model=OkResp[TagtoolClassificationModelResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_tagtool_classification_model(
    file: UploadFile = File(..., description="TagTool ResNet18 权重文件，.pt 或 .pth"),
    display_name: Optional[str] = Form(None, description="列表展示名称，默认同文件名"),
    category: Optional[str] = Form(None, description="分类"),
    label_map_json: Optional[str] = Form(None, description="可选 label_map.json 文本，用于与 checkpoint 校验"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await service.create_imported_model(
        db,
        current_user,
        file=file,
        display_name=display_name,
        category=category,
        label_map_json=label_map_json,
    )
    return OkResp[TagtoolClassificationModelResponse](data=data)


@models_router.get(
    "/{model_id}/download",
    status_code=status.HTTP_200_OK,
)
async def download_tagtool_classification_model(
    model_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    path, filename = service.get_model_download(db, current_user, model_id)
    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/octet-stream",
    )


@models_router.delete(
    "/{model_id}",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def delete_tagtool_classification_model(
    model_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    service.delete_imported_model(db, current_user, model_id)
    return OkResp[dict](data={"deleted": True})


@tasks_router.get(
    "/{task_id}/tagtool_preannotate/models",
    response_model=OkResp[TagtoolPreannotateModelsResponse],
    status_code=status.HTTP_200_OK,
)
async def list_tagtool_preannotate_models(
    task_id: int,
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按模型名称、分类、文件名或标签头搜索",
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
    data = service.list_models_for_task(
        db=db,
        task_id=task_id,
        current_user=current_user,
        search=(search if search is not None else legacy_search),
        page=page,
        size=size,
    )
    return OkResp[TagtoolPreannotateModelsResponse](data=data)


@tasks_router.get(
    "/{task_id}/tagtool-preannotate-jobs",
    response_model=OkRespWithMeta[List[TagtoolPreannotateJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_tagtool_preannotate_jobs(
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
    data, total = service.list_jobs_for_task(
        db=db,
        task_id=task_id,
        current_user=current_user,
        search=(search if search is not None else legacy_search),
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[TagtoolPreannotateJobResponse]](
        data=data,
        meta_data=MetaData(total=total, page=page, size=len(data)),
    )


@tasks_router.post(
    "/{task_id}/tagtool_preannotate_job",
    response_model=OkResp[TagtoolPreannotateJobResponse],
    status_code=status.HTTP_200_OK,
)
async def create_tagtool_preannotate_job(
    task_id: int,
    cmd: BatchTagtoolPreannotateCommand,
    sample_id: Optional[int] = Query(default=None, ge=1),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    if sample_id is not None:
        merged = list(cmd.sample_ids) if cmd.sample_ids else []
        if sample_id not in merged:
            merged.append(sample_id)
        cmd = cmd.model_copy(update={"sample_ids": merged})
    data = await service.create_batch_job(
        db=db, task_id=task_id, cmd=cmd, current_user=current_user
    )
    return OkResp[TagtoolPreannotateJobResponse](data=data)


@tasks_router.get(
    "/{task_id}/tagtool_preannotate_job/active",
    response_model=OkResp[Optional[TagtoolPreannotateJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_active_tagtool_preannotate_job(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = service.get_active_job(db=db, task_id=task_id, current_user=current_user)
    return OkResp[Optional[TagtoolPreannotateJobResponse]](data=data)


@tasks_router.get(
    "/{task_id}/tagtool_preannotate_job/{job_id}",
    response_model=OkResp[TagtoolPreannotateJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_tagtool_preannotate_job_status(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = service.get_job(db=db, task_id=task_id, job_id=job_id, current_user=current_user)
    return OkResp[TagtoolPreannotateJobResponse](data=data)
