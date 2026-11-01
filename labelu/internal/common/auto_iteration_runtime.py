"""自动迭代训练策略的运行时配置解析。

配置唯一来源是数据库配置组 ``auto_iteration``（页面可视化修改，保存即生效）。
环境变量只保留为「应急锁定/灰度」手段：一旦显式设置，页面变为只读，避免
两个来源同时生效导致判定口径不可追溯。

读取链路（高 → 低）::

    runtime override（仅测试） > 环境变量（显式设置即锁定） > 数据库 > 默认值

判定发生在「每轮训练完成 / next_iteration 报告完成」时，因此这里只做短
TTL 缓存（默认 5 秒），保存配置后同进程立即失效、跨进程最多 5 秒生效。
"""

from __future__ import annotations

import os
import time
from typing import Any

from labelu.internal.common.config import settings
from labelu.internal.common.system_config_provider import (
    MISSING,
    get_runtime_override,
    read_group_values,
)

GROUP_KEY = "auto_iteration"

# 所有可配置项的默认值，必须与 system_config_registry 中 auto_iteration 组一致。
AUTO_ITERATION_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "metric_basis": "val_recall",
    "pass_threshold": 0.6,
    "warn_threshold": 0.5,
    "max_rounds": 4,
    "no_improvement_rounds": 2,
    "min_improvement_delta": 0.02,
    "allow_conditional_pass": True,
    "require_quality_for_compile": True,
    "min_class_recall_enabled": False,
    "min_class_recall": 0.3,
    "precision_floor_enabled": False,
    "precision_floor": 0.7,
    "max_regression": 0.05,
    "eval_conf": 0.25,
}

METRIC_BASIS_OPTIONS = ("val_recall", "overall_recall", "map50_95")

# 这些键对应历史环境变量；其余键使用 LABELU_AUTO_ITERATION_<KEY> 形式。
_ENV_VAR_NAMES = {
    "enabled": "YOLO_AUTO_ITERATION_ENABLED",
    "max_rounds": "YOLO_AUTO_ITERATION_MAX_ROUNDS",
}

_NUMERIC_BOUNDS: dict[str, tuple[float, float]] = {
    "pass_threshold": (0.3, 0.99),
    "warn_threshold": (0.0, 0.99),
    "min_improvement_delta": (0.0, 0.5),
    "min_class_recall": (0.0, 0.99),
    "precision_floor": (0.0, 0.99),
    "max_regression": (0.0, 0.5),
    "eval_conf": (0.01, 0.99),
}

_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "max_rounds": (1, 20),
    "no_improvement_rounds": (0, 10),
}

_CACHE_TTL_SECONDS = float(os.environ.get("LABELU_AUTO_ITERATION_CACHE_TTL", "5"))
_cache: tuple[float, dict[str, Any]] | None = None


def invalidate_auto_iteration_cache() -> None:
    """配置写入后调用，保证同进程下一次判定立即使用新值。"""
    global _cache
    _cache = None


def _env_raw(key: str) -> str | None:
    return os.environ.get(
        _ENV_VAR_NAMES.get(key) or f"LABELU_AUTO_ITERATION_{key.upper()}"
    )


def _settings_env_fallback(key: str) -> Any:
    """兼容 pydantic settings 从 .env 读取的历史开关（os.environ 里可能没有）。"""
    if key == "enabled":
        value = getattr(settings, "YOLO_AUTO_ITERATION_ENABLED", None)
        # 默认 False：只有显式为 True 才视为环境级开启。
        return True if value else None
    if key == "max_rounds":
        value = getattr(settings, "YOLO_AUTO_ITERATION_MAX_ROUNDS", None)
        try:
            if value is not None and int(value) != 5:
                return int(value)
        except (TypeError, ValueError):
            return None
        return None
    return None


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_number(raw: str) -> float | None:
    try:
        return float(raw.strip())
    except (TypeError, ValueError):
        return None


def _coerce(key: str, value: Any) -> Any:
    """把任意来源的值归一化到配置要求的类型；无法解析时返回 None 表示无效。"""
    default = AUTO_ITERATION_DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        return None
    if key == "metric_basis":
        text = str(value or "").strip()
        return text if text in METRIC_BASIS_OPTIONS else None
    if isinstance(default, int):
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None
        return number
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(key: str, value: Any) -> Any:
    if key in _INT_BOUNDS:
        low, high = _INT_BOUNDS[key]
        return max(low, min(high, int(value)))
    if key in _NUMERIC_BOUNDS:
        low, high = _NUMERIC_BOUNDS[key]
        return round(max(low, min(high, float(value))), 4)
    return value


def _cross_field_clamp(policy: dict[str, Any]) -> dict[str, Any]:
    """字段间约束：预警阈值不得超过达标阈值；单类召回不得超过达标线。"""
    pass_threshold = float(policy["pass_threshold"])
    if float(policy["warn_threshold"]) > pass_threshold:
        policy["warn_threshold"] = round(pass_threshold, 4)
    if float(policy["min_class_recall"]) > pass_threshold:
        policy["min_class_recall"] = round(pass_threshold, 4)
    if float(policy["min_improvement_delta"]) > 0.5:
        policy["min_improvement_delta"] = 0.5
    return policy


def _env_value(key: str) -> Any:
    raw = _env_raw(key)
    if raw is not None and raw.strip() != "":
        default = AUTO_ITERATION_DEFAULTS[key]
        if isinstance(default, bool):
            return _parse_bool(raw)
        number = _parse_number(raw)
        return number if number is not None else None
    return _settings_env_fallback(key)


def _collect_source_values() -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    db_values = read_group_values(GROUP_KEY)
    resolved: dict[str, Any] = {}
    sources: dict[str, str] = {}
    locked_keys: list[str] = []
    for key, default in AUTO_ITERATION_DEFAULTS.items():
        runtime_value = get_runtime_override(GROUP_KEY, key)
        env_value = _env_value(key)
        db_value = db_values.get(key, MISSING)
        if runtime_value is not MISSING:
            raw, source = runtime_value, "runtime"
        elif env_value is not None:
            raw, source = env_value, "env"
        elif db_value is not MISSING:
            raw, source = db_value, "db"
        else:
            raw, source = default, "default"
        coerced = _coerce(key, raw)
        if coerced is None:
            # 非法值回退默认，避免把坏配置带进判定。
            coerced, source = default, "default"
        else:
            coerced = _clamp(key, coerced)
        resolved[key] = coerced
        sources[key] = source
        if env_value is not None:
            locked_keys.append(key)
    resolved = _cross_field_clamp(resolved)
    return resolved, sources, locked_keys


def load_auto_iteration_policy(*, force_refresh: bool = False) -> dict[str, Any]:
    """返回已校验/夹紧的自动迭代策略，附带来源与锁定信息。"""
    global _cache
    now = time.monotonic()
    if (
        not force_refresh
        and _cache is not None
        and now - _cache[0] < _CACHE_TTL_SECONDS
    ):
        return dict(_cache[1])
    values, sources, locked_keys = _collect_source_values()
    payload = {
        **values,
        "defaults": dict(AUTO_ITERATION_DEFAULTS),
        "sources": sources,
        "locked_keys": sorted(locked_keys),
        "max_iterations_after_first": max(0, int(values["max_rounds"]) - 1),
    }
    _cache = (now, dict(payload))
    return dict(payload)


def validate_policy_values(values: dict[str, Any]) -> list[str]:
    """校验页面提交的配置，返回中文错误列表（空表示通过）。"""
    errors: list[str] = []
    unknown = sorted(set(values).difference(AUTO_ITERATION_DEFAULTS))
    if unknown:
        errors.append("未知配置项：" + "、".join(unknown))

    def _as_float(key: str) -> float | None:
        try:
            return float(values[key])
        except (KeyError, TypeError, ValueError):
            return None

    metric_basis = values.get("metric_basis")
    if metric_basis is not None and metric_basis not in METRIC_BASIS_OPTIONS:
        errors.append("判定指标口径只能是 " + " / ".join(METRIC_BASIS_OPTIONS))
    for key, (low, high) in _NUMERIC_BOUNDS.items():
        if key not in values:
            continue
        number = _as_float(key)
        if number is None:
            errors.append(f"{key} 必须是数值")
        elif number < low or number > high:
            errors.append(f"{key} 必须在 {low} ~ {high} 之间")
    for key, (low, high) in _INT_BOUNDS.items():
        if key not in values:
            continue
        try:
            number = int(float(values[key]))
        except (TypeError, ValueError):
            errors.append(f"{key} 必须是整数")
            continue
        if number < low or number > high:
            errors.append(f"{key} 必须在 {low} ~ {high} 之间")
    for key in (
        "enabled",
        "allow_conditional_pass",
        "require_quality_for_compile",
        "min_class_recall_enabled",
        "precision_floor_enabled",
    ):
        if key in values and not isinstance(values[key], bool):
            errors.append(f"{key} 必须是布尔值")
    if "warn_threshold" in values and "pass_threshold" in values:
        warn = _as_float("warn_threshold")
        pass_line = _as_float("pass_threshold")
        if warn is not None and pass_line is not None and warn > pass_line:
            errors.append("预警阈值不能大于达标阈值")
    if "min_class_recall" in values and "pass_threshold" in values:
        min_class = _as_float("min_class_recall")
        pass_line = _as_float("pass_threshold")
        if min_class is not None and pass_line is not None and min_class > pass_line:
            errors.append("单类最低召回不能大于达标阈值")
    return errors
