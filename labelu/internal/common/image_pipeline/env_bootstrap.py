"""启动时补全 .env 中的图像流水线开关（仅当键尚未存在时写入）。"""
from __future__ import annotations

import os
import re
from pathlib import Path

from loguru import logger

from labelu.internal.common.config import settings

_KEY_ENABLED = "IMAGE_PIPELINE_ENABLED"
_KEY_AUTO_INSTALL = "IMAGE_PIPELINE_AUTO_INSTALL_DEPS"


def _dotenv_path() -> Path:
    return Path.cwd() / ".env"


def _parse_bool_value(raw: str) -> bool | None:
    v = (raw or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return None


def _enabled_from_dotenv(path: Path) -> bool | None:
    if not path.is_file():
        return None
    pattern = re.compile(rf"^\s*{re.escape(_KEY_ENABLED)}\s*=\s*(.*)\s*$", re.IGNORECASE)
    for line in path.read_text(encoding="utf-8").splitlines():
        m = pattern.match(line.strip())
        if m:
            return _parse_bool_value(m.group(1))
    return None


def apply_image_pipeline_env_bootstrap() -> bool:
    """
    当未在环境变量 / .env 中声明 IMAGE_PIPELINE_ENABLED 时，写入 .env 并启用当前进程。
    若 .env 已显式设为 false，则不改动。
    """
    if not settings.IMAGE_PIPELINE_AUTO_CONFIGURE_ENV:
        return bool(settings.IMAGE_PIPELINE_ENABLED)

    dotenv = _dotenv_path()

    env_raw = os.environ.get(_KEY_ENABLED)
    if env_raw is not None:
        parsed = _parse_bool_value(env_raw)
        if parsed is not None:
            object.__setattr__(settings, "IMAGE_PIPELINE_ENABLED", parsed)
            return parsed

    file_val = _enabled_from_dotenv(dotenv)
    if file_val is not None:
        object.__setattr__(settings, "IMAGE_PIPELINE_ENABLED", file_val)
        return file_val

    lines = [
        "",
        "# 图像导入流水线（聚类/离群/子场景，由启动脚本自动写入；可改为 false 关闭）",
        f"{_KEY_ENABLED}=true",
        f"{_KEY_AUTO_INSTALL}=true",
    ]
    try:
        if dotenv.is_file():
            with open(dotenv, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            logger.info(
                "image_pipeline: 已在 {} 追加 {}=true",
                dotenv.resolve(),
                _KEY_ENABLED,
            )
        else:
            dotenv.write_text(
                "\n".join(
                    [
                        "# LabelU 图像导入流水线（自动生成，可按需修改）",
                        f"{_KEY_ENABLED}=true",
                        f"{_KEY_AUTO_INSTALL}=true",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            logger.info(
                "image_pipeline: 已创建 {} 并设置 {}=true",
                dotenv.resolve(),
                _KEY_ENABLED,
            )
    except OSError as exc:
        logger.warning("image_pipeline: 无法写入 .env（{}），仅在本进程启用", exc)
        os.environ[_KEY_ENABLED] = "true"
        object.__setattr__(settings, "IMAGE_PIPELINE_ENABLED", True)
        return True

    os.environ[_KEY_ENABLED] = "true"
    object.__setattr__(settings, "IMAGE_PIPELINE_ENABLED", True)
    return True
