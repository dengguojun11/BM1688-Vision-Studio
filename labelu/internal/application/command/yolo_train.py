from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class CreateYoloTrainJobCommand(BaseModel):
    model_weights: str = Field(
        default="yolo11n.pt",
        description=(
            "Ultralytics 权重名：普通检测使用 yolo11n.pt，"
            "plate_detection_v1/plate_recognition_v1 使用 yolov8n-pose.pt；"
            "weights_source 非 preset 时可忽略"
        ),
    )
    weights_source: Optional[Literal["preset", "imported", "trained", "auto"]] = Field(
        default=None,
        description="初始权重来源：auto=同系列迭代；preset=官方名；imported=外部导入；trained=本系统已完成训练",
    )
    weights_ref_id: Optional[int] = Field(
        default=None,
        gt=0,
        description="imported 模型 id 或已完成训练作业 id（weights_source 为 imported/trained 时必填）",
    )
    plate_training_target: Optional[Literal["plate_detection", "plate_recognition"]] = (
        None
    )
    epochs: int = Field(default=50, ge=1, le=3000)
    imgsz: int = Field(default=640, ge=320, le=1280)
    category: str = Field(
        default="",
        max_length=128,
        description="训练分类，用于归类同一实验线下的多次训练",
    )
    hyperparams: Optional[Dict[str, Any]] = Field(
        default=None,
        description="可选 Ultralytics train 超参（仅允许服务端白名单内的键）",
    )
    merge_task_ids: Optional[List[int]] = Field(
        default=None,
        description="与主任务合并训练的其它 FINISHED 图像任务 id（须与 rect_preset_slug 一致）",
    )
    rect_preset_slug: Optional[str] = Field(
        default=None,
        max_length=128,
        description="annotation-settings 中的拉框预设 slug，用于校验/合并同预设任务",
    )
    training_recipe_key: Optional[str] = Field(
        default=None,
        max_length=128,
        description="统一训练契约选中的训练配方 key",
    )
    training_recipe_version: Optional[int] = Field(
        default=None,
        ge=1,
        description="训练配方版本；与 training_recipe_key 一起形成不可变快照",
    )
    execution_mode: Literal["local", "remote"] = Field(
        default="local",
        description="local=本机 GPU 队列；remote=下发到配置的远程训练节点",
    )
    train_node_id: Optional[str] = Field(
        default=None,
        max_length=64,
        description="execution_mode=remote 时必填，对应远程节点 id",
    )
    # Optional model-series lineage; old YOLO clients may omit all fields.
    series_id: Optional[int] = Field(default=None, gt=0)
    variant_id: Optional[int] = Field(default=None, gt=0)
    series_name: Optional[str] = Field(default=None, max_length=128)
    variant_name: Optional[str] = Field(default=None, max_length=128)
    model_family: Optional[str] = Field(default=None, max_length=128)
    parent_model_version_id: Optional[int] = Field(default=None, gt=0)
    parent_training_job_id: Optional[int] = Field(default=None, gt=0)
    branch_key: Optional[str] = Field(default=None, min_length=1, max_length=64)
    inheritance_mode: Optional[str] = Field(default=None, max_length=32)
    selection_contract: Optional[Dict[str, Any]] = None
    sample_conditions: Dict[str, List[str]] = Field(default_factory=dict)


class PatchYoloTrainRuntimeHyperparamsCommand(BaseModel):
    hyperparams: Dict[str, Any] = Field(
        default_factory=dict,
        description="暂停/失败后或当前 epoch 结束后可应用的续训超参覆盖",
    )
