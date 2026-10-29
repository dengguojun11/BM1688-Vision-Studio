from __future__ import annotations

import json
from typing import Any

from fastapi import status
from fastapi.encoders import jsonable_encoder
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.orm import Session

from labelu.internal.adapter.persistence import crud_annotation_taxonomy
from labelu.internal.application.command.yolo_quality_policy import RecommendYoloQualityPolicyCommand
from labelu.internal.application.response.yolo_quality_policy import (
    YoloQualityPolicyRecommendationResponse,
    YoloQualityPolicyResponse,
)
from labelu.internal.application.service import yolo_aicm_publish as yolo_aicm_publish_service
from labelu.internal.application.service import yolo_train as yolo_train_service
from labelu.internal.application.service import annotation_contract as annotation_contract_service
from labelu.internal.common.label_contract import label_set_signature
from labelu.internal.application.service.vision_multimodal_chat import chat_multimodal_markdown
from labelu.internal.application.service.vision_llm_chain import _configured_model_for
from labelu.internal.common.ai_labeling_builtin import resolve_effective_vision_call_plan
from labelu.internal.common.ai_labeling_runtime import load_ai_labeling_runtime
from labelu.internal.common.config import settings
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.domain.models.user import User

_SYSTEM_PROMPT = (
    "你是 YOLO 模型质量标准设计专家。"
    "请根据训练历史、边端验证与业务目标，输出一个结构化 JSON 质量标准草稿。"
    "这是“场景质量标准”草稿，不是当前某个模型的最终判定结果。"
    "不要把“可发布”、“已达标”、“边端可用”、“需复核”等"
    "写成当前模型已完成的事实结论，只能写成标准规则、"
    "人工审核说明或风险提示。"
    "如果缺少 test 集、边端实测或量化信息，必须显式说明“需在训练完成后再依据实测结果判定”。"
    "只返回 JSON，不要输出 Markdown。"
)
_TRAIN_SUMMARY_LIMIT = 1
_PUBLISH_SUMMARY_LIMIT = 1


def _append_provider(
    out: list[dict[str, str]],
    *,
    provider_key: str,
    label: str,
    base_url: str,
    model: str,
    api_key: str,
    allow_unauthenticated: bool = False,
) -> None:
    from labelu.internal.application.service.yolo_train_evaluation import (
        _validated_provider_spec,
    )

    spec = _validated_provider_spec(
        label, base_url, api_key, model,
        provider_key=provider_key, allow_unauthenticated=allow_unauthenticated,
    )
    if not spec:
        return
    label, base_url, api_key, model = spec
    for item in out:
        if str(item.get("provider_key") or "").strip() == provider_key:
            return
        if (
            str(item.get("base_url") or "").strip() == base_url
            and str(item.get("model") or "").strip() == model
        ):
            return
    out.append(
        {
            "provider_key": provider_key or model,
            "label": label or provider_key or model,
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
        }
    )


def _candidate_providers() -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    configured_model = _configured_model_for("vision_llm")
    if configured_model:
        if not configured_model.get("blocked"):
            provider_key = str(configured_model.get("provider_key") or "vision_llm")
            _append_provider(
                out,
                provider_key=provider_key,
                label=str(configured_model.get("name") or provider_key),
                base_url=configured_model.get("endpoint"),
                model=configured_model.get("model"),
                api_key=configured_model.get("api_key"),
                allow_unauthenticated=(
                    provider_key != "volcengine"
                    and configured_model.get("auth_type") == "none"
                ),
            )
        return out

    runtime = load_ai_labeling_runtime()

    runtime_for_plan = dict(runtime)
    profiles_for_plan = []
    for profile in runtime.get("llm_profiles") or []:
        if not isinstance(profile, dict):
            continue
        normalized = dict(profile)
        normalized_id = str(normalized.get("id") or normalized.get("model") or "").strip()
        if normalized_id:
            normalized["id"] = normalized_id
        profiles_for_plan.append(normalized)
    runtime_for_plan["llm_profiles"] = profiles_for_plan

    plan = resolve_effective_vision_call_plan(runtime_for_plan)
    for step in plan.get("steps") or []:
        if not isinstance(step, dict):
            continue
        provider_key = str(step.get("provider_key") or "").strip()
        base_url = str(step.get("base_url") or "").strip()
        model = str(step.get("model") or "").strip()
        label = str(step.get("label") or provider_key or model).strip()
        if not provider_key or not base_url or not model:
            continue
        api_key = ""
        kind = str(step.get("kind") or "").strip()
        if kind == "profile":
            for profile in profiles_for_plan:
                if str(profile.get("id") or "").strip() != provider_key:
                    continue
                api_key = str(profile.get("api_key") or "").strip()
                break
        elif provider_key == "volcengine":
            api_key = str(settings.VISION_VOLCENGINE_API_KEY or "").strip()
        _append_provider(
            out,
            provider_key=provider_key or model,
            label=label,
            base_url=base_url,
            model=model,
            api_key=api_key,
        )

    for profile in runtime.get("llm_profiles") or []:
        if not isinstance(profile, dict):
            continue
        base_url = str(profile.get("base_url") or "").strip()
        model = str(profile.get("model") or "").strip()
        api_key = str(profile.get("api_key") or "").strip()
        _append_provider(
            out,
            provider_key=str(profile.get("id") or model).strip() or model,
            label=str(profile.get("name") or model).strip() or model,
            base_url=base_url,
            model=model,
            api_key=api_key,
        )
    return out


def _fallback_reason(providers: list[dict[str, str]], error: Exception | None = None) -> str:
    if not providers:
        return "未配置可用的 AI 服务，已生成系统建议模板。"
    if error is not None:
        return "AI 服务暂时不可用，已生成系统建议模板。"
    return "AI 返回结果无法解析，已生成系统建议模板。"


def _recommend_timeout_seconds() -> int:
    default_timeout = 20
    configured = int(settings.AI_MODEL_TIMEOUT_SECONDS or 0)
    if configured <= 0:
        return default_timeout
    return min(configured, default_timeout)


def _row_to_dict(row: Any) -> dict[str, Any]:
    data: Any
    if hasattr(row, "model_dump"):
        try:
            data = row.model_dump(mode="json")
        except TypeError:
            data = row.model_dump()
    elif hasattr(row, "dict"):
        data = row.dict()
    else:
        data = dict(row or {})
    encoded = jsonable_encoder(data)
    return encoded if isinstance(encoded, dict) else dict(encoded or {})


def _quality_gate_summary(bundle: Any, key: str) -> dict[str, Any] | None:
    if not isinstance(bundle, dict):
        return None
    gate = bundle.get(key)
    if not isinstance(gate, dict):
        return None
    return {
        "status": gate.get("status"),
        "summary": gate.get("summary"),
        "reasons": [str(item) for item in (gate.get("reasons") or [])[:2]],
    }


def _trained_model_summary(row: Any) -> dict[str, Any]:
    data = _row_to_dict(row)
    train_metrics = data.get("training_val_metrics") or {}
    test_metrics = data.get("test_val_metrics") or {}
    validation_pass = data.get("validation_pass") or {}
    quality_assessment = data.get("quality_assessment") or {}
    return {
        "job_id": data.get("job_id"),
        "created_at": data.get("created_at"),
        "training_metrics": {
            "map50_95": train_metrics.get("map50_95"),
            "map50": train_metrics.get("map50"),
            "precision": train_metrics.get("precision"),
            "recall": train_metrics.get("recall"),
        },
        "test_metrics": {
            "map50_95": test_metrics.get("map50_95"),
            "map50": test_metrics.get("map50"),
            "precision": test_metrics.get("precision"),
            "recall": test_metrics.get("recall"),
        },
        "validation_pass": {
            "status": validation_pass.get("status"),
        },
        "quality_assessment": {
            "training_quality": _quality_gate_summary(quality_assessment, "training_quality"),
            "generalization_quality": _quality_gate_summary(quality_assessment, "generalization_quality"),
            "edge_usability": _quality_gate_summary(quality_assessment, "edge_usability"),
            "release_readiness": _quality_gate_summary(quality_assessment, "release_readiness"),
        },
    }


def _publishable_model_summary(row: Any) -> dict[str, Any]:
    data = _row_to_dict(row)
    validation_pass = data.get("validation_pass") or {}
    quality_assessment = data.get("quality_assessment") or {}
    edge_validation = data.get("edge_validation") or {}
    return {
        "export_id": data.get("export_id"),
        "export_created_at": data.get("export_created_at"),
        "map50_95": data.get("map50_95"),
        "validation_pass": {
            "status": validation_pass.get("status"),
        },
        "quality_assessment": {
            "edge_usability": _quality_gate_summary(quality_assessment, "edge_usability"),
            "release_readiness": _quality_gate_summary(quality_assessment, "release_readiness"),
        },
        "edge_validation": {
            "status": edge_validation.get("status"),
            "report_summary": edge_validation.get("report_summary"),
            "completed_at": edge_validation.get("completed_at"),
        },
        "publish_blocked_reason": data.get("publish_blocked_reason"),
    }


def _history_snapshot(db: Session, current_user: User, rect_preset_slug: str) -> dict[str, Any]:
    train_rows, _ = yolo_train_service.list_trained_models_global(
        db,
        current_user,
        task_name=None,
        rect_preset_slug=rect_preset_slug,
        page=0,
        size=10,
    )
    publish_rows, _ = yolo_aicm_publish_service.list_publishable(
        db,
        current_user,
        rect_preset_slug=rect_preset_slug,
        page=0,
        size=10,
    )
    return {
        "trained_models": [_trained_model_summary(row) for row in train_rows[:_TRAIN_SUMMARY_LIMIT]],
        "publishable_models": [_publishable_model_summary(row) for row in publish_rows[:_PUBLISH_SUMMARY_LIMIT]],
    }


def _preset_snapshot(db: Session, rect_preset_slug: str) -> dict[str, Any]:
    slug = str(rect_preset_slug or "").strip()
    if not slug or slug == "__default__":
        return {}
    preset = crud_annotation_taxonomy.label_preset_get_by_slug(db, slug)
    if preset is None:
        return {}
    items = sorted(list(preset.items or []), key=lambda x: (x.sort_order, x.id))
    return {
        "slug": slug,
        "name": str(preset.name or "").strip(),
        "description": str(preset.description or "").strip(),
        "labels": [
            {
                "label_key": str(item.label_key or "").strip(),
                "label_value": str(item.label_value or "").strip(),
            }
            for item in items
            if str(item.label_key or "").strip() or str(item.label_value or "").strip()
        ],
    }


def _label_snapshot(db: Session, label_ids: list[int]) -> dict[str, Any]:
    if not label_ids:
        return {}
    labels = annotation_contract_service.active_labels_by_ids(db, label_ids)
    return {
        "label_ids": [int(row.id) for row in labels],
        "labels": [
            {
                "label_id": int(row.id),
                "label_key": str(row.label_key or "").strip(),
                "label_value": str(row.label_value or "").strip(),
            }
            for row in labels
        ],
    }


def _default_recommendation(cmd: RecommendYoloQualityPolicyCommand) -> dict[str, Any]:
    binding = str(cmd.rect_preset_slug or "").strip() or (
        "labels-" + str(cmd.label_set_signature or "")[:12]
    )
    return {
        "policy_name": f"{binding}-ai-draft-v1",
        "scene_name": cmd.scene_name,
        "rect_preset_slug": cmd.rect_preset_slug,
        "label_ids": list(cmd.label_ids or []),
        "model_family": cmd.model_family,
        "annotation_kind": cmd.annotation_kind,
        "label_set_signature": cmd.label_set_signature,
        "status": "draft",
        "source": "ai_recommended",
        "version": 1,
        "source_summary": {
            "business_goal": cmd.business_goal or "默认兼顾召回、精度与边端可用性",
            "historical_reference_used": bool(cmd.include_historical_training),
            "edge_validation_reference_used": bool(cmd.include_edge_validation_history),
            "benchmark_name": cmd.benchmark_name or "",
        },
        "training_quality": {
            "map50_95_pass": 0.55,
            "map50_warn": 0.50,
            "min_total_samples": max(int(cmd.benchmark_image_count or 0), 20) if cmd.benchmark_image_count else 20,
            "allow_conditional_pass": True,
        },
        "generalization_quality": {
            "require_test_metrics": True,
            "test_map50_95_warn": 0.48,
            "max_gap_map50_95": 0.10,
            "max_gap_map50": 0.10,
        },
        "edge_usability": {
            "require_edge_validation": True,
            "require_eval_when_labeled_images_exist": True,
            "max_pt_to_bmodel_map50_drop": 0.02,
        },
        "release_readiness": {
            "block_publish_when_training_failed": True,
            "block_publish_when_edge_unverified": True,
            "allow_review_when_generalization_missing": True,
            "summary": "用于约束训练完成后的发布门禁，不代表当前模型已可发布",
        },
        "key_classes": [{"label": item, "min_ap50_95": 0.50} for item in cmd.key_classes[:10]],
        "reasons": ["基于当前场景、业务目标与历史结果生成默认 AI 草稿"],
        "risks": [
            "该结果只是场景质量标准草稿，不代表当前模型已达标或可发布",
        ],
    }


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    if "{" in raw and "}" in raw:
        candidates.append(raw[raw.find("{") : raw.rfind("}") + 1])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _normalize_key_classes(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("name") or "").strip()
            if not label:
                continue
            row = dict(item)
            row["label"] = label
            row.setdefault("min_ap50_95", 0.5)
            out.append(row)
            continue
        label = str(item or "").strip()
        if label:
            out.append({"label": label, "min_ap50_95": 0.5})
    return out


def _normalize_release_readiness(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if not text:
        return {}
    return {"summary": text}


def _coerce_float(value: Any, *, minimum: float | None = None, maximum: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def _coerce_int(value: Any, *, minimum: int | None = None) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if minimum is not None and number < minimum:
        return None
    return number


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "是", "需要", "开启", "启用"}:
        return True
    if text in {"false", "0", "no", "n", "off", "disable", "disabled", "否", "不需要", "关闭", "禁用"}:
        return False
    return None


def _normalized_section_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _first_float(raw: dict[str, Any], *keys: str, minimum: float | None = None, maximum: float | None = None) -> float | None:
    for key in keys:
        if key not in raw:
            continue
        number = _coerce_float(raw.get(key), minimum=minimum, maximum=maximum)
        if number is not None:
            return number
    return None


def _first_int(raw: dict[str, Any], *keys: str, minimum: int | None = None) -> int | None:
    for key in keys:
        if key not in raw:
            continue
        number = _coerce_int(raw.get(key), minimum=minimum)
        if number is not None:
            return number
    return None


def _first_bool(raw: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key not in raw:
            continue
        flag = _coerce_bool(raw.get(key))
        if flag is not None:
            return flag
    return None


def _normalize_source_summary(
    value: Any,
    *,
    fallback: dict[str, Any],
    cmd: RecommendYoloQualityPolicyCommand,
) -> dict[str, Any]:
    raw = _normalized_section_dict(value)
    summary = dict(fallback)
    summary["business_goal"] = str(
        cmd.business_goal or fallback.get("business_goal") or ""
    ).strip()
    summary["historical_reference_used"] = bool(cmd.include_historical_training)
    summary["edge_validation_reference_used"] = bool(cmd.include_edge_validation_history)
    summary["benchmark_name"] = str(cmd.benchmark_name or fallback.get("benchmark_name") or "").strip()
    note = str(raw.get("note") or raw.get("summary") or "").strip()
    if note and not _looks_like_final_verdict(note):
        summary["note"] = note
    return summary


def _normalize_training_quality_rules(value: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
    raw = _normalized_section_dict(value)
    rules = dict(fallback)
    pass_line = _first_float(
        raw,
        "map50_95_pass",
        "map50_95_threshold",
        "pass_threshold",
        "pass_line",
        "min_map50_95",
        "训练达标阈值",
        "达标阈值",
        minimum=0.0,
        maximum=1.0,
    )
    if pass_line is not None:
        rules["map50_95_pass"] = pass_line
    warn_line = _first_float(
        raw,
        "map50_warn",
        "map50_95_warn",
        "warn_threshold",
        "warn_line",
        "条件达标阈值",
        "预警阈值",
        minimum=0.0,
        maximum=1.0,
    )
    if warn_line is not None:
        rules["map50_warn"] = min(warn_line, float(rules.get("map50_95_pass", warn_line)))
    min_total_samples = _first_int(
        raw,
        "min_total_samples",
        "minimum_samples",
        "min_samples",
        "最小样本数",
        "最少样本数",
        minimum=1,
    )
    if min_total_samples is not None:
        rules["min_total_samples"] = min_total_samples
    allow_conditional_pass = _first_bool(
        raw,
        "allow_conditional_pass",
        "允许条件达标",
        "support_conditional_pass",
    )
    if allow_conditional_pass is not None:
        rules["allow_conditional_pass"] = allow_conditional_pass
    return {
        "map50_95_pass": float(rules.get("map50_95_pass", fallback.get("map50_95_pass", 0.55))),
        "map50_warn": float(rules.get("map50_warn", fallback.get("map50_warn", 0.5))),
        "min_total_samples": int(rules.get("min_total_samples", fallback.get("min_total_samples", 20))),
        "allow_conditional_pass": bool(
            rules.get("allow_conditional_pass", fallback.get("allow_conditional_pass", True))
        ),
    }


def _normalize_generalization_quality_rules(value: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
    raw = _normalized_section_dict(value)
    rules = dict(fallback)
    require_test_metrics = _first_bool(
        raw,
        "require_test_metrics",
        "需要test集验证",
        "need_test_metrics",
    )
    if require_test_metrics is not None:
        rules["require_test_metrics"] = require_test_metrics
    test_warn = _first_float(
        raw,
        "test_map50_95_warn",
        "test_map50_95_threshold",
        "test_warn_threshold",
        "test预警阈值",
        minimum=0.0,
        maximum=1.0,
    )
    if test_warn is not None:
        rules["test_map50_95_warn"] = test_warn
    gap_5095 = _first_float(
        raw,
        "max_gap_map50_95",
        "max_delta_map50_95",
        "允许最大波动_map50_95",
        minimum=0.0,
        maximum=1.0,
    )
    if gap_5095 is not None:
        rules["max_gap_map50_95"] = gap_5095
    gap_50 = _first_float(
        raw,
        "max_gap_map50",
        "max_delta_map50",
        "允许最大波动_map50",
        minimum=0.0,
        maximum=1.0,
    )
    if gap_50 is not None:
        rules["max_gap_map50"] = gap_50
    return {
        "require_test_metrics": bool(
            rules.get("require_test_metrics", fallback.get("require_test_metrics", True))
        ),
        "test_map50_95_warn": float(
            rules.get("test_map50_95_warn", fallback.get("test_map50_95_warn", 0.48))
        ),
        "max_gap_map50_95": float(
            rules.get("max_gap_map50_95", fallback.get("max_gap_map50_95", 0.1))
        ),
        "max_gap_map50": float(rules.get("max_gap_map50", fallback.get("max_gap_map50", 0.1))),
    }


def _normalize_edge_usability_rules(value: Any, *, fallback: dict[str, Any]) -> dict[str, Any]:
    raw = _normalized_section_dict(value)
    rules = dict(fallback)
    require_edge_validation = _first_bool(
        raw,
        "require_edge_validation",
        "需要边端验证",
        "need_edge_validation",
    )
    if require_edge_validation is not None:
        rules["require_edge_validation"] = require_edge_validation
    require_eval = _first_bool(
        raw,
        "require_eval_when_labeled_images_exist",
        "有标注图时需要精度评估",
        "need_eval_when_labeled_images_exist",
    )
    if require_eval is not None:
        rules["require_eval_when_labeled_images_exist"] = require_eval
    max_drop = _first_float(
        raw,
        "max_pt_to_bmodel_map50_drop",
        "max_pt_bmodel_gap",
        "量化精度下降阈值",
        minimum=0.0,
        maximum=1.0,
    )
    if max_drop is not None:
        rules["max_pt_to_bmodel_map50_drop"] = max_drop
    return {
        "require_edge_validation": bool(
            rules.get("require_edge_validation", fallback.get("require_edge_validation", True))
        ),
        "require_eval_when_labeled_images_exist": bool(
            rules.get(
                "require_eval_when_labeled_images_exist",
                fallback.get("require_eval_when_labeled_images_exist", True),
            )
        ),
        "max_pt_to_bmodel_map50_drop": float(
            rules.get("max_pt_to_bmodel_map50_drop", fallback.get("max_pt_to_bmodel_map50_drop", 0.02))
        ),
    }


def _allowed_key_class_labels(
    *,
    cmd: RecommendYoloQualityPolicyCommand,
    preset_snapshot: dict[str, Any],
) -> list[str]:
    labels: list[str] = []
    for item in cmd.key_classes or []:
        text = str(item or "").strip()
        if text and text not in labels:
            labels.append(text)
    for item in preset_snapshot.get("labels") or []:
        if not isinstance(item, dict):
            continue
        for key in ("label_value", "label_key"):
            text = str(item.get(key) or "").strip()
            if text and text not in labels:
                labels.append(text)
    return labels[:20]


def _looks_like_final_verdict(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    tokens = (
        "可发布",
        "不可发布",
        "需复核",
        "已达标",
        "未达标",
        "边端可用",
        "可用",
        "publish",
        "release-ready",
        "release ready",
        "ready for release",
    )
    return any(token in raw or token in lowered for token in tokens)


def _normalize_reasons(items: Any, *, default_reason: str) -> list[str]:
    if not isinstance(items, list):
        return [default_reason]
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        out.append(text)
    return out or [default_reason]


def _normalize_risks(items: Any) -> list[str]:
    if isinstance(items, str):
        items = [items]
    elif not isinstance(items, list):
        items = []
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        out.append(text)
    if not any("质量标准草稿" in text for text in out):
        out.append(
            "该结果只是场景质量标准草稿，当前模型是否可发布仍需等训练、test 集、边端与量化实测后再判定"
        )
    return out


def _normalize_release_readiness_rules(
    value: Any,
    *,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(fallback)
    raw = _normalize_release_readiness(value)
    for key in (
        "block_publish_when_training_failed",
        "block_publish_when_edge_unverified",
        "allow_review_when_generalization_missing",
    ):
        if key in raw:
            merged[key] = bool(raw.get(key))
    summary = str(raw.get("summary") or "").strip()
    if not summary or _looks_like_final_verdict(summary):
        summary = "用于约束训练完成后的发布门禁，不代表当前模型已可发布"
    merged["summary"] = summary
    return merged


def _normalize_ai_recommendation(
    parsed: dict[str, Any],
    *,
    fallback: dict[str, Any],
    cmd: RecommendYoloQualityPolicyCommand,
    preset_snapshot: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(fallback)
    merged["status"] = "draft"
    merged["source"] = "ai_recommended"
    merged["scene_name"] = cmd.scene_name
    merged["rect_preset_slug"] = cmd.rect_preset_slug
    merged["label_ids"] = list(cmd.label_ids or [])
    merged["model_family"] = cmd.model_family
    merged["annotation_kind"] = cmd.annotation_kind
    merged["label_set_signature"] = cmd.label_set_signature
    policy_name = str(parsed.get("policy_name") or "").strip()
    merged["policy_name"] = policy_name or fallback.get("policy_name")
    merged["source_summary"] = _normalize_source_summary(
        parsed.get("source_summary"),
        fallback=fallback.get("source_summary") or {},
        cmd=cmd,
    )
    merged["training_quality"] = _normalize_training_quality_rules(
        parsed.get("training_quality"),
        fallback=fallback.get("training_quality") or {},
    )
    merged["generalization_quality"] = _normalize_generalization_quality_rules(
        parsed.get("generalization_quality"),
        fallback=fallback.get("generalization_quality") or {},
    )
    merged["edge_usability"] = _normalize_edge_usability_rules(
        parsed.get("edge_usability"),
        fallback=fallback.get("edge_usability") or {},
    )
    allowed_labels = _allowed_key_class_labels(cmd=cmd, preset_snapshot=preset_snapshot)
    normalized_key_classes = _normalize_key_classes(parsed.get("key_classes"))
    if allowed_labels:
        normalized_key_classes = [
            item for item in normalized_key_classes if str(item.get("label") or "").strip() in allowed_labels
        ]
    if not normalized_key_classes and allowed_labels:
        normalized_key_classes = [{"label": label, "min_ap50_95": 0.5} for label in allowed_labels[:10]]
    merged["key_classes"] = normalized_key_classes
    merged["release_readiness"] = _normalize_release_readiness_rules(
        parsed.get("release_readiness"),
        fallback=fallback.get("release_readiness") or {},
    )
    merged["reasons"] = _normalize_reasons(
        parsed.get("reasons"),
        default_reason="该建议用于生成场景质量标准草稿，最终阈值仍需结合业务目标人工确认",
    )
    merged["risks"] = _normalize_risks(parsed.get("risks"))
    return merged


def _recommendation_context(
    *,
    cmd: RecommendYoloQualityPolicyCommand,
    history_snapshot: dict[str, Any],
    preset_snapshot: dict[str, Any],
) -> dict[str, Any]:
    return {
        "scene_name": cmd.scene_name,
        "rect_preset_slug": cmd.rect_preset_slug,
        "label_ids": cmd.label_ids,
        "label_set_signature": cmd.label_set_signature,
        "model_family": cmd.model_family,
        "annotation_kind": cmd.annotation_kind,
        "preset_snapshot": preset_snapshot,
        "benchmark_name": cmd.benchmark_name,
        "benchmark_image_count": cmd.benchmark_image_count,
        "benchmark_box_count": cmd.benchmark_box_count,
        "business_goal": cmd.business_goal,
        "key_classes": cmd.key_classes,
        "business_constraints": cmd.business_constraints,
        "history_snapshot": history_snapshot,
    }


def _recommendation_user_text(context: dict[str, Any]) -> str:
    return (
        "请为当前场景推荐一份模型质量标准草稿。"
        "返回 JSON，字段至少包含 policy_name、scene_name、"
        "training_quality、generalization_quality、edge_usability、release_readiness、"
        "key_classes、reasons、risks。"
        "\n请优先使用输入里已给出的场景名称、标签集合和关键类别作为标准对象，"
        "不要自行虚构英文类别或假定已经做过量化、test 集、边端推理实测。"
        "\nrelease_readiness 只能表示发布规则，不能表示当前模型已可发布。"
        "\n上下文：\n"
        + json.dumps(jsonable_encoder(context), ensure_ascii=False, indent=2)
    )


def recommend_quality_policy(
    db: Session,
    *,
    cmd: RecommendYoloQualityPolicyCommand,
    current_user: User,
) -> YoloQualityPolicyRecommendationResponse:
    label_snapshot = _label_snapshot(db, list(cmd.label_ids or []))
    if label_snapshot:
        resolved_ids = label_snapshot["label_ids"]
        resolved_key_classes = list(cmd.key_classes or []) or [
            str(item["label_value"]) for item in label_snapshot["labels"]
        ]
        cmd = cmd.model_copy(
            update={
                "label_ids": resolved_ids,
                "label_set_signature": label_set_signature(resolved_ids),
                "key_classes": resolved_key_classes,
                "rect_preset_slug": "",
            }
        )
    elif not str(cmd.rect_preset_slug or "").strip():
        raise LabelUException(
            code=ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            message="AI 推荐必须选择标签集合；rect_preset_slug 仅用于历史兼容",
        )
    fallback = _default_recommendation(cmd)
    default_rec = YoloQualityPolicyResponse(**fallback, is_active=False)
    providers = _candidate_providers()
    provider_key: str | None = None
    fallback_reason = ""

    preset_snapshot = label_snapshot or _preset_snapshot(db, cmd.rect_preset_slug)
    history_snapshot = _history_snapshot(db, current_user, cmd.rect_preset_slug)
    context = _recommendation_context(
        cmd=cmd,
        history_snapshot=history_snapshot,
        preset_snapshot=preset_snapshot,
    )
    user_text = _recommendation_user_text(context)

    last_error: Exception | None = None
    for provider in providers:
        provider_key = provider["provider_key"]
        try:
            text = chat_multimodal_markdown(
                base_url=provider["base_url"],
                api_key=provider["api_key"],
                model=provider["model"],
                user_text=user_text,
                images_jpeg=[],
                system_prompt=_SYSTEM_PROMPT,
                temperature=0.2,
                max_tokens=4096,
                timeout=_recommend_timeout_seconds(),
            )
        except Exception as exc:
            last_error = exc
            logger.warning(
                "quality policy ai call failed provider={} model={} error={}",
                provider["base_url"],
                provider["model"],
                str(exc)[:500],
            )
            continue

        parsed = _extract_json(text)
        if not isinstance(parsed, dict):
            logger.warning(
                "quality policy ai returned non-json model={} text={}",
                provider["model"],
                (text or "")[:1000],
            )
            continue

        try:
            merged = _normalize_ai_recommendation(
                parsed,
                fallback=fallback,
                cmd=cmd,
                preset_snapshot=preset_snapshot,
            )
            rec = YoloQualityPolicyResponse(**merged, is_active=False)
        except ValidationError as exc:
            last_error = exc
            logger.warning(
                "quality policy ai payload validation failed model={} errors={} payload={}",
                provider["model"],
                exc.errors(),
                json.dumps(parsed, ensure_ascii=False)[:2000],
            )
            continue

        return YoloQualityPolicyRecommendationResponse(
            recommendation=rec,
            default_recommendation=default_rec,
            fallback_used=False,
            provider_key=provider_key,
            fallback_reason="",
        )

    fallback_reason = _fallback_reason(providers, error=last_error)
    return YoloQualityPolicyRecommendationResponse(
        recommendation=default_rec,
        default_recommendation=default_rec,
        fallback_used=True,
        provider_key=provider_key,
        fallback_reason=fallback_reason,
    )
