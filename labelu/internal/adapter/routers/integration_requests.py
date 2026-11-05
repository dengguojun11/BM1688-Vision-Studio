from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from labelu.internal.application.command.integration_requests import (
    ApprovePublishCommand,
    BindPackagesCommand,
    CreateOptimizationRequestCommand,
    CreateSceneTrainingCommand,
    CreateThresholdExperimentCommand,
    PublishDecisionUpsertCommand,
)
from labelu.internal.application.response.base import MetaData, OkResp, OkRespWithMeta
from labelu.internal.application.service import integration_requests as service
from labelu.internal.common import db as db_module
from labelu.internal.common.db import session_scope
from labelu.internal.dependencies.integration_auth import get_integration_actor
from labelu.internal.dependencies.user import require_system_admin
from labelu.internal.domain.models.user import User

router = APIRouter(prefix="/api/v2/integrations", tags=["integration_requests"])


def _forced_command(cmd: CreateOptimizationRequestCommand, request_type: str) -> CreateOptimizationRequestCommand:
    payload = cmd.model_dump()
    payload["request_type"] = request_type
    return CreateOptimizationRequestCommand(**payload)


@router.post("/requests", response_model=OkResp[dict[str, Any]], status_code=status.HTTP_201_CREATED)
async def create_request(
    cmd: CreateOptimizationRequestCommand,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](data=service.create_request(db, cmd, current_user))


@router.post(
    "/threshold-experiments",
    response_model=OkResp[dict[str, Any]],
    status_code=status.HTTP_201_CREATED,
)
async def create_threshold_experiment(
    cmd: CreateThresholdExperimentCommand,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_integration_actor),
):
    command = _forced_command(cmd, "threshold_experiment")
    return OkResp[dict[str, Any]](data=service.create_request(db, command, current_user))


@router.post(
    "/scene-trainings",
    response_model=OkResp[dict[str, Any]],
    status_code=status.HTTP_201_CREATED,
)
async def create_scene_training(
    cmd: CreateSceneTrainingCommand,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_integration_actor),
):
    command = _forced_command(cmd, "scene_training")
    return OkResp[dict[str, Any]](data=service.create_request(db, command, current_user))


@router.get("/requests", response_model=OkRespWithMeta[list[Any]])
async def list_requests(
    status_value: str | None = Query(default=None, alias="status"),
    stage: str | None = Query(default=None),
    request_type: str | None = Query(default=None),
    source_system: str | None = Query(default=None),
    model_id: str | None = Query(default=None),
    external_request_id: str | None = Query(default=None),
    keyword: str | None = Query(default=None, alias="q"),
    page: int = Query(default=0, ge=0),
    size: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    rows, total = service.list_requests(
        db,
        status_value=status_value,
        stage=stage,
        request_type=request_type,
        source_system=source_system,
        model_id=model_id,
        external_request_id=external_request_id,
        keyword=keyword,
        page=page,
        size=size,
    )
    return OkRespWithMeta[list[Any]](
        data=rows,
        meta_data=MetaData(total=total, page=page, size=len(rows)),
    )


@router.get("/requests/summary", response_model=OkResp[dict[str, Any]])
async def request_summary(
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](data=service.summary(db))


@router.get("/requests/stream")
async def stream_requests(
    request: Request,
    status_value: str | None = Query(default=None, alias="status"),
    stage: str | None = Query(default=None),
    request_type: str | None = Query(default=None),
    source_system: str | None = Query(default=None),
    model_id: str | None = Query(default=None),
    keyword: str | None = Query(default=None, alias="q"),
    size: int = Query(default=50, ge=1, le=100),
    _current_user: User = Depends(get_integration_actor),
):
    """Server-Sent Events snapshot stream for the optimization console.

    The stream intentionally sends whole snapshots rather than per-row diffs:
    the business screen needs the summary, coverage and stage together, and a
    snapshot is idempotent when an EventSource client reconnects.
    """

    async def event_generator():
        last_signature = ""
        while True:
            if await request.is_disconnected():
                break
            try:
                with session_scope() as session:
                    payload = service.stream_snapshot(
                        session,
                        status_value=status_value,
                        stage=stage,
                        request_type=request_type,
                        source_system=source_system,
                        model_id=model_id,
                        keyword=keyword,
                        size=size,
                    )
                signature = hashlib.sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode(
                        "utf-8"
                    )
                ).hexdigest()
                if signature != last_signature:
                    yield (
                        "event: snapshot\n"
                        f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
                    )
                    last_signature = signature
                else:
                    yield ": ping\n\n"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # stream must not crash the ASGI worker
                yield (
                    "event: error\n"
                    f"data: {json.dumps({'message': str(exc)[:300]}, ensure_ascii=False)}\n\n"
                )
            await asyncio.sleep(3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/data-availability", response_model=OkResp[dict[str, Any]])
async def data_availability(
    model_id: str | None = Query(default=None),
    event_label: str | None = Query(default=None),
    event_time_from: datetime | None = Query(default=None),
    event_time_to: datetime | None = Query(default=None),
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](
        data=service.data_availability(
            db,
            model_id=model_id,
            event_label=event_label,
            event_time_from=event_time_from,
            event_time_to=event_time_to,
        )
    )


@router.get("/requests/alerts", response_model=OkResp[dict[str, Any]])
async def request_alerts(
    stale_minutes: int = Query(default=30, ge=1, le=1440),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](data=service.alerts(db, stale_minutes=stale_minutes, limit=limit))


@router.get("/requests/by-external/{external_request_id}", response_model=OkResp[dict[str, Any]])
async def get_request_by_external(
    external_request_id: str,
    source_system: str | None = Query(default=None),
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](
        data=service.get_request_by_external(db, external_request_id, source_system)
    )


@router.get("/requests/by-image/{external_image_id}", response_model=OkResp[list[dict[str, Any]]])
async def get_requests_by_image(
    external_image_id: str,
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[list[dict[str, Any]]](
        data=service.get_requests_by_image(db, external_image_id)
    )


@router.get("/requests/by-package/{package_id}", response_model=OkResp[list[dict[str, Any]]])
async def get_requests_by_package(
    package_id: str,
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[list[dict[str, Any]]](
        data=service.get_requests_by_package(db, package_id)
    )


@router.get("/requests/{request_id}", response_model=OkResp[dict[str, Any]])
async def get_request(
    request_id: int,
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](data=service.get_request(db, request_id))


@router.get(
    "/requests/{request_id}/original-request",
    response_model=OkResp[dict[str, Any]],
)
async def get_original_request(
    request_id: int,
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(require_system_admin),
):
    """Admin-only raw/composed third-party request view (not polled)."""

    return OkResp[dict[str, Any]](data=service.original_request(db, request_id))


@router.post("/requests/{request_id}/refresh", response_model=OkResp[dict[str, Any]])
async def refresh_request(
    request_id: int,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](
        data=service.refresh_request(db, request_id, int(current_user.id))
    )


@router.post("/requests/{request_id}/start-pipeline", response_model=OkResp[dict[str, Any]])
async def start_pipeline(
    request_id: int,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](
        data=await service.start_strategy_flow(db, request_id, int(current_user.id))
    )


@router.get("/requests/{request_id}/coverage", response_model=OkResp[dict[str, Any]])
async def get_request_coverage(
    request_id: int,
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](data=service.coverage_for_request(db, request_id))


@router.get("/requests/{request_id}/timeline", response_model=OkResp[dict[str, Any]])
async def get_request_timeline(
    request_id: int,
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](data=service.timeline_for_request(db, request_id))


@router.get("/requests/{request_id}/callbacks", response_model=OkResp[dict[str, Any]])
async def get_request_callbacks(
    request_id: int,
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](data=service.callbacks_for_request(db, request_id))


@router.post("/requests/{request_id}/packages", response_model=OkResp[dict[str, Any]])
async def bind_request_packages(
    request_id: int,
    cmd: BindPackagesCommand,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](data=service.bind_packages(db, request_id, cmd, current_user))


@router.post("/requests/{request_id}/cancel", response_model=OkResp[dict[str, Any]])
async def cancel_request(
    request_id: int,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](data=service.cancel_request(db, request_id, current_user))


@router.get("/requests/{request_id}/publish-decision", response_model=OkResp[dict[str, Any]])
async def get_publish_decision(
    request_id: int,
    db: Session = Depends(db_module.get_db),
    _current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](data=service.get_request(db, request_id))


@router.post("/requests/{request_id}/publish-decision", response_model=OkResp[dict[str, Any]])
async def upsert_publish_decision(
    request_id: int,
    cmd: PublishDecisionUpsertCommand,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(get_integration_actor),
):
    return OkResp[dict[str, Any]](
        data=service.upsert_publish_decision(db, request_id, cmd, current_user)
    )


@router.post(
    "/requests/{request_id}/publish-decision/approve",
    response_model=OkResp[dict[str, Any]],
)
async def approve_publish_decision(
    request_id: int,
    cmd: ApprovePublishCommand,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[dict[str, Any]](
        data=service.approve_publish(db, request_id, cmd, current_user)
    )


@router.post("/requests/{request_id}/publish", response_model=OkResp[dict[str, Any]])
async def publish_request(
    request_id: int,
    db: Session = Depends(db_module.get_db),
    current_user: User = Depends(require_system_admin),
):
    return OkResp[dict[str, Any]](
        data=await service.publish_request(db, request_id, current_user)
    )
