"""Production-only configuration gates.

The application keeps development defaults for backwards compatibility, but a
production process must never silently start with ephemeral credentials or
the bundled MinIO account.  This module is deliberately side-effect free so
it can be exercised before the rest of the startup sequence.
"""
from __future__ import annotations

from dataclasses import dataclass

from labelu.internal.common.config import settings


PRODUCTION_ENVS = {"prod", "production"}


def _is_placeholder_secret(value: str | None) -> bool:
    """Identify common template values without logging or comparing real secrets."""
    normalized = (value or "").strip().lower()
    if not normalized:
        return True
    markers = (
        "change_me",
        "replace-with",
        "replace_me",
        "your-secret",
        "dev-only-change-me",
        "example",
    )
    return any(marker in normalized for marker in markers)


@dataclass(frozen=True)
class ProductionSecurityReport:
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors


def _is_production() -> bool:
    return (settings.APP_ENV or "").strip().lower() in PRODUCTION_ENVS


def validate_production_security(*, jwt_from_env: bool | None = None) -> ProductionSecurityReport:
    """Return production security errors without exposing secret values.

    ``jwt_from_env`` is supplied by ``main`` because the application may have
    generated a development fallback before the lifespan starts.  In tests or
    non-production environments this function always returns a valid report.
    """
    if not _is_production():
        return ProductionSecurityReport(())

    errors: list[str] = []
    warnings: list[str] = []
    secret = (settings.PASSWORD_SECRET_KEY or "").strip()
    if jwt_from_env is False or _is_placeholder_secret(secret):
        errors.append("PASSWORD_SECRET_KEY 必须由生产环境变量或 .env 提供")
    elif len(secret) < 32:
        errors.append("PASSWORD_SECRET_KEY 长度至少为 32 个字符")

    if (settings.STORAGE_BACKEND or "").strip().lower() == "s3":
        missing = [
            name
            for name, value in (
                ("S3_BUCKET", settings.S3_BUCKET),
                ("S3_ACCESS_KEY_ID", settings.S3_ACCESS_KEY_ID),
                ("S3_SECRET_ACCESS_KEY", settings.S3_SECRET_ACCESS_KEY),
            )
            if not (value or "").strip()
        ]
        if missing:
            errors.append("生产 S3 存储缺少配置: " + ", ".join(missing))

    strategy_key = (settings.STRATEGY_INTEGRATION_S3_ACCESS_KEY_ID or "").strip()
    strategy_secret = (settings.STRATEGY_INTEGRATION_S3_SECRET_ACCESS_KEY or "").strip()
    if strategy_key.lower() == "minioadmin" or strategy_secret.lower() == "minioadmin":
        errors.append("禁止使用策略回流 MinIO 默认账号，请配置独立凭据")

    if settings.STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS:
        errors.append("生产环境禁止 STRATEGY_EVOLUTION_ALLOW_UNSIGNED_CALLBACKS")
    if (
        (settings.STRATEGY_EVOLUTION_OUTBOUND_CALLBACK_BASE_URL or "").strip()
        and not (settings.STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET or "").strip()
    ):
        errors.append("启用策略回调时必须配置 STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET")
    if getattr(settings, "STRATEGY_EVOLUTION_INTERNAL_AUTH_REQUIRED", True):
        if not (
            (settings.STRATEGY_EVOLUTION_SERVICE_TOKEN or "").strip()
            or (settings.STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET or "").strip()
        ):
            errors.append(
                "策略演化内部接口启用鉴权时必须配置 STRATEGY_EVOLUTION_SERVICE_TOKEN "
                "或 STRATEGY_EVOLUTION_CALLBACK_HMAC_SECRET"
            )

    board_token = (settings.BOARD_AGENT_TOKEN or "").strip()
    if settings.BOARD_AGENT_ENABLED and _is_placeholder_secret(board_token):
        errors.append("启用板端 Agent 时必须配置 BOARD_AGENT_TOKEN")

    # Board OTA is a production supply-chain boundary.  Requiring both the
    # signing key and the runtime gate prevents a deployment from silently
    # accepting unsigned artifacts after a rolling configuration change.
    if settings.BOARD_AGENT_ENABLED:
        ota_key = getattr(settings, "SEVEN_GAI_AGENT_OTA_SIGNING_KEY", "") or ""
        if _is_placeholder_secret(ota_key):
            errors.append("启用板端 Agent 时必须配置 SEVEN_GAI_AGENT_OTA_SIGNING_KEY")
        if not getattr(settings, "SEVEN_GAI_AGENT_REQUIRE_OTA_SIGNATURE", False):
            errors.append("启用板端 Agent 时必须开启 SEVEN_GAI_AGENT_REQUIRE_OTA_SIGNATURE")
        if not getattr(settings, "SEVEN_GAI_AGENT_REQUIRE_PROJECT", False):
            errors.append("启用板端 Agent 时必须开启 SEVEN_GAI_AGENT_REQUIRE_PROJECT")
    if settings.MEDIA_VIRUS_SCAN_REQUIRED:
        if not settings.MEDIA_VIRUS_SCAN_ENABLED:
            errors.append("生产环境要求媒体病毒扫描，但 MEDIA_VIRUS_SCAN_ENABLED 未启用")
        elif not (settings.MEDIA_VIRUS_SCAN_COMMAND or "").strip():
            errors.append("启用媒体病毒扫描时必须配置 MEDIA_VIRUS_SCAN_COMMAND")

    if settings.MEDIA_VIRUS_SCAN_ENABLED and not (settings.MEDIA_VIRUS_SCAN_COMMAND or "").strip():
        warnings.append("媒体病毒扫描已启用但未配置扫描命令，上传会被隔离")

    enrollment_token = getattr(settings, "SEVEN_GAI_AGENT_ENROLLMENT_TOKEN", "") or ""
    if getattr(settings, "SEVEN_GAI_AGENT_OTA_SIGNING_KEY", "") and _is_placeholder_secret(enrollment_token):
        warnings.append("OTA 签名已配置但 Agent 注册凭据为空，Agent 无法自动注册")

    if settings.AI_AUTO_LABEL_ENABLED and settings.AI_PROVIDER == "vision_llm_chain":
        from labelu.internal.common.model_access_config import resolve_model_integration

        try:
            try:
                cloud_ready = bool(
                    resolve_model_integration("volcengine", capability="vision_reasoning")
                )
            except TypeError:
                cloud_ready = bool(resolve_model_integration("volcengine"))
        except Exception:
            cloud_ready = False
        if not cloud_ready:
            errors.append(
                "启用云端视觉 Provider 时，请在「系统管理→模型接入」"
                "配置、启用并验证火山方舟 Coding Plan"
            )

    return ProductionSecurityReport(tuple(errors), tuple(warnings))


def enforce_production_security(*, jwt_from_env: bool | None = None) -> None:
    report = validate_production_security(jwt_from_env=jwt_from_env)
    if report.errors:
        raise RuntimeError("生产安全配置校验失败: " + "；".join(report.errors))
