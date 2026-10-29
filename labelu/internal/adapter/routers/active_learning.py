from typing import List

from fastapi import APIRouter, Depends, Query, Security
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from labelu.internal.application.command.active_learning import AcceptActiveLearningBatchCommand, CreateActiveLearningBatchCommand, RebuildFeedbackClustersCommand
from labelu.internal.application.response.active_learning import ActiveLearningBatchResponse, FeedbackClusterResponse
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.service import active_learning as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/active-learning", tags=["active_learning"])


@router.post("/clusters/rebuild", response_model=OkResp[List[FeedbackClusterResponse]])
async def rebuild(cmd: RebuildFeedbackClustersCommand, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[List[FeedbackClusterResponse]](data=await service.rebuild_clusters(db, cmd, current_user))


@router.get("/clusters", response_model=OkResp[List[FeedbackClusterResponse]])
async def clusters(project_id: int | None = Query(default=None, gt=0), authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[List[FeedbackClusterResponse]](data=await service.list_clusters(db, project_id, current_user))


@router.get("/clusters/search", response_model=OkRespWithMeta[List[FeedbackClusterResponse]])
async def search_clusters(
    project_id: int | None = Query(default=None, gt=0),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=500),
    q: str | None = Query(default=None, max_length=128),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    rows, total = await service.list_clusters_page(
        db, project_id, current_user, page, size, search=q
    )
    return OkRespWithMeta[List[FeedbackClusterResponse]](
        meta_data=MetaData(total=total, page=page, size=len(rows)), data=rows
    )


@router.post("/batches", response_model=OkResp[ActiveLearningBatchResponse], status_code=201)
async def create_batch(cmd: CreateActiveLearningBatchCommand, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[ActiveLearningBatchResponse](data=await service.create_batch(db, cmd, current_user))


@router.get("/batches/{batch_id}", response_model=OkResp[ActiveLearningBatchResponse])
async def get_batch(batch_id: int, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    row = await service.get_batch(db, batch_id, current_user)
    return OkResp[ActiveLearningBatchResponse](data=row)


@router.post("/batches/{batch_id}/accept", response_model=OkResp[ActiveLearningBatchResponse])
async def accept(batch_id: int, cmd: AcceptActiveLearningBatchCommand, authorization: HTTPAuthorizationCredentials = Security(security), db: Session = Depends(db_module.get_db), current_user: User = Depends(get_current_user)):
    return OkResp[ActiveLearningBatchResponse](data=await service.accept_batch(db, batch_id, cmd, current_user))
