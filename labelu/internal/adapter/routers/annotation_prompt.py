"""HTTP API for compiling and publishing contract-bound AI prompts."""

from typing import List

from fastapi import APIRouter, Depends, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.annotation_prompt import (
    AnnotationPromptPreviewCommand,
    PublishAnnotationPromptCommand,
    SaveAnnotationPromptCommand,
)
from labelu.internal.application.response.annotation_prompt import (
    AnnotationPromptResponse,
)
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.service import annotation_prompt as service
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
