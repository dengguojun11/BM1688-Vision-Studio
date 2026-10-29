"""VLX-Seek 1.5 图片检测预标注。每张图片一次 multipart 请求。"""
from __future__ import annotations

import asyncio
import io
import json
import re
import time
import uuid
from typing import Any

import httpx
from PIL import Image
from fastapi import status
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import (
    crud_locateanything_preannotate_job,
    crud_pre_annotation,
    crud_sample,
    crud_task,
)
from labelu.internal.application.command.locateanything_preannotate import BatchLocateAnythingPreannotateCommand
from labelu.internal.application.response.locateanything_preannotate import LocateAnythingPreannotateJobResponse
from labelu.internal.application.service import auto_label as auto_label_mod
from labelu.internal.application.service.task_background_jobs import assert_no_active_task_background_job
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.model_access_config import (
    has_configured_model_integration,
    request_headers,
    resolve_model_integration,
)
from labelu.internal.common.storage import get_model_read_url, get_storage_backend
from labelu.internal.common.task_access import assert_user_has_task_access
from labelu.internal.domain.models.auto_label_job import AutoLabelStatus
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import SampleState
from labelu.internal.domain.models.task import MediaType, TaskStatus
from labelu.internal.domain.models.user import User

VLX_PRE_META = "vlx_seek_generated"
REX_PRE_META = "rex_omni_generated"
_LOG = "[vlx-seek-preannotate]"


def _provider_auth_headers(provider: str) -> dict[str, str]:
    """Return configured service credentials for the selected provider."""
    key = "rex_omni" if provider == "rex" else "vlx_seek"
    integration = resolve_model_integration(key)
    return request_headers(integration) if integration else {}


def _provider_settings(provider: str) -> tuple[bool, str, int, str, bool, int, str, str]:
    if provider == "rex":
        integration = resolve_model_integration("rex_omni")
        if integration:
            return (
                bool(integration.get("enabled", True)),
                str(integration.get("endpoint") or "").strip(),
                int(integration.get("timeout_seconds") or settings.REX_OMNI_TIMEOUT_SECONDS),
                str(integration.get("model") or settings.REX_OMNI_MODEL).strip(),
                bool(integration.get("verify_tls", settings.REX_OMNI_VERIFY_TLS)),
                int(integration.get("max_new_tokens") or settings.REX_OMNI_MAX_NEW_TOKENS),
                REX_PRE_META,
                "rex-omni",
            )
        if has_configured_model_integration("rex_omni"):
            return (False, "", settings.REX_OMNI_TIMEOUT_SECONDS, settings.REX_OMNI_MODEL, settings.REX_OMNI_VERIFY_TLS, settings.REX_OMNI_MAX_NEW_TOKENS, REX_PRE_META, "rex-omni")
        return (
            settings.REX_OMNI_ENABLED,
            settings.REX_OMNI_ENDPOINT,
            settings.REX_OMNI_TIMEOUT_SECONDS,
            settings.REX_OMNI_MODEL,
            settings.REX_OMNI_VERIFY_TLS,
            settings.REX_OMNI_MAX_NEW_TOKENS,
            REX_PRE_META,
            "rex-omni",
        )
    integration = resolve_model_integration("vlx_seek")
    if integration:
        return (
            bool(integration.get("enabled", True)),
            str(integration.get("endpoint") or "").strip(),
            int(integration.get("timeout_seconds") or settings.VLX_SEEK_TIMEOUT_SECONDS),
            str(integration.get("model") or settings.VLX_SEEK_MODEL).strip(),
            bool(integration.get("verify_tls", settings.VLX_SEEK_VERIFY_TLS)),
            int(integration.get("max_new_tokens") or settings.VLX_SEEK_MAX_NEW_TOKENS),
            VLX_PRE_META,
            "vlx-seek",
        )
    if has_configured_model_integration("vlx_seek"):
        return (False, "", settings.VLX_SEEK_TIMEOUT_SECONDS, settings.VLX_SEEK_MODEL, settings.VLX_SEEK_VERIFY_TLS, settings.VLX_SEEK_MAX_NEW_TOKENS, VLX_PRE_META, "vlx-seek")
    return (
        settings.VLX_SEEK_ENABLED,
        settings.VLX_SEEK_ENDPOINT,
        settings.VLX_SEEK_TIMEOUT_SECONDS,
        settings.VLX_SEEK_MODEL,
        settings.VLX_SEEK_VERIFY_TLS,
        settings.VLX_SEEK_MAX_NEW_TOKENS,
        VLX_PRE_META,
        "vlx-seek",
    )


def _rect_labels(task_config: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for tool in task_config.get("tools", []) or []:
        if not isinstance(tool, dict) or tool.get("tool") != "rectTool":
            continue
        for attr in (tool.get("config") or {}).get("attributes", []) or []:
            if isinstance(attr, dict) and (attr.get("value") or attr.get("key")):
                out.append({"value": str(attr.get("value") or attr.get("key")), "key": str(attr.get("key") or attr.get("value"))})
    return out


def _explicit_ids(cmd: BatchLocateAnythingPreannotateCommand) -> list[int] | None:
    values = [*(cmd.sample_ids or []), *(([cmd.sample_id]) if cmd.sample_id is not None else [])]
    result = list(dict.fromkeys(int(value) for value in values))
    return result or None


def _job_response(job, provider: str = "vlx") -> LocateAnythingPreannotateJobResponse:
    _, _, _, model, _, _, _, _ = _provider_settings(provider)
    return LocateAnythingPreannotateJobResponse(
        id=job.id, task_id=job.task_id, status=job.status, sample_count=job.sample_count or 0,
        processed_count=job.processed_count or 0, success_count=job.success_count or 0,
        failed_count=job.failed_count or 0, model=model,
        error_message=job.error_message, created_at=job.created_at,
    )


def _is_provider(data: Any, provider: str = "vlx") -> bool:
    _, _, _, _, _, _, source_type, _ = _provider_settings(provider)
    try:
        value = json.loads(data) if isinstance(data, str) else data
        return isinstance(value, dict) and (value.get("meta") or {}).get("source_type") == source_type
    except Exception:
        return False


def _is_vlx(data: Any) -> bool:
    return _is_provider(data, "vlx")


def _labels_text(labels: list[dict[str, Any]]) -> str:
    return "; ".join(item["key"] for item in labels)


def _target_labels(task_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Restrict VLX classes to the task's frozen class contract when present."""
    labels = _rect_labels(task_config)
    pool = task_config.get("consistent_pool") or {}
    class_names = pool.get("class_names") if isinstance(pool, dict) else None
    if not isinstance(class_names, list) or not class_names:
        return labels
    wanted = {str(value).strip() for value in class_names if str(value).strip()}
    scoped = [item for item in labels if item["value"] in wanted or item["key"] in wanted]
    return scoped or labels


def _selected_target_labels(
    task_config: dict[str, Any], selected_values: list[str] | None
) -> list[dict[str, Any]]:
    available = _target_labels(task_config)
    if selected_values is None:
        return available

    requested = list(
        dict.fromkeys(str(value).strip() for value in selected_values if str(value).strip())
    )
    if not requested:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="请至少选择一个任务目标标签",
        )

    by_value = {item["value"]: item for item in available}
    selected = [by_value[value] for value in requested if value in by_value]
    invalid = [value for value in requested if value not in by_value]
    if invalid:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"目标标签不属于当前任务：{', '.join(invalid)}",
        )
    return selected


def _business_scope(task_config: dict[str, Any]) -> str:
    pool = task_config.get("consistent_pool") or {}
    if not isinstance(pool, dict):
        return ""
    return str(pool.get("dataset_category_key") or pool.get("rect_preset_slug") or "").strip()


def _label_description(value: str) -> str:
    return {
        "nonvehicle_electricbike": "electric scooter or e-bike",
        "vehicle_motorbike": "motorcycle",
        "nonvehicle_tricycle": "tricycle",
        "nonvehicle_bicycle": "bicycle",
        "nonvehicle_strolle": "stroller",
        "nonvehicle_balanceBike": "balance scooter",
        "nonvehicle_skateboard": "skateboard",
        "nonvehicle_wheelchair": "wheelchair",
        "nonvehicle_other": "other non-motor vehicle",
        "garbage_street_dump": "dumped garbage pile",
        "garbage_street_pack": "bagged garbage",
        "garbage_street_scattered": "scattered litter",
        "object_box": "cardboard box",
        "garbage_street_can": "bottle or can",
        "garbage_bulky": "bulky waste",
        "trashbin_overflow": "overflowing trash bin",
        "trashbin": "trash bin",
        "road_spillage": "road spillage",
    }.get(value, _humanize_label_value(value))


def _humanize_label_value(value: str) -> str:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(value or ""))
    return re.sub(r"[_-]+", " ", text).strip().lower()


def _normalize_label_alias(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[_-]+", " ", text)
    return re.sub(r"\s+", " ", text)


def _detection_query(
    task_config: dict[str, Any],
    provider: str = "vlx",
    target_labels: list[str] | None = None,
) -> tuple[str, str]:
    """Build a model query and language from the task business scope."""
    labels = _selected_target_labels(task_config, target_labels)
    scope = _business_scope(task_config)
    if scope == "illegalParking" and len(labels) == 1:
        description = _label_description(labels[0]["value"])
        return (
            f"Detect only {description} that are illegally parked, blocking pedestrian or road traffic, "
            "or parked outside a designated parking area. Exclude moving vehicles, normally parked vehicles, "
            "and every other object. Return bounding boxes for matching violations only.",
            "en",
        )
    if provider == "rex":
        # Rex-Omni reliably handles one natural-language target per request.
        # Keep the platform label key so its response can be mapped back to
        # the platform value without relying on custom internal identifiers.
        if len(labels) == 1:
            return labels[0]["key"], "zh"
        return "; ".join(item["key"] for item in labels), "zh"
    return _labels_text(labels), "zh"


def _raw_model_boxes(model_data: dict[str, Any]) -> list[Any]:
    raw = model_data.get("boxes") or model_data.get("result_bbox_list") or []
    if isinstance(raw, dict):
        raw = raw.get("boxes") or raw.get("items") or []
    return raw if isinstance(raw, list) else []


def _parse_boxes(
    model_data: dict[str, Any],
    labels: list[dict[str, Any]],
    width: int,
    height: int,
    provider: str = "vlx",
    query: str = "",
) -> list[dict[str, Any]]:
    by_alias: dict[str, dict[str, Any]] = {}
    for item in labels:
        aliases = [item["value"], item["key"]]
        if provider == "rex":
            aliases.append(_label_description(item["value"]))
        for alias in aliases:
            normalized = _normalize_label_alias(alias)
            if normalized:
                by_alias[normalized] = item
    raw = _raw_model_boxes(model_data)
    result = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or item.get("name") or item.get("category") or "").strip()
        configured = by_alias.get(_normalize_label_alias(label))
        # Some adapters return the complete query as a scoped single-class
        # label. Only that exact query may use the single-label fallback;
        # arbitrary model categories must never become a platform annotation.
        if (
            not configured
            and len(labels) == 1
            and _normalize_label_alias(label) == _normalize_label_alias(query)
        ):
            configured = labels[0]
        if not configured:
            continue
        try:
            x1 = float(item.get("xmin", item.get("x1")))
            y1 = float(item.get("ymin", item.get("y1")))
            x2 = float(item.get("xmax", item.get("x2")))
            y2 = float(item.get("ymax", item.get("y2")))
        except (TypeError, ValueError):
            continue
        x1, x2 = sorted((max(0.0, min(width, x1)), max(0.0, min(width, x2))))
        y1, y2 = sorted((max(0.0, min(height, y1)), max(0.0, min(height, y2))))
        if x2 <= x1 or y2 <= y1:
            continue
        # LabelU's canvas protocol stores rectangle geometry in source-image
        # pixels.  The model already returns pixel bounds, so preserve those
        # coordinates instead of converting them to percentages.
        rect = {
            "id": f"vlx-{uuid.uuid4().hex}", "order": index, "label": configured["value"], "visible": True,
            "x": x1, "y": y1,
            "width": x2 - x1, "height": y2 - y1,
        }
        result.append(rect)
    return result


def _build_payload(
    sample_name: str,
    task_config: dict[str, Any],
    model_data: dict[str, Any],
    width: int,
    height: int,
    latency_ms: int,
    provider: str = "vlx",
    target_labels: list[str] | None = None,
) -> dict[str, Any]:
    _, _, _, model_name, _, _, source_type, provider_name = _provider_settings(provider)
    labels = _selected_target_labels(task_config, target_labels)
    query, _ = _detection_query(task_config, provider, target_labels)
    raw_boxes = _raw_model_boxes(model_data)
    rects = _parse_boxes(model_data, labels, width, height, provider, query)
    _, configs, _ = auto_label_mod._extract_tool_configs(task_config)
    return {
        "sample_name": sample_name,
        "annotations": {"rectTool": {"toolName": "rectTool", "result": rects}} if rects else {},
        "config": {"rectTool": (configs.get("rectTool") or {}).get("attributes", [])},
        "meta": {
            "source_type": source_type,
            "provider": provider_name,
            "model": model_name,
            "latency_ms": latency_ms,
            "width": width,
            "height": height,
            "overwrite_existing_annotations": True,
            "apply_to_canvas_overwrite": True,
            "include_geometry_tools": True,
            "preserve_global_annotations": True,
            "target_labels": [item["value"] for item in labels],
            "model_categories": [_label_description(item["value"]) for item in labels] if provider == "rex" else [item["value"] for item in labels],
            "raw_box_count": len(raw_boxes),
            "accepted_box_count": len(rects),
            "rejected_box_count": max(0, len(raw_boxes) - len(rects)),
            "raw_answer": str(model_data.get("raw_answer") or "")[:2000],
            "request_results": model_data.get("request_results") or [],
            "business_scope": _business_scope(task_config),
        },
    }


def _read_image(attachment, provider: str = "vlx") -> tuple[bytes, str]:
    backend = get_storage_backend()
    path = backend.get_local_path(attachment.path)
    if path and path.is_file():
        return path.read_bytes(), attachment.filename or "image.jpg"
    url = get_model_read_url(attachment.path)
    _, _, timeout, _, verify_tls, _, _, _ = _provider_settings(provider)
    response = httpx.get(url, timeout=timeout, verify=verify_tls)
    response.raise_for_status()
    return response.content, attachment.filename or "image.jpg"


def _delete_previous(db: Session, task_id: int, sample_name: str) -> None:
    rows, _ = crud_pre_annotation.list_by(db=db, task_id=task_id, sample_name=sample_name, page=0, size=500)
    ids = [row.id for row in rows if _is_vlx(row.data)]
    if ids:
        crud_pre_annotation.delete(db=db, pre_annotation_ids=ids)


async def create_batch_job(db: Session, task_id: int, cmd: BatchLocateAnythingPreannotateCommand, current_user: User, provider: str = "vlx") -> LocateAnythingPreannotateJobResponse:
    enabled, endpoint, _, _, _, _, _, provider_name = _provider_settings(provider)
    if not enabled:
        label = "Rex-Omni" if provider == "rex" else "VLX"
        raise LabelUException(code=ErrorCode.CODE_56000_AUTO_LABEL_DISABLED, status_code=status.HTTP_400_BAD_REQUEST, message=f"{label} 图片预标注已停用")
    if not endpoint:
        setting_name = "REX_OMNI_ENDPOINT" if provider == "rex" else "VLX_SEEK_ENDPOINT"
        raise LabelUException(code=ErrorCode.CODE_56004_AUTO_LABEL_NOT_CONFIGURED, status_code=500, message=f"未配置 {setting_name}")
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=404)
    assert_user_has_task_access(task, current_user)
    if task.media_type != MediaType.IMAGE.value:
        label = "Rex-Omni" if provider == "rex" else "VLX"
        raise LabelUException(code=ErrorCode.CODE_56001_AUTO_LABEL_UNSUPPORTED_MEDIA, status_code=400, message=f"{label} 图片预标注仅支持图片任务")
    if task.status == TaskStatus.DRAFT.value:
        raise LabelUException(code=ErrorCode.CODE_30002_VALIDATION_ERROR, status_code=400, message="任务仍为草稿")
    task_config = auto_label_mod.resolve_ai_task_config(db, task)
    if not _rect_labels(task_config):
        raise LabelUException(code=ErrorCode.CODE_56002_AUTO_LABEL_NO_LABELS_CONFIGURED, status_code=400, message="任务未配置 rectTool 标签")
    target_labels = [
        item["value"] for item in _selected_target_labels(task_config, cmd.target_labels)
    ]
    explicit = _explicit_ids(cmd)
    if explicit:
        rows = crud_sample.get_by_ids(db=db, sample_ids=explicit, task_id=task_id)
        by_id = {row.id: row for row in rows}
        if len(by_id) != len(explicit):
            raise LabelUException(code=ErrorCode.CODE_56006_AUTO_LABEL_NO_SAMPLES, status_code=400, message="存在无效样本")
        targets = [by_id[sid] for sid in explicit if by_id[sid].state != SampleState.SKIPPED.value]
    else:
        targets = crud_sample.list_batch_auto_label_targets(
            db=db, task_id=task_id, include_done_samples=True
        )
        if not cmd.include_done_samples:
            # A global-tag pass can change a new sample to DONE before VLX has
            # ever produced a rectangle. Use provider history as the idempotent
            # boundary instead of relying on the mutable sample state alone.
            source_type = _provider_settings(provider)[6]
            generated_names = crud_pre_annotation.list_sample_names_by_source_type(
                db=db, task_id=task_id, source_type=source_type
            )
            targets = [
                row for row in targets
                if row.file is not None and row.file.filename not in generated_names
            ]
    if not targets:
        raise LabelUException(code=ErrorCode.CODE_56006_AUTO_LABEL_NO_SAMPLES, status_code=400, message="当前没有可处理的图片")
    with begin_transaction(db):
        assert_no_active_task_background_job(db=db, task_id=task_id)
        job = crud_locateanything_preannotate_job.create(
            db=db,
            task_id=task_id,
            user_id=current_user.id,
            sample_count=len(targets),
            provider=provider,
            payload_json=json.dumps(
                {
                    "sample_ids": [row.id for row in targets],
                    "task_config": task_config,
                    "target_labels": target_labels,
                    "overwrite": bool(cmd.overwrite),
                    "created_by": int(current_user.id),
                    "provider": provider,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        )
        if not settings.PROVIDER_PREANNOTATE_DEDICATED_WORKER_ENABLED:
            crud_locateanything_preannotate_job.update_status(db, job, AutoLabelStatus.PROCESSING.value)
        job_id = job.id
    if not settings.PROVIDER_PREANNOTATE_DEDICATED_WORKER_ENABLED:
        asyncio.get_event_loop().run_in_executor(
            None,
            _run_batch_sync,
            job_id,
            task_id,
            [row.id for row in targets],
            task_config,
            target_labels,
            cmd.overwrite,
            current_user.id,
            provider,
        )
    return _job_response(job, provider)


def get_job(db: Session, task_id: int, job_id: int, current_user: User, provider: str = "vlx"):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=404)
    assert_user_has_task_access(task, current_user)
    job = crud_locateanything_preannotate_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=404)
    return _job_response(job, provider)


def get_active_job(db: Session, task_id: int, current_user: User, provider: str = "vlx"):
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=404)
    assert_user_has_task_access(task, current_user)
    job = crud_locateanything_preannotate_job.get_active_by_task_id(db=db, task_id=task_id, provider=provider)
    return _job_response(job, provider) if job else None


def _run_batch_sync(
    job_id: int,
    task_id: int,
    sample_ids: list[int],
    task_config: dict[str, Any],
    target_labels: list[str],
    overwrite: bool,
    created_by: int,
    provider: str = "vlx",
    lease_owner: str | None = None,
    lease_fencing_token: int | None = None,
    lease_seconds: int | None = None,
) -> None:
    from labelu.internal.common.db import session_scope
    try:
        for sample_id in sample_ids:
            if lease_owner is not None and lease_fencing_token is not None:
                with session_scope() as db:
                    with begin_transaction(db):
                        renewed = crud_locateanything_preannotate_job.renew_fenced(
                            db, job_id=job_id, lease_owner=lease_owner,
                            lease_fencing_token=lease_fencing_token,
                            lease_seconds=int(lease_seconds or settings.PROVIDER_PREANNOTATE_WORKER_LEASE_SECONDS),
                        )
                    current = crud_locateanything_preannotate_job.get(db=db, job_id=job_id)
                    if (
                        not renewed
                        or
                        current is None
                        or current.status != AutoLabelStatus.PROCESSING.value
                        or current.provider != provider
                        or current.lease_owner != lease_owner
                        or int(current.lease_fencing_token or 0) != int(lease_fencing_token)
                    ):
                        return
            try:
                _process_one_sample(
                    task_id,
                    sample_id,
                    task_config,
                    target_labels,
                    overwrite,
                    created_by,
                    provider,
                    lease_owner=lease_owner,
                    lease_fencing_token=lease_fencing_token,
                    job_id=job_id,
                )
                with session_scope() as db:
                    job = crud_locateanything_preannotate_job.get(db=db, job_id=job_id)
                    if job:
                        with begin_transaction(db):
                            if lease_owner is not None and lease_fencing_token is not None:
                                crud_locateanything_preannotate_job.increment_progress_fenced(
                                    db, job_id=job_id, lease_owner=lease_owner,
                                    lease_fencing_token=lease_fencing_token, success=True,
                                )
                            else:
                                crud_locateanything_preannotate_job.increment_progress(db, job, success=True)
            except Exception:
                logger.exception("{} sample_fail job_id={} sample_id={}", _LOG, job_id, sample_id)
                with session_scope() as db:
                    job = crud_locateanything_preannotate_job.get(db=db, job_id=job_id)
                    if job:
                        with begin_transaction(db):
                            if lease_owner is not None and lease_fencing_token is not None:
                                crud_locateanything_preannotate_job.increment_progress_fenced(
                                    db, job_id=job_id, lease_owner=lease_owner,
                                    lease_fencing_token=lease_fencing_token, success=False,
                                )
                            else:
                                crud_locateanything_preannotate_job.increment_progress(db, job, success=False)
        with session_scope() as db:
            job = crud_locateanything_preannotate_job.get(db=db, job_id=job_id)
            if job:
                with begin_transaction(db):
                    if lease_owner is not None and lease_fencing_token is not None:
                        crud_locateanything_preannotate_job.finish_fenced(
                            db, job_id=job_id, lease_owner=lease_owner,
                            lease_fencing_token=lease_fencing_token,
                            status=AutoLabelStatus.COMPLETED.value,
                        )
                    else:
                        crud_locateanything_preannotate_job.update_status(db, job, AutoLabelStatus.COMPLETED.value)
    except Exception as exc:
        logger.exception("{} job_fatal job_id={}", _LOG, job_id)
        with session_scope() as db:
            job = crud_locateanything_preannotate_job.get(db=db, job_id=job_id)
            if job:
                with begin_transaction(db):
                    if lease_owner is not None and lease_fencing_token is not None:
                        crud_locateanything_preannotate_job.finish_fenced(
                            db, job_id=job_id, lease_owner=lease_owner,
                            lease_fencing_token=lease_fencing_token,
                            status=AutoLabelStatus.FAILED.value, error_message=str(exc),
                        )
                    else:
                        crud_locateanything_preannotate_job.update_status(db, job, AutoLabelStatus.FAILED.value, error_message=str(exc))


def _process_one_sample(
    task_id: int,
    sample_id: int,
    task_config: dict[str, Any],
    target_labels: list[str],
    overwrite: bool,
    created_by: int,
    provider: str = "vlx",
    lease_owner: str | None = None,
    lease_fencing_token: int | None = None,
    job_id: int | None = None,
) -> None:
    from labelu.internal.common.db import session_scope
    with session_scope() as db:
        task = crud_task.get(db=db, task_id=task_id)
        sample = crud_sample.get(db=db, sample_id=sample_id)
        if not task or not sample or sample.task_id != task_id or not sample.file:
            raise ValueError("sample missing or has no attachment")
        attachment = sample.file
        sample_name = attachment.filename
        image_bytes, filename = _read_image(attachment, provider)
    with Image.open(io.BytesIO(image_bytes)) as image:
        width, height = image.size
    _, endpoint, timeout, model_name, verify_tls, max_new_tokens, _, _ = _provider_settings(provider)
    started = time.perf_counter()
    with httpx.Client(timeout=timeout, verify=verify_tls) as client:
        if provider == "rex":
            # Rex returns no boxes when several custom classes are requested
            # together. Run each selected platform class independently, then
            # persist the merged result once below so requests cannot overwrite
            # one another's annotations.
            model_boxes: list[Any] = []
            raw_answers: list[str] = []
            request_results: list[dict[str, Any]] = []
            for target in _selected_target_labels(task_config, target_labels):
                text, lang = _detection_query(
                    task_config, provider, [target["value"]]
                )
                response = client.post(
                    endpoint,
                    headers=_provider_auth_headers(provider),
                    files={"image": (filename, image_bytes, "application/octet-stream")},
                    data={
                        "task": "detection",
                        "model": model_name,
                        "text": text,
                        "lang": lang,
                        "max_new_tokens": str(max_new_tokens),
                    },
                )
                response.raise_for_status()
                result = response.json()
                boxes = _raw_model_boxes(result)
                model_boxes.extend(boxes)
                raw_answer = str(result.get("raw_answer") or "")[:2000]
                raw_answers.append(raw_answer)
                request_results.append(
                    {
                        "target_label": target["value"],
                        "query": text,
                        "lang": lang,
                        "raw_box_count": len(boxes),
                        "raw_answer": raw_answer,
                    }
                )
            model_data = {
                "boxes": model_boxes,
                "raw_answer": "\n".join(raw_answers),
                "request_results": request_results,
            }
        else:
            text, lang = _detection_query(task_config, provider, target_labels)
            response = client.post(
                endpoint,
                headers=_provider_auth_headers(provider),
                files={"image": (filename, image_bytes, "application/octet-stream")},
                data={
                    "task": "detection",
                    "model": model_name,
                    "text": text,
                    "lang": lang,
                    "max_new_tokens": str(max_new_tokens),
                },
            )
            response.raise_for_status()
            model_data = response.json()
    normalized = _build_payload(
        sample_name,
        task_config,
        model_data,
        width,
        height,
        int((time.perf_counter() - started) * 1000),
        provider,
        target_labels,
    )
    with session_scope() as db:
        if lease_owner is not None and lease_fencing_token is not None:
            current = crud_locateanything_preannotate_job.get(db=db, job_id=job_id) if job_id is not None else None
            if (
                current is None
                or current.status != AutoLabelStatus.PROCESSING.value
                or current.provider != provider
                or current.lease_owner != lease_owner
                or int(current.lease_fencing_token or 0) != int(lease_fencing_token)
            ):
                raise RuntimeError(f"{provider} 预标注作业租约已失效，拒绝写入结果")
        with begin_transaction(db):
            if overwrite:
                rows, _ = crud_pre_annotation.list_by(db=db, task_id=task_id, sample_name=sample_name, page=0, size=500)
                ids = [row.id for row in rows if _is_provider(row.data, provider)]
                if ids:
                    crud_pre_annotation.delete(db=db, pre_annotation_ids=ids)
            crud_pre_annotation.batch(db=db, pre_annotations=[TaskPreAnnotation(task_id=task_id, file_id=None, sample_name=sample_name, data=json.dumps(normalized, ensure_ascii=False), created_by=created_by, updated_by=created_by)])
