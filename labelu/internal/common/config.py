import os
from pathlib import Path
from typing import Optional

from loguru import logger
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from labelu.internal.common.io import get_data_dir


def _candidate_deploy_env_files() -> list[Path]:
    """常见部署 .env 位置（与 deploy.sh 的 labeIuMedia/.env 一致）。"""
    candidates: list[Path] = []
    venv_root = (os.environ.get("VIRTUAL_ENV") or "").strip()
    if venv_root:
        candidates.append(Path(venv_root).resolve().parent / ".env")
    candidates.extend(
        [
            Path("/data/labeIU/labeIuMedia/.env"),
            Path.cwd() / ".env",
        ]
    )
    seen: set[str] = set()
    out: list[Path] = []
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def bootstrap_labelu_env_file() -> Optional[Path]:
    """在 Settings 加载前设置 LABELU_ENV_FILE，避免 CLI 落到 ~/.local/share/labelu。"""
    explicit = (os.environ.get("LABELU_ENV_FILE") or "").strip()
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p.resolve()
    for p in _candidate_deploy_env_files():
        if p.is_file():
            resolved = p.resolve()
            os.environ["LABELU_ENV_FILE"] = str(resolved)
            return resolved
    return None


def _settings_env_files() -> tuple[str, ...]:
    """返回 dotenv 加载顺序，让显式运行环境文件覆盖兼容性 .env。"""
    bootstrap_labelu_env_file()
    files: list[str] = []
    explicit = (os.environ.get("LABELU_ENV_FILE") or "").strip()
    cwd_env = Path.cwd() / ".env"
    if cwd_env.is_file():
        resolved = str(cwd_env.resolve())
        if resolved not in files:
            files.append(resolved)
    # Pydantic Settings applies later dotenv files over earlier ones.  The
    # explicit deploy/local file must therefore be last; otherwise a stale
    # repository-level .env can silently replace the selected database URL.
    if explicit and Path(explicit).is_file():
        resolved = str(Path(explicit).resolve())
        if resolved not in files:
            files.append(resolved)
    return tuple(files) if files else (".env",)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=_settings_env_files(),
        env_file_encoding="utf-8",
        case_sensitive=True,
        # 生产环境可能保留来自旧版本的扩展变量；忽略未知键，避免一次新增配置阻断整个服务启动。
        extra="ignore",
    )

    SCHEME: str = "http"
    APP_ENV: str = Field(
        default="development",
        description=(
            "运行环境：development/test/staging/production。生产环境启动时启用强制安全校验。"
        ),
    )
    HOST: str = "localhost"
    PORT: str = "8000"
    API_V1_STR: str = "/api/v1"
    MEDIA_HOST: str = Field(
        default="",
        description=(
            "浏览器访问的站点根 URL（附件/预览绝对链接）。"
            "留空则按 SCHEME://HOST:PORT 推导；端口与访问不一致会导致图片 404。"
            "通过 labelu --port 启动时会自动覆盖为与监听端口一致"
        ),
    )
    MEDIA_HOST_AUTO_SYNC: bool = Field(
        default=True,
        description="启动时是否将解析后的 MEDIA_HOST 写回当前目录 .env（与 --host/--port 一致）",
    )

    # 数据根目录：默认使用 appdirs 用户数据目录（Windows 多为 %LOCALAPPDATA%\labelu\labelu）。
    # 服务器大盘可设 BASE_DATA_DIR=/data/labelu（见 .env.example）；将同步推导 MEDIA_ROOT 与 SQLite 路径。
    BASE_DATA_DIR: str = Field(default_factory=get_data_dir)
    MEDIA_ROOT: Optional[Path] = Field(
        default=None,
        description="媒体文件根目录；省略时为 BASE_DATA_DIR/media",
    )
    UPLOAD_DIR: str = "upload"
    EXPORT_DIR: str = "export"
    LOG_FILE_ENABLED: bool = Field(
        default=True,
        description="是否写入 BASE_DATA_DIR/logs 下的按天滚动日志文件（stdout/journal 仍保留）",
    )
    LOG_DIR: str = Field(
        default="",
        description="日志目录；留空则为 BASE_DATA_DIR/logs",
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="写入 labelu.log 的最低级别（DEBUG/INFO/WARNING/ERROR）",
    )
    LOG_RETENTION: str = Field(
        default="30 days",
        description="全量日志 labelu.log 保留时长（loguru retention）",
    )
    LOG_ERROR_RETENTION: str = Field(
        default="90 days",
        description="错误日志 error.log 保留时长",
    )
    LOG_ROTATION: str = Field(
        default="00:00",
        description="按天切分时刻（本地时区），默认每天 0 点新文件",
    )
    LOG_HTTP_DETAIL: bool = Field(
        default=True,
        description="HTTP 访问日志是否包含 request_id、用户、query 等详情",
    )
    LOG_HTTP_BODY: bool = Field(
        default=False,
        description="是否记录 /api/ 的 JSON 请求体与响应体（默认关以免大响应拖慢接口；排障时再开）",
    )
    LOG_HTTP_BODY_MAX_CHARS: int = Field(
        default=2000,
        description="单条 HTTP 请求体日志最大字符数",
    )
    LOG_HTTP_BODY_MAX_CAPTURE_BYTES: int = Field(
        default=65536,
        description="开启 LOG_HTTP_BODY 时，超过该字节的 body 只记长度、不解析 JSON（避免大标注拖慢）",
    )
    LOG_BUSINESS_DETAIL: bool = Field(
        default=True,
        description="是否输出 [biz] 业务步骤日志（服务内关键操作）",
    )
    SAMPLE_LOCKING_ENABLED: bool = Field(
        default=False,
        description="是否启用标注页样本占用/协作 WebSocket；默认关闭以避免样本切换时的连接、心跳和 peers 广播开销",
    )
    UPLOAD_FILE_MAX_SIZE: int = 200_000_000  # ~200MB
    MEDIA_VIRUS_SCAN_ENABLED: bool = Field(
        default=False,
        description="是否对断点上传完成前的媒体文件执行病毒扫描",
    )
    MEDIA_VIRUS_SCAN_REQUIRED: bool = Field(
        default=False,
        description="生产环境是否禁止未完成病毒扫描的媒体进入数据集",
    )
    MEDIA_VIRUS_SCAN_COMMAND: str = Field(
        default="",
        description="病毒扫描命令模板；使用 {path} 占位符，不经过 shell 执行",
    )
    MEDIA_VIRUS_SCAN_TIMEOUT_SECONDS: int = Field(
        default=120,
        ge=1,
        le=3600,
        description="单个媒体病毒扫描超时秒数",
    )
    MEDIA_VIRUS_QUARANTINE_RETENTION_HOURS: int = Field(
        default=72,
        ge=1,
        le=24 * 365,
        description="病毒隔离文件保留时长，过期后由清理任务删除",
    )
    THUMBNAIL_HEIGH_PIXEL: int = 120
    STORAGE_BACKEND: str = "local"

    S3_ENDPOINT: str = ""
    S3_REGION: str = ""
    S3_BUCKET: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_PUBLIC_BASE_URL: str = ""
    S3_PRESIGN_EXPIRE_SECONDS: int = 3600
    S3_PATH_STYLE: bool = False
    S3_USE_SSL: bool = True
    STRATEGY_INTEGRATION_S3_ENDPOINT: str = "http://10.10.11.28:29100"
    STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID: str = "minioadmin"
    STRATEGY_INTEGRATION_S3_SECRET_ACCESS_KEY: str = "minioadmin"
    STRATEGY_INTEGRATION_S3_PATH_STYLE: bool = True
    STRATEGY_INTEGRATION_S3_USE_SSL: bool = False
    STRATEGY_EVOLUTION_OUTBOUND_CALLBACK_BASE_URL: str = ""
    STRATEGY_EVOLUTION_THRESHOLD_CALLBACK_URL: str = ""
    STRATEGY_EVOLUTION_MODEL_CALLBACK_URL: str = ""
    STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET: str = Field(
        "",
        repr=False,
        description=("策略演化结果回调 HMAC-SHA256 密钥；生产环境必须配置"),
    )
    STRATEGY_EVOLUTION_SERVICE_TOKEN: str = Field(
        "",
        repr=False,
        description=(
            "策略演化内部查询服务令牌；配置后可通过 X-7GAI-Service-Token "
            "或 Authorization: Bearer 调用受保护的查询/重试接口"
        ),
    )
    STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED: bool = Field(
        True,
        description=(
            "是否强制策略演化和实验数据集内部查询接口使用服务令牌或 HMAC；"
            "仅测试/旧协议迁移可显式关闭"
        ),
    )
    STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS: bool = Field(
        False,
        description=("是否允许无签名策略回调；仅用于受控的本地开发或旧协议迁移"),
    )
    STRATEGY_EVOLUTION_CALLBACK_HMAC_MAX_SKEW_SECONDS: int = Field(
        300,
        ge=30,
        le=3600,
        description="第三方回调 HMAC 时间戳允许的最大时钟偏差（秒）",
    )
    THIRD_PARTY_REQUEST_MAX_BYTES: int = Field(default=10 * 1024 * 1024, ge=1024)
    THIRD_PARTY_RATE_LIMIT_PER_MINUTE: int = Field(default=120, ge=1)

    AI_AUTO_LABEL_ENABLED: bool = False
    AI_PROVIDER: str = "local_http"
    AI_MODEL_ENDPOINT: str = ""
    AI_MODEL_TIMEOUT_SECONDS: int = 300
    AI_MODEL_NAME: str = ""
    AI_IMAGE_URL_EXPIRE_SECONDS: int = 300
    AI_BATCH_EMPTY_GEOMETRY_RETRIES: int = Field(
        0,
        ge=0,
        le=3,
        description=(
            "批量 AI 目标检测单张返回 0 个框时的可选重试次数（默认关闭）。"
            "实测重试多产生顶边小框假阳性，建议人工复核后再按任务开启"
        ),
    )
    AI_VISION_MAX_IMAGE_SIDE: int = Field(
        1536,
        ge=0,
        le=8192,
        description="发送给视觉模型的临时图片最长边；0 表示保留原尺寸",
    )
    AI_BBOX_VISION_MAX_IMAGE_SIDE: int = Field(
        2560,
        ge=0,
        le=8192,
        description="发送给目标检测视觉模型的临时图片最长边；0 表示保留原尺寸",
    )
    AI_BBOX_COORDINATE_SPACE: str = Field(
        "auto",
        description=(
            "目标检测模型返回坐标空间：auto=自动识别（模型图为大图且坐标全部落在 0-1000 时按"
            " 0-1000 归一化处理），pixels=按模型输入图像素，normalized_0_1000=强制按 0-1000 归一化"
        ),
    )
    AI_BBOX_WIDTH_CONVENTION: str = Field(
        "auto",
        description=(
            "归一化坐标下模型返回的 width 语义：auto=自动识别（多数框 x+width 越界时按右边界 x2 处理），"
            "size=宽，right_edge=右边界 x2"
        ),
    )
    AI_BBOX_HEIGHT_CONVENTION: str = Field(
        "auto",
        description=(
            "归一化坐标下模型返回的 height 语义：auto=自动识别（多数框 y+height 越界时按下边界 y2 处理），"
            "size=高，bottom_edge=下边界 y2"
        ),
    )

    # Provider adapters are never registered implicitly. Set this explicit
    # switch only after the corresponding model endpoint has been verified.
    PROVIDER_RUNTIME_AUTO_REGISTER: bool = False

    LA_AUTO_LABEL_ENABLED: bool = False
    LA_MODEL_ENDPOINT: str = "http://127.0.0.1:5001/"
    LA_MODEL_TIMEOUT_SECONDS: int = 300
    LA_MODEL_NAME: str = "nvidia/LocateAnything-3B"
    LA_MAX_IMAGE_SIDE: int = 1536
    LA_GPU_IDLE_UNLOAD_SECONDS: int = 300

    # VLX-Seek 1.5 图片预标注服务（独立于边端 SSH 和 LocateAnything）
    VLX_SEEK_ENABLED: bool = False
    VLX_SEEK_ENDPOINT: str = ""
    VLX_SEEK_TIMEOUT_SECONDS: int = 300
    VLX_SEEK_MODEL: str = "omlab/VLX-Seek-1.5-10B"
    VLX_SEEK_VERIFY_TLS: bool = False
    VLX_SEEK_MAX_NEW_TOKENS: int = 384

    # Rex-Omni 图片预标注服务（与 VLX 共用任务作业表和标签约束）
    REX_OMNI_ENABLED: bool = False
    REX_OMNI_ENDPOINT: str = ""
    REX_OMNI_TIMEOUT_SECONDS: int = 300
    REX_OMNI_MODEL: str = "IDEA-Research/Rex-Omni-AWQ"
    REX_OMNI_VERIFY_TLS: bool = False
    REX_OMNI_MAX_NEW_TOKENS: int = 1024

    # AI_PROVIDER=vision_llm_chain：优先使用系统管理的火山方舟 Coding Plan 配置。
    # 注意：BASE_URL 必须用 Coding 套餐地址 /api/coding/v3，用 /api/v3 会按量计费产生额外费用。
    VISION_VOLCENGINE_BASE_URL: str = "https://ark.cn-beijing.volces.com/api/coding/v3"
    VISION_VOLCENGINE_API_KEY: str = ""
    VISION_VOLCENGINE_MODEL: str = "doubao-seed-2-1-pro-260915"
    VISION_QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    VISION_QWEN_API_KEY: str = ""
    VISION_QWEN_MODEL_PLUS: str = "qwen3-vl-plus"
    VISION_QWEN_MODEL_235B: str = "qwen3-vl-235b-a22b-thinking"

    YOLO_TRAIN_ENABLED: bool = False
    # 独立 YOLO/车牌训练 Worker：一期默认关闭，前置迁移和本地验收完成后再单独开启。
    YOLO_TRAIN_DEDICATED_WORKER_ENABLED: bool = False
    YOLO_TRAIN_DEDICATED_WORKER_INTERVAL_SECONDS: float = 5.0
    YOLO_TRAIN_DEDICATED_WORKER_LEASE_SECONDS: int = 900
    YOLO_TRAIN_DEDICATED_WORKER_BATCH_LIMIT: int = 1
    # 自动样本预标注 Worker。启用后 API 只入队，独立进程轮询并执行，
    # 避免 Web 进程内线程持有模型和数据库连接。默认关闭，生产需完成
    # systemd、迁移、并发和重启接管验收后再开启。
    AUTO_PREANNOTATE_DEDICATED_WORKER_ENABLED: bool = False
    AUTO_PREANNOTATE_WORKER_INTERVAL_SECONDS: float = 5.0
    AUTO_PREANNOTATE_WORKER_LEASE_SECONDS: int = 900
    AUTO_PREANNOTATE_WORKER_BATCH_LIMIT: int = 1
    AUTO_PREANNOTATE_MAX_CONCURRENT_JOBS: int = 1
    # Automatic object detection runs the managed vision model per sample when
    # no compatible YOLO model exists.  Keep it switchable and optionally cap
    # the number of samples handled by one automatic job (0 = unlimited) so a
    # large import cannot silently start hundreds of model calls.
    AUTO_PREANNOTATE_AI_DETECTION_ENABLED: bool = True
    AUTO_PREANNOTATE_AI_DETECTION_MAX_SAMPLES: int = Field(
        0,
        ge=0,
        description="单个自动预标注作业最多处理多少张样本的目标检测回退；0 表示不限制",
    )
    # Manual YOLO batch preannotation uses the same durable-worker boundary as
    # automatic sample preannotation.  Keep disabled until production has the
    # migration and a managed worker process.
    YOLO_PREANNOTATE_DEDICATED_WORKER_ENABLED: bool = False
    YOLO_PREANNOTATE_WORKER_INTERVAL_SECONDS: float = 5.0
    YOLO_PREANNOTATE_WORKER_LEASE_SECONDS: int = 900
    YOLO_PREANNOTATE_WORKER_BATCH_LIMIT: int = 1
    YOLO_PREANNOTATE_MAX_CONCURRENT_JOBS: int = 1
    # TagTool、LocateAnything、VLX-Seek、Rex-Omni 共用一个独立预标注
    # Worker。默认关闭，关闭时保留现有 API 进程线程兼容路径。
    PROVIDER_PREANNOTATE_DEDICATED_WORKER_ENABLED: bool = False
    PROVIDER_PREANNOTATE_WORKER_INTERVAL_SECONDS: float = 5.0
    PROVIDER_PREANNOTATE_WORKER_LEASE_SECONDS: int = 900
    PROVIDER_PREANNOTATE_WORKER_BATCH_LIMIT: int = 1
    PROVIDER_PREANNOTATE_MAX_CONCURRENT_JOBS: int = 1
    # AICM 发布 Worker。开启后 Web 进程只持久化发布请求，外部 AICM
    # 上传、模型目录登记和结果回写由独立进程执行；默认关闭，完成
    # 真实 AICM、MySQL、systemd 和重启接管验收后再开启。
    AICM_PUBLISH_DEDICATED_WORKER_ENABLED: bool = False
    AICM_PUBLISH_WORKER_INTERVAL_SECONDS: float = 5.0
    AICM_PUBLISH_WORKER_LEASE_SECONDS: int = 21600
    AICM_PUBLISH_WORKER_BATCH_LIMIT: int = 1
    # 开启训练且环境未安装 ultralytics 时，是否用当前 Python 自动执行 pip install（首次会下载较大依赖如 torch）
    YOLO_AUTO_INSTALL_ULTRALYTICS: bool = True
    # 新增/检查边端设备时缺少平台侧 SSH 依赖，自动安装后继续原探测与环境准备流程
    EDGE_SSH_AUTO_INSTALL_PARAMIKO: bool = True
    YOLO_TRAIN_WORK_SUBDIR: str = "yolo_train"
    YOLO_TRAIN_VAL_RATIO: float = 0.2
    YOLO_TRAIN_TEST_RATIO: float = 0.1
    YOLO_TRAIN_AUTO_TEST_VAL: bool = True
    YOLO_TRAIN_SPLIT_SEED: int = 42
    YOLO_TRAIN_DEVICE: str = ""
    # 多卡时按任务负载自动分配单卡/双卡（小任务并行各占一卡；大任务双卡 DDP）
    YOLO_TRAIN_GPU_AUTO_ALLOC: bool = True
    # load_score >= 此阈值时使用双卡（YOLO_TRAIN_GPU_PREFER_DUAL=false 时生效）
    YOLO_TRAIN_GPU_DUAL_SCORE_THRESHOLD: float = 4.0
    # 双卡池可用时本地训练默认双卡 DDP（不区分 yolo11n/s/m）；imgsz>=1024 仍单卡
    YOLO_TRAIN_GPU_PREFER_DUAL: bool = True
    # 开训前 GPU 空闲显存门槛：占总显存比例（小任务另有 YOLO_TRAIN_GPU_MIN_FREE_MB_SMALL 下限）
    YOLO_TRAIN_GPU_MIN_FREE_FRACTION: float = 0.15
    # 小任务（单卡 yolo11n/s 等）最低空闲显存 MB，避免 35% 阈值把可用卡挡在队外
    YOLO_TRAIN_GPU_MIN_FREE_MB_SMALL: int = 4096
    # 本地并发训练上限；0=自动（自动分配模式下等于训练 GPU 数量）
    YOLO_TRAIN_MAX_CONCURRENT_JOBS: int = 0
    # 可选：预训练 .pt 所在目录（如 /data/labeIU/labeIuproject/labeIU），优先于 ~/.config/Ultralytics/weights
    YOLO_PRETRAINED_WEIGHTS_DIR: str = ""
    # DataLoader 进程数；在 systemd 后台线程 + 小数据集场景建议 0，避免 workers=8 卡死
    YOLO_TRAIN_WORKERS: int = 0
    # 是否开启 AMP；False 可跳过首轮 AMP 自检（部分环境会长时间卡住且无 results.csv）
    YOLO_TRAIN_AMP: bool = False
    # 训练启动 watchdog：running 后长时间无 epoch/results 进展则终止子进程并标记失败
    YOLO_TRAIN_STALL_WATCHDOG_ENABLED: bool = True
    # 一期只读保留历史自动训练计划；完成真实连续轮次验收后才允许显式开启写入。
    AUTO_TRAINING_PLAN_WRITES_ENABLED: bool = False
    # 独立 7GAI Scheduler 部署时关闭 API 进程内自动训练 watchdog，避免重复调度
    AUTO_TRAINING_API_WATCHDOG_ENABLED: bool = True
    AUTO_TRAINING_WATCHDOG_SYNC_BATCH_SIZE: int = Field(
        20,
        ge=1,
        le=100,
        description="自动训练看门狗每批同步训练/编译/边端/发布记录的最大数量",
    )
    YOLO_TRAIN_START_STALL_TIMEOUT_SECONDS: int = 900
    # 文档 3.6.2：BM1688 导出完成后在部署机自动跑 pt/onnx 评估（bmodel 仍在边端）
    YOLO_SOP_SERVER_EVAL_ENABLED: bool = True
    YOLO_SOP_SERVER_EVAL_TIMEOUT_SEC: int = 7200
    YOLO_SOP_EVAL_CONF_THRES: float = 0.35
    YOLO_SOP_EVAL_IOU_THRES: float = 0.5

    YOLO_EVAL_ENABLED: bool = True
    # 训练报告包含曲线、指标和长文本，使用独立超时并仅重试暂时性网络错误。
    YOLO_EVAL_TIMEOUT_SECONDS: int = Field(default=600, ge=30, le=3600)
    YOLO_EVAL_MAX_RETRIES: int = Field(default=1, ge=0, le=2)
    # YOLO 训练 AI 评估：逗号分隔的 provider_key，暂不调用（如 qwen_235b）
    YOLO_EVAL_DISABLED_PROVIDERS: str = "qwen_235b"
    # 验证未达标时，按「下一次迭代建议」在同一数据集上自动续训（最多轮数，含首轮后的自动次数）
    YOLO_AUTO_ITERATION_ENABLED: bool = False
    YOLO_AUTO_ITERATION_MAX_ROUNDS: int = Field(
        5,
        ge=1,
        le=20,
        description="同一迭代链上未达标时最多自动发起训练的次数（达上限后需人工新建）",
    )
    # 非人为暂停（进程退出、部署重启、异常中断等）时自动从 last.pt 续训
    YOLO_AUTO_RESUME_TRAIN_ENABLED: bool = True
    YOLO_AUTO_RESUME_SCAN_INTERVAL_SECONDS: int = Field(
        90,
        ge=30,
        le=600,
        description="后台扫描可续训作业的间隔（秒）",
    )
    # 部署重启后自动恢复 PENDING/PROCESSING 的 BM1688 导出（ONNX 已存在则仅续编译）
    YOLO_BM1688_AUTO_RESUME_ENABLED: bool = True
    YOLO_BM1688_AUTO_RESUME_SCAN_INTERVAL_SECONDS: int = Field(
        120,
        ge=30,
        le=600,
        description="后台扫描可恢复 BM1688 导出的间隔（秒）",
    )
    # 训练完成并生成 best.pt 后自动创建 BM1688 导出任务；质量门禁只控制发布。
    YOLO_BM1688_AUTO_COMPILE_ON_TRAINING_PASSED: bool = True
    YOLO_BM1688_AUTO_COMPILE_SCAN_INTERVAL_SECONDS: int = Field(
        120,
        ge=30,
        le=600,
        description="扫描训练达标但缺少 BM1688 导出记录的间隔（秒）",
    )
    YOLO_BM1688_AUTO_COMPILE_BATCH_SIZE: int = Field(
        2,
        ge=1,
        le=20,
        description="每轮自动补偿创建 BM1688 导出任务的最大数量",
    )
    # 部署重启后自动恢复 PROCESSING 卡死的边端 SSH 验证任务
    YOLO_EDGE_VALIDATION_AUTO_RESUME_ENABLED: bool = True
    YOLO_EDGE_VALIDATION_STALE_MINUTES: int = Field(
        5,
        ge=1,
        le=120,
        description="边端验证 PROCESSING 超过该分钟数视为中断，可自动恢复",
    )
    YOLO_EDGE_VALIDATION_AUTO_RESUME_SCAN_INTERVAL_SECONDS: int = Field(
        90,
        ge=30,
        le=600,
        description="后台扫描可恢复边端验证任务的间隔（秒）",
    )
    # Missing Test/PT-BModel evidence is repaired automatically before manual review.
    YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_ENABLED: bool = True
    YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_MAX_ATTEMPTS: int = Field(
        3, ge=1, le=10, description="Maximum automatic quality-evidence attempts"
    )
    YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_INTERVAL_SECONDS: int = Field(
        60, ge=30, le=600, description="Quality-evidence watchdog interval in seconds"
    )
    YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_RETRY_DELAY_SECONDS: int = Field(
        60,
        ge=10,
        le=3600,
        description="Retry cooldown after a quality-evidence failure",
    )
    YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_LOOKBACK_DAYS: int = Field(
        30,
        ge=1,
        le=365,
        description="Lookback window for automatic quality-evidence recovery",
    )
    YOLO_QUALITY_EVIDENCE_AUTO_RECOVERY_BATCH_SIZE: int = Field(
        2, ge=1, le=20, description="Maximum train/export candidates per recovery scan"
    )
    STRATEGY_EVOLUTION_AUTO_ADVANCE_ENABLED: bool = True
    STRATEGY_EVOLUTION_AUTO_ADVANCE_SCAN_INTERVAL_SECONDS: int = Field(
        30,
        ge=10,
        le=600,
        description="策略演化自动编排扫描间隔（秒）",
    )
    STRATEGY_EVOLUTION_AUTOMATION_LEASE_SECONDS: int = Field(
        300,
        ge=60,
        le=3600,
        description="策略演化单次自动推进的数据库租约时间（秒）",
    )
    STRATEGY_EVOLUTION_CALLBACK_MAX_ATTEMPTS: int = Field(
        8,
        ge=1,
        le=20,
        description="策略演化结果回调最大尝试次数",
    )
    STRATEGY_EVOLUTION_CALLBACK_RETRY_BASE_SECONDS: int = Field(
        30,
        ge=10,
        le=3600,
        description="策略演化结果回调指数退避基础秒数",
    )
    STRATEGY_EVOLUTION_CALLBACK_RETRY_MAX_SECONDS: int = Field(
        1800,
        ge=30,
        le=86400,
        description="策略演化结果回调最大退避秒数",
    )
    # 模型列表「验证达标」判定：与 Ultralytics results.csv 末轮指标对比（可通过环境变量覆盖）
    YOLO_VAL_PASS_MAP5095: float = Field(
        0.5,
        description="验证集 mAP50-95 ≥ 该值判定为达标（0~1）",
    )
    YOLO_VAL_PASS_MAP50: float = Field(
        0.55,
        description="仅存在 mAP50 而无 mAP50-95 时使用的达标线（0~1）",
    )
    YOLO_GT_PRED_COMPARE_ENABLED: bool = Field(
        True,
        description="训练作业：逐图标注 vs 预测对比报告",
    )
    YOLO_GT_PRED_COMPARE_AUTO: bool = Field(
        True,
        description="训练完成后后台自动生成标注对比（可手动重跑）",
    )
    YOLO_GT_PRED_COMPARE_SPLITS: str = Field(
        "val,test,train",
        description="生成对比的数据划分，逗号分隔：val,test,train（USE_ALL_DATASET_SPLITS=false 时生效）",
    )
    YOLO_GT_PRED_COMPARE_USE_ALL_DATASET_SPLITS: bool = Field(
        True,
        description="对比与筛选一致入池时，默认覆盖训练导出 dataset 中所有划分（train+val+test=任务全量）",
    )
    YOLO_GT_PRED_COMPARE_MAX_IMAGES: int = Field(
        3000,
        ge=50,
        le=20000,
        description="单次生成最多处理的图片数（防止超大作业拖垮 GPU）",
    )
    YOLO_GT_PRED_COMPARE_CONF: float = Field(
        0.25,
        ge=0.01,
        le=0.99,
        description="对比推理默认 conf（作业 hyperparams 含 conf 时优先）",
    )
    YOLO_GT_PRED_COMPARE_IOU: float = Field(
        0.5,
        ge=0.1,
        le=0.95,
        description="逐图预测时的 NMS IoU 阈值，不再作为一致池框匹配标准",
    )
    YOLO_CONSISTENT_POOL_MATCH_IOU: float = Field(
        0.8,
        ge=0.5,
        le=0.99,
        description="一致池中预测框与人工框逐框匹配所需的最低 IoU",
    )
    YOLO_CONSISTENT_POOL_ENABLED: bool = Field(
        True,
        description="训练完成后按标签集合汇总无漏检、无误检且逐框达到匹配 IoU 的图片，并按文件名去重",
    )
    YOLO_CONSISTENT_POOL_AUTO: bool = Field(
        False,
        description="旧版兼容配置；训练完成后自动创建一致数据集任务已停用",
    )
    YOLO_REMOTE_TRAIN_ENABLED: bool = Field(
        True,
        description="允许将训练作业下发到 remote_train_nodes.json 配置的远程 GPU 节点",
    )
    YOLO_REMOTE_TRAIN_PULL_PAYLOAD: bool = Field(
        True,
        description="远程训练由节点经 HTTP 拉取 payload（支持断点续传）；False 时回退 SFTP 推送",
    )
    YOLO_REMOTE_TRAIN_PAYLOAD_COMPRESS: bool = Field(
        False,
        description="远程 payload 是否 gzip 压缩（JPEG 数据集收益小且打包慢，默认不压缩）",
    )
    YOLO_REMOTE_TRAIN_PAYLOAD_DOWNLOAD_HOST: str = Field(
        default="",
        description="远程节点拉取 payload 的 URL 根（内网更快时填，如 http://10.x.x.x:10000）；留空用 MEDIA_HOST",
    )
    YOLO_REMOTE_TRAIN_PAYLOAD_PARALLEL_WORKERS: int = Field(
        16,
        description="节点 Agent 并行 HTTP Range 下载线程数（0/1=单连接；内网建议 16）",
    )
    YOLO_REMOTE_TRAIN_PAYLOAD_NGINX_ACCEL: bool = Field(
        True,
        description="payload 下载经 nginx X-Accel-Redirect 直出磁盘（绕过 uvicorn 流式转发）",
    )
    YOLO_REMOTE_TRAIN_HMAC_SECRET: str = Field(
        default="",
        repr=False,
        description=(
            "远程训练 Agent 与平台共享的 HMAC-SHA256 密钥；只从环境变量读取，"
            "不得写入页面、作业清单或日志。生产环境必须配置。"
        ),
    )
    YOLO_REMOTE_TRAIN_HMAC_KEY_ID: str = Field(
        default="v1",
        description="远程训练 HMAC 密钥版本标识，轮换密钥时递增。",
    )
    YOLO_REMOTE_TRAIN_HMAC_REQUIRED: bool = Field(
        default=False,
        description=(
            "是否强制远程制品带 HMAC；APP_ENV=production 时即使此项为 false 也强制开启。"
        ),
    )
    YOLO_MODEL_MANIFEST_HMAC_SECRET: str = Field(
        default="",
        repr=False,
        description=(
            "平台内部 ModelManifest HMAC 密钥；仅用于服务端/受管 Worker，不能下发到 BM1688。"
        ),
    )
    YOLO_MODEL_MANIFEST_HMAC_KEY_ID: str = Field(
        default="v1",
        description="ModelManifest 内部签名密钥版本标识。",
    )
    YOLO_MODEL_MANIFEST_HMAC_REQUIRED: bool = Field(
        default=False,
        description=(
            "是否强制新模型清单带平台内部签名；生产环境即使为 false 也强制开启。"
        ),
    )
    EDGE_MANIFEST_ED25519_PRIVATE_KEY: str = Field(
        default="",
        repr=False,
        description="边端制品清单 Ed25519 私钥（仅服务器持有，可填 PEM/base64/hex）",
    )
    EDGE_MANIFEST_ED25519_KEY_ID: str = Field(
        default="edge-v1",
        description="边端制品清单 Ed25519 公钥编号，必须与设备预置公钥编号一致",
    )
    EDGE_MANIFEST_ED25519_REQUIRE: bool = Field(
        default=False,
        description="是否要求边端验证包必须有 Ed25519 签名；生产应开启",
    )

    # 算能 BM1688：Ultralytics 导出 ONNX；BModel 需在装有 TPU-MLIR 的环境编译（见 YOLOv11_det-TPU）
    YOLO_BM1688_EXPORT_SUBDIR: str = "yolo_bm1688_exports"
    YOLO_BM1688_COMPILE_ENABLED: bool = False
    YOLO_BM1688_COMPILE_COMMAND: str = Field(
        default="",
        description="可选：编译钩子，占位符 {onnx} {out_dir} {imgsz} {platform}；需在安装了算能工具链的环境中配置",
    )
    YOLO_BM1688_COMPILE_TIMEOUT_SECONDS: int = 7200
    # 部署脚本写入；用于自动填充 BM1688 编译页（无需手工配置）
    TPU_MLIR_ROOT: str = Field(
        default="",
        description="TPU-MLIR 根目录（含 envsetup.sh），deploy 默认同级 tpu-mlir",
    )
    SOPHON_DET_ROOT: str = Field(
        default="",
        description="sophon-demo YOLOv8_plus_det 例程根目录",
    )
    YOLO_BM1688_CALIBRATION_DATASET_DIR: str = Field(
        default="",
        description="INT8 校准集目录，默认可从 SOPHON_DET_ROOT/datasets/coco128 探测",
    )
    YOLO_BM1688_QTABLE_PATH: str = Field(
        default="",
        description="INT8 qtable 文件路径",
    )
    YOLO_BM1688_COMPILE_PLATFORMS: str = Field(
        default="bm1688",
        description="逗号分隔：bm1684x,bm1688,cv186x",
    )
    YOLO_BM1688_BMODEL_BASENAME: str = Field(
        default="yolov11n",
        description="bmodel 文件名前缀，如 yolov11n / yolov11s（与训练权重一致）",
    )
    YOLO_BM1688_USE_JOB_CALIBRATION: bool = Field(
        default=True,
        description="BM1688 导出时优先从本训练作业 images/train 抽样校准集（对齐文档 3.5）",
    )
    YOLO_BM1688_CALIBRATION_IMAGE_COUNT: int = Field(
        default=128,
        ge=8,
        le=2000,
        description="从训练集抽取的 INT8 校准图片数量",
    )

    # AICM 模型发布（页面 JSON 未保存时的 .env 默认值）
    YOLO_AICM_BASE_URL: str = Field(
        default="",
        description="AICM 后台根 URL，如 http://192.168.54.169:8102",
    )
    YOLO_AICM_USERNAME: str = Field(default="", description="AICM 登录用户名")
    YOLO_AICM_PASSWORD: str = Field(default="", description="AICM 登录密码")
    YOLO_AICM_REQUIRE_BMODEL_EVAL: bool = Field(
        default=False,
        description="为 True 时在发布日志中强调 INT8 SOP 门控结果；未达标不再阻断 AICM 发布",
    )

    # 算能盒板端 Agent：内网 192.168.x.x 主动拉取任务（公网无需访问板端）
    BOARD_AGENT_ENABLED: bool = Field(
        default=False,
        description="BM1688 导出完成后是否自动创建板端验证任务",
    )
    BOARD_AGENT_TOKEN: str = Field(
        default="",
        description="板端 Agent 鉴权 Token（请求头 X-Board-Agent-Token）",
    )
    BOARD_AGENT_PRIMARY_ENABLED: bool = Field(
        default=False,
        description=(
            "启用后板端主动拉取使用统一 yolo_edge_validation_job 主链；"
            "关闭时保留历史 yolo_board_validation 兼容链"
        ),
    )
    BOARD_AGENT_TASK_TOKEN_SECRET: str = Field(
        default="",
        repr=False,
        description="板端统一验证任务短期令牌签名密钥；为空时仅兼容使用 BOARD_AGENT_TOKEN",
    )
    BOARD_AGENT_TASK_TOKEN_TTL_SECONDS: int = Field(
        default=900,
        ge=60,
        le=86400,
        description="板端统一验证任务令牌有效期（秒）",
    )
    BOARD_AGENT_HEARTBEAT_TTL_SECONDS: int = Field(
        default=120,
        ge=10,
        le=86400,
        description="板端设备调度使用的最后心跳有效期（秒）",
    )
    SEVEN_GAI_AGENT_ENROLLMENT_TOKEN: str = Field(
        default="",
        description="7GAI 通用边端 Agent 注册凭据；为空时兼容使用 BOARD_AGENT_TOKEN",
    )
    SEVEN_GAI_AGENT_OTA_SIGNING_KEY: str = Field(
        default="",
        description="OTA 产物 HMAC-SHA256 签名密钥；配置后强制校验 release signature",
    )
    SEVEN_GAI_AGENT_REQUIRE_OTA_SIGNATURE: bool = Field(
        default=False,
        description="生产环境启用 OTA 签名硬门禁；开启后必须配置签名密钥且 Release 必须携带有效签名",
    )
    SEVEN_GAI_AGENT_REQUIRE_PROJECT: bool = Field(
        default=False,
        description="启用后新 Agent 注册和 OTA Release 必须绑定项目；关闭时保留历史兼容模式",
    )
    SEVEN_GAI_AGENT_OFFLINE_TTL_SECONDS: int = Field(
        default=120,
        ge=1,
        le=86400,
        description="设备监控读模型将最后心跳超过该秒数的 ONLINE Agent 显示为 OFFLINE；不改写控制面状态",
    )
    SEVEN_GAI_RULE_SIGNING_KEYS_JSON: str = Field(
        default="",
        repr=False,
        description=(
            "规则发布 HMAC-SHA256 验签 key map，JSON 格式为 key_id -> secret；"
            "仅用于服务端发布契约验证，不代表边端设备已经验签"
        ),
    )
    SEVEN_GAI_RULE_REQUIRE_SIGNATURE: bool = Field(
        default=False,
        description="启用后规则版本发布必须携带可由服务端验证的签名信封",
    )
    BOARD_AGENT_PUBLIC_BASE_URL: str = Field(
        default="",
        description="公网 LabelU 根 URL，供板端 Agent 拼下载地址；留空则用 MEDIA_HOST",
    )
    EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED: bool = Field(
        default=True,
        description=(
            "新建或补全 SSH 边端设备后是否自动引导常驻 Board Agent；"
            "仅在 BOARD_AGENT_ENABLED、BOARD_AGENT_PRIMARY_ENABLED 和 Token 均开启时生效"
        ),
    )
    BOARD_AGENT_EVAL_MAX_IMAGES: int = Field(
        default=32,
        description="打入 models.zip 的板端验证图数量上限（来自训练 val/test 或任务样本）",
    )

    EDGE_PERFORMANCE_WARMUP_COUNT: int = Field(
        default=50, ge=0, le=10000, description="BM1688 性能基准预热次数"
    )
    EDGE_PERFORMANCE_REPEAT_COUNT: int = Field(
        default=1000, ge=1, le=100000, description="BM1688 性能基准正式重复次数"
    )
    EDGE_PERFORMANCE_RESOURCE_INTERVAL_SECONDS: float = Field(
        default=1.0, ge=0.2, le=60.0, description="边端资源序列采样间隔"
    )
    EDGE_PERFORMANCE_STABILITY_SECONDS: int = Field(
        default=0,
        ge=0,
        le=86400,
        description="INT8 长稳运行秒数；0 表示不执行长稳基准",
    )
    YOLO_EDGE_VALIDATION_MAX_IMAGES: int = Field(default=50, ge=1, le=500)
    YOLO_EDGE_VALIDATION_DEDICATED_WORKER_ENABLED: bool = Field(
        default=False,
        description="独立边端验证 Worker；开启后 Web 只分配设备，不在进程内执行 SSH/推理",
    )
    YOLO_EDGE_VALIDATION_DEDICATED_WORKER_INTERVAL_SECONDS: float = Field(
        default=5.0, ge=0.5, le=300
    )
    YOLO_EDGE_VALIDATION_DEDICATED_WORKER_BATCH_LIMIT: int = Field(
        default=1, ge=1, le=16
    )
    YOLO_EDGE_VALIDATION_EXTERNAL_CLEANUP_ENABLED: bool = Field(
        default=True,
    )
    YOLO_EDGE_VALIDATION_EXTERNAL_RETENTION_HOURS: int = Field(default=24, ge=1, le=720)
    YOLO_EDGE_VALIDATION_EXTERNAL_CLEANUP_INTERVAL_SECONDS: int = Field(
        default=1800, ge=300, le=86400
    )
    EDGE_RUNTIME_AUTO_PROVISION_ENABLED: bool = Field(
        default=True,
        description="手动边端验证前是否自动预检并用离线包修复可恢复的 BM1688 环境",
    )
    EDGE_REMOTE_ROOT: str = Field(
        default="/data/labelu_edge",
        description="边端所有 LabelU 文件的唯一允许写入根目录",
    )
    EDGE_RUNTIME_OFFLINE_ARCHIVE: str = Field(
        default="",
        description="LabelU 服务端已有的 BM1688 离线运行时包路径",
    )
    EDGE_RUNTIME_OFFLINE_ARCHIVE_URL: str = Field(
        default="",
        description="可选：由 LabelU 服务端下载并缓存的 BM1688 离线运行时包 URL",
    )
    EDGE_RUNTIME_OFFLINE_SHA256: str = Field(
        default="",
        description="可选：BM1688 离线运行时包的 SHA256 完整性校验值",
    )
    EDGE_RUNTIME_REMOTE_ROOT: str = Field(
        default="/data/labelu_edge/runtime",
        description="兼容字段；实际运行时目录由 EDGE_REMOTE_ROOT 派生",
    )
    EDGE_RUNTIME_MIN_FREE_MB: int = Field(default=2048, ge=512, le=102400)
    EDGE_RUNTIME_MIN_TMP_FREE_MB: int = Field(default=256, ge=64, le=10240)
    EDGE_RUNTIME_MIN_DOCKER_FREE_MB: int = Field(default=512, ge=128, le=102400)
    EDGE_RUNTIME_MIN_DATA_FREE_MB: int = Field(default=2048, ge=512, le=102400)
    EDGE_RUNTIME_MIN_FREE_INODES: int = Field(default=1024, ge=128, le=1000000)
    EDGE_RUNTIME_AUTO_LOG_CLEANUP_ENABLED: bool = Field(
        default=True,
        description="空间体检不通过时，仅清理告警文件系统内允许名单中的过期轮转日志",
    )
    EDGE_RUNTIME_LOG_RETENTION_DAYS: int = Field(default=7, ge=1, le=90)
    EDGE_RUNTIME_JOURNAL_MAX_MB: int = Field(default=256, ge=64, le=2048)
    EDGE_RUNTIME_PROVISION_TIMEOUT_SEC: int = Field(default=3600, ge=300, le=14400)
    EDGE_REMOTE_FAILED_JOB_RETENTION_HOURS: int = Field(default=24, ge=1, le=720)
    EDGE_REMOTE_CLEANUP_INTERVAL_SECONDS: int = Field(default=1800, ge=300, le=86400)

    # 导入图片时运行聚类/离群/子场景分析（可选依赖 image-pipeline）；不改文件名与导入后流程
    IMAGE_PIPELINE_ENABLED: bool = False
    IMAGE_PIPELINE_MAX_IMPORT_BATCH: int = Field(
        default=80,
        description="批量建样本时超过该数量则跳过聚类/离群等导入分析，避免大批量导入超时",
    )
    IMAGE_PIPELINE_CONFIG_PATH: str = Field(
        default="",
        description="可选：外部 JSON 覆盖内置默认；留空则完全使用代码内默认，无需维护配置文件",
    )
    IMAGE_PIPELINE_REJECT_INVALID_IMAGE: bool = Field(
        default=True,
        description="上传时无法解码的图片是否拒绝入库",
    )
    IMAGE_PIPELINE_AUTO_INSTALL_DEPS: bool = Field(
        default=True,
        description="开启流水线且缺少 opencv/sklearn 时，是否用当前 Python 自动 pip install",
    )
    IMAGE_PIPELINE_AUTO_CONFIGURE_ENV: bool = Field(
        default=True,
        description="若 .env 中未声明 IMAGE_PIPELINE_ENABLED，启动时自动写入 true（已显式 false 则不改动）",
    )

    EXPERIMENT_DATASET_MIN_SAMPLE_COUNT: int = Field(
        default=50,
        ge=1,
        le=100000,
        description="Minimum valid images required before creating an experiment dataset task.",
    )
    EXPERIMENT_DATASET_DEDICATED_WORKER_ENABLED: bool = Field(
        default=False,
        description="第三方 S3 问题数据回流是否由独立 Worker 消费；开启后 Web 进程不执行导入副作用。",
    )
    EXPERIMENT_DATASET_WORKER_INTERVAL_SECONDS: int = Field(
        default=5,
        ge=1,
        le=300,
        description="第三方 S3 回流 Worker 扫描间隔。",
    )
    EXPERIMENT_DATASET_WORKER_LEASE_SECONDS: int = Field(
        default=300,
        ge=30,
        le=86400,
        description="第三方 S3 回流 Worker 数据库租约时长。",
    )
    EXPERIMENT_DATASET_WORKER_BATCH_SIZE: int = Field(
        default=1,
        ge=1,
        le=20,
        description="第三方 S3 回流 Worker 单轮最多领取的任务数。",
    )
    EXPERIMENT_DATASET_WAITING_REQUEUE_SECONDS: int = Field(
        default=30,
        ge=1,
        le=86400,
        description="第三方 S3 回流等待状态的最短重试间隔，避免未满足条件时空转。",
    )

    DATABASE_URL: str = Field(
        default="",
        description=(
            "数据库连接串（SQLite / MySQL）。留空则使用 sqlite:///BASE_DATA_DIR/labelu.sqlite"
        ),
    )
    LABELU_DEV_DATABASE_URL: str = Field(
        default="",
        description=(
            "开发模式显式数据库连接串；仅在 APP_ENV=development/test 时覆盖 DATABASE_URL，"
            "避免开发进程意外继承 .env 的共享数据库配置。"
        ),
    )
    LABELU_ALLOW_DEV_MYSQL: bool = Field(
        default=False,
        description="开发模式连接 MySQL 的显式开关；默认拒绝，联调共享库时必须主动开启。",
    )
    # or using MySQL DATABASE_URL=mysql://labelu:labelupass@localhost/labeludb
    DATABASE_POOL_SIZE: int = Field(
        default=20,
        description=(
            "MySQL QueuePool 常驻连接数（仅 mysql URL 生效）。"
            "多人并发 + YOLO 训练时默认 5 易耗尽，生产建议 20 起。"
        ),
    )
    DATABASE_MAX_OVERFLOW: int = Field(
        default=30,
        description="MySQL 连接池临时溢出连接数（与 POOL_SIZE 相加为峰值并发占用上限）。",
    )
    DATABASE_POOL_TIMEOUT: int = Field(
        default=60,
        description="从连接池获取连接的最长等待秒数，超时抛出 QueuePool TimeoutError。",
    )
    DATABASE_POOL_RECYCLE: int = Field(
        default=3600,
        description="连接回收间隔（秒），避免 MySQL wait_timeout 断开闲置连接。",
    )

    PASSWORD_SECRET_KEY: str = Field(
        default="",
        description="JWT secret key. Generate with: openssl rand -hex 32. MUST be set in production.",
    )

    TOKEN_GENERATE_ALGORITHM: str = "HS256"
    # 登录 JWT 有效期（分钟）。默认 7 天，避免短时间无操作后因 token 过期被踢回登录页。
    # 生产环境可按安全策略改短，或通过环境变量覆盖，例如 TOKEN_ACCESS_EXPIRE_MINUTES=60
    TOKEN_ACCESS_EXPIRE_MINUTES: int = 10080
    TOKEN_TYPE: str = "Bearer"
    EXPOSE_INTERNAL_ERRORS: bool = False

    # 逗号分隔的用户名（须与 user.username 一致，一般为邮箱）。这些账号可访问任意任务（列表、标注、YOLO 等），
    # 无需被添加为协同标注；亦可删任务、管理协同成员。默认与内置默认管理员一致；多租户生产环境请设为空或显式名单。
    # 后海安防老平台对接
    HOUHAI_BASE_URL: str = ""
    HOUHAI_API_TOKEN: str = ""
    HOUHAI_VERIFY_SSL: str = "true"

    TASK_SUPERUSER_USERNAMES: str = Field(
        default="admin@123.com",
        description="Comma-separated usernames with unrestricted task access.",
    )

    @model_validator(mode="after")
    def _derive_media_and_sqlite_url(self):
        """保证 MEDIA_ROOT、默认 SQLite URL 与 BASE_DATA_DIR 一致（覆盖仅改 BASE_DATA_DIR 时的旧行为）。"""
        base = Path(self.BASE_DATA_DIR)
        if self.MEDIA_ROOT is None:
            object.__setattr__(self, "MEDIA_ROOT", base / "media")
        db_url = (self.DATABASE_URL or "").strip()
        explicit_dev_url = (self.LABELU_DEV_DATABASE_URL or "").strip()
        if explicit_dev_url and (self.APP_ENV or "").strip().lower() in {
            "development",
            "test",
        }:
            db_url = explicit_dev_url
            object.__setattr__(self, "DATABASE_URL", db_url)
        if db_url.lower().startswith("mysql"):
            from labelu.internal.common.mysql_url import normalize_mysql_database_url

            normalized = normalize_mysql_database_url(db_url)
            if normalized and normalized != db_url:
                object.__setattr__(self, "DATABASE_URL", normalized)
                db_url = normalized
        if not db_url:
            db_file = (base / "labelu.sqlite").resolve()
            object.__setattr__(self, "DATABASE_URL", "sqlite:///" + db_file.as_posix())
        if not (self.MEDIA_HOST or "").strip():
            host = self.HOST
            if host in ("0.0.0.0", "::", "[::]", "[::1]"):
                host = "127.0.0.1"
            object.__setattr__(
                self,
                "MEDIA_HOST",
                f"{self.SCHEME}://{host}:{self.PORT}",
            )
        return self

    @property
    def need_migration_to_mysql(self) -> bool:
        sqlite_path = Path(self.BASE_DATA_DIR) / "labelu.sqlite"
        return self.DATABASE_URL.startswith("mysql") and sqlite_path.exists()

    @property
    def effective_log_dir(self) -> Path:
        custom = (self.LOG_DIR or "").strip()
        if custom:
            return Path(custom).expanduser().resolve()
        return Path(self.BASE_DATA_DIR).resolve() / "logs"


settings = Settings()
os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
_env = (os.environ.get("LABELU_ENV_FILE") or "").strip() or "(未指定，使用默认路径)"
logger.info(
    "LabelU paths: BASE_DATA_DIR={} MEDIA_ROOT={} env_file={}",
    settings.BASE_DATA_DIR,
    settings.MEDIA_ROOT,
    _env,
)
if settings.DATABASE_URL.startswith("mysql"):
    logger.info(
        "LabelU MySQL pool: size={} max_overflow={} timeout={}s recycle={}s",
        settings.DATABASE_POOL_SIZE,
        settings.DATABASE_MAX_OVERFLOW,
        settings.DATABASE_POOL_TIMEOUT,
        settings.DATABASE_POOL_RECYCLE,
    )
