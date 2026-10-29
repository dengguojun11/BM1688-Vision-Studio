from typing import List, Optional
from datetime import date

from sqlalchemy.orm import Session
from fastapi import APIRouter, Header, Query, status, Depends, Security, Response
from fastapi.security import HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool

from labelu.internal.application.response.user import UserResponse
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.domain.models.user import User
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.application.service import task as service
from labelu.internal.application.service import annotation_taxonomy as taxonomy_service
from labelu.internal.application.command.task import (
    AssignTaskProjectCommand,
    BatchAssignTaskProjectCommand,
    BasicConfigCommand,
    BatchAssignDatasetCategoryCommand,
    CollaboratorBatchCommand,
    CollaboratorUpdateCommand,
)
from labelu.internal.application.command.task import UpdateCommand
from labelu.internal.application.command.annotation_taxonomy import (
    ApplyGlobalTagsCommand,
    ApplyLabelPresetCommand,
    ApplyTaskLabelsCommand,
)
from labelu.internal.application.response.annotation_taxonomy import (
    TaskAnnotationContractResponse,
)
from labelu.internal.application.service import annotation_contract as contract_service
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.base import MetaData
from labelu.internal.application.response.base import CommonDataResp
from labelu.internal.application.response.base import OkRespWithMeta
from labelu.internal.application.response.task import TaskRectPresetSummaryResponse
from labelu.internal.application.response.task import TaskImageSearchHit
from labelu.internal.application.response.task import TaskLabelImageHit
from labelu.internal.application.response.task import TaskResponse
from labelu.internal.application.response.task import TaskResponseWithStatics
from labelu.internal.application.response.task import TaskDatasetCategoryBatchResponse
from labelu.internal.application.response.task import TaskDatasetCategorySummaryResponse
from labelu.internal.application.response.task import TaskProjectAssignmentResponse
from labelu.internal.application.response.dataset_analysis import (
    DatasetAnalysisResponse,
)
from labelu.internal.application.response.annotation_stats import (
    AnnotationStatsDashboardResponse,
)
from labelu.internal.application.service import (
    annotation_stats as annotation_stats_service,
)
from labelu.internal.application.service import (
    dataset_analysis as dataset_analysis_service,
)
from labelu.internal.application.service import (
    preannotation_capabilities as preannotation_capabilities_service,
)
from labelu.internal.application.response.preannotation_capabilities import (
    PreannotationCapabilitiesResponse,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post(
    "",
    response_model=OkResp[TaskResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create(
    cmd: BasicConfigCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Create a task with basic config.
    """

    # business logic
    data = await service.create(
        db=db,
        cmd=cmd,
        current_user=current_user,
        idempotency_key=idempotency_key,
    )

    # response
    return OkResp[TaskResponse](data=data)


@router.get(
    "",
    response_model=OkRespWithMeta[List[TaskResponseWithStatics]],
    status_code=status.HTTP_200_OK,
)
async def list_by(
    page: int = 0,
    size: int = 100,
    status: Optional[str] = Query(
        None, description="任务状态筛选，如 FINISHED / DRAFT / IMPORTED"
    ),
    media_type: Optional[str] = Query(
        None, description="媒体类型筛选，如 IMAGE / VIDEO / AUDIO"
    ),
    rect_preset_slug: Optional[str] = Query(
        None,
        description="拉框预设筛选；__uncategorized__ 表示未应用预设的任务",
    ),
    label_ids: Optional[List[int]] = Query(
        None,
        description="全局标签 ID；重复参数表示任务当前契约必须覆盖全部所选标签",
    ),
    training_ready: Optional[bool] = Query(
        None,
        description="训练就绪筛选：true=可训练，false=未达标",
    ),
    dataset_category_id: Optional[int] = Query(None, gt=0),
    uncategorized: bool = Query(False),
    project_id: Optional[int] = Query(None, gt=0),
    dataset_id: Optional[int] = Query(None, gt=0),
    dataset_label_contract_id: Optional[int] = Query(None, gt=0),
    dataset_search: Optional[str] = Query(None, max_length=128),
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按任务、任务 ID、数据集、标签契约、来源或状态搜索",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    List task.
    """

    # business logic
    data, total = await run_in_threadpool(
        service.list_by,
        db=db,
        current_user=current_user,
        page=page,
        size=size,
        status=status,
        media_type=media_type,
        rect_preset_slug=rect_preset_slug,
        label_ids=label_ids,
        training_ready=training_ready,
        dataset_category_id=dataset_category_id,
        uncategorized=uncategorized,
        project_id=project_id,
        dataset_id=dataset_id,
        dataset_label_contract_id=dataset_label_contract_id,
        dataset_search=dataset_search,
        search=search,
    )

    # response
    meta_data = MetaData(total=total, page=page, size=len(data))
    return OkRespWithMeta[List[TaskResponseWithStatics]](meta_data=meta_data, data=data)


@router.post(
    "/project-assignment",
    response_model=OkResp[TaskProjectAssignmentResponse],
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def batch_assign_project(
    cmd: BatchAssignTaskProjectCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Assign one or more legacy tasks to a managed 7GAI project."""
    data = await service.batch_assign_project(db, cmd, current_user)
    return OkResp[TaskProjectAssignmentResponse](data=data)


@router.patch(
    "/{task_id}/project",
    response_model=OkResp[TaskProjectAssignmentResponse],
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
async def assign_project(
    task_id: int,
    cmd: AssignTaskProjectCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Assign one legacy task to a managed 7GAI project."""
    data = await service.assign_project(db, task_id, cmd, current_user)
    return OkResp[TaskProjectAssignmentResponse](data=data)


@router.get("/label-summary", response_model=OkResp[dict])
async def task_label_summary(
    dataset_category_id: Optional[int] = Query(
        None, gt=0, description="Independent dataset category ID"
    ),
    dataset_id: Optional[int] = Query(None, gt=0, description="Logical dataset ID"),
    uncategorized: bool = Query(False, description="Only uncategorized tasks"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[dict](
        data=service.label_summary(
            db,
            current_user,
            dataset_category_id=dataset_category_id,
            dataset_id=dataset_id,
            uncategorized=uncategorized,
        )
    )


@router.get(
    "/dataset-category-summary",
    response_model=OkResp[TaskDatasetCategorySummaryResponse],
)
async def task_dataset_category_summary(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[TaskDatasetCategorySummaryResponse](
        data=service.dataset_category_summary(db, current_user)
    )


@router.get(
    "/dataset-analysis",
    response_model=OkResp[DatasetAnalysisResponse],
)
async def dataset_analysis(
    dataset_id: Optional[int] = Query(None, gt=0, description="数据集 ID"),
    task_ids: Optional[List[int]] = Query(
        None,
        description="可选的任务 ID；指定后只统计这些任务，必须属于所选数据集",
    ),
    dataset_category_id: Optional[int] = Query(
        None, gt=0, description="历史数据集分类 ID，仅兼容旧客户端"
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[DatasetAnalysisResponse](
        data=dataset_analysis_service.build_dataset_analysis(
            db,
            current_user,
            dataset_category_id=dataset_category_id,
            dataset_id=dataset_id,
            task_ids=task_ids,
        )
    )


@router.patch(
    "/batch-dataset-category",
    response_model=OkResp[TaskDatasetCategoryBatchResponse],
)
async def batch_assign_task_dataset_category(
    cmd: BatchAssignDatasetCategoryCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[TaskDatasetCategoryBatchResponse](
        data=service.assign_dataset_category(
            db,
            cmd=cmd,
            current_user=current_user,
        )
    )


@router.get(
    "/rect-preset-summary",
    response_model=OkResp[TaskRectPresetSummaryResponse],
    status_code=status.HTTP_200_OK,
)
async def rect_preset_summary(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    当前用户可见任务按 annotation-settings 拉框预设分组的数量。
    """
    data = await service.rect_preset_summary(db=db, current_user=current_user)
    return OkResp[TaskRectPresetSummaryResponse](data=data)


@router.get(
    "/image-search",
    response_model=OkRespWithMeta[List[TaskImageSearchHit]],
    status_code=status.HTTP_200_OK,
)
async def image_search(
    q: str = Query(
        ..., min_length=1, max_length=200, description="图片文件名关键词（模糊匹配）"
    ),
    page: int = Query(0, ge=0),
    size: int = Query(24, ge=1, le=100),
    rect_preset_slug: Optional[str] = Query(
        None,
        description="与任务列表侧边栏一致；空=全部任务，有值=仅该分类",
    ),
    label_ids: Optional[List[int]] = Query(
        None,
        description="全局标签 ID；图片搜索范围与任务标签筛选保持一致",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    按文件名模糊搜索图片样本；搜索范围与当前任务列表分类筛选一致。
    """
    data, total = await service.search_images(
        db=db,
        current_user=current_user,
        keyword=q,
        page=page,
        size=size,
        rect_preset_slug=rect_preset_slug,
        label_ids=label_ids,
    )
    meta_data = MetaData(total=total, page=page, size=len(data))
    return OkRespWithMeta[List[TaskImageSearchHit]](meta_data=meta_data, data=data)


@router.get(
    "/label-images",
    response_model=OkRespWithMeta[List[TaskLabelImageHit]],
    status_code=status.HTTP_200_OK,
)
async def label_images(
    label_id: int = Query(..., gt=0),
    page: int = Query(0, ge=0),
    size: int = Query(24, ge=1, le=100),
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按图片文件名、任务名称或编号搜索",
    ),
    dataset_category_id: Optional[int] = Query(None, gt=0),
    dataset_id: Optional[int] = Query(None, gt=0),
    uncategorized: bool = Query(False),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data, total, label = service.label_images(
        db=db,
        current_user=current_user,
        label_id=label_id,
        page=page,
        size=size,
        search=search,
        dataset_category_id=dataset_category_id,
        dataset_id=dataset_id,
        uncategorized=uncategorized,
    )
    meta_data = MetaData(total=total, page=page, size=len(data))
    return OkRespWithMeta[List[TaskLabelImageHit]](
        meta_data=meta_data,
        data=data,
    )


@router.get(
    "/annotation-stats/dashboard",
    response_model=OkResp[AnnotationStatsDashboardResponse],
    status_code=status.HTTP_200_OK,
)
async def annotation_stats_dashboard(
    date_from: Optional[date] = Query(
        None, description="统计起始日期（含），默认近 30 天"
    ),
    date_to: Optional[date] = Query(None, description="统计结束日期（含），默认今天"),
    task_id: Optional[int] = Query(None, description="限定单个任务，默认全部可见任务"),
    sample_page: int = Query(0, ge=0, description="单张用时明细页码"),
    sample_size: int = Query(50, ge=1, le=200, description="单张用时明细每页条数"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    标注统计大屏：每人每日真实修改图片张数、单张图片真实修改记录（估算用时）、汇总指标。
    """
    data = annotation_stats_service.get_annotation_stats_dashboard(
        db=db,
        current_user=current_user,
        date_from=date_from,
        date_to=date_to,
        task_id=task_id,
        sample_page=sample_page,
        sample_size=sample_size,
    )
    return OkResp[AnnotationStatsDashboardResponse](data=data)


@router.post(
    "/{task_id}/annotation/apply-label-preset",
    response_model=OkResp[TaskResponse],
    status_code=status.HTTP_200_OK,
)
async def apply_label_preset(
    task_id: int,
    cmd: ApplyLabelPresetCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    将数据库中的拉框预设写入任务 config（覆盖 rectTool.attributes），仅支持 IMAGE 任务。
    """
    data = await taxonomy_service.apply_label_preset_to_task(
        db=db,
        task_id=task_id,
        preset_id=cmd.preset_id,
        current_user=current_user,
    )
    return OkResp[TaskResponse](data=data)


@router.post(
    "/{task_id}/annotation/apply-labels",
    response_model=OkResp[TaskResponse],
    status_code=status.HTTP_200_OK,
)
async def apply_task_labels(
    task_id: int,
    cmd: ApplyTaskLabelsCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await taxonomy_service.apply_labels_to_task(
        db,
        task_id,
        cmd.label_ids,
        current_user,
        expected_contract_revision=cmd.expected_contract_revision,
        config=cmd.config,
        tool_name=cmd.tool_name,
        scope_mode=cmd.scope_mode,
    )
    return OkResp[TaskResponse](data=data)


@router.get(
    "/{task_id}/annotation-contract",
    response_model=OkResp[TaskAnnotationContractResponse],
    status_code=status.HTTP_200_OK,
)
async def get_task_annotation_contract(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    task = await service.get(db=db, task_id=task_id, current_user=current_user)
    _ = task
    payload = contract_service.contract_payload(
        contract_service.current_contract(db, task_id)
    )
    if payload is None:
        from labelu.internal.common.error_code import ErrorCode, LabelUException

        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="任务尚未建立标签契约",
        )
    return OkResp[TaskAnnotationContractResponse](
        data=TaskAnnotationContractResponse(**payload)
    )


@router.get(
    "/{task_id}/annotation-contracts",
    response_model=OkResp[List[TaskAnnotationContractResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_task_annotation_contracts(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    await service.get(db=db, task_id=task_id, current_user=current_user)
    data = [
        TaskAnnotationContractResponse(**item)
        for item in contract_service.contract_history_payloads(db, task_id)
    ]
    return OkResp[List[TaskAnnotationContractResponse]](data=data)


@router.post(
    "/{task_id}/annotation-contracts",
    response_model=OkResp[TaskAnnotationContractResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_task_annotation_contract(
    task_id: int,
    cmd: ApplyTaskLabelsCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    await taxonomy_service.apply_labels_to_task(
        db,
        task_id,
        cmd.label_ids,
        current_user,
        expected_contract_revision=cmd.expected_contract_revision,
        config=cmd.config,
        tool_name=cmd.tool_name,
        scope_mode=cmd.scope_mode,
    )
    payload = contract_service.contract_payload(
        contract_service.current_contract(db, task_id)
    )
    return OkResp[TaskAnnotationContractResponse](
        data=TaskAnnotationContractResponse(**payload)
    )


@router.post(
    "/{task_id}/annotation/apply-global-tags",
    response_model=OkResp[TaskResponse],
    status_code=status.HTTP_200_OK,
)
async def apply_global_tags(
    task_id: int,
    cmd: ApplyGlobalTagsCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    将数据库中的全局属性维度合并进任务 config（tagTool + tagList）。
    """
    data = await taxonomy_service.apply_global_tags_to_task(
        db=db,
        task_id=task_id,
        current_user=current_user,
        dimension_ids=cmd.dimension_ids,
    )
    return OkResp[TaskResponse](data=data)


@router.get(
    "/{task_id}/preannotation-capabilities",
    response_model=OkResp[PreannotationCapabilitiesResponse],
    status_code=status.HTTP_200_OK,
)
async def get_preannotation_capabilities(
    task_id: int,
    sample_id: Optional[int] = Query(default=None, ge=1),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Return the task-contract and model-access gated pre-annotation menu."""
    data = await run_in_threadpool(
        preannotation_capabilities_service.get_task_capabilities,
        db,
        task_id,
        current_user,
        sample_id,
    )
    return OkResp[PreannotationCapabilitiesResponse](data=data)


@router.get(
    "/{task_id}",
    response_model=OkResp[TaskResponseWithStatics],
    status_code=status.HTTP_200_OK,
)
async def get(
    task_id: int,
    response: Response,
    sample_id: Optional[int] = Query(default=None, ge=1),
    compare_access_token: Optional[str] = Query(default=None),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get task detail.
    """

    # business logic
    if compare_access_token and sample_id:
        data = await service.get_with_compare_sample_access(
            db=db,
            task_id=task_id,
            sample_id=sample_id,
            compare_access_token=compare_access_token,
            current_user=current_user,
        )
    else:
        data = await service.get(db=db, task_id=task_id, current_user=current_user)

    # 避免浏览器 / React Query 使用缓存的旧 config（应用预设后界面不刷新标签）
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"

    # response
    return OkResp[TaskResponseWithStatics](data=data)


@router.get(
    "/{task_id}/collaborators",
    response_model=OkResp[List[UserResponse]],
    status_code=status.HTTP_200_OK,
)
async def get_collaborators(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get task detail.
    """

    # business logic
    data = await service.get_collaborators(
        db=db, task_id=task_id, current_user=current_user
    )

    # response
    return OkResp[List[UserResponse]](data=data)


@router.post(
    "/{task_id}/collaborators",
    response_model=OkResp[UserResponse],
    status_code=status.HTTP_201_CREATED,
)
async def add_collaborator(
    task_id: int,
    cmd: CollaboratorUpdateCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add collaborator to task.
    """

    # business logic
    data = await service.add_collaborator(
        db=db, task_id=task_id, user_id=cmd.user_id, current_user=current_user
    )

    # response
    return OkResp[UserResponse](data=data)


@router.post(
    "/{task_id}/collaborators/batch_add",
    response_model=OkResp[List[UserResponse]],
    status_code=status.HTTP_201_CREATED,
)
async def batch_add_collaborator_request(
    task_id: int,
    cmd: CollaboratorBatchCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add collaborators to task.
    """

    # business logic
    data = await service.batch_add_collaborators(
        db=db, task_id=task_id, user_ids=cmd.user_ids, current_user=current_user
    )

    # response
    return OkResp[List[UserResponse]](data=data)


@router.post(
    "/{task_id}/collaborators/batch_remove",
    response_model=OkResp[CommonDataResp],
    status_code=status.HTTP_201_CREATED,
)
async def batch_remove_collaborator_request(
    task_id: int,
    cmd: CollaboratorBatchCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Add collaborators to task.
    """

    # business logic
    data = await service.batch_remove_collaborators(
        db=db, task_id=task_id, user_ids=cmd.user_ids, current_user=current_user
    )

    # response
    return OkResp[CommonDataResp](data=data)


@router.delete(
    "/{task_id}/collaborators/{user_id}",
    response_model=OkResp[CommonDataResp],
    status_code=status.HTTP_200_OK,
)
async def remove_collaborator(
    task_id: int,
    user_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Remove collaborator from task.
    """

    # business logic
    data = await service.remove_collaborator(
        db=db, task_id=task_id, user_id=user_id, current_user=current_user
    )

    # response
    return OkResp[CommonDataResp](data=data)


@router.patch(
    "/{task_id}",
    response_model=OkResp[TaskResponse],
    status_code=status.HTTP_200_OK,
)
async def update(
    task_id: int,
    cmd: UpdateCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Update task info, inlucde annotation config, name, description and tips.
    """

    # business logic
    data = await service.update(
        db=db, task_id=task_id, cmd=cmd, current_user=current_user
    )

    # response
    return OkResp[TaskResponse](data=data)


@router.delete(
    "/{task_id}",
    response_model=OkResp[CommonDataResp],
    status_code=status.HTTP_200_OK,
)
async def delete(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Delete task.
    """

    # business logic
    data = await service.delete(db=db, task_id=task_id, current_user=current_user)

    # response
    return OkResp[CommonDataResp](data=data)
