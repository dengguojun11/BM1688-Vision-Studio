from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from labelu.internal.application.command.strategy_task_integration import (
    CreateStrategyTaskIntegrationCommand,
)
from labelu.internal.application.response.base import OkResp
from labelu.internal.application.response.strategy_task_integration import (
    StrategyTaskIntegrationResponse,
)
from labelu.internal.application.service import strategy_task_integration as service
from labelu.internal.application.service import experiment_dataset_import as experiment_import_service
from labelu.internal.common import db as db_module
from labelu.internal.common.third_party_auth import verify_third_party_request


router = APIRouter(
    prefix="/integrations/strategy-tasks",
    tags=["strategy_task_integration"],
)


@router.post(
    "",
    response_model=OkResp[StrategyTaskIntegrationResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_strategy_task(
    cmd: CreateStrategyTaskIntegrationCommand,
    request: Request,
    db: Session = Depends(db_module.get_db),
):
    await verify_third_party_request(
        request,
        required=str(cmd.schema_version).lower().startswith("v2"),
        db=db,
        source_system=cmd.source_system,
    )
    data = await service.create(
        db=db,
        cmd=cmd,
    )
    return OkResp[StrategyTaskIntegrationResponse](data=data)


@router.get(
    "/{external_task_id}",
    response_model=OkResp[StrategyTaskIntegrationResponse | None],
)
async def get_strategy_task(
    external_task_id: str,
    db: Session = Depends(db_module.get_db),
):
    row = service.crud_strategy_task_integration.get_by_external_task_id(
        db=db, external_task_id=external_task_id
    )
    if row is None:
        return OkResp[StrategyTaskIntegrationResponse | None](data=None)
    package_id = str(row.source_package_id or "").strip()
    import_row = (
        service.crud_experiment_dataset_import.get_by_package_id(
            db=db, package_id=package_id
        )
        if package_id
        else None
    )
    data = service._legacy_response_from_import(row, import_row, created=False, db=db)
    return OkResp[StrategyTaskIntegrationResponse | None](data=data)
