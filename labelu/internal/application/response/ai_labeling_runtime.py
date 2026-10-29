from typing import List

from pydantic import BaseModel, Field


class BuiltinEnvProviderItem(BaseModel):
    key: str = Field(description="volcengine")
    display_name: str = ""
    env_key_var: str = Field(description="对应 .env 中的密钥变量名")
    configured: bool = Field(
        description="密钥与模型 ID 均已配置，具备调用条件",
    )
    api_key_present: bool = False
    enabled: bool = Field(description="是否在治理表 ai_labeling_runtime.enabled_providers 中勾选")
    usable: bool = Field(description="configured 且 enabled；没有系统管理配置或档案链路时可用于兼容回退")
    model_id: str = ""
    base_url: str = ""
    missing_reason: str = Field(default="", description="未 configured 时的原因说明")


class VisionCallPlanStep(BaseModel):
    kind: str = Field(description="managed | profile | builtin")
    provider_key: str = ""
    label: str = ""
    model: str = ""
    base_url: str = ""
    configured: bool = True


class EffectiveVisionCallPlan(BaseModel):
    mode: str = Field(description="managed | profiles | builtin_env | none")
    steps: List[VisionCallPlanStep] = Field(default_factory=list)
    hint: str = ""


class AiLabelingRuntimeSecretsStatus(BaseModel):
    volcengine_configured: bool = False
    qwen_configured: bool = Field(default=False, description="兼容字段；千问视觉链路已停用，固定为 false")
    local_endpoint_configured: bool = False


class AiLabelingRuntimeModels(BaseModel):
    volcengine_model: str = ""
    qwen_plus: str = Field(default="", description="兼容字段；千问视觉链路已停用，固定为空")
    qwen_235b: str = Field(default="", description="兼容字段；千问视觉链路已停用，固定为空")
    local_endpoint: str = ""


class AiLabelingRuntimeResponse(BaseModel):
    instruction_suffix: str = ""
    enabled_providers: List[str] = Field(default_factory=list)
    annotation_chain_profile_ids: List[str] = Field(
        default_factory=list,
        description="兼容档案调用顺序；系统管理的火山 Coding Plan 配置优先，千问档案不再参与视觉识别",
    )
    llm_profile_count: int = Field(default=0, description="已保存的可配置模型档案数量")
    ai_auto_label_enabled: bool = False
    ai_provider: str = ""
    secrets: AiLabelingRuntimeSecretsStatus = Field(default_factory=AiLabelingRuntimeSecretsStatus)
    models: AiLabelingRuntimeModels = Field(default_factory=AiLabelingRuntimeModels)
    config_backend_label: str = Field(
        default="MySQL 治理表（system_config_*）",
        description="当前配置主读写来源",
    )
    runtime_file: str = Field(description="兼容同步文件路径（非主读写来源）")
    prompt_main_location: str = Field(
        default="labelu/internal/application/service/vision_llm_chain.py → _build_instruction",
        description="主提示词源码位置",
    )
    secrets_doc: str = Field(
        default="在系统管理的模型接入配置中维护 Coding Plan，API Key 加密保存且不回显明文",
    )
    builtin_providers: List[BuiltinEnvProviderItem] = Field(
        default_factory=list,
        description=".env 内置视觉链路各项的真实配置与勾选状态",
    )
    effective_call_plan: EffectiveVisionCallPlan = Field(
        default_factory=EffectiveVisionCallPlan,
        description="与 call_vision_llm_chain 一致的实际调用顺序预览",
    )
