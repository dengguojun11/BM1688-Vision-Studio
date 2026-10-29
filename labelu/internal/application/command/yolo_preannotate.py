from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class BatchYoloPreannotateCommand(BaseModel):
    """对当前任务批量写入 YOLO 检测结果预标注。"""

    model_kind: Literal["imported", "trained"] = Field(
        ...,
        description="imported=上传的 .pt；trained=本系统已完成训练作业",
    )
    model_id: int = Field(..., gt=0, description="导入模型 id 或训练作业 id")
    overwrite: bool = Field(default=True, description="覆盖该样本已有的 YOLO 预标注")
    apply_to_canvas_overwrite: Optional[bool] = Field(
        default=None,
        description="是否强制替换画布已有几何；省略时沿用 overwrite，false 只更新 YOLO 预标注并保留正式几何",
    )
    agnostic_nms: bool = Field(
        default=False,
        description="是否跨类别抑制重叠检测框；默认保持按类别分别抑制",
    )
    include_done_samples: bool = Field(
        default=False,
        description="未指定 sample_ids 时：false 仅 NEW；true 含 DONE（不含 SKIPPED）。指定 sample_ids 时本字段不参与筛选。",
    )
    sample_ids: Optional[List[int]] = Field(
        default=None,
        max_length=500,
        description="仅处理这些样本 id（须属于当前任务）；指定时忽略 include_done_samples，已跳过样本自动排除",
    )
    sample_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="仅处理单个样本时可传此字段，与 sample_ids 合并去重",
    )
    conf: float = Field(default=0.25, ge=0.05, le=1.0, description="推理置信度阈值")
    imgsz: Optional[int] = Field(
        default=None,
        ge=32,
        le=4096,
        description="推理尺寸；省略时使用训练作业尺寸或导入权重保存的训练尺寸，读取失败回退 640",
    )
