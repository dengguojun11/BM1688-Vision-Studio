from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from labelu.internal.application.response.base import OkResp
from labelu.internal.application.service import integration_accumulation_config as service
from labelu.internal.common import db as db_module
from labelu.internal.dependencies.user import get_current_user, require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/integration-settings", tags=["integration_settings"])


class AccumulationConfigCommand(BaseModel):
    min_sample_count: int = Field(ge=1, le=100000)


@router.get("/accumulation", response_model=OkResp[dict[str, Any]])
async def get_accumulation_config(
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_current_user),
):
    return OkResp[dict[str, Any]](data=service.get_config(db))


@router.put("/accumulation", response_model=OkResp[dict[str, Any]])
async def update_accumulation_config(
    cmd: AccumulationConfigCommand,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[dict[str, Any]](
        data=service.save_min_sample_count(db, cmd.min_sample_count, current_user)
    )
