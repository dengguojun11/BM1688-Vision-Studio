from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from labelu.internal.application.response.base import OkResp
from labelu.internal.application.service import data_analysis as service
from labelu.internal.common import db as db_module
from labelu.internal.common.security import security
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter()


class DataAnalysisResponse(BaseModel):
    summary: Dict[str, Any] = Field(default_factory=dict)
    state_counts: List[Dict[str, Any]] = Field(default_factory=list)
    label_distribution: List[Dict[str, Any]] = Field(default_factory=list)
    contracts: List[Dict[str, Any]] = Field(default_factory=list)
    gaps: List[Dict[str, Any]] = Field(default_factory=list)
    global_tools: List[Dict[str, Any]] = Field(default_factory=list)


@router.get(
    "/data-analysis/overview",
    response_model=OkResp[DataAnalysisResponse],
    status_code=status.HTTP_200_OK,
)
def data_analysis_overview(
    target_per_label: int = Query(100, ge=1, le=100000),
    authorization: HTTPAuthorizationCredentials = Security(security),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    _ = authorization
    data = service.get_data_analysis(
        db,
        current_user,
        target_per_label=target_per_label,
    )
    return OkResp[DataAnalysisResponse](data=DataAnalysisResponse(**data))
