from __future__ import annotations

from typing import Any

from fastapi import status

from labelu.internal.application.command.ai_labeling_llm_profile import (
    CreateLlmProfileCommand,
    PatchAnnotationChainCommand,
    UpdateLlmProfileCommand,
)
from labelu.internal.application.response.ai_labeling_llm_profile import (
    LlmProfilePublic,
    LlmProfilesBundleResponse,
    YoloEvalPromptsPublicResponse,
)
from labelu.internal.common.ai_labeling_runtime import (
    API_KEY_KEEP_SENTINEL,
    load_ai_labeling_runtime,
    mask_api_key,
    new_profile_id,
    runtime_file_path,
    save_ai_labeling_runtime,
)
from labelu.internal.common.error_code import ErrorCode, LabelUException
from labelu.internal.common.yolo_eval_prompts import (
    load_yolo_eval_prompts,
    prompts_file_path,
    save_yolo_eval_prompts,
)

_PROFILE_ID_RE_STR = "仅允许字母、数字、下划线、横线，长度 1～64"


def _to_public(p: dict[str, Any]) -> LlmProfilePublic:
    configured, masked = mask_api_key(p.get("api_key"))
    return LlmProfilePublic(
        id=p["id"],
        name=p["name"],
        base_url=p["base_url"],
        model=p["model"],
        api_key_configured=configured,
        api_key_masked=masked,
    )


def list_llm_profiles_bundle() -> LlmProfilesBundleResponse:
    ov = load_ai_labeling_runtime()
    profs = [_to_public(p) for p in ov.get("llm_profiles") or []]
    return LlmProfilesBundleResponse(
        profiles=profs,
        annotation_chain_profile_ids=list(ov.get("annotation_chain_profile_ids") or []),
        config_backend_label="MySQL 治理表（system_config_*）",
        runtime_file=str(runtime_file_path().resolve()),
    )


def create_llm_profile(cmd: CreateLlmProfileCommand) -> LlmProfilesBundleResponse:
    from labelu.internal.common.ai_labeling_runtime import PROFILE_ID_RE

    ov = load_ai_labeling_runtime()
    profiles: list[dict[str, Any]] = list(ov.get("llm_profiles") or [])
    pid = (cmd.id or "").strip()
    if pid:
        if not _PROFILE_ID_RE.match(pid):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message=f"档案 ID 格式无效：{_PROFILE_ID_RE_STR}",
            )
        if any(x["id"] == pid for x in profiles):
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_400_BAD_REQUEST,
                message="该档案 ID 已存在",
            )
    else:
        for _ in range(50):
            cand = new_profile_id()
            if not any(x["id"] == cand for x in profiles):
                pid = cand
                break
        else:
            raise LabelUException(
                code=ErrorCode.CODE_30002_VALIDATION_ERROR,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                message="无法生成唯一档案 ID，请稍后重试",
            )

    profiles.append(
        {
            "id": pid,
            "name": cmd.name.strip(),
            "base_url": cmd.base_url.strip(),
            "model": cmd.model.strip(),
            "api_key": cmd.api_key.strip(),
        }
    )
    save_ai_labeling_runtime(llm_profiles=profiles)
    return list_llm_profiles_bundle()


def update_llm_profile(profile_id: str, cmd: UpdateLlmProfileCommand) -> LlmProfilesBundleResponse:
    ov = load_ai_labeling_runtime()
    profiles: list[dict[str, Any]] = [dict(x) for x in ov.get("llm_profiles") or []]
    idx = next((i for i, x in enumerate(profiles) if x["id"] == profile_id), -1)
    if idx < 0:
        raise LabelUException(
            code=ErrorCode.CODE_30003_CLIENT_ERROR,
            status_code=status.HTTP_404_NOT_FOUND,
            message="模型档案不存在",
        )
    row = profiles[idx]
    if cmd.name is not None:
        row["name"] = cmd.name.strip()[:200] or row["name"]
    if cmd.base_url is not None:
        row["base_url"] = cmd.base_url.strip()[:500]
    if cmd.model is not None:
        row["model"] = cmd.model.strip()[:300]
    if cmd.api_key is not None:
        ak = cmd.api_key.strip()
        if ak and ak != API_KEY_KEEP_SENTINEL:
            row["api_key"] = ak[:2000]
    profiles[idx] = row
    save_ai_labeling_runtime(llm_profiles=profiles)
    return list_llm_profiles_bundle()


def delete_llm_profile(profile_id: str) -> LlmProfilesBundleResponse:
    ov = load_ai_labeling_runtime()
    profiles = [x for x in ov.get("llm_profiles") or [] if x["id"] != profile_id]
    chain = [x for x in ov.get("annotation_chain_profile_ids") or [] if x != profile_id]
    save_ai_labeling_runtime(llm_profiles=profiles, annotation_chain_profile_ids=chain)
    return list_llm_profiles_bundle()


def patch_annotation_chain(cmd: PatchAnnotationChainCommand) -> LlmProfilesBundleResponse:
    save_ai_labeling_runtime(annotation_chain_profile_ids=cmd.profile_ids)
    return list_llm_profiles_bundle()


def get_yolo_eval_prompts_public() -> YoloEvalPromptsPublicResponse:
    p = load_yolo_eval_prompts()
    return YoloEvalPromptsPublicResponse(
        full_analysis=p.get("full_analysis") or "",
        next_iteration=p.get("next_iteration") or "",
        config_backend_label="MySQL 治理表（system_config_*）",
        prompts_file=str(prompts_file_path().resolve()),
    )


def patch_yolo_eval_prompts(
    *,
    full_analysis: str | None,
    next_iteration: str | None,
) -> YoloEvalPromptsPublicResponse:
    if full_analysis is not None or next_iteration is not None:
        save_yolo_eval_prompts(
            full_analysis=full_analysis,
            next_iteration=next_iteration,
        )
    return get_yolo_eval_prompts_public()
