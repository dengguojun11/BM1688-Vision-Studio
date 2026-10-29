"""BM1688 一期模型质量门禁的唯一默认规则。

规则以绝对 mAP50 掉点表示，服务端转换评估和 BM1688 真机评估都使用同一份
版本化快照。运行任务可以携带 ``quality_rule`` 覆盖快照，但不能再由不同
执行脚本各自写一套默认值。
"""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Dict


BM1688_QUALITY_RULE_VERSION = "bm1688-yolo11-detection-v1"
BM1688_QUALITY_RULE: Dict[str, Any] = {
    "version": BM1688_QUALITY_RULE_VERSION,
    "metric": "mAP50",
    "threshold_unit": "map50_absolute_drop",
    # 这些数值是一期工程起点，不代表已经完成生产标定；真实代表模型和
    # BM1688 证据齐全后，必须以新版本规则快照替换，不能现场改旧任务。
    "threshold_status": "engineering_default_pending_production_calibration",
    "required_bmodel_variants": ["fp32", "fp16", "int8"],
    "conversion": {
        "onnx_vs_pt": {"pass_max_drop": 0.005, "review_max_drop": 0.010},
        "fp32_vs_pt": {"pass_max_drop": 0.005, "review_max_drop": 0.010},
        "fp16_vs_fp32": {"pass_max_drop": 0.005, "review_max_drop": 0.010},
        "int8_vs_fp32": {"pass_max_drop": 0.010, "review_max_drop": 0.020},
    },
    "edge": {
        "baseline_variant": "fp32",
        "variant_max_drop": {
            "fp16": 0.010,
            "int8": 0.020,
        },
        "require_all_variant_metrics": True,
    },
}


def validate_bm1688_quality_rule(rule: Dict[str, Any] | None) -> list[str]:
    """Validate a persisted rule snapshot without silently applying defaults.

    The edge runner receives this snapshot in ``run_config.json``.  A malformed
    snapshot must block that run instead of falling back to a looser threshold.
    Conversion limits are optional for specialized paths, while the edge
    baseline and per-precision limits are mandatory for generic YOLO runs.
    """

    if not isinstance(rule, dict):
        return ["quality_rule_missing"]
    reasons: list[str] = []
    if not str(rule.get("version") or "").strip():
        reasons.append("quality_rule_version_missing")
    if str(rule.get("metric") or "").strip() != "mAP50":
        reasons.append("quality_rule_metric_invalid")
    if str(rule.get("threshold_unit") or "").strip() != "map50_absolute_drop":
        reasons.append("quality_rule_threshold_unit_invalid")
    edge = rule.get("edge") if isinstance(rule.get("edge"), dict) else {}
    if str(edge.get("baseline_variant") or "").strip().lower() != "fp32":
        reasons.append("quality_rule_baseline_invalid")
    limits = edge.get("variant_max_drop")
    if not isinstance(limits, dict):
        reasons.append("quality_rule_variant_limits_missing")
    else:
        for precision in ("fp16", "int8"):
            value = limits.get(precision)
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                parsed = float("nan")
            if not math.isfinite(parsed) or not 0 <= parsed <= 1:
                reasons.append(f"quality_rule_{precision}_limit_invalid")

    conversion = rule.get("conversion")
    if conversion is not None:
        if not isinstance(conversion, dict):
            reasons.append("quality_rule_conversion_invalid")
        else:
            for pair, values in conversion.items():
                if not isinstance(values, dict):
                    reasons.append(f"quality_rule_conversion_{pair}_invalid")
                    continue
                try:
                    passed = float(values.get("pass_max_drop"))
                    review = float(values.get("review_max_drop"))
                except (TypeError, ValueError):
                    passed = review = float("nan")
                if not all(math.isfinite(item) and 0 <= item <= 1 for item in (passed, review)):
                    reasons.append(f"quality_rule_conversion_{pair}_limit_invalid")
                elif passed > review:
                    reasons.append(f"quality_rule_conversion_{pair}_order_invalid")
    return reasons


def get_bm1688_quality_rule() -> Dict[str, Any]:
    """Return a detached rule snapshot suitable for persisting in a job."""

    return deepcopy(BM1688_QUALITY_RULE)


def edge_variant_drop_limits(rule: Dict[str, Any] | None = None) -> Dict[str, float]:
    selected = rule if isinstance(rule, dict) else BM1688_QUALITY_RULE
    edge = selected.get("edge") if isinstance(selected.get("edge"), dict) else {}
    limits = edge.get("variant_max_drop")
    if not isinstance(limits, dict):
        # 只对历史快照提供读取兼容；新快照永远使用按精度的统一字段。
        legacy_int8 = edge.get("max_int8_map50_drop")
        limits = {
            "int8": legacy_int8,
        } if legacy_int8 is not None else BM1688_QUALITY_RULE["edge"]["variant_max_drop"]
    out: Dict[str, float] = {}
    for variant, value in limits.items():
        try:
            out[str(variant)] = float(value)
        except (TypeError, ValueError):
            continue
    return out
