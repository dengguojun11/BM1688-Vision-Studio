from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from labelu.internal.application.response.board_agent import (
    BoardValidationStatusResponse,
)
from labelu.internal.application.response.yolo_train import (
    EdgeValidationAssessmentResponse,
    YoloQualityAssessmentBundle,
    ReleaseGateAssessmentResponse,
    StrategyTaskMarkerResponse,
    TrainingAssessmentResponse,
    YoloValidationPassAssessment,
)


class AicmPublishEnvironmentResponse(BaseModel):
    id: str
    name: str = ""
    base_url: str = ""
    username: str = ""
    password_configured: bool = Field(
        default=False,
        description="密码是否已经加密保存到 MySQL",
    )
    password_runtime_available: bool = Field(
        default=False,
        description="当前后端是否能够成功解密并使用密码",
    )
    password_masked: str = ""
    password_source_type: str = ""
    password_masked_hint: str = ""
    password_updated_at: Optional[str] = None
    chip_type: str = "SOPHGO,BM1688,NPU"
    algorithm_type: str = "Detection"
    verify_ssl: bool = False
    ready_to_publish: bool = False
    missing_fields: List[str] = Field(default_factory=list)
    readiness_reason: str = Field(
        default="",
        description="发布环境就绪或阻断的可读原因",
    )
    is_active: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AicmPublishEnvironmentsBundleResponse(BaseModel):
    active_environment_id: str = ""
    active_environment: Optional[AicmPublishEnvironmentResponse] = None
    environments: List[AicmPublishEnvironmentResponse] = Field(default_factory=list)
    total: int = Field(default=0, ge=0, description="当前搜索条件下的环境总数")
    ready_count: int = Field(default=0, ge=0, description="当前搜索条件下可发布的环境数")
    page: Optional[int] = Field(default=None, ge=0)
    size: int = Field(default=0, ge=0)
    config_backend_label: str = Field(
        default="MySQL 治理表（system_config_*）",
        description="配置唯一主存说明",
    )
    template_cache_dir: str = ""


class AicmRuntimeTemplateCandidateResponse(BaseModel):
    model_set_id: int
    name: str = ""
    model_alg_type: str = ""
    status: str = ""
    created_at: Optional[str] = None


class AicmPublishConfigResponse(BaseModel):
    base_url: str = ""
    username: str = ""
    password_configured: bool = False
    password_masked: str = ""
    password_source_type: str = ""
    password_masked_hint: str = ""
    password_updated_at: Optional[str] = None
    chip_type: str = "SOPHGO,BM1688,NPU"
    algorithm_type: str = "Detection"
    verify_ssl: bool = False
    ready_to_publish: bool = Field(
        default=False,
        description="base_url、用户名、密码均已保存，可点击发布",
    )
    missing_fields: List[str] = Field(
        default_factory=list,
        description="尚未写入的配置项：base_url / username / password",
    )
    template_cache_dir: str = Field(default="", description="AICM 模板 zip 缓存目录")


class AicmPublishBmodelOption(BaseModel):
    rel_path: str = Field(
        description="相对 models/ 的路径，如 BM1688/yolov11n_int8_1b.bmodel"
    )
    filename: str = Field(description="文件名，如 yolov11n_int8_1b.bmodel")
    precision: str = Field(description="fp32 | fp16 | int8")
    label: str = Field(description="前端展示标签")
    recommended: bool = Field(
        default=False,
        description="是否为推荐默认项（边端部署通常为 INT8）",
    )
    eval_gate_required: bool = Field(
        default=False,
        description="历史字段：INT8 SOP 门控仅作参考，未达标不阻断发布",
    )
    export_storage_path: str = Field(
        default="",
        description="本系统 BM1688 导出目录中的存储路径（models/ 下），非 AICM zip 内路径",
    )
    validation_status: str = Field(
        default="unknown",
        description="该精度板端同口径验证状态：pass / review / exceeded / missing / not_applicable",
    )
    publishable: bool = Field(
        default=False,
        description="该精度产物是否可由当前人工发布入口选择",
    )
    pt_map50: Optional[float] = None
    bmodel_map50: Optional[float] = None
    drop_map50: Optional[float] = None
    blocked_reason: Optional[str] = None
    quality_warning: Optional[str] = Field(
        default=None,
        description="该精度的质量风险提示；人工发布时仅提示、不阻断",
    )


class AicmPublishableRowResponse(BaseModel):
    export_id: int
    strategy_task: Optional[StrategyTaskMarkerResponse] = None
    yolo_train_job_id: int
    task_id: int
    task_name: str = ""
    dataset_category_id: Optional[int] = None
    dataset_category_key: Optional[str] = None
    dataset_category_name: Optional[str] = None
    class_names: List[str] = Field(default_factory=list)
    label_set_signature: Optional[str] = None
    class_order_signature: Optional[str] = None
    data_yaml_sha256: Optional[str] = None
    dataset_content_signature: Optional[str] = None
    training_recipe_key: Optional[str] = None
    training_recipe_version: Optional[int] = None
    runtime_profile: Optional[str] = None
    runtime_interface_version: Optional[int] = None
    publish_template_key: Optional[str] = None
    publish_template_name: Optional[str] = None
    publish_template_source: str = "runtime_profile"
    publish_template_id: Optional[int] = None
    publish_template_version: Optional[int] = None
    publish_template_sha256: Optional[str] = None
    publish_template_model_set_id: Optional[int] = None
    legacy_preset_fallback: bool = False
    category: str = ""
    rect_preset_name: str = Field(
        default="", description="用于 AICM _like_name 搜索的拉框预设显示名"
    )
    rect_preset_slug: str = ""
    imgsz: int = 640
    bmodel_filename: Optional[str] = None
    bmodel_options: List[AicmPublishBmodelOption] = Field(
        default_factory=list,
        description="本次导出可用的 bmodel 精度选项",
    )
    default_bmodel_rel_path: Optional[str] = Field(
        default=None,
        description="推荐默认选中的 bmodel 相对路径",
    )
    validation_pass: YoloValidationPassAssessment
    training_assessment: Optional[TrainingAssessmentResponse] = None
    edge_validation_assessment: Optional[EdgeValidationAssessmentResponse] = None
    release_gate_assessment: Optional[ReleaseGateAssessmentResponse] = None
    quality_assessment: Optional[YoloQualityAssessmentBundle] = None
    map50_95: Optional[float] = None
    map50: Optional[float] = None
    export_created_at: Optional[datetime] = None
    job_created_at: Optional[datetime] = None
    creator_email: Optional[str] = None
    template_cached: bool = Field(
        default=False,
        description="该预设名下是否已有缓存的 AICM 模板 zip",
    )
    edge_validation: Optional[BoardValidationStatusResponse] = Field(
        default=None,
        description="边端验证最新状态；人工发布时作为风险证据展示",
    )
    publish_blocked_reason: Optional[str] = Field(
        default=None,
        description="当前禁止发布的原因",
    )
    publish_gate_status: str = Field(default="unknown")
    publish_gate_reason_codes: List[str] = Field(default_factory=list)


class AicmPublishStepItem(BaseModel):
    key: str
    label: str
    status: str = Field(description="pending | running | ok | error | skipped")
    detail: Optional[str] = None


class AicmPublishResultResponse(BaseModel):
    ok: bool
    export_id: int
    precision: Optional[str] = Field(
        default=None,
        description="本次实际发布的编译精度：int8 | fp16 | fp32",
    )
    bmodel_rel_path: Optional[str] = Field(
        default=None,
        description="本次实际发布的 BModel 在导出目录 models/ 下的已校验相对路径",
    )
    model_id: Optional[str] = Field(
        default=None,
        description="AICM 上传后新模型集 ID，外部策略平台 target.model_id 应使用该值",
    )
    rect_preset_name: str = ""
    model_set_id: Optional[int] = None
    upload_name: Optional[str] = Field(
        default=None,
        description="edit_name 设置的 AICM 展示名（默认使用本次训练任务名称，最长 20 个字符）",
    )
    upload_response: Optional[str] = None
    package_path: Optional[str] = Field(
        default=None, description="本次打包 zip 路径（服务端）"
    )
    steps: List[str] = Field(default_factory=list)
    business_thresholds: Optional[Dict[str, Any]] = None
    validation_thresholds: Optional[Dict[str, Any]] = None
    threshold_binding: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class AicmPublishHistoryRowResponse(BaseModel):
    job_id: str
    export_id: int
    strategy_task: Optional[StrategyTaskMarkerResponse] = None
    model_id: Optional[str] = None
    model_set_id: Optional[int] = None
    upload_name: Optional[str] = None
    task_id: Optional[int] = None
    task_name: str = ""
    dataset_category_id: Optional[int] = None
    dataset_category_key: Optional[str] = None
    dataset_category_name: Optional[str] = None
    class_names: List[str] = Field(default_factory=list)
    label_set_signature: Optional[str] = None
    class_order_signature: Optional[str] = None
    data_yaml_sha256: Optional[str] = None
    dataset_content_signature: Optional[str] = None
    training_recipe_key: Optional[str] = None
    training_recipe_version: Optional[int] = None
    runtime_profile: Optional[str] = None
    runtime_interface_version: Optional[int] = None
    publish_template_key: Optional[str] = None
    publish_template_name: Optional[str] = None
    publish_template_source: Optional[str] = None
    publish_template_id: Optional[int] = None
    publish_template_version: Optional[int] = None
    publish_template_sha256: Optional[str] = None
    publish_template_model_set_id: Optional[int] = None
    preset_name: str = ""
    status: str = Field(description="PROCESSING | COMPLETED | FAILED")
    success: bool = Field(description="是否发布成功（COMPLETED 且 result.ok）")
    publisher_username: str = ""
    publisher_user_id: Optional[int] = None
    publish_mode: str = Field(
        default="automatic",
        description="发布触发方式：manual | automatic",
    )
    quality_gate_status: Optional[str] = None
    quality_risk_snapshot: Optional[Dict[str, Any]] = None
    business_thresholds: Optional[Dict[str, Any]] = None
    validation_thresholds: Optional[Dict[str, Any]] = None
    threshold_binding: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class AicmPublishJobResponse(BaseModel):
    job_id: str
    export_id: int
    strategy_task: Optional[StrategyTaskMarkerResponse] = None
    status: str = Field(description="PROCESSING | COMPLETED | FAILED")
    current_step: Optional[str] = None
    preset_name: str = ""
    task_id: Optional[int] = None
    task_name: str = ""
    dataset_category_id: Optional[int] = None
    dataset_category_key: Optional[str] = None
    dataset_category_name: Optional[str] = None
    class_names: List[str] = Field(default_factory=list)
    label_set_signature: Optional[str] = None
    class_order_signature: Optional[str] = None
    data_yaml_sha256: Optional[str] = None
    dataset_content_signature: Optional[str] = None
    training_recipe_key: Optional[str] = None
    training_recipe_version: Optional[int] = None
    runtime_profile: Optional[str] = None
    runtime_interface_version: Optional[int] = None
    publish_template_key: Optional[str] = None
    publish_template_name: Optional[str] = None
    publish_template_source: Optional[str] = None
    publish_template_id: Optional[int] = None
    publish_template_version: Optional[int] = None
    publish_template_sha256: Optional[str] = None
    publish_template_model_set_id: Optional[int] = None
    publisher_username: str = ""
    publisher_user_id: Optional[int] = None
    publish_mode: str = Field(
        default="automatic",
        description="发布触发方式：manual | automatic",
    )
    quality_gate_status: Optional[str] = None
    quality_risk_snapshot: Optional[Dict[str, Any]] = None
    steps: List[AicmPublishStepItem] = Field(default_factory=list)
    log_lines: List[str] = Field(default_factory=list)
    process_log_path: Optional[str] = Field(
        default=None,
        description="详细过程日志文件路径（BASE_DATA_DIR/logs/aicm_publish/）",
    )
    result: Optional[AicmPublishResultResponse] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
