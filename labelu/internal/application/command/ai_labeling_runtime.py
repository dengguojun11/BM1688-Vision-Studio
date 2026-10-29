from typing import List, Optional

from pydantic import BaseModel, Field


class PatchAiLabelingRuntimeCommand(BaseModel):
    instruction_suffix: Optional[str] = Field(default=None, description="附加在系统提示后的说明，可为空")
    enabled_providers: Optional[List[str]] = Field(
        default=None,
        description="兼容内置视觉链路启用项：volcengine；系统管理的火山 Coding Plan 配置优先",
    )
