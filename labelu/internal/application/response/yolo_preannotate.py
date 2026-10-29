from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from labelu.internal.application.response.yolo_train import YoloTrainingValMetrics


class YoloPreannotateCandidateResponse(BaseModel):
    kind: str = Field(description="imported | trained")
    id: int
    title: str
    detail: str = ""
    category: str = Field(
        default="",
        description="用于筛选：导入模型为上传时填写的分类（空则未分类）；训练权重为「本系统训练」",
    )
    imgsz: Optional[int] = None
    labels: List[str] = Field(default_factory=list)
    matched_labels: List[str] = Field(
        default_factory=list,
        description="模型与当前任务共有、可实际写入的标签",
    )
    missing_task_labels: List[str] = Field(
        default_factory=list,
        description="当前任务存在但模型不具备、仍需人工处理的标签",
    )
    extra_model_labels: List[str] = Field(
        default_factory=list,
        description="模型存在但当前任务未配置、推理时会忽略的标签",
    )
    preset_slug: str = ""
    recommended: bool = False
    training_val_metrics: Optional[YoloTrainingValMetrics] = None
    recommended_conf: Optional[float] = Field(
        default=None,
        description="基于 mAP50-95 向上取 0.05 步长的推理置信度建议值",
    )


class YoloPreannotateModelsResponse(BaseModel):
    items: List[YoloPreannotateCandidateResponse]
    task_labels: List[str] = Field(default_factory=list)
    task_preset_slug: str = ""
    total: int = Field(default=0, description="搜索结果总数")
    page: int = Field(default=0, ge=0, description="从 0 开始的页码")
    size: int = Field(default=0, ge=0, description="当前页返回条数")


class YoloPreannotateJobResponse(BaseModel):
    id: int
    task_id: int
    status: str
    sample_count: int
    processed_count: int
    success_count: int
    annotated_count: int = 0
    empty_count: int = 0
    failed_count: int
    model_kind: str
    model_id: int
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
