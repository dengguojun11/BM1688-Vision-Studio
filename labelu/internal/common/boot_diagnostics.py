"""启动时与 `labelu doctor` 的诊断输出（不写密钥明文）。"""
from __future__ import annotations

import re

from loguru import logger

from labelu.internal.common.config import settings

# main.py 在自动填充 JWT 之前写入，供 print_doctor_report 准确展示
_jwt_secret_was_from_env: bool | None = None


def set_jwt_secret_was_from_env(value: bool) -> None:
    global _jwt_secret_was_from_env
    _jwt_secret_was_from_env = value


def _mask_mysql_password(url: str) -> str:
    """将 mysql(+driver)://user:password@ 中的 password 替换为 ***。"""
    return re.sub(r"(://[^:]+:)[^@]+(@)", r"\1***\2", url, count=1)


def describe_database_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return "(empty)"
    if u.startswith("sqlite:"):
        if len(u) > 96:
            return u[:28] + "…" + u[-60:]
        return u
    if "@" in u and "://" in u and not u.startswith("sqlite"):
        return _mask_mysql_password(u)
    return u


def log_boot_profile(*, jwt_from_env: bool) -> None:
    """在 init_logging 之后调用，便于排查部署与 AI 配置。

    jwt_from_env：在 main 里对 PASSWORD_SECRET_KEY 自动填充 **之前** 读取的结果。
    """
    dbu = describe_database_url(settings.DATABASE_URL)
    pipeline_deps_ok = False
    if settings.IMAGE_PIPELINE_ENABLED:
        try:
            from labelu.internal.common.image_pipeline.deps import (
                ensure_image_pipeline_deps,
            )

            pipeline_deps_ok = ensure_image_pipeline_deps()
        except Exception:
            pipeline_deps_ok = False
    logger.info(
        "LabelU boot: db_url={} media_host={} ai_provider={} ai_auto_label={} "
        "vision_keys_env volc={} yolo_train={} yolo_device={} jwt_secret_from_env={} "
        "image_pipeline_enabled={} image_pipeline_deps_ok={}",
        dbu,
        settings.MEDIA_HOST,
        settings.AI_PROVIDER,
        settings.AI_AUTO_LABEL_ENABLED,
        bool((settings.VISION_VOLCENGINE_API_KEY or "").strip()),
        settings.YOLO_TRAIN_ENABLED,
        (settings.YOLO_TRAIN_DEVICE or "").strip() or "auto",
        jwt_from_env,
        settings.IMAGE_PIPELINE_ENABLED,
        pipeline_deps_ok,
    )


def _password_secret_line() -> str:
    if _jwt_secret_was_from_env is True:
        return "PASSWORD_SECRET_KEY: from .env / environment"
    if _jwt_secret_was_from_env is False:
        return (
            "PASSWORD_SECRET_KEY: not in env (this process uses auto-generated key; "
            "set PASSWORD_SECRET_KEY in .env for production)"
        )
    if (settings.PASSWORD_SECRET_KEY or "").strip():
        return "PASSWORD_SECRET_KEY: set"
    return "PASSWORD_SECRET_KEY: not set"


def print_doctor_report() -> None:
    """人类可读 stdout，供运维与 issue 粘贴。"""
    from labelu.version import version as labelu_version
    from labelu.internal.application.service.vision_llm_chain import (
        vision_llm_chain_configured,
    )

    endpoint = (settings.AI_MODEL_ENDPOINT or "").strip()
    endpoint_line = endpoint[:120] + ("…" if len(endpoint) > 120 else "") if endpoint else "(empty)"
    lines = [
        f"labelu {labelu_version}",
        f"BASE_DATA_DIR: {settings.BASE_DATA_DIR}",
        f"MEDIA_ROOT: {settings.MEDIA_ROOT}",
        f"MEDIA_HOST: {settings.MEDIA_HOST}",
        f"DATABASE_URL: {describe_database_url(settings.DATABASE_URL)}",
        _password_secret_line(),
        f"AI_PROVIDER: {settings.AI_PROVIDER}",
        f"AI_AUTO_LABEL_ENABLED: {settings.AI_AUTO_LABEL_ENABLED}",
        f"AI_MODEL_ENDPOINT: {endpoint_line}",
        f"VISION_VOLCENGINE_API_KEY: {'set' if (settings.VISION_VOLCENGINE_API_KEY or '').strip() else 'not set'}",
        f"vision_llm_chain_configured (system management / Coding Plan): {vision_llm_chain_configured()}",
        f"YOLO_TRAIN_ENABLED: {settings.YOLO_TRAIN_ENABLED}",
        f"YOLO_AUTO_INSTALL_ULTRALYTICS: {settings.YOLO_AUTO_INSTALL_ULTRALYTICS}",
        f"IMAGE_PIPELINE_ENABLED: {settings.IMAGE_PIPELINE_ENABLED}",
        f"IMAGE_PIPELINE_AUTO_INSTALL_DEPS: {settings.IMAGE_PIPELINE_AUTO_INSTALL_DEPS}",
        f"IMAGE_PIPELINE_AUTO_CONFIGURE_ENV: {settings.IMAGE_PIPELINE_AUTO_CONFIGURE_ENV}",
        f"MEDIA_HOST_AUTO_SYNC: {settings.MEDIA_HOST_AUTO_SYNC}",
        f"TASK_SUPERUSER_USERNAMES: {(settings.TASK_SUPERUSER_USERNAMES or '').strip() or '(empty — only built-in admin@123.com via fallback)'}",
    ]
    if settings.IMAGE_PIPELINE_ENABLED:
        try:
            from labelu.internal.common.image_pipeline.deps import (
                ensure_image_pipeline_deps,
            )

            lines.append(
                f"IMAGE_PIPELINE_DEPS_OK: {ensure_image_pipeline_deps()}"
            )
        except Exception as exc:
            lines.append(f"IMAGE_PIPELINE_DEPS_OK: False ({exc})")
    if settings.YOLO_TRAIN_ENABLED:
        try:
            from labelu.internal.application.service.yolo_train import (
                _resolve_yolo_train_device,
                _torch_cuda_summary,
                yolo_train_cuda_mismatch_hint,
                yolo_train_python_executable,
            )

            lines.append(f"YOLO train python: {yolo_train_python_executable()}")
            lines.append(f"YOLO_TRAIN_DEVICE (.env): {(settings.YOLO_TRAIN_DEVICE or '').strip() or '(auto)'}")
            lines.append(f"YOLO train resolved device: {_resolve_yolo_train_device()}")
            lines.append(_torch_cuda_summary())
            hint = yolo_train_cuda_mismatch_hint()
            if hint:
                lines.append(f"YOLO train CUDA 修复: {hint}")
        except Exception as exc:
            lines.append(f"YOLO CUDA check: error ({exc})")
        try:
            from labelu.internal.common.bm1688_deploy_bootstrap import deploy_readiness_summary

            lines.append(f"BM1688 deploy auto-config: {deploy_readiness_summary()}")
        except Exception as exc:
            lines.append(f"BM1688 deploy auto-config: error ({exc})")
    print("\n".join(lines))
