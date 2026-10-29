"""可选重型依赖：在开启对应功能时按需探测，并可自动 pip 安装（当前解释器）。"""
from __future__ import annotations

import subprocess
import sys
import threading
from typing import Final

from loguru import logger

from labelu.internal.common.config import settings

_UL_LOCK = threading.Lock()
_ULTRALYTICS_AVAILABLE: bool | None = None
_ULTRALYTICS_INSTALL_ATTEMPTED = False

_UL_PACKAGE_SPEC: Final[str] = "ultralytics==8.3.163"

_IP_LOCK = threading.Lock()
_IMAGE_PIPELINE_AVAILABLE: bool | None = None
_IMAGE_PIPELINE_INSTALL_ATTEMPTED = False

_IMAGE_PIPELINE_PACKAGE_SPECS: Final[tuple[str, ...]] = (
    "opencv-python-headless>=4.8.0",
    "scikit-learn>=1.3.0",
    "numpy>=1.24.0",
)


def _try_import_ultralytics() -> bool:
    try:
        import ultralytics  # noqa: F401

        return True
    except ImportError:
        return False


def ensure_ultralytics_installed(*, reason: str = "YOLO 训练") -> bool:
    """
    保证当前进程可 import ultralytics。
    - 已可用：立即返回 True。
    - 未安装且 YOLO_AUTO_INSTALL_ULTRALYTICS=true：每个进程最多尝试一次 pip install。
    - 自动安装失败后仍检查 import，支持手动补装后恢复。
    - 否则返回 False。
    """
    global _ULTRALYTICS_AVAILABLE, _ULTRALYTICS_INSTALL_ATTEMPTED
    if _ULTRALYTICS_AVAILABLE is True:
        return True
    if _try_import_ultralytics():
        _ULTRALYTICS_AVAILABLE = True
        return True

    if not settings.YOLO_AUTO_INSTALL_ULTRALYTICS:
        logger.debug("跳过自动安装 ultralytics（YOLO_AUTO_INSTALL_ULTRALYTICS=false）")
        return False

    with _UL_LOCK:
        if _try_import_ultralytics():
            _ULTRALYTICS_AVAILABLE = True
            return True
        if _ULTRALYTICS_INSTALL_ATTEMPTED:
            return False
        _ULTRALYTICS_INSTALL_ATTEMPTED = True

        logger.warning(
            "检测到未安装 ultralytics（{}需要）。正在使用当前 Python 执行 pip install {}，首次下载可能较慢（含 PyTorch 等依赖）…",
            reason,
            _UL_PACKAGE_SPEC,
        )
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    _UL_PACKAGE_SPEC,
                ],
                capture_output=True,
                text=True,
                timeout=1800,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("自动安装 ultralytics 超时（30min），请手动执行：pip install ultralytics")
            return False
        except OSError as exc:
            logger.error("无法执行 pip 安装 ultralytics：{}", exc)
            return False

        if proc.returncode != 0:
            logger.error(
                "自动安装 ultralytics 失败（exit {}）。stdout/stderr 节选：\n{}\n{}",
                proc.returncode,
                (proc.stdout or "")[-2000:],
                (proc.stderr or "")[-2000:],
            )
            return False

        logger.info("ultralytics 自动安装完成")
        if _try_import_ultralytics():
            _ULTRALYTICS_AVAILABLE = True
            return True

        logger.error("pip 安装 ultralytics 后仍无法 import，请重启服务或检查虚拟环境")
        return False


_PARAMIKO_LOCK = threading.Lock()
_PARAMIKO_AVAILABLE: bool | None = None
_PARAMIKO_INSTALL_ATTEMPTED = False
_PARAMIKO_PACKAGE_SPEC: Final[str] = "paramiko>=3.4.0"


def _try_import_paramiko() -> bool:
    try:
        import paramiko  # noqa: F401

        return True
    except ImportError:
        return False


def ensure_paramiko_installed(*, reason: str = "边端 SSH 连接") -> bool:
    """按需自动安装平台侧 SSH 依赖，安装成功后由调用方继续原流程。"""
    global _PARAMIKO_AVAILABLE, _PARAMIKO_INSTALL_ATTEMPTED
    if _PARAMIKO_AVAILABLE is True:
        return True
    if _try_import_paramiko():
        _PARAMIKO_AVAILABLE = True
        return True

    if not settings.EDGE_SSH_AUTO_INSTALL_PARAMIKO:
        logger.warning(
            "{} 需要 paramiko；请执行 pip install paramiko，"
            "或设置 EDGE_SSH_AUTO_INSTALL_PARAMIKO=true 自动安装",
            reason,
        )
        return False

    with _PARAMIKO_LOCK:
        if _try_import_paramiko():
            _PARAMIKO_AVAILABLE = True
            return True
        if _PARAMIKO_INSTALL_ATTEMPTED:
            return False
        _PARAMIKO_INSTALL_ATTEMPTED = True

        logger.warning(
            "检测到未安装 paramiko（{}需要）。正在使用当前 Python 执行 pip install {} …",
            reason,
            _PARAMIKO_PACKAGE_SPEC,
        )
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    _PARAMIKO_PACKAGE_SPEC,
                ],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("自动安装 paramiko 超时（5min），请手动执行：pip install paramiko")
            return False
        except OSError as exc:
            logger.error("无法执行 pip 安装 paramiko：{}", exc)
            return False

        if proc.returncode != 0:
            logger.error(
                "自动安装 paramiko 失败（exit {}）。stdout/stderr 节选：\n{}\n{}",
                proc.returncode,
                (proc.stdout or "")[-2000:],
                (proc.stderr or "")[-2000:],
            )
            return False

        logger.info("paramiko 自动安装完成，继续执行边端 SSH 流程")
        if _try_import_paramiko():
            _PARAMIKO_AVAILABLE = True
            return True

        logger.error("pip 安装 paramiko 后仍无法 import，请重启服务或检查虚拟环境")
        return False


def warm_yolo_dependencies_at_startup() -> None:
    """服务启动时预热：仅在开启训练且允许自动安装时尝试。"""
    if not settings.YOLO_TRAIN_ENABLED:
        return
    ensure_ultralytics_installed(reason="启动检查")


def _try_import_image_pipeline() -> bool:
    try:
        import cv2  # noqa: F401
        import sklearn  # noqa: F401

        return True
    except ImportError:
        return False


def ensure_image_pipeline_packages_installed(
    *, reason: str = "图像导入流水线"
) -> bool:
    """
    保证当前进程可 import cv2 与 sklearn。
    未安装且 IMAGE_PIPELINE_AUTO_INSTALL_DEPS=true 时，每个进程最多尝试一次 pip install。
    自动安装失败后仍检查 import，支持手动补装后恢复。
    """
    global _IMAGE_PIPELINE_AVAILABLE, _IMAGE_PIPELINE_INSTALL_ATTEMPTED
    if _IMAGE_PIPELINE_AVAILABLE is True:
        return True
    if _try_import_image_pipeline():
        _IMAGE_PIPELINE_AVAILABLE = True
        return True

    if not settings.IMAGE_PIPELINE_AUTO_INSTALL_DEPS:
        logger.warning(
            "{} 需要 opencv-python-headless 与 scikit-learn；"
            "请安装 pip install opencv-python-headless scikit-learn numpy，"
            "或设置 IMAGE_PIPELINE_AUTO_INSTALL_DEPS=true 自动安装",
            reason,
        )
        return False

    with _IP_LOCK:
        if _try_import_image_pipeline():
            _IMAGE_PIPELINE_AVAILABLE = True
            return True
        if _IMAGE_PIPELINE_INSTALL_ATTEMPTED:
            return False
        _IMAGE_PIPELINE_INSTALL_ATTEMPTED = True

        specs = " ".join(_IMAGE_PIPELINE_PACKAGE_SPECS)
        logger.warning(
            "检测到未安装图像流水线依赖（{}需要）。正在使用当前 Python 执行 pip install {} …",
            reason,
            specs,
        )
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    *_IMAGE_PIPELINE_PACKAGE_SPECS,
                ],
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error(
                "自动安装图像流水线依赖超时（15min），请手动执行：pip install {}",
                specs,
            )
            return False
        except OSError as exc:
            logger.error("无法执行 pip 安装图像流水线依赖：{}", exc)
            return False

        if proc.returncode != 0:
            logger.error(
                "自动安装图像流水线依赖失败（exit {}）。stdout/stderr 节选：\n{}\n{}",
                proc.returncode,
                (proc.stdout or "")[-2000:],
                (proc.stderr or "")[-2000:],
            )
            return False

        logger.info("图像流水线依赖（opencv-python-headless、scikit-learn、numpy）自动安装完成")
        if _try_import_image_pipeline():
            _IMAGE_PIPELINE_AVAILABLE = True
            return True

        logger.error(
            "pip 安装后仍无法 import cv2/sklearn，请重启服务或检查虚拟环境"
        )
        return False


def warm_image_pipeline_dependencies_at_startup() -> None:
    """服务启动时预热：流水线已开启时自动安装缺失依赖。"""
    if not settings.IMAGE_PIPELINE_ENABLED:
        return
    ensure_image_pipeline_packages_installed(reason="启动检查")
