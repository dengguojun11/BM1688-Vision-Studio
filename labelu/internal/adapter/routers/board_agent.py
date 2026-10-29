from typing import Optional

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from labelu.internal.application.command.board_agent import (
    BoardAgentHeartbeatCommand,
    BoardAgentProgressCommand,
    BoardAgentReportCommand,
)
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.board_agent import (
    BoardAgentJobResponse,
    BoardValidationStatusResponse,
)
from labelu.internal.application.service import board_agent as board_agent_service
from labelu.internal.adapter.routers._phase_one_gate import hide_router_from_openapi
from labelu.internal.common import db as db_module

router = APIRouter(
    prefix="/board-agent",
    tags=["BM1688 真机验证"],
)


def _require_board_token(
    x_board_agent_token: Optional[str] = Header(None, alias="X-Board-Agent-Token"),
) -> None:
    board_agent_service.verify_board_agent_token(x_board_agent_token or "")


@router.post(
    "/heartbeat",
    response_model=OkResp[dict],
    status_code=status.HTTP_200_OK,
)
async def board_agent_heartbeat(
    cmd: BoardAgentHeartbeatCommand,
    _: None = Depends(_require_board_token),
    x_board_agent_id: Optional[str] = Header(None, alias="X-Board-Agent-Id"),
):
    data = board_agent_service.record_board_agent_heartbeat(
        agent_id=x_board_agent_id or "",
        environment_ready=cmd.environment_ready,
        capabilities=cmd.capabilities,
        message=cmd.message,
    )
    return OkResp[dict](data=data)


@router.get(
    "/jobs/next",
    response_model=OkResp[Optional[BoardAgentJobResponse]],
    status_code=status.HTTP_200_OK,
)
async def board_agent_claim_next(
    _: None = Depends(_require_board_token),
    x_board_agent_id: Optional[str] = Header(None, alias="X-Board-Agent-Id"),
    db: Session = Depends(db_module.get_db),
):
    if board_agent_service.unified_board_agent_enabled():
        data = board_agent_service.claim_next_unified_job(
            db, agent_id=x_board_agent_id or ""
        )
    else:
        normalized_agent = (x_board_agent_id or "").strip()
        if normalized_agent:
            data = board_agent_service.claim_next_job(db, agent_id=normalized_agent)
        else:
            data = board_agent_service.claim_next_job(db)
    return OkResp[Optional[BoardAgentJobResponse]](data=data)


@router.get(
    "/jobs/{validation_id}/models.zip",
    status_code=status.HTTP_200_OK,
)
async def board_agent_download_models(
    validation_id: int,
    _: None = Depends(_require_board_token),
    x_board_agent_id: Optional[str] = Header(None, alias="X-Board-Agent-Id"),
    x_board_agent_task_token: Optional[str] = Header(
        None, alias="X-Board-Agent-Task-Token"
    ),
    db: Session = Depends(db_module.get_db),
):
    if board_agent_service.unified_board_agent_enabled():
        zip_path = board_agent_service.build_unified_models_zip_path(
            db,
            validation_id=validation_id,
            agent_id=x_board_agent_id or "",
            task_token=x_board_agent_task_token or "",
        )
        export_id = board_agent_service.get_unified_validation_for_agent(
            db,
            validation_id=validation_id,
            agent_id=x_board_agent_id or "",
            task_token=x_board_agent_task_token or "",
        ).export_id
    else:
        row = board_agent_service.get_validation_for_agent(db, validation_id)
        export_id = row.export_id
        zip_path = board_agent_service.build_models_zip_path(export_id)
    return FileResponse(
        path=str(zip_path.resolve()),
        filename=f"labelu_export_{export_id}_models.zip",
        media_type="application/zip",
    )


@router.post(
    "/jobs/{validation_id}/progress",
    response_model=OkResp[BoardValidationStatusResponse],
    status_code=status.HTTP_200_OK,
)
async def board_agent_submit_progress(
    validation_id: int,
    cmd: BoardAgentProgressCommand,
    _: None = Depends(_require_board_token),
    x_board_agent_id: Optional[str] = Header(None, alias="X-Board-Agent-Id"),
    x_board_agent_task_token: Optional[str] = Header(
        None, alias="X-Board-Agent-Task-Token"
    ),
    db: Session = Depends(db_module.get_db),
):
    if board_agent_service.unified_board_agent_enabled():
        data = board_agent_service.submit_unified_progress(
            db,
            validation_id=validation_id,
            agent_id=x_board_agent_id or "",
            task_token=x_board_agent_task_token or "",
            stage=cmd.stage,
            message=cmd.message,
            detail=cmd.detail,
        )
    else:
        data = board_agent_service.submit_progress(
            db,
            validation_id=validation_id,
            stage=cmd.stage,
            message=cmd.message,
            detail=cmd.detail,
        )
    return OkResp[BoardValidationStatusResponse](data=data)


@router.post(
    "/jobs/{validation_id}/report",
    response_model=OkResp[BoardValidationStatusResponse],
    status_code=status.HTTP_200_OK,
)
async def board_agent_submit_report(
    validation_id: int,
    cmd: BoardAgentReportCommand,
    _: None = Depends(_require_board_token),
    x_board_agent_id: Optional[str] = Header(None, alias="X-Board-Agent-Id"),
    x_board_agent_task_token: Optional[str] = Header(
        None, alias="X-Board-Agent-Task-Token"
    ),
    db: Session = Depends(db_module.get_db),
):
    if board_agent_service.unified_board_agent_enabled():
        data = board_agent_service.submit_unified_report(
            db,
            validation_id=validation_id,
            agent_id=x_board_agent_id or "",
            task_token=x_board_agent_task_token or "",
            status_value=cmd.status,
            report=cmd.report,
            error_message=cmd.error_message,
        )
    else:
        data = board_agent_service.submit_report(
            db,
            validation_id=validation_id,
            status_value=cmd.status,
            report=cmd.report,
            error_message=cmd.error_message,
        )
    return OkResp[BoardValidationStatusResponse](data=data)


hide_router_from_openapi(router)
