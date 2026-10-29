import json

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_edge_device_job, crud_export_job
from labelu.internal.application.command.edge_device_job import (
    ClaimEdgeDeviceJobCommand,
    CreateEdgeDeviceJobCommand,
    SubmitEdgeDeviceJobResultCommand,
)
from labelu.internal.application.response.edge_device_job import (
    EdgeDeviceClaimResponse,
    EdgeDeviceJobResponse,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.phase_one_contract import ensure_legacy_control_plane_retired
from labelu.internal.domain.models.export_job import ExportStatus
from labelu.internal.domain.models.user import User


def _decode_json(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _download_url(row) -> str:
    return f"/api/v1/tasks/{row.task_id}/samples/export/{row.export_job_id}/download"


def _to_response(row, include_download: bool = True) -> EdgeDeviceJobResponse:
    return EdgeDeviceJobResponse(
        id=row.id,
        export_job_id=row.export_job_id,
        task_id=row.task_id,
        status=row.status,
        device_id=row.device_id,
        payload=_decode_json(row.payload, {}),
        result=_decode_json(row.result, None),
        error_message=row.error_message,
        download_url=_download_url(row) if include_download else None,
        claimed_at=row.claimed_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def create(
    db: Session, *, cmd: CreateEdgeDeviceJobCommand, current_user: User
) -> EdgeDeviceJobResponse:
    ensure_legacy_control_plane_retired()
    export_job = crud_export_job.get(db=db, job_id=cmd.export_job_id)
    if not export_job or export_job.status != ExportStatus.COMPLETED.value:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if export_job.task_id is None:
        raise LabelUException(
            code=ErrorCode.CODE_61000_NO_DATA,
            status_code=status.HTTP_409_CONFLICT,
            message="导出作业未绑定父任务，无法创建边端作业",
        )
    export_job_id = export_job.id
    task_id = export_job.task_id

    with begin_transaction(db):
        row = crud_edge_device_job.create(
            db,
            export_job_id=export_job_id,
            task_id=task_id,
            user_id=current_user.id,
            payload=json.dumps(cmd.payload, ensure_ascii=False),
        )
    return _to_response(row)


async def claim_next(
    db: Session, *, cmd: ClaimEdgeDeviceJobCommand
) -> EdgeDeviceClaimResponse:
    ensure_legacy_control_plane_retired()
    with begin_transaction(db):
        row = crud_edge_device_job.claim_next(db, device_id=cmd.device_id)
    if row is None:
        return EdgeDeviceClaimResponse(job=None)
    return EdgeDeviceClaimResponse(job=_to_response(row))


async def submit_result(
    db: Session, *, job_id: int, cmd: SubmitEdgeDeviceJobResultCommand
) -> EdgeDeviceJobResponse:
    ensure_legacy_control_plane_retired()
    with begin_transaction(db):
        row = crud_edge_device_job.get(db=db, job_id=job_id)
        if row is None:
            raise LabelUException(
                code=ErrorCode.CODE_61000_NO_DATA,
                status_code=status.HTTP_404_NOT_FOUND,
            )
        export_job = crud_export_job.get(db=db, job_id=row.export_job_id)
        if export_job is None or int(export_job.task_id or 0) != int(row.task_id):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="边端作业与导出作业的父任务不一致",
            )
        if row.status != "RUNNING":
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="边端任务当前不允许回传结果",
            )
        if row.device_id != cmd.device_id:
            raise LabelUException(
                code=ErrorCode.CODE_30001_NO_PERMISSION,
                status_code=status.HTTP_403_FORBIDDEN,
                message="仅领取该任务的设备可回传结果",
            )
        row = crud_edge_device_job.finish_if_claimed(
            db,
            job_id=job_id,
            device_id=cmd.device_id,
            status=cmd.status,
            result=json.dumps(cmd.result, ensure_ascii=False),
            error_message=cmd.error_message,
        )
        if row is None:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=status.HTTP_409_CONFLICT,
                message="边端任务已被其他请求处理",
            )
    return _to_response(row)
