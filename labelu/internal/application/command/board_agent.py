from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BoardAgentReportCommand(BaseModel):
    status: str = Field(..., description="COMPLETED 或 FAILED")
    report: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class BoardAgentProgressCommand(BaseModel):
    stage: str = Field(..., description="阶段标识，如 downloading / bmrt_test / infer")
    message: str = Field(..., description="给人看的简短说明")
    detail: Optional[Dict[str, Any]] = Field(default=None, description="可选结构化信息")


class BoardAgentHeartbeatCommand(BaseModel):
    environment_ready: bool = Field(default=False, description="板端运行时是否已就绪")
    capabilities: Optional[Dict[str, Any]] = Field(default=None, description="板端能力摘要")
    message: Optional[str] = Field(default=None, max_length=800, description="板端状态说明")
