from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class BoardAgentJobResponse(BaseModel):
    validation_id: int
    export_id: int
    task_id: int
    yolo_train_job_id: int
    status: str
    imgsz: int = 640
    bmodel_files: List[str] = Field(default_factory=list)
    models_zip_url: str = ""
    report_url: str = ""
    progress_url: str = ""
    agent_id: Optional[str] = None
    task_token: Optional[str] = None
    protocol_version: str = "legacy"
    threshold_contract: Dict[str, Any] = Field(
        default_factory=dict,
        description="本次板端验证使用的 validation 阈值快照，单位固定为 ratio_0_1",
    )


class BoardValidationProgressResponse(BaseModel):
    stage: str
    message: str
    detail: Optional[Dict[str, Any]] = None
    updated_at: Optional[str] = None
    history: List[Dict[str, Any]] = Field(default_factory=list)


class BoardValidationStatusResponse(BaseModel):
    validation_id: int
    export_id: int
    status: str
    error_message: Optional[str] = None
    workflow_stage: str = "edge_validation"
    edge_result_status: str = "unknown"
    threshold_contract: Dict[str, Any] = Field(
        default_factory=dict,
        description="本次板端任务下发的阈值契约快照，单位固定为 ratio_0_1",
    )
    report_summary: Optional[str] = None
    execution_device_host: Optional[str] = Field(
        default=None,
        description="本次边端验证实际执行设备 IP；普通成员响应中隐藏",
    )
    completed_at: Optional[datetime] = None
    progress: Optional[BoardValidationProgressResponse] = Field(
        default=None,
        description="PROCESSING 时板端上报的实时进度",
    )
    report: Optional[Dict[str, Any]] = Field(
        default=None,
        description="算能盒 Agent 回传的完整 JSON（bmrt_test / 推理步骤等）",
    )
    train_board_comparison: Optional[Dict[str, Any]] = Field(
        default=None,
        description="训练质量报告 vs 板端验证对照表（导出详情页展示）",
    )
    agent_id: Optional[str] = None
    task_token: Optional[str] = None
