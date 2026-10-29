"""Operational Provider runtime probes used by the local 7GAI control plane.

Adapter registration remains an explicit process concern (startup bootstrap or
deployment code).  This module only probes the registered adapter and records
an auditable runtime snapshot.  The local HIL probe deliberately writes
``environment=local`` evidence and never flips the production HIL gate.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_provider_registry
from labelu.internal.application.command.provider_runtime import RunProviderLocalHilCommand
from labelu.internal.application.service.provider_adapter import (
    ProviderInputAsset,
    ProviderRequest,
    inspect_provider_adapter,
    resolve_provider_adapter,
)
from labelu.internal.application.service.provider_registry import (
    _assert_registry_admin,
    _response,
    ensure_seeded,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.user import User


def _provider_row(db: Session, provider_key: str, provider_version: str | None):
    ensure_seeded(db)
    row = crud_provider_registry.get_provider(db, provider_key, provider_version)
    if row is None or row.status != "ACTIVE":
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=404,
            message="Provider 不存在或已禁用",
        )
    return row


def _metadata(row) -> dict[str, Any]:
    try:
        value = json.loads(row.metadata_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def runtime_check(
    db: Session,
    provider_key: str,
    *,
    provider_version: str | None,
    current_user: User,
):
    """Probe one registered adapter and persist a runtime evidence snapshot."""

    _assert_registry_admin(current_user)
    row = _provider_row(db, provider_key, provider_version)
    runtime = inspect_provider_adapter(row.provider_key, row.provider_version)
    checked_at = datetime.now().isoformat()
    status = "READY" if runtime.ready else ("UNHEALTHY" if runtime.registered else "UNREGISTERED")
    evidence = {
        "status": status,
        "environment": "local",
        "checked_at": checked_at,
        "provider_key": row.provider_key,
        "provider_version": row.provider_version,
        "runtime_adapter": runtime.runtime_adapter,
        "registered": bool(runtime.registered),
        "healthy": bool(runtime.healthy),
        "ready": bool(runtime.ready),
        "error": runtime.last_error,
    }
    metadata = _metadata(row)
    metadata["runtime_evidence"] = evidence
    with begin_transaction(db):
        row.metadata_json = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        db.flush()
        db.refresh(row)
    return _response(row)


def local_hil_check(
    db: Session,
    provider_key: str,
    *,
    command: RunProviderLocalHilCommand,
    current_user: User,
):
    """Run one local end-to-end prediction contract probe.

    This validates runtime registration, health/readiness, request/response
    serialization and result hashing over the actual adapter.  It does not
    create a ProviderJob, PredictionSet or formal HIL attestation.
    """

    _assert_registry_admin(current_user)
    row = _provider_row(db, provider_key, command.provider_version)
    runtime = inspect_provider_adapter(row.provider_key, row.provider_version)
    if not runtime.registered or not runtime.ready:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=409,
            message=runtime.last_error or "Provider 运行时未就绪",
        )
    adapter = resolve_provider_adapter(row.provider_key, row.provider_version)
    started_at = datetime.now().isoformat()
    try:
        result = adapter.predict(
            ProviderRequest(
                provider_key=row.provider_key,
                provider_version=row.provider_version,
                asset_ids=[0],
                contract_version=row.output_contract_version,
                config={"labels": command.labels},
                assets=(ProviderInputAsset(id=0, object_uri=command.sample_uri, media_type="IMAGE"),),
            )
        )
        if not (result.payload_uri or result.result_manifest_uri or result.payload is not None):
            raise ValueError("provider result has no payload")
        if result.payload is not None:
            canonical = json.dumps(result.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if result.payload_sha256 and result.payload_sha256.lower() != digest:
                raise ValueError("provider inline payload sha256 mismatch")
        status = "PASSED"
        error = None
    except Exception as exc:
        result = None
        status = "FAILED"
        error = str(exc)[:1000]
    finished_at = datetime.now().isoformat()
    report = {
        "evidence_id": f"local-hil-{uuid4().hex}",
        "status": status,
        "environment": "local",
        "provider_key": row.provider_key,
        "provider_version": row.provider_version,
        "runtime_adapter": runtime.runtime_adapter,
        "sample_uri": command.sample_uri,
        "label_count": len(command.labels),
        "item_count": int(result.item_count) if result is not None else 0,
        "payload_sha256": result.payload_sha256 if result is not None else None,
        "started_at": started_at,
        "finished_at": finished_at,
        "error": error,
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    metadata = _metadata(row)
    metadata["local_hil"] = report
    runtime_evidence = metadata.get("runtime_evidence")
    if not isinstance(runtime_evidence, dict):
        runtime_evidence = {}
    runtime_evidence.update(
        {
            "status": "READY" if status == "PASSED" else "UNHEALTHY",
            "environment": "local",
            "local_hil_status": status,
            "checked_at": finished_at,
        }
    )
    metadata["runtime_evidence"] = runtime_evidence
    with begin_transaction(db):
        row.metadata_json = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        db.flush()
        db.refresh(row)
    return _response(row)


__all__ = ["runtime_check", "local_hil_check"]
