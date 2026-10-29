from typing import Any, Dict, List, Literal, Optional

from datetime import datetime

from pydantic import BaseModel, Field

from labelu.internal.application.response.board_agent import (
    BoardValidationStatusResponse,
)
from labelu.internal.application.response.training_task import (
    TrainingTaskDetailResponse,
)


class YoloHyperParamFieldItem(BaseModel):
    key: str
    label: str
    type: str
    hint: str = ""
    default: Optional[Any] = Field(
        default=None, description="LabelU 平台新建训练表单初始值"
    )
    official_default: Optional[Any] = Field(
        default=None,
        description="Ultralytics DEFAULT_CFG 官方值；null 表示 train() 默认不传该键",
    )


class YoloValidationPassAssessment(BaseModel):
    """由验证集 mAP 与阈值比对得到的达标结论（与 AI 报告无关）"""

    status: str = Field(description="达标 | 不达标 | 无验证指标")
    detail: Optional[str] = Field(default=None, description="数值与达标线说明")


class TrainingAssessmentResponse(BaseModel):
    status_code: Literal["passed", "conditional_passed", "failed", "pending"]
    status_label: Literal["达标", "条件达标", "不达标", "待验证"]
    reason_codes: List[str] = Field(default_factory=list)
    reason_summary: str = ""
    evidence: Optional[Dict[str, Any]] = None
    summary: Optional[str] = Field(
        default=None,
        description="兼容展示摘要；主消费应改用 status_label/reason_summary",
    )


class EdgeValidationAssessmentResponse(BaseModel):
    status_code: Literal["usable", "usable_needs_review", "pending", "unusable"]
    status_label: Literal["可用", "可运行但效果待确认", "未验证", "不可用"]
    reason_codes: List[str] = Field(default_factory=list)
    reason_summary: str = ""
    evidence: Optional[Dict[str, Any]] = None
    summary: Optional[str] = Field(
        default=None,
        description="兼容展示摘要；主消费应改用 status_label/reason_summary",
    )


class ReleaseGateAssessmentResponse(BaseModel):
    status_code: Literal["publishable", "review_required", "blocked"]
    status_label: Literal["可发布", "需复核", "不可发布"]
    reason_codes: List[str] = Field(default_factory=list)
    reason_summary: str = ""
    evidence: Optional[Dict[str, Any]] = None
    summary: Optional[str] = Field(
        default=None,
        description="兼容展示摘要；主消费应改用 status_label/reason_summary",
    )


class YoloQualityGateItem(BaseModel):
    key: str
    label: str
    status: str
    score: Optional[float] = None
    summary: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)


class YoloQualityAssessmentBundle(BaseModel):
    training_quality: Optional[YoloQualityGateItem] = None
    generalization_quality: Optional[YoloQualityGateItem] = None
    edge_usability: Optional[YoloQualityGateItem] = None
    release_readiness: Optional[YoloQualityGateItem] = None


class YoloTrainingValMetrics(BaseModel):
    """训练质量指标。

    常规作业使用验证集 mAP；自动训练作业在没有传统 ``val`` 指标时，
    可以使用受信评估写入的 ``overall_recall`` 作为质量证据。两者保留
    独立字段，避免把整体召回率误标成 mAP。
    """

    map50_95: Optional[float] = None
    map50: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    overall_recall: Optional[float] = Field(
        default=None,
        description="自动训练评估整体召回率；仅在没有传统 val 指标时作为训练质量证据",
    )
    overall_recall_threshold: Optional[float] = Field(
        default=None,
        description="自动训练评估使用的整体召回率达标线（若证据中提供）",
    )
    fitness: Optional[float] = None
    epoch: Optional[int] = None
    summary_line: Optional[str] = Field(
        default=None, description="单行摘要，便于表格展示"
    )
    source: Optional[str] = Field(
        default=None,
        description="best_pt_val_metrics | results_csv_best_epoch | final_metrics | auto_training_evaluation | none",
    )
    split: Optional[str] = Field(default="val", description="val | test | evaluation")


class YoloTestValMetrics(BaseModel):
    """测试集 model.val(split=test) 结果（写入 metrics_summary.test_val_metrics）。"""

    status: str = Field(description="ok | fail | skip | running")
    split: str = "test"
    map50_95: Optional[float] = None
    map50: Optional[float] = None
    precision: Optional[float] = None
    recall: Optional[float] = None
    images: Optional[int] = None
    instances: Optional[int] = None
    summary_line: Optional[str] = None
    detail: Optional[str] = None
    run_at: Optional[str] = None


class YoloValTestComparison(BaseModel):
    """训练验证集 vs 测试集指标对比。"""

    val_map50: Optional[float] = None
    test_map50: Optional[float] = None
    delta_map50: Optional[float] = None
    val_map50_95: Optional[float] = None
    test_map50_95: Optional[float] = None
    delta_map50_95: Optional[float] = None
    val_inference_ms: Optional[float] = None
    test_inference_ms: Optional[float] = None
    summary_line: Optional[str] = None


class YoloAiProviderMetricRow(BaseModel):
    """某一视觉大模型 full_analysis：摘录 + 由正文推断的结论"""

    provider_key: str
    provider_label: str
    metric_text: Optional[str] = None
    has_report: bool = False
    error_message: Optional[str] = None
    conclusion: Optional[str] = Field(
        default=None,
        description="由 AI 报告推断：达标|不达标|未明确；无报告为未评估；异常为评估失败",
    )


class YoloClassMetricRow(BaseModel):
    """单个检测类别：数据集框数 + 验证/测试精度。"""

    index: int = Field(description="类别序号，与训练 data.yaml names 顺序一致")
    label: str = Field(description="拉框预设标签名")
    train_instances: Optional[int] = Field(
        default=None, description="训练集 labels 中该类矩形框数量"
    )
    val_instances: Optional[int] = Field(
        default=None, description="验证集 labels 中该类矩形框数量"
    )
    test_instances: Optional[int] = Field(
        default=None, description="测试集 labels 中该类矩形框数量"
    )
    train_images: Optional[int] = Field(
        default=None, description="训练集中包含该标签拉框的图片数量"
    )
    val_images: Optional[int] = Field(
        default=None, description="验证集中包含该标签拉框的图片数量"
    )
    test_images: Optional[int] = Field(
        default=None, description="测试集中包含该标签拉框的图片数量"
    )
    map50: Optional[float] = Field(default=None, description="验证集 AP@IoU0.5")
    map50_95: Optional[float] = Field(default=None, description="验证集 AP@IoU0.5:0.95")
    precision: Optional[float] = Field(default=None, description="验证集 Precision")
    recall: Optional[float] = Field(default=None, description="验证集 Recall")
    test_map50: Optional[float] = Field(default=None, description="测试集 AP@IoU0.5")
    test_map50_95: Optional[float] = Field(
        default=None, description="测试集 AP@IoU0.5:0.95"
    )
    test_precision: Optional[float] = Field(
        default=None, description="测试集 Precision"
    )
    test_recall: Optional[float] = Field(default=None, description="测试集 Recall")


class YoloClassMetricsDetail(BaseModel):
    """拉框预设全部标签（训练顺序）、框数分布与各类别模型精度。"""

    classes: List[str] = Field(default_factory=list)
    rows: List[YoloClassMetricRow] = Field(
        default_factory=list,
        description="合并框数与 val/test 精度，前端主表",
    )
    val_metrics: List[YoloClassMetricRow] = Field(default_factory=list)
    test_metrics: List[YoloClassMetricRow] = Field(default_factory=list)


class YoloTrainStepItemResponse(BaseModel):
    class YoloTrainStepArtifactResponse(BaseModel):
        path: str
        label: str

    class YoloTrainSubStepCheckResponse(BaseModel):
        key: str
        label: str
        status: str = Field(
            description="waiting | running | done | failed | cancelled | skipped"
        )
        detail: Optional[str] = None

    class YoloTrainSubStepItemResponse(BaseModel):
        key: str
        label: str
        status: str = Field(
            description="waiting | running | done | failed | cancelled | skipped"
        )
        summary: Optional[str] = None
        detail: Optional[str] = None
        checks: List["YoloTrainStepItemResponse.YoloTrainSubStepCheckResponse"] = Field(
            default_factory=list
        )
        artifacts: List["YoloTrainStepItemResponse.YoloTrainStepArtifactResponse"] = (
            Field(default_factory=list)
        )

    key: str
    label: str
    status: str = Field(
        description="waiting | running | done | failed | cancelled | skipped"
    )
    summary: Optional[str] = None
    detail: Optional[str] = None
    order: int = 0
    substeps: List[YoloTrainSubStepItemResponse] = Field(default_factory=list)
    artifacts: List[YoloTrainStepArtifactResponse] = Field(default_factory=list)
    root_path: Optional[str] = None


class StrategyTaskMarkerResponse(BaseModel):
    source: str = "third_party_api"
    source_label: str = "第三方接口"
    task_type: str = ""
    task_type_label: str = ""
    external_task_id: str = ""
    platform_status: str = ""


class YoloTrainedModelRowResponse(BaseModel):
    """训练完成且可下载权重的作业（一条对应一个 best.pt）"""

    job_id: int
    job_type: str = "yolo_detection"
    component_progress: Optional[Dict[str, Any]] = None
    component_metrics: Optional[Dict[str, Any]] = None
    composite_assessment: Optional[Dict[str, Any]] = None
    strategy_task: Optional[StrategyTaskMarkerResponse] = None
    task_id: int
    task_name: Optional[str] = None
    training_task_id: Optional[int] = None
    training_task_name: Optional[str] = None
    training_task_mode: Optional[str] = None
    training_task_display_subtitle: Optional[str] = None
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
    category: Optional[str] = None
    weights_filename: Optional[str] = Field(
        default=None,
        description="权重文件原始文件名（一般为 best.pt，取自 weights_path 末段）",
    )
    model_weights: Optional[str] = None
    epochs: Optional[int] = None
    imgsz: Optional[int] = None
    train_sample_count: Optional[int] = None
    val_sample_count: Optional[int] = None
    train_phase_detail: Optional[str] = Field(default=None)
    current_step_key: Optional[str] = Field(default=None)
    train_steps: List[YoloTrainStepItemResponse] = Field(default_factory=list)
    test_sample_count: Optional[int] = Field(
        default=None, description="测试集样本数（metrics_summary.dataset.test）"
    )
    total_sample_count: Optional[int] = Field(
        default=None, description="本次训练图片总数 train+val+test"
    )
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    creator_email: Optional[str] = Field(
        default=None,
        description="创建该训练作业的用户登录账号（username，一般为邮箱）",
    )
    training_val_metrics: Optional[YoloTrainingValMetrics] = None
    test_val_metrics: Optional[YoloTestValMetrics] = None
    val_test_comparison: Optional[YoloValTestComparison] = None
    training_assessment: Optional[TrainingAssessmentResponse] = None
    edge_validation_assessment: Optional[EdgeValidationAssessmentResponse] = None
    release_gate_assessment: Optional[ReleaseGateAssessmentResponse] = None
    quality_assessment: Optional[YoloQualityAssessmentBundle] = None
    training_status: Literal["passed", "review_required", "failed"] = "review_required"
    training_status_label: Literal["达标", "待复核", "不达标"] = "待复核"
    bm1688_export_id: Optional[int] = None
    bm1688_export_status: Optional[str] = None
    bm1688_compile_status: Literal[
        "not_started", "queued", "compiling", "completed", "failed"
    ] = "not_started"
    bm1688_compile_status_label: Literal[
        "未编译", "排队中", "编译中", "已编译", "编译失败"
    ] = "未编译"
    bm1688_compile_updated_at: Optional[datetime] = None
    bm1688_bmodel_rel_path: Optional[str] = None
    validation_pass: Optional[YoloValidationPassAssessment] = Field(
        default=None,
        description="依据验证指标与 YOLO_VAL_PASS_* 阈值判定",
    )
    ai_eval_metrics: List[YoloAiProviderMetricRow] = Field(
        default_factory=list,
        description="已弃用于模型列表展示，保留字段兼容旧客户端",
    )
    class_metrics: Optional[YoloClassMetricsDetail] = Field(
        default=None,
        description="拉框预设各类别验证/测试精度（训练顺序）",
    )
    task_rect_preset_slug: Optional[str] = Field(
        default=None,
        description="主任务 config.defaultRectPresetId，用于初始权重下拉与拉框预设匹配",
    )
    task_rect_classes: Optional[List[str]] = Field(
        default=None,
        description="主任务 rectTool 类别名列表（与训练 data.yaml 一致）",
    )


class YoloTrainedModelCatalogSummary(BaseModel):
    """模型库在当前任务/分类筛选范围内的全量训练门禁统计。"""

    total: int = 0
    training_passed: int = 0
    training_review_required: int = 0
    training_failed: int = 0
    # 兼容旧客户端；新模型库不再展示发布资格。
    publishable: int = 0
    review_required: int = 0
    blocked: int = 0


class YoloTrainedModelCatalogMeta(BaseModel):
    total: int
    page: Optional[int] = None
    size: int
    summary: YoloTrainedModelCatalogSummary


class YoloTrainedModelListResponse(BaseModel):
    meta_data: Optional[YoloTrainedModelCatalogMeta] = None
    data: List[YoloTrainedModelRowResponse]


class YoloModelValidationPointResponse(BaseModel):
    x: float
    y: float
    confidence: Optional[float] = None


class YoloModelValidationDetectionResponse(BaseModel):
    label: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    corners: List[YoloModelValidationPointResponse] = Field(default_factory=list)
    plate_number: Optional[str] = None
    plate_number_confidence: Optional[float] = None
    color: Optional[str] = None
    color_confidence: Optional[float] = None
    plate_type: Optional[str] = None
    plate_type_confidence: Optional[float] = None


class YoloModelValidateImageResponse(BaseModel):
    task_id: int
    job_id: int
    job_type: str = "yolo_detection"
    model_source: str = "best.pt"
    strategy_task: Optional[StrategyTaskMarkerResponse] = None
    task_name: Optional[str] = None
    image_width: int
    image_height: int
    box_count: int
    max_confidence: float = 0.0
    detections: List[YoloModelValidationDetectionResponse] = Field(default_factory=list)
    result_image_data_url: str


class YoloTrainStopInfoResponse(BaseModel):
    configured_epochs: Optional[int] = None
    actual_epochs: Optional[int] = None
    best_epoch: Optional[int] = None
    patience: Optional[int] = None
    early_stopped: bool = False
    stop_reason: Optional[str] = Field(
        default=None, description="early_stopping，未确认早停时为 null"
    )
    stop_reason_summary: Optional[str] = None


class YoloTrainJobListRowResponse(BaseModel):
    """全局训练作业列表行（含所属标注任务名称）"""

    id: int
    job_type: str = "yolo_detection"
    component_progress: Optional[Dict[str, Any]] = None
    component_metrics: Optional[Dict[str, Any]] = None
    composite_assessment: Optional[Dict[str, Any]] = None
    strategy_task: Optional[StrategyTaskMarkerResponse] = None
    task_id: int
    project_id: Optional[int] = None
    task_name: Optional[str] = None
    training_task_id: Optional[int] = None
    training_task_name: Optional[str] = None
    training_task_mode: Optional[str] = None
    training_task_display_subtitle: Optional[str] = None
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
    merge_task_ids: Optional[List[int]] = Field(
        default=None, description="合并训练时的全部来源任务 id"
    )
    merge_task_label: Optional[str] = Field(
        default=None, description="合并任务展示，如 #1+2+3"
    )
    merge_task_sample_counts: Optional[Dict[str, int]] = Field(
        default=None,
        description="各任务导出张数（多任务时）；数据集就绪后可用",
    )
    category: Optional[str] = None
    status: str
    hyperparams: Optional[Dict[str, Any]] = None
    model_weights: Optional[str] = None
    epochs: Optional[int] = None
    actual_epochs: Optional[int] = Field(
        default=None,
        description="results.csv 末行 epoch（训练完成或进行中均已落盘时可用）",
    )
    train_stop: Optional[YoloTrainStopInfoResponse] = None
    imgsz: Optional[int] = None
    train_sample_count: Optional[int] = None
    val_sample_count: Optional[int] = None
    test_sample_count: Optional[int] = None
    training_assessment: Optional[TrainingAssessmentResponse] = None
    edge_validation_assessment: Optional[EdgeValidationAssessmentResponse] = None
    release_gate_assessment: Optional[ReleaseGateAssessmentResponse] = None
    quality_assessment: Optional[YoloQualityAssessmentBundle] = None
    bm1688_export_id: Optional[int] = None
    bm1688_export_status: Optional[str] = None
    bm1688_compile_status: Literal[
        "not_started", "queued", "compiling", "completed", "failed"
    ] = "not_started"
    bm1688_compile_status_label: Literal[
        "未编译", "排队中", "编译中", "已编译", "编译失败"
    ] = "未编译"
    bm1688_compile_updated_at: Optional[datetime] = None
    bm1688_bmodel_rel_path: Optional[str] = None
    validation_pass: Optional[YoloValidationPassAssessment] = Field(
        default=None,
        description="依据验证指标与 YOLO_VAL_PASS_* 阈值判定",
    )
    total_sample_count: Optional[int] = Field(
        default=None, description="本次训练图片总数 train+val+test"
    )
    runtime_status: Optional[str] = Field(
        default=None,
        description="运行态：RUNNING / STOPPED / COMPLETED / FAILED / CANCELLED",
    )
    runtime_pid: Optional[int] = Field(default=None, description="训练运行进程 PID")
    is_restartable: bool = Field(default=False, description="当前作业是否可继续运行")
    restart_reason: Optional[str] = Field(default=None, description="可继续运行原因")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    creator_email: Optional[str] = Field(
        default=None,
        description="创建该训练作业的用户登录账号（username，一般为邮箱）",
    )
    task_rect_preset_slug: Optional[str] = Field(
        default=None,
        description="涓讳换鍔?config.defaultRectPresetId锛岀敤浜庡墠绔睍绀鸿缁冪被鍒笌棰勮",
    )
    task_rect_classes: Optional[List[str]] = Field(
        default=None,
        description="涓讳换鍔?rectTool 绫诲埆鍚嶅垪琛紙涓庤缁?data.yaml 涓€鑷达級",
    )
    execution_mode: Optional[str] = Field(
        default="local",
        description="local=本机；remote=远程 GPU 节点",
    )
    train_node_id: Optional[str] = None
    train_node_name: Optional[str] = None
    remote_phase: Optional[str] = Field(
        default=None,
        description="远程训练阶段（上传/运行/同步等）",
    )
    remote_upload_pct: Optional[int] = Field(
        default=None,
        description="远程上传进度 0–100（phase=uploading 时）",
    )
    train_gpu_device: Optional[str] = Field(
        default=None,
        description="Ultralytics device，如 0 / 1 / 0,1",
    )
    train_gpu_mode: Optional[str] = Field(
        default=None,
        description="single=单卡；dual=双卡 DDP；cpu",
    )
    train_gpu_label: Optional[str] = Field(
        default=None,
        description="页面展示：单卡 GPU0 / 双卡 DDP (0,1) 等",
    )


class YoloTrainJobResponse(BaseModel):
    id: int
    job_type: str = "yolo_detection"
    component_status: Optional[Dict[str, Any]] = None
    component_artifacts: Optional[Dict[str, Any]] = None
    component_progress: Optional[Dict[str, Any]] = None
    component_metrics: Optional[Dict[str, Any]] = None
    composite_assessment: Optional[Dict[str, Any]] = None
    strategy_task: Optional[StrategyTaskMarkerResponse] = None
    task_id: int
    project_id: Optional[int] = None
    task_name: Optional[str] = None
    training_task_id: Optional[int] = None
    series_id: Optional[int] = None
    variant_id: Optional[int] = None
    branch_key: Optional[str] = None
    selection_contract_sha256: Optional[str] = None
    selection_contract: Optional[Dict[str, Any]] = None
    sample_conditions: Dict[str, List[str]] = Field(default_factory=dict)
    parent_model_version_id: Optional[int] = None
    parent_training_job_id: Optional[int] = None
    inheritance_mode: Optional[str] = None
    training_task_name: Optional[str] = None
    training_task_mode: Optional[str] = None
    training_task_display_subtitle: Optional[str] = None
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
    training_task_detail: Optional[TrainingTaskDetailResponse] = None
    merge_task_ids: Optional[List[int]] = Field(
        default=None, description="合并训练时的全部来源任务 id"
    )
    merge_task_label: Optional[str] = None
    merge_task_sample_counts: Optional[Dict[str, int]] = Field(
        default=None,
        description="各任务参与训练的 DONE 样本数，键为任务 id 字符串",
    )
    status: str
    category: Optional[str] = None
    hyperparams: Optional[Dict[str, Any]] = None
    model_weights: Optional[str] = None
    epochs: Optional[int] = None
    actual_epochs: Optional[int] = None
    train_stop: Optional[YoloTrainStopInfoResponse] = None
    imgsz: Optional[int] = None
    train_sample_count: Optional[int] = None
    val_sample_count: Optional[int] = None
    weights_path: Optional[str] = None
    dataset_manifest: Optional[Dict[str, Any]] = Field(
        default=None,
        description="训练数据不可变清单及文件 SHA-256",
    )
    metrics_summary: Optional[str] = None
    error_message: Optional[str] = None
    ai_eval_full_status: Optional[str] = None
    ai_eval_next_status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # 训练中：尝试从作业目录 results.csv 末行解析
    current_epoch: Optional[int] = Field(
        default=None, description="当前 epoch（训练中可能有）"
    )
    progress_snapshot: Optional[str] = Field(
        default=None, description="results.csv 最新一行摘要，便于页面展示趋势"
    )
    train_phase: Optional[str] = Field(
        default=None,
        description="训练阶段（phase.json）：如 preparing_dataset / loading_model / running",
    )
    train_phase_label: Optional[str] = Field(
        default=None,
        description="训练阶段中文说明，无 results.csv 时用于进度条文案",
    )
    train_phase_detail: Optional[str] = Field(default=None)
    current_step_key: Optional[str] = Field(default=None)
    train_steps: List[YoloTrainStepItemResponse] = Field(default_factory=list)
    test_sample_count: Optional[int] = Field(
        default=None, description="测试集样本数（metrics_summary.dataset.test）"
    )
    total_sample_count: Optional[int] = Field(
        default=None, description="本次训练图片总数 train+val+test"
    )
    training_val_metrics: Optional[YoloTrainingValMetrics] = None
    test_val_metrics: Optional[YoloTestValMetrics] = None
    val_test_comparison: Optional[YoloValTestComparison] = None
    training_assessment: Optional[TrainingAssessmentResponse] = None
    edge_validation_assessment: Optional[EdgeValidationAssessmentResponse] = None
    release_gate_assessment: Optional[ReleaseGateAssessmentResponse] = None
    quality_assessment: Optional[YoloQualityAssessmentBundle] = None
    bm1688_export_id: Optional[int] = None
    bm1688_export_status: Optional[str] = None
    bm1688_compile_status: Literal[
        "not_started", "queued", "compiling", "completed", "failed"
    ] = "not_started"
    bm1688_compile_status_label: Literal[
        "未编译", "排队中", "编译中", "已编译", "编译失败"
    ] = "未编译"
    bm1688_compile_updated_at: Optional[datetime] = None
    bm1688_bmodel_rel_path: Optional[str] = None
    validation_pass: Optional[YoloValidationPassAssessment] = Field(
        default=None,
        description="依据验证指标与 YOLO_VAL_PASS_* 阈值判定",
    )
    runtime_status: Optional[str] = Field(
        default=None,
        description="运行态：RUNNING / STOPPED / COMPLETED / FAILED / CANCELLED",
    )
    runtime_pid: Optional[int] = Field(default=None, description="训练运行进程 PID")
    is_restartable: bool = Field(default=False, description="当前作业是否可继续运行")
    restart_reason: Optional[str] = Field(default=None, description="可继续运行原因")
    class_metrics: Optional[YoloClassMetricsDetail] = Field(
        default=None,
        description="拉框预设各类别验证/测试精度（训练顺序）",
    )
    task_rect_preset_slug: Optional[str] = Field(
        default=None,
        description="主任务 config.defaultRectPresetId，用于前端展示训练类别与预设",
    )
    task_rect_classes: Optional[List[str]] = Field(
        default=None,
        description="主任务 rectTool 类别名列表（与训练 data.yaml 一致）",
    )
    execution_mode: Optional[str] = Field(
        default="local",
        description="local=本机；remote=远程 GPU 节点",
    )
    train_node_id: Optional[str] = None
    train_node_name: Optional[str] = None
    remote_status: Optional[Dict[str, Any]] = Field(
        default=None,
        description="远程训练状态摘要",
    )
    remote_upload_pct: Optional[int] = Field(
        default=None,
        description="远程上传进度 0–100（phase=uploading 时）",
    )
    train_gpu_device: Optional[str] = Field(
        default=None,
        description="Ultralytics device，如 0 / 1 / 0,1",
    )
    train_gpu_mode: Optional[str] = Field(
        default=None,
        description="single=单卡；dual=双卡 DDP；cpu",
    )
    train_gpu_label: Optional[str] = Field(
        default=None,
        description="页面展示：单卡 GPU0 / 双卡 DDP (0,1) 等",
    )


class YoloImportedModelRowResponse(BaseModel):
    """用户上传的外部权重记录"""

    id: int
    display_name: Optional[str] = None
    registered_display_name: Optional[str] = Field(
        default=None,
        description="最近一次登记到模型仓库时填写的模型显示名称",
    )
    category: Optional[str] = None
    original_filename: Optional[str] = None
    file_size: Optional[int] = None
    created_at: Optional[datetime] = None
    creator_email: Optional[str] = Field(
        default=None,
        description="上传该权重的用户登录账号（username，一般为邮箱）",
    )
    can_delete: bool = Field(
        default=False,
        description="当前用户是否可以删除这条外部导入记录",
    )
    registered_model_count: int = Field(
        default=0,
        description="引用该权重的模型仓库版本数量",
    )
    training_val_metrics: Optional[YoloTrainingValMetrics] = Field(
        default=None,
        description="外部导入无本平台训练记录时为 None",
    )
    validation_pass: Optional[YoloValidationPassAssessment] = Field(
        default=None,
        description="外部导入一般为无验证指标",
    )
    ai_eval_metrics: List[YoloAiProviderMetricRow] = Field(
        default_factory=list,
        description="模型列表不再填充，保留兼容",
    )


class YoloBm1688ExportRowResponse(BaseModel):
    """YOLO 训练权重导出：ONNX（必选）+ 可选 .bmodel（依赖服务端编译钩子）"""

    id: int
    strategy_task: Optional[StrategyTaskMarkerResponse] = None
    task_id: int
    task_name: Optional[str] = None
    yolo_train_job_id: int
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
    status: str
    can_recompile_bmodel: bool = Field(
        default=False,
        description="存在可用 best.pt 或 ONNX 时允许手动重新编译",
    )
    imgsz: int
    onnx_filename: Optional[str] = None
    bmodel_filename: Optional[str] = None
    bmodel_filenames: List[str] = Field(
        default_factory=list,
        description="导出目录下全部 .bmodel（对齐 sophon BM1688 多精度/多 batch）",
    )
    error_message: Optional[str] = None
    quality_summary: str = Field(
        default="",
        description="编译服务器侧质量报告一行摘要（训练/ONNX/产物校验）",
    )
    training_assessment: Optional[TrainingAssessmentResponse] = None
    edge_validation_assessment: Optional[EdgeValidationAssessmentResponse] = None
    release_gate_assessment: Optional[ReleaseGateAssessmentResponse] = None
    validation_gate_label: str = Field(
        default="",
        description="关联训练作业验证集达标判定（达标/不达标/无验证指标）",
    )
    edge_validation_label: str = Field(
        default="",
        description="边端验证任务状态（已完成/部分完成/失败/验证中等）",
    )
    edge_execution_label: str = Field(
        default="",
        description="边端执行阶段状态（未开始/待执行/执行中/已完成/执行失败）",
    )
    edge_int8_eval_label: str = Field(
        default="",
        description="INT8 量化保真状态（待编译/待执行/评估中/待回传/通过/需复核/未通过/不可评估）",
    )
    current_verdict_label: str = Field(
        default="",
        description="按训练达标、编译、边端执行、INT8 精度聚合后的当前业务结论",
    )
    workflow_stage: str = Field(default="compile")
    edge_result_status: str = Field(default="unknown")
    verdict_code: str = Field(default="review_required")
    doc_hint: str = Field(
        default="",
        description="算能盒部署说明摘要（API 返回固定文案）",
    )
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    board_validation: Optional[BoardValidationStatusResponse] = Field(
        default=None,
        description="算能盒板端验证状态（Agent 拉取任务并回传）",
    )
    artifact_manifest: Optional[Dict[str, Any]] = Field(
        default=None,
        description="PT/ONNX/BModel/校准集及编译配置追溯清单",
    )
    compatibility_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="专用模型编译、板端加载、推理与产物范围的分层兼容结论",
    )
    quality_gate: Optional[Dict[str, Any]] = Field(
        default=None,
        description="模型质量准入检查快照",
    )


class YoloBm1688ExportCatalogSummary(BaseModel):
    """当前 BM1688 列表筛选范围内的全量工作流统计。"""

    total: int = 0
    running: int = 0
    blocked: int = 0
    publish_ready: int = 0
    pending_edge: int = 0
    failed: int = 0


class YoloBm1688ExportCatalogMeta(BaseModel):
    total: int
    page: Optional[int] = None
    size: int
    summary: Optional[YoloBm1688ExportCatalogSummary] = None


class YoloBm1688ExportListResponse(BaseModel):
    meta_data: Optional[YoloBm1688ExportCatalogMeta] = None
    data: List[YoloBm1688ExportRowResponse]


class YoloBm1688ExportFamilyItemResponse(YoloBm1688ExportRowResponse):
    """One compile task enriched with its governed model-version lineage."""

    model_version_id: Optional[int] = None
    version_no: Optional[int] = None
    dataset_version_id: Optional[int] = None
    dataset_version_no: Optional[int] = None


class YoloBm1688ExportFamilyResponse(BaseModel):
    """Compile history grouped by the same task + algorithm model series."""

    family_key: str
    task_id: int
    task_name: Optional[str] = None
    algorithm_id: Optional[int] = None
    algorithm_key: str
    grouping_source: Literal["model_version", "legacy_training_job", "mixed"]
    latest_model_version_id: Optional[int] = None
    latest_version_no: Optional[int] = None
    version_count: int
    compile_task_count: int
    latest_export_id: int
    latest_status: str
    updated_at: Optional[datetime] = None
    exports: List[YoloBm1688ExportFamilyItemResponse] = Field(default_factory=list)


class YoloBm1688ExportDetailResponse(YoloBm1688ExportRowResponse):
    """导出记录详情（含编译日志与目录提示）。"""

    compile_log: Optional[str] = None
    export_dir: str = Field(
        default="",
        description="服务端导出根目录（MEDIA_ROOT 下 yolo_bm1688_exports/{id}）",
    )
    export_dir_abs: str = Field(
        default="",
        description="服务端导出根目录绝对路径（评估弹窗数据来源追溯）",
    )
    can_recompile_bmodel: bool = Field(
        default=False,
        description="存在可用 best.pt 或 ONNX 时可 POST recompile；运行中的自动任务会排队接管",
    )
    quality_report: Optional[dict] = Field(
        default=None,
        description="编译服务器侧质量报告 JSON（训练 mAP、编译日志摘要、产物校验等）",
    )
    quality_report_refresh: Optional[dict] = Field(
        default=None,
        description="quality-report 后台刷新状态（RUNNING/COMPLETED/FAILED）",
    )
