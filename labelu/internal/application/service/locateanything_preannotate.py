"""LocateAnything-3B 专用图片标框预标注链路。"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import httpx
from fastapi import status
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_locateanything_preannotate_job, crud_pre_annotation, crud_sample, crud_task
from labelu.internal.application.command.locateanything_preannotate import BatchLocateAnythingPreannotateCommand
from labelu.internal.application.response.locateanything_preannotate import LocateAnythingPreannotateJobResponse
from labelu.internal.application.service import auto_label as auto_label_mod
from labelu.internal.application.service.task_background_jobs import (
    GROUP_GEOMETRY,
    assert_no_active_task_background_job,
)
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.model_access_config import (
    has_configured_model_integration,
    request_headers,
    resolve_model_integration,
)
from labelu.internal.common.storage import get_model_read_url
from labelu.internal.common.task_access import assert_user_has_task_access
from labelu.internal.domain.models.auto_label_job import AutoLabelStatus
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import SampleState
from labelu.internal.domain.models.task import MediaType, TaskStatus
from labelu.internal.domain.models.user import User

LA_PRE_META = "locateanything_generated"
_LOG = "[locateanything-preannotate]"
_STREET_VENDOR_SCOPE = "streetVendorOccupation"
_STREET_VENDOR_OTHER_LABEL = "streetVendor_other"
_STREET_VENDOR_MODEL_QUERIES = {
    "streetVendor_vegetablet": "售卖蔬菜的路边摊位，不是水果摊或餐饮摊",
    "streetVendor_catering": "售卖熟食、餐饮或饮料的路边摊位，不是水果摊或蔬菜摊",
    "streetVendor_clother": "售卖衣服服装的路边摊位，不是店铺",
    "streetVendor_fruit": "售卖水果或榴莲的路边水果摊位，不是餐饮摊",
    "streetVendor_other": "不属于蔬菜摊、餐饮摊、衣服摊、水果摊的其他路边占道摊位",
}


def _model_runtime() -> tuple[bool, str, int, str, bool, int]:
    """Resolve LocateAnything endpoint from system managed model access config.

    The environment settings remain a compatibility fallback for existing
    installations which have not migrated their model endpoint into the
    administration page.  A configured record is used only after it has been
    enabled and its connectivity check has passed (the helper enforces that
    rule), so an untested endpoint cannot silently replace a known-good one.
    """
    integration = resolve_model_integration("locateanything")
    if integration:
        return (
            bool(integration.get("enabled", True)),
            str(integration.get("endpoint") or "").strip(),
            int(integration.get("timeout_seconds") or settings.LA_MODEL_TIMEOUT_SECONDS),
            str(integration.get("model") or settings.LA_MODEL_NAME).strip(),
            bool(integration.get("verify_tls", settings.LA_MODEL_VERIFY_TLS if hasattr(settings, "LA_MODEL_VERIFY_TLS") else False)),
            int(integration.get("max_new_tokens") or 1024),
        )
    # An administrator-created configuration is authoritative.  Do not hide
    # an untested/failed service by silently falling back to the legacy .env
    # endpoint; the task page must show that the configured service is not
    # ready until its connectivity check passes.
    if has_configured_model_integration("locateanything"):
        return False, "", int(settings.LA_MODEL_TIMEOUT_SECONDS), str(settings.LA_MODEL_NAME or ""), False, 1024
    return (
        bool(settings.LA_AUTO_LABEL_ENABLED),
        str(settings.LA_MODEL_ENDPOINT or "").strip(),
        int(settings.LA_MODEL_TIMEOUT_SECONDS),
        str(settings.LA_MODEL_NAME or "").strip(),
        False,
        1024,
    )


def _explicit_target_sample_ids(cmd: BatchLocateAnythingPreannotateCommand) -> list[int] | None:
    combined: list[int] = []
    if cmd.sample_ids:
        combined.extend(int(x) for x in cmd.sample_ids)
    if cmd.sample_id is not None:
        combined.append(int(cmd.sample_id))
    out: list[int] = []
    seen: set[int] = set()
    for sid in combined:
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out or None


def _consistent_pool(task_config: dict[str, Any]) -> dict[str, Any]:
    value = task_config.get("consistent_pool")
    return value if isinstance(value, dict) else {}


def _contract_class_names(task_config: dict[str, Any]) -> list[str]:
    names = _consistent_pool(task_config).get("class_names")
    if not isinstance(names, list):
        return []
    return [str(name) for name in names if name]


def _business_scope(task_config: dict[str, Any]) -> str:
    pool = _consistent_pool(task_config)
    return str(pool.get("dataset_category_key") or pool.get("rect_preset_slug") or "").strip()


def _model_query_text(task_config: dict[str, Any], label_name: str, display_name: Any) -> str:
    if _business_scope(task_config) == _STREET_VENDOR_SCOPE:
        return _STREET_VENDOR_MODEL_QUERIES.get(label_name, str(display_name or label_name))
    return str(display_name or label_name)


def _rect_labels(task_config: dict[str, Any]) -> list[dict[str, Any]]:
    contract_names = set(_contract_class_names(task_config))
    labels: list[dict[str, Any]] = []
    for tool in task_config.get("tools", []) or []:
        if not isinstance(tool, dict) or tool.get("tool") != "rectTool":
            continue
        cfg = tool.get("config") or {}
        for attr in cfg.get("attributes", []) or []:
            if not isinstance(attr, dict):
                continue
            name = attr.get("value") or attr.get("key")
            if not name:
                continue
            name = str(name)
            labels.append(
                {
                    "name": name,
                    "display_name": _model_query_text(task_config, name, attr.get("key")),
                    "color": attr.get("color"),
                    "tool": "rectTool",
                }
            )
    if not contract_names:
        return labels
    scoped = [label for label in labels if label["name"] in contract_names]
    return scoped or labels


def _build_la_model_payload(
    *,
    image_url: str,
    task_id: int,
    task_name: str | None,
    task_config: dict[str, Any],
    filter_by_labels: bool,
) -> dict[str, Any]:
    return {
        "request_id": str(uuid.uuid4()),
        "image_url": image_url,
        "model": _model_runtime()[3],
        "max_new_tokens": _model_runtime()[5],
        "task": {"id": task_id, "name": task_name},
        "labels": _rect_labels(task_config),
        "constraints": {
            "allowed_tools": ["rectTool"],
            "max_results_per_label": 100,
            "filter_by_labels": filter_by_labels,
        },
        "prompt": None,
    }


def _annotation_meta(data: Any) -> dict[str, Any]:
    parsed = json.loads(data) if isinstance(data, str) else data
    return parsed.get("meta", {}) if isinstance(parsed, dict) else {}


def is_locateanything_preannotation_data(raw: Any) -> bool:
    try:
        return _annotation_meta(raw).get("source_type") == LA_PRE_META
    except Exception:
        return False


def _normalize_rect_result(item: dict[str, Any], order: int) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    tool = item.get("tool") or item.get("toolName") or "rectTool"
    if tool != "rectTool":
        return None
    result = item.get("result") if isinstance(item.get("result"), dict) else item
    try:
        rect = {
            "id": item.get("id") or str(uuid.uuid4()),
            "order": item.get("order", order),
            "label": item.get("label"),
            "visible": item.get("visible", True),
            "x": result["x"],
            "y": result["y"],
            "width": result["width"],
            "height": result["height"],
        }
    except KeyError:
        return None
    attrs = dict(item.get("attributes") or {})
    if item.get("score") is not None:
        attrs["score"] = str(item["score"])
    if attrs:
        rect["attributes"] = attrs
    return rect


def _rect_intersection_over_smaller(a: dict[str, Any], b: dict[str, Any]) -> float:
    left = max(float(a["x"]), float(b["x"]))
    top = max(float(a["y"]), float(b["y"]))
    right = min(float(a["x"]) + float(a["width"]), float(b["x"]) + float(b["width"]))
    bottom = min(float(a["y"]) + float(a["height"]), float(b["y"]) + float(b["height"]))
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    smaller = min(
        float(a["width"]) * float(a["height"]),
        float(b["width"]) * float(b["height"]),
    )
    return intersection / smaller if smaller > 0 else 0.0


def _filter_business_rects(task_config: dict[str, Any], rects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contract_names = set(_contract_class_names(task_config))
    if contract_names:
        rects = [rect for rect in rects if rect.get("label") in contract_names]
    if _business_scope(task_config) != _STREET_VENDOR_SCOPE:
        return rects

    specific_rects = [rect for rect in rects if rect.get("label") != _STREET_VENDOR_OTHER_LABEL]
    return [
        rect
        for rect in rects
        if rect.get("label") != _STREET_VENDOR_OTHER_LABEL
        or not any(_rect_intersection_over_smaller(rect, specific) >= 0.8 for specific in specific_rects)
    ]


def _build_la_preannotation_payload(
    *,
    sample_name: str,
    task_config: dict[str, Any],
    model_response: dict[str, Any],
) -> dict[str, Any]:
    results = model_response.get("results") or []
    if isinstance(results, dict):
        results = results.get("rectTool") or []
    rects: list[dict[str, Any]] = []
    for index, item in enumerate(results if isinstance(results, list) else []):
        rect = _normalize_rect_result(item, index)
        if rect:
            rects.append(rect)
    rects = _filter_business_rects(task_config, rects)

    _, config_by_tool, _ = auto_label_mod._extract_tool_configs(task_config)
    pre_annotation_config = {
        tool_name: tool_cfg.get("attributes", [])
        for tool_name, tool_cfg in config_by_tool.items()
        if tool_name == "rectTool"
    }
    annotations: dict[str, Any] = {}
    if rects:
        annotations["rectTool"] = {"toolName": "rectTool", "result": rects}

    model_labels = _rect_labels(task_config)

    return {
        "sample_name": sample_name,
        "annotations": annotations,
        "config": pre_annotation_config,
        "meta": {
            "source_type": LA_PRE_META,
            "provider": "locateanything",
            "model": model_response.get("model") or _model_runtime()[3],
            "latency_ms": model_response.get("latency_ms"),
            "target_labels": [label["name"] for label in model_labels],
            "model_queries": [label["display_name"] for label in model_labels],
        },
    }


def _job_response(job) -> LocateAnythingPreannotateJobResponse:
    return LocateAnythingPreannotateJobResponse(
        id=job.id,
        task_id=job.task_id,
        status=job.status,
        sample_count=job.sample_count or 0,
        processed_count=job.processed_count or 0,
        success_count=job.success_count or 0,
        failed_count=job.failed_count or 0,
        model=_model_runtime()[3],
        error_message=job.error_message,
        created_at=job.created_at,
    )


def _get_image_url(attachment) -> str:
    if getattr(attachment, "data_source_id", None) and getattr(attachment, "data_source", None):
        from labelu.internal.application.service.datasource import get_presigned_url

        return get_presigned_url(attachment.data_source, attachment.path)
    return get_model_read_url(attachment.path)


def _delete_la_preannotations_for_sample(db: Session, task_id: int, sample_name: str) -> int:
    rows, _ = crud_pre_annotation.list_by(db=db, task_id=task_id, sample_name=sample_name, page=0, size=500)
    ids = [row.id for row in rows if is_locateanything_preannotation_data(row.data)]
    if ids:
        crud_pre_annotation.delete(db=db, pre_annotation_ids=ids)
    return len(ids)


def _sample_has_la_preannotation(db: Session, task_id: int, sample_name: str) -> bool:
    rows, _ = crud_pre_annotation.list_by(db=db, task_id=task_id, sample_name=sample_name, page=0, size=100)
    return any(is_locateanything_preannotation_data(row.data) for row in rows)


async def create_batch_job(
    db: Session,
    task_id: int,
    cmd: BatchLocateAnythingPreannotateCommand,
    current_user: User,
) -> LocateAnythingPreannotateJobResponse:
    enabled, endpoint, _, _, _, _ = _model_runtime()
    if not enabled:
        raise LabelUException(
            code=ErrorCode.CODE_56000_AUTO_LABEL_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="LA 标注已暂时停用",
        )
    if not endpoint:
        raise LabelUException(
            code=ErrorCode.CODE_56004_AUTO_LABEL_NOT_CONFIGURED,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="未配置 LA 模型地址，请设置 LA_MODEL_ENDPOINT",
        )

    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
    assert_user_has_task_access(task, current_user)
    if task.media_type != MediaType.IMAGE.value:
        raise LabelUException(
            code=ErrorCode.CODE_56001_AUTO_LABEL_UNSUPPORTED_MEDIA,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="LA 标注仅支持图片任务",
        )
    if task.status == TaskStatus.DRAFT.value:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="任务仍为草稿，请先完成配置并导入样本后再运行 LA 标注",
        )

    task_config = auto_label_mod.resolve_ai_task_config(db, task)
    if not _rect_labels(task_config):
        raise LabelUException(
            code=ErrorCode.CODE_56002_AUTO_LABEL_NO_LABELS_CONFIGURED,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="任务未配置 rectTool 拉框标签，无法运行 LA 标注",
        )

    explicit_ids = _explicit_target_sample_ids(cmd)
    if explicit_ids:
        rows = crud_sample.get_by_ids(db=db, sample_ids=explicit_ids, task_id=task_id)
        by_id = {row.id: row for row in rows}
        missing = [sid for sid in explicit_ids if sid not in by_id]
        if missing:
            raise LabelUException(
                code=ErrorCode.CODE_56006_AUTO_LABEL_NO_SAMPLES,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"样本不存在、已删除或不属于该任务: {missing[:15]}",
            )
        targets = [by_id[sid] for sid in explicit_ids if by_id[sid].state != SampleState.SKIPPED.value]
    else:
        targets = crud_sample.list_batch_auto_label_targets(
            db=db,
            task_id=task_id,
            include_done_samples=cmd.include_done_samples,
        )
    if not targets:
        raise LabelUException(
            code=ErrorCode.CODE_56006_AUTO_LABEL_NO_SAMPLES,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="当前没有符合 LA 标注条件的样本",
        )

    sample_ids = [sample.id for sample in targets]
    logger.info(
        "{} create_job task_id={} user_id={} samples={} overwrite={} endpoint={!r}",
        _LOG,
        task_id,
        current_user.id,
        len(sample_ids),
        cmd.overwrite,
        endpoint,
    )
    with begin_transaction(db):
        assert_no_active_task_background_job(db=db, task_id=task_id, group=GROUP_GEOMETRY)
        job = crud_locateanything_preannotate_job.create(
            db=db,
            task_id=task_id,
            user_id=current_user.id,
            sample_count=len(sample_ids),
            provider="locateanything",
            payload_json=json.dumps(
                {
                    "sample_ids": sample_ids,
                    "task_config": task_config,
                    "filter_by_labels": bool(cmd.filter_by_labels),
                    "overwrite": bool(cmd.overwrite),
                    "created_by": int(current_user.id),
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
            sample_ids,
            task_config,
            cmd.filter_by_labels,
            cmd.overwrite,
            current_user.id,
        )
    return get_job(db=db, task_id=task_id, job_id=job_id, current_user=current_user)


def get_job(
    db: Session,
    task_id: int,
    job_id: int,
    current_user: User,
) -> LocateAnythingPreannotateJobResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
    assert_user_has_task_access(task, current_user)
    job = crud_locateanything_preannotate_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
    return _job_response(job)


def get_active_job(
    db: Session,
    task_id: int,
    current_user: User,
) -> LocateAnythingPreannotateJobResponse | None:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(code=ErrorCode.CODE_50002_TASK_NOT_FOUND, status_code=status.HTTP_404_NOT_FOUND)
    assert_user_has_task_access(task, current_user)
    job = crud_locateanything_preannotate_job.get_active_by_task_id(
        db=db, task_id=task_id, provider="locateanything"
    )
    return _job_response(job) if job else None


def _run_batch_sync(
    job_id: int,
    task_id: int,
    sample_ids: list[int],
    task_config: dict[str, Any],
    filter_by_labels: bool,
    overwrite: bool,
    created_by: int,
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
                        or current.lease_owner != lease_owner
                        or int(current.lease_fencing_token or 0) != int(lease_fencing_token)
                    ):
                        return
            try:
                _process_one_sample(
                    job_id=job_id,
                    task_id=task_id,
                    sample_id=sample_id,
                    task_config=task_config,
                    filter_by_labels=filter_by_labels,
                    overwrite=overwrite,
                    created_by=created_by,
                    lease_owner=lease_owner,
                    lease_fencing_token=lease_fencing_token,
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
                        crud_locateanything_preannotate_job.update_status(
                            db, job, AutoLabelStatus.FAILED.value, error_message=str(exc)
                        )


def _process_one_sample(
    *,
    job_id: int | None = None,
    task_id: int,
    sample_id: int,
    task_config: dict[str, Any],
    filter_by_labels: bool,
    overwrite: bool,
    created_by: int,
    lease_owner: str | None = None,
    lease_fencing_token: int | None = None,
) -> None:
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        task = crud_task.get(db=db, task_id=task_id)
        sample = crud_sample.get(db=db, sample_id=sample_id)
        if not task or not sample or sample.task_id != task_id or not sample.file:
            raise ValueError("sample missing or has no attachment")
        sample_name = sample.file.filename
        if not overwrite and _sample_has_la_preannotation(db, task_id, sample_name):
            return
        payload = _build_la_model_payload(
            image_url=_get_image_url(sample.file),
            task_id=task_id,
            task_name=task.name,
            task_config=task_config,
            filter_by_labels=filter_by_labels,
        )

    start = time.perf_counter()
    _, endpoint, timeout, _, verify_tls, _ = _model_runtime()
    with httpx.Client(timeout=timeout, verify=verify_tls) as client:
        integration = resolve_model_integration("locateanything")
        response = client.post(
            endpoint,
            json=payload,
            headers=request_headers(integration) if integration else {},
        )
        if response.status_code >= 400:
            logger.error("{} model service returned {}: {}", _LOG, response.status_code, response.text)
        response.raise_for_status()
        model_data = response.json()
    model_data["latency_ms"] = int((time.perf_counter() - start) * 1000)
    normalized = _build_la_preannotation_payload(
        sample_name=sample_name,
        task_config=task_config,
        model_response=model_data,
    )

    with session_scope() as db:
        if lease_owner is not None and lease_fencing_token is not None:
            current = crud_locateanything_preannotate_job.get(db=db, job_id=job_id) if job_id is not None else None
            if (
                current is None
                or current.status != AutoLabelStatus.PROCESSING.value
                or current.lease_owner != lease_owner
                or int(current.lease_fencing_token or 0) != int(lease_fencing_token)
            ):
                raise RuntimeError("LocateAnything 预标注作业租约已失效，拒绝写入结果")
        with begin_transaction(db):
            if overwrite:
                _delete_la_preannotations_for_sample(db, task_id, sample_name)
            crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task_id,
                        file_id=None,
                        sample_name=sample_name,
                        data=json.dumps(normalized, ensure_ascii=False),
                        created_by=created_by,
                        updated_by=created_by,
                    )
                ],
            )
