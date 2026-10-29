"""Configuration and adapter registry for the 7GAI training pipeline.

The registry is intentionally small and declarative.  Persisted rows provide
an auditable catalog while this module remains the source of truth for the
built-in platform capabilities.  Adding a same-kind model means adding a
definition here (and its migration seed), not changing the training state
machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from labelu.internal.common.training_worker import (
    TRAINING_PROTOCOL_VERSION,
    TRAINING_STAGES,
    make_command,
)

# 7GAI 的边端训练目标只面向实际交付硬件。CPU/CUDA 是训练执行资源，
# 不是可部署的边端芯片，因此不能出现在目标芯片能力或训练表单中。
TARGET_CHIPS: tuple[str, ...] = ("bm1688", "bm1684", "rk1808")


@dataclass(frozen=True)
class AlgorithmGroupSpec:
    key: str
    name: str
    description: str
    sort_order: int


@dataclass(frozen=True)
class AlgorithmSpec:
    key: str
    name: str
    description: str
    task_type: str
    group_key: str
    required_tools: tuple[str, ...]
    required_attributes: tuple[str, ...]
    training_adapter: str
    evaluation_adapter: str
    supported_chips: tuple[str, ...]
    default_recipe: str
    input_contract: Mapping[str, Any]
    output_contract: Mapping[str, Any]
    # Product catalog classification.  Kept in metadata for compatibility
    # with databases created before the registry acquired these fields.
    algorithm_kind: str = "model"
    runtime_adapter: str = "yolo_detection_runtime"
    rule_schema: Mapping[str, Any] | None = None
    catalog_kind: str = "internal"
    supported_formats: tuple[str, ...] = ()
    min_samples: Mapping[str, Any] | None = None
    quality_gate: Mapping[str, Any] | None = None
    risk_level: str = "standard"
    annotation_contract_version: str = "v1"


ALGORITHM_GROUPS: tuple[AlgorithmGroupSpec, ...] = (
    AlgorithmGroupSpec(
        "person_vehicle_detection", "通用人车检测", "人员、车辆及其基础目标检测能力", 10
    ),
    AlgorithmGroupSpec(
        "person_behavior", "人员属性与行为", "人员属性、行为和安全相关模型", 20
    ),
    AlgorithmGroupSpec(
        "urban_management", "城市管理杂物", "垃圾、占道和城市管理目标模型", 30
    ),
    AlgorithmGroupSpec(
        "fire_environment", "消防安全与环境", "消防、安全和环境目标模型", 40
    ),
)


ALGORITHM_DEFINITIONS: tuple[AlgorithmSpec, ...] = (
    AlgorithmSpec(
        key="yolo_detection",
        name="YOLO 通用目标检测",
        description="兼容现有 YoloTrainJob 的目标检测训练、评估和导出能力",
        task_type="object_detection",
        group_key="person_vehicle_detection",
        required_tools=("rectTool", "tagTool"),
        required_attributes=(),
        training_adapter="yolo_detection",
        evaluation_adapter="yolo_detection",
        supported_chips=TARGET_CHIPS,
        default_recipe="yolo_detection_v1",
        input_contract={"media": ["image", "video_frame"], "annotation": "bbox"},
        output_contract={"type": "detections", "fields": ["class_id", "score", "bbox"]},
    ),
    AlgorithmSpec(
        key="classification",
        name="通用图像分类",
        description="通过标准容器适配器执行图像分类训练、评估和导出",
        task_type="image_classification",
        group_key="person_behavior",
        required_tools=("tagTool",),
        required_attributes=(),
        training_adapter="classification",
        evaluation_adapter="classification",
        supported_chips=TARGET_CHIPS,
        default_recipe="classification_v1",
        input_contract={"media": ["image"], "annotation": "class_label"},
        output_contract={"type": "classification", "fields": ["class_id", "score"]},
    ),
    AlgorithmSpec(
        key="segmentation",
        name="通用实例分割",
        description="通过标准容器适配器执行实例分割训练、评估和导出",
        task_type="instance_segmentation",
        group_key="urban_management",
        required_tools=("polygonTool",),
        required_attributes=(),
        training_adapter="segmentation",
        evaluation_adapter="segmentation",
        supported_chips=TARGET_CHIPS,
        default_recipe="segmentation_v1",
        input_contract={"media": ["image", "video_frame"], "annotation": "polygon"},
        output_contract={"type": "masks", "fields": ["class_id", "score", "mask"]},
    ),
    AlgorithmSpec(
        key="pose",
        name="通用姿态估计",
        description="通过标准容器适配器执行关键点姿态训练、评估和导出",
        task_type="pose_estimation",
        group_key="person_behavior",
        required_tools=("keypointTool",),
        required_attributes=(),
        training_adapter="pose",
        evaluation_adapter="pose",
        supported_chips=TARGET_CHIPS,
        default_recipe="pose_v1",
        input_contract={"media": ["image", "video_frame"], "annotation": "keypoints"},
        output_contract={"type": "poses", "fields": ["class_id", "score", "keypoints"]},
    ),
    AlgorithmSpec(
        key="custom_container",
        name="自定义容器模型",
        description="使用平台标准输入输出协议接入自定义训练容器",
        task_type="custom",
        group_key="fire_environment",
        required_tools=(),
        required_attributes=(),
        training_adapter="custom_container",
        evaluation_adapter="custom_container",
        supported_chips=TARGET_CHIPS,
        default_recipe="custom_container_v1",
        input_contract={"media": ["image", "video", "audio"], "annotation": "custom"},
        output_contract={"type": "custom", "fields": ["predictions", "artifacts"]},
    ),
)


# Product-facing catalog.  These entries deliberately reuse the existing
# YOLO/container adapter boundary; adding a capability therefore does not
# require another training state machine.  Three event capabilities are rule
# templates and consume detections plus a rule schema instead of training a
# standalone model.  The five internal adapter entries above remain available
# as implementation primitives and are marked ``catalog_kind=internal``.
_PRODUCT_CATALOG: tuple[tuple[str, str, str, str, str], ...] = (
    ("non_motor_vehicle_detection", "非机动车识别", "person_vehicle_detection", "model", "product_detection"),
    ("illegal_parking_detection", "车辆违停", "person_vehicle_detection", "model", "product_detection"),
    ("traffic_flow_analysis", "车流监测分析", "person_vehicle_detection", "pipeline", "traffic_analysis"),
    ("large_vehicle_detection", "大车识别", "person_vehicle_detection", "model", "product_detection"),
    ("smoke_detection", "烟雾识别", "fire_environment", "model", "object_detection"),
    ("overflowing_bin_detection", "垃圾桶溢出识别", "urban_management", "model", "object_detection"),
    ("street_litter_detection", "街道垃圾识别", "urban_management", "model", "object_detection"),
    ("street_vendor_detection", "游摊小贩识别", "urban_management", "model", "object_detection"),
    ("manhole_detection", "井盖识别", "urban_management", "model", "object_detection"),
    ("construction_waste_detection", "装修垃圾识别", "urban_management", "model", "object_detection"),
    ("corridor_clutter_detection", "楼道杂物堆放识别", "urban_management", "model", "object_detection"),
    ("cat_dog_detection", "猫狗识别", "urban_management", "model", "object_detection"),
    ("escalator_luggage_detection", "扶梯大件行李识别", "urban_management", "model", "object_detection"),
    ("lightweight_floating_object_detection", "轻飘物识别", "urban_management", "model", "object_detection"),
    ("electric_bike_helmet_detection", "电动车头盔识别", "person_behavior", "model", "attribute_detection"),
    ("face_mask_detection", "口罩识别", "person_behavior", "model", "attribute_detection"),
    ("phone_usage_detection", "玩手机识别", "person_behavior", "model", "attribute_detection"),
    ("insulated_glove_detection", "绝缘手套智能识别", "person_behavior", "model", "attribute_detection"),
    ("safety_helmet_detection", "安全帽识别", "person_behavior", "model", "attribute_detection"),
    ("chef_uniform_detection", "厨师服检测", "person_behavior", "model", "attribute_detection"),
    ("person_intrusion", "人员闯入识别", "person_behavior", "rule_template", "rule_template"),
    ("boundary_crossing", "越界检测", "person_behavior", "rule_template", "rule_template"),
    ("near_water_danger", "靠近水域危险识别", "person_behavior", "rule_template", "rule_template"),
    ("fence_climbing_detection", "翻越栏杆检测", "person_behavior", "model", "behavior_detection"),
    ("crowd_gathering_detection", "聚众识别", "person_behavior", "pipeline", "behavior_detection"),
    ("passenger_flow_counting", "客流统计", "person_behavior", "pipeline", "behavior_detection"),
    ("loitering_detection", "徘徊检测", "person_behavior", "pipeline", "behavior_detection"),
    ("control_room_absence_detection", "监控室脱岗检测", "person_behavior", "pipeline", "behavior_detection"),
    ("fall_detection", "跌倒识别", "person_behavior", "model", "behavior_detection"),
    ("fishing_detection", "钓鱼/捕捞行为检测", "person_behavior", "pipeline", "behavior_detection"),
    ("tent_illegal_setup_detection", "违规搭建帐篷检测", "person_behavior", "pipeline", "behavior_detection"),
)


def _build_product_specs() -> tuple[AlgorithmSpec, ...]:
    specs: list[AlgorithmSpec] = []
    for key, name, group_key, kind, task_type in _PRODUCT_CATALOG:
        is_rule = kind == "rule_template"
        adapter = "rule_template" if is_rule else "yolo_detection"
        specs.append(
            AlgorithmSpec(
                key=key,
                name=name,
                description=(
                    "复用通用检测结果的可配置规则模板"
                    if is_rule
                    else f"7GAI 产品目录中的{name}能力"
                ),
                task_type=task_type,
                group_key=group_key,
                required_tools=() if is_rule else ("rectTool", "tagTool"),
                required_attributes=(),
                training_adapter=adapter,
                evaluation_adapter=adapter,
                supported_chips=TARGET_CHIPS,
                default_recipe=None if is_rule else "yolo_detection_v1",
                input_contract={"media": ["image", "video_frame"], "annotation": "rule" if is_rule else "bbox"},
                output_contract={"type": "rule_event" if is_rule else "detections", "fields": ["event", "score"] if is_rule else ["class_id", "score", "bbox"]},
                algorithm_kind=kind,
                runtime_adapter="rule_engine" if is_rule else "yolo_detection_runtime",
                rule_schema=(
                    {
                        "version": "1",
                        # ``requires`` is retained for older clients that only
                        # understand presence checks.  New clients and the
                        # backend use the stricter JSON Schema below.
                        "requires": ["region_or_line", "time_window"],
                        "json_schema": {
                            "type": "object",
                            "required": ["region_or_line", "time_window"],
                            "properties": {
                                "region_or_line": {
                                    "oneOf": [
                                        {"type": "string", "minLength": 1},
                                        {
                                            "type": "array",
                                            "minItems": 2,
                                            "items": {
                                                "oneOf": [
                                                    {
                                                        "type": "array",
                                                        "minItems": 2,
                                                        "maxItems": 2,
                                                        "items": {"type": "number"},
                                                    },
                                                    {
                                                        "type": "object",
                                                        "required": ["x", "y"],
                                                        "properties": {
                                                            "x": {"type": "number"},
                                                            "y": {"type": "number"},
                                                        },
                                                        "additionalProperties": False,
                                                    },
                                                ]
                                            },
                                        },
                                    ]
                                },
                                "time_window": {
                                    "oneOf": [
                                        {"type": "number", "exclusiveMinimum": 0},
                                        {
                                            "type": "object",
                                            "required": ["seconds"],
                                            "properties": {
                                                "seconds": {
                                                    "type": "number",
                                                    "exclusiveMinimum": 0,
                                                }
                                            },
                                            "additionalProperties": False,
                                        },
                                    ]
                                },
                            },
                            "additionalProperties": False,
                        },
                    }
                    if is_rule
                    else None
                ),
                catalog_kind="product",
            )
        )
    return tuple(specs)


PRODUCT_ALGORITHM_DEFINITIONS: tuple[AlgorithmSpec, ...] = _build_product_specs()
# Keep this symbol as the complete registry consumed by seeding and callers.
ALL_ALGORITHM_DEFINITIONS: tuple[AlgorithmSpec, ...] = ALGORITHM_DEFINITIONS + PRODUCT_ALGORITHM_DEFINITIONS


def algorithm_spec(key: str) -> AlgorithmSpec | None:
    normalized = str(key or "").strip().lower()
    return next(
        (item for item in ALL_ALGORITHM_DEFINITIONS if item.key == normalized), None
    )


def all_algorithm_specs() -> tuple[AlgorithmSpec, ...]:
    return ALL_ALGORITHM_DEFINITIONS


class TrainingAdapter:
    """SPI implemented by each model family.

    Implementations return a normalized request/result envelope.  They do not
    own persistence or job status transitions; those remain in the existing
    training services and queue.
    """

    key = ""

    def prepare_dataset(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def train(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def evaluate(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def export(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def validate_runtime(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class RuleTemplateAdapter(TrainingAdapter):
    """Runtime-only adapter for rule capabilities.

    Rule templates consume an upstream model's detections and therefore do
    not own dataset preparation, training, or model export.  They still use a
    versioned envelope for validation/runtime execution so the API can expose
    their capability without presenting them as trainable models.
    """

    key = "rule_template"
    execution_mode = "in_process"

    @staticmethod
    def _envelope(stage: str, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise TypeError("adapter request must be a mapping")
        payload = dict(request)
        payload.setdefault("job_type", "rule_template")
        return {
            "adapter_key": "rule_template",
            "stage": stage,
            "request": payload,
        }

    def prepare_dataset(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise ValueError("rule_template 不需要训练数据准备")

    def train(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise ValueError("rule_template 不支持模型训练")

    def evaluate(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise ValueError("rule_template 不支持独立模型评估")

    def export(self, request: Mapping[str, Any]) -> dict[str, Any]:
        raise ValueError("rule_template 不支持模型导出")

    def validate_runtime(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("validate_runtime", request)


class YoloDetectionAdapter(TrainingAdapter):
    """Adapter boundary for the existing YOLO service and job state machine."""

    key = "yolo_detection"

    @staticmethod
    def _envelope(stage: str, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise TypeError("adapter request must be a mapping")
        payload = dict(request)
        payload.setdefault("job_type", "yolo_detection")
        return {"adapter_key": "yolo_detection", "stage": stage, "request": payload}

    def prepare_dataset(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("prepare_dataset", request)

    def train(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("train", request)

    def evaluate(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("evaluate", request)

    def export(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("export", request)

    def validate_runtime(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("validate_runtime", request)


class ContainerTrainingAdapter(TrainingAdapter):
    """Standard envelope for model families executed by an external worker.

    The adapter owns contract validation and command normalization only. The
    worker owns container execution and reports back through the existing job
    lifecycle, so every model family uses the same retry and quality gates.
    """

    execution_mode = "container"

    def __init__(self, key: str) -> None:
        self.key = key

    def _envelope(self, stage: str, request: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            raise TypeError("adapter request must be a mapping")
        payload = dict(request)
        payload.setdefault("job_type", self.key)
        image = payload.get("container_image")
        if image is not None and (not isinstance(image, str) or not image.strip()):
            raise ValueError("container_image must be a non-empty string")
        if isinstance(image, str):
            payload["container_image"] = image.strip()
            image = payload["container_image"]
        if stage in {"train", "evaluate", "export"} and not image:
            raise ValueError(f"{self.key} 适配器需要 container_image")
        # Normalize the mode before exposing the envelope.  ``make_command``
        # also mirrors this field into the worker request; keeping it here
        # makes envelope.request and command.request the same contract.
        payload["execution_mode"] = self.execution_mode
        command = make_command(
            adapter_key=self.key,
            stage=stage,
            request=payload,
            execution_mode=self.execution_mode,
            command_id=payload.get("command_id"),
            container_image=image,
            fencing_token=payload.get("fencing_token"),
        )
        return {
            "adapter_key": self.key,
            "execution_mode": self.execution_mode,
            "stage": stage,
            "request": payload,
            "protocol_version": TRAINING_PROTOCOL_VERSION,
            "command": command,
        }

    def prepare_dataset(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("prepare_dataset", request)

    def train(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("train", request)

    def evaluate(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("evaluate", request)

    def export(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("export", request)

    def validate_runtime(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return self._envelope("validate_runtime", request)


class ClassificationAdapter(ContainerTrainingAdapter):
    """Image classification model-family adapter."""

    def __init__(self) -> None:
        super().__init__("classification")


class SegmentationAdapter(ContainerTrainingAdapter):
    """Instance segmentation model-family adapter."""

    def __init__(self) -> None:
        super().__init__("segmentation")


class PoseAdapter(ContainerTrainingAdapter):
    """Human pose/keypoint model-family adapter."""

    def __init__(self) -> None:
        super().__init__("pose")


class CustomContainerAdapter(ContainerTrainingAdapter):
    """Escape hatch for a model implementing the worker protocol itself."""

    def __init__(self) -> None:
        super().__init__("custom_container")


_ADAPTERS: dict[str, TrainingAdapter] = {
    "yolo_detection": YoloDetectionAdapter(),
    "classification": ClassificationAdapter(),
    "segmentation": SegmentationAdapter(),
    "pose": PoseAdapter(),
    "custom_container": CustomContainerAdapter(),
    "rule_template": RuleTemplateAdapter(),
}


def get_training_adapter(key: str) -> TrainingAdapter:
    normalized = str(key or "").strip().lower()
    try:
        return _ADAPTERS[normalized]
    except KeyError as exc:
        raise ValueError(f"未注册的训练适配器: {key}") from exc


def adapter_capabilities(key: str) -> dict[str, Any]:
    adapter = get_training_adapter(key)
    if isinstance(adapter, RuleTemplateAdapter):
        return {
            "adapter_key": adapter.key,
            "execution_mode": "rule",
            "protocol_version": TRAINING_PROTOCOL_VERSION,
            "stages": ["validate_runtime"],
            "worker": "in_process",
            "implemented": ["validate_runtime"],
        }
    return {
        "adapter_key": adapter.key,
        "execution_mode": getattr(adapter, "execution_mode", "in_process"),
        "protocol_version": TRAINING_PROTOCOL_VERSION,
        "stages": list(TRAINING_STAGES),
        "worker": (
            "container"
            if getattr(adapter, "execution_mode", None) == "container"
            else "in_process"
        ),
        "implemented": [
            name
            for name in (
                "prepare_dataset",
                "train",
                "evaluate",
                "export",
                "validate_runtime",
            )
            if getattr(type(adapter), name) is not getattr(TrainingAdapter, name)
        ],
    }
