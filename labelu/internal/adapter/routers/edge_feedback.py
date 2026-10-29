from typing import List

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.edge_feedback import CreateInferenceEventCommand, ReviewHardExampleCommand
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.response.edge_feedback import HardExampleResponse
from labelu.internal.application.service import edge_feedback as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/edge-feedback", tags=["edge_feedback"])


@router.post("/events", response_model=OkResp[HardExampleResponse], status_code=status.HTTP_201_CREATED)
async def ingest(cmd: CreateInferenceEventCommand, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[HardExampleResponse](data=await service.ingest(db, cmd, current_user))


@router.get("/hard-examples", response_model=OkRespWithMeta[List[HardExampleResponse]])
async def list_hard(
    project_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=50, ge=1, le=200),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, alias="q", max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    data, total = await service.list_by(
        db, project_id, page, size, status_filter, current_user, search=search
    )
    return OkRespWithMeta[List[HardExampleResponse]](meta_data=MetaData(total=total, page=page, size=len(data)), data=data)


@router.get("/hard-examples/{hard_id}", response_model=OkResp[HardExampleResponse])
async def get_hard(hard_id: int, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[HardExampleResponse](data=await service.get_by_id(db, hard_id, current_user))


@router.post("/hard-examples/{hard_id}/review", response_model=OkResp[HardExampleResponse])
async def review(hard_id: int, cmd: ReviewHardExampleCommand, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[HardExampleResponse](data=await service.review(db, hard_id, cmd, current_user))
