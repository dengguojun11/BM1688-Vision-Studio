from __future__ import annotations

import json
import threading
import hashlib
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from fastapi import status as http_status
from sqlalchemy import func
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_model_version,
    crud_published_model_catalog as catalog_crud,
    crud_task,
    crud_yolo_bm1688_export,
    crud_yolo_train_job,
)
from labelu.internal.application.command.published_model_catalog import (
    CreatePublishedModelAliasCommand,
    UpdatePublishedModelCommand,
)
from labelu.internal.application.response.published_model_catalog import (
    PublishedModelAliasResponse,
    PublishedModelClassLabelResponse,
    PublishedModelDownloadResponse,
    PublishedModelListResponse,
    PublishedModelResponse,
    PublishedModelSummaryResponse,
    PublishedModelSyncResponse,
)
from labelu.internal.application.service import yolo_aicm_publish
from labelu.internal.common.aicm_publish_config import (
    config_is_complete,
    get_active_environment_id,
    load_aicm_publish_config,
)
from labelu.internal.common.aicm_publish_job import list_jobs, load_job
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.config import settings
from labelu.internal.common.business_log import biz_log
from labelu.internal.domain.models.published_model_catalog import (
    PublishedModelCatalog,
)
from labelu.internal.domain.models.annotation_taxonomy import AnnotationLabel
from labelu.internal.domain.models.user import User

PACKAGE_ROOT_DIRNAME = "published_model_packages"


@dataclass(frozen=True)
class ResolvedPublishedModelPackage:
    catalog_id: int
    model_id: str
    aicm_model_set_id: Optional[int]
    file_path: Path
    filename: str


def _package_root() -> Path:
    return (Path(settings.BASE_DATA_DIR) / PACKAGE_ROOT_DIRNAME).resolve()


def _package_rel_path(model_id: str) -> str:
    text = str(model_id or "").strip()
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._-")
    if not safe or safe != text:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        safe = f"{safe[:80] or 'model'}-{digest}"
    return (Path(PACKAGE_ROOT_DIRNAME) / safe / "model.zip").as_posix()


def _resolve_package_rel_path(package_rel_path: str) -> Path:
    rel = Path(str(package_rel_path or "").strip())
    if not str(rel) or rel.is_absolute():
        raise ValueError("package path must be a non-empty relative path")
    package_root = _package_root()
    candidate = (Path(settings.BASE_DATA_DIR).resolve() / rel).resolve()
    try:
        candidate.relative_to(package_root)
    except ValueError as exc:
        raise ValueError(
            "package path escapes the published model package root"
        ) from exc
    return candidate


def _existing_local_package(
    row: PublishedModelCatalog, *, strict_path: bool = False
) -> Optional[Path]:
    if not row.package_rel_path:
        return None
    try:
        path = _resolve_package_rel_path(row.package_rel_path)
    except ValueError:
        if strict_path:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=http_status.HTTP_409_CONFLICT,
                message=f"模型 {row.public_model_id} 的本地模型包路径无效",
            )
        return None
    return path if path.is_file() else None


def _file_hash(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_metadata(path: Path, package_rel_path: str) -> dict[str, Any]:
    return {
        "package_rel_path": package_rel_path,
        "package_size_bytes": path.stat().st_size,
        "package_sha256": _file_hash(path, "sha256"),
        "package_md5": _file_hash(path, "md5"),
    }


def _assert_package_integrity(row: PublishedModelCatalog, path: Path) -> None:
    """Reject a locally replaced package when publication recorded its digest."""
    expected_sha256 = str(getattr(row, "package_sha256", "") or "").strip().lower()
    expected_size = getattr(row, "package_size_bytes", None)
    if expected_size is not None and int(expected_size) != int(path.stat().st_size):
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message=f"模型 {row.public_model_id} 的本地模型包大小与登记摘要不一致",
        )
    if expected_sha256 and _file_hash(path, "sha256").lower() != expected_sha256:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message=f"模型 {row.public_model_id} 的本地模型包摘要与登记值不一致",
        )


def _install_package_once(temporary: Path, target: Path, *, model_id: str) -> Path:
    """Atomically install a verified package without replacing a concurrent winner."""

    try:
        os.link(temporary, target)
    except FileExistsError:
        if not target.is_file() or not zipfile.is_zipfile(target):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=http_status.HTTP_409_CONFLICT,
                message=f"模型 {model_id} 的长期保留包已被并发写入且文件无效",
            )
        if _file_hash(temporary, "sha256") != _file_hash(target, "sha256"):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=http_status.HTTP_409_CONFLICT,
                message=(
                    f"模型 {model_id} 的长期保留包已被另一进程写入不同内容，"
                    "系统不会覆盖"
                ),
            )
    return target


def _persist_local_package(model_id: str, source_path: str | Path) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message=f"LabelU 本地模型包不存在：{source}",
        )
    if not zipfile.is_zipfile(source):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message=f"LabelU 本地模型包不是有效 ZIP：{source.name}",
        )
    package_rel_path = _package_rel_path(model_id)
    target = _resolve_package_rel_path(package_rel_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or not zipfile.is_zipfile(target):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=http_status.HTTP_409_CONFLICT,
                message=f"模型 {model_id} 已有的长期保留包无效，系统不会自动覆盖",
            )
        if source != target and _file_hash(source, "sha256") != _file_hash(
            target, "sha256"
        ):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=http_status.HTTP_409_CONFLICT,
                message=f"模型 {model_id} 已有不同内容的长期保留包，系统不会自动覆盖",
            )
        return _package_metadata(target, package_rel_path)
    if source != target:
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temporary)
            if not zipfile.is_zipfile(temporary):
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=http_status.HTTP_409_CONFLICT,
                    message="复制后的 LabelU 模型包校验失败",
                )
            _install_package_once(temporary, target, model_id=model_id)
        finally:
            if temporary.exists():
                temporary.unlink()
    return _package_metadata(target, package_rel_path)


def _build_model_version_package(
    model_version: Any, *, aicm_model_set_id: int, environment_id: str
) -> Path:
    """Materialize a reproducible platform package from READY model artifacts.

    The canonical model-center publish action must leave a downloadable copy
    in LabelU even when the AICM publisher only returns a numeric model id.
    The temporary archive is verified again by ``_persist_local_package`` and
    is removed by the caller after the atomic copy completes.
    """

    media_root = Path(settings.MEDIA_ROOT).resolve()
    artifacts = [
        item
        for item in (getattr(model_version, "artifacts", None) or [])
        if str(getattr(item, "status", "")).upper() == "READY"
    ]
    if not artifacts:
        raise LabelUException(
            code=ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
            status_code=http_status.HTTP_409_CONFLICT,
            message="模型没有可保留的 READY 制品，无法生成平台下载包",
        )
    fd, raw_path = tempfile.mkstemp(prefix="labelu-model-version-", suffix=".zip")
    os.close(fd)
    archive_path = Path(raw_path)
    manifest: dict[str, Any] = {
        "manifest_version": 1,
        "model_version_id": int(getattr(model_version, "id")),
        "aicm_model_set_id": int(aicm_model_set_id),
        "aicm_environment_id": str(environment_id or ""),
        "display_name": str(getattr(model_version, "display_name", "") or ""),
        "artifacts": [],
    }
    try:
        with zipfile.ZipFile(
            archive_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as bundle:
            for artifact in artifacts:
                relative = str(getattr(artifact, "relative_path", "") or "").replace(
                    "\\", "/"
                )
                source = (media_root / relative).resolve()
                try:
                    source.relative_to(media_root)
                except ValueError as exc:
                    raise LabelUException(
                        code=ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
                        status_code=http_status.HTTP_409_CONFLICT,
                        message=f"模型制品路径越界，无法打包：{relative}",
                    ) from exc
                if not source.is_file():
                    raise LabelUException(
                        code=ErrorCode.CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT,
                        status_code=http_status.HTTP_409_CONFLICT,
                        message=f"模型制品文件不存在，无法打包：{relative}",
                    )
                arcname = f"artifacts/{Path(relative).name}"
                bundle.write(source, arcname=arcname)
                manifest["artifacts"].append(
                    {
                        "artifact_type": str(getattr(artifact, "artifact_type", "")),
                        "precision": getattr(artifact, "precision", None),
                        "chip_type": getattr(artifact, "chip_type", None),
                        "source_path": relative,
                        "package_path": arcname,
                        "sha256": str(getattr(artifact, "sha256", "") or ""),
                    }
                )
            bundle.writestr(
                "model_manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path


def register_model_version_publication(
    db: Session,
    *,
    model_version: Any,
    user: User,
    aicm_model_set_id: int,
    environment_id: str = "",
    package_path: str = "",
) -> PublishedModelCatalog:
    """Create the local download-directory record for a canonical model version."""

    model_id = str(int(aicm_model_set_id))
    env_id = str(environment_id or "").strip()
    row = catalog_crud.get_by_public_model_id(db, model_id)
    if row is not None:
        _assert_aicm_identity_not_rebound(
            row, model_set_id=int(aicm_model_set_id), environment_id=env_id
        )
        if row.published_at and row.package_rel_path:
            model_version.published_catalog_id = int(row.id)
            return row

    temporary: Optional[Path] = None
    source = str(package_path or "").strip()
    try:
        if not source:
            temporary = _build_model_version_package(
                model_version,
                aicm_model_set_id=int(aicm_model_set_id),
                environment_id=env_id,
            )
            source = str(temporary)
        package_meta = _persist_local_package(model_id, source)
        manifest = _json_object(getattr(model_version, "manifest_json", None))
        labels = (
            manifest.get("class_names")
            if isinstance(manifest.get("class_names"), list)
            else []
        )
        values: dict[str, Any] = {
            "aicm_environment_id": env_id or None,
            "aicm_model_set_id": int(aicm_model_set_id),
            "name": str(getattr(model_version, "model_key", "") or model_id),
            "display_name": str(getattr(model_version, "display_name", "") or model_id),
            "source_type": "labelu_model_version",
            "source_system": "aicm",
            "status": "active",
            "is_public": True,
            "download_enabled": True,
            "availability_status": "available",
            "chip_type": getattr(model_version, "chip_type", None),
            "precision": _normalize_precision(
                getattr(model_version, "precision", None)
            ),
            "labels_json": json.dumps(labels, ensure_ascii=False),
            "publisher_username": str(
                getattr(user, "username", "") or getattr(user, "email", "") or ""
            )
            or None,
            "published_at": datetime.now(),
            "last_synced_at": datetime.now(),
            **package_meta,
        }
        if row is None:
            row = catalog_crud.create(db, public_model_id=model_id, **values)
        else:
            values.update(
                display_name=row.display_name or values["display_name"],
                status=row.status,
                is_public=row.is_public,
                download_enabled=row.download_enabled,
                published_at=row.published_at or values["published_at"],
            )
            row = catalog_crud.update(db, row, **values)
        _ensure_alias(
            db, row, source_system="aicm", external_model_id=model_id, alias_type="aicm"
        )
        model_version.published_catalog_id = int(row.id)
        model_version.published_by = int(user.id)
        model_version.published_at = model_version.published_at or datetime.now()
        return row
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _download_filename(row: PublishedModelCatalog) -> str:
    safe_name = re.sub(
        r"[^A-Za-z0-9._-]+", "_", row.display_name or row.name or "model"
    ).strip("_")
    base = safe_name or "model"
    model_suffix = f"-{row.public_model_id}"
    if base.endswith(model_suffix):
        return f"{base}.zip"
    return f"{base}{model_suffix}.zip"


def _assert_aicm_identity_not_rebound(
    row: PublishedModelCatalog,
    *,
    model_set_id: int,
    environment_id: str,
) -> None:
    """Keep the public model ID bound to one AICM identity for its lifetime."""

    incoming_model_set_id = int(model_set_id)
    existing_model_set_id = (
        int(row.aicm_model_set_id) if row.aicm_model_set_id is not None else None
    )
    existing_environment_id = str(row.aicm_environment_id or "").strip()
    incoming_environment_id = str(environment_id or "").strip()
    model_id_changed = (
        existing_model_set_id is not None
        and existing_model_set_id != incoming_model_set_id
    )
    environment_changed = (
        bool(existing_environment_id)
        and bool(incoming_environment_id)
        and existing_environment_id != incoming_environment_id
    )
    if not model_id_changed and not environment_changed:
        return
    raise LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=http_status.HTTP_409_CONFLICT,
        message=(
            f"模型编号 {row.public_model_id} 已绑定 AICM 环境 "
            f"{existing_environment_id or '未记录'}、模型集合编号 "
            f"{existing_model_set_id if existing_model_set_id is not None else '未记录'}，"
            "不能自动改绑；请核对发布环境和第三方回流模型编号"
        ),
    )


def _json_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _normalize_precision(value: Any) -> Optional[str]:
    """Normalize known BModel precision values without guessing legacy data."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    for precision in ("int8", "fp16", "fp32"):
        if re.search(rf"(?<![a-z0-9]){precision}(?![a-z0-9])", text):
            return precision
    return None


def _precision_from_publish_record(raw: dict[str, Any]) -> Optional[str]:
    result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
    quality = (
        raw.get("quality_risk_snapshot")
        if isinstance(raw.get("quality_risk_snapshot"), dict)
        else {}
    )
    for value in (
        result.get("precision"),
        result.get("bmodel_precision"),
        result.get("bmodel_rel_path"),
        raw.get("bmodel_precision"),
        raw.get("bmodel_rel_path"),
        quality.get("selected_precision"),
    ):
        precision = _normalize_precision(value)
        if precision:
            return precision
    return None


def _row_precision(row: PublishedModelCatalog) -> Optional[str]:
    precision = _normalize_precision(row.precision)
    if precision or not row.publish_job_id:
        return precision
    try:
        raw = load_job(str(row.publish_job_id))
    except (OSError, ValueError, TypeError):
        raw = None
    return _precision_from_publish_record(raw or {}) if raw else None


def _as_datetime(value: Any) -> Optional[datetime]:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, datetime):
        return value
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(float(value))
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OSError):
        return None


def _normalized_label_value(value: Any) -> str:
    return str(value or "").strip().casefold()


def _label_lookup_for_rows(
    db: Session, rows: list[PublishedModelCatalog]
) -> dict[str, AnnotationLabel]:
    normalized_values = {
        _normalized_label_value(class_name)
        for row in rows
        for class_name in _json_list(row.labels_json)
        if _normalized_label_value(class_name)
    }
    if not normalized_values:
        return {}
    labels = (
        db.query(AnnotationLabel)
        .filter(
            AnnotationLabel.deleted_at.is_(None),
            AnnotationLabel.normalized_label_value.in_(sorted(normalized_values)),
        )
        .all()
    )
    return {
        _normalized_label_value(label.normalized_label_value): label for label in labels
    }


def _class_label_responses(
    class_names: list[str], label_lookup: dict[str, AnnotationLabel]
) -> list[PublishedModelClassLabelResponse]:
    responses: list[PublishedModelClassLabelResponse] = []
    for class_index, class_name in enumerate(class_names):
        label = label_lookup.get(_normalized_label_value(class_name))
        responses.append(
            PublishedModelClassLabelResponse(
                class_index=class_index,
                label_key=str(label.label_key) if label else None,
                label_value=str(label.label_value) if label else class_name,
                color=str(label.color) if label else None,
                status=str(label.status) if label else None,
                annotation_kind=str(label.annotation_kind) if label else None,
                matched=label is not None,
            )
        )
    return responses


def _row_response(
    row: PublishedModelCatalog,
    label_lookup: Optional[dict[str, AnnotationLabel]] = None,
) -> PublishedModelResponse:
    class_names = _json_list(row.labels_json)
    return PublishedModelResponse(
        catalog_id=int(row.id),
        model_id=str(row.public_model_id),
        download_path=(
            "/api/v1/public/models/"
            f"{quote(str(row.public_model_id), safe='')}/download"
        ),
        aicm_model_set_id=row.aicm_model_set_id,
        aicm_environment_id=row.aicm_environment_id,
        export_id=row.export_id,
        yolo_train_job_id=row.yolo_train_job_id,
        task_id=row.task_id,
        name=str(row.name or ""),
        display_name=str(row.display_name or row.name or ""),
        source_type=str(row.source_type or ""),
        source_system=str(row.source_system or ""),
        status=str(row.status or "active"),
        is_public=bool(row.is_public),
        download_enabled=bool(row.download_enabled),
        availability_status=str(row.availability_status or "unknown"),
        chip_type=row.chip_type,
        algorithm_type=row.algorithm_type,
        precision=_row_precision(row),
        resolution=row.resolution,
        map50=_as_float(row.map50),
        class_names=class_names,
        class_labels=_class_label_responses(class_names, label_lookup or {}),
        remote_download_url=row.remote_download_url,
        local_package_available=_existing_local_package(row) is not None,
        package_size_bytes=row.package_size_bytes,
        package_sha256=row.package_sha256,
        package_md5=row.package_md5,
        publisher_username=row.publisher_username,
        publish_job_id=row.publish_job_id,
        published_at=row.published_at,
        last_synced_at=row.last_synced_at,
        aliases=[
            PublishedModelAliasResponse(
                id=int(alias.id),
                source_system=str(alias.source_system),
                external_model_id=str(alias.external_model_id),
                alias_type=str(alias.alias_type),
                created_at=alias.created_at,
            )
            for alias in sorted(row.aliases or [], key=lambda item: int(item.id))
        ],
    )


def _catalog_label_analysis(db: Session) -> dict[int, dict[str, Any]]:
    """Build versions from the whole directory, independent of page/filter order."""
    from labelu.internal.application.service.aicm_model_analysis import analyze_models

    groups: dict[str, list[dict[str, Any]]] = {}
    catalog_ids: dict[tuple[str, str], int] = {}
    rows = (
        db.query(
            PublishedModelCatalog.id,
            PublishedModelCatalog.public_model_id,
            PublishedModelCatalog.aicm_environment_id,
            PublishedModelCatalog.name,
            PublishedModelCatalog.labels_json,
            PublishedModelCatalog.published_at,
        )
        .filter(
            PublishedModelCatalog.deleted_at.is_(None),
            PublishedModelCatalog.availability_status == "available",
        )
        .all()
    )
    for row in rows:
        environment = str(row.aicm_environment_id or "")
        identifier = str(row.public_model_id)
        catalog_ids[(environment, identifier)] = int(row.id)
        groups.setdefault(environment, []).append(
            {
                "id": identifier,
                "name": row.name,
                "label_class_set_obj_info": [
                    {"key": value} for value in _json_list(row.labels_json)
                ],
                "published_at": (
                    row.published_at.astimezone().isoformat()
                    if row.published_at
                    else None
                ),
            }
        )
    result: dict[int, dict[str, Any]] = {}
    for environment, models in groups.items():
        for model in analyze_models(models)["models"]:
            result[catalog_ids[(environment, str(model["id"]))]] = model
    return result


def _attach_label_analysis(
    response: PublishedModelResponse, analysis: dict[str, Any]
) -> PublishedModelResponse:
    response.label_group_version = analysis.get("version_label")
    response.label_group_size = int(analysis.get("label_group_size") or 0)
    response.related_model_count = int(analysis.get("related_model_count") or 0)
    return response


def _summary(db: Session) -> PublishedModelSummaryResponse:
    base = db.query(PublishedModelCatalog).filter(
        PublishedModelCatalog.deleted_at.is_(None)
    )
    total = base.count()
    active = base.filter(PublishedModelCatalog.status == "active").count()
    public = base.filter(
        PublishedModelCatalog.status == "active",
        PublishedModelCatalog.is_public.is_(True),
        PublishedModelCatalog.download_enabled.is_(True),
        (
            (PublishedModelCatalog.package_rel_path.isnot(None))
            | (PublishedModelCatalog.availability_status == "available")
        ),
    ).count()
    unavailable = base.filter(
        PublishedModelCatalog.package_rel_path.is_(None),
        PublishedModelCatalog.availability_status != "available",
    ).count()
    legacy = base.filter(PublishedModelCatalog.source_type == "remote_legacy").count()
    return PublishedModelSummaryResponse(
        total=total,
        active=active,
        public=public,
        unavailable=unavailable,
        legacy=legacy,
    )


def list_models(
    db: Session,
    *,
    page: int = 0,
    size: int = 20,
    q: str = "",
    export_id: Optional[int] = None,
    yolo_train_job_id: Optional[int] = None,
    status: str = "",
    availability_status: str = "",
    source_type: str = "",
    is_public: Optional[bool] = None,
    public_only: bool = False,
) -> tuple[PublishedModelListResponse, int]:
    download_enabled: Optional[bool] = None
    if public_only:
        status = "active"
        is_public = True
        download_enabled = True
    size = min(max(int(size), 1), 100)
    page = max(int(page), 0)
    rows, total = catalog_crud.list_rows(
        db,
        page=page,
        size=size,
        query_text=q,
        export_id=export_id,
        yolo_train_job_id=yolo_train_job_id,
        status=status,
        availability_status=availability_status,
        source_type=source_type,
        is_public=is_public,
        download_enabled=download_enabled,
        downloadable_only=public_only,
    )
    summary = (
        _summary(db)
        if not public_only
        else PublishedModelSummaryResponse(total=total, active=total, public=total)
    )
    label_lookup = _label_lookup_for_rows(db, rows)
    label_analysis = _catalog_label_analysis(db) if rows and not public_only else {}
    return (
        PublishedModelListResponse(
            summary=summary,
            models=[
                _attach_label_analysis(
                    _row_response(row, label_lookup),
                    label_analysis.get(int(row.id), {}),
                )
                for row in rows
            ],
        ),
        total,
    )


def get_model(
    db: Session,
    identifier: str,
    *,
    source_system: Optional[str] = None,
    public_only: bool = False,
) -> PublishedModelResponse:
    row = resolve_model(db, identifier, source_system=source_system)
    if public_only and (
        row.status != "active"
        or not row.is_public
        or not row.download_enabled
        or (
            _existing_local_package(row) is None
            and row.availability_status != "available"
        )
    ):
        raise _not_found(identifier)
    response = _row_response(row, _label_lookup_for_rows(db, [row]))
    if not public_only:
        _attach_label_analysis(
            response, _catalog_label_analysis(db).get(int(row.id), {})
        )
    return response


def _not_found(identifier: str) -> LabelUException:
    return LabelUException(
        code=ErrorCode.CODE_30003_CLIENT_ERROR,
        status_code=http_status.HTTP_404_NOT_FOUND,
        message=f"未找到模型：{identifier}",
    )


def resolve_model(
    db: Session,
    identifier: str,
    *,
    source_system: Optional[str] = None,
) -> PublishedModelCatalog:
    text = str(identifier or "").strip()
    namespace = str(source_system or "").strip().lower() or None
    alias_value = text
    if not namespace and ":" in text:
        possible_namespace, possible_value = text.split(":", 1)
        if possible_namespace and possible_value:
            namespace = possible_namespace.strip().lower()
            alias_value = possible_value.strip()
    matches = catalog_crud.list_by_alias(db, alias_value, source_system=namespace)
    distinct = {int(item.id): item for item in matches}
    if len(distinct) == 1:
        return next(iter(distinct.values()))
    if len(distinct) > 1:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message=f"模型标识不唯一，请带 source_system：{text}",
        )
    row = catalog_crud.get_by_public_model_id(db, text)
    if row:
        return row
    if text.isdigit():
        row = catalog_crud.get_by_aicm_model_set_id(db, int(text))
        if row:
            return row
    raise _not_found(text)


def _ensure_alias(
    db: Session,
    row: PublishedModelCatalog,
    *,
    source_system: str,
    external_model_id: str,
    alias_type: str,
    created_by: Optional[int] = None,
) -> None:
    source = str(source_system or "").strip().lower()
    external = str(external_model_id or "").strip()
    if not source or not external:
        return
    existing = catalog_crud.get_alias(db, source, external)
    if existing:
        if int(existing.catalog_id) != int(row.id):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=http_status.HTTP_409_CONFLICT,
                message=f"模型标识已绑定到其他模型：{source}:{external}",
            )
        return
    catalog_crud.create_alias(
        db,
        catalog_id=int(row.id),
        source_system=source,
        external_model_id=external,
        alias_type=alias_type,
        created_by=created_by,
    )


def register_published_model(
    db: Session,
    *,
    export_id: int,
    model_set_id: int,
    environment_id: str,
    precision: Optional[str] = None,
    publish_job_id: str = "",
    upload_name: str = "",
    publisher_username: str = "",
    published_at: Optional[datetime] = None,
    package_path: str = "",
    package_required: bool = False,
    published_by_id: Optional[int] = None,
) -> PublishedModelCatalog:
    public_model_id = str(int(model_set_id))
    export_row = crud_yolo_bm1688_export.get(db, int(export_id))
    train_job = (
        crud_yolo_train_job.get(db, int(export_row.yolo_train_job_id))
        if export_row
        else None
    )
    task = crud_task.get(db, int(export_row.task_id)) if export_row else None
    row = catalog_crud.get_by_public_model_id(db, public_model_id)
    if row is not None:
        _assert_aicm_identity_not_rebound(
            row,
            model_set_id=int(model_set_id),
            environment_id=environment_id,
        )
    labels = []
    if train_job:
        labels = yolo_aicm_publish._class_names_for_publish(train_job)
    values: dict[str, Any] = {
        "aicm_environment_id": str(environment_id or "").strip() or None,
        "aicm_model_set_id": int(model_set_id),
        "export_id": int(export_id),
        "yolo_train_job_id": int(export_row.yolo_train_job_id) if export_row else None,
        "task_id": int(export_row.task_id) if export_row else None,
        "name": str(upload_name or (task.name if task else public_model_id)),
        "display_name": str(upload_name or (task.name if task else public_model_id)),
        "source_type": "labelu_publish",
        "source_system": "aicm",
        "status": "active",
        "is_public": True,
        "download_enabled": True,
        "availability_status": "available",
        "resolution": (
            f"{int(export_row.imgsz)}x{int(export_row.imgsz)}"
            if export_row and export_row.imgsz
            else None
        ),
        "labels_json": json.dumps(labels, ensure_ascii=False),
        "publisher_username": str(publisher_username or "") or None,
        "publish_job_id": str(publish_job_id or "") or None,
        "published_at": published_at or datetime.now(),
        "last_synced_at": datetime.now(),
    }
    normalized_precision = _normalize_precision(precision)
    if normalized_precision:
        values["precision"] = normalized_precision
    package_source = str(package_path or "").strip()
    if package_source:
        try:
            values.update(_persist_local_package(public_model_id, package_source))
        except LabelUException:
            if package_required:
                raise
    elif package_required:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message=f"模型 {public_model_id} 未生成 LabelU 本地最终模型包",
        )
    if row:
        values.update(
            display_name=row.display_name or values["display_name"],
            status=row.status,
            is_public=row.is_public,
            download_enabled=row.download_enabled,
            published_at=row.published_at or values["published_at"],
        )
        row = catalog_crud.update(db, row, **values)
    else:
        row = catalog_crud.create(db, public_model_id=public_model_id, **values)
    _ensure_alias(
        db,
        row,
        source_system="aicm",
        external_model_id=public_model_id,
        alias_type="aicm",
    )
    # AICM publish success is the external fact. Keep the canonical model
    # version and the platform download catalog in sync so the two model-center
    # views cannot report contradictory states. Historical rows without a
    # canonical ModelVersion remain readable and can be reconciled later.
    if train_job:
        model_version = crud_model_version.get_by_training_job(db, int(train_job.id))
        if model_version is not None:
            model_version.published_catalog_id = int(row.id)
            model_version.status = "PUBLISHED"
            model_version.published_at = (
                model_version.published_at or values["published_at"]
            )
            if published_by_id is not None:
                model_version.published_by = int(published_by_id)
            db.flush()
    # A third-party S3 package may have been waiting for this exact AICM model.
    # Wake only matching rows; the dedicated import Worker still performs the
    # actual S3 and task side effects.
    from labelu.internal.application.service import experiment_dataset_import

    experiment_dataset_import.wake_waiting_imports(
        db,
        status=experiment_dataset_import.IMPORT_STATUS_WAITING_FOR_MODEL,
        model_id=public_model_id,
        reason="published_model_registered",
    )
    return row


def _history_model_id(raw: dict[str, Any]) -> Optional[int]:
    result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
    if not result.get("ok"):
        return None
    for key in ("model_set_id", "model_id"):
        value = result.get(key)
        match = re.fullmatch(r"\s*(\d+)\s*", str(value or ""))
        if match:
            return int(match.group(1))
    return yolo_aicm_publish._published_model_set_id(raw)


def _remote_values(remote: dict[str, Any], *, environment_id: str) -> dict[str, Any]:
    model_set_id = yolo_aicm_publish._model_set_row_id(remote)
    labels = [
        str(item.get("key") or item.get("name") or "").strip()
        for item in (remote.get("label_class_set_obj_info") or [])
        if isinstance(item, dict)
        and str(item.get("key") or item.get("name") or "").strip()
    ]
    name = str(remote.get("name") or model_set_id)
    return {
        "public_model_id": str(model_set_id),
        "aicm_environment_id": environment_id or None,
        "aicm_model_set_id": model_set_id,
        "name": name,
        "display_name": name,
        "source_type": "remote_legacy",
        "source_system": "aicm",
        "status": "active",
        "is_public": True,
        "download_enabled": True,
        "availability_status": "available",
        "chip_type": str(remote.get("chip_type") or "") or None,
        "algorithm_type": str(remote.get("algorithm_type") or "") or None,
        "precision": _normalize_precision(
            remote.get("precision")
            or remote.get("bmodel_precision")
            or remote.get("model_precision")
        ),
        "resolution": str(remote.get("resolution") or "") or None,
        "map50": str(remote.get("mAP")) if remote.get("mAP") is not None else None,
        "labels_json": json.dumps(labels, ensure_ascii=False),
        "published_at": _as_datetime(
            remote.get("published_at") or remote.get("created_at")
        ),
        "last_synced_at": datetime.now(),
    }


def _fetch_all_remote_models(environment_id: str) -> list[dict[str, Any]]:
    cfg = load_aicm_publish_config(environment_id=environment_id)
    if not config_is_complete(cfg):
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=http_status.HTTP_400_BAD_REQUEST,
            message="AICM 发布环境配置不完整，无法同步模型列表",
        )
    session = yolo_aicm_publish._aicm_session(cfg)
    try:
        base = yolo_aicm_publish._normalize_base_url(cfg.get("base_url") or "")
        verify = bool(cfg.get("verify_ssl"))
        rows: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        expected_total: Optional[int] = None
        page = 1
        while True:
            response = session.get(
                f"{base}/v2/model-set/",
                params={
                    "_page": page,
                    "_limit": 100,
                    "_method": "list",
                    "_filter_in_use": "",
                    "_filter_engine_id": "",
                    "_like_name": "",
                    "_sort": "",
                    "_filter-exact_list-learning_task__label_class_set__id": "",
                    "_filter-gte-created_at": "",
                    "_filter-lt-created_at": "",
                    "_filter_model_source": "",
                },
                timeout=yolo_aicm_publish.AICM_HTTP_TIMEOUT,
                verify=verify,
            )
            if response.status_code >= 400:
                raise LabelUException(
                    code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                    status_code=http_status.HTTP_502_BAD_GATEWAY,
                    message=(
                        "AICM 模型列表同步失败 "
                        f"HTTP {response.status_code}: {response.text[:300]}"
                    ),
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise LabelUException(
                    code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                    status_code=http_status.HTTP_502_BAD_GATEWAY,
                    message="AICM 模型列表不是有效 JSON，已停止同步并保留本地目录",
                ) from exc
            if isinstance(payload, dict) and payload.get("error_code") not in (
                None,
                0,
                "0",
            ):
                raise LabelUException(
                    code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                    status_code=http_status.HTTP_502_BAD_GATEWAY,
                    message="AICM 模型列表返回业务错误，请检查发布配置中的登录信息",
                )
            data = payload.get("data") if isinstance(payload, dict) else payload
            if not isinstance(data, (dict, list)) or (
                isinstance(data, dict)
                and not any(
                    isinstance(data.get(key), list) for key in ("list", "rows", "items")
                )
            ):
                raise LabelUException(
                    code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                    status_code=http_status.HTTP_502_BAD_GATEWAY,
                    message="AICM 模型列表缺少有效列表字段，已停止同步并保留本地目录",
                )
            batch = yolo_aicm_publish._extract_rows(data)
            if isinstance(data, dict) and data.get("total") is not None:
                total = int(data["total"])
                if expected_total is not None and total != expected_total:
                    raise LabelUException(
                        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                        status_code=http_status.HTTP_502_BAD_GATEWAY,
                        message="AICM 模型数量在分页期间发生变化，请重新同步",
                    )
                expected_total = total
            for remote in batch:
                identifier = yolo_aicm_publish._model_set_row_id(remote)
                if identifier <= 0 or identifier in seen_ids:
                    raise LabelUException(
                        code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                        status_code=http_status.HTTP_502_BAD_GATEWAY,
                        message="AICM 分页包含无效或重复模型编号，请重新同步；本地目录未更改",
                    )
                seen_ids.add(identifier)
            rows.extend(batch)
            has_next = data.get("has_next") if isinstance(data, dict) else None
            if has_next is False or (
                has_next is None
                and (
                    (expected_total is not None and len(rows) >= expected_total)
                    or (expected_total is None and len(batch) < 100)
                )
            ):
                break
            if not batch or page >= 10000:
                raise LabelUException(
                    code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                    status_code=http_status.HTTP_502_BAD_GATEWAY,
                    message="AICM 分页未能完整读取，已停止同步并保留本地目录",
                )
            page += 1
        if expected_total is not None and len(rows) != expected_total:
            raise LabelUException(
                code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                message=f"AICM 模型列表不完整：应有 {expected_total} 条，实际 {len(rows)} 条，已停止同步",
            )
        return rows
    finally:
        session.close()


_catalog_sync_lock = threading.Lock()


def sync_models(
    db: Session,
    *,
    environment_id: str = "",
    include_remote_models: bool = True,
) -> PublishedModelSyncResponse:
    # Remote I/O happens before the database transaction; reject duplicate
    # clicks instead of holding additional HTTP connections and DB writers.
    if not _catalog_sync_lock.acquire(blocking=False):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message="模型目录正在同步，请等待本次同步完成",
        )
    try:
        return _sync_models(
            db,
            environment_id=environment_id,
            include_remote_models=include_remote_models,
        )
    finally:
        _catalog_sync_lock.release()


def _sync_models(
    db: Session,
    *,
    environment_id: str = "",
    include_remote_models: bool = True,
) -> PublishedModelSyncResponse:
    env_id = str(environment_id or "").strip() or get_active_environment_id()
    history, history_total = list_jobs(page=0, size=100)
    all_history = list(history)
    page = 1
    while page * 100 < history_total:
        batch, _ = list_jobs(page=page, size=100)
        all_history.extend(batch)
        page += 1
    remote_rows = _fetch_all_remote_models(env_id) if include_remote_models else []
    created = 0
    updated = 0
    seen_remote_ids: set[int] = set()
    with begin_transaction(db):
        for raw in all_history:
            model_set_id = _history_model_id(raw)
            export_id = int(raw.get("export_id") or 0)
            if not model_set_id or export_id <= 0:
                continue
            existed = (
                catalog_crud.get_by_public_model_id(db, str(model_set_id)) is not None
            )
            result = raw.get("result") if isinstance(raw.get("result"), dict) else {}
            register_published_model(
                db,
                export_id=export_id,
                model_set_id=model_set_id,
                environment_id=str(raw.get("environment_id") or env_id),
                precision=_precision_from_publish_record(raw),
                publish_job_id=str(raw.get("job_id") or ""),
                upload_name=str(
                    result.get("upload_name") or raw.get("task_name") or ""
                ),
                publisher_username=str(raw.get("publisher_username") or ""),
                published_at=_as_datetime(
                    raw.get("created_at") or raw.get("updated_at")
                ),
                package_path=str(result.get("package_path") or ""),
            )
            updated += int(existed)
            created += int(not existed)
        for remote in remote_rows:
            values = _remote_values(remote, environment_id=env_id)
            model_set_id = int(values["aicm_model_set_id"])
            if model_set_id <= 0:
                continue
            seen_remote_ids.add(model_set_id)
            row = catalog_crud.get_by_public_model_id(db, str(model_set_id))
            if row:
                _assert_aicm_identity_not_rebound(
                    row,
                    model_set_id=model_set_id,
                    environment_id=env_id,
                )
                preserved = {
                    "source_type": row.source_type,
                    "display_name": row.display_name or values["display_name"],
                    "status": row.status,
                    "is_public": row.is_public,
                    "download_enabled": row.download_enabled,
                }
                if row.precision and not values.get("precision"):
                    preserved["precision"] = row.precision
                values.update(preserved)
                catalog_crud.update(db, row, **values)
                updated += 1
            else:
                row = catalog_crud.create(db, **values)
                created += 1
            _ensure_alias(
                db,
                row,
                source_system="aicm",
                external_model_id=str(model_set_id),
                alias_type="aicm",
            )
        if include_remote_models:
            candidates = db.query(PublishedModelCatalog).filter(
                PublishedModelCatalog.deleted_at.is_(None),
                PublishedModelCatalog.aicm_environment_id == (env_id or None),
                PublishedModelCatalog.aicm_model_set_id.isnot(None),
            )
            for row in candidates.all():
                if int(row.aicm_model_set_id) not in seen_remote_ids:
                    catalog_crud.update(
                        db,
                        row,
                        availability_status="unavailable",
                        last_synced_at=datetime.now(),
                    )
        from labelu.internal.application.service import experiment_dataset_import

        experiment_dataset_import.wake_waiting_imports(
            db,
            status=experiment_dataset_import.IMPORT_STATUS_WAITING_FOR_MODEL,
            reason="published_model_catalog_synced",
        )
    unavailable = (
        db.query(func.count(PublishedModelCatalog.id))
        .filter(
            PublishedModelCatalog.deleted_at.is_(None),
            PublishedModelCatalog.availability_status == "unavailable",
        )
        .scalar()
        or 0
    )
    return PublishedModelSyncResponse(
        history_scanned=len(all_history),
        remote_scanned=len(remote_rows),
        created=created,
        updated=updated,
        unavailable=int(unavailable),
        environment_id=env_id,
    )


def update_model(
    db: Session,
    current_user: User,
    catalog_id: int,
    cmd: UpdatePublishedModelCommand,
) -> PublishedModelResponse:
    _ = current_user
    row = catalog_crud.get(db, catalog_id)
    if not row:
        raise _not_found(str(catalog_id))
    values = cmd.model_dump(exclude_unset=True)
    expected_download_enabled = values.pop("expected_download_enabled", None)
    retired_controls = {"status", "is_public"}.intersection(values)
    if retired_controls:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=http_status.HTTP_400_BAD_REQUEST,
            message=(
                "已发布模型不支持撤回、隐藏或重新公开；"
                "模型有问题时请关闭下载开关并发布替代模型"
            ),
        )
    if "display_name" in values:
        values["display_name"] = str(values["display_name"] or "").strip()
    with begin_transaction(db):
        row = catalog_crud.get(db, catalog_id)
        if "download_enabled" in values and expected_download_enabled is None:
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=http_status.HTTP_409_CONFLICT,
                message="修改下载开关必须携带当前值，请刷新后重试",
            )
        if "download_enabled" in values and expected_download_enabled is not None:
            next_value = bool(values.pop("download_enabled"))
            row = catalog_crud.compare_and_set_download_enabled(
                db,
                catalog_id=catalog_id,
                expected=bool(expected_download_enabled),
                value=next_value,
            )
            if row is None:
                raise LabelUException(
                    code=ErrorCode.CODE_30003_CLIENT_ERROR,
                    status_code=http_status.HTTP_409_CONFLICT,
                    message="下载开关已被其他人修改，请刷新后重试",
                )
        if values:
            row = catalog_crud.update(db, row, **values)
    if cmd.download_enabled is not None:
        biz_log(
            "published_model.download_switch.changed",
            catalog_id=int(catalog_id),
            model_id=str(row.public_model_id),
            user_id=int(current_user.id),
            download_enabled=bool(row.download_enabled),
        )
    return _row_response(row, _label_lookup_for_rows(db, [row]))


def add_alias(
    db: Session,
    current_user: User,
    catalog_id: int,
    cmd: CreatePublishedModelAliasCommand,
) -> PublishedModelResponse:
    row = catalog_crud.get(db, catalog_id)
    if not row:
        raise _not_found(str(catalog_id))
    source = cmd.source_system.strip().lower()
    external = cmd.external_model_id.strip()
    if source == "aicm":
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=http_status.HTTP_400_BAD_REQUEST,
            message="aicm 是系统保留来源，请填写第三方来源系统",
        )
    existing = catalog_crud.get_alias(db, source, external)
    if existing and int(existing.catalog_id) != int(catalog_id):
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message=f"别名已绑定到其他模型：{source}:{external}",
        )
    if not existing:
        with begin_transaction(db):
            catalog_crud.create_alias(
                db,
                catalog_id=catalog_id,
                source_system=source,
                external_model_id=external,
                alias_type=cmd.alias_type,
                created_by=int(current_user.id),
            )
    row = catalog_crud.get(db, catalog_id)
    return _row_response(row, _label_lookup_for_rows(db, [row]))


def remove_alias(db: Session, catalog_id: int, alias_id: int) -> PublishedModelResponse:
    row = catalog_crud.get(db, catalog_id)
    if not row:
        raise _not_found(str(catalog_id))
    protected_alias = next(
        (alias for alias in row.aliases if int(alias.id) == int(alias_id)), None
    )
    if protected_alias and protected_alias.alias_type == "aicm":
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message="AICM 兼容标识由系统维护，不能删除",
        )
    with begin_transaction(db):
        if not catalog_crud.delete_alias(db, catalog_id, alias_id):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=http_status.HTTP_404_NOT_FOUND,
                message="模型别名不存在",
            )
    row = catalog_crud.get(db, catalog_id)
    return _row_response(row, _label_lookup_for_rows(db, [row]))


def _cache_remote_package(
    db: Session, row: PublishedModelCatalog
) -> PublishedModelCatalog:
    catalog_id = int(row.id)
    package_rel_path = _package_rel_path(row.public_model_id)
    target = _resolve_package_rel_path(package_rel_path)
    if target.exists():
        if not target.is_file() or not zipfile.is_zipfile(target):
            raise LabelUException(
                code=ErrorCode.CODE_30003_CLIENT_ERROR,
                status_code=http_status.HTTP_409_CONFLICT,
                message=(
                    f"模型 {row.public_model_id} 已有的长期保留包无效，"
                    "系统不会自动删除或覆盖"
                ),
            )
        metadata = _package_metadata(target, package_rel_path)
        with begin_transaction(db):
            current = catalog_crud.get(db, catalog_id)
            return catalog_crud.update(db, current, **metadata)
    if not row.aicm_model_set_id:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message=(
                f"模型 {row.public_model_id} 在 LabelU 中没有本地模型包，"
                "且未绑定可用于补缓存的 AICM model_set_id"
            ),
        )
    model_set_id = int(row.aicm_model_set_id)
    env_id = str(row.aicm_environment_id or "").strip() or get_active_environment_id()
    cfg = load_aicm_publish_config(environment_id=env_id)
    if not config_is_complete(cfg):
        raise LabelUException(
            code=ErrorCode.CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE,
            status_code=http_status.HTTP_409_CONFLICT,
            message=(
                f"模型 {row.public_model_id} 在 LabelU 中没有本地模型包，"
                "AICM 配置也不完整，无法执行一次性补缓存"
            ),
        )
    session = yolo_aicm_publish._aicm_session(cfg)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    download_url = ""
    try:
        base = yolo_aicm_publish._normalize_base_url(cfg.get("base_url") or "")
        verify = bool(cfg.get("verify_ssl"))
        download_url = yolo_aicm_publish._get_download_url(
            session, base, model_set_id, verify=verify
        )
        response = session.get(
            download_url,
            timeout=yolo_aicm_publish.AICM_HTTP_TIMEOUT,
            verify=verify,
            stream=True,
        )
        try:
            if response.status_code >= 400:
                raise LabelUException(
                    code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                    status_code=http_status.HTTP_502_BAD_GATEWAY,
                    message=(
                        f"AICM 模型包补缓存失败 HTTP {response.status_code}: "
                        f"{response.text[:300]}"
                    ),
                )
            with temporary.open("wb") as stream:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        stream.write(chunk)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if not zipfile.is_zipfile(temporary):
            raise LabelUException(
                code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
                status_code=http_status.HTTP_502_BAD_GATEWAY,
                message=f"AICM 为模型 {model_set_id} 返回的文件不是有效 ZIP",
            )
        _install_package_once(
            temporary,
            target,
            model_id=str(row.public_model_id),
        )
        metadata = _package_metadata(target, package_rel_path)
    except Exception:
        with begin_transaction(db):
            current = catalog_crud.get(db, catalog_id)
            if current:
                catalog_crud.update(
                    db,
                    current,
                    availability_status="unavailable",
                    last_synced_at=datetime.now(),
                )
        raise
    finally:
        if temporary.exists():
            temporary.unlink()
        session.close()
    with begin_transaction(db):
        current = catalog_crud.get(db, catalog_id)
        current = catalog_crud.update(
            db,
            current,
            remote_download_url=download_url,
            availability_status="available",
            last_synced_at=datetime.now(),
            **metadata,
        )
    return current


def resolve_download_file(
    db: Session,
    identifier: str,
    *,
    source_system: Optional[str] = None,
    public_only: bool = False,
) -> ResolvedPublishedModelPackage:
    row = resolve_model(db, identifier, source_system=source_system)
    if public_only and (
        row.status != "active" or not row.is_public or not row.download_enabled
    ):
        raise _not_found(identifier)
    if not row.download_enabled:
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message=(
                f"模型 {row.public_model_id} 的下载开关已由管理员关闭，"
                "当前不提供模型包下载"
            ),
        )
    local_path = _existing_local_package(row, strict_path=True)
    if local_path is None:
        row = _cache_remote_package(db, row)
        local_path = _existing_local_package(row, strict_path=True)
    if local_path is None or not zipfile.is_zipfile(local_path):
        raise LabelUException(
            code=ErrorCode.CODE_57028_YOLO_AICM_REMOTE_ERROR,
            status_code=http_status.HTTP_409_CONFLICT,
            message=f"模型 {row.public_model_id} 在 LabelU 中没有可用的最终模型包",
        )
    _assert_package_integrity(row, local_path)
    return ResolvedPublishedModelPackage(
        catalog_id=int(row.id),
        model_id=str(row.public_model_id),
        aicm_model_set_id=(
            int(row.aicm_model_set_id) if row.aicm_model_set_id is not None else None
        ),
        file_path=local_path,
        filename=_download_filename(row),
    )


def resolve_download(
    db: Session,
    identifier: str,
    *,
    source_system: Optional[str] = None,
    public_only: bool = False,
) -> PublishedModelDownloadResponse:
    resolved = resolve_download_file(
        db,
        identifier,
        source_system=source_system,
        public_only=public_only,
    )
    return PublishedModelDownloadResponse(
        catalog_id=resolved.catalog_id,
        model_id=resolved.model_id,
        aicm_model_set_id=resolved.aicm_model_set_id,
        download_url=(
            "/api/v1/published-models/" f"{quote(resolved.model_id, safe='')}/download"
        ),
        filename=resolved.filename,
    )
