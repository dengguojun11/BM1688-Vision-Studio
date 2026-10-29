from __future__ import annotations

from datetime import datetime, timezone

import boto3
from botocore.config import Config
from fastapi import status

from labelu.internal.application.command.strategy_integration_s3_config import (
    CreateStrategyIntegrationS3ConnectionCommand,
    SetActiveStrategyIntegrationS3ConnectionCommand,
    UpdateStrategyIntegrationS3ConnectionCommand,
)
from labelu.internal.application.response.strategy_integration_s3_config import (
    StrategyIntegrationS3ConnectionResponse,
    StrategyIntegrationS3ConnectionTestResponse,
    StrategyIntegrationS3ConnectionsBundleResponse,
)
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.strategy_integration_s3_config import (
    create_connection,
    delete_connection,
    ensure_s3_connection_store_initialized,
    load_s3_connection_store,
    set_active_connection,
    update_connection,
)
from labelu.internal.common.config import settings


def _error(exc: ValueError, *, not_found: bool = False) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=status.HTTP_404_NOT_FOUND if not_found else status.HTTP_400_BAD_REQUEST,
        message=str(exc),
    )


def _to_response(record: dict, *, active_id: str) -> StrategyIntegrationS3ConnectionResponse:
    return StrategyIntegrationS3ConnectionResponse(
        id=str(record.get("id") or ""),
        name=str(record.get("name") or ""),
        endpoint=str(record.get("endpoint") or ""),
        remark=str(record.get("remark") or ""),
        is_active=str(record.get("id") or "") == active_id,
        created_at=record.get("created_at"),
        updated_at=record.get("updated_at"),
    )


def list_connections() -> StrategyIntegrationS3ConnectionsBundleResponse:
    store = ensure_s3_connection_store_initialized()
    active_id = str(store.get("active_connection_id") or "")
    return StrategyIntegrationS3ConnectionsBundleResponse(
        active_connection_id=active_id,
        connections=[
            _to_response(item, active_id=active_id)
            for item in list(store.get("connections") or [])
        ],
    )


def create(cmd: CreateStrategyIntegrationS3ConnectionCommand) -> StrategyIntegrationS3ConnectionResponse:
    try:
        record = create_connection(
            name=cmd.name,
            endpoint=cmd.endpoint,
            remark=cmd.remark,
            set_active=cmd.set_active,
        )
    except ValueError as exc:
        raise _error(exc) from exc
    store = ensure_s3_connection_store_initialized()
    return _to_response(record, active_id=str(store.get("active_connection_id") or ""))


def update(
    connection_id: str,
    cmd: UpdateStrategyIntegrationS3ConnectionCommand,
) -> StrategyIntegrationS3ConnectionResponse:
    try:
        record = update_connection(
            connection_id,
            name=cmd.name,
            endpoint=cmd.endpoint,
            remark=cmd.remark,
        )
    except ValueError as exc:
        raise _error(exc, not_found="不存在" in str(exc)) from exc
    store = ensure_s3_connection_store_initialized()
    return _to_response(record, active_id=str(store.get("active_connection_id") or ""))


def delete(connection_id: str) -> StrategyIntegrationS3ConnectionsBundleResponse:
    try:
        delete_connection(connection_id)
    except ValueError as exc:
        raise _error(exc, not_found="不存在" in str(exc)) from exc
    return list_connections()


def set_active(
    cmd: SetActiveStrategyIntegrationS3ConnectionCommand,
) -> StrategyIntegrationS3ConnectionsBundleResponse:
    try:
        set_active_connection(cmd.connection_id)
    except ValueError as exc:
        raise _error(exc, not_found="不存在" in str(exc)) from exc
    return list_connections()


def _connection_record(connection_id: str) -> dict:
    store = load_s3_connection_store()
    record = next(
        (
            item
            for item in list(store.get("connections") or [])
            if str(item.get("id") or "") == str(connection_id)
        ),
        None,
    )
    if record is None:
        raise _error(ValueError(f"S3 配置不存在：{connection_id}"), not_found=True)
    return record


def _build_probe_client(endpoint: str):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=str(
            getattr(settings, "STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID", "") or ""
        ),
        aws_secret_access_key=str(
            getattr(settings, "STRATEGY_INTEGRATION_S3_SECRET_ACCESS_KEY", "") or ""
        ),
        use_ssl=bool(getattr(settings, "STRATEGY_INTEGRATION_S3_USE_SSL", False)),
        config=Config(
            s3={
                "addressing_style": (
                    "path"
                    if bool(
                        getattr(settings, "STRATEGY_INTEGRATION_S3_PATH_STYLE", True)
                    )
                    else "auto"
                )
            },
            connect_timeout=5,
            read_timeout=5,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )


def _is_s3_console_endpoint_error(exc: Exception) -> bool:
    """识别 MinIO 将 Console 管理端口误当作 S3 API 端口的明确响应。"""

    response = getattr(exc, "response", None)
    error = response.get("Error") if isinstance(response, dict) else None
    error_text = " ".join(
        str(value or "")
        for value in (
            str(exc),
            error.get("Code") if isinstance(error, dict) else "",
            error.get("Message") if isinstance(error, dict) else "",
        )
    ).lower()
    return "api port" in error_text or "console port" in error_text


def test_connection(connection_id: str) -> StrategyIntegrationS3ConnectionTestResponse:
    """Run a bounded, read-only endpoint/bucket/object-list probe.

    The connection record intentionally stores only the endpoint.  When the
    global S3 bucket is absent, ``list_buckets`` discovers one bucket so the
    probe can still run ``head_bucket`` and ``list_objects_v2`` without adding
    credentials or bucket fields to the configuration page.
    """

    record = _connection_record(connection_id)
    endpoint = str(record.get("endpoint") or "")
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    base = dict(
        connection_id=str(connection_id),
        endpoint=endpoint,
        checked_at=checked_at,
        object_count=0,
    )
    try:
        client = _build_probe_client(endpoint)
        base["endpoint_reachable"] = True
        bucket = str(getattr(settings, "S3_BUCKET", "") or "").strip()
        if not bucket:
            discovered = client.list_buckets()
            buckets = discovered.get("Buckets") or []
            bucket = str((buckets[0] or {}).get("Name") or "").strip() if buckets else ""
        base["bucket"] = bucket
        if not bucket:
            base.update(
                ok=False,
                head_bucket_ok=False,
                list_objects_ok=False,
                error_stage="bucket",
                message="S3 地址可达，但没有可测试的存储桶；请配置 S3_BUCKET",
            )
            return StrategyIntegrationS3ConnectionTestResponse(**base)

        client.head_bucket(Bucket=bucket)
        base["head_bucket_ok"] = True
        listed = client.list_objects_v2(Bucket=bucket, Prefix="", MaxKeys=1)
        base["list_objects_ok"] = True
        base["object_count"] = len(list(listed.get("Contents") or []))
        base.update(ok=True, message="S3 连接成功，存储桶可访问且对象列表读取成功")
        return StrategyIntegrationS3ConnectionTestResponse(**base)
    except Exception as exc:
        # Keep credentials and provider-specific response bodies out of the UI.
        stage = "endpoint"
        if base.get("bucket"):
            stage = "head_bucket" if not base.get("head_bucket_ok") else "list_objects"
        if stage == "endpoint" and _is_s3_console_endpoint_error(exc):
            stage = "s3_api"
        base.update(
            ok=False,
            error_stage=stage,
            message=(
                "当前地址是对象存储管理端（MinIO Console），不是 S3 API 地址；请填写 S3 API 端口"
                if stage == "s3_api"
                else "S3 地址不可达或请求失败"
                if stage == "endpoint"
                else (
                    "S3 存储桶不可访问"
                    if stage == "head_bucket"
                    else "S3 对象列表读取失败"
                )
            ),
        )
        return StrategyIntegrationS3ConnectionTestResponse(**base)
