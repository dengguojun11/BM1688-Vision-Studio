"""BM1688 quality refresh and edge-validation business-state tests."""
from __future__ import annotations

import time

from labelu.internal.application.service.yolo_bm1688_export import (
    _current_verdict_label,
    _edge_result_status,
    _edge_execution_label,
    _edge_int8_eval_label,
    _edge_validation_label,
    _verdict_code,
    _workflow_stage,
)
from labelu.internal.common.bm1688_quality_refresh import (
    is_refresh_running,
    read_refresh_status,
    start_quality_report_refresh_background,
)
from labelu.internal.common.edge_ssh_client import _sftp_oserror_message


PASS_GATE = "\u8fbe\u6807"
FAIL_GATE = "\u4e0d\u8fbe\u6807"


def _row(status: str, quality_report=None):
    return type("Row", (), {"status": status, "quality_report": quality_report})()


def _bv(status: str, report=None):
    return type("BV", (), {"status": status, "report": report})()


def test_edge_validation_label_mapping():
    assert _edge_validation_label(None) == "\u672a\u5f00\u59cb"
    # Execution completion is distinct from a quality verdict.  Without a
    # returned evaluation report the UI must keep the result pending.
    assert _edge_validation_label(_bv("COMPLETED")) == "\u5df2\u5b8c\u6210"
    assert _edge_validation_label(_bv("FAILED")) == "\u5931\u8d25"
    assert _edge_validation_label(_bv("PROCESSING")) == "\u9a8c\u8bc1\u4e2d"


def test_edge_execution_and_int8_labels_follow_business_flow():
    row_pending = _row("PROCESSING")
    row_compiled = _row("COMPLETED")
    row_passed = _row(
        "COMPLETED",
        {"sop_eval": {"comparison_pt_bmodel": {"status": "ok"}}},
    )
    bv_pending = _bv("PENDING")
    bv_done = _bv("COMPLETED")
    bv_done_with_eval = _bv(
        "COMPLETED",
        {
            "result": {
                "eval_summary": {"map50": 0.66},
                "execution_summary": {"has_bmodel_eval": True},
            }
        },
    )
    bv_failed = _bv("FAILED")

    assert _edge_execution_label(None) == "\u672a\u5f00\u59cb"
    assert _edge_execution_label(bv_pending) == "\u5f85\u6267\u884c"
    assert _edge_execution_label(bv_done) == "\u6267\u884c\u5b8c\u6210"
    assert _edge_execution_label(bv_done_with_eval) == "\u7ed3\u679c\u5df2\u56de\u4f20"
    assert _edge_execution_label(bv_failed) == "\u6267\u884c\u5931\u8d25"

    assert _edge_int8_eval_label(row_pending, None) == "\u5f85\u7f16\u8bd1"
    assert _edge_int8_eval_label(row_compiled, None) == "\u5f85\u6267\u884c"
    assert _edge_int8_eval_label(row_compiled, bv_pending) == "\u8bc4\u4f30\u4e2d"
    assert _edge_int8_eval_label(row_compiled, bv_done) == "\u5f85\u56de\u4f20"
    assert _edge_int8_eval_label(row_passed, bv_done) == "\u901a\u8fc7"


def test_plate_recognition_runtime_completion_is_review_not_pending():
    row = _row(
        "COMPLETED",
        {
            "sop_eval": {
                "plate_recognition_eval": {
                    "status": "ok",
                    "model_type": "plate_recognition",
                    "model_tag": "fp16",
                    "runtime_ok": True,
                    "input_image_count": 351,
                    "total_detections": 437,
                }
            }
        },
    )
    board_validation = _bv("COMPLETED", {"result": {}})

    assert _edge_int8_eval_label(row, board_validation) == "需复核"
    assert _edge_result_status(row, board_validation) == "review"


def test_manual_edge_validation_metrics_can_close_int8_eval_gap():
    row_manual_only = _row(
        "COMPLETED",
        {
            "sop_eval": {
                "pt": {
                    "metrics": {
                        "map50": 0.6719,
                        "map50_95": 0.6719,
                        "precision": 0.7664,
                        "recall": 0.6436,
                    }
                }
            }
        },
    )
    bv_manual_done = _bv(
        "COMPLETED",
        {
            "source": "manual_edge_validation",
            "result": {
                "execution": {
                    "ingest": {
                        "bmodel_sop": {
                            "status": "ok",
                            "metrics": {
                                "map50": 0.6764,
                                "map50_95": 0.6764,
                                "precision": 0.7265,
                                "recall": 0.6645,
                            },
                        }
                    }
                }
            },
        },
    )

    assert _edge_int8_eval_label(row_manual_only, bv_manual_done) == "\u901a\u8fc7"
    assert _current_verdict_label(row_manual_only, PASS_GATE, bv_manual_done) == "\u53ef\u8fdb\u5165\u53d1\u5e03\u8bc4\u4f30"


def test_manual_edge_validation_metrics_without_source_flag_still_count():
    row_manual_only = _row(
        "COMPLETED",
        {
            "sop_eval": {
                "pt": {
                    "metrics": {
                        "map50": 0.6719,
                        "map50_95": 0.6719,
                    }
                }
            }
        },
    )
    bv_without_source = _bv(
        "COMPLETED",
        {
            "result": {
                "execution": {
                    "ingest": {
                        "bmodel_sop": {
                            "status": "ok",
                            "metrics": {
                                "map50": 0.6764,
                                "map50_95": 0.6764,
                            },
                        }
                    }
                }
            }
        },
    )
    bv_with_source = _bv(
        "COMPLETED",
        {
            "source": "manual_edge_validation",
            "result": {
                "execution": {
                    "ingest": {
                        "bmodel_sop": {
                            "status": "ok",
                            "metrics": {
                                "map50": 0.6764,
                                "map50_95": 0.6764,
                            },
                        }
                    }
                }
            },
        },
    )

    assert _edge_int8_eval_label(row_manual_only, bv_without_source) == _edge_int8_eval_label(
        row_manual_only, bv_with_source
    )
    assert _current_verdict_label(row_manual_only, PASS_GATE, bv_without_source) == _current_verdict_label(
        row_manual_only, PASS_GATE, bv_with_source
    )


def test_current_verdict_label_matches_real_release_path():
    row_processing = _row("PROCESSING")
    row_compiled = _row("COMPLETED")
    row_failed = _row(
        "COMPLETED",
        {"sop_eval": {"comparison_pt_bmodel": {"status": "fail"}}},
    )
    row_passed = _row(
        "COMPLETED",
        {"sop_eval": {"comparison_pt_bmodel": {"status": "ok"}}},
    )
    bv_processing = _bv("PROCESSING")
    bv_done = _bv(
        "COMPLETED",
        {
            "result": {
                "eval_summary": {"map50": 0.66},
                "execution_summary": {"has_bmodel_eval": True},
            }
        },
    )

    assert _current_verdict_label(row_processing, PASS_GATE, None) == "\u7f16\u8bd1\u4e2d"
    assert _current_verdict_label(row_compiled, FAIL_GATE, None) == "\u8bad\u7ec3\u672a\u8fbe\u6807"
    assert _current_verdict_label(row_compiled, PASS_GATE, bv_processing) == "\u5f85\u5b8c\u6210\u8fb9\u7aef\u9a8c\u8bc1"
    assert _current_verdict_label(row_compiled, PASS_GATE, bv_done) == "\u5f85\u8fdb\u4e00\u6b65\u786e\u8ba4"
    assert _current_verdict_label(row_failed, PASS_GATE, bv_done) == "\u8fb9\u7aef\u7cbe\u5ea6\u672a\u901a\u8fc7"
    assert _current_verdict_label(row_passed, PASS_GATE, bv_done) == "\u53ef\u8fdb\u5165\u53d1\u5e03\u8bc4\u4f30"


def test_machine_fields_follow_bm1688_business_flow():
    row_processing = _row("PROCESSING")
    row_compiled = _row("COMPLETED")
    row_failed = _row(
        "COMPLETED",
        {"sop_eval": {"comparison_pt_bmodel": {"status": "fail"}}},
    )
    row_passed = _row(
        "COMPLETED",
        {"sop_eval": {"comparison_pt_bmodel": {"status": "ok"}}},
    )
    bv_processing = _bv("PROCESSING")
    bv_failed = _bv("FAILED")
    bv_done = _bv("COMPLETED")

    assert _edge_result_status(row_processing, None) == "not_started"
    assert _edge_result_status(row_compiled, None) == "not_started"
    assert _edge_result_status(row_compiled, bv_processing) == "running"
    assert _edge_result_status(row_compiled, bv_done) == "result_pending"
    assert _edge_result_status(row_failed, bv_done) == "fail"
    assert _edge_result_status(row_passed, bv_done) == "pass"

    assert _verdict_code(row_processing, PASS_GATE, None) == "compiling"
    assert _verdict_code(row_compiled, FAIL_GATE, None) == "training_failed"
    assert _verdict_code(row_compiled, PASS_GATE, bv_processing) == "edge_pending"
    assert _verdict_code(row_failed, PASS_GATE, bv_done) == "int8_failed"
    assert _verdict_code(row_compiled, PASS_GATE, bv_failed) == "edge_failed"
    assert _verdict_code(row_passed, PASS_GATE, bv_done) == "release_ready"

    assert _workflow_stage(row_processing, None, PASS_GATE) == "compile"
    assert _workflow_stage(row_compiled, bv_processing, PASS_GATE) == "edge_validation"
    assert _workflow_stage(row_passed, bv_done, PASS_GATE) == "publish_ready"


def test_sftp_oserror_message_disk_full():
    msg = _sftp_oserror_message(OSError("Failure"), remote_path="/data/x", action="\u4e0a\u4f20")
    assert "SFTP" in msg
    assert "Failure" in msg or "\u78c1\u76d8" in msg or "\u6743\u9650" in msg


def test_quality_refresh_status_lifecycle(tmp_path, monkeypatch):
    export_id = 77
    root = tmp_path / "yolo_bm1688_exports" / str(export_id)
    root.mkdir(parents=True)
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_quality_refresh.settings.MEDIA_ROOT",
        str(tmp_path),
    )
    monkeypatch.setattr(
        "labelu.internal.common.bm1688_quality_refresh.settings.YOLO_BM1688_EXPORT_SUBDIR",
        "yolo_bm1688_exports",
    )

    def _fake_generate(export_id, *, run_onnx_val=False):
        return {}

    monkeypatch.setattr(
        "labelu.internal.application.service.yolo_bm1688_export._generate_quality_report_sync",
        _fake_generate,
    )

    out = start_quality_report_refresh_background(export_id, run_onnx_val=False)
    assert out.get("status") == "started"

    deadline = time.time() + 5
    final = None
    while time.time() < deadline:
        final = read_refresh_status(export_id)
        if final and str(final.get("status")).upper() != "RUNNING":
            break
        time.sleep(0.05)

    assert final is not None
    assert str(final.get("status")).upper() == "COMPLETED"
    assert is_refresh_running(export_id) is False
