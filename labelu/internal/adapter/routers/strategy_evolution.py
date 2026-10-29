import json
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from labelu.internal.application.command.strategy_evolution import (
    CreateStrategyEvolutionTaskCommand,
    RetryStrategyEvolutionCallbackCommand,
)
from labelu.internal.application.response.base import OkResp, OkRespWithMeta
from labelu.internal.application.response.strategy_evolution import (
    StrategyEvolutionTaskDetailResponse,
    StrategyEvolutionTaskListRowResponse,
)
from labelu.internal.application.service import strategy_evolution as service
from labelu.internal.common import db as db_module
from labelu.internal.common.internal_service_auth import (
    verify_strategy_evolution_internal_request,
)

internal_router = APIRouter(
    prefix="/internal/v1/strategy-evolution",
    tags=["strategy_evolution"],
)

callback_router = APIRouter(
    prefix="/api/v1/strategy-evolution",
    tags=["strategy_evolution"],
)

# Official unified strategy-task API.  The historical internal task-type
# routes below remain available for the documented experiment-platform
# contract; this router exposes one stable create/list/detail surface that
# reuses exactly the same service and authentication behavior.
public_router = APIRouter(
    prefix="/api/v1/strategy-tasks",
    tags=["strategy_tasks"],
)


def _task_error_response(exc: service.StrategyEvolutionError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "rejected", "message": exc.message},
    )


def _gateway_response(
    *, body: Any, msg: str = "", status_value: int = 0
) -> dict[str, Any]:
    return {
        "body": body,
        "msg": msg,
        "status": status_value,
        "timestamp": service.gateway_timestamp_ms(),
    }


@internal_router.post(
    "/tasks",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_strategy_evolution_task(
    cmd: CreateStrategyEvolutionTaskCommand = Body(...),
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    try:
        return await service.accept_task(db=db, cmd=cmd)
    except service.StrategyEvolutionError as exc:
        return _task_error_response(exc)


@public_router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_strategy_task_public(
    cmd: CreateStrategyEvolutionTaskCommand = Body(...),
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    try:
        return await service.accept_task(db=db, cmd=cmd)
    except service.StrategyEvolutionError as exc:
        return _task_error_response(exc)


@public_router.get(
    "",
    response_model=OkRespWithMeta[list[StrategyEvolutionTaskListRowResponse]],
)
async def list_strategy_tasks_public(
    task_type: str | None = Query(None),
    platform_status: str | None = Query(None, alias="status"),
    q: str | None = Query(None, alias="q", max_length=128),
    keyword: str | None = Query(None),
    external_task_id: str | None = Query(None),
    model_id: str | None = Query(None, alias="modelId"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    effective_keyword = q if q is not None else keyword
    data, meta = await service.list_tasks(
        db=db,
        task_type=task_type,
        platform_status=platform_status,
        keyword=effective_keyword,
        external_task_id=external_task_id,
        model_id=model_id,
        page=page,
        size=size,
    )
    return OkRespWithMeta[list[StrategyEvolutionTaskListRowResponse]](
        data=data,
        meta_data=meta,
    )


@public_router.get(
    "/{external_task_id}",
    response_model=OkResp[StrategyEvolutionTaskDetailResponse | None],
)
async def get_strategy_task_public(
    external_task_id: str,
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    data = await service.get_task_detail(db=db, external_task_id=external_task_id)
    return OkResp[StrategyEvolutionTaskDetailResponse | None](data=data)


@internal_router.post(
    "/threshold-experiments",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_strategy_evolution_threshold_experiment(
    cmd: CreateStrategyEvolutionTaskCommand = Body(...),
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    if cmd.task_type != "threshold_experiment":
        return _task_error_response(
            service.StrategyEvolutionError(
                400,
                "task_type 必须为 threshold_experiment",
            )
        )
    return await create_strategy_evolution_task(cmd=cmd, db=db)


@internal_router.post(
    "/model-trainings",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_strategy_evolution_model_training(
    cmd: CreateStrategyEvolutionTaskCommand = Body(...),
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    if cmd.task_type != "model_capability":
        return _task_error_response(
            service.StrategyEvolutionError(
                400,
                "task_type 必须为 model_capability",
            )
        )
    return await create_strategy_evolution_task(cmd=cmd, db=db)


@internal_router.get(
    "/tasks",
    response_model=OkRespWithMeta[list[StrategyEvolutionTaskListRowResponse]],
)
async def list_strategy_evolution_tasks(
    task_type: str | None = Query(None),
    platform_status: str | None = Query(None, alias="status"),
    q: str | None = Query(None, alias="q", max_length=128),
    keyword: str | None = Query(None),
    external_task_id: str | None = Query(None),
    model_id: str | None = Query(None, alias="modelId"),
    page: int = Query(0, ge=0),
    size: int = Query(20, ge=1, le=100),
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    effective_keyword = q if q is not None else keyword
    data, meta = await service.list_tasks(
        db=db,
        task_type=task_type,
        platform_status=platform_status,
        keyword=effective_keyword,
        external_task_id=external_task_id,
        model_id=model_id,
        page=page,
        size=size,
    )
    return OkRespWithMeta[list[StrategyEvolutionTaskListRowResponse]](
        data=data,
        meta_data=meta,
    )


@internal_router.get(
    "/tasks/{external_task_id}",
    response_model=OkResp[StrategyEvolutionTaskDetailResponse | None],
)
async def get_strategy_evolution_task(
    external_task_id: str,
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    data = await service.get_task_detail(db=db, external_task_id=external_task_id)
    return OkResp[StrategyEvolutionTaskDetailResponse | None](data=data)


@internal_router.post(
    "/tasks/{external_task_id}/callback-retry",
    response_model=OkResp[StrategyEvolutionTaskDetailResponse],
)
async def retry_strategy_evolution_callback(
    external_task_id: str,
    cmd: RetryStrategyEvolutionCallbackCommand | None = Body(default=None),
    db: Session = Depends(db_module.get_db),
    _service_auth: None = Depends(verify_strategy_evolution_internal_request),
):
    try:
        data = service.retry_outbound_callback(
            db=db,
            external_task_id=external_task_id,
            callback_url=str(cmd.callback_url) if cmd and cmd.callback_url else None,
        )
    except service.StrategyEvolutionError as exc:
        return _task_error_response(exc)
    return OkResp[StrategyEvolutionTaskDetailResponse](data=data)


@callback_router.post("/platform-callbacks")
async def receive_strategy_evolution_platform_callback(
    request: Request,
    db: Session = Depends(db_module.get_db),
):
    try:
        raw_body = await request.body()
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise service.StrategyEvolutionError(
                status.HTTP_400_BAD_REQUEST,
                "第三方回调请求体必须是有效 JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise service.StrategyEvolutionError(
                status.HTTP_400_BAD_REQUEST,
                "第三方回调请求体必须是 JSON 对象",
            )
        service.verify_platform_callback_request(
            payload=payload,
            raw_body=raw_body,
            headers=request.headers,
        )
        data = await service.handle_platform_callback(db=db, payload=payload)
    except service.StrategyEvolutionError as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content=_gateway_response(
                body=None, msg=exc.message, status_value=exc.status_code
            ),
        )
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content=_gateway_response(
                body=None,
                msg="策略演化平台回调保存失败",
                status_value=status.HTTP_502_BAD_GATEWAY,
            ),
        )
    return _gateway_response(body=data.model_dump())
