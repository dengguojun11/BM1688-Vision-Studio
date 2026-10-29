"""Model optimization adapters used by the flexible workflow executor.

The local adapter is intentionally an honest, deterministic adapter.  It
records a traceable job/result and validates project lineage, but does not
claim to have produced a deployable binary.  A production GPU/edge adapter
can implement the same contract later without changing workflow semantics.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_model_optimization,
    crud_model_version,
)
from labelu.internal.common.config import settings
from labelu.internal.domain.models.model_optimization import ModelOptimizationJob
from labelu.internal.domain.models.model_version import ModelArtifact

DEPLOYABLE_ARTIFACT_TYPES = frozenset({"pt", "onnx", "bmodel", "rknn"})


def _json(value: Any) -> str:
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _result(row: ModelOptimizationJob) -> dict[str, Any]:
    try:
        parsed = json.loads(row.result_json or "{}")
    except (TypeError, ValueError):
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_relative_path(value: Any) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("模型制品路径必须是安全的相对路径")
    return path.as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _input_artifact(model: Any, parameters: dict[str, Any]) -> tuple[Any, Path, str]:
    artifacts = list(getattr(model, "artifacts", None) or [])
    requested = parameters.get("input_artifact_id")
    if requested is not None:
        try:
            requested_id = int(requested)
        except (TypeError, ValueError) as exc:
            raise ValueError("input_artifact_id 必须是整数") from exc
        candidates = [
            item for item in artifacts if int(getattr(item, "id", 0)) == requested_id
        ]
    else:
        candidates = [
            item
            for item in artifacts
            if str(getattr(item, "artifact_type", "")).lower()
            in DEPLOYABLE_ARTIFACT_TYPES
        ]
        candidates.sort(key=lambda item: int(getattr(item, "id", 0)))
    artifact = next(
        (
            item
            for item in candidates
            if str(getattr(item, "status", "")).upper() == "READY"
            and str(getattr(item, "artifact_type", "")).lower()
            in DEPLOYABLE_ARTIFACT_TYPES
        ),
        None,
    )
    if artifact is None:
        raise ValueError("模型版本缺少 READY 状态的输入权重制品")
    relative = _safe_relative_path(getattr(artifact, "relative_path", ""))
    root = Path(settings.MEDIA_ROOT).resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("模型输入制品路径必须位于 MEDIA_ROOT 内") from exc
    if not path.is_file():
        raise ValueError("模型输入制品文件不存在")
    actual_sha = _sha256(path)
    recorded_sha = str(getattr(artifact, "sha256", "") or "").strip().lower()
    if recorded_sha and recorded_sha != actual_sha:
        raise ValueError("模型输入制品 SHA256 与文件内容不一致")
    return artifact, path, actual_sha


def run_local_sync(
    db: Session,
    *,
    project_id: int,
    workflow_id: int | None,
    model_version_id: int,
    parameters: dict[str, Any] | None,
    created_by: int,
    idempotency_key: str,
) -> dict[str, Any]:
    """Run/replay a local optimization job with strict tenant isolation."""

    model = crud_model_version.get(db, int(model_version_id))
    if model is None or int(model.project_id) != int(project_id):
        raise ValueError("模型版本不存在或不属于当前项目")
    key = str(idempotency_key).strip()
    if not key:
        raise ValueError("模型优化幂等键不能为空")
    existing = crud_model_optimization.get_by_key(db, key)
    if existing is not None:
        if int(existing.project_id) != int(project_id) or int(
            existing.model_version_id
        ) != int(model_version_id):
            raise ValueError("模型优化幂等键已用于其他项目或模型")
        return {
            "job_id": int(existing.id),
            "status": existing.status,
            "result": _result(existing),
            "replayed": True,
        }

    params = dict(parameters) if isinstance(parameters, dict) else {}
    input_artifact, input_path, input_sha256 = _input_artifact(model, params)
    params_json = _json(params)
    now = datetime.now()
    row = ModelOptimizationJob(
        project_id=int(project_id),
        workflow_id=int(workflow_id) if workflow_id is not None else None,
        model_version_id=int(model_version_id),
        adapter_key="local_sync",
        idempotency_key=key,
        status="RUNNING",
        attempts=1,
        parameters_json=params_json,
        created_by=int(created_by),
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()

    parameter_hash = hashlib.sha256(params_json.encode("utf-8")).hexdigest()
    manifest = {
        "schema_version": "7gai.model-optimization-manifest.v1",
        "project_id": int(project_id),
        "workflow_id": int(workflow_id) if workflow_id is not None else None,
        "optimization_job_id": int(row.id),
        "model_version_id": int(model_version_id),
        "input_artifact": {
            "id": int(getattr(input_artifact, "id", 0)),
            "artifact_type": str(input_artifact.artifact_type).lower(),
            "relative_path": _safe_relative_path(input_artifact.relative_path),
            "sha256": input_sha256,
            "size_bytes": int(input_path.stat().st_size),
        },
        "parameters": params,
        "parameter_hash": parameter_hash,
        "chip_type": getattr(model, "chip_type", None),
        "precision": getattr(model, "precision", None),
        "deployable": False,
        "production_ready": False,
    }
    manifest_bytes = (_json(manifest) + "\n").encode("utf-8")
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    relative_manifest = (
        f"model_optimization/{int(project_id)}/{int(model_version_id)}/"
        f"{parameter_hash}/bundle-manifest.json"
    )
    manifest_path = (Path(settings.MEDIA_ROOT).resolve() / relative_manifest).resolve()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = manifest_path.with_suffix(".tmp")
    temporary_path.write_bytes(manifest_bytes)
    temporary_path.replace(manifest_path)
    existing_manifest = next(
        (
            item
            for item in list(getattr(model, "artifacts", None) or [])
            if str(getattr(item, "artifact_type", "")).lower() == "manifest"
            and str(getattr(item, "sha256", "")).lower() == manifest_sha256
            and str(getattr(item, "status", "")).upper() == "READY"
        ),
        None,
    )
    artifact_created = existing_manifest is None
    manifest_artifact_id = (
        int(getattr(existing_manifest, "id", 0)) if existing_manifest else None
    )
    if artifact_created:
        manifest_artifact = ModelArtifact(
            model_version_id=int(model_version_id),
            artifact_type="manifest",
            relative_path=relative_manifest,
            sha256=manifest_sha256,
            size_bytes=len(manifest_bytes),
            chip_type=getattr(model, "chip_type", None),
            precision=getattr(model, "precision", None),
            status="READY",
            metadata_json=_json(
                {"deployable": False, "production_ready": False, "source": "local_sync"}
            ),
            created_by=int(created_by),
        )
        crud_model_version.create_artifact(db, manifest_artifact)
        manifest_artifact_id = int(manifest_artifact.id)
    result = {
        "status": "SUCCEEDED",
        "adapter_key": "local_sync",
        "execution_mode": "local_deterministic",
        "model_version_id": int(model_version_id),
        "parameter_hash": parameter_hash,
        "parameters": params,
        "input_artifact_id": int(getattr(input_artifact, "id", 0)),
        "input_artifact_sha256": input_sha256,
        "artifact_created": artifact_created,
        "artifact_status": "READY",
        "artifact_id": manifest_artifact_id,
        "artifact_type": "manifest",
        "artifact_relative_path": relative_manifest,
        "deployable": False,
        "production_ready": False,
        "message": "本地适配器已生成模型优化元数据 Bundle；未生成可部署二进制产物",
    }
    row.status = "SUCCEEDED"
    row.result_json = _json(result)
    row.finished_at = datetime.now()
    row.updated_at = row.finished_at
    db.flush()
    return {
        "job_id": int(row.id),
        "status": row.status,
        "result": result,
        "replayed": False,
    }


def get_job(db: Session, job_id: int, *, project_id: int) -> dict[str, Any] | None:
    row = crud_model_optimization.get(db, job_id)
    if row is None or int(row.project_id) != int(project_id):
        return None
    return {
        "job_id": int(row.id),
        "project_id": int(row.project_id),
        "workflow_id": row.workflow_id,
        "model_version_id": int(row.model_version_id),
        "adapter_key": row.adapter_key,
        "status": row.status,
        "attempts": int(row.attempts or 0),
        "result": _result(row),
        "last_error": row.last_error,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }
