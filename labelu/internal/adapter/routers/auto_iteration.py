from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from labelu.internal.application.command.auto_iteration import (
    UpdateAutoIterationSettingsCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.service import auto_iteration_settings as service
from labelu.internal.common import db as db_module
from labelu.internal.dependencies.user import get_current_user
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/auto-iteration-settings", tags=["auto_iteration"])


def _cmd_values(cmd: UpdateAutoIterationSettingsCommand) -> Dict[str, Any]:
    payload = cmd.dict(exclude={"reason", "expected_values"})
    return {key: value for key, value in payload.items() if value is not None}


@router.get("", response_model=OkResp[dict])
def get_auto_iteration_settings(
    current_user: User = Depends(get_current_user),
):
    return OkResp[dict](data=service.settings_payload(user=current_user))


@router.patch("", response_model=OkResp[dict])
def patch_auto_iteration_settings(
    cmd: UpdateAutoIterationSettingsCommand,
    current_user: User = Depends(get_current_user),
):
    data = service.update_settings(
        user=current_user,
        values=_cmd_values(cmd),
        reason=cmd.reason,
        expected_values=cmd.expected_values,
    )
    return OkResp[dict](data=data)


@router.get("/history", response_model=OkRespWithMeta[List[dict]])
def list_auto_iteration_history(
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    item_key: Optional[str] = Query(None),
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_current_user),
):
    rows = service.history_payload(db, limit=200, item_key=item_key)
    total = len(rows)
    start = page * size
    data = rows[start : start + size]
    return OkRespWithMeta[List[dict]](
        data=data,
        meta_data=MetaData(total=total, page=page, size=len(data)),
    )
