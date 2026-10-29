from typing import List

from pydantic import BaseModel, Field


class LlmProfilePublic(BaseModel):
    id: str
    name: str
    base_url: str
    model: str
    api_key_configured: bool = False
    api_key_masked: str = Field(default="", description="脱敏预览，不含完整密钥")


class LlmProfilesBundleResponse(BaseModel):
    profiles: List[LlmProfilePublic] = Field(default_factory=list)
    annotation_chain_profile_ids: List[str] = Field(default_factory=list)
    config_backend_label: str = Field(
        default="MySQL 治理表（system_config_*）",
        description="当前配置主读写来源",
    )
    runtime_file: str = Field(description="兼容同步文件路径（非主读写来源）")


class YoloEvalPromptsPublicResponse(BaseModel):
    full_analysis: str = ""
    next_iteration: str = ""
    config_backend_label: str = Field(
        default="MySQL 治理表（system_config_*）",
        description="当前配置主读写来源",
    )
    prompts_file: str = Field(default="", description="兼容同步文件路径（非主读写来源）")
    placeholders_help: str = Field(
        default=(
            "模板中可使用占位符，保存后在下一次 YOLO AI 评估时由系统自动替换：\n"
            "• {{WEIGHTS_PATH}} — best.pt 绝对路径\n"
            "• {{METRICS_SUMMARY}} — 训练任务 metrics 摘要\n"
            "• {{ARGS_YAML}} — 训练目录下 args.yaml（服务端读取，有长度上限）\n"
            "• {{RESULTS_TAIL}} — results.csv 末尾若干行\n"
            "• {{PRIOR_REPORTS}} — 仅「下一轮迭代」报告：拼接历史「全维度分析」摘要"
        ),
    )
