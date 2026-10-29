from fastapi import APIRouter, Depends, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.yolo_eval_prompts_cmd import PatchYoloEvalPromptsCommand
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.yolo_eval import YoloEvalPromptsResponse
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.common.yolo_eval_prompts import (
    load_yolo_eval_prompts,
    prompts_file_path,
    save_yolo_eval_prompts,
)
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/yolo-eval-prompts", tags=["yolo-eval"])


@router.get(
    "",
    response_model=OkResp[YoloEvalPromptsResponse],
    status_code=status.HTTP_200_OK,
)
async def get_yolo_eval_prompts(
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = db, current_user, authorization
    data = load_yolo_eval_prompts()
    return OkResp[YoloEvalPromptsResponse](
        data=YoloEvalPromptsResponse(
            full_analysis=data["full_analysis"],
            next_iteration=data["next_iteration"],
            config_backend_label="MySQL 治理表（system_config_*）",
            prompts_file=str(prompts_file_path().resolve()),
        )
    )


@router.patch(
    "",
    response_model=OkResp[YoloEvalPromptsResponse],
    status_code=status.HTTP_200_OK,
)
async def patch_yolo_eval_prompts(
    cmd: PatchYoloEvalPromptsCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    _ = db, current_user, authorization
    saved = save_yolo_eval_prompts(
        full_analysis=cmd.full_analysis,
        next_iteration=cmd.next_iteration,
    )
    return OkResp[YoloEvalPromptsResponse](
        data=YoloEvalPromptsResponse(
            full_analysis=saved["full_analysis"],
            next_iteration=saved["next_iteration"],
            config_backend_label="MySQL 治理表（system_config_*）",
            prompts_file=str(prompts_file_path().resolve()),
        )
    )
