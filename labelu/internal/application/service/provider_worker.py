from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from labelu.internal.adapter.persistence import (
    crud_ingestion,
    crud_model_version,
    crud_provider_job,
    crud_provider_registry,
)
from labelu.internal.application.service.provider_adapter import (
    ProviderRequest,
    ProviderResult,
    inspect_provider_adapter,
    resolve_provider_adapter,
)
from labelu.internal.common.db import begin_transaction
from labelu.internal.domain.models.provider_job import PredictionSet


def _loads(raw: str | None, default):
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return value if isinstance(value, type(default)) else default


class _ProviderWorkerError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _result_values(result: ProviderResult) -> dict:
    payload_json = (
        json.dumps(
            result.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if result.payload is not None
        else None
    )
    return {
        "payload_uri": result.payload_uri,
        "payload_sha256": result.payload_sha256,
        "result_manifest_uri": result.result_manifest_uri,
        "result_sha256": result.result_sha256,
        "item_count": int(result.item_count or 0),
        "payload_json": payload_json,
        "metrics_json": json.dumps(
            result.metrics or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


def _payload_digest(payload: dict) -> str:
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    import hashlib

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prediction_matches_result(
    prediction: PredictionSet, result: ProviderResult
) -> bool:
    expected = _result_values(result)
    actual_metrics = _loads(prediction.metrics_json, {})
    # Historical snapshots may carry the retired project identifier.  It is
    # excluded from replay identity once the provider boundary is creator/task
    # scoped, so an old immutable result can still be safely recognized.
    if isinstance(actual_metrics.get("lineage"), dict):
        actual_metrics["lineage"].pop("project_id", None)
    actual_metrics_json = json.dumps(
        actual_metrics, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    actual_payload_json = (
        json.dumps(
            _loads(prediction.payload_json, {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if prediction.payload_json is not None
        else None
    )
    return bool(
        prediction.payload_uri == expected["payload_uri"]
        and prediction.payload_sha256 == expected["payload_sha256"]
        and prediction.result_manifest_uri == expected["result_manifest_uri"]
        and prediction.result_sha256 == expected["result_sha256"]
        and int(prediction.item_count or 0) == expected["item_count"]
        and actual_payload_json == expected["payload_json"]
        and actual_metrics_json == expected["metrics_json"]
    )


def _mark_succeeded(row) -> None:
    row.status = "SUCCEEDED"
    row.completed_count = row.total_count
    row.error_code = None
    row.error_message = None
    row.completed_at = datetime.now()
    row.lease_owner = None
    row.lease_expires_at = None


def _input_assets(db, *, creator_id: int, asset_ids: list[int]):
    from labelu.internal.application.service.provider_adapter import ProviderInputAsset

    result = []
    for asset_id in asset_ids:
        asset = crud_ingestion.get_asset(db, int(asset_id))
        asset_creator_id = getattr(asset, "created_by", None)
        if asset is None or (
            asset_creator_id is not None and int(asset_creator_id) != int(creator_id)
        ):
            raise _ProviderWorkerError(
                "PROVIDER_INPUT_ASSET_NOT_FOUND",
                f"input media asset {asset_id} is missing, deleted or outside the creator scope",
            )
        if (
            str(getattr(asset, "scan_status", "")).upper() != "PASSED"
            or str(getattr(asset, "lifecycle_status", "")).upper() != "ACTIVE"
            or getattr(asset, "deleted_at", None) is not None
        ):
            raise _ProviderWorkerError(
                "PROVIDER_INPUT_ASSET_NOT_READY",
                f"input media asset {asset_id} is not scanned, active or has been deleted",
            )
        result.append(
            ProviderInputAsset(
                id=int(asset.id),
                object_uri=asset.object_uri,
                sha256=asset.sha256,
                media_type=asset.media_type,
                size_bytes=asset.size_bytes,
            )
        )
    return tuple(result)


def _with_lineage(result: ProviderResult, *, job, assets) -> ProviderResult:
    """Attach platform-owned lineage even when an adapter omits it."""
    lineage = {
        "provider_job_id": int(job.id),
        "provider_key": job.provider_key,
        "provider_version": job.provider_version,
        "model_version_id": (
            int(job.model_version_id) if job.model_version_id is not None else None
        ),
        "input_assets": [
            {
                "id": int(asset.id),
                "object_uri": asset.object_uri,
                "sha256": asset.sha256,
                "media_type": asset.media_type,
                "size_bytes": asset.size_bytes,
            }
            for asset in assets
        ],
    }
    metrics = dict(result.metrics or {})
    metrics["lineage"] = lineage
    return replace(result, metrics=metrics)


def run_next_provider_job(db, *, worker_id: str, lease_seconds: int = 300) -> bool:
    """Claim and execute one job. Returns False when the queue is empty.

    Missing adapters are durable failures, making incomplete production
    registration visible instead of treating a compatibility stub as success.
    """
    with begin_transaction(db):
        job = crud_provider_job.claim_next_job(
            db, worker_id=worker_id, lease_seconds=lease_seconds
        )
    if job is None:
        return False
    try:
        registry = crud_provider_registry.get_provider(
            db, job.provider_key, job.provider_version
        )
        if registry is None or registry.status != "ACTIVE":
            raise _ProviderWorkerError(
                "PROVIDER_ADAPTER_NOT_READY",
                f"active provider registry missing for {job.provider_key}@{job.provider_version}",
            )
        runtime = inspect_provider_adapter(job.provider_key, job.provider_version)
        expected_runtime = str(registry.runtime_adapter or "").strip().lower()
        if (
            not runtime.registered
            or not runtime.ready
            or runtime.runtime_adapter != expected_runtime
        ):
            detail = runtime.last_error or (
                f"runtime adapter {runtime.runtime_adapter!r} does not match registry "
                f"{expected_runtime!r}"
            )
            raise _ProviderWorkerError("PROVIDER_ADAPTER_NOT_READY", detail)
        adapter = resolve_provider_adapter(job.provider_key, job.provider_version)
        asset_ids = _loads(job.input_asset_ids_json, [])
        model_version = (
            crud_model_version.get(db, int(job.model_version_id))
            if job.model_version_id is not None
            else None
        )
        # Provider jobs are creator/task scoped.  ``project_id`` remains only
        # as nullable historical storage on old rows and must never gate a
        # new job or model lineage lookup.
        if job.model_version_id is not None and model_version is None:
            raise _ProviderWorkerError(
                "PROVIDER_MODEL_VERSION_NOT_FOUND",
                f"model version {job.model_version_id} is missing",
            )
        input_assets = _input_assets(db, creator_id=job.created_by, asset_ids=asset_ids)
        result = adapter.predict(
            ProviderRequest(
                provider_key=job.provider_key,
                provider_version=job.provider_version,
                asset_ids=asset_ids,
                contract_version=job.contract_version,
                config=_loads(job.config_json, {}),
                model_version_id=(
                    int(model_version.id) if model_version is not None else None
                ),
                assets=input_assets,
            )
        )
        result = _with_lineage(result, job=job, assets=input_assets)
        if not (
            result.payload_uri
            or result.result_manifest_uri
            or result.payload is not None
        ):
            raise ValueError(
                "provider result must include payload_uri, result_manifest_uri or payload"
            )
        if result.payload_uri and not result.payload_sha256:
            raise ValueError("provider payload_uri missing sha256")
        if result.payload is not None and result.payload_sha256:
            if result.payload_sha256.lower() != _payload_digest(result.payload):
                raise ValueError("provider inline payload sha256 mismatch")
        if result.result_manifest_uri and not result.result_sha256:
            raise ValueError("provider result_manifest_uri missing sha256")
        result_values = _result_values(result)
        with begin_transaction(db):
            row = crud_provider_job.get_job(db, job.id)
            if row is None or row.status != "RUNNING" or row.lease_owner != worker_id:
                return False
            existing = crud_provider_job.get_prediction_by_job_version(db, row.id, 1)
            if existing is not None:
                if not _prediction_matches_result(existing, result):
                    raise _ProviderWorkerError(
                        "PROVIDER_RESULT_CONFLICT",
                        "provider replay result conflicts with persisted PredictionSet version 1",
                    )
                _mark_succeeded(row)
                db.flush()
                return True
            prediction = PredictionSet(
                provider_job_id=row.id,
                version=1,
                annotation_contract_version="annotation_contract.v1",
                prediction_schema_version=row.contract_version,
                payload_uri=result.payload_uri,
                payload_sha256=result.payload_sha256,
                result_manifest_uri=result.result_manifest_uri,
                result_sha256=result.result_sha256,
                payload_json=result_values["payload_json"],
                status="COMPLETED",
                review_status="PENDING",
                source="AI",
                item_count=result_values["item_count"],
                metrics_json=result_values["metrics_json"],
                created_by=row.created_by,
            )
            try:
                with db.begin_nested():
                    crud_provider_job.create_prediction_set(db, prediction)
            except IntegrityError:
                existing = crud_provider_job.get_prediction_by_job_version(
                    db, row.id, 1
                )
                if existing is None or not _prediction_matches_result(existing, result):
                    raise _ProviderWorkerError(
                        "PROVIDER_RESULT_CONFLICT",
                        "provider result raced with a conflicting PredictionSet version 1",
                    )
                row = crud_provider_job.get_job(db, job.id)
                if (
                    row is None
                    or row.status != "RUNNING"
                    or row.lease_owner != worker_id
                ):
                    return False
            _mark_succeeded(row)
            db.flush()
        return True
    except Exception as exc:
        with begin_transaction(db):
            row = crud_provider_job.get_job(db, job.id)
            if (
                row is not None
                and row.status == "RUNNING"
                and row.lease_owner == worker_id
            ):
                row.status = "FAILED"
                row.error_code = getattr(exc, "code", "PROVIDER_EXECUTION_FAILED")
                row.error_message = str(exc)[:4000]
                row.completed_at = datetime.now()
                row.lease_owner = None
                row.lease_expires_at = None
                db.flush()
        return True
