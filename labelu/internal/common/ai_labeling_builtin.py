"""内置视觉链路（.env）可用性与实际调用顺序解析。"""

from __future__ import annotations

from typing import Any

from labelu.internal.common import model_access_config
from labelu.internal.common.ai_labeling_runtime import is_qwen_visual_provider
from labelu.internal.common.config import settings
from labelu.internal.common.model_access_config import (
    validate_volcengine_coding_endpoint,
)
from labelu.internal.common.vision_volcengine import normalize_volcengine_ark_model_id

BUILTIN_ORDER = ("volcengine",)

BUILTIN_META: dict[str, dict[str, str]] = {
    "volcengine": {
        "display_name": "火山方舟",
        "env_key_var": "VISION_VOLCENGINE_API_KEY",
    },
}


def _volcengine_model() -> str:
    return normalize_volcengine_ark_model_id(settings.VISION_VOLCENGINE_MODEL)


def _volcengine_key_configured() -> bool:
    return bool((settings.VISION_VOLCENGINE_API_KEY or "").strip())


def list_builtin_env_providers(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    """返回 legacy 火山 Coding Plan 的配置与勾选状态。"""
    enabled = set(runtime.get("enabled_providers") or list(BUILTIN_ORDER))
    out: list[dict[str, Any]] = []

    ve_model = _volcengine_model()
    ve_key = _volcengine_key_configured()
    try:
        ve_base = validate_volcengine_coding_endpoint(
            settings.VISION_VOLCENGINE_BASE_URL
        )
        valid_endpoint = True
    except ValueError:
        ve_base = ""
        valid_endpoint = False
    ve_configured = (
        ve_key
        and bool(ve_model)
        and valid_endpoint
        and not is_qwen_visual_provider(ve_base, ve_model)
    )
    out.append(
        {
            "key": "volcengine",
            "display_name": BUILTIN_META["volcengine"]["display_name"],
            "env_key_var": BUILTIN_META["volcengine"]["env_key_var"],
            "configured": ve_configured,
            "api_key_present": ve_key,
            "enabled": "volcengine" in enabled,
            "usable": ve_configured and "volcengine" in enabled,
            "model_id": ve_model or "",
            "base_url": ve_base,
            "missing_reason": (
                ""
                if ve_configured
                else (
                    "服务地址必须为火山 Coding Plan /api/coding/v3"
                    if not valid_endpoint
                    else (
                        "未配置 API Key（.env 中 VISION_VOLCENGINE_API_KEY）"
                        if not ve_key
                        else "未配置模型 ID"
                    )
                )
            ),
        }
    )

    return out


def resolve_effective_vision_call_plan(runtime: dict[str, Any]) -> dict[str, Any]:
    """与 vision_llm_chain.call_vision_llm_chain 一致的调用顺序预览。"""
    managed_ark = any(
        row.get("provider_key") == "volcengine"
        for row in model_access_config.load_store().get("connections", [])
    )
    if managed_ark:
        try:
            configured = model_access_config.resolve_model_integration(
                "volcengine", capability="vision_reasoning"
            )
        except TypeError:
            configured = model_access_config.resolve_model_integration("volcengine")
        if configured and is_qwen_visual_provider(
            configured.get("endpoint") or "",
            configured.get("model") or "",
            configured.get("name") or "",
        ):
            configured = None
        if configured:
            try:
                base_url = validate_volcengine_coding_endpoint(
                    configured.get("endpoint") or ""
                )
            except ValueError:
                configured = None
        if not configured:
            return {
                "mode": "none",
                "steps": [],
                "hint": "页面火山模型已配置但已禁用、未通过检查或不是 Coding Plan 地址，请到系统管理检查。",
            }
        return {
            "mode": "managed",
            "steps": [
                {
                    "kind": "managed",
                    "provider_key": "volcengine",
                    "label": configured.get("name") or "火山 Coding Plan",
                    "model": normalize_volcengine_ark_model_id(
                        configured.get("model") or ""
                    ),
                    "base_url": base_url,
                    "configured": True,
                }
            ],
            "hint": "使用系统管理的火山 Coding Plan 配置，不回退历史模型链。",
        }
    profiles_by_id = {p["id"]: p for p in runtime.get("llm_profiles") or []}
    chain_ids = runtime.get("annotation_chain_profile_ids") or []
    steps: list[dict[str, Any]] = []

    for pid in chain_ids:
        p = profiles_by_id.get(str(pid).strip())
        if not p:
            continue
        key = (p.get("api_key") or "").strip()
        base = (p.get("base_url") or "").strip()
        mname = normalize_volcengine_ark_model_id((p.get("model") or "").strip())
        label = (p.get("name") or "").strip() or pid
        if is_qwen_visual_provider(base, mname, label):
            continue
        if "volces.com" in base.lower():
            try:
                base = validate_volcengine_coding_endpoint(base)
            except ValueError:
                continue
        if key and base and mname:
            steps.append(
                {
                    "kind": "profile",
                    "provider_key": str(pid),
                    "label": label,
                    "model": mname,
                    "base_url": base,
                    "configured": True,
                }
            )

    if steps:
        return {
            "mode": "profiles",
            "steps": steps,
            "hint": "已配置档案调用顺序：仅按下列档案依次调用，不使用 .env 内置链路。",
        }

    for item in list_builtin_env_providers(runtime):
        if item["usable"]:
            steps.append(
                {
                    "kind": "builtin",
                    "provider_key": item["key"],
                    "label": item["display_name"],
                    "model": item["model_id"],
                    "base_url": item["base_url"],
                    "configured": item["configured"],
                }
            )

    if steps:
        return {
            "mode": "builtin_env",
            "steps": steps,
            "hint": "未配置档案顺序：按 .env 内置链路勾选顺序降级调用（仅包含已配置密钥且已勾选的项）。",
        }

    configured_any = any(x["configured"] for x in list_builtin_env_providers(runtime))
    hint = (
        "未检测到可用视觉模型：请在系统管理配置火山 Coding Plan 并完成连通性检查。"
        if not configured_any
        else "内置模型已在 .env 配置，但当前均未勾选进调用顺序；请勾选至少一项或配置档案顺序。"
    )
    return {"mode": "none", "steps": [], "hint": hint}
