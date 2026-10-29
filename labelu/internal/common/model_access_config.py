"""统一模型服务接入配置。

配置保存在 system_config 治理表中，服务密钥使用 PASSWORD_SECRET_KEY 派生的
Fernet 加密后保存。运行时只读取启用且最近一次连通性检查通过的配置；没有这种
配置时由调用方回退到既有 .env 设置，保证旧部署可以平滑升级。
"""
from __future__ import annotations

import uuid
import copy
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from loguru import logger

from labelu.internal.common.crypto import decrypt_value, encrypt_value
from labelu.internal.common.system_config_provider import read_group_values, write_group_values

GROUP_KEY = "model_access"
SUPPORTED_PROVIDER_KEYS = frozenset(
    {"vision_llm", "tagtool", "locateanything", "vlx_seek", "rex_omni", "volcengine"}
)
SUPPORTED_CAPABILITIES = frozenset({"vision_reasoning", "global_tag", "bbox_detection"})
SUPPORTED_REQUEST_PROTOCOLS = frozenset({"auto", "multipart", "openai_compatible"})
SUPPORTED_AUTH_TYPES = frozenset({"none", "bearer", "api_key", "header"})
MIN_BBOX_DETECTION_TIMEOUT_SECONDS = 120
_CAPABILITY_ALIASES = {
    "vision": "vision_reasoning",
    "global_tag": "global_tag",
    "detection": "bbox_detection",
    "bbox_detection": "bbox_detection",
    "vision_reasoning": "vision_reasoning",
}
SECRET_SOURCE = "mysql:encrypted"
VOLCENGINE_CODING_ENDPOINT = "https://ark.cn-beijing.volces.com/api/coding/v3"
_CONNECTION_CHECK_FIELDS = (
    "provider_key", "capability", "endpoint", "model", "auth_type",
    "auth_header", "request_protocol", "verify_tls", "max_new_tokens",
)
_STORE_CACHE_TTL_SECONDS = 2.0
_STORE_CACHE_LOCK = threading.RLock()
# Keep the identity of the reader in the cache key.  This matters in tests and
# in embedded workers which replace the configuration reader at runtime: a
# short TTL alone can otherwise return a previous reader's store.
_STORE_CACHE: tuple[float, object, dict[str, Any]] | None = None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _mask(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return "*" * max(0, len(raw) - 4) + raw[-4:]


def _empty_store() -> dict[str, Any]:
    return {"version": 1, "connections": []}


def normalize_timeout_seconds(capability: str | None, value: Any) -> int:
    """Normalize a model timeout, keeping VLM detection calls usable.

    Geometry extraction is materially slower than a text-only request.  The
    old form defaulted every capability to 30 seconds, which caused the Ark
    detection request to expire before the model returned its JSON.  Keep the
    administrator's value for other capabilities, while enforcing a practical
    floor for the dedicated bbox capability.
    """
    normalized_capability = _CAPABILITY_ALIASES.get(
        str(capability or "vision_reasoning").strip().lower(),
        str(capability or "vision_reasoning").strip().lower(),
    )
    default_timeout = (
        MIN_BBOX_DETECTION_TIMEOUT_SECONDS
        if normalized_capability == "bbox_detection"
        else 300
    )
    try:
        timeout = int(default_timeout if value in (None, "") else value)
    except (TypeError, ValueError):
        timeout = default_timeout
    timeout = max(1, min(timeout, 3600))
    if normalized_capability == "bbox_detection":
        timeout = max(timeout, MIN_BBOX_DETECTION_TIMEOUT_SECONDS)
    return timeout


def _secret(record: dict[str, Any]) -> str:
    # Resolved runtime records carry the decrypted value only at the request
    # boundary.  Test/raw records may only contain the encrypted form; support
    # both without exposing the secret in public responses.
    direct = str(record.get("api_key") or "").strip()
    if direct:
        return direct
    encrypted = str(record.get("api_key_encrypted") or "").strip()
    if not encrypted:
        return ""
    try:
        return decrypt_value(encrypted).strip()
    except Exception:
        logger.warning("模型接入配置密钥解密失败 id={}", record.get("id"))
        return ""


def _restore(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_store()
    rows: list[dict[str, Any]] = []
    for item in raw.get("connections") or []:
        if not isinstance(item, dict):
            continue
        provider_key = str(item.get("provider_key") or "").strip().lower()
        endpoint = str(item.get("endpoint") or "").strip()
        if not provider_key or not endpoint:
            continue
        capability = _CAPABILITY_ALIASES.get(
            str(item.get("capability") or "vision_reasoning").strip().lower(),
            str(item.get("capability") or "vision_reasoning").strip().lower(),
        )
        rows.append(
            {
                "id": str(item.get("id") or f"model_{uuid.uuid4().hex[:12]}"),
                "name": str(item.get("name") or provider_key).strip()[:128] or provider_key,
                "provider_key": provider_key,
                # Records written before capability-aware model access was
                # introduced represented the global visual model without a
                # capability field. Preserve those records as the original
                # vision path; treating them as bbox detection would make an
                # existing global-tag connection disappear from resolution.
                "capability": capability,
                "endpoint": endpoint[:1000],
                "model": str(item.get("model") or "").strip()[:300],
                "enabled": bool(item.get("enabled", True)),
                "is_default": bool(item.get("is_default", False)),
                "timeout_seconds": normalize_timeout_seconds(
                    capability, item.get("timeout_seconds")
                ),
                "verify_tls": bool(item.get("verify_tls", False)),
                "max_new_tokens": max(1, min(int(item.get("max_new_tokens") or 1024), 32768)),
                "auth_type": str(item.get("auth_type") or "none").strip().lower(),
                "auth_header": str(item.get("auth_header") or "Authorization").strip()[:128],
                "request_protocol": str(item.get("request_protocol") or "auto").strip().lower(),
                "api_key_encrypted": str(item.get("api_key_encrypted") or "").strip(),
                "api_key_masked": str(item.get("api_key_masked") or "").strip()[:128],
                "last_check_status": str(item.get("last_check_status") or "NOT_CHECKED").strip().upper(),
                "last_check_message": str(item.get("last_check_message") or "").strip()[:1000],
                "last_check_at": item.get("last_check_at"),
                "created_at": str(item.get("created_at") or _now()),
                "updated_at": str(item.get("updated_at") or _now()),
            }
        )
    return {"version": 1, "connections": rows}


def load_store() -> dict[str, Any]:
    global _STORE_CACHE
    now = time.monotonic()
    reader_identity = read_group_values
    with _STORE_CACHE_LOCK:
        if (
            _STORE_CACHE is not None
            and _STORE_CACHE[1] is reader_identity
            and now - _STORE_CACHE[0] < _STORE_CACHE_TTL_SECONDS
        ):
            return copy.deepcopy(_STORE_CACHE[2])
    restored = _restore(read_group_values(GROUP_KEY).get("connections_store"))
    with _STORE_CACHE_LOCK:
        _STORE_CACHE = (time.monotonic(), reader_identity, restored)
    return copy.deepcopy(restored)


def clear_model_access_cache() -> None:
    """Clear the short-lived process cache after external config updates/tests."""
    global _STORE_CACHE
    with _STORE_CACHE_LOCK:
        _STORE_CACHE = None


def _write(store: dict[str, Any]) -> None:
    ok = write_group_values(
        GROUP_KEY,
        {"connections_store": {"version": 1, "connections": list(store.get("connections") or [])}},
        value_source="db",
        change_reason="save model access configurations",
    )
    if not ok:
        raise RuntimeError("模型接入配置写入失败")
    global _STORE_CACHE
    with _STORE_CACHE_LOCK:
        _STORE_CACHE = (time.monotonic(), read_group_values, _restore(store))


def _validate(provider_key: str, capability: str, endpoint: str) -> tuple[str, str, str]:
    key = str(provider_key or "").strip().lower()
    # Existing clients omitted capability for the original visual model;
    # preserve that default as vision reasoning.  Detection is opt-in.
    raw_cap = str(capability or "vision_reasoning").strip().lower()
    cap = _CAPABILITY_ALIASES.get(raw_cap, raw_cap)
    url = str(endpoint or "").strip()
    if key not in SUPPORTED_PROVIDER_KEYS:
        raise ValueError("不支持的模型能力标识")
    if cap not in SUPPORTED_CAPABILITIES:
        raise ValueError("不支持的模型能力类型")
    if not url.startswith(("http://", "https://")):
        raise ValueError("服务地址必须以 http:// 或 https:// 开头")
    if key == "volcengine":
        url = validate_volcengine_coding_endpoint(url)
    return key, cap, url


def validate_volcengine_coding_endpoint(endpoint: str) -> str:
    """Enforce the Coding Plan boundary before any Volcengine HTTP request.

    Accept the base URL or its chat-completions URL and return the canonical
    base. Never repair a pay-as-you-go URL, credentials, or query parameters.
    """
    raw = str(endpoint or "").strip()
    message = (
        "火山方舟仅允许 Coding Plan 地址 "
        f"{VOLCENGINE_CODING_ENDPOINT}；已阻止其他地址及按量计费接口"
    )
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError(message)
    try:
        parsed = urlsplit(raw)
    except ValueError:
        raise ValueError(message) from None
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "ark.cn-beijing.volces.com"
        or parsed.path not in {
            "/api/coding/v3", "/api/coding/v3/",
            "/api/coding/v3/chat/completions", "/api/coding/v3/chat/completions/",
        }
        or "?" in raw
        or "#" in raw
    ):
        raise ValueError(message)
    return VOLCENGINE_CODING_ENDPOINT


def _validate_protocol(value: str | None) -> str:
    protocol = str(value or "auto").strip().lower()
    if protocol not in SUPPORTED_REQUEST_PROTOCOLS:
        raise ValueError("不支持的请求协议")
    return protocol


def public_record(record: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in record.items() if k != "api_key_encrypted"}
    out["api_key_configured"] = bool(record.get("api_key_encrypted"))
    out["api_key_masked"] = str(record.get("api_key_masked") or "")
    out["available"] = bool(
        record.get("enabled")
        and str(record.get("last_check_status") or "").upper() in {"PASSED", "READY"}
    )
    if record.get("provider_key") == "volcengine":
        try:
            validate_volcengine_coding_endpoint(record.get("endpoint"))
        except ValueError:
            out["available"] = False
    return out


def request_headers(record: dict[str, Any]) -> dict[str, str]:
    """Build outbound authentication headers for a resolved configuration.

    The decrypted key is only materialized here at the request boundary.  A
    custom header is honored for both ``header`` and ``api_key`` modes; the
    latter defaults to the conventional ``X-API-Key`` header.  ``none`` never
    emits a secret, even if one is accidentally stored on an old record.
    """
    secret = _secret(record)
    if not secret:
        return {}
    auth_type = str(record.get("auth_type") or "none").strip().lower()
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if auth_type in {"header", "api_key"}:
        default_name = "X-API-Key" if auth_type == "api_key" else "Authorization"
        configured_name = str(record.get("auth_header") or "").strip()
        # Older records and the form's shared default use Authorization.  For
        # the dedicated API-key mode that value is not a useful default;
        # preserve explicit custom headers while mapping the legacy default to
        # the conventional X-API-Key header.
        if auth_type == "api_key" and configured_name in {"", "Authorization"}:
            configured_name = default_name
        name = configured_name or default_name
        return {name: secret}
    return {}


def list_records() -> list[dict[str, Any]]:
    return [public_record(item) for item in load_store()["connections"]]


def get_record(config_id: str) -> dict[str, Any] | None:
    wanted = str(config_id or "").strip()
    return next((item for item in load_store()["connections"] if str(item.get("id")) == wanted), None)


def create_record(**payload: Any) -> dict[str, Any]:
    key, cap, endpoint = _validate(payload.get("provider_key"), payload.get("capability"), payload.get("endpoint"))
    now = _now()
    auth_type = str(payload.get("auth_type") or "none").strip().lower()
    if auth_type not in SUPPORTED_AUTH_TYPES:
        raise ValueError("不支持的鉴权方式")
    record = {
        "id": f"model_{uuid.uuid4().hex[:12]}",
        "name": str(payload.get("name") or key).strip()[:128] or key,
        "provider_key": key,
        "capability": cap,
        "endpoint": endpoint[:1000],
        "model": str(payload.get("model") or "").strip()[:300],
        "enabled": bool(payload.get("enabled", True)),
        "is_default": bool(payload.get("is_default", False)),
        "timeout_seconds": normalize_timeout_seconds(
            cap, payload.get("timeout_seconds")
        ),
        "verify_tls": bool(payload.get("verify_tls", False)),
        "max_new_tokens": max(1, min(int(payload.get("max_new_tokens") or 1024), 32768)),
        "auth_type": auth_type,
        "auth_header": str(payload.get("auth_header") or "Authorization").strip()[:128] or "Authorization",
        "request_protocol": _validate_protocol(payload.get("request_protocol")),
        "api_key_encrypted": "",
        "api_key_masked": "",
        "last_check_status": "NOT_CHECKED",
        "last_check_message": "尚未进行连通性检查",
        "last_check_at": None,
        "created_at": now,
        "updated_at": now,
    }
    api_key = str(payload.get("api_key") or "").strip()
    if api_key:
        record["api_key_encrypted"] = encrypt_value(api_key)
        record["api_key_masked"] = _mask(api_key)
    store = load_store()
    rows = list(store["connections"])
    if record["is_default"]:
        for row in rows:
            if row.get("provider_key") == key and row.get("capability") == cap:
                row["is_default"] = False
    rows.append(record)
    store["connections"] = rows
    _write(store)
    return public_record(record)


def update_record(config_id: str, **payload: Any) -> dict[str, Any]:
    store = load_store()
    rows = list(store["connections"])
    found = next((row for row in rows if str(row.get("id")) == str(config_id)), None)
    if found is None:
        raise ValueError("模型接入配置不存在")
    key, cap, endpoint = _validate(payload.get("provider_key", found["provider_key"]), payload.get("capability", found["capability"]), payload.get("endpoint", found["endpoint"]))
    auth_type = str(payload.get("auth_type", found.get("auth_type", "none")) or "none").strip().lower()
    if auth_type not in SUPPORTED_AUTH_TYPES:
        raise ValueError("不支持的鉴权方式")
    previous_check_values = {field: found.get(field) for field in _CONNECTION_CHECK_FIELDS}
    if found.get("provider_key") == "volcengine":
        try:
            previous_check_values["endpoint"] = validate_volcengine_coding_endpoint(found.get("endpoint"))
        except ValueError:
            # Allow correcting legacy unsafe endpoints; their old value must
            # differ so a previous check cannot survive the correction.
            pass
    found.update({
        "name": str(payload.get("name", found["name"]) or found["name"]).strip()[:128],
        "provider_key": key,
        "capability": cap,
        "endpoint": endpoint[:1000],
        "model": str(payload.get("model", found.get("model")) or "").strip()[:300],
        "enabled": bool(payload.get("enabled", found.get("enabled", True))),
        "is_default": bool(payload.get("is_default", found.get("is_default", False))),
        "timeout_seconds": normalize_timeout_seconds(
            cap, payload.get("timeout_seconds", found.get("timeout_seconds", 300))
        ),
        "verify_tls": bool(payload.get("verify_tls", found.get("verify_tls", False))),
        "max_new_tokens": max(1, min(int(payload.get("max_new_tokens", found.get("max_new_tokens", 1024)) or 1024), 32768)),
        "auth_type": auth_type,
        "auth_header": str(payload.get("auth_header", found.get("auth_header", "Authorization")) or "Authorization").strip()[:128],
        "request_protocol": _validate_protocol(payload.get("request_protocol", found.get("request_protocol", "auto"))),
        "updated_at": _now(),
    })
    credentials_changed = False
    if "api_key" in payload and payload.get("api_key") is not None:
        api_key = str(payload.get("api_key") or "").strip()
        encrypted_key = str(found.get("api_key_encrypted") or "").strip()
        if encrypted_key:
            try:
                credentials_changed = decrypt_value(encrypted_key).strip() != api_key
            except Exception:
                credentials_changed = True
        else:
            credentials_changed = bool(api_key)
        if api_key:
            found["api_key_encrypted"] = encrypt_value(api_key)
            found["api_key_masked"] = _mask(api_key)
        else:
            found["api_key_encrypted"] = ""
            found["api_key_masked"] = ""
    if credentials_changed or any(
        found.get(field) != previous_check_values[field]
        for field in _CONNECTION_CHECK_FIELDS
    ):
        found["last_check_status"] = "NOT_CHECKED"
        found["last_check_message"] = ""
        found["last_check_at"] = None
    if found["is_default"]:
        for row in rows:
            if row is not found and row.get("provider_key") == key and row.get("capability") == cap:
                row["is_default"] = False
    store["connections"] = rows
    _write(store)
    return public_record(found)


def delete_record(config_id: str) -> None:
    store = load_store()
    rows = [row for row in store["connections"] if str(row.get("id")) != str(config_id)]
    if len(rows) == len(store["connections"]):
        raise ValueError("模型接入配置不存在")
    store["connections"] = rows
    _write(store)


def record_check(config_id: str, *, status: str, message: str) -> dict[str, Any]:
    store = load_store()
    row = next((item for item in store["connections"] if str(item.get("id")) == str(config_id)), None)
    if row is None:
        raise ValueError("模型接入配置不存在")
    row["last_check_status"] = str(status).strip().upper()
    row["last_check_message"] = str(message or "").strip()[:1000]
    row["last_check_at"] = _now()
    row["updated_at"] = row["last_check_at"]
    store["connections"] = list(store["connections"])
    _write(store)
    return public_record(row)


def resolve_model_integration(
    provider_key: str,
    *,
    capability: str | None = None,
    require_tested: bool = True,
) -> dict[str, Any] | None:
    key = str(provider_key or "").strip().lower()
    rows = [row for row in load_store()["connections"] if row.get("provider_key") == key and row.get("enabled")]
    if capability:
        wanted = _CAPABILITY_ALIASES.get(str(capability).strip().lower(), str(capability).strip().lower())
        rows = [row for row in rows if row.get("capability") == wanted]
    if require_tested:
        rows = [row for row in rows if row.get("last_check_status") in {"PASSED", "READY"}]
    if key == "volcengine":
        safe_rows = []
        for row in rows:
            try:
                endpoint = validate_volcengine_coding_endpoint(row.get("endpoint"))
            except ValueError:
                continue
            safe_rows.append({**row, "endpoint": endpoint})
        rows = safe_rows
    if not rows:
        return None
    # Prefer the explicitly selected default; among the remaining records use
    # the most recently edited one so a newly rotated endpoint takes effect.
    def sort_key(row: dict[str, Any]) -> tuple[int, float]:
        try:
            stamp = datetime.fromisoformat(str(row.get("updated_at")).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OverflowError):
            stamp = 0.0
        return (0 if bool(row.get("is_default")) else 1, -stamp)

    rows.sort(key=sort_key)
    row = dict(rows[0])
    row["api_key"] = _secret(row)
    return row


def has_configured_model_integration(provider_key: str, *, capability: str | None = None) -> bool:
    """Whether an enabled admin configuration exists, regardless of check state.

    Runtime callers use this to distinguish a deliberate but untested/failed
    admin configuration from a legacy installation with no admin configuration;
    the former must fail closed instead of silently falling back to .env.
    """
    key = str(provider_key or "").strip().lower()
    rows = [row for row in load_store()["connections"] if row.get("provider_key") == key and row.get("enabled")]
    if capability:
        wanted = _CAPABILITY_ALIASES.get(str(capability).strip().lower(), str(capability).strip().lower())
        rows = [row for row in rows if row.get("capability") == wanted]
    return bool(rows)


__all__ = [
    "SUPPORTED_PROVIDER_KEYS", "SUPPORTED_CAPABILITIES", "create_record", "delete_record",
    "get_record", "list_records", "public_record", "record_check", "resolve_model_integration",
    "update_record", "SUPPORTED_AUTH_TYPES", "SUPPORTED_REQUEST_PROTOCOLS", "has_configured_model_integration",
    "clear_model_access_cache", "request_headers", "validate_volcengine_coding_endpoint",
    "VOLCENGINE_CODING_ENDPOINT",
]
