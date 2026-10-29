from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from labelu.internal.application.response.base import OkResp
from labelu.internal.application.service import third_party_calls as service
from labelu.internal.common import db as db_module
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/third-party-calls", tags=["third_party_calls"])


@router.get("/summary", response_model=OkResp[dict[str, Any]])
def get_summary(
    project_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[dict[str, Any]](data=service.summary(db, project_id=project_id))


@router.get("/chains", response_model=OkResp[dict[str, Any]])
def list_chains(
    project_id: int | None = Query(default=None, gt=0),
    source_system: str | None = Query(default=None),
    record_type: str | None = Query(default=None),
    business_type: str | None = Query(default=None),
    health: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    keyword: str | None = Query(default=None),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[dict[str, Any]](
        data=service.list_chains(
            db,
            source_system=source_system,
            record_type=record_type,
            business_type=business_type,
            health=health,
            status=status_value,
            keyword=keyword,
            page=page,
            size=size,
            project_id=project_id,
        )
    )


@router.get("/audit-events", response_model=OkResp[dict[str, Any]])
def list_audit_events(
    project_id: int | None = Query(default=None, gt=0),
    source_system: str | None = Query(default=None),
    operation: str | None = Query(default=None),
    business_type: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    hours: int = Query(default=168, ge=1, le=24 * 365),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=30, ge=1, le=100),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[dict[str, Any]](
        data=service.list_audit_events(
            db,
            source_system=source_system,
            operation=operation,
            business_type=business_type,
            outcome=outcome,
            keyword=keyword,
            hours=hours,
            page=page,
            size=size,
            project_id=project_id,
        )
    )


@router.get("/chains/{record_type}/{record_id}", response_model=OkResp[dict[str, Any]])
def get_chain_detail(
    record_type: str,
    record_id: str,
    project_id: int | None = Query(default=None, gt=0),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    data = service.get_chain_detail(
        db,
        record_type=record_type,
        record_id=record_id,
        project_id=project_id,
    )
    if data is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=404,
            message="未找到第三方业务链路",
        )
    return OkResp[dict[str, Any]](data=data)
