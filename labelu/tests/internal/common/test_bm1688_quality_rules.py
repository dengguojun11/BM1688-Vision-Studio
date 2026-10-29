from labelu.internal.application.service.yolo_sop_model_evaluation import (
    build_conversion_gate_criteria,
)
from labelu.internal.common.bm1688_quality_rules import (
    BM1688_QUALITY_RULE_VERSION,
    edge_variant_drop_limits,
    get_bm1688_quality_rule,
    validate_bm1688_quality_rule,
)


def test_bm1688_quality_rule_is_versioned_and_detached():
    first = get_bm1688_quality_rule()
    second = get_bm1688_quality_rule()
    first["edge"]["variant_max_drop"]["int8"] = 0.9

    assert second["version"] == BM1688_QUALITY_RULE_VERSION
    assert second["edge"]["variant_max_drop"]["int8"] == 0.020
    assert "max_int8_map50_drop" not in second["edge"]
    assert second["conversion"]["onnx_vs_pt"]["pass_max_drop"] == 0.005


def test_legacy_int8_threshold_is_read_only_compatibility():
    assert edge_variant_drop_limits(
        {"edge": {"max_int8_map50_drop": 0.05}}
    ) == {"int8": 0.05}


def test_conversion_gate_carries_the_same_edge_rule_snapshot():
    criteria = build_conversion_gate_criteria()

    assert criteria["quality_rule_version"] == BM1688_QUALITY_RULE_VERSION
    assert criteria["quality_rule"]["edge"]["baseline_variant"] == "fp32"
    assert criteria["quality_rule"]["required_bmodel_variants"] == [
        "fp32",
        "fp16",
        "int8",
    ]


def test_quality_rule_rejects_malformed_precision_limit_without_defaulting():
    rule = get_bm1688_quality_rule()
    rule["edge"]["variant_max_drop"]["int8"] = "wide-open"

    reasons = validate_bm1688_quality_rule(rule)

    assert reasons == ["quality_rule_int8_limit_invalid"]


def test_quality_rule_accepts_engineering_snapshot_and_marks_calibration_state():
    rule = get_bm1688_quality_rule()

    assert validate_bm1688_quality_rule(rule) == []
    assert rule["threshold_status"] == "engineering_default_pending_production_calibration"
