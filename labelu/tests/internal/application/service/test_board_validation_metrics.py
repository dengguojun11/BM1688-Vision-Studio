from labelu.internal.application.service.board_agent import (
    _parse_sophon_demo_stage_timing,
    build_board_threshold_contract,
    compare_board_threshold_contract,
    edge_result_status_from_report,
    normalize_board_report_metrics,
)
from labelu.internal.application.service.board_validation_compare import (
    build_train_board_comparison,
)


def test_normalize_historical_export_8_infer_stage_averages():
    report = {
        "steps": [
            {
                "step": "infer_fp16",
                "bmodel": "yolov11n_fp16_1b.bmodel",
                "returncode": 0,
                "stdout": (
                    "decode_time(ms): 18.19\n"
                    "preprocess_time(ms): 9.80\n"
                    "inference_time(ms): 19.39\n"
                    "postprocess_time(ms): 6.91\n"
                ),
            },
            {
                "step": "infer_fp32",
                "bmodel": "yolov11n_fp32_1b.bmodel",
                "returncode": 0,
                "stdout": (
                    "decode_time(ms): 17.98\n"
                    "preprocess_time(ms): 10.16\n"
                    "inference_time(ms): 51.40\n"
                    "postprocess_time(ms): 6.70\n"
                ),
            },
        ],
        "metrics": {"infer_runs": []},
    }

    normalized = normalize_board_report_metrics(report)
    fp16, fp32 = normalized["metrics"]["infer_runs"]

    assert fp16["decode_ms_avg"] == 18.19
    assert fp16["preprocess_ms_avg"] == 9.8
    assert fp16["infer_ms_avg"] == 19.39
    assert fp16["postprocess_ms_avg"] == 6.91
    assert fp16["pipeline_ms_sum"] == 54.29
    assert fp32["decode_ms_avg"] == 17.98
    assert fp32["preprocess_ms_avg"] == 10.16
    assert fp32["infer_ms_avg"] == 51.4
    assert fp32["postprocess_ms_avg"] == 6.7
    assert fp32["pipeline_ms_sum"] == 86.24


def test_stage_timing_does_not_sum_incomplete_pipeline():
    parsed = _parse_sophon_demo_stage_timing(
        "decode_time(ms): 18.19\ninference_time(ms): 19.39\n"
    )

    assert parsed["decode_ms_avg"] == 18.19
    assert parsed["infer_ms_avg"] == 19.39
    assert "pipeline_ms_sum" not in parsed


def test_normalize_collects_all_bmcv_infer_runs_without_overwriting_models():
    report = {
        "bmcv_infer_runs": [
            {
                "step": "bmcv_fp32_detection",
                "bmodel": "model_fp32_1b.bmodel",
                "returncode": 0,
                "metrics": {"infer_ms_avg": 51.4},
            },
            {
                "step": "bmcv_fp16_detection",
                "bmodel": "model_fp16_1b.bmodel",
                "returncode": 0,
                "metrics": {"infer_ms_avg": 19.39},
            },
            {
                "step": "bmcv_int8_detection",
                "bmodel": "model_int8_1b.bmodel",
                "returncode": 0,
                "metrics": {"infer_ms_avg": 5.54},
            },
        ]
    }

    normalized = normalize_board_report_metrics(report)

    assert [row["bmodel"] for row in normalized["metrics"]["infer_runs"]] == [
        "model_fp32_1b.bmodel",
        "model_fp16_1b.bmodel",
        "model_int8_1b.bmodel",
    ]
    assert [row["infer_ms_avg"] for row in normalized["metrics"]["infer_runs"]] == [
        51.4,
        19.39,
        5.54,
    ]


def test_comparison_keeps_missing_successful_timings_as_na():
    comparison = build_train_board_comparison(
        quality_report=None,
        board_report={
            "metrics": {
                "bmrt_tests": [{"bmodel": "fp16.bmodel", "ok": True}],
                "infer_runs": [{"bmodel": "fp16.bmodel", "ok": True}],
            }
        },
    )

    rows = {row["metric"]: row for row in comparison["rows"]}
    assert rows["bmrt_ms_fp16.bmodel"]["edge"] == "n/a"
    assert rows["infer_ms_avg_fp16.bmodel"]["edge"] == "n/a"


def test_edge_result_status_uses_analysis_failure_over_execution_success():
    report = {
        "result": {
            "execution": {"status": "ok", "remote_exit_code": 0},
            "execution_summary": {"status": "ok", "has_bmodel_eval": True},
            "eval_summary": {"status": "ok", "map50": 0.4247},
            "analysis": {
                "status": "fail",
                "global_delta": {"bmodel_delta_ratio": 0.1417},
            },
        }
    }

    assert edge_result_status_from_report(report) == "fail"


def test_edge_result_status_keeps_execution_only_report_pending():
    report = {"result": {"execution": {"status": "ok", "remote_exit_code": 0}}}

    assert edge_result_status_from_report(report) == "result_pending"


def test_edge_result_status_accepts_explicit_legacy_pass():
    assert edge_result_status_from_report({"passed": True}) == "pass"


def test_board_threshold_contract_rejects_percent_like_legacy_values():
    row = type(
        "Export",
        (),
        {
            "validation_thresholds_json": (
                '{"effective":{"conf_thres":35,"nms_thres":0.7,"match_iou_thres":0.5},'
                '"source":"legacy"}'
            ),
            "threshold_binding_json": None,
        },
    )()

    contract = build_board_threshold_contract(row)

    assert contract["threshold_unit"] == "ratio_0_1"
    assert contract["conf_thres"] == 0.35
    assert "conf_thres" in contract["invalid_fields"]
    assert "conf_thres" in contract["defaulted_fields"]


def test_board_threshold_contract_reads_strategy_binding_effective_validation():
    row = type(
        "Export",
        (),
        {
            "validation_thresholds_json": None,
            "threshold_binding_json": (
                '{"threshold_unit":"ratio_0_1",'
                '"effective_validation_thresholds":{'
                '"conf_thres":0.42,"nms_thres":0.61,"match_iou_thres":0.53}}'
            ),
        },
    )()

    contract = build_board_threshold_contract(row)

    assert contract["conf_thres"] == 0.42
    assert contract["nms_thres"] == 0.61
    assert contract["match_iou_thres"] == 0.53
    assert contract["defaulted_fields"] == []


def test_board_threshold_contract_preserves_valid_ratio_values_outside_recommendation_range():
    row = type(
        "Export",
        (),
        {
            "validation_thresholds_json": (
                '{"threshold_unit":"ratio_0_1","effective":{'
                '"conf_thres":0.80,"nms_thres":0.85,"match_iou_thres":0.90},'
                '"source":"third_party"}'
            ),
            "threshold_binding_json": None,
        },
    )()

    contract = build_board_threshold_contract(row)

    assert contract["conf_thres"] == 0.80
    assert contract["nms_thres"] == 0.85
    assert contract["match_iou_thres"] == 0.90
    assert contract["defaulted_fields"] == []
    assert contract["invalid_fields"] == []
    assert contract["policy_warning_fields"] == [
        "conf_thres",
        "match_iou_thres",
        "nms_thres",
    ]


def test_board_threshold_contract_does_not_silently_accept_percent_unit():
    row = type(
        "Export",
        (),
        {
            "validation_thresholds_json": (
                '{"unit":"percent","effective":{'
                '"conf_thres":0.35,"nms_thres":0.7,"match_iou_thres":0.5}}'
            ),
            "threshold_binding_json": None,
        },
    )()

    contract = build_board_threshold_contract(row)

    assert contract["invalid_unit"] == "percent"
    assert set(contract["defaulted_fields"]) == {
        "conf_thres",
        "nms_thres",
        "match_iou_thres",
    }


def test_board_report_threshold_contract_match_and_mismatch_are_explicit():
    expected = {
        "threshold_unit": "ratio_0_1",
        "conf_thres": 0.35,
        "nms_thres": 0.7,
        "match_iou_thres": 0.5,
    }
    matched = compare_board_threshold_contract(
        expected,
        {
            "thresholds_applied": {
                "threshold_unit": "ratio_0_1",
                "conf_thres": 0.35,
                "nms_thres": 0.7,
                "match_iou_thres": 0.5,
            }
        },
    )
    mismatch = compare_board_threshold_contract(
        expected,
        {
            "thresholds_applied": {
                "threshold_unit": "ratio_0_1",
                "conf_thres": 0.42,
                "nms_thres": 0.7,
                "match_iou_thres": 0.5,
            }
        },
    )

    assert matched["status"] == "matched"
    assert mismatch["status"] == "mismatch"
    assert edge_result_status_from_report(
        {"threshold_contract_check": mismatch, "passed": True}
    ) == "fail"


def test_board_report_without_thresholds_remains_pending():
    check = compare_board_threshold_contract(
        {
            "threshold_unit": "ratio_0_1",
            "conf_thres": 0.35,
            "nms_thres": 0.7,
            "match_iou_thres": 0.5,
        },
        {"passed": True},
    )

    assert check["status"] == "missing"
    assert edge_result_status_from_report(
        {"threshold_contract_check": check, "passed": True}
    ) == "result_pending"
