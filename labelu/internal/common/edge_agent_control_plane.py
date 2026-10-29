"""Effective configuration for the resident edge Agent control plane.

The original Agent switches live in the process environment.  That is useful
for a deployment, but it makes the device-management page unable to onboard a
new box without editing ``.env`` and restarting the service.  This module adds
a narrow DB overlay for the onboarding path while keeping explicit environment
values authoritative.

Only encrypted secret fields are persisted.  The encryption key remains
``PASSWORD_SECRET_KEY``; no generated secret is accepted when that key is not
available.  Production still requires the existing static security gates, so a
page save cannot silently enable an unsafe production Agent.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from labelu.internal.common.config import settings
from labelu.internal.common.crypto import decrypt_value, encrypt_value
from labelu.internal.common.system_config_provider import (
    read_group_values,
    write_group_values,
)

GROUP_KEY = "edge_agent_control_plane"
ITEM_KEY = "config"
_MISSING = object()
_CONTROL_ENV_NAMES = (
    "BOARD_AGENT_ENABLED",
    "BOARD_AGENT_PRIMARY_ENABLED",
    "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED",
    "BOARD_AGENT_TOKEN",
    "BOARD_AGENT_TASK_TOKEN_SECRET",
    "BOARD_AGENT_PUBLIC_BASE_URL",
    "SEVEN_GAI_AGENT_ENROLLMENT_TOKEN",
)
_DEFAULT_DB_CONFIG: dict[str, Any] = {
    "version": 1,
    "enabled": False,
    "primary_enabled": False,
    "auto_bootstrap_enabled": True,
    "public_base_url": "",
    "token_encrypted": "",
    "task_token_secret_encrypted": "",
}


class EdgeAgentControlPlaneError(RuntimeError):
    """A safe, user-facing failure while preparing the control plane."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EdgeAgentControlPlaneConfig:
    enabled: bool
    primary_enabled: bool
    auto_bootstrap_enabled: bool
    token: str
    task_token_secret: str
    enrollment_token: str
    public_base_url: str
    source: str
    token_source: str
    public_base_url_source: str
    message: str = ""

    @property
    def control_plane_enabled(self) -> bool:
        return bool(
            self.enabled
            and self.primary_enabled
            and self.auto_bootstrap_enabled
            and self.token
            and self.task_token_secret
        )


@dataclass(frozen=True)
class AutoConfigureResult:
    ok: bool
    changed: bool
    config: EdgeAgentControlPlaneConfig
    code: str = ""
    message: str = ""


def _explicit_env_values() -> dict[str, str]:
    """Read explicit process/dotenv values without exposing their contents."""

    values: dict[str, str] = {}
    # Pydantic Settings applies dotenv files in the order returned by this
    # helper and then gives process variables the final precedence.  Resolve
    # lazily to avoid importing config while its Settings singleton is built.
    try:
        from dotenv import dotenv_values

        from labelu.internal.common.config import _settings_env_files

        for filename in _settings_env_files():
            path = Path(filename)
            if not path.is_file():
                continue
            parsed = dotenv_values(path)
            for key, value in parsed.items():
                if key in _CONTROL_ENV_NAMES and value is not None:
                    values[key] = str(value)
    except Exception:
        # A malformed optional dotenv file must not make the API unusable.  The
        # Settings object remains the fallback, and process variables below are
        # still authoritative.
        pass
    for key in _CONTROL_ENV_NAMES:
        if key in os.environ:
            values[key] = os.environ.get(key, "")
    return values


def _env_value(values: dict[str, str], key: str) -> Any:
    return values[key] if key in values else _MISSING


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    raw = str(value or "").strip().lower()
    if raw in {"1", "true", "yes", "on", "y", "是"}:
        return True
    if raw in {"0", "false", "no", "off", "n", "否", ""}:
        return False
    return default


def _db_config() -> dict[str, Any]:
    try:
        raw = read_group_values(GROUP_KEY).get(ITEM_KEY)
    except Exception:
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _decrypt_secret(raw: Any) -> tuple[str, str]:
    ciphertext = str(raw or "").strip()
    if not ciphertext:
        return "", "missing"
    try:
        value = decrypt_value(ciphertext)
    except Exception:
        return "", "invalid"
    return str(value or ""), "db"


def _resolve_value(
    *,
    env_values: dict[str, str],
    env_name: str,
    db: dict[str, Any],
    db_name: str,
    setting_name: str,
    default: Any,
) -> tuple[Any, str]:
    explicit = _env_value(env_values, env_name)
    if explicit is not _MISSING:
        return explicit, "env"
    if db_name in db:
        return db.get(db_name), "db"
    return getattr(settings, setting_name, default), "settings"


def resolve_control_plane() -> EdgeAgentControlPlaneConfig:
    """Return the current effective config; env values always win over DB."""

    env_values = _explicit_env_values()
    # Keep the raw DB mapping here.  Merging defaults before resolution would
    # make a missing DB value look authoritative and would hide test/runtime
    # settings fallbacks.
    db = _db_config()

    enabled_raw, enabled_source = _resolve_value(
        env_values=env_values,
        env_name="BOARD_AGENT_ENABLED",
        db=db,
        db_name="enabled",
        setting_name="BOARD_AGENT_ENABLED",
        default=False,
    )
    primary_raw, primary_source = _resolve_value(
        env_values=env_values,
        env_name="BOARD_AGENT_PRIMARY_ENABLED",
        db=db,
        db_name="primary_enabled",
        setting_name="BOARD_AGENT_PRIMARY_ENABLED",
        default=False,
    )
    auto_raw, auto_source = _resolve_value(
        env_values=env_values,
        env_name="EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED",
        db=db,
        db_name="auto_bootstrap_enabled",
        setting_name="EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED",
        default=True,
    )

    explicit_token = _env_value(env_values, "BOARD_AGENT_TOKEN")
    if explicit_token is not _MISSING:
        token = str(explicit_token or "").strip()
        token_source = "env"
        token_error = ""
    elif str(db.get("token_encrypted") or "").strip():
        token, token_source = _decrypt_secret(db.get("token_encrypted"))
        token_error = (
            "数据库中的 Agent Token 无法解密，请检查 PASSWORD_SECRET_KEY"
            if token_source == "invalid"
            else ""
        )
    else:
        token = str(getattr(settings, "BOARD_AGENT_TOKEN", "") or "").strip()
        token_source = "settings" if token else "default"
        token_error = ""

    explicit_task_secret = _env_value(env_values, "BOARD_AGENT_TASK_TOKEN_SECRET")
    task_secret_invalid = False
    if explicit_task_secret is not _MISSING:
        task_secret = str(explicit_task_secret or "").strip()
    elif str(db.get("task_token_secret_encrypted") or "").strip():
        task_secret, task_source = _decrypt_secret(db.get("task_token_secret_encrypted"))
        if task_source == "invalid":
            task_secret_invalid = True
            token_error = token_error or (
                "数据库中的边端任务令牌密钥无法解密，请检查 PASSWORD_SECRET_KEY"
            )
    else:
        task_secret = str(getattr(settings, "BOARD_AGENT_TASK_TOKEN_SECRET", "") or "").strip()
    if (
        not task_secret
        and not task_secret_invalid
        and explicit_task_secret is _MISSING
    ):
        task_secret = token

    explicit_enrollment = _env_value(env_values, "SEVEN_GAI_AGENT_ENROLLMENT_TOKEN")
    if explicit_enrollment is not _MISSING:
        enrollment_token = str(explicit_enrollment or "").strip()
    else:
        enrollment_token = token

    public_raw, public_source = _resolve_value(
        env_values=env_values,
        env_name="BOARD_AGENT_PUBLIC_BASE_URL",
        db=db,
        db_name="public_base_url",
        setting_name="BOARD_AGENT_PUBLIC_BASE_URL",
        default="",
    )
    public_base_url = str(public_raw or "").strip().rstrip("/")
    if not public_base_url:
        # MEDIA_HOST is a legacy fallback, but only when no DB URL exists.
        # ``edge_agent_bootstrap`` performs the definitive reachability check.
        public_base_url = str(getattr(settings, "MEDIA_HOST", "") or "").strip().rstrip("/")
        if public_base_url:
            public_source = "settings"

    sources = {enabled_source, primary_source, auto_source, token_source, public_source}
    if "env" in sources:
        source = "env"
    elif "db" in sources:
        source = "db"
    elif "settings" in sources:
        source = "settings"
    else:
        source = "default"
    enabled = _as_bool(enabled_raw, False)
    primary_enabled = _as_bool(primary_raw, False)
    auto_bootstrap_enabled = _as_bool(auto_raw, True)
    message = token_error
    # A production process may use the DB overlay only after its static
    # deployment contract is present.  This keeps a database write from
    # bypassing ``production_security`` (OTA signature/project gates included).
    app_env = str(getattr(settings, "APP_ENV", "development") or "development").strip().lower()
    if app_env in {"prod", "production"}:
        required_static = (
            "BOARD_AGENT_ENABLED",
            "BOARD_AGENT_PRIMARY_ENABLED",
            "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED",
            "BOARD_AGENT_TOKEN",
            "BOARD_AGENT_PUBLIC_BASE_URL",
        )
        missing_static = [
            key
            for key in required_static
            if key not in env_values or not str(env_values.get(key) or "").strip()
        ]
        if missing_static:
            enabled = False
            primary_enabled = False
            auto_bootstrap_enabled = False
            token = ""
            task_secret = ""
            enrollment_token = ""
            message = "生产环境必须先由环境变量/.env提供边端 Agent 控制面配置"
    return EdgeAgentControlPlaneConfig(
        enabled=enabled,
        primary_enabled=primary_enabled,
        auto_bootstrap_enabled=auto_bootstrap_enabled,
        token=token,
        task_token_secret=task_secret,
        enrollment_token=enrollment_token,
        public_base_url=public_base_url,
        source=source,
        token_source=token_source,
        public_base_url_source=public_source,
        message=message,
    )


def control_plane_enabled() -> bool:
    return resolve_control_plane().control_plane_enabled


def board_agent_enabled() -> bool:
    return resolve_control_plane().enabled


def primary_enabled() -> bool:
    return resolve_control_plane().primary_enabled


def auto_bootstrap_enabled() -> bool:
    return resolve_control_plane().auto_bootstrap_enabled


def board_agent_token() -> str:
    return resolve_control_plane().token


def task_token_secret() -> str:
    return resolve_control_plane().task_token_secret


def enrollment_token() -> str:
    return resolve_control_plane().enrollment_token


def public_base_url() -> str:
    return resolve_control_plane().public_base_url


def public_base_url_configured() -> bool:
    """Return whether the effective callback URL is usable by an edge box."""

    return _usable_url(public_base_url())


def _is_non_loopback_host(hostname: str) -> bool:
    value = str(hostname or "").strip().strip("[]").lower().rstrip(".")
    if not value or value in {"localhost", "0.0.0.0", "::", "::1"}:
        return False
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return True
    return not bool(address.is_loopback or address.is_unspecified)


def _usable_url(value: str) -> bool:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlparse(raw)
        hostname = str(parsed.hostname or "")
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and _is_non_loopback_host(hostname)


def derive_public_base_url(*, device_host: str | None = None) -> str:
    """Derive a callback URL from a reachable local interface when possible."""

    current = resolve_control_plane()
    if current.public_base_url and _usable_url(current.public_base_url):
        return current.public_base_url

    scheme = str(getattr(settings, "SCHEME", "http") or "http").strip().lower()
    if scheme not in {"http", "https"}:
        scheme = "http"
    try:
        port = int(str(getattr(settings, "PORT", "8000") or "8000"))
    except (TypeError, ValueError):
        port = 8000

    # Prefer the configured media URL if it is already a real LAN/DNS address.
    media_host = str(getattr(settings, "MEDIA_HOST", "") or "").strip().rstrip("/")
    if _usable_url(media_host):
        return media_host

    candidates: list[str] = []
    if device_host:
        host = str(device_host).strip().strip("[]")
        try:
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            sock = socket.socket(family, socket.SOCK_DGRAM)
            try:
                target = (host, 22, 0, 0) if family == socket.AF_INET6 else (host, 22)
                sock.connect(target)
                candidates.append(str(sock.getsockname()[0]))
            finally:
                sock.close()
        except (OSError, ValueError, TypeError):
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_UNSPEC, socket.SOCK_STREAM):
            candidates.append(str(info[4][0]))
    except OSError:
        pass
    for candidate in candidates:
        if _is_non_loopback_host(candidate):
            formatted = f"[{candidate}]" if ":" in candidate else candidate
            return f"{scheme}://{formatted}:{port}"
    return ""


def _persist_config(values: dict[str, Any], *, updated_by: int | None = None) -> None:
    write_group_values(
        GROUP_KEY,
        {ITEM_KEY: values},
        value_source="db",
        updated_by=updated_by,
        change_reason="edge device page automatic Agent control-plane setup",
    )


def ensure_auto_configured(
    *,
    device_host: str | None = None,
    updated_by: int | None = None,
) -> AutoConfigureResult:
    """Create the DB overlay needed by the page-driven onboarding flow.

    Explicit env values are never changed.  Production requires the static
    env/OTA gates to be configured before a page save may use this operation.
    """

    current = resolve_control_plane()
    env_values = _explicit_env_values()
    app_env = str(getattr(settings, "APP_ENV", "development") or "development").strip().lower()

    if app_env in {"prod", "production"}:
        required = (
            "BOARD_AGENT_ENABLED",
            "BOARD_AGENT_PRIMARY_ENABLED",
            "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED",
            "BOARD_AGENT_TOKEN",
            "BOARD_AGENT_PUBLIC_BASE_URL",
        )
        missing = [key for key in required if key not in env_values or not str(env_values.get(key) or "").strip()]
        if missing:
            return AutoConfigureResult(
                ok=False,
                changed=False,
                config=current,
                code="production_env_required",
                message="生产环境必须先在环境变量/.env中配置：" + ", ".join(missing),
            )
        if not current.control_plane_enabled:
            return AutoConfigureResult(
                ok=False,
                changed=False,
                config=current,
                code="control_plane_disabled",
                message="生产环境的边端 Agent 控制面未通过静态安全开关校验",
            )
        return AutoConfigureResult(ok=True, changed=False, config=current)

    # An explicit false/empty value is an intentional operator decision and
    # must not be overridden by a page save.
    for key, label in (
        ("BOARD_AGENT_ENABLED", "BOARD_AGENT_ENABLED"),
        ("BOARD_AGENT_PRIMARY_ENABLED", "BOARD_AGENT_PRIMARY_ENABLED"),
        ("EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED", "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED"),
    ):
        if key in env_values and not _as_bool(env_values.get(key), False):
            return AutoConfigureResult(
                ok=False,
                changed=False,
                config=current,
                code="control_plane_env_disabled",
                message=f"{label} 已由环境变量显式关闭，页面不会覆盖它",
            )
    if "BOARD_AGENT_TOKEN" in env_values and not str(env_values.get("BOARD_AGENT_TOKEN") or "").strip():
        return AutoConfigureResult(
            ok=False,
            changed=False,
            config=current,
            code="token_env_empty",
            message="BOARD_AGENT_TOKEN 已显式留空，页面不会生成替代凭据",
        )

    db = {**_DEFAULT_DB_CONFIG, **_db_config()}
    updates = dict(db)
    token = current.token
    if not token:
        if str(db.get("token_encrypted") or "").strip():
            return AutoConfigureResult(
                ok=False,
                changed=False,
                config=current,
                code="token_decrypt_failed",
                message=current.message or "数据库中的 Agent Token 无法解密，请检查 PASSWORD_SECRET_KEY",
            )
        token = secrets.token_urlsafe(48)
        try:
            updates["token_encrypted"] = encrypt_value(token)
        except Exception as exc:
            return AutoConfigureResult(
                ok=False,
                changed=False,
                config=current,
                code="secret_key_missing",
                message="无法安全保存自动生成的 Agent Token，请先配置 PASSWORD_SECRET_KEY",
            )
    elif not str(updates.get("token_encrypted") or "").strip() and "BOARD_AGENT_TOKEN" not in env_values:
        # A non-empty settings fallback can be used for legacy env loading, but
        # it must not be copied into DB as plaintext.  Keep going so the DB
        # switches and callback URL can still be provisioned.
        pass

    # Keep the task-signing secret independent from the Agent bearer token.
    # An existing DB ciphertext or explicit environment value remains
    # authoritative. When neither exists, generate a new encrypted secret;
    # this also repairs older records that only stored the Agent token.
    # Start from the effective value so an existing encrypted DB secret can be
    # reused without falling through to an uninitialized local variable.  A
    # later branch still fails closed when the ciphertext was present but
    # could not be decrypted.
    task_secret = current.task_token_secret
    has_db_task_secret = bool(str(db.get("task_token_secret_encrypted") or "").strip())
    has_env_task_secret = "BOARD_AGENT_TASK_TOKEN_SECRET" in env_values
    settings_task_secret = str(
        getattr(settings, "BOARD_AGENT_TASK_TOKEN_SECRET", "") or ""
    ).strip()
    if not has_db_task_secret and not has_env_task_secret and not settings_task_secret:
        task_secret = secrets.token_urlsafe(48)
        try:
            updates["task_token_secret_encrypted"] = encrypt_value(task_secret)
        except Exception as exc:
            return AutoConfigureResult(
                ok=False,
                changed=False,
                config=current,
                code="secret_key_missing",
                message="无法安全保存自动生成的边端任务密钥，请先配置 PASSWORD_SECRET_KEY",
            )
    elif settings_task_secret and not has_db_task_secret and not has_env_task_secret:
        task_secret = settings_task_secret

    if has_db_task_secret and not task_secret:
        return AutoConfigureResult(
            ok=False,
            changed=False,
            config=current,
            code="task_secret_decrypt_failed",
            message=current.message
            or "数据库中的边端任务令牌密钥无法解密，请检查 PASSWORD_SECRET_KEY",
        )

    base_url = current.public_base_url
    if not base_url or not _usable_url(base_url):
        if "BOARD_AGENT_PUBLIC_BASE_URL" in env_values:
            return AutoConfigureResult(
                ok=False,
                changed=False,
                config=current,
                code="public_base_url_unreachable",
                message="BOARD_AGENT_PUBLIC_BASE_URL 不是边端可访问的 http/https 地址",
            )
        base_url = derive_public_base_url(device_host=device_host)
        if not base_url:
            return AutoConfigureResult(
                ok=False,
                changed=False,
                config=current,
                code="public_base_url_missing",
                message="无法自动推导边端回连地址，请将服务监听到局域网并配置可访问的 MEDIA_HOST",
            )
        updates["public_base_url"] = base_url

    if "BOARD_AGENT_ENABLED" not in env_values:
        updates["enabled"] = True
    if "BOARD_AGENT_PRIMARY_ENABLED" not in env_values:
        updates["primary_enabled"] = True
    if "EDGE_AGENT_AUTO_BOOTSTRAP_ENABLED" not in env_values:
        updates["auto_bootstrap_enabled"] = True
    updates["version"] = 1

    changed = updates != db
    if changed:
        try:
            _persist_config(updates, updated_by=updated_by)
        except Exception as exc:
            # Keep storage/connection details out of the admin response. The
            # exception is still available in the server log for diagnosis.
            logger.warning("persist edge Agent control-plane config failed: {}", exc)
            return AutoConfigureResult(
                ok=False,
                changed=False,
                config=current,
                code="control_plane_persist_failed",
                message="边端 Agent 控制面保存失败，请检查服务端日志",
            )
    resolved = resolve_control_plane()
    if not resolved.control_plane_enabled:
        return AutoConfigureResult(
            ok=False,
            changed=changed,
            config=resolved,
            code="control_plane_disabled",
            message="边端 Agent 控制面未完整启用，请检查开关和 Token",
        )
    return AutoConfigureResult(ok=True, changed=changed, config=resolved)

