import asyncio
import json
import math
import time
import uuid
from typing import Any

import httpx
from fastapi import status
from loguru import logger
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_annotation_taxonomy
from labelu.internal.adapter.persistence import crud_pre_annotation, crud_sample, crud_task
from labelu.internal.adapter.persistence import crud_auto_label_job
from labelu.internal.application.command.auto_label import AutoLabelCommand, BatchAutoLabelCommand
from labelu.internal.application.response.auto_label import AutoLabelResponse, AutoLabelJobResponse
from labelu.internal.common.business_log import biz_log, log_business_operation, set_request_context
from labelu.internal.common.config import settings
from labelu.internal.common.db import begin_transaction
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.model_access_config import (
    has_configured_model_integration,
    resolve_model_integration,
)
from labelu.internal.common.storage import get_model_read_url
from labelu.internal.common.task_access import assert_user_has_task_access
from labelu.internal.common.tag_tool_frontend_payload import expand_tag_tool_result_items
from labelu.internal.common.task_annotation_completeness import audit_one_sample
from labelu.internal.application.service import vision_llm_chain
from labelu.internal.application.service import annotation_prompt as annotation_prompt_service
from labelu.internal.application.service import annotation_contract
from labelu.internal.application.service.provider_job import bridge_legacy_preannotation
from labelu.internal.application.service.annotation_taxonomy import merge_global_dimensions_into_cfg
from labelu.internal.application.service.task_background_jobs import assert_no_active_task_background_job
from labelu.internal.application.service.task_training_ready import refresh_task_training_ready
from labelu.internal.domain.models.auto_label_job import AutoLabelStatus
from labelu.internal.domain.models.pre_annotation import TaskPreAnnotation
from labelu.internal.domain.models.sample import SampleState, TaskSample
from labelu.internal.domain.models.task import MediaType
from labelu.internal.domain.models.user import User


SUPPORTED_IMAGE_TOOLS = {"rectTool", "polygonTool", "pointTool", "lineTool"}
TAG_TOOL = "tagTool"
AI_SUPPORTED_TOOLS = frozenset(SUPPORTED_IMAGE_TOOLS) | {TAG_TOOL}
BATCH_MODE_TAGS_ONLY = "TAGS_ONLY"
BATCH_MODE_BOX_AND_TAGS = "BOX_AND_TAGS"
AI_GLOBAL_TAG_PROVIDER = "tagtool"
AI_GLOBAL_TAG_FALLBACK_PROVIDER = "vision_llm"
AI_BBOX_DETECTION_PROVIDER = "bbox_detection"
BATCH_SAMPLE_STATES = {
    SampleState.NEW.value,
    SampleState.INCOMPLETE.value,
    SampleState.DONE.value,
}


def _resolve_model_integration_compat(provider_key: str, *, capability: str | None = None):
    """Resolve a managed model while keeping older embedded test callers compatible."""
    try:
        return resolve_model_integration(provider_key, capability=capability)
    except TypeError:
        try:
            return resolve_model_integration(provider_key)
        except TypeError:
            # A few embedded integrations monkeypatch this resolver with a
            # zero-argument probe. Treat that as no configured record rather
            # than leaking a compatibility TypeError into the request path.
            return None


def _has_managed_volcengine_config_compat(capability: str | None = None) -> bool:
    """Read capability-aware config while tolerating older embedded probes."""
    try:
        return bool(vision_llm_chain.has_managed_volcengine_config(capability))
    except TypeError:
        try:
            return bool(vision_llm_chain.has_managed_volcengine_config())
        except TypeError:
            return False


def resolve_ai_task_config(db: Session, task) -> dict[str, Any]:
    """合并数据库全局维度（tagTool/tagList），供 AI 标注与界面 GET 富集一致。"""
    cfg = _parse_task_config(task.config)
    dims = crud_annotation_taxonomy.global_dimensions_all_active(db)
    merge_global_dimensions_into_cfg(cfg, dims)
    return cfg


def _vision_chain_mode() -> bool:
    return settings.AI_PROVIDER.strip().lower() == "vision_llm_chain"


def _configured_global_tag_vision_available() -> bool:
    """Return whether the admin-managed global-tag model should handle a request.

    Page-managed Ark connections use the vision chain regardless of the legacy
    environment mode. A disabled/failed page connection must also select that
    chain so its fail-closed validation blocks historical fallback endpoints.
    """
    return bool(
        _has_managed_volcengine_config_compat("vision_reasoning")
        or _has_managed_volcengine_config_compat("global_tag")
        or _resolve_model_integration_compat("volcengine", capability="vision_reasoning")
        or _resolve_model_integration_compat("volcengine", capability="global_tag")
        or _resolve_model_integration_compat(AI_GLOBAL_TAG_PROVIDER)
        or _resolve_model_integration_compat(AI_GLOBAL_TAG_FALLBACK_PROVIDER)
    )


def _configured_bbox_vision_available() -> bool:
    """Whether the admin-managed Volcengine bbox model is ready."""
    return bool(
        _has_managed_volcengine_config_compat("bbox_detection")
        or _resolve_model_integration_compat("volcengine", capability="bbox_detection")
    )


def _configured_runtime(provider_key: str = AI_GLOBAL_TAG_PROVIDER) -> dict[str, Any] | None:
    """Resolve the configured runtime for a global-tag request.

    The dedicated global-tag connection is authoritative when present.  The
    older ``vision_llm`` key remains a compatibility fallback only when no
    ``tagtool`` connection has been configured at all; an enabled but
    unchecked connection must fail closed instead of silently switching
    services.
    """
    if vision_llm_chain.has_managed_volcengine_config():
        return vision_llm_chain._configured_model_for(provider_key)
    keys = [provider_key]
    if provider_key == AI_GLOBAL_TAG_PROVIDER:
        keys.append(AI_GLOBAL_TAG_FALLBACK_PROVIDER)
    for key in keys:
        integration = resolve_model_integration(key)
        if integration:
            return integration
        if has_configured_model_integration(key):
            return {
                "endpoint": "",
                "model": "",
                "timeout_seconds": 300,
                "api_key": "",
                "verify_tls": False,
                "blocked": True,
                "provider_key": key,
            }
    return None


def _model_runtime(provider_key: str = AI_GLOBAL_TAG_PROVIDER) -> dict[str, Any]:
    """Resolve the configured global-tag model endpoint.

    A configured but untested/failed admin record deliberately blocks fallback
    to the legacy environment value.  Existing deployments with no admin
    records continue to use AI_MODEL_ENDPOINT until migrated.
    """
    integration = _configured_runtime(provider_key)
    if integration:
        return integration
    return {
        "endpoint": str(settings.AI_MODEL_ENDPOINT or "").strip(),
        "model": str(settings.AI_MODEL_NAME or "").strip(),
        "timeout_seconds": int(settings.AI_MODEL_TIMEOUT_SECONDS),
        "api_key": "",
        "auth_type": "none",
        "verify_tls": False,
        "blocked": False,
    }


def _model_auth_headers(runtime: dict[str, Any]) -> dict[str, str]:
    secret = str(runtime.get("api_key") or "").strip()
    if not secret:
        return {}
    auth_type = str(runtime.get("auth_type") or "none").strip().lower()
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if auth_type in {"header", "api_key"}:
        return {str(runtime.get("auth_header") or ("X-API-Key" if auth_type == "api_key" else "Authorization")): secret}
    return {}


def _validated_local_http_runtime() -> dict[str, Any]:
    """Recheck legacy HTTP targets before opening either model client."""
    runtime = _model_runtime()
    endpoint = str(runtime.get("endpoint") or "").strip()
    if runtime.get("blocked") or not endpoint:
        raise ValueError("视觉模型已禁用、未通过连通性检查或未配置服务地址")
    model = str(runtime.get("model") or "")
    for label in (runtime.get("provider_key"), runtime.get("name")):
        vision_llm_chain._safe_visual_base_url(endpoint, model, str(label or ""))
    # Validate without rewriting custom HTTP paths or their request protocol.
    return {**runtime, "endpoint": endpoint}


def _apply_model_runtime_options(payload: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """Attach administrator-selected model options to a remote request.

    The legacy endpoint did not require these fields, so they remain omitted
    when no value is configured.  A configured service receives the selected
    model identifier and output limit instead of silently using code defaults.
    """
    enriched = dict(payload)
    model = str(runtime.get("model") or "").strip()
    if model:
        enriched["model"] = model
    max_new_tokens = runtime.get("max_new_tokens")
    if max_new_tokens is not None:
        try:
            enriched["max_new_tokens"] = max(1, min(int(max_new_tokens), 32768))
        except (TypeError, ValueError):
            pass
    return enriched


def _contract_prompt_for_http_payload(payload: dict[str, Any]) -> str | None:
    """Expose the immutable contract prompt to legacy HTTP model adapters.

    The managed vision chain reads ``contract_prompt`` directly and renders
    it with the transient image dimensions.  Older local model servers only
    consume the top-level ``prompt`` field and ignore unknown JSON fields, so
    leaving the binding nested would silently bypass the published prompt on
    that path.  Keep the ordinary request prompt as supplementary context.
    """

    binding = payload.get("contract_prompt")
    if not isinstance(binding, dict):
        return None
    effective = str(binding.get("effective_prompt") or "").strip()
    if not effective:
        return None
    request_prompt = str(payload.get("prompt") or "").strip()
    if request_prompt:
        effective += (
            "\n\n单次请求附加说明（仅作补充，不得改变标签契约、坐标规则或输出 JSON 结构）："
            + request_prompt
        )
    return effective


def _ensure_ai_backend_configured(provider_key: str = AI_GLOBAL_TAG_PROVIDER) -> None:
    use_bbox = provider_key == AI_BBOX_DETECTION_PROVIDER
    use_vision_chain = (
        _configured_bbox_vision_available()
        if use_bbox
        else _vision_chain_mode() or _configured_global_tag_vision_available()
    )
    if use_vision_chain:
        if not vision_llm_chain.vision_llm_chain_configured(
            provider_key=provider_key,
            capability="bbox_detection" if use_bbox else "vision_reasoning",
        ):
            raise LabelUException(
                code=ErrorCode.CODE_56007_AUTO_LABEL_VISION_CHAIN_NOT_CONFIGURED,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return
    runtime = _model_runtime(provider_key)
    if runtime.get("blocked") or not runtime.get("endpoint"):
        logger.error("AI_MODEL_ENDPOINT is not configured")
        raise LabelUException(
            code=ErrorCode.CODE_56004_AUTO_LABEL_NOT_CONFIGURED,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _parse_task_config(raw_config: str | dict | None) -> dict[str, Any]:
    if not raw_config:
        return {}
    if isinstance(raw_config, dict):
        return raw_config
    try:
        return json.loads(raw_config)
    except json.JSONDecodeError:
        logger.warning("failed to parse task config: {}", raw_config)
        return {}


def _extract_tool_configs(
    task_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    labels: list[dict[str, Any]] = []
    config_by_tool: dict[str, Any] = {}
    tag_dimensions: list[dict[str, Any]] = []
    for tool in task_config.get("tools", []) or []:
        tool_name = tool.get("tool")
        tool_cfg = tool.get("config", {}) or {}
        attributes = tool_cfg.get("attributes", []) or []
        if tool_name in SUPPORTED_IMAGE_TOOLS:
            if not attributes:
                continue
            config_by_tool[tool_name] = tool_cfg
            for attr in attributes:
                labels.append(
                    {
                        "name": attr.get("value") or attr.get("key"),
                        "display_name": attr.get("key"),
                        "color": attr.get("color"),
                        "tool": tool_name,
                    }
                )
        elif tool_name == TAG_TOOL:
            if not attributes:
                continue
            config_by_tool[tool_name] = tool_cfg
            for attr in attributes:
                if not isinstance(attr, dict):
                    continue
                fk = attr.get("value")
                if not fk:
                    continue
                opts = attr.get("options") or []
                opts_ok = (
                    isinstance(opts, list)
                    and len(opts) > 0
                    and all(isinstance(o, dict) for o in opts)
                )
                # 兼容旧模板：仅有占位属性、无 type；只要有 options 仍视为枚举维度
                if attr.get("type") != "enum" and not opts_ok:
                    continue
                tag_dimensions.append(
                    {
                        "field_key": fk,
                        "name_cn": attr.get("key") or fk,
                        "options": opts,
                    }
                )
    return labels, config_by_tool, tag_dimensions


def _get_image_url(attachment) -> str:
    """Get a readable URL for the image, handling both local/global-S3 and external data source attachments."""
    if getattr(attachment, "data_source_id", None) and getattr(attachment, "data_source", None):
        from labelu.internal.application.service.datasource import get_presigned_url
        return get_presigned_url(attachment.data_source, attachment.path)
    return get_model_read_url(attachment.path)


def _validate_auto_label_task_config(
    task_config: dict[str, Any],
    *,
    include_geometry_tools: bool,
) -> None:
    """Raise if task config cannot satisfy the requested auto-label mode."""
    labels, config_by_tool, tag_dimensions = _extract_tool_configs(task_config)
    if include_geometry_tools:
        # BOX_AND_TAGS is a detection request; a task must expose at least
        # one configured geometry class.  A tag-only task has no meaningful
        # target-detection contract even if it also has global dimensions.
        if not labels:
            raise LabelUException(
                code=ErrorCode.CODE_56002_AUTO_LABEL_NO_LABELS_CONFIGURED,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    else:
        if TAG_TOOL not in config_by_tool or not tag_dimensions:
            raise LabelUException(
                code=ErrorCode.CODE_56002_AUTO_LABEL_NO_LABELS_CONFIGURED,
                status_code=status.HTTP_400_BAD_REQUEST,
            )


def _resolve_contract_prompt(
    db: Session,
    task,
    *,
    include_geometry_tools: bool,
) -> dict[str, Any] | None:
    """Resolve the exact published prompt for this task, if one exists.

    Prompt bindings are normally published for ``volcengine`` while the
    runtime provider names used by the legacy auto-label path are
    ``bbox_detection``/``tagtool``.  Probe the canonical provider first and
    then compatibility aliases.  A missing binding is intentionally returned
    as ``None`` so callers can record an explicit legacy fallback state.
    """

    operation = "bbox_detection" if include_geometry_tools else "global_tag"
    # Determine this before querying prompt bindings.  A task can be governed
    # by a dataset contract even when its legacy task row has not yet received
    # a materialized TaskAnnotationContract; that path must still fail closed.
    has_formal_contract = _task_has_formal_contract(db, task)
    provider_candidates = ["volcengine"]
    configured_provider = str(getattr(settings, "AI_PROVIDER", "") or "").strip()
    for candidate in (
        configured_provider,
        AI_BBOX_DETECTION_PROVIDER if include_geometry_tools else AI_GLOBAL_TAG_PROVIDER,
    ):
        if candidate and candidate not in provider_candidates:
            provider_candidates.append(candidate)
    model_name = _prompt_runtime_model_name(include_geometry_tools=include_geometry_tools)
    resolved_bindings: list[tuple[int, dict[str, Any]]] = []
    for provider_key in provider_candidates:
        try:
            binding = annotation_prompt_service.resolve_published_prompt_for_task(
                db,
                task,
                operation=operation,
                provider_key=provider_key,
                model_name=model_name,
            )
        except Exception as exc:
            # A legacy task may still run while its optional prompt table is
            # being introduced.  A governed task must never use that as a
            # reason to fall back to task.config, however.
            if has_formal_contract and include_geometry_tools:
                raise LabelUException(
                    code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                    status_code=status.HTTP_409_CONFLICT,
                    message=(
                        "当前任务标签契约的 AI 提示词不可用，"
                        "请检查提示词绑定和数据库迁移状态"
                    ),
                ) from exc
            logger.warning(
                "contract prompt lookup failed task_id={} provider={} reason={}",
                getattr(task, "id", None),
                provider_key,
                str(exc)[:240],
            )
            return None
        if binding:
            # A model-pinned binding must win over a provider-wide binding,
            # even when the latter is found under the canonical provider key
            # before a compatibility alias is inspected.
            exact_model = bool(
                model_name
                and str(binding.get("model_name") or "").strip() == model_name
            )
            resolved_bindings.append((1 if exact_model else 0, binding))
    if resolved_bindings:
        resolved_bindings.sort(key=lambda item: item[0], reverse=True)
        return resolved_bindings[0][1]
    # Once a task has a formal contract, silently falling back to the legacy
    # task.config prompt would violate the selected label vocabulary.  Legacy
    # tasks without any contract remain supported and are explicitly marked by
    # ``legacy_fallback`` metadata.
    # Dataset/task contracts currently govern the object-label vocabulary. The
    # global-tag dimensions still come from the task's tagTool configuration
    # and are not part of DatasetLabelContract's immutable label snapshot.
    # Keep that established path available until a dimension snapshot and a
    # matching global_tag prompt compiler are introduced. It is explicit in
    # metadata as ``legacy_fallback`` and does not weaken bbox detection's
    # fail-closed contract gate above.
    if has_formal_contract and include_geometry_tools:
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            message=(
                "当前任务标签契约没有匹配的已发布 AI 提示词，"
                "请先为该契约修订生成并发布提示词"
            ),
        )
    return None


def _prompt_runtime_model_name(*, include_geometry_tools: bool) -> str | None:
    """Return the model identity used by the current inference capability.

    Prompt bindings may be generic (``model_name`` is null) or pinned to one
    model.  The model-access page is authoritative when it has a tested
    Volcengine connection; the legacy environment setting remains a fallback
    for deployments that have not migrated that configuration yet.
    """

    capability = "bbox_detection" if include_geometry_tools else "vision_reasoning"
    try:
        configured = _resolve_model_integration_compat(
            "volcengine", capability=capability
        )
    except Exception as exc:
        logger.debug(
            "unable to resolve managed model for prompt binding capability={} reason={}",
            capability,
            str(exc)[:160],
        )
        configured = None
    configured_name = str((configured or {}).get("model") or "").strip()
    if configured_name:
        return configured_name
    configured_name = str(getattr(settings, "AI_MODEL_NAME", "") or "").strip()
    return configured_name or None


def _task_has_formal_contract(db: Session, task) -> bool:
    """Return whether either task- or dataset-level governance is active.

    Dataset-bound tasks created during the migration window can have a
    ``dataset_label_contract_id`` without a materialized task contract.  They
    are still not legacy tasks: allowing ``task.config`` to choose a prompt
    could violate the dataset label vocabulary.
    """

    if getattr(task, "dataset_label_contract_id", None):
        return True
    # A few embedded/legacy callers execute the model adapter with a task
    # object but no ORM session (for example, a worker boundary probe).  The
    # contract lookup is an optional enrichment for those paths; it must not
    # turn a legacy HTTP request into an AttributeError before the adapter has
    # a chance to validate its own runtime.  Real application requests always
    # provide a SQLAlchemy Session and therefore retain the fail-closed gate.
    task_id = getattr(task, "id", None)
    if task_id:
        try:
            if annotation_contract.current_contract(db, int(task_id)) is not None:
                return True
        except AttributeError:
            # Embedded adapter probes may pass a sentinel instead of a
            # Session.  Treat that as an ungoverned legacy task unless the
            # caller's contract resolver explicitly reports one above.
            if db is None or not hasattr(db, "query"):
                return False
            raise
    if db is None or not hasattr(db, "query"):
        return False
    dataset_id = getattr(task, "dataset_id", None)
    if not dataset_id:
        return False
    # Import locally to keep the auto-label module's import graph unchanged.
    from labelu.internal.domain.models.dataset import Dataset

    dataset = (
        db.query(Dataset)
        .filter(Dataset.id == int(dataset_id), Dataset.deleted_at.is_(None))
        .one_or_none()
    )
    return bool(dataset and getattr(dataset, "dataset_label_contract_id", None))


def _contract_prompt_meta(binding: dict[str, Any] | None) -> dict[str, Any]:
    """Return non-sensitive provenance fields stored with pre-annotations."""

    if not binding:
        return {"contract_prompt_status": "legacy_fallback"}
    return {
        "contract_prompt_status": "published",
        "prompt_binding_id": binding.get("id"),
        "contract_id": binding.get("contract_id"),
        "contract_revision": binding.get("contract_revision"),
        "label_set_signature": binding.get("label_set_signature"),
        "class_order_signature": binding.get("class_order_signature"),
        "prompt_sha256": binding.get("prompt_sha256"),
        "prompt_profile_key": binding.get("profile_key"),
        "prompt_profile_version": binding.get("profile_version"),
    }


def _cached_contract_prompt_matches(
    meta: dict[str, Any], binding: dict[str, Any] | None
) -> bool:
    """Prevent an old pre-annotation from bypassing a prompt revision."""

    if not isinstance(meta, dict):
        return False
    if binding is None:
        # Legacy results created before prompt provenance was introduced are
        # deliberately not treated as a match; they get one explicit refresh.
        return str(meta.get("contract_prompt_status") or "") == "legacy_fallback"
    try:
        return (
            str(meta.get("contract_prompt_status") or "") == "published"
            and int(meta.get("prompt_binding_id")) == int(binding.get("id"))
            and str(meta.get("prompt_sha256") or "")
            == str(binding.get("prompt_sha256") or "")
            and int(meta.get("contract_revision"))
            == int(binding.get("contract_revision"))
            and str(meta.get("label_set_signature") or "")
            == str(binding.get("label_set_signature") or "")
            and str(meta.get("class_order_signature") or "")
            == str(binding.get("class_order_signature") or "")
        )
    except (TypeError, ValueError):
        return False


def _build_inference_payload(
    sample,
    task,
    task_config: dict[str, Any],
    *,
    filter_by_labels: bool,
    include_geometry_tools: bool,
    prompt: str | None,
    contract_prompt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    labels, config_by_tool, tag_dimensions = _extract_tool_configs(task_config)
    contract_spec = (
        contract_prompt.get("prompt_spec")
        if isinstance(contract_prompt, dict)
        and isinstance(contract_prompt.get("prompt_spec"), dict)
        else None
    )
    contract_labels = (
        contract_spec.get("labels")
        if isinstance(contract_spec, dict)
        else None
    )
    contract_bound_geometry = bool(
        include_geometry_tools
        and isinstance(contract_labels, list)
        and contract_labels
    )
    # A formal contract supplies the detection vocabulary itself.  Do not
    # reject a governed task merely because its legacy UI config has not yet
    # been backfilled with the same labels.
    if not contract_bound_geometry:
        _validate_auto_label_task_config(
            task_config,
            include_geometry_tools=include_geometry_tools,
        )
    if TAG_TOOL in config_by_tool and not tag_dimensions:
        logger.warning(
            "AI auto-label task {}: tagTool enabled but no enum dimensions extracted; "
            "model may skip tag_values. Check tagTool.attributes or global dimensions.",
            task.id,
        )

    if include_geometry_tools:
        labels_for_model = (
            [
                {
                    "name": str(
                        item.get("label_value")
                        or item.get("name")
                        or item.get("value")
                        or ""
                    ).strip(),
                    "display_name": str(
                        item.get("label_key")
                        or item.get("display_name")
                        or item.get("key")
                        or item.get("label_value")
                        or item.get("name")
                        or ""
                    ).strip(),
                    "tool": "rectTool",
                    "description": item.get("description"),
                    "attributes": item.get("attributes") or [],
                }
                for item in contract_labels
                if isinstance(item, dict)
                and str(
                    item.get("label_value")
                    or item.get("name")
                    or item.get("value")
                    or ""
                ).strip()
            ]
            if contract_bound_geometry
            else labels
        )
        # Detection is a geometry-only request.  Global dimensions can be
        # filled through the dedicated tag action; including them here makes
        # the VLM solve two unrelated tasks and increases timeout/empty-result
        # risk for bbox extraction.
        allowed_tools = (
            ["rectTool"]
            if contract_bound_geometry
            else [
                tool_name
                for tool_name in config_by_tool
                if tool_name in SUPPORTED_IMAGE_TOOLS
            ]
        )
        tag_dimensions_for_model = []
    else:
        labels_for_model = []
        allowed_tools = [TAG_TOOL]
        tag_dimensions_for_model = tag_dimensions

    payload = {
        "request_id": str(uuid.uuid4()),
        "image_url": _get_image_url(sample.file),
        "task": {
            "id": task.id,
            "name": task.name,
        },
        "labels": labels_for_model,
        "tag_dimensions": tag_dimensions_for_model,
        "constraints": {
            "allowed_tools": allowed_tools,
            "max_results_per_label": 100,
            "filter_by_labels": filter_by_labels,
        },
        "prompt": prompt,
    }
    if contract_prompt:
        # Keep the user prompt separate from the immutable contract artifact.
        # ``vision_llm_chain`` renders the latter and appends the former only
        # as supplementary context.
        payload["contract_prompt"] = {
            "id": contract_prompt.get("id"),
            "contract_id": contract_prompt.get("contract_id"),
            "contract_revision": contract_prompt.get("contract_revision"),
            "label_set_signature": contract_prompt.get("label_set_signature"),
            "class_order_signature": contract_prompt.get("class_order_signature"),
            "operation": contract_prompt.get("operation"),
            "provider_key": contract_prompt.get("provider_key"),
            "model_name": contract_prompt.get("model_name"),
            "profile_key": contract_prompt.get("profile_key"),
            "profile_version": contract_prompt.get("profile_version"),
            "prompt_sha256": contract_prompt.get("prompt_sha256"),
            "effective_prompt": contract_prompt.get("effective_prompt"),
            "prompt_spec": contract_prompt.get("prompt_spec") or {},
        }
        # A structured copy is useful to non-VLM adapters and keeps the
        # provenance available without trusting anything returned by the model.
        payload["prompt_binding"] = _contract_prompt_meta(contract_prompt)
    return payload


def _build_model_payload(
    sample,
    task,
    task_config: dict[str, Any],
    cmd: AutoLabelCommand,
    *,
    contract_prompt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _build_inference_payload(
        sample,
        task,
        task_config,
        filter_by_labels=cmd.filter_by_labels,
        include_geometry_tools=bool(cmd.include_geometry_tools),
        prompt=cmd.prompt,
        contract_prompt=contract_prompt,
    )


def _annotation_meta(data: str | dict | None) -> dict[str, Any]:
    if not data:
        return {}
    parsed = data if isinstance(data, dict) else json.loads(data)
    return parsed.get("meta", {}) or {}


def _is_ai_generated(pre_annotation: TaskPreAnnotation) -> bool:
    try:
        return _annotation_meta(pre_annotation.data).get("source_type") == "ai_generated"
    except Exception:
        return False


def _finite_number(value: Any, field: str) -> int | float:
    """Validate a geometry scalar before it reaches the annotation canvas."""
    # JSON booleans are ints in Python but are never valid coordinates.
    if isinstance(value, bool):
        raise ValueError(f"geometry {field} must be a number")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"geometry {field} must be a number")
        try:
            value = float(text)
        except ValueError as exc:
            raise ValueError(f"geometry {field} must be a number") from exc
    if not isinstance(value, (int, float)):
        raise ValueError(f"geometry {field} must be a number")
    if not math.isfinite(float(value)):
        raise ValueError(f"geometry {field} must be finite")
    return value


def _validate_geometry_bounds(
    tool_name: str,
    value: int | float,
    field: str,
    image_width: int | float | None,
    image_height: int | float | None,
) -> None:
    """Reject coordinates outside the image when the VLM supplied dimensions."""
    if field == "x" or field == "width":
        limit = image_width
    else:
        limit = image_height
    if limit is not None and value > limit:
        raise ValueError(f"{tool_name} {field} is outside image bounds")


def _normalize_single_result(tool_name: str, item: dict[str, Any], order: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("annotation result must be an object")
    result_payload = item.get("result", item)
    if not isinstance(result_payload, dict):
        raise ValueError(f"{tool_name} result must be an object")
    attributes = item.get("attributes") or {}
    if item.get("score") is not None:
        attributes = {**attributes, "score": str(item["score"])}

    base = {
        "id": item.get("id") or str(uuid.uuid4()),
        "order": item.get("order", order),
        "label": item.get("label"),
        "visible": item.get("visible", True),
    }
    if attributes:
        base["attributes"] = attributes

    if tool_name == "rectTool":
        x = _finite_number(result_payload.get("x"), "x")
        y = _finite_number(result_payload.get("y"), "y")
        width = _finite_number(result_payload.get("width"), "width")
        height = _finite_number(result_payload.get("height"), "height")
        if x < 0 or y < 0:
            raise ValueError("rectTool x/y must be non-negative")
        if width <= 0 or height <= 0:
            raise ValueError("rectTool width/height must be positive")
        return {
            **base,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    if tool_name == "polygonTool":
        points = result_payload.get("points") or result_payload.get("pointList") or []
        if not isinstance(points, list) or len(points) < 3:
            raise ValueError("polygonTool points must contain at least three points")
        normalized_points = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("polygonTool point must be an object")
            normalized_points.append(
                {
                    "x": _finite_number(point.get("x"), "x"),
                    "y": _finite_number(point.get("y"), "y"),
                }
            )
        return {
            **base,
            "type": result_payload.get("type", "line"),
            "points": normalized_points,
        }

    if tool_name == "lineTool":
        points = result_payload.get("points") or result_payload.get("pointList") or []
        if not isinstance(points, list) or len(points) < 2:
            raise ValueError("lineTool points must contain at least two points")
        normalized_points = []
        for point in points:
            if not isinstance(point, dict):
                raise ValueError("lineTool point must be an object")
            normalized_points.append(
                {
                    "x": _finite_number(point.get("x"), "x"),
                    "y": _finite_number(point.get("y"), "y"),
                }
            )
        return {
            **base,
            "type": result_payload.get("type", "line"),
            "points": normalized_points,
        }

    if tool_name == "pointTool":
        x = _finite_number(result_payload.get("x"), "x")
        y = _finite_number(result_payload.get("y"), "y")
        if x < 0 or y < 0:
            raise ValueError("pointTool x/y must be non-negative")
        return {
            **base,
            "x": x,
            "y": y,
        }

    if tool_name == TAG_TOOL:
        vp = result_payload.get("value") if isinstance(result_payload, dict) else None
        norm: dict[str, list[str]] = {}
        if isinstance(vp, dict):
            for k, v in vp.items():
                key = str(k)
                if isinstance(v, list):
                    norm[key] = [str(x) for x in v if x is not None and str(x).strip()]
                elif v is not None and str(v).strip():
                    norm[key] = [str(v)]
        # 与前端预标注 schema 一致：tagTool.result[] 每项须含 type:"tag"，否则全局工具不展示预填
        return {
            **base,
            "type": "tag",
            "label": "",
            "valid": True,
            "isVisible": True,
            "value": norm,
        }

    raise LabelUException(
        code=ErrorCode.CODE_56005_AUTO_LABEL_INVALID_RESPONSE,
        status_code=status.HTTP_502_BAD_GATEWAY,
    )


def _normalize_results(
    model_data: dict[str, Any],
    sample_name: str,
    task_config: dict[str, Any],
    *,
    include_geometry_tools: bool = False,
    overwrite_existing_annotations: bool = True,
    filter_by_labels: bool = True,
    image_size: tuple[int, int] | list[int] | None = None,
    contract_prompt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    results = model_data.get("results", [])
    geometry_warnings: list[str] = []

    def record_geometry_warning(index: int, tool_name: Any, exc: Exception) -> None:
        message = (
            f"Skipped invalid {tool_name or 'geometry'} result at index {index}: {exc}"
        )
        geometry_warnings.append(message)
        logger.warning(
            "AI auto-label geometry result skipped sample={} index={} tool={} reason={}",
            sample_name,
            index,
            tool_name,
            exc,
        )

    flattened: list[tuple[str, dict[str, Any]]] = []
    if isinstance(results, dict):
        for tool_name, items in results.items():
            for item in items or []:
                if not isinstance(item, dict):
                    if include_geometry_tools and tool_name in SUPPORTED_IMAGE_TOOLS:
                        record_geometry_warning(
                            len(flattened),
                            tool_name,
                            ValueError("annotation result must be an object"),
                        )
                        continue
                    raise ValueError("annotation result must be an object")
                flattened.append((tool_name, item))
    else:
        for item in results:
            if not isinstance(item, dict):
                if include_geometry_tools:
                    record_geometry_warning(len(flattened), None, ValueError("annotation result must be an object"))
                    continue
                raise ValueError("annotation results must contain objects")
            flattened.append((item.get("toolName"), item))

    labels, _, _ = _extract_tool_configs(task_config)
    configured_labels = {
        str(name).strip()
        for label in labels
        for name in (label.get("name"), label.get("display_name"))
        if str(name or "").strip()
    }
    # A published contract is authoritative for both the model instruction
    # and response validation.  Do not let a stale task.config silently
    # discard a label from the current contract (or admit a label that was
    # removed from it).  Legacy tasks without a binding retain the historical
    # task-config behavior.
    contract_bound = isinstance(contract_prompt, dict) and bool(contract_prompt)
    contract_spec = (
        contract_prompt.get("prompt_spec")
        if contract_bound and isinstance(contract_prompt.get("prompt_spec"), dict)
        else {}
    )
    contract_labels = contract_spec.get("labels") if isinstance(contract_spec, dict) else None
    contract_label_aliases: dict[str, str] = {}
    if contract_bound and isinstance(contract_labels, list):
        configured_labels = set()
        for label in contract_labels:
            if not isinstance(label, dict):
                continue
            canonical = str(
                label.get("label_value")
                or label.get("name")
                or label.get("value")
                or ""
            ).strip()
            if not canonical:
                continue
            for alias in (
                canonical,
                label.get("label_key"),
                label.get("label_value"),
                label.get("name"),
                label.get("display_name"),
                label.get("value"),
                label.get("key"),
            ):
                normalized_alias = str(alias or "").strip()
                if normalized_alias:
                    configured_labels.add(normalized_alias)
                    contract_label_aliases[normalized_alias] = canonical
    contract_allowed_tools = None
    if contract_bound and isinstance(contract_spec, dict):
        raw_allowed_tools = (contract_spec.get("constraints") or {}).get("allowed_tools")
        if isinstance(raw_allowed_tools, list):
            contract_allowed_tools = {
                str(tool).strip() for tool in raw_allowed_tools if str(tool or "").strip()
            }
    image_width: int | float | None = None
    image_height: int | float | None = None
    if isinstance(image_size, (tuple, list)) and len(image_size) == 2:
        try:
            parsed_width = _finite_number(image_size[0], "image_width")
            parsed_height = _finite_number(image_size[1], "image_height")
            if parsed_width > 0 and parsed_height > 0:
                image_width, image_height = parsed_width, parsed_height
        except ValueError:
            # A malformed optional size must not disable basic geometry
            # validation; the coordinates are still checked below.
            pass

    # The vision chain may resize only the transient image sent to the model.
    # Convert pixel coordinates back to the attachment's original resolution
    # before validating and storing the pre-annotation.
    geometry_scale_x = geometry_scale_y = 1.0
    raw_geometry_scale = model_data.get("_geometry_scale")
    if isinstance(raw_geometry_scale, (tuple, list)) and len(raw_geometry_scale) == 2:
        try:
            parsed_scale_x = _finite_number(raw_geometry_scale[0], "geometry_scale_x")
            parsed_scale_y = _finite_number(raw_geometry_scale[1], "geometry_scale_y")
            if parsed_scale_x > 0 and parsed_scale_y > 0:
                geometry_scale_x, geometry_scale_y = parsed_scale_x, parsed_scale_y
        except ValueError:
            pass

    if not include_geometry_tools:
        raw_tag_values = model_data.get("tag_values")
        if raw_tag_values is None:
            raw_tag_values = model_data.get("tags")
        if isinstance(raw_tag_values, dict) and raw_tag_values:
            flattened.append((TAG_TOOL, {"result": {"value": raw_tag_values}}))

    grouped_annotations: dict[str, dict[str, Any]] = {}
    for index, (tool_name, item) in enumerate(flattened):
        if tool_name not in AI_SUPPORTED_TOOLS:
            continue
        if contract_allowed_tools is not None and tool_name not in contract_allowed_tools:
            # The published prompt's operation/tool contract is stricter than
            # whatever legacy task configuration may still contain.
            continue
        if not include_geometry_tools and tool_name in SUPPORTED_IMAGE_TOOLS:
            continue
        if (
            include_geometry_tools
            and (filter_by_labels or contract_bound)
            and tool_name in SUPPORTED_IMAGE_TOOLS
            and str(item.get("label") or "").strip() not in configured_labels
        ):
            # Keep the task's label taxonomy authoritative even if the model
            # invents a class name in its JSON response.
            continue
        normalized_item = item
        if contract_bound and tool_name in SUPPORTED_IMAGE_TOOLS:
            raw_label = str(item.get("label") or "").strip()
            canonical_label = contract_label_aliases.get(raw_label)
            if canonical_label and canonical_label != item.get("label"):
                normalized_item = {**item, "label": canonical_label}
        if include_geometry_tools and tool_name in SUPPORTED_IMAGE_TOOLS:
            try:
                if geometry_scale_x != 1.0 or geometry_scale_y != 1.0:
                    raw_payload = item.get("result", item)
                    if not isinstance(raw_payload, dict):
                        raise ValueError(f"{tool_name} result must be an object")
                    scaled_payload = dict(raw_payload)
                    if tool_name == "rectTool":
                        for key, scale in (
                            ("x", geometry_scale_x),
                            ("width", geometry_scale_x),
                            ("y", geometry_scale_y),
                            ("height", geometry_scale_y),
                        ):
                            if key in scaled_payload:
                                scaled_payload[key] = _finite_number(scaled_payload[key], key) * scale
                    elif tool_name in {"pointTool", "polygonTool", "lineTool"}:
                        points_key = "points" if isinstance(scaled_payload.get("points"), list) else "pointList"
                        points = scaled_payload.get(points_key)
                        if points is not None and not isinstance(points, list):
                            raise ValueError(f"{tool_name} points must be a list")
                        if isinstance(points, list):
                            scaled_points = []
                            for point in points:
                                if not isinstance(point, dict):
                                    raise ValueError(f"{tool_name} point must be an object")
                                scaled_points.append(
                                    {
                                        **point,
                                        "x": _finite_number(point.get("x"), "x") * geometry_scale_x,
                                        "y": _finite_number(point.get("y"), "y") * geometry_scale_y,
                                    }
                                )
                            scaled_payload[points_key] = scaled_points
                    normalized_item = (
                        {**item, "result": scaled_payload}
                        if item.get("result") is not None
                        else scaled_payload
                    )

                result_payload = normalized_item.get("result", normalized_item)
                if not isinstance(result_payload, dict):
                    raise ValueError(f"{tool_name} result must be an object")
                if tool_name == "rectTool":
                    x = _finite_number(result_payload.get("x"), "x")
                    y = _finite_number(result_payload.get("y"), "y")
                    width = _finite_number(result_payload.get("width"), "width")
                    height = _finite_number(result_payload.get("height"), "height")
                    _validate_geometry_bounds(tool_name, x, "x", image_width, image_height)
                    _validate_geometry_bounds(tool_name, y, "y", image_width, image_height)
                    if image_width is not None and x + width > image_width:
                        raise ValueError("rectTool extends beyond image width")
                    if image_height is not None and y + height > image_height:
                        raise ValueError("rectTool extends beyond image height")
                elif tool_name in {"pointTool", "polygonTool", "lineTool"}:
                    points = result_payload.get("points") or result_payload.get("pointList") or []
                    if not isinstance(points, list):
                        raise ValueError(f"{tool_name} points must be a list")
                    for point in points:
                        if not isinstance(point, dict):
                            raise ValueError(f"{tool_name} point must be an object")
                        px = _finite_number(point.get("x"), "x")
                        py = _finite_number(point.get("y"), "y")
                        if px < 0 or py < 0:
                            raise ValueError(f"{tool_name} point coordinates must be non-negative")
                        if image_width is not None and px > image_width:
                            raise ValueError(f"{tool_name} point is outside image width")
                        if image_height is not None and py > image_height:
                            raise ValueError(f"{tool_name} point is outside image height")
                normalized_result = _normalize_single_result(tool_name, normalized_item, index)
            except (TypeError, ValueError, KeyError) as exc:
                record_geometry_warning(index, tool_name, exc)
                continue
        else:
            normalized_result = _normalize_single_result(tool_name, normalized_item, index)
        grouped_annotations.setdefault(tool_name, {"toolName": tool_name, "result": []})
        grouped_annotations[tool_name]["result"].append(normalized_result)

    tg_block = grouped_annotations.get(TAG_TOOL)
    if tg_block and isinstance(tg_block.get("result"), list):
        tg_block["result"] = expand_tag_tool_result_items(tg_block["result"])

    _, config_by_tool, _ = _extract_tool_configs(task_config)
    # Pre-annotation config format: { toolName: [{key, value, color}, ...] }
    pre_annotation_config = {
        tool_name: tool_cfg.get("attributes", [])
        for tool_name, tool_cfg in config_by_tool.items()
    }
    warning_message = model_data.get("warning_message")
    if geometry_warnings:
        warning_messages = ([str(warning_message)] if warning_message else []) + geometry_warnings
        warning_message = "; ".join(warning_messages)
    return {
        "sample_name": sample_name,
        "annotations": grouped_annotations,
        "config": pre_annotation_config,
        "meta": {
            "source_type": "ai_generated",
            "provider": settings.AI_PROVIDER,
            "model": model_data.get("model") or _model_runtime().get("model") or settings.AI_MODEL_NAME,
            "latency_ms": model_data.get("latency_ms"),
            "warning_message": warning_message,
            "include_geometry_tools": include_geometry_tools,
            "overwrite_existing_annotations": overwrite_existing_annotations,
            **_contract_prompt_meta(contract_prompt),
        },
    }


def _json_loads_maybe(data: Any) -> Any:
    if not isinstance(data, str):
        return data
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None


def _extract_sample_result(sample_data: Any) -> dict[str, Any]:
    parsed_data = _json_loads_maybe(sample_data)
    if not isinstance(parsed_data, dict):
        return {}
    raw_result = parsed_data.get("result")
    parsed_result = _json_loads_maybe(raw_result)
    return parsed_result if isinstance(parsed_result, dict) else {}


def _sample_has_formal_geometry(sample: TaskSample) -> bool:
    result = _extract_sample_result(sample.data)
    for tool_name in SUPPORTED_IMAGE_TOOLS:
        block = result.get(tool_name)
        if not isinstance(block, dict):
            continue
        items = block.get("result")
        if isinstance(items, list) and len(items) > 0:
            return True
    return False


def _sample_global_tags_complete(sample: TaskSample, task_config: dict[str, Any]) -> bool:
    return bool(audit_one_sample(sample, task_config).get("tags_ok"))


def _raise_if_ai_global_tags_incomplete(sample: TaskSample, task_config: dict[str, Any]) -> None:
    audit = audit_one_sample(sample, task_config)
    if audit.get("tags_ok"):
        return
    missing = audit.get("missing_tag_keys") or []
    raise ValueError(
        "AI global tags incomplete"
        + (f"; missing dimensions: {', '.join(str(x) for x in missing)}" if missing else "")
    )


def _merge_ai_global_tags_into_sample_data(
    sample_data: dict[str, Any],
    normalized_payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Only promote AI LLM tagTool output into formal sample data.

    Geometry from the same AI payload remains pre-annotation only.
    """
    if not isinstance(sample_data, dict) or not isinstance(normalized_payload, dict):
        return sample_data, False

    annotations = normalized_payload.get("annotations")
    if not isinstance(annotations, dict):
        return sample_data, False

    tag_tool = annotations.get(TAG_TOOL)
    if not isinstance(tag_tool, dict) or not isinstance(tag_tool.get("result"), list):
        return sample_data, False

    ai_tag_items = expand_tag_tool_result_items(tag_tool.get("result") or [])
    if not ai_tag_items:
        return sample_data, False

    original_result = sample_data.get("result")
    result_was_string = isinstance(original_result, str)
    result_data = _json_loads_maybe(original_result) if result_was_string else original_result
    if not isinstance(result_data, dict):
        result_data = {}

    merged_result = {
        **result_data,
        TAG_TOOL: {
            "toolName": TAG_TOOL,
            "result": ai_tag_items,
        },
    }
    merged_sample_data = {
        **sample_data,
        "result": json.dumps(merged_result, ensure_ascii=False) if result_was_string else merged_result,
    }
    return merged_sample_data, True


def _apply_ai_global_tags_to_sample(
    db: Session,
    sample,
    normalized_payload: dict[str, Any],
    task_config: dict[str, Any],
    updated_by: int,
) -> bool:
    sample_data = _json_loads_maybe(sample.data) or {}
    if not isinstance(sample_data, dict):
        sample_data = {}
    merged_data, changed = _merge_ai_global_tags_into_sample_data(sample_data, normalized_payload)
    if not changed:
        return False

    obj_in: dict[str, Any] = {
        TaskSample.data.key: json.dumps(merged_data, ensure_ascii=False),
        TaskSample.updated_by.key: updated_by,
    }
    current_state = sample.state
    if current_state != SampleState.SKIPPED.value:
        sample.data = obj_in[TaskSample.data.key]
        audit = audit_one_sample(sample, task_config)
        obj_in[TaskSample.state.key] = audit.get("sample_quality_state") or current_state

    crud_sample.update(db=db, db_obj=sample, obj_in=obj_in)
    return True


def _batch_mode_requests_geometry(cmd: BatchAutoLabelCommand) -> bool:
    return (
        str(cmd.mode or BATCH_MODE_TAGS_ONLY).strip().upper() == BATCH_MODE_BOX_AND_TAGS
        and bool(cmd.include_geometry_tools)
    )


def _batch_sample_states(cmd: BatchAutoLabelCommand) -> list[str]:
    if cmd.sample_states:
        selected: list[str] = []
        for raw_state in cmd.sample_states:
            state = str(raw_state).strip().upper()
            if state in BATCH_SAMPLE_STATES and state not in selected:
                selected.append(state)
        return selected or [SampleState.NEW.value]

    if cmd.include_done_samples:
        return [SampleState.NEW.value, SampleState.INCOMPLETE.value, SampleState.DONE.value]
    return [SampleState.NEW.value]


def _filter_batch_targets(
    samples: list[TaskSample],
    task_config: dict[str, Any],
    *,
    skip_complete_global_tags: bool,
    include_geometry_tools: bool = False,
) -> list[TaskSample]:
    targets = list(samples)
    if include_geometry_tools:
        # Detection mode must not create duplicate boxes on samples that
        # already have formal geometry. Existing manual geometry is preserved.
        targets = [sample for sample in targets if not _sample_has_formal_geometry(sample)]
    if skip_complete_global_tags and not include_geometry_tools:
        targets = [sample for sample in targets if not _sample_global_tags_complete(sample, task_config)]
    return targets


def _sample_batch_include_geometry(sample: TaskSample, requested_include_geometry: bool) -> bool:
    return (
        requested_include_geometry
        and sample.state == SampleState.NEW.value
        and not _sample_has_formal_geometry(sample)
    )


@log_business_operation("auto_label.sample")
async def create(
    db: Session,
    task_id: int,
    sample_id: int,
    cmd: AutoLabelCommand,
    current_user: User,
) -> AutoLabelResponse:
    if not settings.AI_AUTO_LABEL_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_56000_AUTO_LABEL_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if cmd.include_geometry_tools:
        _ensure_ai_backend_configured(AI_BBOX_DETECTION_PROVIDER)
    else:
        # Keep the legacy no-argument call shape for embedded integrations
        # that wrap the global-tag backend probe.
        _ensure_ai_backend_configured()

    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    assert_user_has_task_access(task, current_user)

    if task.media_type != MediaType.IMAGE.value:
        raise LabelUException(
            code=ErrorCode.CODE_56001_AUTO_LABEL_UNSUPPORTED_MEDIA,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    sample = crud_sample.get(db=db, sample_id=sample_id)
    if not sample or sample.task_id != task_id or not sample.file:
        raise LabelUException(
            code=ErrorCode.CODE_55001_SAMPLE_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    include_geometry_tools = bool(cmd.include_geometry_tools)
    # Resolve before the cache check.  A newly published contract prompt must
    # invalidate an older AI pre-annotation instead of silently reusing it.
    contract_prompt = _resolve_contract_prompt(
        db,
        task,
        include_geometry_tools=include_geometry_tools,
    )
    existing_pre_annotations, _ = crud_pre_annotation.list_by(
        db=db,
        task_id=task_id,
        sample_name=sample.file.filename,
        page=0,
        size=100,
    )
    existing_ai_annotations = [item for item in existing_pre_annotations if _is_ai_generated(item)]
    if (
        existing_ai_annotations
        and not cmd.overwrite
        and _cached_contract_prompt_matches(
            _annotation_meta(existing_ai_annotations[-1].data), contract_prompt
        )
    ):
        latest_pre_annotation = existing_ai_annotations[-1]
        meta = _annotation_meta(latest_pre_annotation.data)
        cached_payload = _json_loads_maybe(latest_pre_annotation.data)
        if isinstance(cached_payload, dict):
            task_cfg_ai = resolve_ai_task_config(db, task)
            with begin_transaction(db):
                changed = _apply_ai_global_tags_to_sample(
                    db=db,
                    sample=sample,
                    normalized_payload=cached_payload,
                    task_config=task_cfg_ai,
                    updated_by=current_user.id,
                )
            if changed:
                task_for_training_ready = crud_task.get(db=db, task_id=task_id)
                if task_for_training_ready:
                    refresh_task_training_ready(db, task_for_training_ready)
        biz_log(
            "auto_label.reuse_cached",
            task_id=task_id,
            sample_id=sample_id,
            pre_annotation_id=latest_pre_annotation.id,
        )
        return AutoLabelResponse(
            status="COMPLETED",
            task_id=task_id,
            sample_id=sample_id,
            media_type=task.media_type,
            provider=meta.get("provider") or settings.AI_PROVIDER,
            model=meta.get("model") or _model_runtime().get("model") or settings.AI_MODEL_NAME,
            latency_ms=meta.get("latency_ms"),
            pre_annotation_id=latest_pre_annotation.id,
            warning_message=meta.get("warning_message"),
        )

    task_cfg_ai = resolve_ai_task_config(db, task)
    overwrite_existing_annotations = False
    if contract_prompt:
        model_payload = _build_model_payload(
            sample,
            task,
            task_cfg_ai,
            cmd,
            contract_prompt=contract_prompt,
        )
    else:
        # Preserve the old call shape for embedded integrations that monkey-
        # patch the payload builder with a positional-only compatibility shim.
        model_payload = _build_model_payload(sample, task, task_cfg_ai, cmd)
    biz_log(
        "auto_label.model_request",
        task_id=task_id,
        sample_id=sample_id,
        sample_name=sample.file.filename,
        include_geometry_tools=include_geometry_tools,
        overwrite_existing_annotations=overwrite_existing_annotations,
        filter_by_labels=cmd.filter_by_labels,
        provider=settings.AI_PROVIDER,
        model=_model_runtime().get("model") or settings.AI_MODEL_NAME,
    )
    start = time.perf_counter()
    try:
        use_vision_chain = (
            _configured_bbox_vision_available()
            if include_geometry_tools
            else _vision_chain_mode() or _configured_global_tag_vision_available()
        )
        if use_vision_chain:
            model_data = await asyncio.to_thread(
                vision_llm_chain.call_vision_llm_chain,
                model_payload,
                provider_key=AI_BBOX_DETECTION_PROVIDER if include_geometry_tools else AI_GLOBAL_TAG_PROVIDER,
            )
        else:
            runtime = _validated_local_http_runtime()
            model_payload = _apply_model_runtime_options(model_payload, runtime)
            http_prompt = _contract_prompt_for_http_payload(model_payload)
            if http_prompt:
                # Legacy local adapters only inspect the top-level prompt;
                # keep the structured contract payload for provenance while
                # exposing the immutable effective prompt at their boundary.
                model_payload["prompt"] = http_prompt
            async with httpx.AsyncClient(
                timeout=float(runtime.get("timeout_seconds") or settings.AI_MODEL_TIMEOUT_SECONDS),
                verify=bool(runtime.get("verify_tls", False)),
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    runtime["endpoint"],
                    json=model_payload,
                    headers=_model_auth_headers(runtime),
                )
                if response.status_code >= 400:
                    logger.error(
                        "model service returned {}: {}",
                        response.status_code,
                        response.text,
                    )
                response.raise_for_status()
                model_data = response.json()
    except Exception as exc:
        logger.opt(exception=exc).error("auto label model request failed")
        detail = str(exc)
        base_msg = ErrorCode.CODE_56003_AUTO_LABEL_MODEL_ERROR.value[1]
        raise LabelUException(
            code=ErrorCode.CODE_56003_AUTO_LABEL_MODEL_ERROR,
            message=f"{base_msg}：{detail}",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )

    normalized_payload = _normalize_results(
        model_data=model_data,
        sample_name=sample.file.filename,
        task_config=task_cfg_ai,
        include_geometry_tools=include_geometry_tools,
        overwrite_existing_annotations=overwrite_existing_annotations,
        filter_by_labels=cmd.filter_by_labels,
        image_size=model_data.get("_image_size") if isinstance(model_data, dict) else None,
        contract_prompt=contract_prompt,
    )
    normalized_payload["meta"]["latency_ms"] = int((time.perf_counter() - start) * 1000)

    with begin_transaction(db):
        if existing_ai_annotations:
            crud_pre_annotation.delete(
                db=db,
                pre_annotation_ids=[item.id for item in existing_ai_annotations],
            )

        pre_annotations = crud_pre_annotation.batch(
            db=db,
            pre_annotations=[
                TaskPreAnnotation(
                    task_id=task_id,
                    file_id=None,
                    sample_name=sample.file.filename,
                    data=json.dumps(normalized_payload, ensure_ascii=False),
                    created_by=current_user.id,
                    updated_by=current_user.id,
                )
            ],
        )
        _apply_ai_global_tags_to_sample(
            db=db,
            sample=sample,
            normalized_payload=normalized_payload,
            task_config=task_cfg_ai,
            updated_by=current_user.id,
        )

    # The old pre-annotation remains the canvas compatibility projection. A
    # provider-bound immutable snapshot is created independently and enters
    # review as PENDING; bridge failures are non-fatal to the legacy request.
    try:
        bridge_legacy_preannotation(
            db,
            task_id=task_id,
            sample_id=sample_id,
            payload=normalized_payload,
            provider_key="vision_llm",
            created_by=current_user.id,
        )
    except Exception:
        logger.opt(exception=True).warning(
            "auto label provider bridge failed task_id={} sample_id={}",
            task_id,
            sample_id,
        )

    task_for_training_ready = crud_task.get(db=db, task_id=task_id)
    if task_for_training_ready:
        refresh_task_training_ready(db, task_for_training_ready)

    created_pre_annotation = pre_annotations[0]
    meta = normalized_payload.get("meta", {})
    biz_log(
        "auto_label.pre_annotation_saved",
        task_id=task_id,
        sample_id=sample_id,
        pre_annotation_id=created_pre_annotation.id,
    )
    return AutoLabelResponse(
        status="COMPLETED",
        task_id=task_id,
        sample_id=sample_id,
        media_type=task.media_type,
        provider=meta.get("provider") or settings.AI_PROVIDER,
        model=meta.get("model") or _model_runtime().get("model") or settings.AI_MODEL_NAME,
        latency_ms=meta.get("latency_ms"),
        pre_annotation_id=created_pre_annotation.id,
        warning_message=meta.get("warning_message"),
    )


# ── Batch auto-label ──────────────────────────────────────────────────────────


@log_business_operation("auto_label.batch_job")
async def create_batch_job(
    db: Session,
    task_id: int,
    cmd: BatchAutoLabelCommand,
    current_user: User,
) -> AutoLabelJobResponse:
    if not settings.AI_AUTO_LABEL_ENABLED:
        raise LabelUException(
            code=ErrorCode.CODE_56000_AUTO_LABEL_DISABLED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if _batch_mode_requests_geometry(cmd):
        _ensure_ai_backend_configured(AI_BBOX_DETECTION_PROVIDER)
    else:
        _ensure_ai_backend_configured()

    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    assert_user_has_task_access(task, current_user)

    if task.media_type != MediaType.IMAGE.value:
        raise LabelUException(
            code=ErrorCode.CODE_56001_AUTO_LABEL_UNSUPPORTED_MEDIA,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    task_config = resolve_ai_task_config(db, task)
    requested_include_geometry = _batch_mode_requests_geometry(cmd)
    overwrite_existing_annotations = False
    # Validate prompt publication before enqueueing a job.  Workers resolve it
    # again per sample so a contract revision cannot change mid-run unnoticed.
    contract_prompt = _resolve_contract_prompt(
        db,
        task,
        include_geometry_tools=requested_include_geometry,
    )
    # A published contract prompt owns the geometry vocabulary and can supply
    # labels even when the legacy task.config has not been backfilled.  Keep
    # the old config validation only for unbound/legacy paths (and for the
    # explicit global-tag compatibility fallback).
    if not contract_prompt:
        _validate_auto_label_task_config(
            task_config,
            include_geometry_tools=requested_include_geometry,
        )

    sample_states = _batch_sample_states(cmd)
    targets = _filter_batch_targets(
        crud_sample.list_batch_auto_label_targets(
            db=db,
            task_id=task_id,
            include_done_samples=cmd.include_done_samples,
            sample_states=sample_states,
        ),
        task_config,
        skip_complete_global_tags=cmd.skip_complete_global_tags,
        include_geometry_tools=requested_include_geometry,
    )
    if not targets:
        raise LabelUException(
            code=ErrorCode.CODE_56006_AUTO_LABEL_NO_SAMPLES,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    sample_ids = [s.id for s in targets]
    biz_log(
        "auto_label.batch_enqueue",
        task_id=task_id,
        sample_count=len(sample_ids),
        mode=BATCH_MODE_BOX_AND_TAGS if requested_include_geometry else BATCH_MODE_TAGS_ONLY,
        include_geometry_tools=requested_include_geometry,
        overwrite_existing_annotations=overwrite_existing_annotations,
        include_done_samples=cmd.include_done_samples,
        sample_states=sample_states,
        skip_complete_global_tags=cmd.skip_complete_global_tags,
    )

    with begin_transaction(db):
        assert_no_active_task_background_job(db=db, task_id=task_id)
        job = crud_auto_label_job.create(
            db=db,
            task_id=task_id,
            user_id=current_user.id,
            sample_count=len(sample_ids),
            filter_by_labels=cmd.filter_by_labels,
        )
        job_id = job.id

    asyncio.get_event_loop().run_in_executor(
        None,
        _run_batch_auto_label_sync,
        job_id,
        task_id,
        sample_ids,
        cmd.filter_by_labels,
        requested_include_geometry,
        overwrite_existing_annotations,
    )

    return AutoLabelJobResponse(
        id=job_id,
        task_id=task_id,
        status=AutoLabelStatus.PENDING.value,
        sample_count=len(sample_ids),
        processed_count=0,
        success_count=0,
        failed_count=0,
        created_at=job.created_at,
    )


@log_business_operation("auto_label.get_batch_job")
def get_batch_job(
    db: Session,
    task_id: int,
    job_id: int,
    current_user: User,
) -> AutoLabelJobResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)
    job = crud_auto_label_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return AutoLabelJobResponse(
        id=job.id,
        task_id=job.task_id,
        status=job.status,
        sample_count=job.sample_count or 0,
        processed_count=job.processed_count or 0,
        success_count=job.success_count or 0,
        failed_count=job.failed_count or 0,
        error_message=job.error_message,
        created_at=job.created_at,
    )


@log_business_operation("auto_label.get_active_batch_job")
def get_active_batch_job(db: Session, task_id: int, current_user: User) -> AutoLabelJobResponse | None:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)

    job = crud_auto_label_job.get_active_by_task_id(db=db, task_id=task_id)
    if not job:
        return None
    return AutoLabelJobResponse(
        id=job.id,
        task_id=job.task_id,
        status=job.status,
        sample_count=job.sample_count or 0,
        processed_count=job.processed_count or 0,
        success_count=job.success_count or 0,
        failed_count=job.failed_count or 0,
        error_message=job.error_message,
        created_at=job.created_at,
    )


@log_business_operation("auto_label.cancel_batch_job")
def cancel_batch_job(
    db: Session,
    task_id: int,
    job_id: int,
    current_user: User,
) -> AutoLabelJobResponse:
    task = crud_task.get(db=db, task_id=task_id)
    if not task:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )
    assert_user_has_task_access(task, current_user)

    job = crud_auto_label_job.get(db=db, job_id=job_id)
    if not job or job.task_id != task_id:
        raise LabelUException(
            code=ErrorCode.CODE_50002_TASK_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if job.status in {AutoLabelStatus.PENDING.value, AutoLabelStatus.PROCESSING.value}:
        with begin_transaction(db):
            job = crud_auto_label_job.update_status(db, job, AutoLabelStatus.CANCELLED.value)

    return AutoLabelJobResponse(
        id=job.id,
        task_id=job.task_id,
        status=job.status,
        sample_count=job.sample_count or 0,
        processed_count=job.processed_count or 0,
        success_count=job.success_count or 0,
        failed_count=job.failed_count or 0,
        error_message=job.error_message,
        created_at=job.created_at,
    )


def _job_is_cancelled(job_id: int) -> bool:
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        job = crud_auto_label_job.get(db=db, job_id=job_id)
        return bool(job and job.status == AutoLabelStatus.CANCELLED.value)


def _run_batch_auto_label_sync(
    job_id: int,
    task_id: int,
    sample_ids: list[int],
    filter_by_labels: bool,
    include_geometry_tools: bool,
    overwrite_existing_annotations: bool,
):
    """Run batch auto-label in a thread. Processes samples sequentially."""
    from labelu.internal.common.db import session_scope

    set_request_context(request_id=f"batch-{job_id}", user_id=None)
    biz_log(
        "auto_label.batch_run.start",
        job_id=job_id,
        task_id=task_id,
        sample_count=len(sample_ids),
    )
    try:
        with session_scope() as db:
            job = crud_auto_label_job.get(db=db, job_id=job_id)
            if not job:
                return
            if job.status == AutoLabelStatus.CANCELLED.value:
                return
            with begin_transaction(db):
                crud_auto_label_job.update_status(db, job, AutoLabelStatus.PROCESSING.value)
            task = crud_task.get(db=db, task_id=task_id)
            if not task:
                raise RuntimeError(f"task {task_id} not found")
            task_config = resolve_ai_task_config(db, task)
            created_by = job.created_by

        for sample_id in sample_ids:
            if _job_is_cancelled(job_id):
                biz_log("auto_label.batch_run.cancelled", job_id=job_id, task_id=task_id)
                break
            try:
                _process_single_sample(
                    task_id=task_id,
                    task_config=task_config,
                    sample_id=sample_id,
                    filter_by_labels=filter_by_labels,
                    include_geometry_tools=include_geometry_tools,
                    overwrite_existing_annotations=overwrite_existing_annotations,
                    created_by=created_by,
                )
                with session_scope() as db:
                    job = crud_auto_label_job.get(db=db, job_id=job_id)
                    if job and job.status != AutoLabelStatus.CANCELLED.value:
                        with begin_transaction(db):
                            crud_auto_label_job.increment_progress(db, job, success=True)
            except Exception as exc:
                logger.error("Batch auto-label failed for sample {}: {}", sample_id, str(exc))
                with session_scope() as db:
                    job = crud_auto_label_job.get(db=db, job_id=job_id)
                    if job and job.status != AutoLabelStatus.CANCELLED.value:
                        with begin_transaction(db):
                            crud_auto_label_job.increment_progress(db, job, success=False)

        with session_scope() as db:
            job = crud_auto_label_job.get(db=db, job_id=job_id)
            if job:
                if job.status != AutoLabelStatus.CANCELLED.value:
                    with begin_transaction(db):
                        crud_auto_label_job.update_status(db, job, AutoLabelStatus.COMPLETED.value)
                biz_log(
                    "auto_label.batch_run.done",
                    job_id=job_id,
                    task_id=task_id,
                    processed_count=job.processed_count,
                    success_count=job.success_count,
                    failed_count=job.failed_count,
                )
            task_for_training_ready = crud_task.get(db=db, task_id=task_id)
            if task_for_training_ready:
                refresh_task_training_ready(db, task_for_training_ready)
    except Exception as e:
        biz_log("auto_label.batch_run.failed", job_id=job_id, task_id=task_id, error=str(e))
        logger.error("Batch auto-label job {} failed: {}", job_id, str(e))
        try:
            with session_scope() as db:
                job = crud_auto_label_job.get(db=db, job_id=job_id)
                if job and job.status != AutoLabelStatus.CANCELLED.value:
                    with begin_transaction(db):
                        crud_auto_label_job.update_status(
                            db,
                            job,
                            AutoLabelStatus.FAILED.value,
                            error_message=str(e),
                        )
        except Exception:
            logger.error("Failed to update auto-label job status for job {}", job_id)


def _process_single_sample(
    *,
    task_id: int,
    task_config: dict[str, Any],
    sample_id: int,
    filter_by_labels: bool,
    include_geometry_tools: bool,
    overwrite_existing_annotations: bool,
    created_by: int,
):
    """Process a single sample: call model, normalize results, save pre-annotation."""
    from labelu.internal.common.db import session_scope

    with session_scope() as db:
        task = crud_task.get(db=db, task_id=task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")
        sample = crud_sample.get(db=db, sample_id=sample_id)
        if not sample or not sample.file:
            raise ValueError(f"Sample {sample_id} not found or has no file")
        sample_name = sample.file.filename
        effective_include_geometry = bool(include_geometry_tools)
        contract_prompt = _resolve_contract_prompt(
            db,
            task,
            include_geometry_tools=effective_include_geometry,
        )
        payload_kwargs = {
            "filter_by_labels": filter_by_labels,
            "include_geometry_tools": effective_include_geometry,
            "prompt": None,
        }
        if contract_prompt:
            payload_kwargs["contract_prompt"] = contract_prompt
        payload = _build_inference_payload(sample, task, task_config, **payload_kwargs)

    # A tested system-management connection takes precedence over the legacy
    # .env endpoint.  This also applies to the dedicated automatic worker,
    # whose production environment may still have AI_PROVIDER=local_http.
    use_vision_chain = (
        _configured_bbox_vision_available()
        if effective_include_geometry
        else _vision_chain_mode() or _configured_global_tag_vision_available()
    )
    if use_vision_chain:
        model_data = vision_llm_chain.call_vision_llm_chain(
            payload,
            provider_key=AI_BBOX_DETECTION_PROVIDER if effective_include_geometry else AI_GLOBAL_TAG_PROVIDER,
        )
    else:
        runtime = _validated_local_http_runtime()
        payload = _apply_model_runtime_options(payload, runtime)
        http_prompt = _contract_prompt_for_http_payload(payload)
        if http_prompt:
            # Keep local HTTP behavior aligned with the managed vision path:
            # a published contract is authoritative, with any request prompt
            # retained only as supplementary context.
            payload["prompt"] = http_prompt
        with httpx.Client(
            timeout=float(runtime.get("timeout_seconds") or settings.AI_MODEL_TIMEOUT_SECONDS),
            verify=bool(runtime.get("verify_tls", False)),
            follow_redirects=False,
        ) as client:
            response = client.post(
                runtime["endpoint"],
                json=payload,
                headers=_model_auth_headers(runtime),
            )
            if response.status_code >= 400:
                logger.error("model service returned {}: {}", response.status_code, response.text)
            response.raise_for_status()
            model_data = response.json()

    normalized_payload = _normalize_results(
        model_data=model_data,
        sample_name=sample_name,
        task_config=task_config,
        include_geometry_tools=effective_include_geometry,
        overwrite_existing_annotations=False,
        filter_by_labels=filter_by_labels,
        image_size=model_data.get("_image_size") if isinstance(model_data, dict) else None,
        contract_prompt=contract_prompt,
    )

    with session_scope() as db:
        existing_pre_annotations, _ = crud_pre_annotation.list_by(
            db=db, task_id=task_id, sample_name=sample_name, page=0, size=100,
        )
        existing_ai = [item for item in existing_pre_annotations if _is_ai_generated(item)]
        with begin_transaction(db):
            if existing_ai:
                crud_pre_annotation.delete(
                    db=db, pre_annotation_ids=[item.id for item in existing_ai]
                )
            crud_pre_annotation.batch(
                db=db,
                pre_annotations=[
                    TaskPreAnnotation(
                        task_id=task_id,
                        file_id=None,
                        sample_name=sample_name,
                        data=json.dumps(normalized_payload, ensure_ascii=False),
                        created_by=created_by,
                        updated_by=created_by,
                    )
                ],
            )
            sample = crud_sample.get(db=db, sample_id=sample_id)
            if sample:
                changed = _apply_ai_global_tags_to_sample(
                    db=db,
                    sample=sample,
                    normalized_payload=normalized_payload,
                    task_config=task_config,
                    updated_by=created_by,
                )
                if changed and _extract_tool_configs(task_config)[2]:
                    _raise_if_ai_global_tags_incomplete(sample, task_config)
                elif not changed and not include_geometry_tools:
                    raise ValueError("AI did not return global tagTool values")
        try:
            bridge_legacy_preannotation(
                db,
                task_id=task_id,
                sample_id=sample_id,
                payload=normalized_payload,
                provider_key="vision_llm",
                created_by=created_by,
            )
        except Exception:
            logger.opt(exception=True).warning(
                "auto label batch provider bridge failed task_id={} sample_id={}",
                task_id,
                sample_id,
            )
