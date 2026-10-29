from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class YoloEvalReportListRowResponse(BaseModel):
    """全局评估报告列表（不含正文，点击查看详情）"""

    id: int
    yolo_train_job_id: int
    task_id: int
    task_name: Optional[str] = None
    provider_key: str
    report_kind: str
    operation_id: Optional[str] = None
    title: str
    summary_level: str = "unknown"
    summary_brief: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None


class YoloEvalReportResponse(BaseModel):
    id: int
    yolo_train_job_id: int
    task_id: Optional[int] = None
    task_name: Optional[str] = None
    provider_key: str
    report_kind: str
    operation_id: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    suggested_params: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None


class YoloEvalPromptsResponse(BaseModel):
    full_analysis: str
    next_iteration: str
    config_backend_label: str = "MySQL 治理表（system_config_*）"
    prompts_file: str = ""


class YoloEvalAcceptedResponse(BaseModel):
    accepted: bool
    report_kind: str
    providers: list[str]
    operation_id: Optional[str] = None


class YoloEvalHubReportItem(BaseModel):
    """全链路报告目录项（正文通过 fetch_url 或 ai_report_id 按需加载）。"""

    key: str
    group: str
    subgroup: str
    title: str
    description: Optional[str] = None
    status: str = "ok"
    format: str = "markdown"
    created_at: Optional[datetime] = None
    export_id: Optional[int] = None
    ai_report_id: Optional[int] = None
    provider_key: Optional[str] = None
    report_kind: Optional[str] = None
    sop_kind: Optional[str] = None
    metrics: Optional[Dict[str, Any]] = None
    preview: Optional[str] = None
    fetch_url: Optional[str] = None
    assets: Optional[List[str]] = None


class YoloEvalHubCompareGroup(BaseModel):
    id: str
    title: str
    description: str
    report_keys: List[str]
    layout: str = "columns"


class YoloEvalHubResponse(BaseModel):
    task_id: int
    task_name: str
    job_id: int
    job_status: str
    ai_eval_full_status: Optional[str] = None
    ai_eval_next_status: Optional[str] = None
    ai_eval_full_operation_id: Optional[str] = None
    ai_eval_next_operation_id: Optional[str] = None
    train_metrics: Optional[Dict[str, Any]] = None
    test_metrics: Optional[Dict[str, Any]] = None
    reports: List[YoloEvalHubReportItem]
    compare_groups: List[YoloEvalHubCompareGroup]
    focus_export_id: Optional[int] = None
