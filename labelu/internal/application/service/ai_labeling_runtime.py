import json

from labelu.internal.application.command.ai_labeling_runtime import PatchAiLabelingRuntimeCommand
from labelu.internal.application.response.ai_labeling_instruction_template import (
    AiLabelingInstructionTemplateResponse,
)
from labelu.internal.application.response.ai_labeling_runtime import (
    AiLabelingRuntimeModels,
    AiLabelingRuntimeResponse,
    AiLabelingRuntimeSecretsStatus,
    BuiltinEnvProviderItem,
    EffectiveVisionCallPlan,
    VisionCallPlanStep,
)
from labelu.internal.application.response.ai_labeling_instruction_template import (
    PromptCompositionStep,
)
from labelu.internal.application.service.vision_llm_chain import (
    INSTRUCTION_TEMPLATE_REFERENCE_PAYLOAD,
    SITE_SUFFIX_HEADER,
    get_instruction_base_template_reference,
)
from labelu.internal.common.ai_labeling_builtin import (
    list_builtin_env_providers,
    resolve_effective_vision_call_plan,
)
from labelu.internal.common.ai_labeling_runtime import (
    load_ai_labeling_runtime,
    runtime_file_path,
    save_ai_labeling_runtime,
)
from labelu.internal.common.config import settings
from labelu.internal.common.vision_volcengine import normalize_volcengine_ark_model_id


def get_ai_labeling_runtime_view() -> AiLabelingRuntimeResponse:
    ov = load_ai_labeling_runtime()
    profiles = ov.get("llm_profiles") or []
    chain = ov.get("annotation_chain_profile_ids") or []
    legacy_path = str(runtime_file_path().resolve())
    call_plan = resolve_effective_vision_call_plan(ov)
    volcengine_step = next(
        (
            step for step in call_plan.get("steps", [])
            if step.get("provider_key") == "volcengine" and step.get("configured")
        ),
        None,
    )
    return AiLabelingRuntimeResponse(
        instruction_suffix=ov["instruction_suffix"],
        enabled_providers=ov["enabled_providers"],
        annotation_chain_profile_ids=list(chain),
        llm_profile_count=len(profiles),
        ai_auto_label_enabled=settings.AI_AUTO_LABEL_ENABLED,
        ai_provider=settings.AI_PROVIDER,
        secrets=AiLabelingRuntimeSecretsStatus(
            volcengine_configured=bool(volcengine_step),
            qwen_configured=False,
            local_endpoint_configured=bool((settings.AI_MODEL_ENDPOINT or "").strip()),
        ),
        models=AiLabelingRuntimeModels(
            volcengine_model=normalize_volcengine_ark_model_id(
                str(volcengine_step.get("model") or "") if volcengine_step else ""
            ),
            qwen_plus="",
            qwen_235b="",
            local_endpoint=settings.AI_MODEL_ENDPOINT or "",
        ),
        config_backend_label="MySQL 治理表（system_config_*）",
        runtime_file=legacy_path,
        secrets_doc=(
            "火山 Coding Plan 优先使用「系统管理→模型接入」中已启用并验证的配置，"
            "密钥加密保存在治理表。千问视觉链路已停用。"
        ),
        builtin_providers=[
            BuiltinEnvProviderItem(**item) for item in list_builtin_env_providers(ov)
        ],
        effective_call_plan=EffectiveVisionCallPlan(
            **call_plan
        ),
    )


def get_instruction_template_view() -> AiLabelingInstructionTemplateResponse:
    ov = load_ai_labeling_runtime()
    base = get_instruction_base_template_reference()
    suf = (ov.get("instruction_suffix") or "").strip()
    composed = base
    if suf:
        composed = f"{base}\n\n{SITE_SUFFIX_HEADER}\n{suf}\n"
    return AiLabelingInstructionTemplateResponse(
        base_template=base,
        site_suffix_header=SITE_SUFFIX_HEADER,
        site_suffix_current=suf,
        composed_with_site_suffix=composed,
        reference_payload_json=json.dumps(
            INSTRUCTION_TEMPLATE_REFERENCE_PAYLOAD, ensure_ascii=False, indent=2
        ),
        composition_steps=[
            PromptCompositionStep(
                order=1,
                title="主提示（按任务动态生成）",
                description=(
                    "根据样本图片尺寸、任务 config 中的几何标签、全局维度 tag_dimensions、"
                    "allowed_tools 与 filter_by_labels 拼出系统 instruction。"
                ),
                source="labelu/internal/application/service/auto_label.py → _build_inference_payload；"
                "labelu/internal/application/service/vision_llm_chain.py → _build_instruction",
            ),
            PromptCompositionStep(
                order=2,
                title="站点附加提示词",
                description=(
                    "若治理表中的 ai_labeling_runtime.instruction_suffix 非空，"
                    f"在其后追加标题行「{SITE_SUFFIX_HEADER}」及正文。"
                ),
                source="system_config_value(ai_labeling_runtime.instruction_suffix)"
                + f" → 兼容同步 {str(runtime_file_path().resolve())}",
            ),
            PromptCompositionStep(
                order=3,
                title="单次请求附加说明（可选）",
                description="AI 预标注接口请求体中的 prompt 字段，仅当次调用追加。",
                source="AutoLabelCommand.prompt / model_payload.prompt",
            ),
            PromptCompositionStep(
                order=4,
                title="多模态请求",
                description="上述 instruction 与 JPEG 图片一并送入 OpenAI 兼容 /chat/completions。",
                source="vision_llm_chain.call_vision_llm_chain",
            ),
        ],
    )


def patch_ai_labeling_runtime(cmd: PatchAiLabelingRuntimeCommand) -> AiLabelingRuntimeResponse:
    if cmd.instruction_suffix is not None or cmd.enabled_providers is not None:
        save_ai_labeling_runtime(
            instruction_suffix=cmd.instruction_suffix,
            enabled_providers=cmd.enabled_providers,
        )
    return get_ai_labeling_runtime_view()
