from typing import List, Optional

from pydantic import BaseModel, Field


class CreateLlmProfileCommand(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="展示名称")
    base_url: str = Field(..., min_length=8, max_length=500, description="OpenAI 兼容 Chat Completions 根地址")
    model: str = Field(..., min_length=1, max_length=300, description="模型名或接入点 ID")
    api_key: str = Field(..., min_length=1, max_length=2000, description="API Key")
    id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="可选自定义 ID（字母数字下划线横线）；不传则自动生成",
    )


class UpdateLlmProfileCommand(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=300)
    api_key: Optional[str] = Field(
        default=None,
        max_length=2000,
        description="留空或传 __KEEP__ 表示不修改原密钥",
    )


class PatchAnnotationChainCommand(BaseModel):
    profile_ids: List[str] = Field(
        default_factory=list,
        description="兼容视觉模型档案顺序；系统管理的火山 Coding Plan 配置优先，千问档案不再参与识别",
    )


class PatchYoloEvalPromptsBody(BaseModel):
    full_analysis: Optional[str] = Field(default=None, max_length=100000)
    next_iteration: Optional[str] = Field(default=None, max_length=100000)
