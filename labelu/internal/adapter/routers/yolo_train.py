from datetime import datetime
import json
from typing import List, Literal, Optional, Union

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Security,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from labelu.internal.application.command.yolo_eval import YoloEvalStartCommand
from labelu.internal.application.command.bm1688_compiler import (
    PatchBm1688CompilerConfigCommand,
)
from labelu.internal.application.command.edge_device_registry import (
    CreateEdgeDeviceCommand,
    PatchEdgeDeviceCommand,
    SetActiveEdgeDeviceCommand,
)
from labelu.internal.application.command.edge_agent_bootstrap import (
    BootstrapEdgeAgentCommand,
)
from labelu.internal.application.command.edge_ssh_config import (
    PatchEdgeSshConfigCommand,
)
from labelu.internal.application.command.remote_train_nodes import (
    PatchRemoteTrainNodeCommand,
    PutRemoteTrainNodesCommand,
)
from labelu.internal.application.command.training_task import (
    CheckTrainingTaskNameCommand,
    CreateLabelDimensionTrainingTaskCommand,
    PreviewLabelDimensionTrainingTaskCommand,
)
from labelu.internal.application.command.yolo_train import (
    CreateYoloTrainJobCommand,
    PatchYoloTrainRuntimeHyperparamsCommand,
)
from labelu.internal.application.command.yolo_edge_validation import (
    CreateYoloEdgeValidationJobCommand,
    RecommendEdgeThresholdsCommand,
    RefineEdgeThresholdsCommand,
)
from labelu.internal.application.command.edge_queue_center import (
    EdgeQueueCancelCommand,
    EdgeQueueListQuery,
    EdgeQueueRetryCommand,
)
from labelu.internal.application.command.yolo_quality_policy import (
    ActivateYoloQualityPolicyCommand,
    CreateOrUpdateYoloQualityPolicyCommand,
    RecommendYoloQualityPolicyCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.task import TaskTrainingCandidateResponse
from labelu.internal.application.response.training_task import (
    LabelDimensionTrainingPreviewResponse,
    TrainingTaskCreateResponse,
    TrainingTaskDetailResponse,
    TrainingTaskListRowResponse,
    TrainingTaskNameCheckResponse,
)
from labelu.internal.application.response.yolo_edge_validation import (
    EdgeThresholdRefinementResponse,
    EdgeThresholdRecommendationResponse,
    YoloEdgeValidationJobDetailResponse,
    YoloEdgeValidationJobRowResponse,
)
from labelu.internal.application.response.edge_queue_center import (
    EdgeQueueJobListResponse,
    EdgeQueueMutationResponse,
    EdgeQueueSummaryResponse,
)
from labelu.internal.application.response.yolo_quality_policy import (
    YoloQualityPoliciesBundleResponse,
    YoloQualityPolicyRecommendationResponse,
)
from labelu.internal.application.response.yolo_eval import (
    YoloEvalAcceptedResponse,
    YoloEvalHubResponse,
    YoloEvalReportListRowResponse,
    YoloEvalReportResponse,
)
from labelu.internal.application.response.bm1688_compiler import (
    Bm1688CompilerConfigResponse,
)
from labelu.internal.application.response.edge_device_registry import (
    EdgeDeviceListResponse,
    EdgeDeviceResponse,
)
from labelu.internal.application.response.edge_agent_bootstrap import (
    EdgeAgentBootstrapResponse,
)
from labelu.internal.application.response.edge_ssh_config import (
    EdgeSshConfigResponse,
    EdgeSshProbeResponse,
)
from labelu.internal.application.response.remote_train_nodes import (
    RemoteTrainNodeResponse,
    RemoteTrainNodesListResponse,
    RemoteTrainNodeTestResponse,
)
from labelu.internal.application.response.board_agent import (
    BoardValidationStatusResponse,
)
from labelu.internal.application.response.yolo_train import (
    YoloBm1688ExportCatalogMeta,
    YoloBm1688ExportCatalogSummary,
    YoloBm1688ExportDetailResponse,
    YoloBm1688ExportFamilyResponse,
    YoloBm1688ExportListResponse,
    YoloBm1688ExportRowResponse,
    YoloHyperParamFieldItem,
    YoloImportedModelRowResponse,
    YoloModelValidateImageResponse,
    YoloTrainJobListRowResponse,
    YoloTrainJobResponse,
    YoloTrainedModelCatalogMeta,
    YoloTrainedModelListResponse,
)
from labelu.internal.application.service import (
    bm1688_compiler_config as bm1688_compiler_cfg_service,
)
from labelu.internal.application.service import (
    edge_device_registry as edge_device_registry_service,
)
from labelu.internal.application.service import edge_ssh_config as edge_ssh_cfg_service
from labelu.internal.application.service import (
    remote_train_nodes as remote_train_nodes_service,
)
from labelu.internal.application.service import task as task_service
from labelu.internal.application.service import training_task as training_task_service
from labelu.internal.application.service import (
    yolo_bm1688_export as yolo_bm1688_export_service,
)
from labelu.internal.application.service import (
    yolo_edge_threshold_recommendation as yolo_edge_threshold_recommendation_service,
)
from labelu.internal.application.service import (
    yolo_edge_threshold_refinement as yolo_edge_threshold_refinement_service,
)
from labelu.internal.application.service import (
    yolo_edge_validation as yolo_edge_validation_service,
)
from labelu.internal.application.service import (
    yolo_quality_policy as yolo_quality_policy_service,
)
from labelu.internal.application.service import (
    yolo_quality_policy_recommendation as yolo_quality_policy_recommendation_service,
)
from labelu.internal.application.service import yolo_train as yolo_train_service
from labelu.internal.application.service.algorithm import ensure_seeded
from labelu.internal.application.service import (
    yolo_edge_visual_evidence as yolo_edge_visual_evidence_service,
)
from labelu.internal.application.service import (
    edge_queue_center as edge_queue_center_service,
)
from labelu.internal.application.service import (
    edge_agent_bootstrap as edge_agent_bootstrap_service,
)
from labelu.internal.application.service import (
    yolo_train_analysis as yolo_train_analysis_service,
)
from labelu.internal.application.service import (
    yolo_train_gt_pred_compare as yolo_train_gt_pred_compare_service,
)
from labelu.internal.application.service import (
    yolo_train_consistent_pool as yolo_train_consistent_pool_service,
)
from labelu.internal.application.service import yolo_train_evaluation
from labelu.internal.adapter.persistence import crud_task, crud_yolo_train_job
from labelu.internal.common import db as db_module
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.security import security
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/tasks", tags=["yolo_train"])

global_jobs_router = APIRouter(prefix="/yolo-train-jobs", tags=["yolo_train"])

eval_reports_router = APIRouter(prefix="/yolo-eval-reports", tags=["yolo_train"])

trained_models_router = APIRouter(prefix="/yolo-trained-models", tags=["yolo_train"])

imported_models_router = APIRouter(prefix="/yolo-imported-models", tags=["yolo_train"])

train_meta_router = APIRouter(prefix="/yolo-train", tags=["yolo_train"])
training_tasks_router = APIRouter(prefix="/yolo-training-tasks", tags=["yolo_train"])

bm1688_exports_router = APIRouter(prefix="/yolo-bm1688-exports", tags=["yolo_train"])
edge_devices_router = APIRouter(prefix="/edge-devices", tags=["yolo_train"])
yolo_edge_validation_router = APIRouter(
    prefix="/yolo-edge-validation", tags=["yolo_train"]
)
quality_policy_router = APIRouter(prefix="/yolo-quality-policies", tags=["yolo_train"])


def _ensure_training_catalog_seeded(db: Session) -> None:
    """Seed built-in training metadata for real request sessions only.

    A few router-level callers intentionally inject a lightweight database
    double while testing delegation/thread-pool behavior.  The production
    dependency always supplies a SQLAlchemy ``Session``; keeping the guard at
    this adapter boundary preserves that behavior without making catalog
    seeding part of the service contract for arbitrary test doubles.
    """

    if isinstance(db, Session):
        ensure_seeded(db)


@training_tasks_router.post(
    "/name-check",
    response_model=OkResp[TrainingTaskNameCheckResponse],
    status_code=status.HTTP_200_OK,
)
async def check_training_task_name(
    cmd: CheckTrainingTaskNameCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    normalized_name = cmd.name.strip()
    available = training_task_service.check_training_task_name_available(
        db,
        current_user.id,
        normalized_name,
    )
    reason = None if available else "该任务名称已存在，请重新修改后创建"
    return OkResp[TrainingTaskNameCheckResponse](
        data=TrainingTaskNameCheckResponse(
            available=available,
            normalized_name=normalized_name,
            reason=reason,
        )
    )


@training_tasks_router.post(
    "/preview",
    response_model=OkResp[LabelDimensionTrainingPreviewResponse],
    status_code=status.HTTP_200_OK,
)
async def preview_training_task(
    cmd: PreviewLabelDimensionTrainingTaskCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    _ensure_training_catalog_seeded(db)
    data = training_task_service.preview_label_dimension_training_task(
        db,
        current_user=current_user,
        cmd=cmd,
    )
    return OkResp[LabelDimensionTrainingPreviewResponse](data=data)


@training_tasks_router.post(
    "",
    response_model=OkResp[TrainingTaskCreateResponse],
    status_code=status.HTTP_200_OK,
)
async def create_training_task(
    cmd: CreateLabelDimensionTrainingTaskCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    _ensure_training_catalog_seeded(db)
    data = await run_in_threadpool(
        training_task_service.create_label_dimension_training_task,
        db,
        current_user=current_user,
        cmd=cmd,
        idempotency_key=idempotency_key,
    )
    return OkResp[TrainingTaskCreateResponse](data=data)


@training_tasks_router.get(
    "",
    response_model=OkRespWithMeta[List[TrainingTaskListRowResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_training_tasks(
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按训练任务、任务编号、作业编号、配方或状态搜索",
    ),
    name: Optional[str] = Query(None, description="训练任务名称，模糊匹配"),
    status_value: Optional[str] = Query(
        None, alias="status", description="训练任务状态"
    ),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data, total = training_task_service.list_training_tasks(
        db,
        current_user=current_user,
        page=page,
        size=size,
        name=name,
        search=search,
        status_value=status_value,
    )
    meta_data = MetaData(total=total, page=page, size=len(data))
    return OkRespWithMeta[List[TrainingTaskListRowResponse]](
        meta_data=meta_data,
        data=data,
    )


@training_tasks_router.get(
    "/{training_task_id}",
    response_model=OkResp[TrainingTaskDetailResponse],
    status_code=status.HTTP_200_OK,
)
async def get_training_task(
    training_task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = training_task_service.get_training_task_detail(
        db,
        current_user=current_user,
        training_task_id=training_task_id,
    )
    return OkResp[TrainingTaskDetailResponse](data=data)


@training_tasks_router.post(
    "/{training_task_id}/iterate",
    response_model=OkResp[TrainingTaskCreateResponse],
    status_code=status.HTTP_200_OK,
)
async def iterate_training_task(
    training_task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = training_task_service.iterate_training_task(
        db,
        current_user=current_user,
        training_task_id=training_task_id,
    )
    return OkResp[TrainingTaskCreateResponse](data=data)


@global_jobs_router.get(
    "",
    response_model=OkRespWithMeta[List[YoloTrainJobListRowResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_train_jobs_global(
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按作业、任务、数据集、权重、状态或执行节点搜索",
    ),
    task_name: Optional[str] = Query(None, description="标注任务名称，模糊匹配"),
    category: Optional[str] = Query(
        None, description="训练分类，模糊匹配（兼容旧数据）"
    ),
    dataset_category_id: Optional[int] = Query(
        None, gt=0, description="独立数据集分类 ID"
    ),
    project_id: Optional[int] = Query(None, gt=0, description="7GAI 项目 ID"),
    rect_preset_slug: Optional[str] = Query(
        None, description="拉框预设 slug，仅列出主任务属于该预设的训练作业"
    ),
    status: Optional[str] = Query(
        None,
        description="作业状态：PENDING / PROCESSING / COMPLETED / FAILED / CANCELLED",
    ),
    created_from: Optional[datetime] = Query(None, description="创建时间起（含）"),
    created_to: Optional[datetime] = Query(None, description="创建时间止（含）"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = yolo_train_service.list_train_jobs_global(
        db,
        current_user,
        search=search,
        task_name=task_name,
        category=category,
        dataset_category_id=dataset_category_id,
        project_id=project_id,
        rect_preset_slug=rect_preset_slug,
        status=status,
        created_from=created_from,
        created_to=created_to,
        page=page,
        size=size,
    )
    meta_data = MetaData(total=total, page=page, size=len(data))
    return OkRespWithMeta[List[YoloTrainJobListRowResponse]](
        meta_data=meta_data, data=data
    )


@global_jobs_router.get(
    "/{job_id}",
    response_model=OkResp[YoloTrainJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_train_job_global(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """统一训练中心详情入口，复用既有项目/任务权限和完整作业聚合响应。"""
    _ = authorization
    return OkResp[YoloTrainJobResponse](
        data=yolo_train_service.get_train_job_by_id(
            db=db, job_id=job_id, current_user=current_user
        )
    )


@global_jobs_router.get(
    "/{job_id}/class-contract",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_train_job_class_contract(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    response = yolo_train_service.get_train_job_by_id(
        db=db, job_id=job_id, current_user=current_user
    )
    row = crud_yolo_train_job.get(db=db, job_id=job_id)
    class_names = (
        json.loads(row.class_names_json or "[]")
        if getattr(row, "class_names_json", None)
        else list(response.task_rect_classes or [])
    )
    source_manifest = (
        json.loads(row.source_sample_manifest_json or "[]")
        if getattr(row, "source_sample_manifest_json", None)
        else []
    )
    return OkResp[dict](
        data={
            "job_id": int(row.id),
            "training_task_id": row.training_task_id,
            "class_names": class_names,
            "label_set_signature": row.label_set_signature,
            "class_order_signature": row.class_order_signature,
            "data_yaml_sha256": row.data_yaml_sha256,
            "dataset_content_signature": row.dataset_content_signature,
            "training_recipe_key": row.training_recipe_key,
            "training_recipe_version": row.training_recipe_version,
            "runtime_profile": row.runtime_profile,
            "runtime_interface_version": row.runtime_interface_version,
            "source_sample_count": len(source_manifest),
            "source": (
                "frozen_contract"
                if getattr(row, "class_names_json", None)
                else "legacy_task_config"
            ),
        }
    )


@global_jobs_router.get(
    "/{job_id}/analysis",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_train_job_analysis(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    job = yolo_train_service.get_train_job_by_id(
        db=db, job_id=job_id, current_user=current_user
    )
    data = yolo_train_analysis_service.build_train_analysis(
        job_id=job_id,
        job_payload=job.model_dump(),
    )
    return OkResp[dict](data=data)


@global_jobs_router.get(
    "/{job_id}/analysis/assets/{asset_id}",
    status_code=status.HTTP_200_OK,
)
async def get_yolo_train_job_analysis_asset(
    job_id: int,
    asset_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    yolo_train_service.get_train_job_by_id(
        db=db, job_id=job_id, current_user=current_user
    )
    path = yolo_train_analysis_service.resolve_analysis_asset_path(job_id, asset_id)
    if path is None:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
            message="训练分析资源不存在",
        )
    return FileResponse(path=str(path.resolve()))


@global_jobs_router.get(
    "/{job_id}/gt-pred-compare",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_train_gt_pred_compare(
    job_id: int,
    split: Optional[str] = Query(None, description="train | val | test"),
    issue: Optional[str] = Query(None, description="all | fn | fp | ok"),
    class_name: Optional[str] = Query(None, description="按 worst_class 筛选"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    yolo_train_service.get_train_job_by_id(
        db=db, job_id=job_id, current_user=current_user
    )
    data = yolo_train_gt_pred_compare_service.list_compare_items(
        job_id,
        split=split,
        issue=issue,
        class_name=class_name,
        page=page,
        size=size,
    )
    return OkResp[dict](data=data)


@global_jobs_router.post(
    "/{job_id}/gt-pred-compare/generate",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def post_yolo_train_gt_pred_compare_generate(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    yolo_train_service.get_train_job_by_id(
        db=db, job_id=job_id, current_user=current_user
    )
    if yolo_train_gt_pred_compare_service.should_block_compare_regenerate(job_id):
        status_row = yolo_train_gt_pred_compare_service.read_compare_status(job_id)
        return OkResp[dict](data=status_row)

    import threading

    threading.Thread(
        target=yolo_train_gt_pred_compare_service.generate_gt_pred_compare,
        kwargs={"job_id": job_id, "force": True},
        name=f"gt-pred-compare-api-{job_id}",
        daemon=True,
    ).start()
    return OkResp[dict](
        data={
            "status": "running",
            "job_id": job_id,
            "message": "已开始生成标注对比，请稍后刷新",
        }
    )


@global_jobs_router.get(
    "/{job_id}/consistent-pool/sync",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_train_consistent_pool_sync(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    yolo_train_service.get_train_job_by_id(
        db=db, job_id=job_id, current_user=current_user
    )
    return OkResp[dict](
        data=yolo_train_consistent_pool_service.read_sync_report(job_id)
    )


@global_jobs_router.post(
    "/{job_id}/consistent-pool/sync",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def post_yolo_train_consistent_pool_sync(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """运维补跑：manifest 已存在时无需重跑标注对比即可同步筛选池。"""
    _ = authorization
    yolo_train_service.get_train_job_by_id(
        db=db, job_id=job_id, current_user=current_user
    )
    import threading

    threading.Thread(
        target=yolo_train_consistent_pool_service.sync_consistent_pool_from_job,
        args=(job_id,),
        name=f"consistent-pool-api-{job_id}",
        daemon=True,
    ).start()
    return OkResp[dict](
        data={
            "status": "running",
            "job_id": job_id,
            "message": "已开始同步训练筛选一致任务，请稍后 GET 查看结果",
        }
    )


@global_jobs_router.get(
    "/{job_id}/gt-pred-compare/asset",
    status_code=status.HTTP_200_OK,
)
async def get_yolo_train_gt_pred_compare_asset(
    job_id: int,
    item_id: str = Query(..., description="如 val/12345_imgstem"),
    side: str = Query(..., description="gt | pred"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    yolo_train_service.get_train_job_by_id(
        db=db, job_id=job_id, current_user=current_user
    )
    path = yolo_train_gt_pred_compare_service.resolve_compare_asset_path(
        job_id, item_id, side
    )
    if path is None:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
            message="对比图不存在，请先生成标注对比",
        )
    return FileResponse(path=str(path.resolve()))


@router.post(
    "/{task_id}/yolo-train-jobs",
    response_model=OkResp[YoloTrainJobResponse],
    status_code=status.HTTP_201_CREATED,
)
async def start_yolo_train(
    task_id: int,
    cmd: CreateYoloTrainJobCommand,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_training_catalog_seeded(db)
    data = await yolo_train_service.create_train_job(
        db=db,
        task_id=task_id,
        cmd=cmd,
        current_user=current_user,
        idempotency_key=idempotency_key,
    )
    return OkResp[YoloTrainJobResponse](data=data)


@router.get(
    "/{task_id}/yolo-train-jobs",
    response_model=OkResp[List[YoloTrainJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_train_jobs(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = yolo_train_service.list_train_jobs(
        db=db, task_id=task_id, current_user=current_user
    )
    return OkResp[List[YoloTrainJobResponse]](data=data)


@router.get(
    "/{task_id}/yolo-train-jobs/{job_id}",
    response_model=OkResp[YoloTrainJobResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_train_job(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = yolo_train_service.get_train_job(
        db=db, task_id=task_id, job_id=job_id, current_user=current_user
    )
    return OkResp[YoloTrainJobResponse](data=data)


@router.post(
    "/{task_id}/yolo-train-jobs/{job_id}/pause",
    response_model=OkResp[YoloTrainJobResponse],
    status_code=status.HTTP_200_OK,
)
async def pause_yolo_train_job(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = yolo_train_service.pause_train_job(
        db=db, task_id=task_id, job_id=job_id, current_user=current_user
    )
    return OkResp[YoloTrainJobResponse](data=data)


@router.post(
    "/{task_id}/yolo-train-jobs/{job_id}/restart",
    response_model=OkResp[YoloTrainJobResponse],
    status_code=status.HTTP_200_OK,
)
async def restart_yolo_train_job(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await yolo_train_service.restart_train_job(
        db=db, task_id=task_id, job_id=job_id, current_user=current_user
    )
    return OkResp[YoloTrainJobResponse](data=data)


@router.patch(
    "/{task_id}/yolo-train-jobs/{job_id}/runtime-hyperparams",
    response_model=OkResp[YoloTrainJobResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_yolo_train_runtime_hyperparams(
    task_id: int,
    job_id: int,
    cmd: PatchYoloTrainRuntimeHyperparamsCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = yolo_train_service.apply_runtime_hyperparams(
        db=db,
        task_id=task_id,
        job_id=job_id,
        current_user=current_user,
        hyperparams=cmd.hyperparams,
    )
    return OkResp[YoloTrainJobResponse](data=data)


@router.delete(
    "/{task_id}/yolo-train-jobs/{job_id}",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def delete_yolo_train_job(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    yolo_train_service.delete_train_job(
        db=db, task_id=task_id, job_id=job_id, current_user=current_user
    )
    return OkResp[dict](data={"deleted": True, "job_id": job_id})


@router.post(
    "/{task_id}/yolo-train-jobs/{job_id}/test-val",
    response_model=OkResp[YoloTrainJobResponse],
    status_code=status.HTTP_200_OK,
)
async def run_yolo_test_val(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await yolo_train_service.run_test_val_for_job(
        db=db, task_id=task_id, job_id=job_id, current_user=current_user
    )
    return OkResp[YoloTrainJobResponse](data=data)


@router.post(
    "/{task_id}/yolo-train-jobs/{job_id}/bm1688-export",
    response_model=OkResp[YoloBm1688ExportRowResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_yolo_bm1688_export(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await yolo_bm1688_export_service.start_export_for_job(
        db, task_id=task_id, job_id=job_id, current_user=current_user
    )
    return OkResp[YoloBm1688ExportRowResponse](data=data)


@router.get(
    "/{task_id}/yolo-train-jobs/{job_id}/download",
    status_code=status.HTTP_200_OK,
)
async def download_yolo_weights(
    task_id: int,
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    job = yolo_train_service.get_train_job(
        db=db, task_id=task_id, job_id=job_id, current_user=current_user
    )
    if job.status != "COMPLETED" or not job.weights_path:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    path = yolo_train_service.resolve_weights_path(job.weights_path)
    return FileResponse(
        path=path,
        filename=f"task-{task_id}-job-{job_id}-best.pt",
        media_type="application/octet-stream",
    )


@router.post(
    "/{task_id}/yolo-train-jobs/{job_id}/validate-image",
    response_model=OkResp[YoloModelValidateImageResponse],
    status_code=status.HTTP_200_OK,
)
async def validate_yolo_train_job_image(
    task_id: int,
    job_id: int,
    file: UploadFile = File(...),
    conf: float = Form(0.25),
    imgsz: int = Form(640),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = yolo_train_service.validate_uploaded_image_for_job(
        db=db,
        task_id=task_id,
        job_id=job_id,
        current_user=current_user,
        upload_file=file,
        conf=conf,
        imgsz=imgsz,
    )
    return OkResp[YoloModelValidateImageResponse](data=data)


@bm1688_exports_router.get(
    "",
    response_model=YoloBm1688ExportListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_yolo_bm1688_exports(
    dataset_category_id: Optional[int] = Query(
        None, gt=0, description="独立数据集分类 ID"
    ),
    rect_preset_slug: Optional[str] = Query(None, description="拉框预设 slug"),
    yolo_train_job_id: Optional[int] = Query(
        None, ge=1, description="按训练作业 ID 精确查询关联导出"
    ),
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按任务名称或编译/训练作业编号搜索",
    ),
    export_id: Optional[int] = Query(None, ge=1, description="按编译任务 ID 精确查询"),
    task_name: Optional[str] = Query(None, description="按任务名称模糊查询"),
    export_status: Optional[
        Literal["PENDING", "PROCESSING", "ONNX_READY", "COMPLETED", "FAILED"]
    ] = Query(None, description="编译导出状态"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    include_summary: bool = Query(True, description="是否同步计算筛选范围内的全量统计"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total, summary = await run_in_threadpool(
        yolo_bm1688_export_service.list_exports_global,
        db,
        current_user,
        dataset_category_id=dataset_category_id,
        rect_preset_slug=rect_preset_slug,
        yolo_train_job_id=yolo_train_job_id,
        export_id=export_id,
        task_name=task_name,
        search=search,
        export_status=export_status,
        page=page,
        size=size,
        include_summary=include_summary,
    )
    meta_data = YoloBm1688ExportCatalogMeta(
        total=total,
        page=page,
        size=len(data),
        summary=summary,
    )
    return YoloBm1688ExportListResponse(meta_data=meta_data, data=data)


@bm1688_exports_router.get(
    "/families",
    response_model=OkRespWithMeta[List[YoloBm1688ExportFamilyResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_bm1688_export_families(
    export_id: Optional[int] = Query(None, ge=1, description="按编译任务 ID 定位系列"),
    yolo_train_job_id: Optional[int] = Query(
        None, ge=1, description="按训练作业 ID 定位系列"
    ),
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按任务名称或编译/训练作业编号搜索系列",
    ),
    task_name: Optional[str] = Query(None, description="按任务名称模糊筛选系列"),
    export_status: Optional[
        Literal["PENDING", "PROCESSING", "ONNX_READY", "COMPLETED", "FAILED"]
    ] = Query(None, description="按包含该状态的编译任务筛选系列"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data, total = await run_in_threadpool(
        yolo_bm1688_export_service.list_export_families_global,
        db,
        current_user,
        export_id=export_id,
        yolo_train_job_id=yolo_train_job_id,
        task_name=task_name,
        search=search,
        export_status=export_status,
        page=page,
        size=size,
    )
    return OkRespWithMeta[List[YoloBm1688ExportFamilyResponse]](
        meta_data=MetaData(total=total, page=page, size=len(data)),
        data=data,
    )


@bm1688_exports_router.get(
    "/catalog-summary",
    response_model=OkResp[YoloBm1688ExportCatalogSummary],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_bm1688_export_catalog_summary(
    dataset_category_id: Optional[int] = Query(None, gt=0),
    rect_preset_slug: Optional[str] = Query(None),
    yolo_train_job_id: Optional[int] = Query(None, ge=1),
    task_name: Optional[str] = Query(None),
    export_status: Optional[
        Literal["PENDING", "PROCESSING", "ONNX_READY", "COMPLETED", "FAILED"]
    ] = Query(None),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    summary = await run_in_threadpool(
        yolo_bm1688_export_service.get_exports_summary_global,
        db,
        current_user,
        dataset_category_id=dataset_category_id,
        rect_preset_slug=rect_preset_slug,
        yolo_train_job_id=yolo_train_job_id,
        task_name=task_name,
        export_status=export_status,
    )
    return OkResp[YoloBm1688ExportCatalogSummary](data=summary)


@yolo_edge_validation_router.post(
    "/recommend-thresholds",
    response_model=OkResp[EdgeThresholdRecommendationResponse],
    status_code=status.HTTP_200_OK,
)
async def recommend_yolo_edge_validation_thresholds(
    cmd: RecommendEdgeThresholdsCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = yolo_edge_threshold_recommendation_service.recommend_thresholds(
        db=db,
        cmd=cmd,
        current_user=current_user,
    )
    return OkResp[EdgeThresholdRecommendationResponse](data=data)


@yolo_edge_validation_router.post(
    "/jobs/{job_id}/refine-thresholds",
    response_model=OkResp[EdgeThresholdRefinementResponse],
    status_code=status.HTTP_200_OK,
)
async def refine_yolo_edge_validation_thresholds(
    job_id: int,
    cmd: RefineEdgeThresholdsCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = yolo_edge_threshold_refinement_service.refine_thresholds(
        db=db,
        job_id=job_id,
        cmd=cmd,
        current_user=current_user,
    )
    return OkResp[EdgeThresholdRefinementResponse](data=data)


@yolo_edge_validation_router.post(
    "/jobs",
    response_model=OkResp[YoloEdgeValidationJobDetailResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_yolo_edge_validation_job(
    cmd: CreateYoloEdgeValidationJobCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = yolo_edge_validation_service.create_job(
        db=db,
        cmd=cmd,
        current_user=current_user,
    )
    data = yolo_bm1688_export_service._redact_export_response_model_for_user(
        data, current_user
    )
    return OkResp[YoloEdgeValidationJobDetailResponse](data=data)


@quality_policy_router.get(
    "",
    response_model=OkResp[YoloQualityPoliciesBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_quality_policies(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, db, current_user
    data = yolo_quality_policy_service.list_quality_policies_bundle()
    return OkResp[YoloQualityPoliciesBundleResponse](data=data)


@quality_policy_router.post(
    "",
    response_model=OkResp[YoloQualityPoliciesBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def upsert_yolo_quality_policy(
    cmd: CreateOrUpdateYoloQualityPolicyCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    data = yolo_quality_policy_service.upsert_quality_policy(cmd, db=db)
    return OkResp[YoloQualityPoliciesBundleResponse](data=data)


@quality_policy_router.post(
    "/activate",
    response_model=OkResp[YoloQualityPoliciesBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def activate_yolo_quality_policy(
    cmd: ActivateYoloQualityPolicyCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, db, current_user
    data = yolo_quality_policy_service.activate_quality_policy(cmd)
    return OkResp[YoloQualityPoliciesBundleResponse](data=data)


@quality_policy_router.post(
    "/recommend",
    response_model=OkResp[YoloQualityPolicyRecommendationResponse],
    status_code=status.HTTP_200_OK,
)
async def recommend_yolo_quality_policy(
    cmd: RecommendYoloQualityPolicyCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = yolo_quality_policy_recommendation_service.recommend_quality_policy(
        db=db,
        cmd=cmd,
        current_user=current_user,
    )
    return OkResp[YoloQualityPolicyRecommendationResponse](data=data)


@yolo_edge_validation_router.get(
    "/jobs",
    response_model=OkRespWithMeta[List[YoloEdgeValidationJobRowResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_edge_validation_jobs(
    export_id: Optional[int] = Query(None),
    task_id: Optional[int] = Query(None),
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按验证作业、任务、边端设备或外部任务标识搜索",
    ),
    status_value: Optional[str] = Query(None, alias="status"),
    source_system: Optional[str] = Query(None, max_length=64),
    pipeline_origin: Optional[str] = Query(None, max_length=64),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data, meta = yolo_edge_validation_service.list_jobs(
        db=db,
        current_user=current_user,
        export_id=export_id,
        task_id=task_id,
        keyword=search,
        status_value=status_value,
        source_system=source_system,
        pipeline_origin=pipeline_origin,
        page=page,
        size=size,
    )
    data = [
        yolo_bm1688_export_service._redact_export_response_model_for_user(
            item, current_user
        )
        for item in data
    ]
    return OkRespWithMeta[List[YoloEdgeValidationJobRowResponse]](
        meta_data=meta,
        data=data,
    )


@yolo_edge_validation_router.get(
    "/queue/summary",
    response_model=OkResp[EdgeQueueSummaryResponse],
    status_code=status.HTTP_200_OK,
)
@yolo_edge_validation_router.get(
    "/jobs/queue-summary",
    response_model=OkResp[EdgeQueueSummaryResponse],
    status_code=status.HTTP_200_OK,
    include_in_schema=False,
)
def get_yolo_edge_validation_queue_summary(
    export_id: Optional[int] = Query(None, gt=0),
    task_id: Optional[int] = Query(None, gt=0),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization
    # Keep the request-scoped SQLAlchemy session on its owning thread.  The
    # queue service performs bounded, synchronous queries and must not receive
    # this session through ``run_in_threadpool`` (Session is not thread-safe).
    data = edge_queue_center_service.get_queue_summary(
        db,
        current_user=current_user,
        export_id=export_id,
        task_id=task_id,
    )
    return OkResp[EdgeQueueSummaryResponse](data=data)


@yolo_edge_validation_router.get(
    "/queue/jobs",
    response_model=OkResp[EdgeQueueJobListResponse],
    status_code=status.HTTP_200_OK,
)
def list_yolo_edge_validation_queue_jobs(
    export_id: Optional[int] = Query(None, gt=0),
    task_id: Optional[int] = Query(None, gt=0),
    q: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按任务、设备或边端任务信息搜索",
    ),
    keyword: Optional[str] = Query(None, max_length=128),
    device_id: Optional[str] = Query(None, max_length=128),
    edge_device_id: Optional[str] = Query(None, max_length=128),
    source_type: Optional[str] = Query(None, max_length=64),
    source_system: Optional[str] = Query(None, max_length=64),
    pipeline_origin: Optional[str] = Query(None, max_length=64),
    status_value: Optional[str] = Query(None, alias="status", max_length=32),
    active_only: bool = Query(False),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization
    effective_keyword = q if q is not None else keyword
    query = EdgeQueueListQuery(
        export_id=export_id,
        task_id=task_id,
        keyword=effective_keyword,
        device_id=device_id or edge_device_id,
        source_type=source_type,
        source_system=source_system,
        pipeline_origin=pipeline_origin,
        status=status_value,
        active_only=active_only,
        page=page,
        size=size,
    )
    data = edge_queue_center_service.list_queue_jobs(
        db,
        current_user=current_user,
        query=query,
    )
    return OkResp[EdgeQueueJobListResponse](data=data)


@yolo_edge_validation_router.post(
    "/jobs/{job_id}/cancel",
    response_model=OkResp[EdgeQueueMutationResponse],
    status_code=status.HTTP_200_OK,
)
def cancel_yolo_edge_validation_queue_job(
    job_id: int,
    cmd: EdgeQueueCancelCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization
    data = edge_queue_center_service.cancel_job(
        db,
        job_id=job_id,
        current_user=current_user,
        reason=cmd.reason if cmd else None,
    )
    return OkResp[EdgeQueueMutationResponse](data=data)


@yolo_edge_validation_router.post(
    "/jobs/{job_id}/retry",
    response_model=OkResp[EdgeQueueMutationResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_yolo_edge_validation_queue_job(
    job_id: int,
    cmd: EdgeQueueRetryCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization
    data = edge_queue_center_service.retry_job(
        db,
        job_id=job_id,
        current_user=current_user,
        reason=cmd.reason if cmd else None,
    )
    return OkResp[EdgeQueueMutationResponse](data=data)


@yolo_edge_validation_router.get(
    "/jobs/{job_id}",
    response_model=OkResp[YoloEdgeValidationJobDetailResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_edge_validation_job(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = yolo_edge_validation_service.get_job(
        db=db,
        job_id=job_id,
        current_user=current_user,
    )
    data = yolo_bm1688_export_service._redact_export_response_model_for_user(
        data, current_user
    )
    return OkResp[YoloEdgeValidationJobDetailResponse](data=data)


@yolo_edge_validation_router.get(
    "/jobs/{job_id}/preview/{filename}",
    status_code=status.HTTP_200_OK,
)
async def get_yolo_edge_validation_job_preview(
    job_id: int,
    filename: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    path = yolo_edge_validation_service.get_job_preview_asset(
        db,
        job_id=job_id,
        filename=filename,
        current_user=current_user,
    )
    return FileResponse(path)


@yolo_edge_validation_router.get(
    "/jobs/{job_id}/visual-evidence",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_edge_validation_visual_evidence(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await run_in_threadpool(
        yolo_edge_visual_evidence_service.get_visual_evidence,
        db,
        job_id=job_id,
        current_user=current_user,
    )
    return OkResp[dict](data=data)


@yolo_edge_validation_router.get(
    "/jobs/{job_id}/visual-evidence/{kind}/{filename}",
    status_code=status.HTTP_200_OK,
)
async def get_yolo_edge_validation_visual_evidence_asset(
    job_id: int,
    kind: str,
    filename: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    path = await run_in_threadpool(
        yolo_edge_visual_evidence_service.get_visual_evidence_asset,
        db,
        job_id=job_id,
        kind=kind,
        filename=filename,
        current_user=current_user,
    )
    return FileResponse(path, media_type="image/jpeg")


@yolo_edge_validation_router.post(
    "/jobs/{job_id}/run",
    response_model=OkResp[YoloEdgeValidationJobDetailResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_yolo_edge_validation_job(
    job_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = yolo_edge_validation_service.start_job(
        db=db,
        job_id=job_id,
        current_user=current_user,
    )
    data = yolo_bm1688_export_service._redact_export_response_model_for_user(
        data, current_user
    )
    return OkResp[YoloEdgeValidationJobDetailResponse](data=data)


@bm1688_exports_router.get(
    "/compiler-settings",
    response_model=OkResp[Bm1688CompilerConfigResponse],
    status_code=status.HTTP_200_OK,
)
async def get_bm1688_compiler_settings(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = db, current_user, authorization
    data = bm1688_compiler_cfg_service.get_bm1688_compiler_config_view()
    data = yolo_bm1688_export_service._redact_path_response_model_for_user(
        data,
        current_user,
        empty_path_fields=(
            "tpu_mlir_root",
            "calibration_dataset_dir",
            "quantize_table_path",
            "tpu_mlir_python",
            "builtin_script_path",
            "legacy_sync_file",
        ),
    )
    return OkResp[Bm1688CompilerConfigResponse](data=data)


@bm1688_exports_router.patch(
    "/compiler-settings",
    response_model=OkResp[Bm1688CompilerConfigResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_bm1688_compiler_settings(
    cmd: PatchBm1688CompilerConfigCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, authorization
    data = bm1688_compiler_cfg_service.patch_bm1688_compiler_config(
        cmd, current_user=current_user
    )
    return OkResp[Bm1688CompilerConfigResponse](data=data)


# 静态路径须在 /{export_id} 之前注册，否则 edge-ssh-settings 会被当成 export_id 导致 422
@bm1688_exports_router.get(
    "/edge-ssh-settings",
    response_model=OkResp[EdgeSshConfigResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_bm1688_edge_ssh_settings(
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = edge_ssh_cfg_service.get_edge_ssh_config_view()
    if not is_task_superuser(current_user):
        data = edge_ssh_cfg_service.redact_edge_ssh_config_for_user(data)
    return OkResp[EdgeSshConfigResponse](data=data)


@bm1688_exports_router.patch(
    "/edge-ssh-settings",
    response_model=OkResp[EdgeSshConfigResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_yolo_bm1688_edge_ssh_settings(
    cmd: PatchEdgeSshConfigCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization
    _ = current_user
    return OkResp[EdgeSshConfigResponse](
        data=edge_ssh_cfg_service.patch_edge_ssh_config(cmd)
    )


@bm1688_exports_router.post(
    "/edge-ssh-settings/probe",
    response_model=OkResp[EdgeSshProbeResponse],
    status_code=status.HTTP_200_OK,
)
async def probe_yolo_bm1688_edge_ssh_settings(
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization
    _ = current_user
    return OkResp[EdgeSshProbeResponse](
        data=edge_ssh_cfg_service.probe_edge_ssh_config()
    )


@edge_devices_router.get(
    "",
    response_model=OkResp[EdgeDeviceListResponse],
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
async def list_edge_devices(
    q: Optional[str] = Query(
        None,
        max_length=128,
        description="按设备名称、设备 ID、地址或连接账号搜索",
    ),
    page: Optional[int] = Query(
        None,
        ge=0,
        description="从 0 开始的页码；省略时返回全部设备（兼容旧客户端）",
    ),
    size: Optional[int] = Query(
        None,
        ge=1,
        le=500,
        description="每页数量；省略时返回全部设备（兼容旧客户端）",
    ),
    status_filter: Optional[Literal["all", "ready", "blocked", "active", "disabled"]] = Query(
        None,
        alias="status",
        description="设备状态筛选",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[EdgeDeviceListResponse](
        data=edge_device_registry_service.list_edge_devices(
            include_sensitive=is_task_superuser(current_user),
            query=q,
            page=page,
            size=size,
            status_filter=status_filter,
        )
    )


@edge_devices_router.post(
    "",
    response_model=OkResp[EdgeDeviceResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_edge_device(
    cmd: CreateEdgeDeviceCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    return OkResp[EdgeDeviceResponse](
        data=edge_device_registry_service.create_edge_device(cmd)
    )


@edge_devices_router.put(
    "/active",
    response_model=OkResp[EdgeDeviceResponse],
    status_code=status.HTTP_200_OK,
)
async def set_active_edge_device(
    cmd: SetActiveEdgeDeviceCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    return OkResp[EdgeDeviceResponse](
        data=edge_device_registry_service.set_active_edge_device(cmd)
    )


@edge_devices_router.post(
    "/environment-check-all",
    response_model=OkResp[EdgeDeviceListResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_all_edge_device_environment_checks(
    only_pending: bool = Query(False, description="仅检查未就绪或上次失败的设备"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    return OkResp[EdgeDeviceListResponse](
        data=edge_device_registry_service.start_all_environment_checks(
            provision=True, only_pending=only_pending
        )
    )


def _edge_agent_bootstrap_exception(exc: Exception) -> LabelUException:
    code = str(getattr(exc, "code", "") or "")
    status_code = (
        status.HTTP_404_NOT_FOUND
        if code == "device_not_found"
        else (
            status.HTTP_409_CONFLICT
            if code
            in {
                "confirmation_required",
                "channel_activation_failed",
                "device_busy",
            }
            else (
                status.HTTP_503_SERVICE_UNAVAILABLE
                if code
                in {
                    "device_busy_check_failed",
                    "device_mutation_lock_unavailable",
                    "bootstrap_state_persist_failed",
                    "channel_state_persist_failed",
                }
                else status.HTTP_400_BAD_REQUEST
            )
        )
    )
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status_code,
        message=str(exc)[:800] or "边端 Agent 引导失败",
    )


@edge_devices_router.post(
    "/agent-bootstrap",
    response_model=OkResp[EdgeAgentBootstrapResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def bootstrap_edge_agent(
    cmd: BootstrapEdgeAgentCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    try:
        data = edge_agent_bootstrap_service.start_bootstrap_background(cmd)
    except Exception as exc:
        raise _edge_agent_bootstrap_exception(exc) from exc
    return OkResp[EdgeAgentBootstrapResponse](data=data)


@edge_devices_router.get(
    "/{device_id}/agent-bootstrap",
    response_model=OkResp[EdgeAgentBootstrapResponse],
    status_code=status.HTTP_200_OK,
)
async def get_edge_agent_bootstrap_status(
    device_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    try:
        data = edge_agent_bootstrap_service.get_bootstrap_status(device_id)
    except Exception as exc:
        raise _edge_agent_bootstrap_exception(exc) from exc
    return OkResp[EdgeAgentBootstrapResponse](data=data)


@edge_devices_router.post(
    "/{device_id}/agent-bootstrap",
    response_model=OkResp[EdgeAgentBootstrapResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def bootstrap_edge_agent_for_device(
    device_id: str,
    cmd: BootstrapEdgeAgentCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    if str(cmd.device_id).strip() != str(device_id).strip():
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请求体 device_id 与路径不一致",
        )
    try:
        data = edge_agent_bootstrap_service.start_bootstrap_background(cmd)
    except Exception as exc:
        raise _edge_agent_bootstrap_exception(exc) from exc
    return OkResp[EdgeAgentBootstrapResponse](data=data)


@edge_devices_router.get(
    "/{device_id}",
    response_model=OkResp[EdgeDeviceResponse],
    response_model_exclude_none=True,
    status_code=status.HTTP_200_OK,
)
async def get_edge_device(
    device_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[EdgeDeviceResponse](
        data=edge_device_registry_service.get_edge_device(
            device_id,
            include_sensitive=is_task_superuser(current_user),
        )
    )


@edge_devices_router.patch(
    "/{device_id}",
    response_model=OkResp[EdgeDeviceResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_edge_device(
    device_id: str,
    cmd: PatchEdgeDeviceCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    return OkResp[EdgeDeviceResponse](
        data=edge_device_registry_service.update_edge_device(device_id, cmd)
    )


@edge_devices_router.delete(
    "/{device_id}",
    response_model=OkResp[EdgeDeviceListResponse],
    status_code=status.HTTP_200_OK,
)
async def delete_edge_device(
    device_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    return OkResp[EdgeDeviceListResponse](
        data=edge_device_registry_service.delete_edge_device(device_id)
    )


@edge_devices_router.post(
    "/{device_id}/probe",
    response_model=OkResp[EdgeSshProbeResponse],
    status_code=status.HTTP_200_OK,
)
async def probe_edge_device(
    device_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    return OkResp[EdgeSshProbeResponse](
        data=edge_device_registry_service.probe_edge_device(device_id)
    )


@edge_devices_router.post(
    "/{device_id}/environment-check",
    response_model=OkResp[EdgeDeviceResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_edge_device_environment_check(
    device_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(require_system_admin),
):
    _ = authorization, current_user
    edge_device_registry_service.assert_no_active_job_for_environment_check(device_id)
    return OkResp[EdgeDeviceResponse](
        data=edge_device_registry_service.start_environment_check(
            device_id, provision=True
        )
    )


@bm1688_exports_router.get(
    "/verify-config",
    status_code=status.HTTP_200_OK,
)
async def verify_yolo_bm1688_compiler_config(
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await yolo_bm1688_export_service.get_compiler_sophon_verify()
    data = yolo_bm1688_export_service._export_payload_for_user(data, current_user)
    return OkResp[dict](data=data)


@bm1688_exports_router.get(
    "/{export_id}/verify",
    status_code=status.HTTP_200_OK,
)
async def verify_yolo_bm1688_export(
    export_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await yolo_bm1688_export_service.get_export_sophon_verify(
        db, export_id=export_id, current_user=current_user
    )
    return OkResp[dict](data=data)


@bm1688_exports_router.get(
    "/{export_id}/summary",
    response_model=OkResp[YoloBm1688ExportDetailResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_bm1688_export_summary(
    export_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await yolo_bm1688_export_service.get_export_summary(
        db, export_id=export_id, current_user=current_user
    )
    return OkResp[YoloBm1688ExportDetailResponse](data=data)


@bm1688_exports_router.get(
    "/{export_id}/evaluation",
    response_model=OkResp[YoloBm1688ExportDetailResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_bm1688_export_evaluation(
    export_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await yolo_bm1688_export_service.get_export_evaluation(
        db, export_id=export_id, current_user=current_user
    )
    return OkResp[YoloBm1688ExportDetailResponse](data=data)


@bm1688_exports_router.get(
    "/{export_id}",
    response_model=OkResp[YoloBm1688ExportDetailResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_bm1688_export_detail(
    export_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await yolo_bm1688_export_service.get_export_detail(
        db, export_id=export_id, current_user=current_user
    )
    return OkResp[YoloBm1688ExportDetailResponse](data=data)


@bm1688_exports_router.post(
    "/{export_id}/board-validation",
    response_model=OkResp[BoardValidationStatusResponse],
    status_code=status.HTTP_200_OK,
    summary="创建板端验证任务",
)
async def request_yolo_bm1688_board_validation(
    export_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    from labelu.internal.application.service import board_agent as board_agent_service
    from labelu.internal.common.bm1688_quality_report import parse_quality_report

    _ = authorization
    row = yolo_bm1688_export_service.get_export_for_download(
        db, export_id=export_id, current_user=current_user
    )
    if row.status != "COMPLETED":
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="导出未完成，无法创建板端验证任务",
        )
    with begin_transaction(db):
        board_agent_service.request_board_validation_for_export(
            db,
            export_id=row.id,
            task_id=row.task_id,
            yolo_train_job_id=row.yolo_train_job_id,
            export_status=row.status,
        )
    qr = parse_quality_report(getattr(row, "quality_report", None))
    data = board_agent_service.get_status_for_export(
        db, export_id=export_id, include_report=True, quality_report=qr
    )
    data = yolo_bm1688_export_service._redact_board_validation_status_for_user(
        data, current_user
    )
    if not data:
        raise LabelUException(
            code=ErrorCode.CODE_57024_BOARD_VALIDATION_NOT_FOUND,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="板端验证任务创建失败",
        )
    return OkResp[BoardValidationStatusResponse](data=data)


@bm1688_exports_router.get(
    "/{export_id}/board-validation",
    response_model=OkResp[BoardValidationStatusResponse],
    status_code=status.HTTP_200_OK,
    summary="查询板端验证结果",
)
async def get_yolo_bm1688_board_validation(
    export_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    from labelu.internal.application.service import (
        yolo_aicm_publish as yolo_aicm_publish_service,
    )

    _ = authorization
    from labelu.internal.common.bm1688_quality_report import parse_quality_report

    export_row = yolo_bm1688_export_service.get_export_for_download(
        db, export_id=export_id, current_user=current_user
    )
    qr = parse_quality_report(getattr(export_row, "quality_report", None))
    data = yolo_aicm_publish_service._edge_validation_status_for_export(
        db,
        export_id=export_id,
        include_report=True,
        quality_report=qr,
    )
    data = yolo_bm1688_export_service._redact_board_validation_status_for_user(
        data, current_user
    )
    if not data:
        raise LabelUException(
            code=ErrorCode.CODE_57024_BOARD_VALIDATION_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return OkResp[BoardValidationStatusResponse](data=data)


@bm1688_exports_router.post(
    "/{export_id}/quality-report",
    response_model=OkResp[YoloBm1688ExportDetailResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def refresh_yolo_bm1688_quality_report(
    export_id: int,
    run_onnx_val: bool = Query(
        False, description="是否在 coco128 上跑 ONNX Ultralytics 验证"
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await yolo_bm1688_export_service.refresh_export_quality_report(
        db,
        export_id=export_id,
        current_user=current_user,
        run_onnx_val=run_onnx_val,
    )
    return OkResp[YoloBm1688ExportDetailResponse](data=data)


@bm1688_exports_router.get(
    "/{export_id}/sop-eval-report/{kind}",
    status_code=status.HTTP_200_OK,
)
async def download_yolo_bm1688_sop_eval_report(
    export_id: int,
    kind: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """文档 3.6.2：evaluation_report.md（kind=pt|onnx|bmodel）。"""
    _ = authorization
    text = yolo_bm1688_export_service.get_sop_eval_report_markdown(
        db, export_id=export_id, kind=kind, current_user=current_user
    )
    text = yolo_bm1688_export_service._export_payload_for_user(text, current_user)
    return PlainTextResponse(content=text, media_type="text/markdown; charset=utf-8")


@bm1688_exports_router.get(
    "/{export_id}/runtime-log/{kind}",
    status_code=status.HTTP_200_OK,
)
async def download_yolo_bm1688_runtime_log(
    export_id: int,
    kind: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    text = yolo_bm1688_export_service.get_runtime_log_text(
        db, export_id=export_id, kind=kind, current_user=current_user
    )
    text = yolo_bm1688_export_service._export_payload_for_user(text, current_user)
    return PlainTextResponse(content=text, media_type="text/plain; charset=utf-8")


@bm1688_exports_router.get(
    "/{export_id}/sop-eval-asset/{kind}/{filename}",
    status_code=status.HTTP_200_OK,
)
async def download_yolo_bm1688_sop_eval_asset(
    export_id: int,
    kind: str,
    filename: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """文档 3.6.2：评估曲线图等资源（kind=pt|onnx|bmodel|bmcv 边端检测可视化）。"""
    _ = authorization
    path = yolo_bm1688_export_service.get_sop_eval_asset_path(
        db, export_id=export_id, kind=kind, filename=filename, current_user=current_user
    )
    return FileResponse(path)


@bm1688_exports_router.post(
    "/{export_id}/edge-ssh-validation",
    status_code=status.HTTP_200_OK,
)
async def start_yolo_bm1688_edge_ssh_validation(
    export_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """部署机 SSH 到边端执行板端验证 + bmodel SOP 评估，结果写入 server_eval/results_bmodel。"""
    from labelu.internal.application.service import edge_ssh_validation as edge_ssh
    from labelu.internal.application.service import (
        yolo_edge_validation as yolo_edge_validation_service,
    )
    from labelu.internal.application.service import yolo_edge_validation_scheduler

    _ = authorization
    scheduler_configured = yolo_edge_validation_scheduler.has_configured_edge_devices()
    legacy_configured = edge_ssh._edge_ssh_configured()
    if not scheduler_configured and not legacy_configured:
        raise LabelUException(
            code=ErrorCode.CODE_57029_EDGE_SSH_VALIDATION_DISABLED,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message="请先至少配置并启用一台边端设备",
        )
    if not scheduler_configured:
        probe = edge_ssh_cfg_service.probe_edge_ssh_config()
        if probe.status != "ok":
            raise LabelUException(
                code=ErrorCode.CODE_57029_EDGE_SSH_VALIDATION_DISABLED,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                message=f"边端 SSH 配置存在但探测失败（{probe.code}/{probe.stage}）：{probe.message}",
            )
    yolo_edge_validation_service.run_legacy_edge_ssh_job_sync(
        db, export_id=export_id, current_user=current_user
    )
    data = await yolo_bm1688_export_service.refresh_export_quality_report(
        db,
        export_id=export_id,
        current_user=current_user,
        run_onnx_val=False,
    )
    return OkResp[YoloBm1688ExportDetailResponse](data=data)


@bm1688_exports_router.post(
    "/{export_id}/recompile",
    response_model=OkResp[YoloBm1688ExportDetailResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def recompile_yolo_bm1688_bmodel(
    export_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await yolo_bm1688_export_service.start_recompile_for_export(
        db, export_id=export_id, current_user=current_user
    )
    return OkResp[YoloBm1688ExportDetailResponse](data=data)


@bm1688_exports_router.delete(
    "/{export_id}",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def delete_yolo_bm1688_export(
    export_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = yolo_bm1688_export_service.delete_export(
        db, export_id=export_id, current_user=current_user
    )
    return OkResp[dict](data=data)


@bm1688_exports_router.get(
    "/{export_id}/onnx",
    status_code=status.HTTP_200_OK,
)
async def download_yolo_bm1688_onnx(
    export_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    row = yolo_bm1688_export_service.get_export_for_download(
        db, export_id=export_id, current_user=current_user
    )
    if row.status in ("PENDING", "PROCESSING"):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message="导出任务进行中，请稍后刷新列表再下载",
        )
    if row.status == "FAILED" or not row.onnx_rel_path:
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message=row.error_message or "ONNX 未生成",
        )
    path = yolo_bm1688_export_service.resolve_bm1688_media_file(row.onnx_rel_path)
    fn = path.name or f"export-{export_id}.onnx"
    return FileResponse(
        path=str(path.resolve()), filename=fn, media_type="application/octet-stream"
    )


@bm1688_exports_router.get(
    "/{export_id}/bmodel",
    status_code=status.HTTP_200_OK,
)
async def download_yolo_bm1688_bmodel(
    export_id: int,
    filename: Union[str, None] = Query(
        default=None, description="指定 .bmodel 文件名；省略则下载主模型"
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    row = yolo_bm1688_export_service.get_export_for_download(
        db, export_id=export_id, current_user=current_user
    )
    if row.status in ("PENDING", "PROCESSING"):
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message="导出任务进行中，请稍后再试",
        )
    if row.status == "FAILED":
        raise LabelUException(
            code=ErrorCode.CODE_57016_YOLO_BM1688_FILE_NOT_READY,
            status_code=status.HTTP_404_NOT_FOUND,
            message=row.error_message or "导出失败",
        )
    path = yolo_bm1688_export_service.resolve_bmodel_download_path(
        row, filename=filename
    )
    fn = path.name or f"export-{export_id}.bmodel"
    return FileResponse(
        path=str(path.resolve()), filename=fn, media_type="application/octet-stream"
    )


@router.post(
    "/{task_id}/yolo-train-jobs/{job_id}/evaluate",
    response_model=OkResp[YoloEvalAcceptedResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_yolo_eval(
    task_id: int,
    job_id: int,
    cmd: YoloEvalStartCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await yolo_train_evaluation.start_evaluation_job(
        db=db, task_id=task_id, job_id=job_id, cmd=cmd, current_user=current_user
    )
    return OkResp[YoloEvalAcceptedResponse](
        data=YoloEvalAcceptedResponse(
            accepted=bool(data.get("accepted")),
            report_kind=str(data.get("report_kind", "")),
            providers=list(data.get("providers") or []),
        )
    )


@router.get(
    "/{task_id}/yolo-train-jobs/{job_id}/evaluate-reports",
    response_model=OkResp[List[YoloEvalReportResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_eval_reports(
    task_id: int,
    job_id: int,
    report_kind: Optional[str] = Query(
        None, description="full_analysis | next_iteration，省略则返回全部"
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    rows = yolo_train_evaluation.list_eval_reports(
        db=db,
        task_id=task_id,
        job_id=job_id,
        report_kind=report_kind,
        current_user=current_user,
    )
    return OkResp[List[YoloEvalReportResponse]](data=rows)


@router.get(
    "/{task_id}/yolo-train-jobs/{job_id}/evaluation-hub",
    response_model=OkResp[YoloEvalHubResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_evaluation_hub(
    task_id: int,
    job_id: int,
    export_id: Optional[int] = Query(
        None, description="优先展示该 BM1688 导出记录的报告"
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = yolo_train_evaluation.build_evaluation_hub(
        db=db,
        task_id=task_id,
        job_id=job_id,
        current_user=current_user,
        focus_export_id=export_id,
    )
    return OkResp[YoloEvalHubResponse](data=data)


@eval_reports_router.get(
    "",
    response_model=OkRespWithMeta[List[YoloEvalReportListRowResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_eval_reports_global(
    report_kind: str = Query(..., description="full_analysis | next_iteration"),
    q: Optional[str] = Query(
        None,
        max_length=128,
        description="按任务名称、报告提供方或报告类型模糊搜索",
    ),
    search: Optional[str] = Query(
        None,
        max_length=128,
        include_in_schema=False,
        description="兼容旧客户端的搜索参数",
    ),
    task_name: Optional[str] = Query(None, description="标注任务名称，模糊匹配"),
    rect_preset_slug: Optional[str] = Query(None, description="拉框预设 slug"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    project_id: Optional[int] = Query(None, gt=0, description="当前7GAI项目 ID"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    normalized_search = str(q if q is not None else search or "").strip() or None
    data, total = yolo_train_evaluation.list_eval_reports_global(
        db,
        current_user,
        report_kind=report_kind,
        task_name=task_name,
        search=normalized_search,
        rect_preset_slug=rect_preset_slug,
        page=page,
        size=size,
        project_id=project_id,
    )
    meta_data = MetaData(total=total, page=page, size=len(data))
    return OkRespWithMeta[List[YoloEvalReportListRowResponse]](
        meta_data=meta_data, data=data
    )


@eval_reports_router.get(
    "/{report_id}",
    response_model=OkResp[YoloEvalReportResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_eval_report_by_id(
    report_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = yolo_train_evaluation.get_eval_report_by_id(
        db, report_id=report_id, current_user=current_user
    )
    return OkResp[YoloEvalReportResponse](data=data)


@trained_models_router.get(
    "",
    response_model=YoloTrainedModelListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_yolo_trained_models_global(
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按训练作业、训练任务、标注任务、数据集、权重、分类或状态搜索",
    ),
    task_name: Optional[str] = Query(None, description="标注任务名称，模糊匹配"),
    dataset_category_id: Optional[int] = Query(
        None, gt=0, description="独立数据集分类 ID"
    ),
    rect_preset_slug: Optional[str] = Query(None, description="拉框预设 slug"),
    training_status: Optional[Literal["passed", "review_required", "failed"]] = Query(
        None, description="训练门禁状态"
    ),
    quality_status: Optional[
        Literal["publishable", "review_required", "blocked"]
    ] = Query(None, description="兼容旧客户端的发布门禁状态别名"),
    sort_by: Literal[
        "training_priority",
        "release_priority",
        "latest",
        "test_map50_95",
        "generalization_risk",
        "sample_count",
    ] = Query("training_priority", description="模型库排序方式"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total, summary = await run_in_threadpool(
        yolo_train_service.list_trained_model_catalog,
        db,
        current_user,
        search=search,
        task_name=task_name,
        dataset_category_id=dataset_category_id,
        rect_preset_slug=rect_preset_slug,
        training_status=training_status,
        quality_status=quality_status,
        sort_by=sort_by,
        page=page,
        size=size,
    )
    meta_data = YoloTrainedModelCatalogMeta(
        total=total, page=page, size=len(data), summary=summary
    )
    return YoloTrainedModelListResponse(meta_data=meta_data, data=data)


@imported_models_router.get(
    "",
    response_model=OkRespWithMeta[List[YoloImportedModelRowResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_imported_models_global(
    search: Optional[str] = Query(
        None,
        alias="q",
        max_length=128,
        description="按模型名称、原始文件名或分类搜索",
    ),
    name: Optional[str] = Query(None, description="显示名称或原始文件名，模糊匹配"),
    category: Optional[str] = Query(None, description="训练分类，模糊匹配"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = yolo_train_service.list_imported_models_global(
        db,
        current_user,
        search=search,
        name=name,
        category=category,
        page=page,
        size=size,
    )
    meta_data = MetaData(total=total, page=page, size=len(data))
    return OkRespWithMeta[List[YoloImportedModelRowResponse]](
        meta_data=meta_data, data=data
    )


@imported_models_router.post(
    "",
    response_model=OkResp[YoloImportedModelRowResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_yolo_imported_model(
    file: UploadFile = File(..., description="权重文件，.pt 或 .pth"),
    display_name: Optional[str] = Form(None, description="列表展示名称，默认同文件名"),
    category: Optional[str] = Form(None, description="分类，便于与本系统训练分组一致"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = await yolo_train_service.create_imported_weights_model(
        db,
        current_user,
        file=file,
        display_name=display_name,
        category=category,
    )
    return OkResp[YoloImportedModelRowResponse](data=data)


@imported_models_router.get(
    "/{model_id}/download",
    status_code=status.HTTP_200_OK,
)
async def download_yolo_imported_model(
    model_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    path, filename = yolo_train_service.get_imported_weights_download(
        db, current_user, model_id=model_id
    )
    return FileResponse(
        path=str(path),
        filename=filename,
        media_type="application/octet-stream",
    )


@imported_models_router.delete(
    "/{model_id}",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def delete_yolo_imported_model(
    model_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data = yolo_train_service.delete_imported_weights_model(
        db, current_user, model_id=model_id
    )
    return OkResp[dict](data=data)


@train_meta_router.post(
    "/consistent-pools/{task_id}/repair-access",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def repair_yolo_train_consistent_pool_access(
    task_id: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove legacy collaborators and restore administrator-only access."""
    _ = authorization
    from labelu.internal.common.task_access import is_consistent_pool_admin

    if not is_consistent_pool_admin(current_user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )

    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    with begin_transaction(db):
        data = yolo_train_consistent_pool_service.repair_pool_task_access(db, task_id)
    return OkResp[dict](data=data)


@train_meta_router.get(
    "/consistent-pools/reconcile-preview",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def preview_yolo_train_consistent_pool_reconciliation(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    from labelu.internal.common.task_access import is_consistent_pool_admin

    if not is_consistent_pool_admin(current_user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return OkResp[dict](
        data=yolo_train_consistent_pool_service.reconcile_consistent_pools(
            db, apply=False
        )
    )


@train_meta_router.post(
    "/consistent-pools/reconcile",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def apply_yolo_train_consistent_pool_reconciliation(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    from labelu.internal.common.task_access import is_consistent_pool_admin

    if not is_consistent_pool_admin(current_user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    with begin_transaction(db):
        data = yolo_train_consistent_pool_service.reconcile_consistent_pools(
            db, apply=True
        )
    return OkResp[dict](data=data)


@train_meta_router.get(
    "/consistent-pools",
    response_model=OkResp[List[dict]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_train_consistent_pools(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    rows = yolo_train_consistent_pool_service.list_consistent_pools(
        db=db, current_user=current_user
    )
    return OkResp[List[dict]](data=rows)


#
# Contract note:
# - `/remote-nodes?env_ready_only=true` is kept for remote-node readiness views.
# - The create-training frontend must not use it as the candidate source anymore.
# - Training-create candidate tasks are formally served by `/eligible-tasks`.
#
@train_meta_router.get(
    "/remote-nodes",
    response_model=OkResp[RemoteTrainNodesListResponse],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_remote_train_nodes(
    env_ready_only: bool = Query(
        False,
        description="创建训练下拉：仅返回已启用且测试通过的节点",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization, current_user
    return OkResp[RemoteTrainNodesListResponse](
        data=remote_train_nodes_service.list_remote_train_nodes_view(
            env_ready_only=env_ready_only
        )
    )


@train_meta_router.get(
    "/remote-nodes/admin",
    response_model=OkResp[RemoteTrainNodesListResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_remote_train_nodes_admin(
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[RemoteTrainNodesListResponse](
        data=remote_train_nodes_service.get_remote_train_nodes_admin_view(current_user)
    )


@train_meta_router.put(
    "/remote-nodes",
    response_model=OkResp[RemoteTrainNodesListResponse],
    status_code=status.HTTP_200_OK,
)
async def put_yolo_remote_train_nodes(
    cmd: PutRemoteTrainNodesCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[RemoteTrainNodesListResponse](
        data=remote_train_nodes_service.put_remote_train_nodes(current_user, cmd)
    )


@train_meta_router.patch(
    "/remote-nodes/{node_id}",
    response_model=OkResp[RemoteTrainNodeResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_yolo_remote_train_node(
    node_id: str,
    cmd: PatchRemoteTrainNodeCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[RemoteTrainNodeResponse](
        data=remote_train_nodes_service.patch_remote_train_node(
            current_user, node_id, cmd
        )
    )


@train_meta_router.post(
    "/remote-nodes/{node_id}/test",
    response_model=OkResp[RemoteTrainNodeTestResponse],
    status_code=status.HTTP_200_OK,
)
async def test_yolo_remote_train_node(
    node_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    return OkResp[RemoteTrainNodeTestResponse](
        data=remote_train_nodes_service.test_remote_train_node(current_user, node_id)
    )


#
# Contract note:
# - This is the formal candidate-task API for the create-training modal.
# - Do not switch the create flow back to generic `/tasks` queries or
#   `/remote-nodes?env_ready_only=true`.
#
@train_meta_router.get(
    "/eligible-tasks",
    response_model=OkRespWithMeta[List[TaskTrainingCandidateResponse]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_train_eligible_tasks(
    project_id: Optional[int] = Query(
        None,
        gt=0,
        description="7GAI 项目 ID；提供时仅返回该项目内可训练任务",
    ),
    label_ids: Optional[List[int]] = Query(
        None,
        description="全局标签 ID；新训练链路按任务标签契约覆盖关系筛选",
    ),
    task_dimension: bool = Query(
        False,
        description="任务优先训练：返回可选择的图片任务及其当前标签契约",
    ),
    rect_preset_slug: Optional[str] = Query(
        None,
        min_length=1,
        description="兼容旧训练入口的拉框预设 slug",
    ),
    dataset_category_ids: Optional[List[int]] = Query(
        None,
        description="可多选的数据集分类 ID；仅返回所选分类下的可训练图片任务",
    ),
    training_recipe_key: Optional[str] = Query(
        None,
        max_length=128,
        description="任务优先训练所选配方；车牌配方使用复合标注审计",
    ),
    search: Optional[str] = Query(
        None,
        max_length=128,
        include_in_schema=False,
        description="兼容旧客户端的候选任务搜索词",
    ),
    q: Optional[str] = Query(
        None,
        max_length=128,
        description="按任务名称、描述或数据集名称搜索候选任务",
    ),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    normalized_search = str(q if q is not None else search or "").strip() or None
    if task_dimension:
        data, total = await task_service.list_task_dimension_training_candidates(
            db,
            current_user,
            page=page,
            size=size,
            project_id=project_id,
            dataset_category_ids=dataset_category_ids,
            label_ids=label_ids,
            training_recipe_key=training_recipe_key,
            search=normalized_search,
        )
    elif label_ids:
        data, total = await task_service.list_training_candidates_by_labels(
            db,
            current_user,
            label_ids=label_ids,
            page=page,
            size=size,
            dataset_category_ids=dataset_category_ids,
            search=normalized_search,
        )
    elif rect_preset_slug:
        data, total = await task_service.list_training_candidates(
            db,
            current_user,
            rect_preset_slug=rect_preset_slug,
            page=page,
            size=size,
            search=normalized_search,
        )
    else:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="label_ids 不能为空；rect_preset_slug 仅用于旧流程兼容",
        )
    meta_data = MetaData(total=total, page=page, size=len(data))
    return OkRespWithMeta[List[TaskTrainingCandidateResponse]](
        meta_data=meta_data,
        data=data,
    )


@train_meta_router.get(
    "/hyperparam-fields",
    response_model=OkResp[List[YoloHyperParamFieldItem]],
    status_code=status.HTTP_200_OK,
)
async def list_yolo_hyperparam_field_meta(
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    from labelu.internal.common.yolo_train_hyperparams import hyperparam_field_meta

    rows = hyperparam_field_meta()
    data = [YoloHyperParamFieldItem(**r) for r in rows]
    return OkResp[List[YoloHyperParamFieldItem]](data=data)


@train_meta_router.get(
    "/iteration-baseline",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_iteration_baseline(
    rect_preset_slug: Optional[str] = Query(
        None,
        description="拉框预设 slug，如 garbageExposure；返回 54.99 SOP 超参基线",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    current_user: User = Depends(get_current_user),
):
    from labelu.internal.common.yolo_train_hyperparams import (
        get_iteration_baseline_hyperparams,
        iteration_baseline_recommended_train_params,
    )

    slug = (rect_preset_slug or "").strip() or None
    return OkResp[dict](
        data={
            "rect_preset_slug": slug,
            "hyperparams": get_iteration_baseline_hyperparams(slug),
            "recommended_train_params": iteration_baseline_recommended_train_params(
                slug
            ),
        }
    )


@train_meta_router.get(
    "/remote-jobs/{job_id}/payload",
    status_code=status.HTTP_200_OK,
)
async def download_remote_train_payload(
    job_id: int,
    token: str = Query(..., description="平台下发的短时下载 token"),
):
    """远程训练节点拉取 job payload（支持 HTTP Range 断点续传，无需用户 JWT）。"""
    from labelu.internal.application.service import (
        remote_yolo_train as remote_train_svc,
    )
    from labelu.internal.common.remote_train_payload_token import (
        verify_remote_payload_token,
    )

    if not verify_remote_payload_token(token, job_id):
        raise LabelUException(
            code=ErrorCode.CODE_57023_BOARD_AGENT_UNAUTHORIZED,
            status_code=status.HTTP_403_FORBIDDEN,
        )
    path = remote_train_svc.resolve_remote_payload_archive(job_id)
    if path is None:
        raise LabelUException(
            code=ErrorCode.CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    media_type = (
        "application/gzip" if path.name.endswith(".gz") else "application/x-tar"
    )
    if settings.YOLO_REMOTE_TRAIN_PAYLOAD_NGINX_ACCEL:
        from starlette.responses import Response

        return Response(
            headers={
                "X-Accel-Redirect": remote_train_svc.nginx_accel_redirect_path(
                    job_id, path
                ),
                "Content-Type": media_type,
                "Accept-Ranges": "bytes",
                "Content-Disposition": f'attachment; filename="{path.name}"',
            }
        )
    return FileResponse(
        path=path,
        filename=path.name,
        media_type=media_type,
        headers={"Accept-Ranges": "bytes"},
    )
