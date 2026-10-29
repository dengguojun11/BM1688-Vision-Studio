from typing import Optional

from pydantic import BaseModel, Field


class PatchYoloEvalPromptsCommand(BaseModel):
    full_analysis: Optional[str] = Field(default=None, description="全维度分析报告提示词模板")
    next_iteration: Optional[str] = Field(default=None, description="下一轮迭代建议提示词模板")
