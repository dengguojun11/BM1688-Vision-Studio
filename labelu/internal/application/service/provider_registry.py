from __future__ import annotations

import json
from datetime import datetime

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_provider_registry
from labelu.internal.application.command.provider_registry import UpdateProviderEvidenceCommand
from labelu.internal.application.response.provider_registry import ProviderRegistryResponse
from labelu.internal.application.service.provider_adapter import inspect_provider_adapter
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import is_task_superuser
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.provider_registry import ProviderRegistry
from labelu.internal.domain.models.user import User


BUILTIN_PROVIDERS = (
    {
        "provider_key": "yolo",
        "provider_version": "11.0.1",
        "display_name": "YOLO目标检测",
        "capabilities": ["DETECTION", "BATCH", "VIDEO_FRAME"],
        "runtime_adapter": "yolo_preannotate",
        "supported_media_types": ["IMAGE", "VIDEO_FRAME"],
        "supported_chips": ["CPU", "CUDA", "BM1688", "RK3588"],
    },
    {
        "provider_key": "tagtool_cls",
        "provider_version": "1.0.0",
        "display_name": "TagTool图像分类",
        "capabilities": ["CLASSIFICATION", "BATCH"],
        "runtime_adapter": "tagtool_preannotate",
        "supported_media_types": ["IMAGE"],
        "supported_chips": ["CPU", "CUDA"],
    },
    {
        "provider_key": "locateanything",
        "provider_version": "1.0.0",
        "display_name": "LocateAnything检测",
        "capabilities": ["DETECTION", "BATCH"],
        "runtime_adapter": "locateanything_preannotate",
        "supported_media_types": ["IMAGE"],
        "supported_chips": ["CPU", "CUDA"],
    },
    {
        "provider_key": "vlx_seek",
        "provider_version": "1.0.0",
        "display_name": "VLX目标检索",
        "capabilities": ["DETECTION", "BATCH"],
        "runtime_adapter": "vlx_seek_preannotate",
        "supported_media_types": ["IMAGE"],
        "supported_chips": ["CPU", "CUDA"],
    },
    {
        "provider_key": "rex_omni",
        "provider_version": "1.0.0",
        "display_name": "Rex Omni检测",
        "capabilities": ["DETECTION", "BATCH"],
        "runtime_adapter": "rex_omni_preannotate",
        "supported_media_types": ["IMAGE"],
        "supported_chips": ["CPU", "CUDA"],
    },
    {
        "provider_key": "vision_llm",
        "provider_version": "1.0.0",
        "display_name": "视觉大模型辅助标注",
        "capabilities": ["CLASSIFICATION", "DETECTION", "BATCH"],
        "runtime_adapter": "auto_label",
        "supported_media_types": ["IMAGE"],
        "supported_chips": ["CPU", "CUDA"],
    },
)


def ensure_seeded(db: Session) -> None:
    """Keep source-mode/test databases useful before Alembic is applied."""
    missing = [
        spec
        for spec in BUILTIN_PROVIDERS
        if crud_provider_registry.get_provider(
            db, spec["provider_key"], spec["provider_version"]
        )
        is None
    ]
    if not missing:
        return
    with begin_transaction(db):
        for spec in missing:
            db.add(
                ProviderRegistry(
                    provider_key=spec["provider_key"],
                    provider_version=spec["provider_version"],
                    display_name=spec["display_name"],
                    provider_kind="PREANNOTATION",
                    capabilities_json=json.dumps(spec["capabilities"], separators=(",", ":")),
                    input_contract_version="annotation_contract.v1",
                    output_contract_version="prediction.v1",
                    runtime_adapter=spec["runtime_adapter"],
                    supported_media_types_json=json.dumps(
                        spec["supported_media_types"], separators=(",", ":")
                    ),
                    supported_chips_json=json.dumps(
                        spec["supported_chips"], separators=(",", ":")
                    ),
                    config_schema_json='{"type":"object"}',
                    status="ACTIVE",
                    availability_reason="已登记现有兼容适配器，等待镜像摘要、工具链和HIL证据",
                    metadata_json=json.dumps(
                        {"legacy_compatibility": True}, separators=(",", ":")
                    ),
                )
            )
        db.flush()


def _json_value(value: str | None, default):
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return parsed


def _response(row: ProviderRegistry) -> ProviderRegistryResponse:
    capabilities = _json_value(row.capabilities_json, [])
    media_types = _json_value(row.supported_media_types_json, [])
    chips = _json_value(row.supported_chips_json, [])
    config_schema = _json_value(row.config_schema_json, {})
    if not isinstance(capabilities, list):
        capabilities = []
    if not isinstance(media_types, list):
        media_types = []
    if not isinstance(chips, list):
        chips = []
    if not isinstance(config_schema, dict):
        config_schema = {}
    metadata = _json_value(row.metadata_json, {})
    if not isinstance(metadata, dict):
        metadata = {}
    hil_evidence = metadata.get("hil_evidence")
    hil_verified = bool(
        metadata.get("hil_verified") is True
        and isinstance(hil_evidence, dict)
        and str(hil_evidence.get("status") or "").strip().upper() in {"PASSED", "VERIFIED"}
        and any(
            hil_evidence.get(field)
            for field in ("evidence_id", "report_uri", "report_sha256")
        )
    )
    runtime = inspect_provider_adapter(row.provider_key, row.provider_version)
    runtime_matches = bool(
        runtime.registered
        and runtime.runtime_adapter
        and runtime.runtime_adapter == str(row.runtime_adapter or "").strip().lower()
    )
    runtime_healthy = bool(runtime_matches and runtime.healthy)
    runtime_ready = bool(runtime_matches and runtime.ready)
    production_ready = bool(
        row.status == "ACTIVE"
        and row.image_digest
        and row.toolchain_version
        and hil_verified
        and runtime_ready
    )
    runtime_error = runtime.last_error
    if runtime.registered and not runtime_matches:
        runtime_error = (
            f"registered runtime adapter {runtime.runtime_adapter!r} does not match "
            f"registry {row.runtime_adapter!r}"
        )
    return ProviderRegistryResponse(
        id=row.id,
        provider_key=row.provider_key,
        provider_version=row.provider_version,
        display_name=row.display_name,
        provider_kind=row.provider_kind,
        capabilities=[str(item) for item in capabilities],
        input_contract_version=row.input_contract_version,
        output_contract_version=row.output_contract_version,
        runtime_adapter=row.runtime_adapter,
        supported_media_types=[str(item) for item in media_types],
        supported_chips=[str(item) for item in chips],
        config_schema=config_schema,
        status=row.status,
        runtime_registered=bool(runtime.registered and runtime_matches),
        runtime_healthy=runtime_healthy,
        runtime_ready=runtime_ready,
        runtime_last_error=runtime_error,
        hil_verified=hil_verified,
        hil_evidence=hil_evidence if isinstance(hil_evidence, dict) else None,
        runtime_evidence=metadata.get("runtime_evidence") if isinstance(metadata.get("runtime_evidence"), dict) else None,
        production_ready=production_ready,
        available=production_ready,
        image_digest=row.image_digest,
        toolchain_version=row.toolchain_version,
        availability_reason=row.availability_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _assert_registry_admin(user: User) -> None:
    if not is_task_superuser(user):
        raise LabelUException(
            code=ErrorCode.CODE_30001_NO_PERMISSION,
            status_code=status.HTTP_403_FORBIDDEN,
            message="只有平台管理员可以更新 Provider 证据",
        )


async def update_evidence(
    db: Session,
    provider_key: str,
    cmd: UpdateProviderEvidenceCommand,
    current_user: User,
) -> ProviderRegistryResponse:
    _assert_registry_admin(current_user)
    ensure_seeded(db)
    row = crud_provider_registry.get_provider(db, provider_key, cmd.provider_version)
    if row is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="Provider 不存在",
        )
    with begin_transaction(db):
        metadata = _json_value(row.metadata_json, {})
        if not isinstance(metadata, dict):
            metadata = {}
        if cmd.hil_evidence is not None:
            metadata["hil_evidence"] = cmd.hil_evidence
        if cmd.hil_verified is not None:
            metadata["hil_verified"] = bool(cmd.hil_verified)
        if cmd.runtime_evidence is not None:
            metadata["runtime_evidence"] = cmd.runtime_evidence
        if cmd.image_digest is not None:
            row.image_digest = cmd.image_digest
        if cmd.toolchain_version is not None:
            row.toolchain_version = cmd.toolchain_version
        if cmd.availability_reason is not None:
            row.availability_reason = cmd.availability_reason
        if cmd.status is not None:
            row.status = cmd.status
        metadata["evidence_updated_by"] = int(current_user.id)
        metadata["evidence_updated_at"] = datetime.now().isoformat()
        row.metadata_json = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        db.flush()
        db.refresh(row)
    return _response(row)


async def list_providers(
    db: Session,
    *,
    current_user: User,
    status_filter: str | None = "ACTIVE",
    capability: str | None = None,
    search: str | None = None,
    page: int = 0,
    size: int = 100,
) -> tuple[list[ProviderRegistryResponse], int]:
    ensure_seeded(db)
    status_value = str(status_filter).strip().upper() if status_filter else None
    if status_value and status_value not in {"ACTIVE", "DISABLED", "DEPRECATED"}:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="Provider 状态不受支持",
        )
    rows, total = crud_provider_registry.list_providers(
        db,
        status=status_value,
        capability=capability,
        search=search,
        page=page,
        size=size,
    )
    return [
        _response(row)
        for row in rows
    ], total


async def get_provider(
    db: Session,
    provider_key: str,
    *,
    current_user: User,
    provider_version: str | None = None,
) -> ProviderRegistryResponse:
    ensure_seeded(db)
    row = crud_provider_registry.get_provider(db, provider_key, provider_version)
    if row is None or row.status != "ACTIVE":
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="Provider 不存在",
        )
    return _response(row)
