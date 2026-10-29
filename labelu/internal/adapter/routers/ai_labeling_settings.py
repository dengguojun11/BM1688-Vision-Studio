from fastapi import APIRouter, Depends, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.ai_labeling_llm_profile import (
    CreateLlmProfileCommand,
    PatchAnnotationChainCommand,
    PatchYoloEvalPromptsBody,
    UpdateLlmProfileCommand,
)
from labelu.internal.application.command.ai_labeling_runtime import PatchAiLabelingRuntimeCommand
from labelu.internal.application.response.ai_labeling_llm_profile import (
    LlmProfilesBundleResponse,
    YoloEvalPromptsPublicResponse,
)
from labelu.internal.application.response.ai_labeling_instruction_template import (
    AiLabelingInstructionTemplateResponse,
)
from labelu.internal.application.response.ai_labeling_runtime import AiLabelingRuntimeResponse
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.service import ai_labeling_profiles as ai_profiles_service
from labelu.internal.application.service import ai_labeling_runtime as ai_runtime_service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/ai-labeling", tags=["ai-labeling"])


@router.get(
    "/runtime-settings",
    response_model=OkResp[AiLabelingRuntimeResponse],
    status_code=status.HTTP_200_OK,
)
async def get_ai_labeling_runtime_settings(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = db, current_user, authorization
    data = ai_runtime_service.get_ai_labeling_runtime_view()
    return OkResp[AiLabelingRuntimeResponse](data=data)


@router.get(
    "/instruction-template",
    response_model=OkResp[AiLabelingInstructionTemplateResponse],
    status_code=status.HTTP_200_OK,
)
async def get_ai_labeling_instruction_template(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = db, current_user, authorization
    data = ai_runtime_service.get_instruction_template_view()
    return OkResp[AiLabelingInstructionTemplateResponse](data=data)


@router.patch(
    "/runtime-settings",
    response_model=OkResp[AiLabelingRuntimeResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_ai_labeling_runtime_settings(
    cmd: PatchAiLabelingRuntimeCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = ai_runtime_service.patch_ai_labeling_runtime(cmd)
    return OkResp[AiLabelingRuntimeResponse](data=data)


@router.get(
    "/llm-profiles",
    response_model=OkResp[LlmProfilesBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def list_llm_profiles(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = db, current_user, authorization
    data = ai_profiles_service.list_llm_profiles_bundle()
    return OkResp[LlmProfilesBundleResponse](data=data)


@router.post(
    "/llm-profiles",
    response_model=OkResp[LlmProfilesBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def create_llm_profile(
    cmd: CreateLlmProfileCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = ai_profiles_service.create_llm_profile(cmd)
    return OkResp[LlmProfilesBundleResponse](data=data)


@router.patch(
    "/llm-profiles/{profile_id}",
    response_model=OkResp[LlmProfilesBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def update_llm_profile(
    profile_id: str,
    cmd: UpdateLlmProfileCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = ai_profiles_service.update_llm_profile(profile_id, cmd)
    return OkResp[LlmProfilesBundleResponse](data=data)


@router.delete(
    "/llm-profiles/{profile_id}",
    response_model=OkResp[LlmProfilesBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def delete_llm_profile(
    profile_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = ai_profiles_service.delete_llm_profile(profile_id)
    return OkResp[LlmProfilesBundleResponse](data=data)


@router.patch(
    "/annotation-chain",
    response_model=OkResp[LlmProfilesBundleResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_annotation_chain(
    cmd: PatchAnnotationChainCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = ai_profiles_service.patch_annotation_chain(cmd)
    return OkResp[LlmProfilesBundleResponse](data=data)


@router.get(
    "/yolo-eval-prompts",
    response_model=OkResp[YoloEvalPromptsPublicResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_eval_prompts(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = db, current_user, authorization
    data = ai_profiles_service.get_yolo_eval_prompts_public()
    return OkResp[YoloEvalPromptsPublicResponse](data=data)


@router.patch(
    "/yolo-eval-prompts",
    response_model=OkResp[YoloEvalPromptsPublicResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_yolo_eval_prompts(
    cmd: PatchYoloEvalPromptsBody,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    data = ai_profiles_service.patch_yolo_eval_prompts(
        full_analysis=cmd.full_analysis,
        next_iteration=cmd.next_iteration,
    )
    return OkResp[YoloEvalPromptsPublicResponse](data=data)
