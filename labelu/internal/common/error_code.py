from enum import Enum

from loguru import logger
from fastapi.responses import JSONResponse, FileResponse
from fastapi import FastAPI, Request, status, HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from labelu.internal.common.config import settings

import os
from importlib.resources import files

# common init error code
COMMON_INIT_CODE = 30000

# user init error code
USER_INIT_CODE = 40000

# task init error code
TASK_INIT_CODE = 50000

# export
EXPORT_INIT_CODE = 60000

UNEXPECTED_ERROR_CODE = 99999


class ErrorCode(Enum):
    """
    business error code
    """
    UNEXPECTED_ERROR = (
        UNEXPECTED_ERROR_CODE,
        "服务器内部错误，请稍后重试",
    )

    # common init error code
    CODE_30000_SQL_ERROR = (
        COMMON_INIT_CODE,
        "数据库执行出错",
    )
    CODE_30001_NO_PERMISSION = (
        COMMON_INIT_CODE + 1,
        "无权限执行此操作",
    )
    CODE_30002_VALIDATION_ERROR = (
        COMMON_INIT_CODE + 2,
        "请求参数校验失败",
    )
    CODE_30003_CLIENT_ERROR = (
        COMMON_INIT_CODE + 3,
        "请求无效",
    )
    CODE_30004_PHASE_ONE_CAPABILITY_RETIRED = (
        COMMON_INIT_CODE + 4,
        "该能力已下线，请使用任务、训练、BM1688 编译、真机验证和模型发布主流程",
    )

    # user error code
    CODE_40000_USERNAME_OR_PASSWORD_INCORRECT = (
        USER_INIT_CODE,
        "用户名或密码错误",
    )
    CODE_40001_USERNAME_ALREADY_EXISTS = (
        USER_INIT_CODE + 1,
        "该用户名已被注册",
    )
    CODE_40002_USER_NOT_FOUND = (USER_INIT_CODE + 2, "用户不存在")
    CODE_40003_CREDENTIAL_ERROR = (
        USER_INIT_CODE + 3,
        "登录已失效或凭证无效，请重新登录",
    )
    CODE_40004_NOT_AUTHENTICATED = (
        USER_INIT_CODE + 4,
        "未登录或登录已失效",
    )
    CODE_40005_SELF_SIGNUP_DISABLED = (
        USER_INIT_CODE + 5,
        "系统不开放用户自行注册，请联系管理员创建账号",
    )

    # task error code
    CODE_50000_TASK_ERROR = (TASK_INIT_CODE, "任务处理出错")
    CODE_50001_TASK_FINISHED_ERROR = (TASK_INIT_CODE + 1, "任务已完成，无法继续该操作")
    CODE_50002_TASK_NOT_FOUND = (TASK_INIT_CODE + 2, "任务不存在")
    CODE_50003_COLLABORATOR_ALREADY_EXISTS = (
        TASK_INIT_CODE + 3,
        "协作者已在任务中",
    )

    CODE_50004_COLLABORATOR_NOT_FOUND = (
        TASK_INIT_CODE + 4,
        "未找到该协作者",
    )
    # task attachment error code
    CODE_51000_CREATE_ATTACHMENT_ERROR = (
        TASK_INIT_CODE + 1000,
        "上传附件失败，文件保存出错",
    )
    CODE_51001_TASK_ATTACHMENT_NOT_FOUND = (
        TASK_INIT_CODE + 1001,
        "附件不存在",
    )

    CODE_51002_TASK_ATTACHMENT_ALREADY_EXISTS = (
        TASK_INIT_CODE + 1002,
        "附件已存在",
    )
    CODE_51003_TASK_ATTACHMENT_INVALID_IMAGE = (
        TASK_INIT_CODE + 1003,
        "图片无法读取或已损坏",
    )
    # task sample error code
    CODE_55000_SAMPLE_LIST_PARAMETERS_ERROR = (
        TASK_INIT_CODE + 5000,
        "列表参数错误：after、before、page 只能任选其一；若使用分页，page 可为 0",
    )
    CODE_55001_SAMPLE_NOT_FOUND = (TASK_INIT_CODE + 5001, "样本不存在")
    CODE_55002_SAMPLE_FORMAT_ERROR = (
        TASK_INIT_CODE + 5002,
        "样本结果格式错误",
    )
    CODE_55002_SAMPLE_NAME_EXISTS = (
        TASK_INIT_CODE + 5003,
        "样本名称已存在",
    )
    CODE_56000_AUTO_LABEL_DISABLED = (
        TASK_INIT_CODE + 6000,
        "未开启 AI 自动标注，请在环境变量中设置 AI_AUTO_LABEL_ENABLED=true",
    )
    CODE_56001_AUTO_LABEL_UNSUPPORTED_MEDIA = (
        TASK_INIT_CODE + 6001,
        "AI 标注仅支持图片任务",
    )
    CODE_56002_AUTO_LABEL_NO_LABELS_CONFIGURED = (
        TASK_INIT_CODE + 6002,
        "任务未配置可用于 AI 的标注工具或标签，请先配置工具与标签",
    )
    CODE_56003_AUTO_LABEL_MODEL_ERROR = (
        TASK_INIT_CODE + 6003,
        "AI 模型服务请求失败",
    )
    CODE_56004_AUTO_LABEL_NOT_CONFIGURED = (
        TASK_INIT_CODE + 6004,
        "未配置 AI 模型地址，请在配置中设置 AI_MODEL_ENDPOINT",
    )
    CODE_56005_AUTO_LABEL_INVALID_RESPONSE = (
        TASK_INIT_CODE + 6005,
        "AI 模型返回了不支持的标注工具类型",
    )
    CODE_56006_AUTO_LABEL_NO_SAMPLES = (
        TASK_INIT_CODE + 6006,
        "当前没有符合批量 AI 标注条件的样本（默认仅包含「未标注」样本）。"
        "若需对已标注样本运行，请在请求中设置 include_done_samples=true",
    )
    CODE_56007_AUTO_LABEL_VISION_CHAIN_NOT_CONFIGURED = (
        TASK_INIT_CODE + 6007,
        "视觉大模型链路未就绪，请在「系统管理→模型接入」配置、启用并验证火山方舟 Coding Plan",
    )
    CODE_56008_SAMPLE_BOUNDS_CHECK_UNSUPPORTED_MEDIA = (
        TASK_INIT_CODE + 6008,
        "标框越界检查仅支持图片任务",
    )
    CODE_56009_SAMPLE_BOUNDS_CHECK_JOB_NOT_FOUND = (
        TASK_INIT_CODE + 6009,
        "未找到该标框越界检查任务",
    )
    CODE_57000_YOLO_TRAIN_DISABLED = (
        TASK_INIT_CODE + 7000,
        "未开启 YOLO 训练，请设置 YOLO_TRAIN_ENABLED=true",
    )
    CODE_57001_YOLO_TRAIN_TASK_NOT_FINISHED = (
        TASK_INIT_CODE + 7001,
        "任务仍为草稿，请先导入数据并完成工具配置后再训练",
    )
    CODE_57002_YOLO_TRAIN_NO_SAMPLES = (
        TASK_INIT_CODE + 7002,
        "没有可用于训练的已完成样本或矩形框标注",
    )
    CODE_57003_YOLO_TRAIN_ULTRALYTICS_MISSING = (
        TASK_INIT_CODE + 7003,
        "未安装或无法加载 ultralytics。已尝试自动安装失败时请手动执行：pip install ultralytics；"
        "若不希望启动时自动下载依赖，请在环境变量中设置 YOLO_AUTO_INSTALL_ULTRALYTICS=false 并自行安装",
    )
    CODE_57020_TAGTOOL_CLASSIFICATION_DEPS_MISSING = (
        TASK_INIT_CODE + 7020,
        "未安装或无法加载 TagTool 分类预标注依赖，请安装：pip install torch torchvision",
    )
    CODE_57004_YOLO_TRAIN_JOB_NOT_FOUND = (
        TASK_INIT_CODE + 7004,
        "未找到该 YOLO 训练任务",
    )
    CODE_57005_YOLO_TRAIN_UNSUPPORTED_MEDIA = (
        TASK_INIT_CODE + 7005,
        "YOLO 训练仅支持图片任务",
    )
    CODE_57006_YOLO_EVAL_DISABLED = (
        TASK_INIT_CODE + 7006,
        "未开启 YOLO AI 评估，请设置 YOLO_EVAL_ENABLED=true",
    )
    CODE_57007_YOLO_EVAL_VISION_NOT_CONFIGURED = (
        TASK_INIT_CODE + 7007,
        "评估所需的视觉模型 API 未配置（请检查 .env 中的 VISION_* 配置）",
    )
    CODE_57008_YOLO_EVAL_JOB_NOT_COMPLETED = (
        TASK_INIT_CODE + 7008,
        "请先等待 YOLO 训练任务完成后再进行 AI 评估",
    )
    CODE_57009_YOLO_EVAL_ALREADY_RUNNING = (
        TASK_INIT_CODE + 7009,
        "该训练任务下同类评估报告正在生成中，请稍后再试",
    )
    CODE_57010_YOLO_EVAL_INVALID_KIND = (
        TASK_INIT_CODE + 7010,
        "report_kind 必须为 full_analysis 或 next_iteration",
    )
    CODE_57011_YOLO_EVAL_REPORT_NOT_FOUND = (
        TASK_INIT_CODE + 7011,
        "未找到该评估报告或无权访问",
    )
    CODE_57012_YOLO_IMPORT_INVALID_FILE = (
        TASK_INIT_CODE + 7012,
        "导入失败：仅支持 .pt / .pth 权重文件，且大小不能超过服务器上传限制",
    )
    CODE_57013_YOLO_IMPORT_NOT_FOUND = (
        TASK_INIT_CODE + 7013,
        "未找到该导入模型或无权访问",
    )
    CODE_57014_YOLO_BM1688_EXPORT_NOT_FOUND = (
        TASK_INIT_CODE + 7014,
        "未找到该 BM1688 导出记录或无权访问",
    )
    CODE_57015_YOLO_BM1688_EXPORT_BUSY = (
        TASK_INIT_CODE + 7015,
        "该训练作业已有导出任务进行中，请稍后再试",
    )
    CODE_57016_YOLO_BM1688_FILE_NOT_READY = (
        TASK_INIT_CODE + 7016,
        "该导出记录中对应文件尚未生成或不可用",
    )
    CODE_57017_YOLO_PREANNOTATE_NO_RECT = (
        TASK_INIT_CODE + 7017,
        "任务未配置矩形框（rectTool）类别，无法与 YOLO 类别对齐；请先配置拉框标签",
    )
    CODE_57018_YOLO_PREANNOTATE_NO_SAMPLES = (
        TASK_INIT_CODE + 7018,
        "当前没有符合批量预标注条件的样本（默认仅「未标注」）。"
        "若需对已标注样本运行，请设置 include_done_samples=true",
    )
    CODE_57019_YOLO_PREANNOTATE_JOB_NOT_FOUND = (
        TASK_INIT_CODE + 7019,
        "未找到该 YOLO 预标注作业",
    )
    CODE_57020_YOLO_TRAIN_JOB_BUSY = (
        TASK_INIT_CODE + 7020,
        "训练作业正在排队或训练中，或存在进行中的 BM1688 导出，请结束后再删除",
    )
    CODE_57021_YOLO_TRAIN_JOB_NOT_PAUSABLE = (
        TASK_INIT_CODE + 7021,
        "仅排队中或训练中的作业可暂停",
    )
    CODE_57022_BOARD_AGENT_DISABLED = (
        TASK_INIT_CODE + 7022,
        "板端 Agent 未启用，请在服务器 .env 配置 BOARD_AGENT_ENABLED 与 BOARD_AGENT_TOKEN",
    )
    CODE_57023_BOARD_AGENT_UNAUTHORIZED = (
        TASK_INIT_CODE + 7023,
        "板端 Agent Token 无效",
    )
    CODE_57024_BOARD_VALIDATION_NOT_FOUND = (
        TASK_INIT_CODE + 7024,
        "未找到板端验证任务",
    )
    CODE_57025_BOARD_VALIDATION_BUSY = (
        TASK_INIT_CODE + 7025,
        "板端验证进行中，请稍后再试",
    )
    CODE_57026_YOLO_TRAIN_MERGE_INCOMPATIBLE = (
        TASK_INIT_CODE + 7026,
        "合并训练的任务类别配置不一致",
    )
    CODE_57027_YOLO_AICM_CONFIG_INCOMPLETE = (
        TASK_INIT_CODE + 7027,
        "AICM 发布配置不完整",
    )
    CODE_57028_YOLO_AICM_REMOTE_ERROR = (
        TASK_INIT_CODE + 7028,
        "AICM 后台请求失败",
    )
    CODE_57029_EDGE_SSH_VALIDATION_DISABLED = (
        TASK_INIT_CODE + 7029,
        "边端 SSH 验证未就绪，请在边端设备配置页新增并启用设备",
    )
    CODE_57030_YOLO_REMOTE_TRAIN_DISABLED = (
        TASK_INIT_CODE + 7030,
        "远程训练未启用",
    )
    CODE_57031_YOLO_REMOTE_TRAIN_NODE_INVALID = (
        TASK_INIT_CODE + 7031,
        "远程训练节点无效或未通过连通性测试",
    )
    CODE_57032_EDGE_SSH_VALIDATION_BUSY = (
        TASK_INIT_CODE + 7032,
        "边端 SSH 验证正在进行中",
    )
    CODE_57033_YOLO_PREANNOTATE_MODEL_MISMATCH = (
        TASK_INIT_CODE + 7033,
        "YOLO pre-annotation model labels do not match the current task labels",
    )
    CODE_57100_AUTO_TRAIN_PLAN_NOT_FOUND = (57100, "自动训练计划不存在或不可见")
    CODE_57101_AUTO_TRAIN_PLAN_CONFIG_INCOMPLETE = (57101, "自动训练计划配置不完整")
    CODE_57102_AUTO_TRAIN_PLAN_CONFLICT = (57102, "自动训练计划版本或状态冲突")
    CODE_57103_AUTO_TRAIN_PLAN_ACTIVE_ROUND = (57103, "自动训练计划已有活动轮次")
    CODE_57104_AUTO_TRAIN_NO_QUALIFIED_TASK = (57104, "没有达到单任务门槛的数据")
    CODE_57105_AUTO_TRAIN_REVIEW_REQUIRED = (57105, "第三方样本仍需人工审核")
    CODE_57106_AUTO_TRAIN_COVERAGE_INSUFFICIENT = (57106, "标签或组件覆盖不足")
    CODE_57107_AUTO_TRAIN_POLICY_NOT_APPROVED = (57107, "质量策略未批准或已变更")
    CODE_57108_AUTO_TRAIN_EVAL_DATASET_NOT_APPROVED = (57108, "评估数据集未批准")
    CODE_57109_AUTO_TRAIN_DUPLICATE_EXECUTION = (57109, "相同数据和配置的执行已存在")
    CODE_57110_AUTO_TRAIN_INVALID_TRANSITION = (57110, "自动训练状态迁移非法")
    CODE_57111_AUTO_TRAIN_QUALITY_EVIDENCE_INSUFFICIENT = (57111, "质量证据不足")
    CODE_57112_AUTO_TRAIN_STAGE_GATE_BLOCKED = (57112, "自动阶段质量门未通过")
    CODE_57113_AUTO_TRAIN_HUMAN_PUBLISH_REQUIRED = (57113, "正式发布必须由管理员人工确认")
    CODE_57114_AUTO_TRAIN_REVIEW_HASH_CONFLICT = (57114, "审核标注哈希与当前标注不一致")
    CODE_57115_AUTO_TRAIN_DATA_GRANT_REQUIRED = (57115, "训练数据授权缺失或失效")
    CODE_57116_AUTO_TRAIN_SCAN_JOB_NOT_FOUND = (57116, "自动训练扫描任务不存在")
    CODE_57117_AUTO_TRAIN_STAGE_BUSY = (57117, "自动训练阶段正在执行")
    CODE_57118_AUTO_TRAIN_QUALITY_REACHED_ACK_REQUIRED = (57118, "恢复已达标计划前必须明确确认")
    CODE_57119_AUTO_TRAIN_FEATURE_STAGE_DISABLED = (57119, "自动训练技术阶段未启用")
    CODE_57120_AUTO_TRAIN_EVALUATION_RUN_NOT_FOUND = (57120, "自动训练评估任务不存在")
    CODE_57121_AUTO_TRAIN_EVALUATION_RUN_CONFLICT = (57121, "自动训练评估任务租约或状态冲突")
    CODE_57122_AUTO_TRAIN_PLAN_NOT_OPEN = (
        57122,
        "自动训练计划一期暂未开放，历史记录仅供查看",
    )
    CODE_61000_NO_DATA = (
        EXPORT_INIT_CODE + 1000,
        "没有可导出的数据",
    )
    CODE_62000_S3_IMPORT_TOO_MANY = (
        EXPORT_INIT_CODE + 2000,
        "导入文件数量过多（单次最多 10000 个）",
    )
    CODE_62001_S3_IMPORT_NO_MATCH = (
        EXPORT_INIT_CODE + 2001,
        "在指定前缀下未找到匹配的文件",
    )
    CODE_62002_S3_REQUEST_FAILED = (
        EXPORT_INIT_CODE + 2002,
        "连接 S3 存储服务失败",
    )




def _public_error_message(default_message: str, exc: Exception) -> str:
    if settings.EXPOSE_INTERNAL_ERRORS:
        return str(exc)
    return default_message


def _request_context(request: Request) -> str:
    method = getattr(request, "method", "UNKNOWN")
    path = request.url.path if getattr(request, "url", None) else "UNKNOWN"
    return f"{method} {path}"


def _spa_index_path() -> str | None:
    path = os.path.join(
        str(files("labelu.internal").joinpath("statics")),
        "index.html",
    )
    return path if os.path.isfile(path) else None


def _is_spa_navigation_request(request: Request) -> bool:
    if getattr(request, "method", "GET").upper() not in {"GET", "HEAD"}:
        return False
    path = request.url.path if getattr(request, "url", None) else ""
    if not path:
        return False
    if path.startswith(
        (
            settings.API_V1_STR,
            "/internal",
            "/ws",
            "/assets",
            "/@vite",
            "/__vite",
            "/node_modules",
        )
    ):
        return False
    if path in ("/favicon.ico", "/favicon.svg"):
        return False
    last_part = path.rsplit("/", 1)[-1]
    if "." in last_part:
        return False
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        return True
    return (request.headers.get("sec-fetch-dest") or "").lower() == "document"


class LabelUException(HTTPException):
    def __init__(
        self, code: ErrorCode, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, message: str = None
    ):
        self.msg = message or code.value[1]
        self.code = code.value[0]
        super().__init__(status_code=status_code, detail=self.msg)


async def labelu_exception_handler(request: Request, exc: LabelUException):
    logger.warning("{} - {}", _request_context(request), exc)
    return JSONResponse(
        status_code=exc.status_code,
        content={"msg": exc.msg, "err_code": exc.code},
    )


# customize http exception
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    spa_index = _spa_index_path()
    is_spa_fallback = (
        exc.status_code == status.HTTP_404_NOT_FOUND
        and os.environ.get("LABELU_VITE_ENABLED", "") != "1"
        and spa_index is not None
        and _is_spa_navigation_request(request)
    )
    if is_spa_fallback:
        logger.debug("{} - {} (SPA index.html)", _request_context(request), exc)
    else:
        logger.warning("{} - {}", _request_context(request), exc)
    if is_spa_fallback:
        return FileResponse(
            spa_index,
            status_code=200,
            headers={
                "Content-Type": "text/html",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
    elif exc.status_code == status.HTTP_403_FORBIDDEN:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "msg": ErrorCode.CODE_40004_NOT_AUTHENTICATED.value[1],
                "err_code": ErrorCode.CODE_40004_NOT_AUTHENTICATED.value[0],
            },
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "msg": str(exc.detail),
            "err_code": ErrorCode.CODE_30003_CLIENT_ERROR.value[0],
        },
    )


# only for sqlalchemy
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.opt(exception=exc).error("{} - sql exception", _request_context(request))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "msg": _public_error_message(ErrorCode.CODE_30000_SQL_ERROR.value[1], exc),
            "err_code": ErrorCode.CODE_30000_SQL_ERROR.value[0],
        },
    )


def _json_safe_validation_errors(exc: RequestValidationError) -> list:
    """Pydantic v2 的 errors() 可能含不可 JSON 序列化的 ctx，需规整后再返回客户端。"""
    safe: list = []
    for err in exc.errors():
        row = {}
        for key, val in err.items():
            if key == "ctx" and isinstance(val, dict):
                row[key] = {k: str(v) for k, v in val.items()}
            elif isinstance(val, (str, int, float, bool)) or val is None:
                row[key] = val
            elif isinstance(val, (list, tuple)):
                row[key] = [str(x) for x in val]
            else:
                row[key] = str(val)
        safe.append(row)
    return safe


# for http request
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = _json_safe_validation_errors(exc)
    logger.warning("{} - validation error: {}", _request_context(request), errors)
    first_msg = errors[0].get("msg") if errors else None
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "msg": first_msg or ErrorCode.CODE_30002_VALIDATION_ERROR.value[1],
            "err_code": ErrorCode.CODE_30002_VALIDATION_ERROR.value[0],
            "errors": errors,
        },
    )

async def unexpected_exception_handler(request: Request, exc: Exception):
    logger.opt(exception=exc).error("{} - unexpected exception", _request_context(request))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "msg": _public_error_message(ErrorCode.UNEXPECTED_ERROR.value[1], exc),
            "err_code": ErrorCode.UNEXPECTED_ERROR.value[0],
        },
    )

def add_exception_handler(app: FastAPI):
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(LabelUException, labelu_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_exception_handler)
    app.add_exception_handler(Exception, unexpected_exception_handler)
