"""自动迭代策略：运行时解析、校验与质量判定口径测试。"""

from __future__ import annotations

import pytest

from labelu.internal.application.response.yolo_train import YoloTrainingValMetrics
from labelu.internal.application.service.yolo_quality_assessment import (
    build_training_quality_assessment,
)
from labelu.internal.common.auto_iteration_runtime import (
    AUTO_ITERATION_DEFAULTS,
    invalidate_auto_iteration_cache,
    load_auto_iteration_policy,
    validate_policy_values,
)
from labelu.internal.common.system_config_provider import (
    clear_runtime_override,
    set_runtime_override,
)


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    clear_runtime_override("auto_iteration")
    invalidate_auto_iteration_cache()
    yield
    clear_runtime_override("auto_iteration")
    invalidate_auto_iteration_cache()


def _apply_policy(**values):
    for key, value in values.items():
        set_runtime_override("auto_iteration", key, value)
    invalidate_auto_iteration_cache()
    return load_auto_iteration_policy(force_refresh=True)


def _metrics(**kwargs) -> YoloTrainingValMetrics:
    return YoloTrainingValMetrics(**kwargs)


def test_policy_defaults_are_page_configurable():
    policy = load_auto_iteration_policy(force_refresh=True)
    assert policy["metric_basis"] == "val_recall"
    assert policy["pass_threshold"] == pytest.approx(0.6)
    assert policy["max_rounds"] == 4
    assert policy["max_iterations_after_first"] == 3
    assert policy["require_quality_for_compile"] is True
    assert policy["allow_conditional_pass"] is True
    for key in AUTO_ITERATION_DEFAULTS:
        assert key in policy


def test_policy_runtime_override_wins_and_is_clamped():
    _apply_policy(pass_threshold=2.0, warn_threshold=0.95, max_rounds=99)
    policy = load_auto_iteration_policy(force_refresh=True)
    assert policy["pass_threshold"] == pytest.approx(0.99)
    # 预警阈值不得超过达标阈值
    assert policy["warn_threshold"] <= policy["pass_threshold"]
    assert policy["max_rounds"] == 20


def test_policy_env_lock_and_priority(monkeypatch):
    monkeypatch.setenv("LABELU_AUTO_ITERATION_PASS_THRESHOLD", "0.8")
    invalidate_auto_iteration_cache()
    set_runtime_override("auto_iteration", "pass_threshold", 0.7)
    invalidate_auto_iteration_cache()
    policy = load_auto_iteration_policy(force_refresh=True)
    # runtime override > env
    assert policy["pass_threshold"] == pytest.approx(0.7)
    assert "pass_threshold" in policy["locked_keys"]
    clear_runtime_override("auto_iteration", "pass_threshold")
    invalidate_auto_iteration_cache()
    policy = load_auto_iteration_policy(force_refresh=True)
    assert policy["pass_threshold"] == pytest.approx(0.8)


def test_policy_invalid_db_value_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(
        "labelu.internal.common.auto_iteration_runtime.read_group_values",
        lambda _group: {"metric_basis": "nonsense", "pass_threshold": "bad"},
    )
    invalidate_auto_iteration_cache()
    policy = load_auto_iteration_policy(force_refresh=True)
    assert policy["metric_basis"] == AUTO_ITERATION_DEFAULTS["metric_basis"]
    assert policy["pass_threshold"] == pytest.approx(
        AUTO_ITERATION_DEFAULTS["pass_threshold"]
    )


def test_validate_policy_values_rejects_out_of_range_and_unknown():
    errors = validate_policy_values({"pass_threshold": 0.1, "unknown_key": 1})
    joined = "；".join(errors)
    assert "pass_threshold" in joined
    assert "unknown_key" in joined
    assert (
        validate_policy_values(
            {"metric_basis": "val_recall", "pass_threshold": 0.6, "warn_threshold": 0.5}
        )
        == []
    )


def test_validate_policy_values_rejects_warn_above_pass():
    errors = validate_policy_values({"pass_threshold": 0.6, "warn_threshold": 0.8})
    assert any("预警阈值" in item for item in errors)


# ---------------------------------------------------------------------------
# 质量判定口径
# ---------------------------------------------------------------------------


def _assess(metrics, **overrides):
    payload = {
        "metric_basis": "val_recall",
        "pass_threshold": 0.6,
        "warn_threshold": 0.5,
    }
    payload.update(overrides)
    return build_training_quality_assessment(
        training_val_metrics=metrics,
        total_sample_count=100,
        policy_overrides=payload,
    )


def test_val_recall_basis_pass_and_fail():
    passed = _assess(_metrics(map50_95=0.1, map50=0.2, precision=0.8, recall=0.72))
    assert passed.status == "达标"
    assert passed.score == pytest.approx(0.72)

    failed = _assess(_metrics(map50_95=0.1, map50=0.2, precision=0.8, recall=0.4))
    assert failed.status == "不达标"
    assert any("0.4000" in item for item in failed.reasons)


def test_val_recall_conditional_pass_can_be_disallowed():
    metrics = _metrics(map50_95=0.1, map50=0.2, precision=0.8, recall=0.55)
    conditional = _assess(metrics)
    assert conditional.status == "条件达标"
    strict = _assess(metrics, allow_conditional_pass=False)
    assert strict.status == "不达标"


def test_precision_floor_guard_blocks_recall_gaming():
    metrics = _metrics(map50_95=0.1, map50=0.2, precision=0.5, recall=0.9)
    item = _assess(
        metrics,
        precision_floor_enabled=True,
        precision_floor=0.7,
    )
    assert item.status == "不达标"
    assert any("precision" in reason for reason in item.reasons)


def test_overall_recall_basis_uses_weighted_recall():
    item = _assess(
        _metrics(map50_95=0.1, map50=0.2, precision=0.8, overall_recall=0.62),
        metric_basis="overall_recall",
    )
    assert item.status == "达标"
    assert item.score == pytest.approx(0.62)


def test_missing_recall_metric_is_pending_not_failed():
    item = _assess(_metrics(map50_95=0.3, map50=0.5, precision=0.8))
    assert item.status == "待验证"
    assert any("召回率" in reason for reason in item.reasons)


def test_legacy_baseline_untouched_without_overrides():
    """不传 policy_overrides 时保持历史 mAP 判定，避免影响既有页面与发布门禁。"""
    item = build_training_quality_assessment(
        training_val_metrics=_metrics(
            map50_95=0.9, map50=0.92, precision=0.8, recall=0.4
        ),
        total_sample_count=100,
    )
    assert item.status == "达标"
