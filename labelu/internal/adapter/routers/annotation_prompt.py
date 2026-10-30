"""HTTP API for compiling and publishing contract-bound AI prompts."""

from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Security, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.annotation_prompt import (
    AnnotationPromptPreviewCommand,
    PublishAnnotationPromptCommand,
    SaveAnnotationPromptCommand,
)
from labelu.internal.application.command.annotation_prompt_trial import (
    AnnotationPromptTrialCommand,
)
from labelu.internal.application.response.annotation_prompt import (
    AnnotationPromptResponse,
)
from labelu.internal.application.response.annotation_prompt_trial import (
    AnnotationPromptTrialResponse,
)
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.service import annotation_prompt as service
from labelu.internal.application.service import annotation_prompt_trial as trial_service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/datasets", tags=["annotation-prompts"])


@router.get(
    "/{dataset_id}/label-contracts/{revision}/ai-prompts",
    response_model=OkResp[List[AnnotationPromptResponse]],
)
async def list_annotation_prompts(
    dataset_id: int,
    revision: int,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await service.list_prompts(db, dataset_id, revision, current_user)
    return OkResp[List[AnnotationPromptResponse]](data=data)


@router.get(
    "/{dataset_id}/ai-prompts/history",
    response_model=OkResp[List[AnnotationPromptResponse]],
)
async def list_annotation_prompt_history(
    dataset_id: int,
    include_invalidated: bool = Query(
        default=True,
        description="历史查询默认包含已失效提示词；传 false 仅返回仍可复用的记录",
    ),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await service.list_prompt_history(db, dataset_id, current_user)
    if not include_invalidated:
        data = [
            item
            for item in data
            if str(item.status or "").upper() != service.INVALIDATED
        ]
    return OkResp[List[AnnotationPromptResponse]](data=data)


@router.post(
    "/{dataset_id}/label-contracts/{revision}/ai-prompts/preview",
    response_model=OkResp[AnnotationPromptResponse],
)
async def preview_annotation_prompt(
    dataset_id: int,
    revision: int,
    cmd: AnnotationPromptPreviewCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await service.preview(db, dataset_id, revision, cmd, current_user)
    return OkResp[AnnotationPromptResponse](data=data)


@router.post(
    "/{dataset_id}/label-contracts/{revision}/ai-prompts/trial",
    response_model=OkResp[AnnotationPromptTrialResponse],
)
async def trial_annotation_prompt(
    dataset_id: int,
    revision: int,
    image: UploadFile = File(..., description="仅用于本次试运行的图片，不会保存为样本"),
    prompt_id: Optional[int] = Form(default=None),
    prompt_override: Optional[str] = Form(default=None),
    prompt_text: Optional[str] = Form(default=None),
    operation: str = Form(default="bbox_detection"),
    provider_key: str = Form(default="volcengine"),
    model_name: Optional[str] = Form(default=None),
    profile_key: str = Form(default="annotation_bbox_v1"),
    profile_version: str = Form(default="1"),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    try:
        image_bytes = await image.read()
        cmd = AnnotationPromptTrialCommand(
            prompt_id=prompt_id,
            prompt_override=prompt_override,
            prompt_text=prompt_text,
            operation=operation,
            provider_key=provider_key,
            model_name=model_name,
            profile_key=profile_key,
            profile_version=profile_version,
        )
        data = await trial_service.run_trial(
            db,
            dataset_id,
            revision,
            image_bytes,
            current_user,
            cmd,
            image_content_type=image.content_type,
            image_name=image.filename or "trial-image",
        )
    finally:
        await image.close()
    return OkResp[AnnotationPromptTrialResponse](data=data)


@router.post(
    "/{dataset_id}/label-contracts/{revision}/ai-prompts",
    response_model=OkResp[AnnotationPromptResponse],
    status_code=status.HTTP_201_CREATED,
)
async def save_annotation_prompt(
    dataset_id: int,
    revision: int,
    cmd: SaveAnnotationPromptCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await service.save_draft(db, dataset_id, revision, cmd, current_user)
    return OkResp[AnnotationPromptResponse](data=data)


@router.post(
    "/{dataset_id}/label-contracts/{revision}/ai-prompts/{prompt_id}/publish",
    response_model=OkResp[AnnotationPromptResponse],
)
async def publish_annotation_prompt(
    dataset_id: int,
    revision: int,
    prompt_id: int,
    cmd: PublishAnnotationPromptCommand | None = None,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = await service.publish(
        db,
        dataset_id,
        revision,
        prompt_id,
        cmd or PublishAnnotationPromptCommand(),
        current_user,
    )
    return OkResp[AnnotationPromptResponse](data=data)
