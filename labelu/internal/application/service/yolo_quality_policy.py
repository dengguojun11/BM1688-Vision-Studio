from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import status

from labelu.internal.application.command.yolo_quality_policy import (
    ActivateYoloQualityPolicyCommand,
    CreateOrUpdateYoloQualityPolicyCommand,
)
from labelu.internal.application.service import annotation_contract as annotation_contract_service
from labelu.internal.adapter.persistence import crud_annotation_taxonomy
from labelu.internal.application.response.yolo_quality_policy import (
    YoloQualityPoliciesBundleResponse,
    YoloQualityPolicyResponse,
)
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.yolo_quality_policy_store import (
    load_quality_policy_store,
    quality_policy_file_path,
    save_quality_policy_store,
)


def _to_response(item: dict[str, Any], active_policy_names: dict[str, str] | None) -> YoloQualityPolicyResponse:
    policy_name = str(item.get("policy_name") or "").strip()
    rect_preset_slug = str(item.get("rect_preset_slug") or "").strip()
    active_map = active_policy_names or {}
    semantic_key = _policy_match_key(item)
    return YoloQualityPolicyResponse(
        policy_name=policy_name,
        scene_name=str(item.get("scene_name") or "").strip(),
        rect_preset_slug=rect_preset_slug,
        label_ids=[int(value) for value in (item.get("label_ids") or [])],
        model_family=str(item.get("model_family") or "yolov11"),
        annotation_kind=str(item.get("annotation_kind") or "bbox"),
        label_set_signature=(str(item.get("label_set_signature") or "").strip() or None),
        status=str(item.get("status") or "draft").strip(),
        source=str(item.get("source") or "manual").strip(),
        version=max(1, int(item.get("version") or 1)),
        is_active=bool(policy_name and semantic_key and active_map.get(semantic_key) == policy_name),
        source_summary=item.get("source_summary") or {},
        training_quality=item.get("training_quality") or {},
        generalization_quality=item.get("generalization_quality") or {},
        edge_usability=item.get("edge_usability") or {},
        release_readiness=item.get("release_readiness") or {},
        key_classes=item.get("key_classes") or [],
        reasons=item.get("reasons") or [],
        risks=item.get("risks") or [],
        updated_at=item.get("updated_at"),
    )


def _policy_match_key(item: dict[str, Any]) -> str:
    signature = str(item.get("label_set_signature") or "").strip()
    if signature:
        return "semantic:{family}:{kind}:{signature}".format(
            family=str(item.get("model_family") or "yolov11").strip(),
            kind=str(item.get("annotation_kind") or "bbox").strip(),
            signature=signature,
        )
    return str(item.get("rect_preset_slug") or "").strip()


def list_quality_policies_bundle() -> YoloQualityPoliciesBundleResponse:
    store = load_quality_policy_store()
    active_policy_name = store.get("active_policy_name")
    active_policy_names = store.get("active_policy_names") or {}
    policies = [_to_response(item, active_policy_names) for item in store.get("policies") or []]
    return YoloQualityPoliciesBundleResponse(
        active_policy_name=active_policy_name,
        active_policy_names=active_policy_names,
        policies=policies,
        config_backend_label="MySQL 治理表（system_config_*）",
        runtime_file=str(quality_policy_file_path().resolve()),
    )


def ensure_quality_policy_preset_exists(db, rect_preset_slug: str) -> None:
    slug = str(rect_preset_slug or "").strip()
    if not slug or slug == "__default__":
        return
    preset = crud_annotation_taxonomy.label_preset_get_by_slug(db, slug)
    if preset is None:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message=f"拉框预设不存在：{slug}，请先在“预设与维度”中创建或选择已有预设",
        )


def upsert_quality_policy(
    cmd: CreateOrUpdateYoloQualityPolicyCommand,
    db=None,
) -> YoloQualityPoliciesBundleResponse:
    # HTTP callers pass their request-scoped DB session so the preset binding
    # is enforced.  Keep the historical file-store-only call shape usable for
    # internal utilities and migrations that intentionally have no DB context.
    resolved_label_ids = list(cmd.label_ids or [])
    resolved_signature = cmd.label_set_signature
    if db is not None and resolved_label_ids:
        labels = annotation_contract_service.active_labels_by_ids(db, resolved_label_ids)
        resolved_label_ids = [int(row.id) for row in labels]
        from labelu.internal.common.label_contract import label_set_signature

        resolved_signature = label_set_signature(resolved_label_ids)
    elif db is not None and not resolved_signature:
        ensure_quality_policy_preset_exists(db, cmd.rect_preset_slug)
    if not resolved_signature and not str(cmd.rect_preset_slug or "").strip():
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="质量策略必须选择标签集合；rect_preset_slug 仅用于历史兼容",
        )
    store = load_quality_policy_store()
    policies = [dict(item) for item in store.get("policies") or []]
    policy_name = cmd.policy_name.strip()
    payload = cmd.model_dump()
    payload["label_ids"] = resolved_label_ids
    payload["label_set_signature"] = resolved_signature
    payload["policy_name"] = policy_name
    payload["updated_at"] = datetime.utcnow().isoformat()

    idx = next(
        (i for i, item in enumerate(policies) if str(item.get("policy_name") or "").strip() == policy_name),
        -1,
    )
    if idx >= 0:
        policies[idx] = payload
    else:
        policies.append(payload)

    active_policy_names = dict(store.get("active_policy_names") or {})
    slug = _policy_match_key(payload)
    if str(payload.get("status") or "").strip() == "active":
        if slug:
            active_policy_names[slug] = policy_name
            store["active_policy_name"] = policy_name
    elif slug and active_policy_names.get(slug) == policy_name:
        active_policy_names.pop(slug, None)
        if str(store.get("active_policy_name") or "").strip() == policy_name:
            store["active_policy_name"] = active_policy_names.get("__default__")
    store["active_policy_names"] = active_policy_names
    store["policies"] = policies
    save_quality_policy_store(store)
    return list_quality_policies_bundle()


def activate_quality_policy(cmd: ActivateYoloQualityPolicyCommand) -> YoloQualityPoliciesBundleResponse:
    store = load_quality_policy_store()
    policy_name = cmd.policy_name.strip()
    policies = [dict(item) for item in store.get("policies") or []]
    matched = False
    target_slug = None
    for item in policies:
        current_name = str(item.get("policy_name") or "").strip()
        if current_name == policy_name:
            target_slug = _policy_match_key(item)
            break

    for item in policies:
        current_name = str(item.get("policy_name") or "").strip()
        current_slug = _policy_match_key(item)
        if current_name == policy_name:
            matched = True
            item["status"] = "active"
            item["updated_at"] = datetime.utcnow().isoformat()
        elif target_slug and current_slug == target_slug and str(item.get("status") or "").strip() == "active":
            item["status"] = "draft"

    if not matched:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="\u8d28\u91cf\u6807\u51c6\u4e0d\u5b58\u5728",
        )

    active_policy_names = dict(store.get("active_policy_names") or {})
    if target_slug:
        active_policy_names[target_slug] = policy_name
        store["active_policy_name"] = policy_name
    store["active_policy_names"] = active_policy_names
    store["policies"] = policies
    save_quality_policy_store(store)
    return list_quality_policies_bundle()


def resolve_policy_for_preset(rect_preset_slug: str | None) -> dict[str, Any]:
    store = load_quality_policy_store()
    active_names = store.get("active_policy_names") or {}
    policies = [item for item in store.get("policies") or [] if isinstance(item, dict)]
    slug = str(rect_preset_slug or "").strip()

    active_name = str(active_names.get(slug) or "").strip()
    if active_name:
        for item in policies:
            if str(item.get("policy_name") or "").strip() != active_name:
                continue
            if str(item.get("rect_preset_slug") or "").strip() == slug:
                return item

    default_active_name = str(active_names.get("__default__") or "").strip()
    if default_active_name:
        for item in policies:
            if str(item.get("policy_name") or "").strip() != default_active_name:
                continue
            if str(item.get("rect_preset_slug") or "").strip() == "__default__":
                return item

    for item in policies:
        if (
            str(item.get("rect_preset_slug") or "").strip() == slug
            and str(item.get("status") or "").strip() == "active"
        ):
            return item

    for item in policies:
        if str(item.get("rect_preset_slug") or "").strip() == "__default__":
            return item

    return {}


def resolve_policy_for_label_set(
    *, model_family: str, annotation_kind: str, label_set_signature: str
) -> dict[str, Any]:
    store = load_quality_policy_store()
    active_names = store.get("active_policy_names") or {}
    key = _policy_match_key({
        "model_family": model_family,
        "annotation_kind": annotation_kind,
        "label_set_signature": label_set_signature,
    })
    active_name = str(active_names.get(key) or "").strip()
    policies = [item for item in store.get("policies") or [] if isinstance(item, dict)]
    if active_name:
        for item in policies:
            if str(item.get("policy_name") or "").strip() == active_name and _policy_match_key(item) == key:
                return item
    for item in policies:
        if _policy_match_key(item) == key and str(item.get("status") or "") == "active":
            return item
    return resolve_policy_for_preset("__default__")
