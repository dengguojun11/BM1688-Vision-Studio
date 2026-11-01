from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, validator


class UpdateAutoIterationSettingsCommand(BaseModel):
    """自动迭代策略的可视化配置更新。

    所有字段可选：页面只提交发生变化的项，未提交项保持数据库中的现值。
    ``reason`` 必填，写入配置变更历史，保证口径调整可追溯。
    ``expected_values`` 可选，提交「修改前看到的值」，服务端不一致时返回 409。
    """

    enabled: Optional[bool] = None
    metric_basis: Optional[str] = None
    pass_threshold: Optional[float] = None
    warn_threshold: Optional[float] = None
    max_rounds: Optional[int] = None
    no_improvement_rounds: Optional[int] = None
    min_improvement_delta: Optional[float] = None
    allow_conditional_pass: Optional[bool] = None
    require_quality_for_compile: Optional[bool] = None
    min_class_recall_enabled: Optional[bool] = None
    min_class_recall: Optional[float] = None
    precision_floor_enabled: Optional[bool] = None
    precision_floor: Optional[float] = None
    max_regression: Optional[float] = None
    eval_conf: Optional[float] = None
    expected_values: Optional[Dict[str, Any]] = None
    reason: str = Field(min_length=1, max_length=2000)

    @validator("reason", pre=True)
    def _normalize_reason(cls, value):
        return str(value or "").strip()
