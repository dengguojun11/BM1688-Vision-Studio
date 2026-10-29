from fastapi import APIRouter, Depends, File, Security, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.media_upload import CreateMediaUploadSessionCommand
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.media_upload import MediaUploadSessionResponse
from labelu.internal.application.service import media_upload as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User


router = APIRouter(prefix="/data-governance", tags=["data-governance"])


@router.post("/quarantine/cleanup", response_model=OkResp[dict])
def cleanup_quarantine(
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    del current_user
    return OkResp[dict](data={"cleaned": service.cleanup_expired_quarantine(db)})


@router.post(
    "/upload-sessions",
    response_model=OkResp[MediaUploadSessionResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    cmd: CreateMediaUploadSessionCommand,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[MediaUploadSessionResponse](data=await service.create_session(db, cmd, current_user))


@router.get(
    "/upload-sessions/{session_id}",
    response_model=OkResp[MediaUploadSessionResponse],
)
async def get_session(
    session_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[MediaUploadSessionResponse](data=service.get_session(db, session_id, current_user))


@router.put(
    "/upload-sessions/{session_id}/chunks/{chunk_no}",
    response_model=OkResp[MediaUploadSessionResponse],
)
async def upload_chunk(
    session_id: str,
    chunk_no: int,
    file: UploadFile = File(...),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[MediaUploadSessionResponse](data=await service.upload_chunk(db, session_id, chunk_no, file, current_user))


@router.post(
    "/upload-sessions/{session_id}/complete",
    response_model=OkResp[MediaUploadSessionResponse],
)
async def complete(
    session_id: str,
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    return OkResp[MediaUploadSessionResponse](data=await service.complete(db, session_id, current_user))
