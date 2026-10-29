"""Task-scoped discovery for the pre-annotation entry points."""

from __future__ import annotations

import json
from typing import Any

from fastapi import status
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_task
from labelu.internal.application.response.preannotation_capabilities import (
    PreannotationCapabilitiesResponse,
    PreannotationCapabilityItem,
)
from labelu.internal.application.service import auto_label
from labelu.internal.application.service import tagtool_preannotate
from labelu.internal.application.service import yolo_preannotate
from labelu.internal.common import model_access_config
from labelu.internal.common.ai_labeling_runtime import is_qwen_visual_provider
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.task_access import assert_user_has_task_access
from labelu.internal.domain.models.sample import TaskSample
from labelu.internal.domain.models.task import MediaType
from labelu.internal.domain.models.user import User


def _item(
    key: str,
    label: str,
    *,
    kind: str = "local",
    visible: bool = False,
    available: bool = False,
    reason: str | None = None,
    mode: str = "",
    supports_batch: bool = False,
    supports_sample: bool = False,
    model_count: int = 0,
    config_id: str | None = None,
    endpoint: str | None = None,
    models: list[Any] | None = None,
) -> PreannotationCapabilityItem:
    return PreannotationCapabilityItem(
        key=key,
        label=label,
        kind=kind,
        visible=visible,
        available=available,
        reason=reason,
        mode=mode,
        supports_batch=supports_batch,
        supports_sample=supports_sample,
        model_count=max(0, int(model_count)),
        config_id=str(config_id) if config_id is not None else None,
        endpoint=endpoint,
        models=models or [],
    )


def _task_config(task, db: Session) -> tuple[dict[str, Any], bool, bool, bool]:
    """Return merged config, image flag, rect flag and global-tag flag."""
    try:
        config = auto_label.resolve_ai_task_config(db, task)
    except Exception:
        raw = getattr(task, "config", None)
        if isinstance(raw, dict):
            config = raw
        else:
            try:
                config = json.loads(raw or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                config = {}
    if not isinstance(config, dict):
        config = {}
    _, tools, dimensions = auto_label._extract_tool_configs(config)
    media_type = getattr(task, "media_type", None)
    media_value = getattr(media_type, "value", media_type)
    return (
        config,
        str(media_value or "").strip().upper() == MediaType.IMAGE.value,
        any(name == "rectTool" for name in tools),
        bool(dimensions),
    )


def _configured_remote(
    provider_key: str, *, capability: str | None = None
) -> tuple[dict[str, Any] | None, bool]:
    """Return a ready record and whether an admin record exists.

    Capability discovery intentionally does not use the legacy environment
    fallback.  This keeps old ``.env`` endpoints from creating a menu item
    before an administrator has explicitly configured the provider.
    """
    records = [
        row
        for row in model_access_config.list_records()
        if str(row.get("provider_key") or "").strip().lower() == provider_key
    ]
    configured = bool(records)
    try:
        ready = model_access_config.resolve_model_integration(
            provider_key, capability=capability
        )
    except TypeError:
        ready = model_access_config.resolve_model_integration(provider_key)
    if ready and provider_key in {"volcengine", "tagtool", "vision_llm"}:
        if is_qwen_visual_provider(
            str(ready.get("endpoint") or ""),
            str(ready.get("model") or ""),
            str(ready.get("name") or ""),
        ):
            ready = None
    return ready, configured


def _same_endpoint(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
    """Whether two admin records point at the same service endpoint.

    A visual model record and a TagTool record may both be entered for the
    same endpoint.  That is one global-tag service exposed under two labels,
    so exposing both menu entries would make an operator run the same model
    twice.  Keep this comparison deliberately limited to the endpoint: a
    separately deployed TagTool service remains visible even when its model
    name happens to match the vision service.
    """
    if not left or not right:
        return False
    return str(left.get("endpoint") or "").strip().rstrip("/").lower() == str(
        right.get("endpoint") or ""
    ).strip().rstrip("/").lower()


def _remote_item(
    key: str,
    *,
    provider_key: str | None = None,
    provider_keys: list[str] | None = None,
    label: str,
    task_supported: bool,
    supports_batch: bool,
    supports_sample: bool,
    capability: str | None = None,
) -> PreannotationCapabilityItem:
    ready: dict[str, Any] | None = None
    configured = False
    # The first configured provider is authoritative. Managed Ark blocks
    # fallback while unavailable; installations without it retain the
    # dedicated global-tag then generic-vision order.
    for candidate in provider_keys or [provider_key or key]:
        candidate_ready, candidate_configured = _configured_remote(candidate, capability=capability)
        configured = configured or candidate_configured
        if candidate_ready:
            ready = candidate_ready
            break
        if candidate_configured:
            break
    if not task_supported:
        return _item(
            key,
            label,
            kind="remote",
            reason="task_contract_unsupported",
        )
    if ready is None:
        return _item(
            key,
            label,
            kind="remote",
            reason=("provider_not_configured" if not configured else "provider_not_ready"),
            supports_batch=supports_batch,
            supports_sample=supports_sample,
        )
    return _item(
        key,
        label,
        kind="remote",
        visible=True,
        available=True,
        reason=None,
        mode="both" if supports_batch and supports_sample else ("batch" if supports_batch else "sample"),
        supports_batch=supports_batch,
        supports_sample=supports_sample,
        model_count=1,
        config_id=ready.get("id"),
        endpoint=str(ready.get("endpoint") or "") or None,
        models=[{"model": str(ready.get("model") or "")}],
    )


def _tagtool_local_models(db: Session, task_id: int, user: User) -> list[dict[str, Any]]:
    """Return local TagTool rows whose heads cover task tag dimensions."""
    task = crud_task.get(db=db, task_id=task_id)
    if task is None:
        return []
    config, _, _, has_tags = _task_config(task, db)
    if not has_tags:
        return []
    _, _, dimensions = auto_label._extract_tool_configs(config)
    required = {str(item.get("field_key") or "").strip() for item in dimensions}
    required.discard("")
    rows, _ = tagtool_preannotate.crud_tagtool_classification_model.list_for_user(
        db,
        user.id,
        page=0,
        size=500,
        list_all_users_models=True,
    )
    compatible: list[dict[str, Any]] = []
    for row in rows:
        try:
            label_map = tagtool_preannotate._parse_label_map(row.label_map_json)
        except Exception:
            continue
        if not required.issubset(set(label_map)):
            continue
        compatible.append(
            {
                "kind": "imported",
                "id": int(row.id),
                "title": (row.display_name or "").strip() or f"TagTool 分类模型 #{row.id}",
                "category": (row.category or "").strip() or "未分类",
                "label_heads": list(label_map),
            }
        )
    return compatible


def get_task_capabilities(
    db: Session,
    task_id: int,
    current_user: User,
    sample_id: int | None = None,
) -> PreannotationCapabilitiesResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if task is None:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)
    if sample_id is not None:
        sample = (
            db.query(TaskSample)
            .filter(
                TaskSample.id == int(sample_id),
                TaskSample.task_id == int(task_id),
                TaskSample.deleted_at.is_(None),
            )
            .first()
        )
        if sample is None:
            raise LabelUException(
                code=ErrorCode.CODE_55001_SAMPLE_NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
            )

    config, is_image, has_rect, has_tags = _task_config(task, db)
    task_contract = yolo_preannotate._task_contract_descriptor(db, task)
    has_rect_labels = bool(task_contract.get("labels"))
    models: list[dict[str, Any]] = []
    yolo_reason = None
    yolo_count = 0
    if is_image and has_rect and has_rect_labels:
        try:
            yolo_models = yolo_preannotate.list_models_for_task(
                db=db, task_id=task_id, current_user=current_user, page=0, size=500
            )
            models = [item.dict() for item in yolo_models.items]
            yolo_count = len(models)
            if yolo_count == 0:
                yolo_reason = "no_compatible_model"
        except Exception:
            yolo_reason = "model_catalog_unavailable"
    elif not is_image:
        yolo_reason = "unsupported_media_type"
    else:
        yolo_reason = "task_contract_unsupported"

    local_tag_models = _tagtool_local_models(db, task_id, current_user) if has_tags else []
    remote_tag_ready, remote_tag_configured = _configured_remote(
        "tagtool", capability="global_tag"
    )
    # Match visual inference precedence: a page-managed Ark record remains
    # authoritative even when disabled or awaiting its connectivity check.
    # An alias of the selected visual service is shown once as AI 全局标签.
    vision_ready, ark_configured = _configured_remote("volcengine", capability="vision_reasoning")
    if not ark_configured:
        vision_ready, _ = _configured_remote("vision_llm")
    if _same_endpoint(remote_tag_ready, vision_ready):
        remote_tag_ready = None
    tag_models = list(local_tag_models)
    if remote_tag_ready:
        tag_models.insert(
            0,
            {
                "kind": "remote",
                "id": 0,
                "provider_key": "tagtool",
                "title": remote_tag_ready.get("name") or "远程 TagTool",
            },
        )
    tag_visible = bool(tag_models)
    tag_reason = None if tag_visible else ("provider_not_configured" if not remote_tag_configured and not local_tag_models else "no_compatible_model")

    items = [
        _remote_item(
            "ai_global_tag",
            provider_keys=["volcengine", "tagtool", "vision_llm"],
            label="AI 全局标签",
            task_supported=is_image and has_tags,
            supports_batch=True,
            supports_sample=True,
            capability="vision_reasoning",
        ),
        _remote_item(
            "ai_bbox_detection",
            provider_key="volcengine",
            label="AI目标检测",
            task_supported=is_image and has_rect and has_rect_labels,
            supports_batch=True,
            supports_sample=True,
            capability="bbox_detection",
        ),
        _item(
            "yolo",
            "YOLO",
            kind="local",
            visible=is_image and has_rect and has_rect_labels and yolo_count > 0,
            available=is_image and has_rect and has_rect_labels and yolo_count > 0,
            reason=yolo_reason,
            mode="both" if is_image else "",
            supports_batch=is_image and has_rect and has_rect_labels,
            supports_sample=is_image and has_rect and has_rect_labels,
            model_count=yolo_count,
            models=models,
        ),
        _item(
            "tagtool",
            "TagTool",
            kind="local" if local_tag_models and not remote_tag_ready else "mixed",
            visible=tag_visible,
            available=tag_visible,
            reason=tag_reason,
            mode="both" if tag_visible else "",
            supports_batch=has_tags,
            supports_sample=has_tags,
            model_count=len(tag_models),
            config_id=remote_tag_ready.get("id") if remote_tag_ready else None,
            endpoint=(str(remote_tag_ready.get("endpoint") or "") or None) if remote_tag_ready else None,
            models=tag_models,
        ),
        _remote_item(
            "locateanything",
            label="LocateAnything",
            task_supported=is_image and has_rect,
            supports_batch=True,
            supports_sample=True,
        ),
        _remote_item(
            "vlx_seek",
            label="VLX-Seek",
            task_supported=is_image and has_rect,
            supports_batch=True,
            supports_sample=True,
        ),
        _remote_item(
            "rex_omni",
            label="Rex-Omni",
            task_supported=is_image and has_rect,
            supports_batch=True,
            supports_sample=True,
        ),
        _item(
            "json_upload",
            "JSON/JSONL 文件导入",
            kind="local",
            visible=is_image,
            available=is_image,
            reason=None if is_image else "unsupported_media_type",
            mode="both" if is_image else "",
            supports_batch=is_image,
            supports_sample=is_image,
            model_count=0,
        ),
    ]
    return PreannotationCapabilitiesResponse(items=items)


__all__ = ["get_task_capabilities"]
