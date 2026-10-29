"""一期产品边界的公共硬门禁。"""

from fastapi import status

from labelu.internal.common.error_code import ErrorCode, LabelUException


# 一期保留车牌专用历史训练链，但它们仍然是 YOLO 检测/车牌兼容链；
# 分类、分割、姿态和自定义容器只登记在目录中，不能进入一期执行入口。
PHASE_ONE_TRAINING_JOB_TYPES = frozenset(
    {"yolo_detection", "plate_detection", "plate_recognition"}
)
PHASE_ONE_TARGET_CHIP = "bm1688"


def ensure_phase_one_training_job_type(job_type: str | None) -> str:
    """校验一期训练执行类型，返回规范化值。

    该函数只保护写入/派发边界；算法目录仍可保留二期定义供管理人员查看。
    """

    normalized = str(job_type or "yolo_detection").strip().lower()
    if normalized not in PHASE_ONE_TRAINING_JOB_TYPES:
        raise LabelUException(
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message=(
                f"一期暂不支持算法执行类型：{normalized}；"
                "当前仅开放 YOLO 目标检测和 BM1688"
            ),
        )
    return normalized


def ensure_phase_one_bm1688_platforms(platforms) -> tuple[str, ...]:
    """校验编译目标只包含 BM1688，避免配置误触发二期芯片编译。"""

    if isinstance(platforms, str):
        values = platforms.split(",")
    else:
        values = platforms or ()
    normalized = tuple(
        str(value or "").strip().lower() for value in values if str(value or "").strip()
    )
    if not normalized:
        normalized = (PHASE_ONE_TARGET_CHIP,)
    unsupported = sorted(set(normalized) - {PHASE_ONE_TARGET_CHIP})
    if unsupported:
        raise LabelUException(
            ErrorCode.CODE_30002_VALIDATION_ERROR,
            status_code=400,
            message=(
                "一期编译目标仅支持 BM1688；暂不支持："
                + ", ".join(unsupported)
            ),
        )
    return normalized


def ensure_legacy_control_plane_retired() -> None:
    """阻止已取消的项目、设备发布、OTA 和内部假发布能力继续写入。"""
    raise LabelUException(
        ErrorCode.CODE_30004_PHASE_ONE_CAPABILITY_RETIRED,
        status_code=status.HTTP_403_FORBIDDEN,
    )
