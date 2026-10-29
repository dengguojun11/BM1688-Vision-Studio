from typing import List

from pydantic import BaseModel, Field


class PromptCompositionStep(BaseModel):
    order: int
    title: str
    description: str
    source: str = Field(description="代码位置或存储位置")


class AiLabelingInstructionTemplateResponse(BaseModel):
    base_template: str = Field(
        description="主提示词标准模版（参考样例 payload 生成，不含站点附加与单次请求说明）"
    )
    site_suffix_header: str = Field(
        default="【站点附加提示（可在 /ai-labeling-settings 页面修改）】",
        description="站点附加段在合成提示中的标题行",
    )
    source_location: str = Field(
        default="labelu/internal/application/service/vision_llm_chain.py → _build_instruction",
    )
    reference_note: str = Field(
        default=(
            "以下为固定参考样例（rectTool + tagTool、1920×1080）；"
            "实际标注时 labels、tag_dimensions、宽高与 allowed_tools 均来自当前任务配置。"
        ),
    )
    reference_image_size: str = Field(default="1920 x 1080")
    site_suffix_current: str = Field(
        default="",
        description="当前已保存的站点附加提示词（主存 MySQL 治理表）",
    )
    composed_with_site_suffix: str = Field(
        default="",
        description="参考样例主提示 + 当前站点附加段（与真实下发 instruction 结构一致）",
    )
    composition_steps: List[PromptCompositionStep] = Field(
        default_factory=list,
        description="从任务到模型的提示词合成步骤说明",
    )
    reference_payload_json: str = Field(
        default="",
        description="生成 base_template 所用的参考 payload（JSON）",
    )
