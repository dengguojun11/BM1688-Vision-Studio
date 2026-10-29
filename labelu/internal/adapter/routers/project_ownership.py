"""Administrative, read-only legacy project ownership audit."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from labelu.internal.application.response.base import OkResp
from labelu.internal.application.service import project_ownership_audit as service
from labelu.internal.common import db as db_module
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User


router = APIRouter(
    prefix="/project-ownership", tags=["project_ownership"], include_in_schema=False
)


@router.get("/audit", response_model=OkResp[dict])
def audit(
    include_ids: bool = Query(
        False,
        description="是否返回每类待确认记录的有限 ID 样本；不会暴露业务正文",
    ),
    sample_limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    """Return a deterministic report without changing historical ownership."""
    _ = current_user
    return OkResp[dict](
        data=service.audit_project_ownership(
            db, include_ids=include_ids, sample_limit=sample_limit
        )
    )
